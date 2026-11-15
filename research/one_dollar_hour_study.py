"""ODH-1: capital needed for 1 USD per hour from non-directional lanes (2026-11-15). Offline, public data, no orders.

    python -m research.one_dollar_hour_study fetch     # Binance perp / spot 4 h klines, Bybit funding + 4 h klines
    python -m research.one_dollar_hour_study run       # M1, M2, M3, reference, capital matrix -> data/one_dollar_hour_study.json

Pre-registration: docs/research_upgrade/plan_one_dollar_hour_breakthrough_2026_11.md (frozen before the `fetch` stage).

M1  funding rotation on Binance: perp short + spot long on the symbols with the largest positive 8 h funding.
M2  Binance perp against Bybit perp on the largest funding differential, both directions.
M3  pairs on 18 majors with core.stat_arb_lane.OnlineKalmanPairTracker, unchanged.
M3R pairs on the same 18 majors with a rolling Engle-Granger rule (addendum A1, registered before any outcome).
All three are replayed on a 4 h grid (price = open of the 4 h bar on the leg's own venue), 5 slots (M1, M2) or a
10-pair budget (M3), then turned into the capital that 24 USD per day needs.
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
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from urllib.parse import quote

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.stat_arb_lane import OnlineKalmanPairTracker  # noqa: E402
from research import hawkes_momentum_study as hwk  # noqa: E402

PLAN = 'docs/research_upgrade/plan_one_dollar_hour_breakthrough_2026_11.md'
ODH = ROOT / 'data' / 'odh'
OUT = ROOT / 'data' / 'one_dollar_hour_study.json'
FUNDING = ROOT / 'data' / 'funding_universe'
BN_F, BN_S, BYBIT = 'https://fapi.binance.com', 'https://api.binance.com', 'https://api.bybit.com'
H4, DAY_MS = 4 * 3_600_000, 86_400_000
KLINE_START, FUND_START, END = '2024-08-01', '2024-12-20', '2026-11-13'
PARTS = {'TRAIN': ('2025-01-01', '2025-12-31'), 'HOLDOUT': ('2026-01-01', '2026-09-30'), 'FRESH': ('2026-10-01', '2026-11-12')}
SEED = 20261117


def ms(day: str) -> int:
    return int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)


def day_of(t_ms: int) -> str:
    return datetime.fromtimestamp(t_ms / 1000, tz=timezone.utc).strftime('%Y-%m-%d')


def part_of(day: str) -> Optional[str]:
    for name, (lo, hi) in PARTS.items():
        if lo <= day <= hi:
            return name
    return None


# ================================================================================================ data: fetch
class Pacer:
    """At most one call start per `gap` seconds across threads."""

    def __init__(self, gap: float):
        self.gap, self.lock, self.next = gap, threading.Lock(), 0.0

    def wait(self):
        with self.lock:
            now = time.time()
            at = max(now, self.next)
            self.next = at + self.gap
        if at > now:
            time.sleep(at - now)


def http_json(url: str, tries: int = 6, timeout: int = 40):
    """Parsed body, or None on 400 / 404 (not served: UNKNOWN). Other failures are retried, then raised."""
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'research'}), timeout=timeout) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (400, 404):
                return None
            if k == tries - 1:
                raise
            time.sleep(15 * (k + 1) if e.code in (418, 429, 403) else 2 * (k + 1))
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2 * (k + 1))
    return None


def perp_symbols(info: dict) -> list:
    return sorted(s['symbol'] for s in info.get('symbols', [])
                  if s.get('quoteAsset') == 'USDT' and s.get('contractType') == 'PERPETUAL' and s.get('underlyingType') == 'COIN')


MULT_PREFIX = (('1000000', 1e6), ('1000', 1e3), ('1M', 1e6))


def spot_name(perp: str, spot: set) -> Optional[tuple]:
    """(spot symbol, price multiplier of the perp) of the same coin, or None: 1000PEPEUSDT -> (PEPEUSDT, 1000)."""
    base = perp[:-4]
    for pre, mult in MULT_PREFIX:
        if base.startswith(pre) and base[len(pre):] + 'USDT' in spot:
            return base[len(pre):] + 'USDT', mult
    return (perp, 1.0) if perp in spot else None


def bn_klines(base: str, path: str, sym: str, start: int, end: int, limit: int, get=http_json, pace: Optional[Pacer] = None) -> list:
    """[[t, open, high, low, close, quote volume]] of 4 h bars, oldest first, paged forward."""
    out, t = [], start
    while t <= end:                                     # endTime is inclusive: the bar that opens at `end` is wanted
        if pace:
            pace.wait()
        rows = get(f'{base}{path}?symbol={quote(sym)}&interval=4h&startTime={t}&endTime={end}&limit={limit}')
        if not rows:
            break
        out += [[int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7])] for r in rows]
        if len(rows) < limit:
            break
        t = int(rows[-1][0]) + H4
    return out


def bybit_paged(path: str, sym: str, start: int, end: int, limit: int, key, row, get=http_json) -> list:
    """Bybit lists are newest first: page backwards by the end time; -> rows oldest first."""
    out, hi = [], end
    while hi > start:
        d = get(f'{BYBIT}{path}&symbol={quote(sym)}&limit={limit}&{key[0]}={start}&{key[1]}={hi}')
        rows = ((d or {}).get('result') or {}).get('list') or []
        if not rows:
            break
        out += [row(r) for r in rows]
        lo = min(x[0] for x in out[-len(rows):])
        if len(rows) < limit or lo <= start:
            break
        hi = lo - 1
    return sorted(out)


def bybit_funding(sym: str, start: int, end: int, get=http_json) -> list:
    """[[t, rate in bps]]."""
    return bybit_paged('/v5/market/funding/history?category=linear', sym, start, end, 200, ('startTime', 'endTime'),
                       lambda r: [int(r['fundingRateTimestamp']), float(r['fundingRate']) * 1e4], get)


def bybit_klines(sym: str, start: int, end: int, get=http_json) -> list:
    """[[t, open, high, low, close, turnover]] of 4 h bars."""
    return bybit_paged('/v5/market/kline?category=linear&interval=240', sym, start, end, 1000, ('start', 'end'),
                       lambda r: [int(r[0]), float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[6])], get)


def fetch_all(out: Path = ODH, workers: int = 4, log=print) -> dict:
    """Everything the study reads beyond data/funding_universe; one JSON per symbol and source, resumable."""
    out.mkdir(parents=True, exist_ok=True)
    perps = perp_symbols(json.loads((FUNDING / 'exchangeInfo.json').read_text(encoding='utf-8')))
    meta_p = out / 'meta.json'
    if meta_p.exists():
        meta = json.loads(meta_p.read_text())
    else:
        spot = {s['symbol'] for s in http_json(f'{BN_S}/api/v3/exchangeInfo?permissions=SPOT')['symbols'] if s['quoteAsset'] == 'USDT'}
        by = http_json(f'{BYBIT}/v5/market/instruments-info?category=linear&limit=1000')['result']['list']
        by = {x['symbol'] for x in by if x['quoteCoin'] == 'USDT' and x['contractType'] == 'LinearPerpetual'}
        meta = {'perps': perps, 'spot': {p: list(spot_name(p, spot)) for p in perps if spot_name(p, spot)},
                'bybit': sorted(set(perps) & by)}
        meta_p.write_text(json.dumps(meta))
    a, f0, b = ms(KLINE_START), ms(FUND_START), ms(END)
    pace = Pacer(0.3)                                   # fapi klines at limit 1500 weigh 10: 200 calls a minute of 240
    jobs = [('bn_perp', p, lambda p=p: bn_klines(BN_F, '/fapi/v1/klines', p, a, b, 1500, pace=pace)) for p in perps]
    jobs += [('bn_spot', p, lambda p=p: bn_klines(BN_S, '/api/v3/klines', meta['spot'][p][0], f0, b, 1000)) for p in meta['spot']]
    jobs += [('by_fund', p, lambda p=p: bybit_funding(p, f0, b)) for p in meta['bybit']]
    jobs += [('by_kl', p, lambda p=p: bybit_klines(p, f0, b)) for p in meta['bybit']]
    todo = [j for j in jobs if not (out / j[0] / f'{j[1]}.json').exists()]
    log(f'{len(todo)} files to fetch of {len(jobs)}')
    lock, done = threading.Lock(), [0]

    def work(job):
        kind, sym, fn = job
        rows = fn()
        (out / kind).mkdir(exist_ok=True)
        tmp = out / kind / f'{sym}.part'
        tmp.write_text(json.dumps(rows))
        tmp.replace(out / kind / f'{sym}.json')
        with lock:
            done[0] += 1
            if done[0] % 100 == 0:
                log(f'  {done[0]}/{len(todo)}')

    with ThreadPoolExecutor(workers) as ex:
        list(ex.map(work, todo))
    return {'files': len(jobs), 'fetched': len(todo), 'perps': len(perps), 'spot': len(meta['spot']), 'bybit': len(meta['bybit'])}


# ================================================================================================ data: grid
def grid(start: str = KLINE_START, end: str = END) -> np.ndarray:
    return np.arange(ms(start), ms(end) + 1, H4, dtype=np.int64)


def on_grid(rows: list, t: np.ndarray, col: int) -> np.ndarray:
    """Column `col` of [[t, ...]] rows placed on the grid; NaN where there is no bar at that boundary."""
    out = np.full(len(t), np.nan)
    if rows:
        a = np.asarray(rows, dtype=np.float64)
        k = np.rint((a[:, 0] - t[0]) / H4).astype(np.int64)
        ok = (k >= 0) & (k < len(t)) & (np.abs(a[:, 0] - (t[0] + k * H4)) < 1000)
        out[k[ok]] = a[ok, col]
    return out


HOLE_H = 16.0                                   # no print for 16 h (2 x the longest interval): the record has a hole


def funding_steps(prints: list, t: np.ndarray, hole_h: float = HOLE_H) -> np.ndarray:
    """F[k] = sum of the prints with t[k-1] < time <= t[k] in bps; NaN outside [first print's step, last print's step] and
    on a step that ends more than `hole_h` hours after the last print before it (a hole in the record: UNKNOWN, not 0)."""
    out = np.full(len(t), np.nan)
    if not prints:
        return out
    a = np.asarray(prints, dtype=np.float64)
    a = a[np.argsort(a[:, 0], kind='stable')]
    k = np.ceil((a[:, 0] - 1000 - t[0]) / H4).astype(np.int64)        # a print up to 1 s after a boundary belongs to it
    k = np.clip(k, 0, None)
    lo, hi = int(max(k.min(), 0)), int(min(k.max(), len(t) - 1))
    if lo > hi:
        return out
    out[lo:hi + 1] = 0.0
    ok = k < len(t)
    np.add.at(out, k[ok], a[ok, 1])
    ends = t[lo:hi + 1]
    j = np.searchsorted(a[:, 0], ends + 1000, side='right') - 1           # last print at or before the step's end (+1 s)
    since = ends - a[np.clip(j, 0, None), 0]
    seg = out[lo:hi + 1]
    seg[(j < 0) | (since > hole_h * 3_600_000 + 1000)] = np.nan
    return out


def trailing_sum(x: np.ndarray, n: int) -> np.ndarray:
    """Sum of the n values before each index (x[k-n] .. x[k-1]); NaN where fewer exist or any of them is NaN. A NaN
    counts only inside its own windows (a plain cumulative sum would carry it to every later index)."""
    out = np.full(x.shape, np.nan)
    bad = ~np.isfinite(x)
    pad = np.zeros(x.shape[:-1] + (1,))
    c = np.concatenate([pad, np.cumsum(np.where(bad, 0.0, x), axis=-1)], axis=-1)
    nb = np.concatenate([pad, np.cumsum(bad, axis=-1)], axis=-1)
    s, holes = c[..., n:-1] - c[..., :-n - 1], nb[..., n:-1] - nb[..., :-n - 1]
    out[..., n:] = np.where(holes == 0, s, np.nan)
    return out


def load_market(src: Path = ODH, funding: Path = FUNDING) -> dict:
    """Arrays [symbol, step] on the 4 h grid. Legs: a = Binance perp, b = Binance spot, c = Bybit perp."""
    meta = json.loads((src / 'meta.json').read_text())
    syms, t = meta['perps'], grid()
    mk = lambda: np.full((len(syms), len(t)), np.nan)
    m = {'sym': np.array(syms), 't': t, **{k: mk() for k in ('pa', 'ha', 'la', 'qa', 'fa', 'pb', 'qb', 'pc', 'hc', 'lc', 'qc', 'fc')}}
    m['mult'] = np.array([meta['spot'].get(s, [None, 1.0])[1] for s in syms], dtype=np.float64)

    def rows(kind, s):
        p = src / kind / f'{s}.json'
        return json.loads(p.read_text()) if p.exists() else []

    for i, s in enumerate(syms):
        r = rows('bn_perp', s)
        for key, col in (('pa', 1), ('ha', 2), ('la', 3), ('qa', 5)):
            m[key][i] = on_grid(r, t, col)
        fp = funding / f'fr_{s}.json'
        m['fa'][i] = funding_steps(json.loads(fp.read_text()) if fp.exists() else [], t)
        if s in meta['spot']:
            r = rows('bn_spot', s)
            m['pb'][i], m['qb'][i] = on_grid(r, t, 1), on_grid(r, t, 5)
        if s in meta['bybit']:
            r = rows('by_kl', s)
            for key, col in (('pc', 1), ('hc', 2), ('lc', 3), ('qc', 5)):
                m[key][i] = on_grid(r, t, col)
            m['fc'][i] = funding_steps(rows('by_fund', s), t)
    return m


# ================================================================================================ carry engine
K_SLOTS, MIN_HOLD, LIQ_MOVE, LIQ_PENALTY = 5, 2, 0.45, 150.0
MIN_QV_A, MIN_QV_B, MAX_GAP = 5e6, 1e6, 0.10
M1_THETA, M2_THETA, THETA_OUT_FRAC = (5, 10, 20), (3, 5, 10), (0.0, 0.5)
SIDE_COST = {'M1': {'HEDGE': 15.0, 'TAKER': 21.0, 'BNB': 12.3, 'ZERO': 0.0},
             'M2': {'HEDGE': 10.0, 'TAKER': 16.5, 'BNB': 9.5, 'ZERO': 0.0},
             'M3': {'HEDGE': 4.0, 'TAKER': 6.0, 'ZERO': 0.0}}


def carry_inputs(m: dict, kind: str) -> dict:
    """Legs, signal and tradability of M1 (perp short / spot long, positive funding only) or M2 (Binance vs Bybit)."""
    pa, fa = m['pa'], m['fa']
    if kind == 'M1':
        pb, fb, qb = m['pb'] * m['mult'][:, None], np.zeros_like(fa), m['qb']
        hb = lb = None
    elif kind == 'M2':
        pb, fb, qb, hb, lb = m['pc'], m['fc'], m['qc'], m['hc'], m['lc']
    else:
        raise ValueError(f'unknown carry kind {kind}')
    f8 = lambda f: f + np.concatenate([np.full((f.shape[0], 1), np.nan), f[:, :-1]], axis=1)
    sig = f8(fa) - f8(fb)
    nxt = lambda p: np.concatenate([p[:, 1:], np.full((p.shape[0], 1), np.nan)], axis=1)
    with np.errstate(invalid='ignore', divide='ignore'):
        ok = (np.isfinite(pa) & np.isfinite(nxt(pa)) & np.isfinite(pb) & np.isfinite(nxt(pb)) & np.isfinite(sig)
              & np.isfinite(nxt(fa)) & np.isfinite(nxt(fb))
              & (trailing_sum(m['qa'], 6) >= MIN_QV_A) & (trailing_sum(qb, 6) >= MIN_QV_B) & (np.abs(pa / pb - 1.0) < MAX_GAP))
    return {'kind': kind, 'pa': pa, 'pb': pb, 'fa': fa, 'fb': fb, 'ha': m['ha'], 'la': m['la'], 'hb': hb, 'lb': lb,
            'sig': sig, 'ok': ok, 'qv24': trailing_sum(m['qa'], 6), 't': m['t'], 'sym': m['sym']}


def run_carry(x: dict, theta: float, theta_out: float, side_cost: float, k_slots: int = K_SLOTS) -> dict:
    """K-slot rotation on the 4 h grid (plan section 2). -> {'t', 'step' (bps of the budget per step), 'rounds'}."""
    kind, pa, pb, fa, fb, sig, ok = (x[k] for k in ('kind', 'pa', 'pb', 'fa', 'fb', 'sig', 'ok'))
    n_t = pa.shape[1]
    step, held, rounds = np.zeros(n_t), {}, []

    def close(i, k, why):
        pos = held.pop(i)
        pos['cost'] += side_cost
        rounds.append({'sym': str(x['sym'][i]), 'd': pos['d'], 't_in': int(x['t'][pos['k']]), 't_out': int(x['t'][k]), 'steps': k - pos['k'],
                       'funding': pos['funding'], 'price': pos['price'], 'cost': pos['cost'], 'penalty': pos['penalty'],
                       'net': pos['funding'] + pos['price'] - pos['cost'] - pos['penalty'], 'why': why, 'qv24': pos['qv24'],
                       'sig': pos['sig']})
        return side_cost

    for k in range(n_t - 1):
        charge = 0.0
        for i in list(held):                                            # exits first
            pos = held[i]
            s = sig[i, k]
            gone = not (np.isfinite(pa[i, k + 1]) and np.isfinite(pb[i, k + 1]) and np.isfinite(fa[i, k + 1]) and np.isfinite(fb[i, k + 1]))
            if gone or (k - pos['k'] >= MIN_HOLD and not (np.isfinite(s) and pos['d'] * s >= theta_out)):
                charge += close(i, k, 'DATA' if gone else 'SIGNAL')
        free = k_slots - len(held)
        if free > 0:                                                    # then entries, largest signal first
            s = sig[:, k]
            want = ok[:, k] & ((s >= theta) if kind == 'M1' else (np.abs(s) >= theta))
            for i in held:
                want[i] = False
            cand = np.flatnonzero(want)
            for i in cand[np.argsort(-np.abs(s[cand]), kind='stable')][:free]:
                held[int(i)] = {'k': k, 'd': 1 if s[i] > 0 else -1, 'funding': 0.0, 'price': 0.0, 'cost': side_cost, 'penalty': 0.0,
                                'qv24': float(x['qv24'][i, k]), 'sig': float(s[i])}
                charge += side_cost
        gain = 0.0
        for i in list(held):                                            # accrue the step k -> k + 1
            pos, d = held[i], held[i]['d']
            fund = d * (fa[i, k + 1] - fb[i, k + 1])
            px = d * (-(pa[i, k + 1] / pa[i, k] - 1.0) + (pb[i, k + 1] / pb[i, k] - 1.0)) * 1e4
            pos['funding'] += fund
            pos['price'] += px
            gain += fund + px
            sh, sp, lg_lo, lg_p = (x['ha'][i, k], pa[i, k], None if x['lb'] is None else x['lb'][i, k], pb[i, k]) if d > 0 else (
                None if x['hb'] is None else x['hb'][i, k], pb[i, k], x['la'][i, k], pa[i, k])
            liq = (sh is not None and np.isfinite(sh) and sh / sp - 1.0 >= LIQ_MOVE) or (
                lg_lo is not None and np.isfinite(lg_lo) and 1.0 - lg_lo / lg_p >= LIQ_MOVE)
            if liq:
                pos['penalty'] += LIQ_PENALTY
                charge += LIQ_PENALTY + close(i, k + 1, 'LIQUIDATION')
        step[k] = (gain - charge) / k_slots
    for i in list(held):
        close(i, n_t - 1, 'END')
    return {'t': x['t'], 'step': step, 'rounds': rounds}


def daily(t: np.ndarray, step: np.ndarray, lo: str = PARTS['TRAIN'][0], hi: str = PARTS['FRESH'][1]) -> dict:
    """{day: sum of the steps that start on it} for lo <= day <= hi."""
    a, b = ms(lo), ms(hi) + DAY_MS
    sel = (t >= a) & (t < b)
    idx = ((t[sel] - a) // DAY_MS).astype(np.int64)
    tot = np.bincount(idx, weights=step[sel], minlength=(b - a) // DAY_MS)
    return {day_of(a + k * DAY_MS): float(v) for k, v in enumerate(tot)}


# ================================================================================================ pairs (M3)
PAIR_COINS = ('BTC', 'ETH', 'SOL', 'XRP', 'DOGE', 'BNB', 'ADA', 'SUI', 'LINK', 'AVAX', 'ENA', 'AAVE', 'LTC', 'HYPE', 'WLD', 'TAO',
              'ZEC', '1000PEPE')
PAIR_BARS, Z_ENTRY, Z_EXIT, Z_STOP, TRACKER_COST, K_PAIRS = (1, 6), (2.1, 2.5, 3.0), 0.25, 3.6, 8.0, 10


def run_pair(pa: np.ndarray, pb: np.ndarray, fa: np.ndarray, fb: np.ndarray, z_entry: float, side_cost: float,
             name: tuple = ('A', 'B')) -> dict:
    """One pair through the module's tracker, bar by bar. -> {'pnl' (bps of the pair's gross; the move of a bar is booked
    at the bar's START, a cost at the bar where it is paid, as in the carry engine), 'open' (held during the bar that
    starts there), 'trades'}. The entry weights are held until the tracker's exit or stop (its HOLD weights, which
    follow the drifting beta, would be an uncosted daily rebalance)."""
    tr = OnlineKalmanPairTracker(*name)
    n = len(pa)
    pnl, opened, trades = np.zeros(n), np.zeros(n, dtype=bool), []
    wa = wb = 0.0
    last, cur = None, None
    for i in range(n):
        if not (np.isfinite(pa[i]) and np.isfinite(pb[i])):
            continue
        if last is not None and (wa or wb):
            g = (wa * (pa[i] / pa[last] - 1.0) + wb * (pb[i] / pb[last] - 1.0)) * 1e4
            g -= wa * np.nansum(fa[last + 1:i + 1]) + wb * np.nansum(fb[last + 1:i + 1])
            pnl[last] += g
            opened[last] = True
            cur['gross'] += g
        sig = tr.evaluate_signal(float(pa[i]), float(pb[i]), round_trip_cost_bps=TRACKER_COST, z_entry=z_entry, z_exit=Z_EXIT,
                                 z_stop=Z_STOP)
        if sig.action in ('ENTER_LONG_SPREAD', 'ENTER_SHORT_SPREAD'):
            wa, wb = sig.weight_a, sig.weight_b
            c = side_cost * (abs(wa) + abs(wb))
            pnl[i] -= c
            cur = {'i_in': i, 'gross': 0.0, 'cost': c, 'wa': wa, 'wb': wb}
        elif sig.action in ('EXIT_SPREAD', 'STOP_BREAKDOWN') and (wa or wb):
            c = side_cost * (abs(wa) + abs(wb))
            pnl[i] -= c
            trades.append({'i_in': cur['i_in'], 'i_out': i, 'gross': cur['gross'], 'cost': cur['cost'] + c,
                           'net': cur['gross'] - cur['cost'] - c, 'why': sig.action})
            wa = wb = 0.0
        last = i
    return {'pnl': pnl, 'open': opened, 'trades': trades}


# ---------------------------------------------------------------------- addendum A1: rolling Engle-Granger pairs (M3R)
ADDENDUM = 'docs/research_upgrade/plan_one_dollar_hour_addendum_a1_2026_11.md'
EG_L, EG_CRIT, EG_HL, EG_BETA, EG_TIME_MIN, N_TRIALS_A1 = 120, -3.34, (1.0, 60.0), (0.20, 3.50), 120, 24


def eg_fit(y: np.ndarray, x: np.ndarray, L: int = EG_L) -> dict:
    """At each bar t >= L: OLS of y on x over bars t-L .. t-1 (bar t excluded), residual sd (ddof 2), Engle-Granger
    t-statistic of phi in dr_j = phi r_(j-1) (no constant, no lag) and the half-life -ln 2 / ln(1 + phi) (inf unless
    0 < 1 + phi < 1). NaN where the window holds a missing price."""
    from numpy.lib.stride_tricks import sliding_window_view
    n = len(y)
    out = {k: np.full(n, np.nan) for k in ('alpha', 'beta', 'sd', 'eg', 'hl')}
    if n <= L:
        return out
    Y, X = sliding_window_view(y, L)[:-1], sliding_window_view(x, L)[:-1]      # row i: bars i .. i+L-1, used at t = i+L
    ok = np.isfinite(Y).all(axis=1) & np.isfinite(X).all(axis=1)
    if not ok.any():
        return out
    Y, X = Y[ok], X[ok]
    mx, my = X.mean(axis=1), Y.mean(axis=1)
    dx, dy = X - mx[:, None], Y - my[:, None]
    sxx = (dx * dx).sum(axis=1)
    with np.errstate(invalid='ignore', divide='ignore'):
        beta = (dx * dy).sum(axis=1) / sxx
        alpha = my - beta * mx
        r = Y - alpha[:, None] - beta[:, None] * X
        sd = np.sqrt((r * r).sum(axis=1) / (L - 2))
        r0, dr = r[:, :-1], np.diff(r, axis=1)
        s00 = (r0 * r0).sum(axis=1)
        phi = (r0 * dr).sum(axis=1) / s00
        e = dr - phi[:, None] * r0
        eg = phi / np.sqrt((e * e).sum(axis=1) / (L - 2) / s00)
        b = 1.0 + phi
        inside = (b > 0) & (b < 1)
        hl = np.where(inside, -math.log(2.0) / np.log(np.where(inside, b, 0.5)), np.inf)
    at = np.flatnonzero(ok) + L
    for k, v in (('alpha', alpha), ('beta', beta), ('sd', sd), ('eg', eg), ('hl', hl)):
        out[k][at] = v
    return out


def run_pair_eg(pa: np.ndarray, pb: np.ndarray, fa: np.ndarray, fb: np.ndarray, z_entry: float, side_cost: float,
                name: tuple = ('A', 'B')) -> dict:
    """One pair under the addendum's rolling Engle-Granger rule; same accounting and output as `run_pair`."""
    with np.errstate(invalid='ignore', divide='ignore'):
        y, x = np.log(pa), np.log(pb)
    fit = eg_fit(y, x)
    n = len(pa)
    pnl, opened, trades = np.zeros(n), np.zeros(n, dtype=bool), []
    pos, last = None, None
    for i in range(n):
        if not (np.isfinite(pa[i]) and np.isfinite(pb[i])):
            continue
        if pos is not None:
            g = (pos['wa'] * (pa[i] / pa[last] - 1.0) + pos['wb'] * (pb[i] / pb[last] - 1.0)) * 1e4
            g -= pos['wa'] * np.nansum(fa[last + 1:i + 1]) + pos['wb'] * np.nansum(fb[last + 1:i + 1])
            pnl[last] += g
            opened[last] = True
            pos['gross'] += g
            pos['bars'] += 1
            z = (y[i] - pos['alpha'] - pos['beta'] * x[i]) / pos['sd']
            d = pos['d']
            why = ('STOP_BREAKDOWN' if d * z <= -Z_STOP else 'EXIT_SPREAD' if d * z >= -Z_EXIT
                   else 'TIME_STOP' if pos['bars'] >= pos['max_bars'] else None)
            if why:
                c = side_cost * (abs(pos['wa']) + abs(pos['wb']))
                pnl[i] -= c
                trades.append({'i_in': pos['i_in'], 'i_out': i, 'gross': pos['gross'], 'cost': pos['cost'] + c,
                               'net': pos['gross'] - pos['cost'] - c, 'why': why})
                pos = None
        else:
            be, sd, eg, hl = fit['beta'][i], fit['sd'][i], fit['eg'][i], fit['hl'][i]
            if (np.isfinite(eg) and eg < EG_CRIT and EG_HL[0] <= hl <= EG_HL[1] and EG_BETA[0] <= be <= EG_BETA[1]
                    and np.isfinite(sd) and sd > 0):
                z = (y[i] - fit['alpha'][i] - be * x[i]) / sd
                if z_entry <= abs(z) < Z_STOP:
                    d = 1 if z < 0 else -1
                    wa, wb = d / (1.0 + be), -d * be / (1.0 + be)
                    c = side_cost * (abs(wa) + abs(wb))
                    pnl[i] -= c
                    pos = {'d': d, 'wa': wa, 'wb': wb, 'alpha': fit['alpha'][i], 'beta': be, 'sd': sd, 'i_in': i, 'bars': 0,
                           'max_bars': max(int(2.5 * hl), EG_TIME_MIN), 'gross': 0.0, 'cost': c}
        last = i
    return {'pnl': pnl, 'open': opened, 'trades': trades}


