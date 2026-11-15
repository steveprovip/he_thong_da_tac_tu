"""CCX-1: carry and convex overlays on a 10 USDT account (2026-11-14). Offline on public data: no keys, no orders.

    python -m research.carry_convex_study events     # count the setups (no outcome is read)
    python -m research.carry_convex_study klines     # 1 m bars around every snipe event (public REST, cached)
    python -m research.carry_convex_study run        # A-E -> data/carry_convex_study.json

Pre-registration: docs/research_upgrade/plan_carry_convex_10u_2026_11.md (frozen before the `klines` stage).

A  venue floors: which two-leg structures 10 U can post at all (Binance MIN_NOTIONAL per symbol, Hyperliquid 10 USD).
B  XV carry (tools/funding_spread_replay XV HEDGE|H28|k2, unchanged rule) as ONE slot on a two-venue 10 U account:
   per-venue balances, the Hyperliquid floor, transfers, the lane's liquidation guard on 4 h opens.
C  XS: the same rule with both legs on Binance (short the highest trailing funding, long the lowest): no second venue,
   no hedge of the coin itself.
D  T3 funding snipe: enter the receiving side 5 minutes before a settlement, leave after it (8 cells).
E  growth: flat lot vs win-streak compounding vs risk-fraction sizing, 2 000 bootstrap paths x 30 days.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
PLAN = 'docs/research_upgrade/plan_carry_convex_10u_2026_11.md'
UNIVERSE = ROOT / 'data' / 'funding_universe'
KLINES = ROOT / 'data' / 'snipe_klines'
OUT = ROOT / 'data' / 'carry_convex_study.json'
EVENTS_OUT = ROOT / 'data' / 'carry_convex_events.json'

MIN_MS, HOUR_MS, DAY_MS = 60_000, 3_600_000, 86_400_000
H8 = 8 * HOUR_MS
PARTS = {'TRAIN': ('2025-01-01', '2025-12-31'), 'HOLDOUT': ('2026-01-01', '2026-09-30'),
         'FRESH': ('2026-10-01', '2026-11-12')}
SEED = 20261114

# ---- costs (bps): Binance USD-M VIP0 without the BNB discount, Hyperliquid base tier
MAKER, TAKER, SLIP = 2.0, 5.0, 1.5
SCHEDULES = {'VIP0': (2.0, 5.0, 1.5), 'VIP0_slip5': (2.0, 5.0, 5.0), 'BNB': (1.8, 4.5, 1.5), 'zero': (0.0, 0.0, 0.0)}
PRIMARY = 'VIP0'

# ---- A / B: venue floors and the two-venue account
EQUITY0 = 10.0
BN_MIN_DEFAULT, HL_MIN_ORDER_USD = 5.0, 10.0
MMR = 0.025                                     # maintenance margin rate assumed on both venues (lowest alt bracket)
GUARD_CLOSE_DIST = 0.06                         # research.carry_lane.GuardPolicy.close_liq_dist_pct
LANE_MAX_LEVERAGE = 2.0                         # research.carry_lane.GuardPolicy.max_leverage
BN_FEE_RT, HL_FEE_RT = 4.0, 11.0                # HEDGE round trip 15 bps: Binance maker 2 x 2, Hyperliquid (4.5 + 1) x 2
CARRY_LEVERAGES, TRANSFER_COSTS = (2.0, 3.0), (0.0, 1.0)
XS_COST_BPS = 16.0                              # one maker + one taker + 1 bp slip at each end, both on Binance
XS_H_DAYS, XS_K = 28, 2.0

# ---- D: funding snipe
THETA_BPS = 15.0                                # 0.15 % per print (the directive's floor)
TIERS = (15.0, 30.0, 50.0, 100.0)
PRE = 10                                        # bars kept before the settlement bar
KL_LIMIT = 99                                   # T - 10 min .. T + 88 min, request weight 1
ENTRIES, EXITS = ('M', 'TK'), ('X0', 'X1', 'TR15', 'TR30')
TRAILS = {'TR15': (15.0, 50.0), 'TR30': (30.0, 100.0)}      # (trail bps, hard stop bps)
MAX_HOLD_MIN = 60
N_TRIALS = len(ENTRIES) * len(EXITS)
MIN_N = 100

# ---- E: growth
PATHS, DAYS = 2000, 30
RUIN_U, MAX_LEV = 1.0, 20.0
CAP_NOTIONAL = 2000.0                           # addendum: 1 % of the lower-quartile 5-minute volume before a settlement
TARGET_PER_HOUR = 20.0


def ms(day: str) -> int:
    from datetime import datetime, timezone
    return int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)


def part_of(t_ms: int) -> Optional[str]:
    for name, (lo, hi) in PARTS.items():
        if ms(lo) <= t_ms < ms(hi) + DAY_MS:
            return name
    return None


# ================================================================================================ statistics
def cluster_stats(x, groups, n_trials: int = 1) -> dict:
    """Mean of x with an SE clustered by `groups` (UTC day), never smaller than the i.i.d. SE."""
    x, g = np.asarray(x, dtype=np.float64), np.asarray(groups)
    if x.size == 0:
        return {'n': 0}
    out = {'n': int(x.size), 'mean': float(x.mean()), 'win': float((x > 0).mean()), 'days': int(np.unique(g).size)}
    iid = float(x.std(ddof=1) / math.sqrt(x.size)) if x.size > 1 else math.nan
    _, inv = np.unique(g, return_inverse=True)
    s = np.bincount(inv, weights=x / x.size)
    cl = float(math.sqrt(s.size / (s.size - 1.0) * ((s - s.mean()) ** 2).sum())) if s.size > 1 else math.nan
    se = np.nanmax([iid, cl]) if not (math.isnan(iid) and math.isnan(cl)) else math.nan
    out.update(se=float(se), lcb95=float(x.mean() - 1.645 * se), ucb95=float(x.mean() + 1.645 * se))
    if n_trials >= 1 and x.size >= 3 and x.std(ddof=1) > 0:
        from research.macro_sweep_study import deflated
        out['dsr'] = deflated(list(x), n_trials)
    return out


def verdict(train: dict, holdout: dict, fresh: dict) -> dict:
    """Plan section 6: every check must hold."""
    checks = {'holdout_n>=%d' % MIN_N: holdout.get('n', 0) >= MIN_N,
              'holdout_lcb95>0': holdout.get('lcb95', -math.inf) > 0,
              'holdout_dsr>=0.95': (holdout.get('dsr') or 0.0) >= 0.95,
              'fresh_mean>0': fresh.get('n', 0) > 0 and fresh.get('mean', -math.inf) > 0}
    return {'checks': checks, 'verdict': 'EDGE' if all(checks.values()) else 'NO EDGE'}


# ================================================================================================ A: venue floors
def min_notional(info: dict) -> dict:
    """{symbol: MIN_NOTIONAL USDT} of the USDT perpetuals in an exchangeInfo payload."""
    out = {}
    for s in info.get('symbols', []):
        if s.get('contractType') == 'PERPETUAL' and s.get('quoteAsset') == 'USDT':
            f = [x for x in s.get('filters', []) if x.get('filterType') == 'MIN_NOTIONAL']
            if f:
                out[s['symbol']] = float(f[0]['notional'])
    return out


def two_venue_plan(equity: float, leverage: float, bn_min: float = BN_MIN_DEFAULT, hl_min: float = HL_MIN_ORDER_USD,
                   transfer_cost: float = 0.0) -> dict:
    """Largest equal notional per leg of a Binance x Hyperliquid pair from an equity split in half, and whether both
    venue floors accept it. Leverage moves the margin, not the floor."""
    if not (equity > 0 and leverage > 0 and transfer_cost >= 0):
        raise ValueError('equity and leverage must be positive')
    per_venue = max(0.0, (equity - transfer_cost) / 2.0)
    notional, need = leverage * per_venue, max(bn_min, hl_min)
    return {'margin_per_venue': per_venue, 'notional_per_leg': notional, 'floor': need, 'feasible': notional >= need - 1e-12,
            'free_margin': 0.0 if notional < need else 2 * per_venue - 2 * need / leverage,
            'min_leverage': need / per_venue if per_venue > 0 else math.inf}


def one_venue_plan(equity: float, leverage: float, min_a: float = BN_MIN_DEFAULT, min_b: float = BN_MIN_DEFAULT) -> dict:
    """Two perp legs in ONE cross-margin wallet (XS): notional per leg = leverage x equity / 2."""
    if not (equity > 0 and leverage > 0):
        raise ValueError('equity and leverage must be positive')
    notional, need = leverage * equity / 2.0, max(min_a, min_b)
    return {'notional_per_leg': notional, 'floor': need, 'feasible': notional >= need - 1e-12}


def liq_move(leverage: float, side: int, mmr: float = MMR) -> float:
    """Adverse price move (fraction of the entry, > 0) that liquidates an isolated leg: equity = mmr x notional."""
    if leverage <= 0 or not 0 <= mmr < 1:
        raise ValueError('leverage > 0 and 0 <= mmr < 1')
    if 1.0 / leverage <= mmr:
        return 0.0
    return (1.0 / leverage - mmr) / (1.0 - mmr if side > 0 else 1.0 + mmr)


def guard_move(leverage: float, side: int, mmr: float = MMR, dist: float = GUARD_CLOSE_DIST) -> float:
    """Adverse move at which the lane's guard closes BOTH legs: the liquidation price is `dist` of the mark away."""
    m = liq_move(leverage, side, mmr)
    g = 1.0 - (1.0 - m) / (1.0 - dist) if side > 0 else (1.0 + m) / (1.0 + dist) - 1.0
    return max(0.0, g)


