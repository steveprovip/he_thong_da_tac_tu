"""Intraday 5m / 15m micro-swing replay (request 10-10 b) on real Binance USD-M 1-minute klines.  Offline.

    python tools/intraday_replay.py --data <dir of SYMBOL-1m-YYYY-MM.zip> [--symbols BTCUSDT,SOLUSDT,DOGEUSDT]
                                    [--json out.json] [--write-verdict]

Everything is simulated at 1-minute resolution; 5m / 15m bars are complete clock-aligned groups of 1m bars; every
decision uses CLOSED bars only and acts from the NEXT minute.

PRE-REGISTERED (written before the first run; thresholds = the request's, windows = textbook defaults):
  common     EMA20 of 5m closes; slope1 = % change of EMA20 over 1 5m bar, slope3 = over 3 bars (15 min);
             ATR14 (Wilder) on 5m; ADX14 (Wilder) on 15m bars (last COMPLETED 15m bar at the decision time);
             CVD 60 s = taker-buy minus taker-sell quote volume of the last 1m bar.
  VWAP       ROLL100: volume-weighted typical price over the last 100 one-minute bars (20 x 5m), sigma = volume-
             weighted std of the 1m typical price around it;  SESSION: same, anchored at 00:00 UTC (>= 60 min in).
             (The engine's vwap_5m is a 5-MINUTE rolling VWAP: its 2-sigma band is ~5-15 bps wide, which cannot
             give the request's 35-50 bps target - so the bands are computed over 100 min / the session.)
  S1 VWAP-band mean reversion, evaluated at each 5m close:
             BUY  if the 5m low <= VWAP - 2 sigma AND CVD 60 s > 0 (sellers exhausted) AND |slope1| < 0.08 %
             SELL if the 5m high >= VWAP + 2 sigma AND CVD 60 s < 0 AND |slope1| < 0.08 %
             entry = post-only at the 5m close, live for the next minute only (one re-peg window, no chase);
             TP = post-only at the VWAP of the signal bar;  SL = 1.0 x ATR(5m) (stop, taker);
             skip unless TP distance >= 1.5 x SL distance (R:R >= 1.5).  Variants: VWAP ROLL100 | SESSION.
  S2 15m momentum continuation:
             UP   if slope >= +0.15 % AND ADX(15m) > 25 AND close > EMA20 (DOWN mirrored); slope3 (primary,
                  "15m confirms") | slope1 (literal per-5m-bar reading)
             entry = post-only at EMA20 (the pull-back zone), re-anchored at each 5m close while the trend
                  condition holds, cancelled at the first 5m close where it fails;
             TP = entry +/- 60 | 100 bps (post-only);  SL = the extreme of the last 2 closed 5m bars (if within
                  5 bps of entry: 1 ATR), trailed to each new closed 5m bar's low / high (never loosened).
  both       one position per symbol per strategy; 45 min time barrier -> taker at that minute's close;
             a resting order fills only when a later 1m bar trades THROUGH it (low < buy limit, high > sell limit);
             inside one 1m bar the stop is assumed hit BEFORE the target (conservative); in the fill minute itself
             only the stop counts (the high may have printed before the fill); a gap through the stop fills at
             the open.  Fees VIP0: maker 2 bps, taker 5 + 1 bp slippage.
  splits     DEV 2025-01-01..2025-12-31 (choose the variant per strategy, pooled symbols),
             HOLDOUT 2026-01-01..2026-09-30 (report).  CI: bootstrap over days.
  verdict    a (strategy, symbol) may trade the demo venue only if its HOLDOUT mean net bps/trade has a 95 % CI
             lower bound > 0 AND its win rate > 50 % (the user's two conditions); otherwise SHADOW.
  money      fixed $150 per trade (3 % of a $5 000 wallet); Sharpe from daily P&L / $5 000, x sqrt(365).
"""
from __future__ import annotations