PAIR_RUNNERS = {'tracker': run_pair, 'eg': run_pair_eg}


def _pair_job(args):
    return PAIR_RUNNERS[args[0]](*args[1:])


def run_pairs(prices: dict, funding: dict, t: np.ndarray, bar: int, z_entry: float, side_cost: float, k_pairs: int = K_PAIRS,
              workers: int = 1, method: str = 'tracker') -> dict:
    """Every pair of `prices` on bars of `bar` grid steps (1 = 4 h, 6 = one UTC day). Each open pair is one unit of gross;
    the bar return is the sum of the pairs' P&L over max(k_pairs, pairs held during the bar), in bps of the gross budget,
    booked at the bar's start."""
    idx = np.arange(0, len(t), bar) if bar == 1 else np.flatnonzero(t % DAY_MS == 0)
    coins = sorted(prices)
    fsum = {c: np.add.reduceat(np.nan_to_num(np.concatenate([funding[c][1:], [0.0]])), idx)[:len(idx)] for c in coins}
    fbar = {c: np.concatenate([[0.0], fsum[c][:-1]]) for c in coins}                  # funding paid over the bar that ENDS at idx[j]
    jobs, names = [], []
    for a_i, a in enumerate(coins):
        for b in coins[a_i + 1:]:
            jobs.append((method, prices[a][idx], prices[b][idx], fbar[a], fbar[b], z_entry, side_cost, (a, b)))
            names.append((a, b))
    if workers > 1:
        with ProcessPoolExecutor(workers) as ex:
            res = list(ex.map(_pair_job, jobs, chunksize=4))
    else:
        res = [_pair_job(j) for j in jobs]
    tot = np.sum([r['pnl'] for r in res], axis=0)
    n_open = np.sum([r['open'] for r in res], axis=0)
    step = np.zeros(len(t))
    step[idx] = tot / np.maximum(k_pairs, n_open)
    rounds = [{'pair': f'{a}/{b}', 't_in': int(t[idx[x['i_in']]]), 't_out': int(t[idx[x['i_out']]]), 'steps': (x['i_out'] - x['i_in']) * bar,
               'gross': x['gross'], 'cost': x['cost'], 'net': x['net'], 'why': x['why']} for (a, b), r in zip(names, res) for x in r['trades']]
    return {'t': t, 'step': step, 'rounds': rounds, 'max_open': int(n_open.max()) if len(n_open) else 0,
            'mean_open': float(n_open[idx >= np.searchsorted(t, ms(PARTS['TRAIN'][0]))].mean()) if len(n_open) else 0.0}


