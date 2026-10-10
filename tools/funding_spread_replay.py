"""Funding-carry Phase 0 replay (10-10 f): does a historical funding spread pay after fees, slippage and basis?
Offline on public data (no keys, no orders):

    python tools/funding_spread_replay.py [--json out.json] [--refresh]

PRE-REGISTERED (written before the first run):
  pairs     XV  Binance USDT-M perp vs Hyperliquid perp, same coin: short the venue with the higher trailing funding,
                long the other (both directions allowed).
            SP  Binance spot long + Binance perp short (cash-and-carry; only when the trailing perp funding > 0, a
                spot short would need margin borrowing).
  coins     the 18 most frequent names of the 2025-26 top-20 universe that list on Hyperliquid: BTC ETH SOL XRP DOGE
            BNB ADA SUI LINK AVAX ENA AAVE LTC HYPE WLD TAO ZEC 1000PEPE (HL kPEPE).  Missing data = no trade.
  clock     decisions at every Binance 8 h funding time T (00/08/16 UTC); a position opens just AFTER the print at T
            and closes just after the print at T + H, so it earns the prints in (T, T + H].
  signal    S = sum over (T - 72 h, T] of (funding of the short leg - funding of the long leg), bps (HL hourly prints
            summed);  expected carry = S / 3 days x H.  Enter iff expected carry >= k x round-trip cost.
  variants  H in {7, 14, 28} days x k in {1, 2}; one position per (pair type, coin), no overlap.
  P&L       per unit of notional on EACH leg (bps): funding received on the short leg - funding paid on the long leg
            + basis = (pA0 - pA1) / pA0 + (pB1 - pB0) / pB0 with prices = open of the 4 h bar starting at T (own
            venue for each leg) - fees.
  costs     fees bps maker/taker: Binance perp 2/5, Hyperliquid 1.5/4.5, Binance spot 10/10 (VIP0, no BNB).
            MAKER = all four fills maker (optimistic bound).  HEDGE (primary) = at open and at close one leg maker,
            the other taker + slip 1 bp (cheapest assignment) - a resting maker leg alone is naked delta.
  splits    DEV = entries 2025-01-01..2025-12-31 whose exit is before 2026 picks the variant per pair type (mean net, HEDGE costs, n >= 30);
            HOLDOUT = entries 2026-01-01..2026-09-30 whose exit is inside the data.
  verdict   (10-10 f gate) HOLDOUT n >= 100 and CI95 lower bound of the mean net > 0, bootstrap over entry DAYS.
            Holds last 7-28 days and coins co-move, so a bootstrap over entry MONTHS is reported beside it as a
            robustness check (not part of the gate).
  money     APR on notional = mean net bps per day held x 365; capital per $1 notional is 2/L (XV, L = leverage on
            each venue) or 1 + 1/L (SP) - reported, not optimised.
Caveat: 4 h opens are mid-quality marks, not fills; Binance perp and HL prices may differ by venue basis at any one
moment, which is exactly the basis risk measured.
"""
from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / 'data' / 'funding_cache'
COINS = ('BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'BNB', 'ADA', 'SUI', 'LINK', 'AVAX', 'ENA', 'AAVE', 'LTC', 'HYPE', 'WLD',
         'TAO', 'ZEC', '1000PEPE')
HL_NAME = {'1000PEPE': 'kPEPE'}
FEES = {'bn_perp': (2.0, 5.0), 'hl_perp': (1.5, 4.5), 'bn_spot': (10.0, 10.0)}
SLIP = 1.0
H_DAYS, K_MULT = (7, 14, 28), (1.0, 2.0)
LOOKBACK_MS = 72 * 3600_000
H8 = 8 * 3600_000
START, END = '2024-12-20', '2026-10-09'
DEV = ('2025-01-01', '2025-12-31')
HOLD = ('2026-01-01', '2026-09-30')
MIN_N = 100


# ------------------------------------------------------------------ pure parts (tested)
def on_minute(t_ms: int) -> int:
    """Venue print times carry a few ms of jitter: snap to the minute so the print AT T is never read as after T."""
    return int(round(t_ms / 60000)) * 60000


def ms(day: str) -> int:
    return int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)


