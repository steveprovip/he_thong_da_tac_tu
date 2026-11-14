"""SMC event study on Binance USD-M 1m klines - offline research: no orders, no network.

Events come from core/smc.py, the same code path the live AlphaEngine runs: BOS, CHoCH, liquidity sweep
(+1 bullish / -1 bearish) and the open-FVG pull (sign of fvg_dist when |fvg_dist| >= --fvg-min ATR). The forward
log return over h bars is signed in the event's direction. Only events >= h bars apart are kept, so overlapping
windows do not inflate the t-stat. The first (1 - --holdout-frac) of time is in-sample and the rest a
time-separated holdout. Net = gross - core.early_stop.COST_ALL_IN_BPS (maker + taker + spread/slippage).

    python -m research.smc_backtest --symbol BTCUSDT --last 6
"""
from __future__ import annotations

import argparse
import json
import time
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

from core.early_stop import COST_ALL_IN_BPS
from core.smc import smc_features

ROOT = Path(__file__).resolve().parents[1]
KLINES = ROOT / 'data' / 'klines_1m'


def load_klines(paths) -> pd.DataFrame:
    frames = []
    for p in paths:
        with zipfile.ZipFile(p) as z:
            name = z.namelist()[0]
            head = z.open(name).readline()
            df = pd.read_csv(z.open(name), header=0 if not head[:1].isdigit() else None, usecols=range(6))
        df.columns = ['open_time', 'open', 'high', 'low', 'close', 'volume']
        frames.append(df)
    df = pd.concat(frames, ignore_index=True).drop_duplicates('open_time').sort_values('open_time')
    return df.reset_index(drop=True)


def non_overlapping(idx: np.ndarray, h: int) -> np.ndarray:
    keep, last = [], -(1 << 62)
    for i in idx.tolist():
        if i - last >= h:
            keep.append(i)
            last = i
    return np.asarray(keep, dtype=np.int64)


def stats(x: np.ndarray) -> dict:
    n = int(x.size)
    if n == 0:
        return {'n': 0}
    sd = float(x.std(ddof=1)) if n > 1 else float('nan')
    mean = float(x.mean())
    return {'n': n, 'gross_bps': round(mean, 3), 'median_bps': round(float(np.median(x)), 3),
            'hit': round(float((x > 0).mean()), 4), 't': round(mean / (sd / n ** 0.5), 2) if n > 1 and sd > 0 else None,
            'net_bps': round(mean - COST_ALL_IN_BPS, 3)}


def study(df: pd.DataFrame, horizons=(5, 15, 60), holdout_frac=0.3, fvg_min=1.0) -> dict:
    o, h, lo, c = (df[k].to_numpy(np.float64) for k in ('open', 'high', 'low', 'close'))
    t0 = time.perf_counter()
    f = smc_features(h, lo, c, o)
    t_detect = time.perf_counter() - t0
    t1 = time.perf_counter()
    logc = np.log(c)
    ev = {'bos': f['bos'], 'choch': f['choch'], 'sweep': f['sweep'],
          'fvg_pull': np.where(np.abs(f['fvg_dist']) >= fvg_min, np.sign(f['fvg_dist']), 0).astype(np.int8)}
    split = int(len(c) * (1 - holdout_frac))
    res = {}
    for hz in horizons:
        fwd = np.full(len(c), np.nan)
        fwd[:-hz] = (logc[hz:] - logc[:-hz]) * 1e4
        base = {}
        for part, sl in (('in_sample', slice(0, split)), ('holdout', slice(split, len(c) - hz))):
            idx = non_overlapping(np.arange(len(c))[sl], hz)
            base[part] = stats(fwd[idx])                                   # always long, for the drift
        res[f'h{hz}'] = {'always_long': base}
        for name, sig in ev.items():
            row = {}
            for part, (a, b) in (('in_sample', (0, split)), ('holdout', (split, len(c) - hz))):
                idx = non_overlapping(np.flatnonzero(sig[a:b]) + a, hz)
                row[part] = stats(sig[idx] * fwd[idx])
            res[f'h{hz}'][name] = row
    return {'bars': int(len(c)), 'split_open_time': int(df['open_time'].iloc[split]), 'cost_bps': COST_ALL_IN_BPS,
            'fvg_min_atr': fvg_min, 'seconds': {'detect': round(t_detect, 2),
                                                'study': round(time.perf_counter() - t1, 2)}, 'results': res}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--symbol', default='BTCUSDT')
    ap.add_argument('--months', nargs='*', default=None, help='YYYY-MM list (default: the last --last files)')
    ap.add_argument('--last', type=int, default=6)
    ap.add_argument('--horizons', nargs='+', type=int, default=[5, 15, 60])
    ap.add_argument('--holdout-frac', type=float, default=0.3)
    ap.add_argument('--fvg-min', type=float, default=1.0)
    ap.add_argument('--out', default=None)
    a = ap.parse_args(argv)
    files = sorted(KLINES.glob(f'{a.symbol}-1m-*.zip'))
    if a.months:
        files = [p for p in files if p.stem.split('-1m-')[-1] in set(a.months)]
    else:
        files = files[-a.last:]
    if not files:
        raise SystemExit(f'no klines for {a.symbol} in {KLINES}')
    t = time.perf_counter()
    df = load_klines(files)
    t_load = time.perf_counter() - t
    rep = {'symbol': a.symbol, 'files': [p.name for p in files], **study(df, a.horizons, a.holdout_frac, a.fvg_min)}
    rep['seconds']['load'] = round(t_load, 2)
    rep['seconds']['total'] = round(time.perf_counter() - t, 2)
    out = Path(a.out) if a.out else ROOT / 'data' / f'smc_backtest_{a.symbol}.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rep, indent=1) + '\n', encoding='utf-8')
    print(f"{a.symbol} {rep['bars']} bars {files[0].stem}..{files[-1].stem} | seconds {rep['seconds']} | "
          f"cost {COST_ALL_IN_BPS} bps -> {out}")
    for hz, rows in rep['results'].items():
        for name, row in rows.items():
            i, o = row['in_sample'], row['holdout']
            print(f"  {hz:4s} {name:11s} in n={i.get('n', 0):6d} gross {i.get('gross_bps', 0):7.3f} t {i.get('t')} | "
                  f"holdout n={o.get('n', 0):6d} gross {o.get('gross_bps', 0):7.3f} t {o.get('t')} net {o.get('net_bps')}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
