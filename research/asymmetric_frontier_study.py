"""AF-1: asymmetric frontiers for a 10 USDT base (2026-11-15). Offline, public data, no orders, no account.

    python -m research.asymmetric_frontier_study kalshi-fetch   # sampled 300 s trade windows + market results (public REST)
    python -m research.asymmetric_frontier_study run            # T-A, B, T-C, growth -> data/asymmetric_frontier_study.json

Pre-registration: docs/research_upgrade/plan_asymmetric_frontier_10u_2026_11.md (frozen before any maker-entry
outcome was simulated and before any Kalshi trade sample was downloaded).

A  maker conversion: the HWK-1 momentum triggers entered with a resting limit instead of a taker order, priced with
   Hyperliquid's real fee schedule (tier 0: maker +1.5 bps, taker 4.5 bps) and with the directive's hypothetical
   rebates. Binance 1 m paths are the proxy; fills need a trade-through.
B  CEX-DEX gap arithmetic for a 5 U round (no data, no verdict).
C  Kalshi takers who bought contracts at 10 cents or less: realised return by price bucket and time to close.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research import hawkes_momentum_study as hwk  # noqa: E402

PLAN = 'docs/research_upgrade/plan_asymmetric_frontier_10u_2026_11.md'
AF = ROOT / 'data' / 'af'
OUT = ROOT / 'data' / 'asymmetric_frontier_study.json'
SEED = 20261116
DAY_S = 86_400


# ================================================================================================ A: maker conversion
S1 = hwk.Cfg(ref='BAR', hl=5, theta=3.0, kappa=3.0, w=15, dir='BOTH', filt='ALL', t=600.0, rr=2.5, h=240, cooldown=60)
S2 = hwk.Cfg(ref='BAR', hl=5, theta=2.0, kappa=3.0, w=30, dir='BOTH', filt='ALL', t=600.0, rr=2.5, h=120, cooldown=60)
K_BPS, TTLS = (0, 10, 25), (1, 5, 15)
SLIP_A = 3.0
SCHEDULES = {'HL0': (1.5, 4.5), 'BN0': (2.0, 5.0), 'HL_TOP': (-0.3, 2.4), 'DIR15': (-1.5, 4.5), 'DIR30': (-3.0, 4.5),
             'ZERO': (0.0, 0.0)}
VERDICT_SCHEDULE = 'HL0'
PRIOR_TRIALS = 38                                # configurations HWK-1 read on the same partitions
A_MIN_TRAIN, A_MIN_HOLDOUT, A_MIN_FRESH = 200, 100, 20
UNFILLED = -1


def cell_name(k_bps: float, ttl: int) -> str:
    return f'k{k_bps:g}|ttl{ttl}'


def sim_maker(o, h, l, c, s: int, side: int, k_bps: float, ttl: int, t: float, stop: float, hold: int,
              n_bars: Optional[int] = None) -> tuple:
    """Post-only limit at L = c[s] x (1 - side x k / 1e4), alive for bars s+1 .. s+ttl; filled at L by the first bar
    that trades THROUGH it. -> (fill bar or UNFILLED, exit bar, gross bps from L, reason). After the fill: stop first
    (at the level, or at the open when a later bar opens beyond it; the fill bar counts), target never in the fill
    bar, a bar that reaches both is a stop, time exit at the close of bar fill + hold - 1."""
    n_bars = len(c) if n_bars is None else int(n_bars)
    lim = float(c[s]) * (1.0 - side * k_bps * 1e-4)
    j1 = min(s + 1 + ttl, n_bars)
    if j1 <= s + 1:
        return UNFILLED, s, math.nan, hwk.EOD
    w = np.asarray(l[s + 1:j1], dtype=np.float64) < lim if side > 0 else np.asarray(h[s + 1:j1], dtype=np.float64) > lim
    if not w.any():
        return UNFILLED, j1 - 1, math.nan, hwk.EOD
    j = s + 1 + int(np.argmax(w))
    e1 = min(j + hold, n_bars)
    k = side * 1e4 / lim
    op = (np.asarray(o[j:e1], dtype=np.float64) - lim) * k
    hi = (np.asarray(h[j:e1], dtype=np.float64) - lim) * k
    lo = (np.asarray(l[j:e1], dtype=np.float64) - lim) * k
    up, dn = (hi, lo) if side > 0 else (lo, hi)
    hit_s, hit_t = dn <= -stop, up >= t
    hit_t[0] = False                                   # the bar's high may precede the fill
    js = int(np.argmax(hit_s)) if hit_s.any() else -1
    jt = int(np.argmax(hit_t)) if hit_t.any() else -1
    if js >= 0 and (jt < 0 or js <= jt):
        return j, j + js, float(min(-stop, op[js])), hwk.STOP    # a bar that opens beyond the stop fills at its open
    if jt >= 0:
        return j, j + jt, float(t), hwk.TP
    return j, e1 - 1, float((float(c[e1 - 1]) - lim) * k), hwk.TIME if e1 == j + hold else hwk.EOD


def run_maker(f: hwk.Features, cfg: hwk.Cfg, k_bps: float, ttl: int) -> dict:
    """Every trigger of `cfg` attempted with a maker entry, one position per symbol: an unfilled order frees the
    symbol at s + ttl + 1, a filled one after its exit plus the cooldown. Each attempt also carries the gross of the
    taker entry at the next open on the same trigger (the HWK-1 execution)."""
    mx = f.mx
    m, side = f.mask(cfg)
    keys = ('row', 'i', 's', 'side', 'filled', 'j_fill', 'j_exit', 'gross', 'reason', 'taker_gross', 'taker_reason')
    out = {k: [] for k in keys}
    free, prev_sym = -1, None
    for i in np.flatnonzero(m.any(axis=1)):
        p = int(f.rows[i])
        sym = mx['sym'][p]
        if sym != prev_sym:
            free, prev_sym = -1, sym
        cols, base, nb = np.flatnonzero(m[i]) + hwk.D0, int(f.base_min[i]), int(mx['n_bars'][p])
        q = 0
        while q < len(cols):
            s = int(cols[q])
            if base + s < free:
                q = int(np.searchsorted(cols, free - base, side='left'))
                continue
            if s + 1 >= nb:
                break
            sd = int(side[i, s - hwk.D0])
            o, h, l, c = mx['o'][p], mx['h'][p], mx['l'][p], mx['c'][p]
            j, je, gross, why = sim_maker(o, h, l, c, s, sd, k_bps, ttl, cfg.t, cfg.s, cfg.h, nb)
            _, tg, twhy = hwk.sim_trade(o, h, l, c, s + 1, sd, cfg.t, cfg.s, cfg.h, n_bars=nb)
            free = base + (s + ttl + 1 if j == UNFILLED else je + cfg.cooldown)
            for key, v in zip(keys, (p, i, s, sd, j != UNFILLED, j, je, gross, why, tg, twhy)):
                out[key].append(v)
            q += 1
    tr = {k: np.array(v, dtype=(np.float64 if k in ('gross', 'taker_gross') else bool if k == 'filled' else np.int64))
          for k, v in out.items()}
    t0 = (np.array([hwk.ms(d) - hwk.DAY_MS for d in mx['day'][tr['row']]], dtype=np.int64) if len(tr['row'])
          else np.zeros(0, np.int64))
    tr['t_entry'], tr['t_exit'] = t0 + tr['j_fill'] * hwk.MIN_MS, t0 + (tr['j_exit'] + 1) * hwk.MIN_MS
    tr['day'], tr['sym'] = mx['day'][tr['row']], mx['sym'][tr['row']]
    return tr


def maker_cost(reason, sched: str) -> np.ndarray:
    """Round-trip cost in bps: maker entry, maker target, taker (+ slip) at the stop, the time exit or the data end."""
    f_m, f_t = SCHEDULES[sched]
    slip = 0.0 if sched == 'ZERO' else SLIP_A
    return f_m + np.where(np.asarray(reason) == hwk.TP, f_m, f_t + slip)


def taker_cost(reason, sched: str) -> np.ndarray:
    """The HWK-1 execution under a schedule: taker (+ slip) entry, maker target, taker (+ slip) otherwise."""
    f_m, f_t = SCHEDULES[sched]
    slip = 0.0 if sched == 'ZERO' else SLIP_A
    return (f_t + slip) + np.where(np.asarray(reason) == hwk.TP, f_m, f_t + slip)


def breakeven_maker_fee(gross, reason, f_t: float = 4.5, slip: float = SLIP_A) -> Optional[float]:
    """f_m* with mean net = 0: (mean gross - (f_t + slip) x (1 - p_TP)) / (1 + p_TP). Negative = a rebate is needed."""
    gross = np.asarray(gross, dtype=np.float64)
    if gross.size == 0:
        return None
    p_tp = float((np.asarray(reason) == hwk.TP).mean())
    return float((gross.mean() - (f_t + slip) * (1.0 - p_tp)) / (1.0 + p_tp))


def maker_summary(tr: dict, stop: float, n_trials: int = 0) -> dict:
    """One cell on one partition: fills, net per fill under every schedule, adverse selection, break-even fee."""
    n = len(tr['filled'])
    if n == 0:
        return {'triggers': 0, 'n': 0}
    fl = tr['filled']
    out = {'triggers': int(n), 'n': int(fl.sum()), 'fill_rate': float(fl.mean())}
    if not fl.any():
        return out
    g, why, day = tr['gross'][fl], tr['reason'][fl], tr['day'][fl]
    net = g - maker_cost(why, VERDICT_SCHEDULE)
    out.update(hwk.cluster_stats(net, day, n_trials))
    r = hwk.cluster_stats(net / stop, day)
    out.update(mean_r=r['mean'], lcb95_r=r['lcb95'], gross=float(g.mean()), cost=float(maker_cost(why, VERDICT_SCHEDULE).mean()),
               exits={hwk.REASONS[k]: float((why == k).mean()) for k in range(4)},
               breakeven_maker_fee=breakeven_maker_fee(g, why),
               schedules={k: {x: v for x, v in hwk.cluster_stats(g - maker_cost(why, k), day).items()
                              if x in ('mean', 'se', 'lcb95', 'ucb95')} for k in SCHEDULES},
               taker_gross_filled=float(tr['taker_gross'][fl].mean()),
               taker_gross_unfilled=float(tr['taker_gross'][~fl].mean()) if (~fl).any() else None,
               taker_gross_all=float(tr['taker_gross'].mean()),
               taker_net_hl0=hwk.cluster_stats(tr['taker_gross'] - taker_cost(tr['taker_reason'], VERDICT_SCHEDULE), tr['day']),
               net_per_trigger=float(net.sum() / n))
    return out


def choose_cell(table: dict, min_n: Optional[int] = None) -> Optional[str]:
    """TRAIN-best maker cell: highest mean net R among cells with at least A_MIN_TRAIN fills."""
    min_n = A_MIN_TRAIN if min_n is None else min_n
    ok = {k: v for k, v in table.items() if v.get('n', 0) >= min_n}
    return max(ok, key=lambda k: ok[k]['mean_r']) if ok else None


def candidate(holdout: dict, fresh: dict, min_holdout: int = A_MIN_HOLDOUT, min_fresh: int = A_MIN_FRESH,
              pass_label: str = 'CANDIDATE') -> dict:
    """The plan's four checks. On reused partitions (T-A) a pass is a CANDIDATE, on a new sample (T-C) an EDGE."""
    checks = {f'holdout_n>={min_holdout}': holdout.get('n', 0) >= min_holdout,
              'holdout_lcb95>0': holdout.get('lcb95', -math.inf) > 0,
              'holdout_dsr>=0.95': (holdout.get('dsr') or 0.0) >= 0.95,
              'fresh_mean>0': fresh.get('n', 0) > 0 and fresh.get('mean', -math.inf) > 0}
    core = all(v for k, v in checks.items() if not k.startswith('fresh'))
    if core and fresh.get('n', 0) < min_fresh:
        return {'checks': checks, 'verdict': 'UNCONFIRMED'}
    return {'checks': checks, 'verdict': pass_label if all(checks.values()) else 'NO EDGE'}