import argparse
import io
import json
import math
import sys
import time
import zipfile
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
MAKER, TAKER = 2.0, 6.0
NOTIONAL, EQUITY = 150.0, 5000.0
DEV = ('2025-01-01', '2025-12-31')
HOLD = ('2026-01-01', '2026-09-30')
VARIANTS = {
    'S1_ROLL100': {'kind': 'S1', 'vwap': 'ROLL100'},
    'S1_SESSION': {'kind': 'S1', 'vwap': 'SESSION'},
    'S2_slope3_tp60': {'kind': 'S2', 'slope': 3, 'tp': 60.0},
    'S2_slope3_tp100': {'kind': 'S2', 'slope': 3, 'tp': 100.0},
    'S2_slope1_tp60': {'kind': 'S2', 'slope': 1, 'tp': 60.0},
    'S2_slope1_tp100': {'kind': 'S2', 'slope': 1, 'tp': 100.0},
}
P = {'band_k': 2.0, 'mr_slope_max': 0.08, 'rr_min': 1.5, 'sl_atr': 1.0, 'trend_slope': 0.15, 'adx_min': 25.0,
     'hold_min': 45, 'pull_bars': 3, 'sl_min_bps': 5.0, 'vwap_roll': 100, 'session_min': 60}


# ------------------------------------------------------------------ data
def load_1m(data: Path, symbol: str):
    """-> dict of numpy arrays t (open, s), o, h, l, c, v, qv, tbq; sorted, de-duplicated."""
    import pandas as pd
    frames = []
    for z in sorted(data.glob(f'{symbol}-1m-*.zip')):
        with zipfile.ZipFile(z) as zf:
            raw = zf.read(zf.namelist()[0]).decode()
        df = pd.read_csv(io.StringIO(raw), header=None)
        df = df[pd.to_numeric(df[0], errors='coerce').notna()]
        frames.append(df.iloc[:, [0, 1, 2, 3, 4, 5, 7, 10]].astype(float))
    df = pd.concat(frames).drop_duplicates(0).sort_values(0)
    a = df.to_numpy()
    return {'t': a[:, 0] / 1000.0, 'o': a[:, 1], 'h': a[:, 2], 'l': a[:, 3], 'c': a[:, 4], 'v': a[:, 5],
            'qv': a[:, 6], 'tbq': a[:, 7]}