# ================================================================================================ B: XV carry account
def load_xv() -> tuple:
    """(series per coin, replay trades XV HEDGE|H28|k2) from the funding cache; a coin with a hole is skipped."""
    from tools import funding_spread_replay as fsr
    series, trades = {}, []
    for c in fsr.COINS:
        sym, hl = f'{c}USDT', fsr.HL_NAME.get(c, c)
        names = {'bn': f'bnf_{sym}', 'hl': f'hlf_{hl}', 'px_bn': f'bnk_{sym}', 'px_other': f'hlk_{hl}'}
        if not all((fsr.CACHE / f'{v}.json').exists() for v in names.values()):
            continue
        ser = {k: fsr.arr(json.loads((fsr.CACHE / f'{v}.json').read_text())) for k, v in names.items()}
        if any(v is None for v in ser.values()):
            continue
        series[c] = ser
        trades += fsr.simulate(ser, 'XV', c, 28, 2.0, 'HEDGE')
    return series, trades


def xv_legs(ser: dict, trade: dict, lev_bn: float, lev_hl: float, mmr: float = MMR) -> dict:
    """Per-venue P&L (bps of the leg notional) of one replay trade, walked on the 4 h opens; the pair is closed at the
    first bar where either leg is within the guard distance of its liquidation price. Intrabar extremes are not seen."""
    from tools import funding_spread_replay as fsr
    t0, h = int(round(trade['t'] * 1000)), int(trade['days']) * DAY_MS
    short_bn = trade['legs'][0] == 'bn_perp'
    (bt, br), (ht, hr) = ser['bn'], ser['hl']
    pb0, ph0 = fsr.price_at(*ser['px_bn'], t0), fsr.price_at(*ser['px_other'], t0)
    s_bn, s_hl = (-1, 1) if short_bn else (1, -1)
    g_bn, g_hl = guard_move(lev_bn, s_bn, mmr), guard_move(lev_hl, s_hl, mmr)
    t1, early, worst = t0 + h, False, 0.0
    for u in range(t0 + 4 * HOUR_MS, t0 + h + 1, 4 * HOUR_MS):
        pb, ph = fsr.price_at(*ser['px_bn'], u), fsr.price_at(*ser['px_other'], u)
        if pb is None or ph is None:
            continue
        adv_bn, adv_hl = -s_bn * (pb / pb0 - 1.0), -s_hl * (ph / ph0 - 1.0)
        worst = max(worst, adv_bn, adv_hl)
        if (adv_bn >= g_bn or adv_hl >= g_hl) and u < t0 + h:
            t1, early = u, True
            break
    pb1, ph1 = fsr.price_at(*ser['px_bn'], t1), fsr.price_at(*ser['px_other'], t1)
    f_bn, f_hl = fsr.window_sum(bt, br, t0, t1), fsr.window_sum(ht, hr, t0, t1)
    bn = -s_bn * f_bn + s_bn * (pb1 / pb0 - 1.0) * 1e4 - BN_FEE_RT
    hl = -s_hl * f_hl + s_hl * (ph1 / ph0 - 1.0) * 1e4 - HL_FEE_RT
    return {'bn_bps': bn, 'hl_bps': hl, 'net_bps': bn + hl, 't_exit': t1, 'guard_close': early,
            'worst_adverse_pct': worst * 100.0}