# ================================================================================================ statistics
NW_LAGS, N_TRIALS, MIN_TRAIN_ROUNDS, MIN_HOLDOUT_ROUNDS = 7, 18, 30, 30


def nw_se(x, lags: int = NW_LAGS) -> float:
    """Newey-West standard error of the mean (Bartlett weights), never below the i.i.d. one."""
    x = np.asarray(x, dtype=np.float64)
    n = x.size
    if n < 2:
        return math.nan
    e = x - x.mean()
    var = float(e @ e) / n
    lr = var
    for lag in range(1, min(lags, n - 1) + 1):
        lr += 2.0 * (1.0 - lag / (lags + 1.0)) * float(e[lag:] @ e[:-lag]) / n
    iid = x.std(ddof=1) / math.sqrt(n)
    return float(max(math.sqrt(max(lr, 0.0) / n), iid))


def day_stats(days: dict, part: str, n_trials: int = 0) -> dict:
    """Mean daily return (bps of the budget) of a partition with the Newey-West bound and the one-sided p-value."""
    lo, hi = PARTS[part]
    x = np.array([v for d, v in sorted(days.items()) if lo <= d <= hi], dtype=np.float64)
    if x.size < 2:
        return {'days': int(x.size)}
    se = nw_se(x)
    z = x.mean() / se if se > 0 else 0.0
    out = {'days': int(x.size), 'mean': float(x.mean()), 'se': se, 'lcb95': float(x.mean() - 1.645 * se),
           'ucb95': float(x.mean() + 1.645 * se), 'p_one_sided': float(0.5 * math.erfc(z / math.sqrt(2.0))),
           'apr_pct': float(x.mean() * 365.0 / 100.0), 'sd': float(x.std(ddof=1)), 'worst_day': float(x.min()),
           'positive_days': float((x > 0).mean())}
    if n_trials >= 1:
        out['dsr'] = hwk.deflated(x, n_trials)
    return out


