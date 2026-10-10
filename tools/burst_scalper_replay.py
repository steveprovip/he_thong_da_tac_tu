"""Sub-minute volatility-burst scalper + touch market making, replayed on real Binance USD-M aggTrades (10-10 request).

    python tools/burst_scalper_replay.py --agg <dir of SYMBOL-aggTrades-YYYY-MM-DD.zip> [--download]
        [--symbols SOLUSDT,DOGEUSDT,SOLUSDC,DOGEUSDC] [--start 2026-09-01] [--end 2026-10-09] [--json out.json]

PRE-REGISTERED (written before the first run; thresholds are the request's):
  burst second s  sigma_1m >= 15 bps (sqrt of the sum of squared 1 s log returns over 60 s)
                  OR |TFI_10s| > 0.85 (taker-flow imbalance (buy - sell) / (buy + sell) over 10 s, >= 20 prints;
                     the proxy for OFI L10: historical depth is not public)
                  OR 1-min volume > 3.0 x the mean 1-min volume of the previous 20 min (liquidation-cascade proxy:
                     forceOrder history is not public)
  signals         MOM  side = sign(CVD 10 s)       (ride the burst flow)
                  MR   side = -sign(price move 10 s) (fade the burst)
  entry           post-only at OUR touch (BUY at the bid, SELL at the ask) when flat in a burst second; re-peg every
                  4 s if the touch moved, as long as the burst lasts; cancel when it ends; latency 0.25 s on every
                  post / cancel (a cancelled order can still fill during the latency)
  exits           TP post-only at entry +/- TP; SL = TP (stop -> taker at the next print after the latency);
                  60 s time stop -> post-only at our touch for 10 s (re-peg 4 s) -> taker
  grid            {MOM, MR} x TP {15, 30} bps
  MM              touch market making (Avellaneda-Stoikov with the inventory term at its limit, i.e. quote both
                  touches when flat, only the reducing side when holding 1 lot), re-peg 1 s, 60 s inventory stop
                  -> taker; runs in every second, not only bursts
  fills           'through' (primary): a resting order fills only when a print trades THROUGH its price (we are at
                  the back of the queue); 'touch' (optimistic bound): a print AT our price fills it
  book            touch rebuilt from aggressor sides (sell aggressor prints on the bid, buy aggressor on the ask)
  fees            USDT VIP0: maker 2 / taker 5 bps.  USDC-M: maker 0 / taker 4 bps (a 0 % maker promotion that
                  third-party sources say ran to 2026-02; NOT confirmed for today -> scenario only)
  splits          DEV 2026-09-01..09-20 (choose), HOLDOUT 09-21..10-09 (report).  CI: bootstrap over hours.
Decision rule: the lane may trade the demo venue only if the DEV-best variant's HOLDOUT mean net bps/trade has a
95 % CI lower bound > 0 under the 'through' fill model; otherwise it stays in shadow.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import urllib.request
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip'
CFG = {'sig1m_bps': 15.0, 'tfi': 0.85, 'tfi_min_n': 20, 'surge': 3.0, 'lat_s': 0.25, 'repeg_s': 4.0,
       'hold_s': 60.0, 'exit_maker_s': 10.0, 'mm_repeg_s': 1.0}
FEES = {'USDT': (2.0, 5.0), 'USDC': (0.0, 4.0)}
INF = float('inf')
DEV = ('2026-09-01', '2026-09-20')
HOLD = ('2026-09-21', '2026-10-09')


# ------------------------------------------------------------------ data
def load_day(path: Path):
    """-> t (s), px, qty, bm (1 = buyer is maker = sell aggressor), sorted by time."""
    import pandas as pd
    with zipfile.ZipFile(path) as zf:
        name = zf.namelist()[0]
        with zf.open(name) as fh:
            df = pd.read_csv(fh, header=None, usecols=[1, 2, 5, 6], names=['px', 'q', 't', 'bm'],
                             dtype={'px': str, 'q': str, 't': str, 'bm': str})
    df = df[df['t'].str.isdigit()]
    t = df['t'].astype(np.int64).to_numpy()
    t = np.where(t > 10 ** 14, t / 1e6, t / 1e3)
    px = df['px'].astype(float).to_numpy()
    q = df['q'].astype(float).to_numpy()
    bm = (df['bm'].str.lower() == 'true').to_numpy().astype(np.int8)
    o = np.argsort(t, kind='stable')
    return t[o], px[o], q[o], bm[o]


def tick_of(px) -> float:
    u = np.unique(px[: min(len(px), 200_000)])
    d = np.diff(u)
    d = d[d > 0]
    tk = float(np.min(d)) if len(d) else 0.01
    return float(f'{tk:.10g}')


def touch_arrays(px, bm, tk):
    """Bid / ask after each print (Touch rule of tools/trend_execution_replay.py)."""
    n = len(px)
    bid, ask = np.empty(n), np.empty(n)
    b = a = None
    for i in range(n):
        p = px[i]
        if bm[i]:
            b = p
            if a is None or a <= p:
                a = p + tk
        else:
            a = p
            if b is None or b >= p:
                b = p - tk
        bid[i], ask[i] = b, a
    return bid, ask


def features(t, px, q, bm):
    """Per UTC second: burst flag, CVD 10 s, price move 10 s, sigma_1m, TFI 10 s, surge.  Index = second - s0."""
    sec = np.floor(t).astype(np.int64)
    s0 = sec[0]
    n = sec[-1] - s0 + 1
    idx = sec - s0
    last = np.full(n, np.nan)
    last[idx] = px                                     # the last print of each second wins (sorted input)
    last = _ffill(last)
    usd = px * q
    buy = np.bincount(idx, weights=usd * (1 - bm), minlength=n)
    sell = np.bincount(idx, weights=usd * bm, minlength=n)
    cnt = np.bincount(idx, minlength=n).astype(float)
    r = np.zeros(n)
    r[1:] = np.diff(np.log(last)) * 1e4
    sig1m = np.sqrt(_roll(r ** 2, 60))
    b10, s10, n10 = _roll(buy, 10), _roll(sell, 10), _roll(cnt, 10)
    tfi = np.where((n10 >= CFG['tfi_min_n']) & (b10 + s10 > 0), (b10 - s10) / np.maximum(b10 + s10, 1e-9), np.nan)
    v60 = _roll(buy + sell, 60)
    prev = np.full(n, np.nan)
    c = np.concatenate([[0.0], np.cumsum(buy + sell)])
    k = np.arange(n)
    ok = k >= 1260
    prev[ok] = (c[k[ok] - 59] - c[k[ok] - 1259]) / 20.0          # mean 1-min volume of the 20 min before
    surge = np.where(prev > 0, v60 / np.where(prev > 0, prev, 1.0), np.nan)
    dpx10 = np.full(n, np.nan)
    dpx10[10:] = last[10:] / last[:-10] - 1
    warm = k >= 60
    burst = warm & ((sig1m >= CFG['sig1m_bps']) | (np.nan_to_num(np.abs(tfi)) > CFG['tfi']) |
                    (np.nan_to_num(surge) > CFG['surge']))
    return {'s0': int(s0), 'burst': burst, 'cvd10': b10 - s10, 'dpx10': dpx10, 'sig1m': sig1m, 'tfi': tfi,
            'surge': surge}


def _ffill(x):
    ok = ~np.isnan(x)
    i = np.where(ok, np.arange(len(x)), 0)
    np.maximum.accumulate(i, out=i)
    out = x[i]
    first = np.argmax(ok)
    out[:first] = x[first]
    return out


def _roll(x, w):
    c = np.concatenate([[0.0], np.cumsum(x)])
    out = np.empty(len(x))
    k = np.arange(len(x))
    lo = np.maximum(k + 1 - w, 0)
    out[:] = c[k + 1] - c[lo]
    return out


# ------------------------------------------------------------------ simulation
def _hit(side_order: int, price: float, p: float, b: int, model: str) -> bool:
    """Does print (p, b) fill our resting order?  side_order +1 = our BUY (rests on the bid), -1 = our SELL."""
    if side_order > 0:
        return b == 1 and (p < price if model == 'through' else p <= price)
    return b == 0 and (p > price if model == 'through' else p >= price)


def simulate(day, policy: str, tp_bps: float, model: str, fees, lat: float = None):
    """One symbol-day.  policy MOM | MR | MM.  -> list of trades {t, side, net_bps, gross_bps, exit, hold_s}."""
    t, px, bm, bid, ask, f = day['t'], day['px'], day['bm'], day['bid'], day['ask'], day['f']
    tk = day['tick']
    lat = CFG['lat_s'] if lat is None else lat
    mk, tkf = fees
    n = len(t)
    s0, burst = f['s0'], f['burst']
    trades = []
    i = 0
    # state
    pos = 0                        # +1 long, -1 short, 0 flat
    entry = entry_t = 0.0
    orders = []                    # resting: dicts {side, px, active, kind}
    last_dec = -1
    exit_phase = None              # None | 'maker' | 'taker'
    exit_since = 0.0
    sl_px = None
    burst_secs = np.flatnonzero(burst) if policy != 'MM' else None
    while i < n:
        ti = t[i]
        sec = int(ti) - s0
        # ---------------- decisions once per second (at the first print of a new second)
        if sec != last_dec:
            last_dec = sec
            now = ti
            if pos == 0:
                orders = [o for o in orders if o['dead'] > now]
                want = 0
                if policy == 'MM':
                    want = 2
                elif sec - 1 >= 0 and burst[sec - 1]:
                    if policy == 'MOM':
                        want = int(np.sign(f['cvd10'][sec - 1]))
                    else:
                        d10 = f['dpx10'][sec - 1]
                        want = -int(np.sign(d10)) if not np.isnan(d10) else 0
                if want == 0:
                    for o in orders:
                        o['dead'] = min(o['dead'], now + lat)               # cancel: live for one more latency
                    if policy != 'MM' and not orders:                       # jump to the next burst second
                        j = np.searchsorted(burst_secs, sec)
                        if j >= len(burst_secs):
                            break
                        nxt = s0 + burst_secs[j] + 1
                        if nxt > ti + 1:
                            i = int(np.searchsorted(t, nxt))
                            continue
                else:
                    sides = (1, -1) if want == 2 else (want,)
                    repeg = CFG['mm_repeg_s'] if policy == 'MM' else CFG['repeg_s']
                    keep = []
                    for o in orders:
                        if o['dead'] < INF:
                            keep.append(o)                                   # already cancelled, still in flight
                        elif o['side'] in sides and (o['px'] == (bid[i - 1] if o['side'] > 0 else ask[i - 1])
                                                     or now - o['posted'] < repeg):
                            keep.append(o)
                        else:
                            o['dead'] = now + lat
                            keep.append(o)
                    live = {o['side'] for o in keep if o['dead'] == INF}
                    for sd in sides:
                        if sd not in live and i:
                            keep.append({'side': sd, 'px': bid[i - 1] if sd > 0 else ask[i - 1], 'active': now + lat,
                                         'dead': INF, 'posted': now, 'kind': 'E'})
                    orders = keep
            else:
                held = ti - entry_t
                if exit_phase is None and held >= CFG['hold_s']:
                    exit_phase, exit_since = 'maker', ti
                    orders = []
                if exit_phase == 'maker':
                    if ti - exit_since >= CFG['exit_maker_s']:
                        exit_phase = 'taker'
                        orders = []
                    else:
                        p_want = ask[i - 1] if pos > 0 else bid[i - 1]
                        o = orders[0] if orders else None
                        if not o or (o['px'] != p_want and ti - o['posted'] >= CFG['repeg_s']):
                            orders = [{'side': -pos, 'px': p_want, 'active': ti + lat, 'dead': INF, 'posted': ti,
                                       'kind': 'X'}]
        # ---------------- this print against resting orders / stops
        p, b = px[i], bm[i]
        if pos == 0:
            for o in orders:
                if o['active'] <= ti < o['dead'] and _hit(o['side'], o['px'], p, b, model):
                    pos, entry, entry_t = o['side'], o['px'], ti
                    exit_phase = None
                    if policy == 'MM':
                        tp_px = None
                        orders = []
                    else:
                        raw = entry * (1 + pos * tp_bps / 1e4)
                        tp_px = (math.ceil(raw / tk) if pos > 0 else math.floor(raw / tk)) * tk
                        orders = [{'side': -pos, 'px': tp_px, 'active': ti + lat, 'dead': INF, 'posted': ti,
                                   'kind': 'TP'}]
                    sl_px = entry * (1 - pos * tp_bps / 1e4) if policy != 'MM' else None
                    break
            i += 1
            continue
        # in position
        if policy == 'MM' and exit_phase is None:
            p_want = ask[i - 1] if pos > 0 else bid[i - 1]
            o = orders[0] if orders else None
            if not o or (o['px'] != p_want and ti - o['posted'] >= CFG['mm_repeg_s']):
                orders = [{'side': -pos, 'px': p_want, 'active': ti + lat, 'dead': INF, 'posted': ti, 'kind': 'X'}]
        out_px, how = None, None
        for o in orders:
            if o['active'] <= ti < o['dead'] and _hit(o['side'], o['px'], p, b, model):
                out_px, how = o['px'], 'TP' if o['kind'] == 'TP' else 'MAKER-EXIT'
                break
        if out_px is None and sl_px is not None and ((pos > 0 and p <= sl_px) or (pos < 0 and p >= sl_px)):
            j = int(np.searchsorted(t, ti + lat))
            if j < n:
                out_px, how, i = px[j], 'SL', j
        if out_px is None and exit_phase == 'taker':
            j = int(np.searchsorted(t, ti + lat))
            if j < n:
                out_px, how, i = px[j], 'TIME-TAKER', j
        if out_px is not None:
            fee_x = tkf if how in ('SL', 'TIME-TAKER') else mk
            gross = pos * (out_px - entry) / entry * 1e4
            trades.append({'t': entry_t, 'side': pos, 'gross_bps': gross, 'net_bps': gross - mk - fee_x, 'exit': how,
                           'taker_frac': 1.0 if how in ('SL', 'TIME-TAKER') else 0.0,
                           'hold_s': t[i] - entry_t})
            pos, orders, exit_phase = 0, [], None
        i += 1
    return trades


def prep(path: Path):
    t, px, q, bm = load_day(path)
    tk = tick_of(px)
    bid, ask = touch_arrays(px, bm, tk)
    return {'t': t, 'px': px, 'bm': bm, 'bid': bid, 'ask': ask, 'tick': tk, 'f': features(t, px, q, bm)}


VARIANTS = [(p, tp) for p in ('MOM', 'MR') for tp in (15.0, 30.0)] + [('MM', 0.0)]


def run_day(args):
    path, sym, d = args
    day = prep(path)
    fees = FEES['USDC' if sym.endswith('USDC') else 'USDT']
    out = {'symbol': sym, 'day': d, 'burst_share': float(day['f']['burst'].mean()), 'tick': day['tick'],
           'mid_bps_tick': float(day['tick'] / np.median(day['px']) * 1e4), 'res': {}}
    for pol, tp in VARIANTS:
        for model in ('through', 'touch'):
            out['res'][f'{pol}{int(tp) or ""}|{model}'] = simulate(day, pol, tp, model, fees)
    for pol, tp in VARIANTS[:4]:                       # latency sensitivity, primary fill model
        out['res'][f'{pol}{int(tp)}|through|lat0.5'] = simulate(day, pol, tp, 'through', fees, lat=0.5)
    return out


def summarize(rows, hours_total):
    if not rows:
        return {'n': 0}
    x = np.array([r['net_bps'] for r in rows])
    hr = np.array([int(r['t'] // 3600) for r in rows])
    uh = np.unique(hr)
    sums = {h: x[hr == h].sum() for h in uh}
    cnts = {h: (hr == h).sum() for h in uh}
    rng = np.random.default_rng(7)
    boots = []
    for _ in range(2000):
        pick = rng.choice(uh, len(uh))
        boots.append(sum(sums[h] for h in pick) / max(1, sum(cnts[h] for h in pick)))
    ex = {}
    for r in rows:
        ex[r['exit']] = ex.get(r['exit'], 0) + 1
    return {'n': len(x), 'mean_net_bps': float(x.mean()), 'ci95': [float(np.percentile(boots, 2.5)),
                                                                   float(np.percentile(boots, 97.5))],
            'median_net_bps': float(np.median(x)), 'win_rate': float((x > 0).mean()),
            'gross_bps': float(np.mean([r['gross_bps'] for r in rows])), 'trades_per_hour': len(x) / hours_total,
            'exits': ex, 'median_hold_s': float(np.median([r['hold_s'] for r in rows]))}


def daterange(a, b):
    d0, d1 = date.fromisoformat(a), date.fromisoformat(b)
    return [str(d0 + timedelta(k)) for k in range((d1 - d0).days + 1)]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--agg', type=Path, required=True)
    ap.add_argument('--download', action='store_true')
    ap.add_argument('--symbols', default='SOLUSDT,DOGEUSDT,SOLUSDC,DOGEUSDC')
    ap.add_argument('--start', default=DEV[0])
    ap.add_argument('--end', default=HOLD[1])
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--json', type=Path)
    ap.add_argument('--write-verdict', action='store_true', help='write config/burst_verdict.json (gate of the '
                    'active burst lane, core/burst_lane.py)')
    a = ap.parse_args(argv)
    jobs = []
    for s in a.symbols.split(','):
        for d in daterange(a.start, a.end):
            p = a.agg / f'{s}-aggTrades-{d}.zip'
            if not p.exists() and a.download:
                a.agg.mkdir(parents=True, exist_ok=True)
                with urllib.request.urlopen(URL.format(s=s, d=d), timeout=120) as r:
                    p.write_bytes(r.read())
            if p.exists():
                jobs.append((p, s, d))
    t0 = time.perf_counter()
    with ProcessPoolExecutor(a.workers) as ex:
        days = list(ex.map(run_day, jobs))
    out = {'cfg': CFG, 'fees': FEES, 'splits': {'DEV': DEV, 'HOLDOUT': HOLD}, 'seconds': time.perf_counter() - t0,
           'symbols': {}}
    for s in a.symbols.split(','):
        ds = [x for x in days if x['symbol'] == s]
        if not ds:
            continue
        o = out['symbols'][s] = {'days': len(ds), 'burst_share': float(np.mean([x['burst_share'] for x in ds])),
                                 'tick_bps': float(np.mean([x['mid_bps_tick'] for x in ds])), 'splits': {}}
        for split, (lo, hi) in (('DEV', DEV), ('HOLDOUT', HOLD)):
            dd = [x for x in ds if lo <= x['day'] <= hi]
            if not dd:
                continue
            hrs = 24.0 * len(dd)
            burst_hrs = sum(x['burst_share'] for x in dd) * 24.0
            o['splits'][split] = {'days': len(dd), 'burst_hours': burst_hrs, 'variants': {}}
            for k in dd[0]['res']:
                rows = [r for x in dd for r in x['res'][k]]
                sm = summarize(rows, hrs)
                if sm.get('n'):
                    sm['trades_per_burst_hour'] = sm['n'] / burst_hrs if burst_hrs else None
                o['splits'][split]['variants'][k] = sm
        dev = o['splits'].get('DEV', {}).get('variants', {})
        cands = [k for k in dev if k.endswith('|through') and not k.startswith('MM') and dev[k].get('n', 0) >= 30]
        best = max(cands, key=lambda k: dev[k]['mean_net_bps']) if cands else None
        ho = o['splits'].get('HOLDOUT', {}).get('variants', {}).get(best, {}) if best else {}
        o['dev_best'] = best
        o['verdict'] = ('ACTIVE-ELIGIBLE' if ho.get('n') and ho['ci95'][0] > 0 else 'SHADOW')
    print(f'burst replay | {len(jobs)} symbol-days | {out["seconds"]:.0f}s')
    for s, o in out['symbols'].items():
        print(f'\n{s}: days {o["days"]} burst share {o["burst_share"]:.1%} tick {o["tick_bps"]:.2f} bps | DEV-best '
              f'{o["dev_best"]} -> {o["verdict"]}')
        for split, sp in o['splits'].items():
            print(f'  {split} ({sp["days"]} d, burst {sp["burst_hours"]:.0f} h)')
            for k, v in sp['variants'].items():
                if not v.get('n'):
                    print(f'    {k:<24} n 0')
                    continue
                print(f'    {k:<24} n {v["n"]:6d} ({v["trades_per_hour"]:5.1f}/h, {v["trades_per_burst_hour"] or 0:5.1f}/burst-h) '
                      f'net {v["mean_net_bps"]:+6.2f} [{v["ci95"][0]:+6.2f}, {v["ci95"][1]:+6.2f}] gross {v["gross_bps"]:+6.2f} '
                      f'win {v["win_rate"]:.0%} hold~{v["median_hold_s"]:.0f}s {v["exits"]}')
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(out, indent=1, default=float))
    if a.write_verdict:
        write_verdict(out, ROOT / 'config' / 'burst_verdict.json')
    return 0


def write_verdict(out: dict, path: Path) -> dict:
    """{symbol: {verdict, variant, holdout mean / CI, generated}} for the active-lane gate."""
    v = {}
    for s, o in out['symbols'].items():
        best = o.get('dev_best')
        ho = o['splits'].get('HOLDOUT', {}).get('variants', {}).get(best, {}) if best else {}
        v[s] = {'verdict': o.get('verdict', 'SHADOW'), 'variant': best, 'holdout_mean_net_bps': ho.get('mean_net_bps'),
                'holdout_ci95': ho.get('ci95'), 'holdout_n': ho.get('n'),
                'generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                'rule': 'ACTIVE-ELIGIBLE iff the DEV-best variant has HOLDOUT CI95 lower bound > 0 (through fills)'}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(v, indent=1, default=float), encoding='utf-8')
    return v


if __name__ == '__main__':
    raise SystemExit(main())