def carry_account(trades: list, series: dict, equity0: float = EQUITY0, leverage: float = 2.0,
                  transfer_cost: float = 0.0, rebalance: bool = True, bn_min: Optional[dict] = None,
                  hl_min: float = HL_MIN_ORDER_USD, mmr: float = MMR) -> dict:
    """One slot (earliest entry, then the larger signal) on a Binance + Hyperliquid account that starts as
    equity0 - transfer_cost split in half. A round uses notional = leverage x the smaller venue balance and needs
    >= max(Binance MIN_NOTIONAL, Hyperliquid 10 USD); a transfer (cost `transfer_cost`) re-levels the two balances
    when that alone makes the round possible."""
    bn = hl = (equity0 - transfer_cost) / 2.0
    paid, rounds, skipped, rebal, t_free = transfer_cost, [], 0, 0, -1
    for tr in sorted(trades, key=lambda x: (x['t'], -x['signal_bps'])):
        t0 = int(round(tr['t'] * 1000))
        if t0 < t_free:
            continue
        need = max(hl_min, (bn_min or {}).get(f"{tr['coin']}USDT", BN_MIN_DEFAULT))
        if leverage * min(bn, hl) < need:
            if rebalance and leverage * (bn + hl - transfer_cost) / 2.0 >= need and abs(bn - hl) > 1e-9:
                bn = hl = (bn + hl - transfer_cost) / 2.0
                paid, rebal = paid + transfer_cost, rebal + 1
            else:
                skipped += 1
                continue
        n = leverage * min(bn, hl)
        legs = xv_legs(series[tr['coin']], tr, n / bn, n / hl, mmr)
        bn, hl = bn + n * legs['bn_bps'] * 1e-4, hl + n * legs['hl_bps'] * 1e-4
        t_free = legs['t_exit']
        rounds.append({'coin': tr['coin'], 't': t0, 'part': part_of(t0), 'notional': n, 'net_bps': legs['net_bps'],
                       'net_u': n * legs['net_bps'] * 1e-4, 'days': (legs['t_exit'] - t0) / DAY_MS,
                       'guard_close': legs['guard_close'], 'worst_adverse_pct': legs['worst_adverse_pct'],
                       'bn': bn, 'hl': hl})
    by = {}
    for name in ('TRAIN', 'HOLDOUT', 'FRESH'):
        r = [x for x in rounds if x['part'] == name]
        by[name] = {'rounds': len(r), 'net_u': float(sum(x['net_u'] for x in r)),
                    'mean_net_bps': float(np.mean([x['net_bps'] for x in r])) if r else None}
    days = sum(x['days'] for x in rounds)
    span = ((rounds[-1]['t'] - rounds[0]['t']) / DAY_MS + rounds[-1]['days']) if rounds else 0.0
    final = bn + hl
    return {'leverage': leverage, 'transfer_cost': transfer_cost, 'rounds': len(rounds), 'skipped_floor': skipped,
            'rebalances': rebal, 'transfers_paid_u': paid, 'guard_closes': sum(x['guard_close'] for x in rounds),
            'final_equity': final, 'net_u': final - equity0, 'trading_net_u': float(sum(x['net_u'] for x in rounds)),
            'days_deployed': days, 'span_days': span,
            'net_u_per_hour': (final - equity0) / (span * 24.0) if span > 0 else None,
            'by_part': by, 'path': rounds}


# ================================================================================================ C: XS (Binance only)
def xs_rounds(series: dict, h_days: int = XS_H_DAYS, k: float = XS_K, cost: float = XS_COST_BPS) -> list:
    """Every 8 h decision time: short the coin with the highest trailing 72 h Binance funding, long the lowest, when
    spread / 3 d x H >= k x cost. Rounds overlap (one per decision time); `one_slot` thins them."""
    from tools import funding_spread_replay as fsr
    start = fsr.ms(fsr.START) + fsr.LOOKBACK_MS
    t = start - start % H8 + H8
    end = min(int(s['bn'][0][-1]) for s in series.values())
    h, out = h_days * DAY_MS, []
    while t + h <= end:
        sig = {}
        for c, s in series.items():
            bt, br = s['bn']
            if fsr.covered(bt, t - fsr.LOOKBACK_MS, t, H8 + 1):
                sig[c] = fsr.window_sum(bt, br, t - fsr.LOOKBACK_MS, t)
        if len(sig) >= 2:
            hi_c, lo_c = max(sig, key=sig.get), min(sig, key=sig.get)
            spread = sig[hi_c] - sig[lo_c]
            if hi_c != lo_c and spread / 3.0 * h_days >= k * cost:
                a, b = series[hi_c], series[lo_c]
                pa0, pa1 = fsr.price_at(*a['px_bn'], t), fsr.price_at(*a['px_bn'], t + h)
                pb0, pb1 = fsr.price_at(*b['px_bn'], t), fsr.price_at(*b['px_bn'], t + h)
                if None not in (pa0, pa1, pb0, pb1) and fsr.covered(a['bn'][0], t, t + h, H8 + 1) \
                        and fsr.covered(b['bn'][0], t, t + h, H8 + 1):
                    fund = fsr.window_sum(*a['bn'], t, t + h) - fsr.window_sum(*b['bn'], t, t + h)
                    basis = ((pa0 - pa1) / pa0 + (pb1 - pb0) / pb0) * 1e4
                    out.append({'coin': f'{hi_c}/{lo_c}', 't': t / 1000, 'days': h_days, 'signal_bps': spread,
                                'funding_bps': fund, 'basis_bps': basis, 'cost_bps': cost,
                                'net_bps': fund + basis - cost})
        t += H8
    return out


def xs_verdict(holdout: dict, fresh: dict) -> dict:
    """Plan section 3 (T2): overlapping rounds >= MIN_N, month-bootstrap CI95 lower bound > 0, FRESH mean > 0."""
    checks = {'holdout_n>=%d' % MIN_N: holdout.get('n', 0) >= MIN_N,
              'holdout_ci95_month_lo>0': holdout.get('n', 0) > 0 and holdout['ci95_month'][0] > 0,
              'fresh_mean>0': fresh.get('n', 0) > 0 and fresh['mean_net_bps'] > 0}
    return {'checks': checks, 'verdict': 'EDGE' if all(checks.values()) else 'NO EDGE'}