def resample(m: dict, span: int):
    """Complete clock-aligned bars of `span` seconds from 1m bars -> dict (t = open, t_close = open + span)."""
    k = (m['t'] // span).astype(np.int64)
    starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
    ends = np.r_[starts[1:], len(k)]
    full = (ends - starts) == span // 60
    s, e = starts[full], ends[full]
    return {'t': k[s] * float(span), 't_close': k[s] * float(span) + span, 'o': m['o'][s],
            'h': np.maximum.reduceat(m['h'], starts)[full], 'l': np.minimum.reduceat(m['l'], starts)[full],
            'c': m['c'][e - 1], 'i_last': e - 1}


def ema(x, n):
    out = np.empty(len(x))
    a = 2.0 / (n + 1)
    acc = x[0]
    for i, v in enumerate(x):
        acc = v if i == 0 else acc + a * (v - acc)
        out[i] = acc
    return out


def wilder_atr(h, l, c, n=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))
    out = np.full(len(c), np.nan)
    if len(c) < n:
        return out
    a = tr[:n].mean()
    out[n - 1] = a
    for i in range(n, len(c)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def wilder_adx(h, l, c, n=14):
    up, dn = h[1:] - h[:-1], l[:-1] - l[1:]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    out = np.full(len(c), np.nan)
    if len(tr) < 2 * n:
        return out
    a, p, q = tr[:n].sum(), pdm[:n].sum(), ndm[:n].sum()
    dx = []
    for k in range(n - 1, len(tr)):
        if k >= n:
            a, p, q = a - a / n + tr[k], p - p / n + pdm[k], q - q / n + ndm[k]
        pdi, ndi = (100 * p / a, 100 * q / a) if a else (0.0, 0.0)
        dx.append(100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0.0)
    adx = sum(dx[:n]) / n
    out[2 * n - 1] = adx
    for j in range(n, len(dx)):
        adx = (adx * (n - 1) + dx[j]) / n
        out[j + n] = adx
    return out


def vwap_bands(m: dict, kind: str):
    """Per 1m bar (value known at that bar's CLOSE): VWAP and sigma, ROLL100 or SESSION."""
    tp = (m['h'] + m['l'] + m['c']) / 3.0
    v = m['v']
    if kind == 'ROLL100':
        n = P['vwap_roll']
        cv, cpv, cp2v = (np.r_[0.0, np.cumsum(x)] for x in (v, tp * v, tp * tp * v))
        i = np.arange(len(v))
        lo = np.maximum(i + 1 - n, 0)
        sv, spv, sp2v = cv[i + 1] - cv[lo], cpv[i + 1] - cpv[lo], cp2v[i + 1] - cp2v[lo]
        ok = (i + 1 >= n) & (sv > 0)
    else:
        day = (m['t'] // 86400).astype(np.int64)
        start = np.r_[True, day[1:] != day[:-1]]
        grp = np.cumsum(start) - 1
        sv, spv, sp2v = (np.zeros(len(v)) for _ in range(3))
        for arr, src in ((sv, v), (spv, tp * v), (sp2v, tp * tp * v)):
            cs = np.cumsum(src)
            base = np.r_[0.0, cs][np.flatnonzero(start)][grp]
            arr[:] = cs - base
        mins = np.arange(len(v)) - np.flatnonzero(start)[grp] + 1
        ok = (mins >= P['session_min']) & (sv > 0)
    vw = np.where(ok, spv / np.where(sv > 0, sv, 1.0), np.nan)
    var = np.where(ok, sp2v / np.where(sv > 0, sv, 1.0) - vw * vw, np.nan)
    return vw, np.sqrt(np.maximum(var, 0.0))


# ------------------------------------------------------------------ features at the 5m closes
def features(m: dict) -> dict:
    b5 = resample(m, 300)
    b15 = resample(m, 900)
    e20 = ema(b5['c'], 20)
    slope1 = np.r_[np.nan, (e20[1:] / e20[:-1] - 1) * 100]
    slope3 = np.r_[np.full(3, np.nan), (e20[3:] / e20[:-3] - 1) * 100]
    atr5 = wilder_atr(b5['h'], b5['l'], b5['c'])
    adx15 = wilder_adx(b15['h'], b15['l'], b15['c'])
    j = np.searchsorted(b15['t_close'], b5['t_close'], side='right') - 1          # last 15m bar CLOSED by then
    adx_at = np.where(j >= 0, adx15[np.maximum(j, 0)], np.nan)
    il = b5['i_last']
    cvd60 = 2 * m['tbq'][il] - m['qv'][il]
    out = {'b5': b5, 'ema20': e20, 'slope1': slope1, 'slope3': slope3, 'atr5': atr5, 'adx15': adx_at,
           'cvd60': cvd60}
    for kind in ('ROLL100', 'SESSION'):
        vw, sd = vwap_bands(m, kind)
        out[f'vwap_{kind}'], out[f'sd_{kind}'] = vw[il], sd[il]
    return out


# ------------------------------------------------------------------ simulation
def _exit_scan(m, i0, side, entry, tp, sl, trail=None, hold=45):
    """From 1m bar i0 on (the bar AFTER the fill): stop first, then target, then the time barrier.
    trail(i) -> new stop at the close of 5m bars (S2).  -> (exit px, how, i_exit, maker?)"""
    n = len(m['t'])
    t_end = m['t'][i0] + hold * 60
    i = i0
    while i < n:
        o, h, l, c, t = m['o'][i], m['h'][i], m['l'][i], m['c'][i], m['t'][i]
        first = i == i0                                   # the fill minute: its high/low may predate the fill
        if side > 0:
            if l <= sl:
                return (sl if first else min(o, sl)), 'SL', i, False
            if h > tp and not first:
                return tp, 'TP', i, True
        else:
            if h >= sl:
                return (sl if first else max(o, sl)), 'SL', i, False
            if l < tp and not first:
                return tp, 'TP', i, True
        if t + 60 >= t_end:
            return c, 'TIME', i, False
        if trail is not None:
            sl = trail(i, sl)
        i += 1
    return m['c'][n - 1], 'END', n - 1, False


def run_symbol(m: dict, f: dict, var: dict):
    b5, n5 = f['b5'], len(f['b5']['t'])
    t1 = m['t']
    trades = []
    busy_until = -1.0
    closes = set(b5['i_last'].tolist())
    k = 30
    while k < n5:
        tc = b5['t_close'][k]
        if tc <= busy_until:
            k += 1
            continue
        i_next = b5['i_last'][k] + 1                     # first 1m bar after the 5m close
        if i_next >= len(t1) or t1[i_next] != tc:
            k += 1
            continue
        c, atr = b5['c'][k], f['atr5'][k]
        if not np.isfinite(atr) or atr <= 0:
            k += 1
            continue
        if var['kind'] == 'S1':
            vw, sd = f[f'vwap_{var["vwap"]}'][k], f[f'sd_{var["vwap"]}'][k]
            sl1 = f['slope1'][k]
            if not (np.isfinite(vw) and np.isfinite(sd) and sd > 0 and np.isfinite(sl1)) or abs(sl1) >= P['mr_slope_max']:
                k += 1
                continue
            side = 0
            if b5['l'][k] <= vw - P['band_k'] * sd and f['cvd60'][k] > 0 and c < vw:
                side = 1
            elif b5['h'][k] >= vw + P['band_k'] * sd and f['cvd60'][k] < 0 and c > vw:
                side = -1
            if not side:
                k += 1
                continue
            entry, tp = c, vw
            sl = entry - side * P['sl_atr'] * atr
            if side * (tp - entry) < P['rr_min'] * side * (entry - sl):
                k += 1
                continue
            i = i_next                                    # one minute to get filled through
            filled = (m['l'][i] < entry) if side > 0 else (m['h'][i] > entry)
            if not filled:
                k += 1
                continue
            # a fill late in the bar can be followed by the stop inside the same minute: check it on this bar too
            px, how, ix, mk = _exit_scan(m, i, side, entry, tp, sl)
            fill_i = i
        else:
            sl_key = 'slope3' if var['slope'] == 3 else 'slope1'
            fill_i, side, entry = None, 0, None
            kk = k
            while kk < n5:                                # re-anchored at each 5m close while the trend holds
                s_, adx, e20, cc = f[sl_key][kk], f['adx15'][kk], f['ema20'][kk], b5['c'][kk]
                up = np.isfinite(s_) and np.isfinite(adx) and s_ >= P['trend_slope'] and adx > P['adx_min'] and cc > e20
                dn = np.isfinite(s_) and np.isfinite(adx) and s_ <= -P['trend_slope'] and adx > P['adx_min'] and cc < e20
                sd = 1 if up else (-1 if dn else 0)
                if not sd or (side and sd != side):
                    break
                side, lim = sd, e20
                a, z = b5['i_last'][kk] + 1, b5['i_last'][kk] + 6           # the 5 minutes until the next 5m close
                for i in range(a, min(z, len(t1))):
                    if (side > 0 and m['l'][i] < lim) or (side < 0 and m['h'][i] > lim):
                        fill_i, entry = i, lim
                        break
                if fill_i is not None:
                    break
                kk += 1
            if fill_i is None:                           # the trend failed at kk: kk itself may start a new signal
                k = kk if kk > k else k + 1
                continue
            last_k = int(np.searchsorted(b5['t_close'], t1[fill_i], side='right')) - 1   # last 5m bar closed
            lows = b5['l'][max(0, last_k - 1):last_k + 1]
            highs = b5['h'][max(0, last_k - 1):last_k + 1]
            sl = lows.min() if side > 0 else highs.max()
            if side * (entry - sl) / entry * 1e4 < P['sl_min_bps']:
                sl = entry - side * P['sl_atr'] * f['atr5'][last_k]
            tp = entry * (1 + side * var['tp'] / 1e4)

            def trail(i, s, side=side):
                if i in closes:
                    kk2 = int(np.searchsorted(b5['i_last'], i))
                    nb = b5['l'][kk2] if side > 0 else b5['h'][kk2]
                    return max(s, nb) if side > 0 else min(s, nb)
                return s
            px, how, ix, mk = _exit_scan(m, fill_i, side, entry, tp, sl, trail)
            k = int(np.searchsorted(b5['t_close'], t1[fill_i], side='right')) - 1
        gross = side * (px - entry) / entry * 1e4
        net = gross - MAKER - (MAKER if mk else TAKER)
        trades.append({'t': float(t1[ix]), 't_entry': float(t1[fill_i]), 'side': side, 'entry': float(entry),
                       'exit_px': float(px), 'gross_bps': gross, 'net_bps': net, 'exit': how,
                       'hold_min': (t1[ix] - t1[fill_i]) / 60 + 1})
        busy_until = t1[ix] + 60
        k += 1
    return trades


def day_of(s):
    return int(datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp()) // 86400


def summarize(trades, lo, hi):
    tr = [x for x in trades if lo <= int(x['t'] // 86400) <= hi]
    days = hi - lo + 1
    if not tr:
        return {'n': 0}
    x = np.array([r['net_bps'] for r in tr])
    d = np.array([int(r['t'] // 86400) for r in tr])
    ud = np.unique(d)
    sums = {u: x[d == u].sum() for u in ud}
    cnt = {u: int((d == u).sum()) for u in ud}
    rng = np.random.default_rng(7)
    boots = []
    for _ in range(2000):
        pick = rng.choice(ud, len(ud))
        boots.append(sum(sums[u] for u in pick) / max(1, sum(cnt[u] for u in pick)))
    daily = np.zeros(days)
    for r in tr:
        daily[int(r['t'] // 86400) - lo] += r['net_bps'] / 1e4 * NOTIONAL / EQUITY
    eq = np.cumsum(daily)
    ex = {}
    for r in tr:
        ex[r['exit']] = ex.get(r['exit'], 0) + 1
    return {'n': len(x), 'per_day': len(x) / days, 'win_rate': float((x > 0).mean()), 'mean_net_bps': float(x.mean()),
            'ci95': [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            'median_net_bps': float(np.median(x)), 'gross_bps': float(np.mean([r['gross_bps'] for r in tr])),
            'avg_win_bps': float(x[x > 0].mean()) if (x > 0).any() else None,
            'avg_loss_bps': float(x[x <= 0].mean()) if (x <= 0).any() else None,
            'usd_per_day': float(x.sum() / 1e4 * NOTIONAL / days),
            'sharpe': float(daily.mean() / daily.std(ddof=1) * math.sqrt(365)) if daily.std() > 0 else 0.0,
            'max_dd_usd': float((np.maximum.accumulate(eq) - eq).max() * EQUITY), 'exits': ex,
            'median_hold_min': float(np.median([r['hold_min'] for r in tr]))}


def run_one(args):
    data, sym = args
    m = load_1m(Path(data), sym)
    f = features(m)
    return sym, {k: run_symbol(m, f, v) for k, v in VARIANTS.items()}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--data', type=Path, required=True)
    ap.add_argument('--symbols', default='BTCUSDT,SOLUSDT,DOGEUSDT')
    ap.add_argument('--json', type=Path)
    ap.add_argument('--write-verdict', action='store_true')
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    syms = a.symbols.split(',')
    with ProcessPoolExecutor(min(3, len(syms))) as ex:
        res = dict(ex.map(run_one, [(str(a.data), s) for s in syms]))
    lo_d, hi_d = day_of(DEV[0]), day_of(DEV[1])
    lo_h, hi_h = day_of(HOLD[0]), day_of(HOLD[1])
    out = {'params': P, 'variants': VARIANTS, 'fees': {'maker': MAKER, 'taker_slip': TAKER}, 'splits': {'DEV': DEV, 'HOLDOUT': HOLD},
           'per_symbol': {}, 'pooled': {}, 'seconds': None}
    for s in syms:
        out['per_symbol'][s] = {v: {'DEV': summarize(res[s][v], lo_d, hi_d), 'HOLDOUT': summarize(res[s][v], lo_h, hi_h)}
                                for v in VARIANTS}
    for v in VARIANTS:
        allt = [x for s in syms for x in res[s][v]]
        out['pooled'][v] = {'DEV': summarize(allt, lo_d, hi_d), 'HOLDOUT': summarize(allt, lo_h, hi_h)}
    best = {}
    for kind in ('S1', 'S2'):
        cands = [v for v in VARIANTS if VARIANTS[v]['kind'] == kind and out['pooled'][v]['DEV'].get('n', 0) >= 30]
        best[kind] = max(cands, key=lambda v: out['pooled'][v]['DEV']['mean_net_bps']) if cands else None
    out['dev_best'] = best
    verdict = {}
    for s in syms:
        for kind, v in best.items():
            h = out['per_symbol'][s][v]['HOLDOUT'] if v else {}
            ok = bool(h.get('n')) and h['ci95'][0] > 0 and h['win_rate'] > 0.5
            verdict.setdefault(s, {})[kind] = {'variant': v, 'verdict': 'ACTIVE-ELIGIBLE' if ok else 'SHADOW',
                                               'holdout_mean_net_bps': h.get('mean_net_bps'), 'holdout_ci95': h.get('ci95'),
                                               'holdout_win_rate': h.get('win_rate'), 'holdout_n': h.get('n')}
    out['verdict'] = verdict
    out['seconds'] = time.perf_counter() - t0

    def fmt(x):
        if not x.get('n'):
            return 'n 0'
        return (f"n {x['n']:5d} ({x['per_day']:4.1f}/d) win {x['win_rate']:.0%} net {x['mean_net_bps']:+6.1f} "
                f"[{x['ci95'][0]:+6.1f}, {x['ci95'][1]:+6.1f}] gross {x['gross_bps']:+6.1f} SR {x['sharpe']:+5.2f} "
                f"${x['usd_per_day']:+6.2f}/d DD ${x['max_dd_usd']:.0f} hold~{x['median_hold_min']:.0f}m {x['exits']}")
    print(f'intraday replay {",".join(syms)} | {out["seconds"]:.0f}s | DEV-best {best}')
    for v in VARIANTS:
        print(f'\n{v}  POOLED')
        for sp in ('DEV', 'HOLDOUT'):
            print(f'   {sp:<8}{fmt(out["pooled"][v][sp])}')
        for s in syms:
            print(f'   {s:<9}DEV {fmt(out["per_symbol"][s][v]["DEV"])}')
            print(f'   {"":<9}HO  {fmt(out["per_symbol"][s][v]["HOLDOUT"])}')
    print('\nverdict', json.dumps(verdict, default=float))
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(out, indent=1, default=float))
    if a.write_verdict:
        (ROOT / 'config').mkdir(exist_ok=True)
        (ROOT / 'config' / 'intraday_verdict.json').write_text(json.dumps(
            {**verdict, '_generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
             '_rule': 'ACTIVE-ELIGIBLE iff HOLDOUT CI95 lower > 0 and win rate > 50 % (DEV-best variant)'},
            indent=1, default=float), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