def hl_coin(symbol: str) -> str:
    """Binance USDT perpetual symbol -> Hyperliquid coin name (1000PEPEUSDT -> kPEPE)."""
    base = symbol[:-4] if symbol.endswith('USDT') else symbol
    return 'k' + base[4:] if base.startswith('1000') and not base.startswith('1000000') else base


def fetch_hl_universe(out: Path = AF / 'hl_meta.json') -> set:
    """Coins of Hyperliquid's current perps `meta` (delisted ones included): the listing is today's, not point in time."""
    if not out.exists():
        req = urllib.request.Request('https://api.hyperliquid.xyz/info', data=json.dumps({'type': 'meta'}).encode(),
                                     headers={'Content-Type': 'application/json', 'User-Agent': 'research'})
        with urllib.request.urlopen(req, timeout=30) as r:
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(r.read())
    return {x['name'] for x in json.loads(out.read_text())['universe']}


# ================================================================================================ B: CEX-DEX arithmetic
DEX_Q, DEX_PHI, DEX_FC, DEX_SLIP = 5.0, (5, 25, 30, 100), (5, 10), 3.0
DEX_GAS, DEX_TAU, DEX_PWIN = (0.001, 0.02, 2.0), (0.0, 0.5, 0.9), (1.0, 0.1, 0.01)