def round_stats(rounds: list, part: str) -> dict:
    lo, hi = ms(PARTS[part][0]), ms(PARTS[part][1]) + DAY_MS
    r = [x for x in rounds if lo <= x['t_in'] < hi]
    if not r:
        return {'n': 0}
    out = {'n': len(r), 'net': float(np.mean([x['net'] for x in r])), 'win': float(np.mean([x['net'] > 0 for x in r])),
           'hold_h': float(np.mean([x['steps'] for x in r]) * 4.0), 'cost': float(np.mean([x['cost'] for x in r]))}
    for k in ('funding', 'price', 'gross', 'penalty'):
        if k in r[0]:
            out[k] = float(np.mean([x[k] for x in r]))
    if 'why' in r[0]:
        out['exits'] = {w: sum(1 for x in r if x['why'] == w) for w in sorted({x['why'] for x in r})}
    if 'qv24' in r[0]:
        out['median_qv24'] = float(np.median([x['qv24'] for x in r]))
        out['symbols'] = len({x['sym'] for x in r})
    return out


def verdict(holdout: dict, fresh: dict, holdout_rounds: int) -> dict:
    checks = {f'holdout_rounds>={MIN_HOLDOUT_ROUNDS}': holdout_rounds >= MIN_HOLDOUT_ROUNDS,
              'holdout_lcb95>0': holdout.get('lcb95', -math.inf) > 0,
              'holdout_dsr>=0.95': (holdout.get('dsr') or 0.0) >= 0.95,
              'fresh_mean>0': fresh.get('mean', -math.inf) > 0}
    return {'checks': checks, 'verdict': 'EDGE' if all(checks.values()) else 'NO EDGE'}