def window_sum(t: np.ndarray, r: np.ndarray, lo: int, hi: int) -> float:
    """Sum of prints r at times t in (lo, hi]; t sorted."""
    return float(r[np.searchsorted(t, lo, 'right'):np.searchsorted(t, hi, 'right')].sum())


def covered(t: np.ndarray, lo: int, hi: int, step_ms: int) -> bool:
    """Prints exist across (lo, hi] with no hole longer than 2 x the print interval (else UNKNOWN)."""
    i, j = np.searchsorted(t, lo, 'right'), np.searchsorted(t, hi, 'right')
    if j - i < 1:
        return False
    w = np.r_[lo, t[i:j], hi]
    return bool(np.diff(w).max() <= 2 * step_ms)


def round_trip_cost(leg_short: str, leg_long: str, mode: str) -> float:
    a, b = FEES[leg_short], FEES[leg_long]
    if mode == 'MAKER':
        return 2 * (a[0] + b[0])
    one = min(a[0] + b[1] + SLIP, a[1] + SLIP + b[0])      # one maker leg + one taker hedge leg
    return 2 * one


def price_at(t: np.ndarray, o: np.ndarray, at: int):
    """Open of the bar starting exactly at `at` (4 h grid), else None (UNKNOWN)."""
    i = np.searchsorted(t, at)
    return float(o[i]) if i < len(t) and t[i] == at else None


def simulate(series: dict, kind: str, coin: str, h_days: int, k: float, mode: str) -> list:
    """One position at a time.  series: {'bn': (t, r), 'hl': (t, r) | None, 'px_bn': (t, o), 'px_other': (t, o)}."""
    bt, br = series['bn']
    other = series['hl'] if kind == 'XV' else None
    step_other = 3600_000
    out, T, end = [], ms(START) + LOOKBACK_MS, int(bt[-1])
    T = T - T % H8 + H8
    H = h_days * 86400_000
    while T + H <= end:
        if not covered(bt, T - LOOKBACK_MS, T, H8 + 1) or (other is not None and not covered(other[0], T - LOOKBACK_MS, T, step_other)):
            T += H8
            continue
        f_bn = window_sum(bt, br, T - LOOKBACK_MS, T)
        f_ot = window_sum(*other, T - LOOKBACK_MS, T) if other is not None else 0.0
        if kind == 'XV':
            short_bn = f_bn >= f_ot
            legs = ('bn_perp', 'hl_perp') if short_bn else ('hl_perp', 'bn_perp')
            spread = abs(f_bn - f_ot)
        else:
            short_bn, legs, spread = True, ('bn_perp', 'bn_spot'), f_bn
        cost = round_trip_cost(*legs, mode)
        if spread <= 0 or spread / 3.0 * h_days < k * cost:
            T += H8
            continue
        pb0, pb1 = price_at(*series['px_bn'], T), price_at(*series['px_bn'], T + H)
        po0, po1 = price_at(*series['px_other'], T), price_at(*series['px_other'], T + H)
        if (None in (pb0, pb1, po0, po1) or not covered(bt, T, T + H, H8 + 1)
                or (other is not None and not covered(other[0], T, T + H, step_other))):
            T += H8                                      # UNKNOWN prices / funding: no trade, never zero-filled
            continue
        r_bn = window_sum(bt, br, T, T + H)
        r_ot = window_sum(*other, T, T + H) if other is not None else 0.0
        if short_bn:                                     # short Binance perp, long the other leg
            fund = r_bn - r_ot
            basis = ((pb0 - pb1) / pb0 + (po1 - po0) / po0) * 1e4
        else:                                            # short HL, long Binance perp
            fund = r_ot - r_bn
            basis = ((po0 - po1) / po0 + (pb1 - pb0) / pb0) * 1e4
        out.append({'coin': coin, 't': T / 1000, 'days': h_days, 'legs': legs, 'signal_bps': spread,
                    'funding_bps': fund, 'basis_bps': basis, 'cost_bps': cost, 'net_bps': fund + basis - cost})
        T += H
    return out