def dex_breakeven_gap(q: float, phi_bps: float, f_c_bps: float, slip_bps: float, gas_u: float) -> float:
    """Smallest gap in bps at which one round of size q pays its fees and gas (first in the block, no tip)."""
    return phi_bps + f_c_bps + slip_bps + gas_u / q * 1e4


def dex_round(q: float, gap_bps: float, phi_bps: float, f_c_bps: float, slip_bps: float, gas_u: float,
              tau: float = 0.0, p_win: float = 1.0, gas_fail_u: Optional[float] = None) -> float:
    """Expected net of one attempt in U: p_win x (1 - tau) x (q x (G - phi - f_c - s) - g) - (1 - p_win) x g_fail.
    A losing round keeps its sign (the inclusion share is paid on profit only)."""
    win = q * (gap_bps - phi_bps - f_c_bps - slip_bps) * 1e-4 - gas_u
    win = win * (1.0 - tau) if win > 0 else win
    return p_win * win - (1.0 - p_win) * (gas_u if gas_fail_u is None else gas_fail_u)


def dex_table(q: float = DEX_Q, target_per_hour: float = 20.0) -> dict:
    out = {'q': q, 'breakeven_gap_bps': {}, 'rounds': []}
    for phi in DEX_PHI:
        for fc in DEX_FC:
            for g in DEX_GAS:
                out['breakeven_gap_bps'][f'phi{phi}|fc{fc}|gas{g:g}'] = dex_breakeven_gap(q, phi, fc, DEX_SLIP, g)
    for gap in (50.0, 100.0, 200.0):
        for g in DEX_GAS:
            for tau in DEX_TAU:
                for pw in DEX_PWIN:
                    net = dex_round(q, gap, 25, 5, DEX_SLIP, g, tau, pw)
                    out['rounds'].append({'gap_bps': gap, 'phi_bps': 25, 'f_c_bps': 5, 'gas_u': g, 'tau': tau, 'p_win': pw,
                                          'net_u': net, 'rounds_per_hour_for_target': (target_per_hour / net if net > 0 else None)})
    return out