def choose(table: dict) -> Optional[str]:
    """TRAIN-best cell: highest mean daily return among cells with at least MIN_TRAIN_ROUNDS rounds."""
    ok = {k: v for k, v in table.items() if v['rounds'].get('n', 0) >= MIN_TRAIN_ROUNDS and 'mean' in v['days']}
    return max(ok, key=lambda k: ok[k]['days']['mean']) if ok else None


# ================================================================================================ capital matrix
PER_BUDGET = {'M1': 1.5, 'M2': 1.0, 'M3': 0.5, 'XVHL': 1.0}
MIN_LEG = {'M1': 5.0, 'M2': 5.0, 'M3': 5.0, 'XVHL': 10.0}
SLOTS = {'M1': K_SLOTS, 'M2': K_SLOTS, 'M3': K_PAIRS, 'XVHL': 18}
TRANSFER_30D = {'M2': 2.0, 'XVHL': 2.0}
TARGET_DAY, TIERS = 24.0, (50, 100, 500, 1000, 2500, 5000, 10000, 25000, 50000, 100000, 250000, 500000)
PATHS, HORIZONS, BLOCK_DAYS, RUIN_FRAC = 2000, (30, 90), 7, 0.5


def base(mech: str) -> str:
    """The plan's mechanism whose capital, slot and cost constants an addendum mechanism shares (M3R -> M3)."""
    return {'M3R': 'M3'}.get(mech, mech)