def one_slot(rounds: list) -> list:
    out, end = [], -1.0
    for r in sorted(rounds, key=lambda x: x['t']):
        if r['t'] >= end:
            out.append(r)
            end = r['t'] + r['days'] * 86400
    return out


# ================================================================================================ D: funding snipe
def find_events(t: np.ndarray, r: np.ndarray, theta: float = THETA_BPS) -> list:
    """Settlements of one symbol that a rule would trade. P (strictly causal): the PREVIOUS print, at most 8 h + 1 min
    before, was >= theta in size. O (optimistic bound): the print at T itself is. -> [(T, prev bps | None, bps at T)]."""
    out = []
    for i in range(len(t)):
        prev = float(r[i - 1]) if i > 0 and 0 < t[i] - t[i - 1] <= H8 + MIN_MS else None
        if (prev is not None and abs(prev) >= theta) or abs(float(r[i])) >= theta:
            out.append((int(t[i]), prev, float(r[i])))
    return out


def load_events(universe: Path = UNIVERSE, theta: float = THETA_BPS) -> dict:
    """{symbol: [(T, prev, rate)]} for T inside a partition."""
    ev = {}
    for p in sorted(universe.glob('fr_*.json')):
        rows = json.loads(p.read_text())
        if len(rows) < 2:
            continue
        a = np.array(sorted(set(map(tuple, rows))), dtype=np.float64)
        e = [x for x in find_events(a[:, 0].astype(np.int64), a[:, 1], theta) if part_of(x[0])]
        if e:
            ev[p.stem[3:]] = e
    return ev