# ================================================================================================ C: Kalshi longshot takers
KALSHI = 'https://api.elections.kalshi.com/trade-api/v2'
C_DAY0 = '2025-01-01'
C_PARTS = {'TRAIN': ('2025-01-01', '2025-12-31', 3), 'HOLDOUT': ('2026-01-01', '2026-09-30', 3),
           'FRESH': ('2026-10-01', '2026-11-12', 2)}
WINDOW_S, MINUTE_MULT, MAX_PAGES, CUTOFF = 300, 397, 120, '2026-09-15'
MAX_PRICE, STAKE, FEE_RATE, LONG_H, COMBO_PREFIX = 0.10, 1.0, 0.07, 24, 'KXMVE'
BUCKETS = {'P1': (0.0, 0.015), 'P2': (0.015, 0.025), 'P12': (0.0, 0.025)}
HORIZONS = ('ALL', 'LONG', 'SHORT')
CAL_EDGES = (0.0, 0.015, 0.025, 0.05, 0.10)
C_MIN_TRAIN, C_MIN_TRAIN_WINDOWS, C_MIN_HOLDOUT, C_MIN_FRESH = 200, 20, 100, 20


def day_ts(day: str) -> int:
    return int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp())


def iso_ts(s: str) -> float:
    """'2025-06-10T15:59:58.821341Z' -> epoch seconds."""
    s = s[:-1] if s.endswith('Z') else s
    head, _, frac = s.partition('.')
    return datetime.strptime(head, '%Y-%m-%dT%H:%M:%S').replace(tzinfo=timezone.utc).timestamp() + (float('0.' + frac) if frac else 0.0)


def windows() -> list:
    """The frozen sample: [{part, day, n, start, end, endpoint}] (plan section 3)."""
    out, d0 = [], day_ts(C_DAY0)
    for part, (lo, hi, step) in C_PARTS.items():
        t = day_ts(lo)
        while t <= day_ts(hi):
            n = (t - d0) // DAY_S
            start = t + ((MINUTE_MULT * n) % 1440) * 60
            out.append({'part': part, 'day': datetime.fromtimestamp(t, tz=timezone.utc).strftime('%Y-%m-%d'), 'n': int(n),
                        'start': int(start), 'end': int(start + WINDOW_S),
                        'endpoint': '/historical/trades' if start + WINDOW_S < day_ts(CUTOFF) else '/markets/trades'})
            t += step * DAY_S
    return out


def http_json(url: str, tries: int = 6, timeout: int = 40):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'research'}), timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if k == tries - 1:
                raise
            time.sleep(5 * (k + 1) if e.code == 429 else 2 * (k + 1))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2 * (k + 1))
    return None


def taker_price(tr: dict) -> tuple:
    """(side, price the taker paid per contract) of one API trade row."""
    side = tr.get('taker_side') or tr.get('taker_outcome_side')
    px = tr.get('yes_price_dollars') if side == 'yes' else tr.get('no_price_dollars')
    return side, (float(px) if px is not None else math.nan)


def fetch_window(w: dict, get=http_json, max_pages: int = MAX_PAGES) -> dict:
    """All trades of one window, newest first; kept rows = taker price <= MAX_PRICE: [ticker, side, price, count, ts]."""
    rows, n_all, pages, cur = [], 0, 0, ''
    while pages < max_pages:
        d = get(f"{KALSHI}{w['endpoint']}?limit=1000&min_ts={w['start']}&max_ts={w['end']}" + (f'&cursor={cur}' if cur else ''))
        trades = (d or {}).get('trades') or []
        pages += 1
        n_all += len(trades)
        for tr in trades:
            side, px = taker_price(tr)
            if side in ('yes', 'no') and 0 < px <= MAX_PRICE:
                rows.append([tr['ticker'], side, px, float(tr.get('count_fp') or tr.get('count') or 0), iso_ts(tr['created_time'])])
        cur = (d or {}).get('cursor') or ''
        if not cur or not trades:
            break
    return {**w, 'n_all': n_all, 'pages': pages, 'truncated': bool(cur), 'rows': rows}


def fetch_trades(out: Path = AF / 'kalshi_trades', workers: int = 4, get=http_json, log=print) -> dict:
    """Every frozen window -> one JSON file per day (resumable)."""
    out.mkdir(parents=True, exist_ok=True)
    todo = [w for w in windows() if not (out / f"{w['day']}.json").exists()]
    log(f'{len(todo)} windows to fetch of {len(windows())}')
    lock, count = threading.Lock(), {'done': 0, 'trades': 0, 'kept': 0}

    def work(w):
        res = fetch_window(w, get)
        tmp = out / f"{w['day']}.part"
        tmp.write_text(json.dumps(res))
        tmp.replace(out / f"{w['day']}.json")
        with lock:
            count['done'] += 1
            count['trades'] += res['n_all']
            count['kept'] += len(res['rows'])
            if count['done'] % 10 == 0:
                log(f"  {count['done']}/{len(todo)} windows, {count['trades']} trades, {count['kept']} kept")

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(work, todo))
    return count