def slots_fundable(mech: str, capital: float) -> int:
    """Slots a capital can hold at the venue minimum per leg (M3: a pair's unit of gross is two legs)."""
    mech = base(mech)
    budget = capital / PER_BUDGET[mech]
    per_slot_min = MIN_LEG[mech] * (2.0 if mech == 'M3' else 1.0)
    return int(min(SLOTS[mech], math.floor(budget / per_slot_min + 1e-9)))


def capital_star(cap_bps: np.ndarray, mech: str) -> dict:
    """Capital at which the mean daily return pays TARGET_DAY (plus the assumed transfers), point and lower 95 % bound."""
    if cap_bps.size < 2:
        return {'point': None, 'lcb95': None}
    mean, se = float(cap_bps.mean()), nw_se(cap_bps)
    need = TARGET_DAY + TRANSFER_30D.get(base(mech), 0.0) / 30.0
    star = lambda y: need / (y * 1e-4) if y > 0 else None
    return {'mean_bps_day': mean, 'se': se, 'apr_pct': mean * 3.65, 'apr_lcb95_pct': (mean - 1.645 * se) * 3.65,
            'point': star(mean), 'lcb95': star(mean - 1.645 * se)}


def capital_mc(cap_bps: np.ndarray, mech: str, capital: float, days: int, paths: int = PATHS, seed: int = SEED) -> dict:
    """Block bootstrap of the daily return on capital; profits are not reinvested. Hourly profit in USD."""
    k = slots_fundable(mech, capital)
    if k < 1 or cap_bps.size < BLOCK_DAYS:
        return {'fundable': False, 'slots': k}
    rng = np.random.default_rng(seed)
    n_blocks = -(-days // BLOCK_DAYS)
    starts = rng.integers(0, cap_bps.size - BLOCK_DAYS + 1, size=(paths, n_blocks))
    r = cap_bps[(starts[:, :, None] + np.arange(BLOCK_DAYS)[None, None, :]).reshape(paths, -1)[:, :days]]
    pnl = capital * r * 1e-4 - TRANSFER_30D.get(base(mech), 0.0) / 30.0
    eq = capital + np.cumsum(pnl, axis=1)
    hourly = (eq[:, -1] - capital) / (24.0 * days)
    return {'fundable': True, 'slots': k, 'hourly_p05': float(np.quantile(hourly, 0.05)), 'hourly_median': float(np.median(hourly)),
            'hourly_p95': float(np.quantile(hourly, 0.95)), 'p_hourly_ge_1': float((hourly >= 1.0).mean()),
            'p_loss': float((hourly < 0).mean()), 'p_ruin': float((eq.min(axis=1) <= RUIN_FRAC * capital).mean())}


def capacity(out: dict, mech: str, qv24: Optional[float] = None) -> dict:
    """Plan section 5: the slot notional at C* against the median 24 h Binance volume of the symbols held on HOLDOUT
    (`qv24` overrides it, e.g. the pair coins' median for M3, whose rounds carry no volume)."""
    cs = (out.get('capital') or {}).get('c_star') or {}
    vol = qv24 if qv24 is not None else ((out.get('holdout') or {}).get('rounds') or {}).get('median_qv24')
    res = {'median_qv24_usdt': vol}
    for key in ('point', 'lcb95'):
        c = cs.get(key)
        slot = c / PER_BUDGET[base(mech)] / SLOTS[base(mech)] if c else None
        res[f'slot_notional_{key}'] = slot
        res[f'share_of_24h_volume_{key}'] = slot / vol if (slot and vol) else None
    return res


def capital_matrix(days: dict, mech: str, paths: int = PATHS) -> dict:
    """C* and the tier table from the HOLDOUT + FRESH daily returns of one mechanism."""
    x = np.array([v for d, v in sorted(days.items()) if PARTS['HOLDOUT'][0] <= d <= PARTS['FRESH'][1]], dtype=np.float64)
    cap = x / PER_BUDGET[base(mech)]
    return {'days': int(cap.size), 'c_star': capital_star(cap, mech),
            'tiers': {str(c): {str(h): capital_mc(cap, mech, float(c), h, paths) for h in HORIZONS} for c in TIERS}}


# ================================================================================================ reference lane
def reference_xvhl() -> dict:
    """The stored rule XV HEDGE|H28|k2 (Binance vs Hyperliquid, 18 coins) from the funding cache, as daily accruals on
    an 18-slot budget. Not searched here."""
    from tools import funding_spread_replay as fsr
    rounds, per_day = [], {}
    end = fsr.ms(fsr.END)
    for coin in fsr.COINS:
        d = fsr.load(coin, refresh=False)
        if d['bn'] is None or d['hl'] is None or d['px_bn'] is None or d['px_hl'] is None:
            continue
        series = {'bn': d['bn'], 'hl': d['hl'], 'px_bn': d['px_bn'], 'px_other': d['px_hl']}
        for tr in fsr.simulate(series, 'XV', coin, 28, 2.0, 'HEDGE'):
            t_in, n = int(round(tr['t'] * 1000)), int(tr['days'])
            if t_in + n * DAY_MS > end:                           # the replay's own rule: a round must end inside its cache
                continue
            rounds.append({'sym': coin, 't_in': t_in, 't_out': t_in + n * DAY_MS, 'steps': n * 6, 'net': float(tr['net_bps']),
                           'cost': float(tr['cost_bps']), 'funding': float(tr['funding_bps']), 'price': float(tr['basis_bps'])})
            for j in range(n):
                day = day_of(t_in + j * DAY_MS)
                per_day[day] = per_day.get(day, 0.0) + float(tr['net_bps']) / n / SLOTS['XVHL']
    days = {day_of(ms(PARTS['TRAIN'][0]) + k * DAY_MS): 0.0 for k in range((ms(PARTS['FRESH'][1]) - ms(PARTS['TRAIN'][0])) // DAY_MS + 1)}
    days.update({d: v for d, v in per_day.items() if d in days})
    return {'days': days, 'rounds': rounds}


# ================================================================================================ run
def cell_name(theta, out_frac) -> str:
    return f'th{theta:g}|out{out_frac:g}'


def carry_cells(kind: str) -> list:
    return [(th, fr) for th in (M1_THETA if kind == 'M1' else M2_THETA) for fr in THETA_OUT_FRAC]


def summarize(res: dict, part: str, n_trials: int = 0) -> dict:
    return {'days': day_stats(daily(res['t'], res['step']), part, n_trials), 'rounds': round_stats(res['rounds'], part)}


def run_mechanism(mech: str, cells: list, runner, log=print, paths: int = PATHS, n_trials: int = N_TRIALS) -> dict:
    """TRAIN table of every cell, one HOLDOUT / FRESH read of the TRAIN-best cell, sensitivities, capital matrix."""
    table, results = {}, {}
    for name, args in cells:
        results[name] = runner(args, 'HEDGE')
        table[name] = summarize(results[name], 'TRAIN')
        d, r = table[name]['days'], table[name]['rounds']
        log(f"  {mech} TRAIN {name}: rounds {r.get('n', 0)} daily {d.get('mean', float('nan')):+.3f} bps net/round {r.get('net', float('nan')):+.1f}")
    out = {'train': table, 'cell': choose(table)}
    if not out['cell']:
        out['verdict'] = 'NO OPPORTUNITY SET'
        return out
    res, args = results[out['cell']], dict(cells)[out['cell']]
    for part in ('HOLDOUT', 'FRESH'):
        out[part.lower()] = summarize(res, part, n_trials)
    out['sensitivity'] = {}
    for k in SIDE_COST[base(mech)]:
        if k != 'HEDGE':
            alt = runner(args, k)
            out['sensitivity'][k] = {p: {key: day_stats(daily(alt['t'], alt['step']), p).get(key) for key in ('mean', 'se', 'lcb95')}
                                     for p in ('HOLDOUT', 'FRESH')}
    out.update(verdict(out['holdout']['days'], out['fresh']['days'], out['holdout']['rounds'].get('n', 0)))
    out['capital'] = capital_matrix(daily(res['t'], res['step']), mech, paths)
    out['capacity'] = capacity(out, mech)
    out['daily'] = daily(res['t'], res['step'])                         # the chosen cell, TRAIN .. FRESH, for the report
    out['rounds_list'] = res['rounds']
    h = out['holdout']['days']
    log(f"  {mech} HOLDOUT {out['cell']}: daily {h.get('mean', float('nan')):+.3f} +- {h.get('se', float('nan')):.3f} bps "
        f"(p {h.get('p_one_sided')}), DSR {h.get('dsr')} -> {out['verdict']}")
    return out


def run(log=print, src: Path = ODH, funding: Path = FUNDING, workers: int = 8, paths: int = PATHS, reference: bool = True) -> dict:
    m = load_market(src, funding)
    res = {'plan': PLAN, 'symbols': {'perps': int(len(m['sym'])), 'with_spot': int(np.isfinite(m['pb']).any(axis=1).sum()),
                                     'with_bybit': int(np.isfinite(m['pc']).any(axis=1).sum())}}
    for mech in ('M1', 'M2'):
        x = carry_inputs(m, mech)
        res[mech.lower() + '_tradable_share'] = float(x['ok'][:, m['t'] >= ms(PARTS['TRAIN'][0])].mean())
        cells = [(cell_name(th, fr), (th, th * fr)) for th, fr in carry_cells(mech)]
        res[mech.lower()] = run_mechanism(mech, cells, lambda a, cost, x=x, mech=mech: run_carry(x, a[0], a[1], SIDE_COST[mech][cost]),
                                          log, paths)
    idx = {s: i for i, s in enumerate(m['sym'])}
    coins = [c for c in PAIR_COINS if c + 'USDT' in idx]
    prices = {c: m['pa'][idx[c + 'USDT']] for c in coins}
    fund = {c: m['fa'][idx[c + 'USDT']] for c in coins}
    cells = [(f'bar{b}|z{z:g}', (b, z)) for b in PAIR_BARS for z in Z_ENTRY]
    res['m3'] = run_mechanism('M3', cells, lambda a, cost: run_pairs(prices, fund, m['t'], a[0], a[1], SIDE_COST['M3'][cost], workers=workers),
                              log, paths)
    res['m3']['coins'] = coins
    res['m3r'] = run_mechanism('M3R', [(f'eg|{name}', a) for name, a in cells],
                               lambda a, cost: run_pairs(prices, fund, m['t'], a[0], a[1], SIDE_COST['M3'][cost], workers=workers, method='eg'),
                               log, paths, n_trials=N_TRIALS_A1)
    res['m3r']['addendum'] = ADDENDUM
    lo, hi = ms(PARTS['HOLDOUT'][0]), ms(PARTS['HOLDOUT'][1]) + DAY_MS
    q = float(np.nanmedian(np.stack([trailing_sum(m['qa'][idx[c + 'USDT']], 6) for c in coins])[:, (m['t'] >= lo) & (m['t'] < hi)]))
    for key in ('m3', 'm3r'):
        if 'capital' in res[key]:
            res[key]['capacity'] = capacity(res[key], key.upper(), q)
    if reference:
        ref = reference_xvhl()
        res['xvhl'] = {p.lower(): {'days': day_stats(ref['days'], p), 'rounds': round_stats(ref['rounds'], p)} for p in PARTS}
        # the cache ends 2026-11-04 and a 28-day round must end inside it: FRESH is truncated, so C* uses HOLDOUT only
        res['xvhl']['capital'] = capital_matrix({d: v for d, v in ref['days'].items() if d <= PARTS['HOLDOUT'][1]}, 'XVHL', paths)
        res['xvhl']['capital_basis'] = 'HOLDOUT only (cache ends 2026-11-04; FRESH truncated)'
    return res


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('stage', choices=('fetch', 'run'))
    ap.add_argument('--workers', type=int, default=4)
    a = ap.parse_args(argv)
    if a.stage == 'fetch':
        print(fetch_all(workers=a.workers))
        return 0
    t0 = time.time()
    res = run(workers=max(a.workers, 8))
    res['runtime_s'] = time.time() - t0
    OUT.write_text(json.dumps(res, indent=1, default=float))
    print(' '.join(f"{k.upper()} {res[k].get('verdict')}" for k in ('m1', 'm2', 'm3', 'm3r')), f'-> {OUT} ({res["runtime_s"]:.0f} s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