def count_events(ev: dict) -> dict:
    out = {}
    for sig, pick in (('P', lambda e: e[1] is not None and abs(e[1]) >= THETA_BPS), ('O', lambda e: abs(e[2]) >= THETA_BPS)):
        for name in PARTS:
            rows = [(s, e) for s, es in ev.items() for e in es if pick(e) and part_of(e[0]) == name]
            out[f'{sig}|{name}'] = {'events': len(rows), 'symbols': len({s for s, _ in rows}),
                                    'settlement_times': len({e[0] for _, e in rows}),
                                    'days': len({e[0] // DAY_MS for _, e in rows})}
    return out


def snipe_trade(o, h, l, c, side: int, rate_bps: float, entry: str, exit_: str, fill: str = 'cons',
                fees: tuple = (MAKER, TAKER, SLIP)) -> Optional[dict]:
    """One snipe on 1 m bars; index PRE is the bar that opens at the settlement T. side +1 long / -1 short;
    rate_bps is the print at T (a long PAYS it when > 0). Returns None when the post-only entry does not fill.

    entry M   post-only at the last trade before T - 5 min; filled when a bar of [T - 5, T) trades THROUGH the price
              (fill 'cons'), or always (fill 'opt': upper bound)
          TK  taker at the open of the T - 1 min bar, slip against
    exit  X0  taker at the open of the settlement bar
          D1  taker at the open of the T + 1 min bar (addendum A2, outside the frozen 8-cell grid)
          X1  post-only at that open; filled when a bar of [T, T + 10) trades through, else taker at T + 10 min
          TRx trailing stop `trail` bps off the best price, armed once the price has paid the round-trip fees;
              hard stop `stop` bps from the entry; taker at T + 60 min otherwise. Inside a bar the adverse extreme
              is read before the favourable one.
    """
    maker, taker, slip = fees
    i0 = PRE
    if entry == 'M':
        pe, fee_in = float(c[i0 - 6]), maker
        if fill == 'cons':
            seg = slice(i0 - 5, i0)
            if not ((float(np.min(l[seg])) < pe) if side > 0 else (float(np.max(h[seg])) > pe)):
                return None
    elif entry == 'TK':
        pe, fee_in = float(o[i0 - 1]) * (1.0 + side * slip * 1e-4), taker
    else:
        raise ValueError(f'unknown entry {entry}')
    fund = -side * rate_bps
    if exit_ == 'X0':
        px, fee_out, why, hold = float(o[i0]) * (1.0 - side * slip * 1e-4), taker, 'T0', 0
    elif exit_ == 'D1':
        px, fee_out, why, hold = float(o[i0 + 1]) * (1.0 - side * slip * 1e-4), taker, 'D1', 1
    elif exit_ == 'X1':
        q, seg = float(o[i0]), slice(i0, i0 + 10)
        if (float(np.max(h[seg])) > q) if side > 0 else (float(np.min(l[seg])) < q):
            px, fee_out, why, hold = q, maker, 'MK', 0
        else:
            px, fee_out, why, hold = float(o[i0 + 10]) * (1.0 - side * slip * 1e-4), taker, 'TO', 10
    elif exit_ in TRAILS:
        trail, stop = TRAILS[exit_]
        shield, stop_px = maker + taker + slip, pe * (1.0 - side * stop * 1e-4)
        best, armed, lvl, why, hold = pe, False, None, 'TM', MAX_HOLD_MIN
        for j in range(i0, i0 + MAX_HOLD_MIN):
            adverse, fav, op = (float(l[j]), float(h[j]), float(o[j])) if side > 0 else (float(h[j]), float(l[j]), float(o[j]))
            if side * (adverse - stop_px) <= 0:
                lvl, why, hold = (stop_px if side * (op - stop_px) > 0 else op), 'SL', j - i0
                break
            if armed:
                tr_px = best * (1.0 - side * trail * 1e-4)
                if side * (adverse - tr_px) <= 0:
                    lvl, why, hold = (tr_px if side * (op - tr_px) > 0 else op), 'TR', j - i0
                    break
            if side * (fav - best) > 0:
                best = fav
            if side * (best / pe - 1.0) * 1e4 >= shield:
                armed = True
        if lvl is None:
            lvl = float(o[i0 + MAX_HOLD_MIN])
        px, fee_out = lvl * (1.0 - side * slip * 1e-4), taker
    else:
        raise ValueError(f'unknown exit {exit_}')
    price = side * (px / pe - 1.0) * 1e4
    return {'net': price + fund - fee_in - fee_out, 'price': price, 'fund': fund, 'fee': fee_in + fee_out, 'why': why,
            'hold_min': hold}


def bars_of(rows: list, t_settle: int) -> Optional[tuple]:
    """Cached kline rows -> (o, h, l, c) aligned so that index PRE opens at T; None unless T - PRE .. T + 60 min are
    all present (a missing minute is UNKNOWN, never filled in)."""
    need = PRE + MAX_HOLD_MIN + 1
    if not rows or len(rows) < need:
        return None
    a = np.asarray(rows, dtype=np.float64)
    idx = np.rint((a[:, 0] - (t_settle - PRE * MIN_MS)) / MIN_MS).astype(np.int64)
    if idx[0] != 0 or not np.array_equal(idx[:need], np.arange(need)):
        return None
    return a[:need, 1], a[:need, 2], a[:need, 3], a[:need, 4]


def drift(o, c, side: int) -> dict:
    """Price move in favour of the receiving side (bps), no fees: what the funding has to beat."""
    i0, f = PRE, lambda a, b: side * (b / a - 1.0) * 1e4
    return {'pre5': f(c[i0 - 6], o[i0]), 'post1': f(o[i0], o[i0 + 1]), 'post10': f(o[i0], o[i0 + 10]),
            'post60': f(o[i0], o[i0 + 60])}


def simulate_events(ev: dict, klines: Path = KLINES, fill: str = 'cons', fees: tuple = (MAKER, TAKER, SLIP),
                    exits: tuple = EXITS) -> tuple:
    """Every event x signal x cell -> (rows {sym, T, part, day, sig, strength, cells{...}, drift}, windows without
    complete bars)."""
    rows, missing = [], 0
    for sym, es in sorted(ev.items()):
        p = klines / f'kl_{sym}.json'
        cache = json.loads(p.read_text()) if p.exists() else {}
        for t, prev, rate in es:
            b = bars_of(cache.get(str(t)), t)
            if b is None:
                missing += 1
                continue
            for sig, strength in (('P', prev), ('O', rate)):
                if strength is None or abs(strength) < THETA_BPS:
                    continue
                side = 1 if strength < 0 else -1
                cells = {}
                for en in ENTRIES:
                    for ex in exits:
                        r = snipe_trade(*b, side, rate, en, ex, fill, fees)
                        if r is not None:
                            cells[f'{en}|{ex}'] = r
                rows.append({'sym': sym, 't': t, 'part': part_of(t), 'day': t // DAY_MS, 'sig': sig,
                             'strength': abs(strength), 'rate': rate, 'side': side, 'cells': cells,
                             'drift': drift(b[0], b[3], side)})
    return rows, missing


def cell_table(rows: list, sig: str, part: str, key: str = 'net', n_trials: int = N_TRIALS, exits: tuple = EXITS) -> dict:
    out = {}
    sub = [r for r in rows if r['sig'] == sig and r['part'] == part]
    for en in ENTRIES:
        for ex in exits:
            cell = f'{en}|{ex}'
            got = [r for r in sub if cell in r['cells']]
            st = cluster_stats([r['cells'][cell][key] for r in got], [r['day'] for r in got],
                               n_trials if key == 'net' else 0)
            st['signals'] = len(sub)
            st['fill_rate'] = len(got) / len(sub) if sub else None
            if got and key == 'net':
                for k in ('price', 'fund', 'fee'):
                    st[k] = float(np.mean([r['cells'][cell][k] for r in got]))
            out[cell] = st
    return out


def choose_cell(train: dict) -> Optional[str]:
    """TRAIN-best cell by mean net bps among cells with n >= MIN_N (ties: the first in grid order)."""
    ok = [k for k, v in train.items() if v.get('n', 0) >= MIN_N]
    return max(ok, key=lambda k: train[k]['mean']) if ok else None


def stop_frac_of(cell: Optional[str]) -> float:
    """Stop distance of a cell as a fraction of the notional: its hard stop, 50 bps for exits without one."""
    ex = (cell or '').split('|')[-1]
    return (TRAILS[ex][1] if ex in TRAILS else 50.0) * 1e-4


def top1_series(rows: list, sig: str, part: str, cell: str) -> tuple:
    """A 10 U account quotes ONE symbol per settlement time: the strongest signal with bars; an unfilled maker entry
    is no trade. -> (net bps per trade, UTC day per trade, days in the partition)."""
    best = {}
    for r in rows:
        if r['sig'] == sig and r['part'] == part and (r['t'] not in best or r['strength'] > best[r['t']]['strength']):
            best[r['t']] = r
    got = [r for _, r in sorted(best.items()) if cell in r['cells']]
    lo, hi = PARTS[part]
    return (np.array([r['cells'][cell]['net'] for r in got]), np.array([r['day'] for r in got]),
            (ms(hi) + DAY_MS - ms(lo)) / DAY_MS)


# ---- klines (public REST, cached per symbol)
_lock, _weight = threading.Lock(), [0]


def _klines(sym: str, start: int):
    import urllib.error
    import urllib.request
    from urllib.parse import quote
    url = (f'https://fapi.binance.com/fapi/v1/klines?symbol={quote(sym)}&interval=1m&startTime={start}'
           f'&limit={KL_LIMIT}')
    for k in range(6):
        with _lock:
            wait = 20.0 if _weight[0] > 1800 else 0.0
        if wait:
            time.sleep(wait)
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'research'})
            with urllib.request.urlopen(req, timeout=30) as r:
                with _lock:
                    _weight[0] = int(r.headers.get('X-MBX-USED-WEIGHT-1M') or 0)
                return [[int(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]), float(x[5])] for x in json.load(r)]
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                time.sleep(float(e.headers.get('Retry-After') or 30) + 5 * k)
            elif e.code == 400:
                return []
            elif k == 5:
                raise
            else:
                time.sleep(2 + 3 * k)
        except Exception:
            if k == 5:
                raise
            time.sleep(2 + 3 * k)
    raise RuntimeError(f'rate limited: {url}')


def fetch_symbol(sym: str, times: list, out: Path = KLINES) -> int:
    p = out / f'kl_{sym}.json'
    cache = json.loads(p.read_text()) if p.exists() else {}
    todo = [t for t in times if str(t) not in cache]
    for i, t in enumerate(todo):
        cache[str(t)] = _klines(sym, t - PRE * MIN_MS)
        if (i + 1) % 200 == 0 or i == len(todo) - 1:
            tmp = p.with_suffix('.tmp')
            tmp.write_text(json.dumps(cache))
            tmp.replace(p)
    return len(todo)


def fetch_klines(ev: dict, workers: int = 4, log=print) -> int:
    KLINES.mkdir(parents=True, exist_ok=True)
    jobs = sorted(((s, sorted({e[0] for e in es})) for s, es in ev.items()), key=lambda x: -len(x[1]))
    done, t0 = [0], time.time()

    def work(job):
        n = fetch_symbol(*job)
        with _lock:
            done[0] += 1
            if done[0] % 25 == 0:
                log(f'{done[0]}/{len(jobs)} symbols, {time.time() - t0:.0f} s, weight {_weight[0]}')
        return n
    with ThreadPoolExecutor(workers) as ex:
        return sum(ex.map(work, jobs))