def load_windows(src: Path = AF / 'kalshi_trades') -> list:
    return [json.loads(p.read_text()) for p in sorted(src.glob('*.json'))]


def fetch_markets(tickers, out: Path = AF / 'kalshi_markets.json', batch: int = 40, get=http_json, log=print,
                  workers: int = 1) -> dict:
    """{ticker: {result, close_ts, event, status}} from the historical and the live market endpoints (resumable).
    Single-event tickers are looked up before the combo class."""
    have = json.loads(out.read_text()) if out.exists() else {}
    todo = sorted(set(tickers) - set(have), key=lambda t: (t.startswith(COMBO_PREFIX), t))
    parts = [todo[a:a + batch] for a in range(0, len(todo), batch)]
    lock, done = threading.Lock(), [0]

    def work(part):
        got = {}
        for ep in ('/historical/markets', '/markets'):
            miss = [t for t in part if t not in got]
            if not miss:
                break
            d = get(f"{KALSHI}{ep}?limit=1000&tickers={','.join(miss)}")
            for mk in (d or {}).get('markets') or []:
                got[mk['ticker']] = {'result': mk.get('result') or '', 'event': mk.get('event_ticker') or '',
                                     'status': mk.get('status') or '',
                                     'close_ts': iso_ts(mk['close_time']) if mk.get('close_time') else None}
        with lock:
            for t in part:
                have[t] = got.get(t, {'result': '', 'event': '', 'status': 'not_found', 'close_ts': None})
            done[0] += 1
            if done[0] % 100 == 0 or done[0] == len(parts):
                out.write_text(json.dumps(have))
                log(f'  markets {done[0]}/{len(parts)} batches')

    if workers <= 1:
        for part in parts:
            work(part)
    else:
        with ThreadPoolExecutor(workers) as ex:
            list(ex.map(work, parts))
    return have


def fee_cents_up(contracts: float, price: float, rate: float = FEE_RATE) -> float:
    """Kalshi taker fee: rate x C x P x (1 - P), rounded up to the cent."""
    return math.ceil(round(rate * contracts * price * (1.0 - price) * 100.0, 9)) / 100.0


def stake_pnl(price: float, win: bool, stake: float = STAKE, rate: float = FEE_RATE) -> Optional[tuple]:
    """(pnl, outlay) of buying floor(stake / price) contracts as a taker; None when the stake buys no contract."""
    c = math.floor(stake / price + 1e-9)
    if c < 1:
        return None
    cost = c * price
    fee = fee_cents_up(c, price, rate) if rate > 0 else 0.0
    return (c if win else 0.0) - cost - fee, cost + fee


def trade_table(wins: list, markets: dict) -> dict:
    """Arrays over kept trades whose market resolved yes or no, plus the counts of what was left out."""
    cols = {k: [] for k in ('part', 'day', 'p', 'count', 'win', 'yes', 'combo', 'hours', 'event')}
    left = {'unresolved': 0, 'not_found': 0, 'kept': 0, 'all_trades': 0, 'windows': len(wins), 'truncated_windows': 0}
    for w in wins:
        left['all_trades'] += w['n_all']
        left['truncated_windows'] += bool(w.get('truncated'))
        for ticker, side, px, cnt, ts in w['rows']:
            left['kept'] += 1
            mk = markets.get(ticker)
            if mk is None or mk.get('status') == 'not_found':
                left['not_found'] += 1
                continue
            if mk['result'] not in ('yes', 'no'):
                left['unresolved'] += 1
                continue
            for k, v in zip(cols, (w['part'], w['day'], px, cnt, mk['result'] == side, side == 'yes',
                                   ticker.startswith(COMBO_PREFIX),
                                   (mk['close_ts'] - ts) / 3600.0 if mk.get('close_ts') else math.nan, mk['event'])):
                cols[k].append(v)
    t = {k: np.array(v) for k, v in cols.items()}
    t['p'], t['count'], t['hours'] = (t[k].astype(np.float64) for k in ('p', 'count', 'hours'))
    t['win'], t['yes'], t['combo'] = (t[k].astype(bool) for k in ('win', 'yes', 'combo'))
    pnl = [stake_pnl(float(p), bool(w)) for p, w in zip(t['p'], t['win'])]
    t['r'] = np.array([x[0] / x[1] for x in pnl], dtype=np.float64)
    t['r0'] = np.array([(1.0 if w else 0.0) / p - 1.0 for p, w in zip(t['p'], t['win'])], dtype=np.float64)   # no fee
    t['left'] = left
    return t


def cell_mask(t: dict, bucket: str, horizon: str, combo: bool = False) -> np.ndarray:
    lo, hi = BUCKETS[bucket]
    m = (t['p'] > lo) & (t['p'] <= hi) & (t['combo'] == combo)
    if horizon == 'LONG':
        m &= t['hours'] >= LONG_H
    elif horizon == 'SHORT':
        m &= t['hours'] < LONG_H
    elif horizon != 'ALL':
        raise ValueError(f'unknown horizon {horizon}')
    return m


