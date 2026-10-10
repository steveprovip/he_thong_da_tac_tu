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
  both       one position per symbol per strategy (no new entry at a 5m close whose last minute closed the previous
             position - the same rule as the live lane); 45 min time barrier -> taker at that minute's close;
             a resting order fills only when a later 1m bar trades THROUGH it (low < buy limit, high > sell limit);
             inside one 1m bar the stop is assumed hit BEFORE the target (conservative); in the fill minute itself
             only the stop counts (the high may have printed before the fill); a gap through the stop fills at
             the open.  Fees VIP0: maker 2 bps, taker 5 + 1 bp slippage.
  splits     DEV 2025-01-01..2025-12-31 (choose the variant per strategy, pooled symbols),
             HOLDOUT 2026-01-01..2026-09-30 (report).  CI: bootstrap over days.
  verdict    a (strategy, symbol) may trade the demo venue only if its HOLDOUT mean net bps/trade has a 95 % CI
             lower bound > 0 AND its win rate > 50 % (the user's two conditions) AND it has >= 100 HOLDOUT trades
             (floor added after the stats audit; it changes no current verdict); otherwise SHADOW.
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
sys.path.insert(0, str(ROOT))
from core.intraday_lane import P, VARIANTS, features, resample  # noqa: E402,F401  (resample re-exported)
from core.intraday_lane import s1_signal, s2_stop, s2_trend  # noqa: E402

MAKER, TAKER = 2.0, 6.0
MIN_HOLDOUT_N = 100                                       # per (symbol, strategy): no verdict from a handful of trades
NOTIONAL, EQUITY = 150.0, 5000.0
DEV = ('2025-01-01', '2025-12-31')
HOLD = ('2026-01-01', '2026-09-30')
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
        atr = f['atr5'][k]
        if not np.isfinite(atr) or atr <= 0:
            k += 1
            continue
        if var['kind'] == 'S1':
            sig = s1_signal(f, k, var['vwap'])                # shared with core/intraday_lane.py
            if not sig:
                k += 1
                continue
            side, entry, tp, sl = sig
            i = i_next                                    # one minute to get filled through
            filled = (m['l'][i] < entry) if side > 0 else (m['h'][i] > entry)
            if not filled:
                k += 1
                continue
            # a fill late in the bar can be followed by the stop inside the same minute: check it on this bar too
            px, how, ix, mk = _exit_scan(m, i, side, entry, tp, sl)
            fill_i = i
        else:
            fill_i, side, entry = None, 0, None
            kk = k
            while kk < n5:                                # re-anchored at each 5m close while the trend holds
                sd = s2_trend(f, kk, var['slope'])            # shared with core/intraday_lane.py
                if not sd or (side and sd != side):
                    break
                side, lim = sd, f['ema20'][kk]
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
            sl = s2_stop(f, last_k, side, entry)
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
    eq = np.r_[0.0, np.cumsum(daily)]                      # the curve starts at 0: a first-day loss is a drawdown
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


LANE_KINDS = ('R1', 'P5', 'P15')                         # 10-10 c variants: replayed by the lane's own engine


def run_lane(m: dict, f: dict, names, symbol: str = 'X') -> dict:
    """Feed every closed 1m bar (from the 31st 5m close) through core/intraday_lane.IntradayLane - the very code
    the shadow lane runs live - and collect its rows as replay trades.  Fees: maker MAKER, taker 5 + 1 bp."""
    from types import SimpleNamespace
    from core.intraday_lane import IntradayLane
    p = SimpleNamespace(symbol=symbol, intraday_variants=tuple(names), intraday_lane='shadow', maker_bps=MAKER,
                        taker_bps=TAKER - 1.0, intraday_slip_bps=1.0, intraday_notional_usdt=NOTIONAL,
                        intraday_hold_min=P['hold_min'], intraday_trend_quiet_s=600.0, intraday_daily_kill_usdt=1e18,
                        intraday_budget_pct=1.0, mock_balance=EQUITY)
    e = SimpleNamespace(p=p, trend_owned=False, pos=None, busy=False, account={'wallet': EQUITY}, spec={},
                        event=lambda x: None)
    lane = IntradayLane(e, Path('.'), fetch=lambda *a, **k: [], clock=lambda: 0.0)
    out = {n: [] for n in names}
    lane.sink = lambda r: out[r['variant']].append(
        {'t': r['t'] - 60, 't_entry': r['fill_t'], 'side': 1 if r['side'] == 'BUY' else -1, 'entry': r['entry'],
         'exit_px': r['exit'], 'gross_bps': r['gross_bps'], 'net_bps': r['net_bps'], 'exit': r['exit_how'],
         'hold_min': r['hold_min'], 'regime': r.get('regime')})
    for i in range(int(f['b5']['i_last'][30]), len(m['t'])):
        lane.on_bar(m, f, i)
    return out


def run_one(args):
    data, sym = args
    m = load_1m(Path(data), sym)
    f = features(m)
    res = {k: run_symbol(m, f, v) for k, v in VARIANTS.items() if v['kind'] not in LANE_KINDS}
    res.update(run_lane(m, f, [k for k, v in VARIANTS.items() if v['kind'] in LANE_KINDS], sym))
    return sym, res


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
    groups = {'S1': ('S1',), 'S2': ('S2',), 'R1': ('R1',), 'P': ('P5', 'P15')}       # 10-10 c: R1 and P families
    for kind, kinds in groups.items():
        cands = [v for v in VARIANTS if VARIANTS[v]['kind'] in kinds and out['pooled'][v]['DEV'].get('n', 0) >= 30]
        best[kind] = max(cands, key=lambda v: out['pooled'][v]['DEV']['mean_net_bps']) if cands else None
    out['dev_best'] = best
    verdict = {}
    for s in syms:
        for kind, v in best.items():
            h = out['per_symbol'][s][v]['HOLDOUT'] if v else {}
            ok = h.get('n', 0) >= MIN_HOLDOUT_N and h['ci95'][0] > 0 and h['win_rate'] > 0.5
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
             '_rule': 'ACTIVE-ELIGIBLE iff HOLDOUT n >= 100, CI95 lower > 0 and win rate > 50 % (DEV-best variant)'},
            indent=1, default=float), encoding='utf-8')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