# ================================================================================================ E: growth
def growth_path(draw, per_day: float, rng: np.random.Generator, days: int = DAYS, equity0: float = EQUITY0,
                mode: str = 'flat', base_notional: float = 10.0, stop_frac: float = 0.005, k: float = 5.0,
                risk_frac: float = 0.05, max_lev: float = MAX_LEV, ruin: float = RUIN_U,
                cap_notional: Optional[float] = None) -> dict:
    """One 30-day path. `draw()` -> net bps of one trade on its notional. Sizing:
    flat     base_notional
    streak   core.position_sizer.streak_scaled_notional (win streak >= 2, confluence taken as 3 / 3 on every trade:
             an upper bound on how often the step-up fires), principal shielded
    frac     risk_frac x equity lost at the stop: notional = risk_frac x equity / stop_frac (the directive's 5-10 %)
    Notional never exceeds max_lev x equity (nor `cap_notional`, a book-capacity assumption, when given); one trade
    cannot lose more than the equity (the account is liquidated, it does not owe: erratum E1 of the report); the path
    stops for good below `ruin` (it cannot margin one lot)."""
    from core.position_sizer import streak_scaled_notional
    eq, cum, streak, peak, mdd, trades = equity0, 0.0, 0, equity0, 0.0, 0
    for _ in range(days):
        for _ in range(int(rng.poisson(per_day))):
            if eq < ruin:
                return {'equity': eq, 'ruined': True, 'trades': trades, 'mdd': mdd}
            if mode == 'flat':
                n = base_notional
            elif mode == 'streak':
                n = streak_scaled_notional(base_notional, cum, streak, 3, stop_frac, k=k)
            elif mode == 'frac':
                n = risk_frac * eq / stop_frac
            else:
                raise ValueError(f'unknown sizing {mode}')
            n = min(n, max_lev * eq) if cap_notional is None else min(n, max_lev * eq, cap_notional)
            pnl = max(n * draw() * 1e-4, -eq)
            eq, cum, trades = eq + pnl, cum + pnl, trades + 1
            streak = streak + 1 if pnl > 0 else 0
            peak = max(peak, eq)
            mdd = max(mdd, peak - eq)
    return {'equity': eq, 'ruined': eq < ruin, 'trades': trades, 'mdd': mdd}


def growth_mc(net_bps, per_day: float, paths: int = PATHS, seed: int = SEED, **kw) -> dict:
    x = np.asarray(net_bps, dtype=np.float64)
    if x.size == 0:
        return {'paths': 0}
    rng = np.random.default_rng(seed)
    res = [growth_path(lambda: float(x[rng.integers(0, x.size)]), per_day, rng, **kw) for _ in range(paths)]
    eq = np.array([r['equity'] for r in res])
    e0, days = kw.get('equity0', EQUITY0), kw.get('days', DAYS)
    return {'paths': paths, 'p05': float(np.quantile(eq, 0.05)), 'median': float(np.median(eq)),
            'p95': float(np.quantile(eq, 0.95)), 'mean': float(eq.mean()), 'max': float(eq.max()),
            'p_ruin': float(np.mean([r['ruined'] for r in res])), 'p_profit': float((eq > e0).mean()),
            'p_double': float((eq >= 2 * e0).mean()),
            'p_target_20u_per_hour': float((eq >= e0 + TARGET_PER_HOUR * 24 * days).mean()),
            'mean_u_per_hour': float((eq.mean() - e0) / (24.0 * days)),
            'mean_trades': float(np.mean([r['trades'] for r in res])), 'mean_mdd': float(np.mean([r['mdd'] for r in res]))}


def target_arithmetic(equity: float = EQUITY0, target_per_hour: float = TARGET_PER_HOUR, max_lev: float = MAX_LEV,
                      best_bps: Optional[float] = None, per_hour: Optional[float] = None) -> dict:
    """What +target_per_hour needs from `equity` at the venue's maximum notional, against a measured edge."""
    cap = equity * max_lev
    need = target_per_hour / cap * 1e4
    out = {'max_notional': cap, 'net_bps_per_hour_needed': need, 'pct_of_equity_per_hour': target_per_hour / equity * 100}
    if best_bps is not None and per_hour is not None:
        have = best_bps * per_hour
        out.update(measured_bps_per_hour=have, shortfall_x=(need / have if have > 0 else math.inf))
    return out


# ================================================================================================ ledger at 10 U
def ledger_view(path: Path = ROOT / 'data' / 'engine_trades.jsonl', orders: Path = ROOT / 'data' / 'engine_orders.jsonl') -> dict:
    import re
    tr = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]
    auto = [t for t in tr if t.get('tag') == 'AUTO' and t.get('qty') and t.get('entry')]
    bps = [t['pnl'] / abs(t['qty'] * t['entry']) * 1e4 for t in auto]
    days = sorted({t['day'] for t in auto})
    pat = re.compile(r'margin|insufficient|liquidat|balance|-2019|-2027|-4164', re.I)
    n_orders = n_margin = 0
    for line in orders.read_text(encoding='utf-8').splitlines():
        if line.strip():
            r = json.loads(line)
            n_orders += 1
            n_margin += bool(pat.search(' '.join(str(r.get(k, '')) for k in ('why', 'error', 'outcome'))))
    micro = [b for t, b in zip(auto, bps) if t.get('micro')]
    return {'auto_trades': len(auto), 'net_bps': bps, 'mean_net_bps': float(np.mean(bps)), 'active_days': len(days),
            'trades_per_active_day': len(auto) / max(1, len(days)), 'order_rows': n_orders,
            'margin_or_liquidation_rows': n_margin, 'micro_trades': len(micro), 'micro_net_bps': micro,
            'micro_mean_net_bps': float(np.mean(micro)) if micro else None,
            'max_notional': float(max(abs(t['qty'] * t['entry']) for t in auto))}