def c_stats(t: dict, m: np.ndarray, n_trials: int = 0) -> dict:
    """Return per 1 U stake of the trades in mask m, clustered by sample window (= day)."""
    if not m.any():
        return {'n': 0}
    out = hwk.cluster_stats(t['r'][m], t['day'][m], n_trials)
    p, w, cnt = t['p'][m], t['win'][m], t['count'][m]
    fee_c = FEE_RATE * p * (1.0 - p)
    out.update(windows=out.pop('days'), mean_price=float(p.mean()), win_rate=float(w.mean()),
               win_over_price=float(w.mean() / p.mean()), mean_r_no_fee=float(t['r0'][m].mean()),
               contract_weighted_r=float((cnt * (w - p - fee_c)).sum() / (cnt * (p + fee_c)).sum()) if cnt.sum() > 0 else None,
               seller_r=float(((~w).astype(float) - (1.0 - p)).sum() / (1.0 - p).sum()),
               yes_share=float(t['yes'][m].mean()))
    return out


def c_cells(t: dict, part: str, n_trials: int = 0) -> dict:
    sel = t['part'] == part
    return {f'{b}|{h}': c_stats(t, sel & cell_mask(t, b, h), n_trials) for b in BUCKETS for h in HORIZONS}


def choose_c(table: dict) -> Optional[str]:
    ok = {k: v for k, v in table.items() if v.get('n', 0) >= C_MIN_TRAIN and v.get('windows', 0) >= C_MIN_TRAIN_WINDOWS}
    return max(ok, key=lambda k: ok[k]['mean']) if ok else None


def calibration(t: dict, part: str, combo: bool = False) -> dict:
    out = {}
    for lo, hi in zip(CAL_EDGES[:-1], CAL_EDGES[1:]):
        m = (t['part'] == part) & (t['combo'] == combo) & (t['p'] > lo) & (t['p'] <= hi)
        out[f'{lo:g}-{hi:g}'] = {k: v for k, v in c_stats(t, m).items()
                                 if k in ('n', 'windows', 'mean', 'se', 'mean_price', 'win_rate', 'win_over_price',
                                          'mean_r_no_fee', 'contract_weighted_r', 'seller_r')}
    return out


# ================================================================================================ growth of 10 U
PATHS, DAYS, EQUITY0, TARGET_PER_HOUR = 2000, 30, 10.0, 20.0
HL_MIN_ORDER, HL_MAX_LEV, FLAT_RISK = 10.0, 5.0, 1.0
C_STAKES, C_BETS_PER_DAY = (1.0, 0.05), 5


def summarize_paths(eq: np.ndarray, ruined: np.ndarray, trades: np.ndarray, days: int = DAYS, equity0: float = EQUITY0) -> dict:
    return {'paths': int(eq.size), 'p05': float(np.quantile(eq, 0.05)), 'median': float(np.median(eq)),
            'p95': float(np.quantile(eq, 0.95)), 'mean': float(eq.mean()), 'max': float(eq.max()),
            'p_ruin': float(ruined.mean()), 'p_profit': float((eq > equity0).mean()),
            'p_double': float((eq >= 2 * equity0).mean()),
            'p_pace_20u_per_hour': float((eq >= equity0 + TARGET_PER_HOUR * 24 * days).mean()),
            'mean_u_per_hour': float((eq.mean() - equity0) / (24.0 * days)), 'mean_trades': float(trades.mean())}


def growth_a(book: list, sizing: str, stop_bps: float, paths: int = PATHS, days: int = DAYS, seed: int = SEED,
             equity0: float = EQUITY0) -> dict:
    """Day-block replay of one-slot fills (net bps under HL0). MIN = the 10 USD minimum order; FLAT = 1.0 U at the stop,
    capped at HL_MAX_LEV x equity. A path stops when it cannot margin one minimum order."""
    if not book:
        return {'paths': 0}
    rng, floor = np.random.default_rng(seed), HL_MIN_ORDER / HL_MAX_LEV
    eq_out, ruin, ntr = np.zeros(paths), np.zeros(paths, dtype=bool), np.zeros(paths)
    for k in range(paths):
        eq, n = equity0, 0
        for _ in range(days):
            if eq < floor:
                ruin[k] = True
                break
            for net in book[int(rng.integers(0, len(book)))]:
                if eq < floor:
                    break
                notional = HL_MIN_ORDER if sizing == 'MIN' else max(HL_MIN_ORDER, min(FLAT_RISK / (stop_bps * 1e-4), HL_MAX_LEV * eq))
                eq += max(notional * net * 1e-4, -eq)
                n += 1
        ruin[k] = ruin[k] or eq < floor
        eq_out[k], ntr[k] = eq, n
    return summarize_paths(eq_out, ruin, ntr, days, equity0)