def summarize(trades: list, lo: str, hi: str) -> dict:
    a, b = ms(lo) / 1000, (ms(hi) + 86400_000) / 1000
    tr = [x for x in trades if a <= x['t'] < b]
    if not tr:
        return {'n': 0}
    x = np.array([r['net_bps'] for r in tr])
    rng = np.random.default_rng(7)

    def boot(key):
        g = np.array([key(r) for r in tr])
        u = np.unique(g)
        s = {v: x[g == v].sum() for v in u}
        c = {v: int((g == v).sum()) for v in u}
        m = []
        for _ in range(2000):
            pick = rng.choice(u, len(u))
            m.append(sum(s[v] for v in pick) / max(1, sum(c[v] for v in pick)))
        return [float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))]
    days_held = sum(r['days'] for r in tr)
    return {'n': len(tr), 'mean_net_bps': float(x.mean()), 'ci95_day': boot(lambda r: int(r['t'] // 86400)),
            'ci95_month': boot(lambda r: datetime.fromtimestamp(r['t'], timezone.utc).strftime('%Y-%m')),
            'win_rate': float((x > 0).mean()), 'funding_bps': float(np.mean([r['funding_bps'] for r in tr])),
            'basis_bps': float(np.mean([r['basis_bps'] for r in tr])),
            'basis_sd_bps': float(np.std([r['basis_bps'] for r in tr])),
            'cost_bps': float(np.mean([r['cost_bps'] for r in tr])),
            'apr_on_notional_pct': float(x.sum() / days_held * 365 / 100), 'coins': len({r['coin'] for r in tr})}


def eligible(h: dict) -> bool:
    return h.get('n', 0) >= MIN_N and h['ci95_day'][0] > 0


# ------------------------------------------------------------------ data (public endpoints, cached)
def _get(url: str, body=None):
    for k in range(4):
        try:
            req = urllib.request.Request(url, data=None if body is None else json.dumps(body).encode(),
                                         headers={'Content-Type': 'application/json', 'User-Agent': 'research'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except Exception:
            if k == 3:
                raise
            time.sleep(2 + 3 * k)


def _cached(name: str, fn, refresh: bool):
    p = CACHE / f'{name}.json'
    if p.exists() and not refresh:
        return json.loads(p.read_text())
    data = fn()
    CACHE.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(data))
    return data


def bn_funding(sym: str, a: int, b: int) -> list:
    out, s = [], a
    while s < b:
        page = _get(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={s}&endTime={b}&limit=1000')
        if not page:
            break
        out += [(on_minute(int(x['fundingTime'])), float(x['fundingRate']) * 1e4) for x in page]
        s = out[-1][0] + 1
        if len(page) < 1000:
            break
        time.sleep(0.3)
    return out


def hl_funding(coin: str, a: int, b: int) -> list:
    out, s = [], a
    while s < b:
        page = _get('https://api.hyperliquid.xyz/info', {'type': 'fundingHistory', 'coin': coin, 'startTime': s,
                                                          'endTime': b})
        if not page:
            break
        out += [(on_minute(int(x['time'])), float(x['fundingRate']) * 1e4) for x in page]
        s = out[-1][0] + 1
        time.sleep(1.0)                                   # HL info weight: stay well under 1 200 / min
        if len(page) < 500:
            break
    return out


def klines_4h(url_base: str, sym: str, a: int, b: int) -> list:
    out, s = [], a
    while s < b:
        try:
            page = _get(f'{url_base}?symbol={sym}&interval=4h&startTime={s}&endTime={b}&limit=1000')
        except urllib.error.HTTPError as e:              # e.g. no Binance spot market for the coin -> no SP leg
            if e.code == 400:
                break
            raise
        if not isinstance(page, list) or not page:
            break
        out += [(int(x[0]), float(x[1])) for x in page]
        s = out[-1][0] + 1
        if len(page) < 1000:
            break
        time.sleep(0.3)
    return out


def hl_4h(coin: str, a: int, b: int) -> list:
    page = _get('https://api.hyperliquid.xyz/info', {'type': 'candleSnapshot', 'req': {'coin': coin, 'interval': '4h',
                                                                                       'startTime': a, 'endTime': b}})
    time.sleep(1.0)
    return [(int(x['t']), float(x['o'])) for x in page]


def arr(rows):
    if not rows:
        return None
    a = np.array(sorted(set(map(tuple, rows))), dtype=float)
    return a[:, 0].astype(np.int64), a[:, 1]


def load(coin: str, refresh: bool) -> dict:
    a, b = ms(START), ms(END) + 86400_000
    sym, hl = f'{coin}USDT', HL_NAME.get(coin, coin)
    spot_sym = 'PEPEUSDT' if coin == '1000PEPE' else sym  # spot PEPE is per coin: scale cancels in returns
    return {'bn': arr(_cached(f'bnf_{sym}', lambda: bn_funding(sym, a, b), refresh)),
            'hl': arr(_cached(f'hlf_{hl}', lambda: hl_funding(hl, a, b), refresh)),
            'px_bn': arr(_cached(f'bnk_{sym}', lambda: klines_4h('https://fapi.binance.com/fapi/v1/klines', sym, a, b),
                                 refresh)),
            'px_hl': arr(_cached(f'hlk_{hl}', lambda: hl_4h(hl, a, b), refresh)),
            'px_spot': arr(_cached(f'bns_{spot_sym}', lambda: klines_4h('https://api.binance.com/api/v3/klines',
                                                                          spot_sym, a, b), refresh))}


# ------------------------------------------------------------------ run
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--json', type=Path)
    ap.add_argument('--refresh', action='store_true')
    a = ap.parse_args(argv)
    data = {}
    for c in COINS:
        data[c] = load(c, a.refresh)
        print(f'  {c}: ' + ' '.join(f'{k} {0 if v is None else len(v[0])}' for k, v in data[c].items()), flush=True)
    out = {'coins': COINS, 'fees': FEES, 'slip': SLIP, 'splits': {'DEV': DEV, 'HOLDOUT': HOLD}, 'pairs': {}}
    for kind in ('XV', 'SP'):
        res = out['pairs'][kind] = {'variants': {}, 'best': None}
        for mode in ('HEDGE', 'MAKER'):
            for h in H_DAYS:
                for k in K_MULT:
                    trades = []
                    for c in COINS:
                        d = data[c]
                        other_px = d['px_hl'] if kind == 'XV' else d['px_spot']
                        if d['bn'] is None or d['px_bn'] is None or other_px is None or (kind == 'XV' and d['hl'] is None):
                            continue
                        trades += simulate({'bn': d['bn'], 'hl': d['hl'], 'px_bn': d['px_bn'], 'px_other': other_px},
                                           kind, c, h, k, mode)
                    trades = [t for t in trades if t['t'] * 1000 + t['days'] * 86400_000 <= ms(END)]
                    dev = [t for t in trades if t['t'] * 1000 + t['days'] * 86400_000 <= ms(HOLD[0])]
                    res['variants'][f'{mode}|H{h}|k{k:g}'] = {'DEV': summarize(dev, *DEV),
                                                              'HOLDOUT': summarize(trades, *HOLD)}
        c = [v for v in res['variants'] if v.startswith('HEDGE') and res['variants'][v]['DEV'].get('n', 0) >= 30]
        if c:
            b = max(c, key=lambda v: res['variants'][v]['DEV']['mean_net_bps'])
            h = res['variants'][b]['HOLDOUT']
            res['best'] = {'variant': b, 'verdict': 'ELIGIBLE' if eligible(h) else 'RESEARCH_ONLY'}

    def fmt(x):
        if not x.get('n'):
            return 'n 0'
        return (f"n {x['n']:4d} ({x['coins']:2d} coins) net {x['mean_net_bps']:+7.1f} day[{x['ci95_day'][0]:+6.1f},"
                f"{x['ci95_day'][1]:+6.1f}] mon[{x['ci95_month'][0]:+6.1f},{x['ci95_month'][1]:+6.1f}] win "
                f"{x['win_rate']:.0%} fund {x['funding_bps']:+6.1f} basis {x['basis_bps']:+6.1f}(sd {x['basis_sd_bps']:.0f})"
                f" cost {x['cost_bps']:.1f} APR {x['apr_on_notional_pct']:+5.1f}%")
    for kind, res in out['pairs'].items():
        print(f'\n{kind}  DEV-best {res["best"]}')
        for v, r in res['variants'].items():
            print(f'  {v:<14} DEV {fmt(r["DEV"])}\n  {"":<14} HO  {fmt(r["HOLDOUT"])}')
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