# ================================================================================================ run
def basis_dislocation(series: dict, cost: float = 21.0, lookback: int = 42) -> dict:
    """Descriptive, upper bound (4 h opens of two venues are not simultaneous quotes): fade the Hyperliquid - Binance
    basis when it sits `cost` bps away from its trailing 7-day median, leave at the next 4 h open, taker both ways."""
    pnl, n_bars = [], 0
    for s in series.values():
        (tb, pb), (th, ph) = s['px_bn'], s['px_other']
        common, ib, ih = np.intersect1d(tb, th, return_indices=True)
        if common.size <= lookback + 1:
            continue
        b = np.log(ph[ih] / pb[ib]) * 1e4
        for i in range(lookback, b.size - 1):
            d = b[i] - np.median(b[i - lookback:i])
            n_bars += 1
            if abs(d) >= cost:
                pnl.append(float(np.sign(d) * (b[i] - b[i + 1]) - cost))
    x = np.array(pnl)
    return {'bars': n_bars, 'signals': int(x.size), 'share': float(x.size / n_bars) if n_bars else None,
            'mean_net_bps': float(x.mean()) if x.size else None, 'win': float((x > 0).mean()) if x.size else None,
            'cost_bps': cost}


def run(log=print, paths: int = PATHS) -> dict:
    from core.funding_carry import two_leg_capital_gate
    from tools import funding_spread_replay as fsr
    out = {'plan': PLAN, 'parts': PARTS, 'costs': SCHEDULES}
    info = json.loads((UNIVERSE / 'exchangeInfo.json').read_text())
    mins = min_notional(info)
    # ---- A
    gate = two_leg_capital_gate(EQUITY0, None, 'live')
    out['A_floors'] = {
        'bn_min_notional_counts': {str(k): int(v) for k, v in zip(*np.unique(list(mins.values()), return_counts=True))},
        'bn_symbols_above_5': sorted(s for s, v in mins.items() if v > 5),
        'capital_gate_10u': {'mode': gate.mode, 'reason': gate.reason},
        'two_venue': {f'L{lv:g}|c{c:g}': two_venue_plan(EQUITY0, lv, transfer_cost=c)
                      for lv in (1.0, 2.0, 3.0) for c in TRANSFER_COSTS},
        'one_venue': {f'L{lv:g}': one_venue_plan(EQUITY0, lv) for lv in (1.0, 2.0, 3.0)},
        'guard_move_pct': {f'L{lv:g}': {'short': guard_move(lv, -1) * 100, 'long': guard_move(lv, 1) * 100,
                                        'liq_short': liq_move(lv, -1) * 100, 'liq_long': liq_move(lv, 1) * 100}
                           for lv in (2.0, 3.0)}}
    # ---- B
    series, trades = load_xv()
    out['B_carry'] = {'replay_holdout': fsr.summarize(trades, *PARTS['HOLDOUT']),
                      'replay_fresh': fsr.summarize(trades, *PARTS['FRESH']),
                      'accounts': {f'L{lv:g}|c{c:g}': carry_account(trades, series, EQUITY0, lv, c, bn_min=mins)
                                   for lv in CARRY_LEVERAGES for c in TRANSFER_COSTS}}
    log('B ' + ' | '.join(f"{k}: rounds {v['rounds']} net {v['net_u']:+.4f} U" for k, v in out['B_carry']['accounts'].items()))
    # ---- C
    xs = xs_rounds(series)
    slot = one_slot(xs)
    out['C_xs'] = {p: fsr.summarize(xs, *PARTS[p]) for p in PARTS}
    out['C_xs']['t2'] = xs_verdict(out['C_xs']['HOLDOUT'], out['C_xs']['FRESH'])
    out['C_xs']['one_slot'] = {p: {'n': len(r), 'net_bps': [round(x['net_bps'], 2) for x in r],
                                   'pairs': [x['coin'] for x in r]}
                               for p in PARTS for r in [[x for x in slot if part_of(int(x['t'] * 1000)) == p]]}
    log('C ' + ' | '.join(f"{p}: n {out['C_xs'][p].get('n')} mean {out['C_xs'][p].get('mean_net_bps')}" for p in PARTS)
        + f" -> {out['C_xs']['t2']['verdict']}")
    # ---- D
    ev = load_events()
    out['D_events'] = count_events(ev)
    rows, missing = simulate_events(ev)
    out['D_missing_bars'] = missing
    tables = {sig: {p: cell_table(rows, sig, p) for p in PARTS} for sig in ('P', 'O')}
    t1 = choose_cell(tables['P']['TRAIN'])
    out['D_snipe'] = {'tables': tables, 't1_cell': t1}
    if t1:
        tr, ho, fr = (tables['P'][p][t1] for p in ('TRAIN', 'HOLDOUT', 'FRESH'))
        out['D_snipe']['t1'] = {'TRAIN': tr, 'HOLDOUT': ho, 'FRESH': fr, **verdict(tr, ho, fr)}
        log(f"D T1 {t1}: TRAIN {tr.get('mean')} HOLDOUT {ho.get('mean')} +- {ho.get('se')} FRESH {fr.get('mean')} "
            f"-> {out['D_snipe']['t1']['verdict']}")
    o_best = choose_cell(tables['O']['TRAIN'])
    out['D_snipe']['oracle_best_cell'] = o_best
    out['D_snipe']['drift'] = {
        sig: {p: {k: cluster_stats([r['drift'][k] for r in rows if r['sig'] == sig and r['part'] == p],
                                   [r['day'] for r in rows if r['sig'] == sig and r['part'] == p], 0)
                  for k in ('pre5', 'post1', 'post10', 'post60')} for p in PARTS} for sig in ('P', 'O')}
    out['D_snipe']['funding'] = {
        sig: {p: cluster_stats([-r['side'] * r['rate'] for r in rows if r['sig'] == sig and r['part'] == p],
                               [r['day'] for r in rows if r['sig'] == sig and r['part'] == p], 0) for p in PARTS}
        for sig in ('P', 'O')}
    cell = t1 or 'M|X0'
    out['D_snipe']['tiers'] = {
        sig: {f'>={lo:g}': cluster_stats(
            [r['cells'][cell]['net'] for r in rows if r['sig'] == sig and r['part'] == 'HOLDOUT' and cell in r['cells']
             and lo <= r['strength'] < hi],
            [r['day'] for r in rows if r['sig'] == sig and r['part'] == 'HOLDOUT' and cell in r['cells']
             and lo <= r['strength'] < hi], 0)
            for lo, hi in zip(TIERS, TIERS[1:] + (math.inf,))} for sig in ('P', 'O')}
    out['D_snipe']['schedules'] = {}
    for name, fees in SCHEDULES.items():
        r2, _ = simulate_events(ev, fees=fees)
        out['D_snipe']['schedules'][name] = {sig: cell_table(r2, sig, 'HOLDOUT', n_trials=0) for sig in ('P', 'O')}
    r_opt, _ = simulate_events(ev, fill='opt')
    out['D_snipe']['opt_fill_holdout'] = {sig: cell_table(r_opt, sig, 'HOLDOUT', n_trials=0) for sig in ('P', 'O')}
    # ---- E
    led = ledger_view()
    out['ledger'] = {k: v for k, v in led.items() if k not in ('net_bps', 'micro_net_bps')}
    growth = {}
    net, _, span = top1_series(rows, 'P', 'HOLDOUT', cell)
    per_day = net.size / span
    out['D_snipe']['top1'] = {'cell': cell, 'trades': int(net.size), 'per_day': per_day,
                              'mean_net_bps': float(net.mean()) if net.size else None}
    sources = {'snipe_P_top1': (net, per_day, stop_frac_of(cell)),
               'ledger_auto': (np.array(led['net_bps']), led['trades_per_active_day'], stop_frac_of(None))}
    net_o, _, _ = top1_series(rows, 'O', 'HOLDOUT', o_best or cell)
    sources['snipe_O_top1_bound'] = (net_o, net_o.size / span, stop_frac_of(o_best or cell))
    for name, (x, rate, stop) in sources.items():
        growth[name] = {'trades_per_day': rate, 'mean_net_bps': float(np.mean(x)) if len(x) else None,
                        'flat_10u': growth_mc(x, rate, paths, mode='flat', base_notional=10.0),
                        'streak_k5': growth_mc(x, rate, paths, mode='streak', base_notional=10.0, stop_frac=stop, k=5.0),
                        'frac_5pct': growth_mc(x, rate, paths, mode='frac', risk_frac=0.05, stop_frac=stop),
                        'frac_10pct': growth_mc(x, rate, paths, mode='frac', risk_frac=0.10, stop_frac=stop)}
        for e0 in (5.0, 10.0):
            growth[name][f'flat_5.05u_lot_from_{e0:g}u'] = growth_mc(x, rate, paths, mode='flat', base_notional=5.05,
                                                                     equity0=e0)
    out['E_growth'] = growth
    best = max((float(np.mean(x)) for x, _, _ in sources.values() if len(x)), default=None)
    out['E_target'] = target_arithmetic(best_bps=out['D_snipe']['top1']['mean_net_bps'], per_hour=per_day / 24.0)
    out['E_target']['best_source_mean_bps'] = best
    out['basis_dislocation'] = basis_dislocation(series)
    return out