def growth_c(book: list, stake: float, paths: int = PATHS, days: int = DAYS, seed: int = SEED, equity0: float = EQUITY0,
             bets_per_day: int = C_BETS_PER_DAY) -> dict:
    """Each day replays one sampled window: up to `bets_per_day` of its cell trades (price, win), without replacement,
    `stake` each (fee rounded up to the cent). A path stops when it cannot fund one stake."""
    if not book:
        return {'paths': 0}
    rng = np.random.default_rng(seed)
    eq_out, ruin, ntr = np.zeros(paths), np.zeros(paths, dtype=bool), np.zeros(paths)
    for k in range(paths):
        eq, n = equity0, 0
        for _ in range(days):
            day = book[int(rng.integers(0, len(book)))]
            for j in (rng.permutation(len(day))[:bets_per_day] if day else ()):
                if eq < stake:
                    ruin[k] = True
                    break
                res = stake_pnl(day[j][0], day[j][1], stake)
                if res is None:
                    continue
                eq += res[0]
                n += 1
            if ruin[k]:
                break
        eq_out[k], ntr[k] = eq, n
    return summarize_paths(eq_out, ruin, ntr, days, equity0)


# ================================================================================================ run
def run_a(log=print, matrix: Path = hwk.MATRIX, hl_universe: Optional[set] = None) -> dict:
    mx = hwk.load_matrix(matrix)
    parts = {p: hwk.rows_of(mx, p) for p in hwk.PARTS}
    cells = [(k, ttl) for k in K_BPS for ttl in TTLS]
    res = {'pairs': {p: int(len(r)) for p, r in parts.items()}, 'signal': S1.name, 'cells': [cell_name(*c) for c in cells]}
    ftr = hwk.Features(mx, parts['TRAIN'])
    table = {cell_name(k, ttl): maker_summary(run_maker(ftr, S1, k, ttl), S1.s) for k, ttl in cells}
    for name, v in table.items():
        log(f"  T-A TRAIN {name}: fills {v.get('n', 0)} ({v.get('fill_rate', 0):.2f}) net {v.get('mean', float('nan')):+.2f} "
            f"gross {v.get('gross', float('nan')):+.2f} f_m* {v.get('breakeven_maker_fee')}")
    res['train'] = table
    res['cell'] = choose_cell(table)
    res['train_s2'] = {cell_name(k, ttl): {x: v for x, v in maker_summary(run_maker(ftr, S2, k, ttl), S2.s).items()
                                           if x in ('triggers', 'n', 'fill_rate', 'mean', 'se', 'gross', 'breakeven_maker_fee')}
                       for k, ttl in cells}
    del ftr
    if not res['cell']:
        res['verdict'] = 'NO OPPORTUNITY SET'
        return res
    k, ttl = next(c for c in cells if cell_name(*c) == res['cell'])
    trials, kept = PRIOR_TRIALS + len(cells), {}
    for part in ('HOLDOUT', 'FRESH'):
        f = hwk.Features(mx, parts[part])
        tr = run_maker(f, S1, k, ttl)
        kept[part] = tr
        res[part.lower()] = maker_summary(tr, S1.s, trials)
        res[part.lower() + '_s2'] = maker_summary(run_maker(f, S2, k, ttl), S2.s)
        res[part.lower() + '_all_cells'] = {cell_name(a, b): {x: v for x, v in maker_summary(run_maker(f, S1, a, b), S1.s).items()
                                                               if x in ('triggers', 'n', 'fill_rate', 'mean', 'se', 'lcb95', 'gross',
                                                                        'breakeven_maker_fee', 'taker_gross_filled',
                                                                        'taker_gross_unfilled')}
                                            for a, b in cells} if part == 'HOLDOUT' else None
        if hl_universe is not None:
            on = np.array([hl_coin(str(s)) in hl_universe for s in tr['sym']], dtype=bool)
            sub = {key: v[on] for key, v in tr.items()}
            res[part.lower() + '_hl_listed'] = {**{x: v for x, v in maker_summary(sub, S1.s).items()
                                                   if x in ('triggers', 'n', 'fill_rate', 'mean', 'se', 'lcb95', 'gross',
                                                            'breakeven_maker_fee')},
                                                'share_of_triggers': float(on.mean()) if len(on) else None}
        st = res[part.lower()]
        log(f"  T-A {part} {res['cell']}: fills {st.get('n', 0)} net {st.get('mean', float('nan')):+.2f} +- "
            f"{st.get('se', float('nan')):.2f} DSR {st.get('dsr')}")
        del f
    res.update(n_trials=trials, **candidate(res['holdout'], res['fresh']))
    # growth: one slot, day blocks of HOLDOUT + FRESH
    fills = {key: np.concatenate([kept[p][key][kept[p]['filled']] for p in ('HOLDOUT', 'FRESH')]) for key in kept['HOLDOUT']}
    net = fills['gross'] - maker_cost(fills['reason'], VERDICT_SCHEDULE)
    days = hwk.calendar_days('HOLDOUT') + hwk.calendar_days('FRESH')
    book, free = {d: [] for d in days}, -1
    for j in np.lexsort((fills['row'], fills['t_entry'])):
        if fills['t_entry'][j] >= free and str(fills['day'][j]) in book:
            book[str(fills['day'][j])].append(float(net[j]))
            free = int(fills['t_exit'][j])
    blocks = [book[d] for d in days]
    slot = [x for d in blocks for x in d]
    res['growth'] = {'one_slot_fills': len(slot), 'fills_per_day': len(slot) / len(days),
                     'one_slot_mean_net_bps': float(np.mean(slot)) if slot else None,
                     'min_loss_at_stop_u': HL_MIN_ORDER * S1.s * 1e-4,
                     'MIN_10U_ORDER': growth_a(blocks, 'MIN', S1.s), 'FLAT_1U': growth_a(blocks, 'FLAT', S1.s)}
    return res


def run_c(log=print, src: Path = AF / 'kalshi_trades', markets_path: Path = AF / 'kalshi_markets.json') -> dict:
    wins = load_windows(src)
    if not wins or not markets_path.exists():
        return {'verdict': 'NOT RUN', 'reason': 'no Kalshi sample on disk (run kalshi-fetch)'}
    t = trade_table(wins, json.loads(markets_path.read_text()))
    res = {'sample': {**t['left'], 'windows_by_part': {p: sum(1 for w in wins if w['part'] == p) for p in C_PARTS},
                      'resolved_trades': int(len(t['p'])), 'combo_share': float(t['combo'].mean()) if len(t['p']) else None}}
    table = c_cells(t, 'TRAIN')
    for name, v in table.items():
        log(f"  T-C TRAIN {name}: n {v.get('n', 0)} windows {v.get('windows', 0)} r {v.get('mean', float('nan')):+.3f} "
            f"win {v.get('win_rate', float('nan')):.4f} price {v.get('mean_price', float('nan')):.4f}")
    res['train'] = table
    res['cell'] = choose_c(table)
    res['calibration'] = {p: calibration(t, p) for p in C_PARTS}
    res['calibration_combo'] = {p: calibration(t, p, combo=True) for p in C_PARTS}
    if not res['cell']:
        res['verdict'] = 'NO OPPORTUNITY SET'
        return res
    b, h = res['cell'].split('|')
    for part in ('HOLDOUT', 'FRESH'):
        sel = (t['part'] == part) & cell_mask(t, b, h)
        res[part.lower()] = c_stats(t, sel, len(BUCKETS) * len(HORIZONS))
        res[part.lower() + '_by_side'] = {'YES': c_stats(t, sel & t['yes']), 'NO': c_stats(t, sel & ~t['yes'])}
        st = res[part.lower()]
        log(f"  T-C {part} {res['cell']}: n {st.get('n', 0)} r {st.get('mean', float('nan')):+.3f} +- "
            f"{st.get('se', float('nan')):.3f} DSR {st.get('dsr')}")
    res['holdout_all_cells'] = {k: {x: v.get(x) for x in ('n', 'windows', 'mean', 'se', 'win_rate', 'mean_price')}
                                for k, v in c_cells(t, 'HOLDOUT').items()}
    res.update(n_trials=len(BUCKETS) * len(HORIZONS),
               **candidate(res['holdout'], res['fresh'], C_MIN_HOLDOUT, C_MIN_FRESH, pass_label='EDGE'))
    sel = np.isin(t['part'], ('HOLDOUT', 'FRESH')) & cell_mask(t, b, h)
    by_day = {w['day']: [] for w in wins if w['part'] in ('HOLDOUT', 'FRESH')}
    for d, p, w in zip(t['day'][sel], t['p'][sel], t['win'][sel]):
        by_day[str(d)].append((float(p), bool(w)))
    blocks = list(by_day.values())
    res['growth'] = {'windows': len(blocks), 'cell_trades': int(sel.sum()),
                     **{f'STAKE_{s:g}U': growth_c(blocks, s) for s in C_STAKES}}
    return res


def run(log=print, matrix: Path = hwk.MATRIX, kalshi_src: Path = AF / 'kalshi_trades',
        kalshi_markets: Path = AF / 'kalshi_markets.json', hl_universe: Optional[set] = None) -> dict:
    res = {'plan': PLAN, 'fee_schedules_bps': {k: {'maker': v[0], 'taker': v[1]} for k, v in SCHEDULES.items()}}
    res['a'] = run_a(log, matrix, hl_universe)
    res['b'] = dex_table()
    res['c'] = run_c(log, kalshi_src, kalshi_markets)
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('stage', choices=('kalshi-fetch', 'run'))
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--batch', type=int, default=100, help='tickers per market lookup (a 250-ticker URL is refused, HTTP 414)')
    a = ap.parse_args(argv)
    if a.stage == 'kalshi-fetch':
        print(fetch_trades(workers=a.workers))
        tickers = {r[0] for w in load_windows() for r in w['rows']}
        mk = fetch_markets(tickers, workers=a.workers, batch=a.batch)
        print(f'{len(tickers)} tickers, {sum(1 for v in mk.values() if v["result"] in ("yes", "no"))} resolved')
        return 0
    t0 = time.time()
    try:
        hl = fetch_hl_universe()
    except Exception as e:                                  # the subset is descriptive: UNKNOWN, not a failure
        print(f'Hyperliquid meta not read ({e}): subset skipped')
        hl = None
    res = run(hl_universe=hl)
    res['runtime_s'] = time.time() - t0
    OUT.write_text(json.dumps(res, indent=1, default=float))
    print(f"T-A {res['a'].get('verdict')}  T-C {res['c'].get('verdict')}  -> {OUT} ({res['runtime_s']:.0f} s)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