def addendum(paths: int = PATHS) -> dict:
    """A2, computed AFTER the frozen run and outside its grid: the same entries with the taker exit at the open of the
    T + 1 min bar instead of the first print after T. Reported without a verdict."""
    rows, _ = simulate_events(load_events(), exits=('X0', 'D1'))
    out = {'delayed_exit': {sig: {p: cell_table(rows, sig, p, n_trials=0, exits=('X0', 'D1')) for p in PARTS}
                            for sig in ('P', 'O')}, 'growth': {}, 'cap_notional': CAP_NOTIONAL}
    for cell in ('M|X0', 'M|D1'):
        net, _, span = top1_series(rows, 'P', 'HOLDOUT', cell)
        rate, stop = net.size / span, stop_frac_of(cell)
        out['growth'][cell] = {
            'trades_per_day': rate, 'mean_net_bps': float(net.mean()), 'worst_bps': float(net.min()),
            'p01_bps': float(np.quantile(net, 0.01)),
            'flat_10u': growth_mc(net, rate, paths, mode='flat', base_notional=10.0),
            'flat_200u_20x': growth_mc(net, rate, paths, mode='flat', base_notional=200.0),
            'streak_k5': growth_mc(net, rate, paths, mode='streak', base_notional=10.0, stop_frac=stop, k=5.0),
            'frac_5pct': growth_mc(net, rate, paths, mode='frac', risk_frac=0.05, stop_frac=stop),
            'frac_10pct': growth_mc(net, rate, paths, mode='frac', risk_frac=0.10, stop_frac=stop),
            'streak_k5_cap': growth_mc(net, rate, paths, mode='streak', base_notional=10.0, stop_frac=stop, k=5.0,
                                       cap_notional=CAP_NOTIONAL),
            'frac_5pct_cap': growth_mc(net, rate, paths, mode='frac', risk_frac=0.05, stop_frac=stop,
                                       cap_notional=CAP_NOTIONAL),
            'frac_10pct_cap': growth_mc(net, rate, paths, mode='frac', risk_frac=0.10, stop_frac=stop,
                                        cap_notional=CAP_NOTIONAL)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('stage', choices=('events', 'klines', 'run', 'addendum'))
    ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args(argv)
    if a.stage == 'events':
        ev = load_events()
        res = {'theta_bps': THETA_BPS, 'symbols_with_history': len(list(UNIVERSE.glob('fr_*.json'))),
               'symbols_with_events': len(ev), 'kline_requests': sum(len({e[0] for e in es}) for es in ev.values()),
               'counts': count_events(ev)}
        EVENTS_OUT.write_text(json.dumps(res, indent=1))
        print(json.dumps(res, indent=1))
        return 0
    if a.stage == 'klines':
        print('fetched', fetch_klines(load_events(), a.workers), 'windows')
        return 0
    if a.stage == 'addendum':
        res = addendum()
        (ROOT / 'data' / 'carry_convex_addendum.json').write_text(json.dumps(res, indent=1, default=float))
        for sig, parts in res['delayed_exit'].items():
            for part, t in parts.items():
                print(sig, part, ' | '.join(f"{c} {v['mean']:+.2f} +- {v['se']:.2f} (n {v['n']})" for c, v in t.items() if v.get('n')))
        for cell, gr in res['growth'].items():
            for k, v in gr.items():
                if isinstance(v, dict):
                    print(f"{cell} {k:<15} median {v['median']:.2f} P05 {v['p05']:.2f} P95 {v['p95']:.2f} ruin {v['p_ruin']:.3f} "
                          f"profit {v['p_profit']:.3f} target {v['p_target_20u_per_hour']:.4f} U/h {v['mean_u_per_hour']:+.4f}")
        return 0
    t0 = time.time()
    res = run()
    res['seconds'] = round(time.time() - t0, 1)
    OUT.write_text(json.dumps(res, indent=1, default=float))
    print(f'wrote {OUT} in {res["seconds"]} s')
    return 0


if __name__ == '__main__':
    sys.exit(main())
