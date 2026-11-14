"""Polymarket 15-minute Up/Down: offline benchmark of research/polymarket_15m_fast_arb.py (2026-11-14).

Three questions, no network:
  1. Is the module's Binance fair value `p_up` calibrated? Every 15-minute window of the local Binance USD-M 1m klines
     (data/klines_1m), scored at 5 / 10 / 13 minutes into the window: Brier score vs the base rate, reliability by
     decile. Outcome proxy = close of the 15th minute > open of the 1st (settlement uses Chainlink 60 s TWAPs).
  2. The latency-impulse idea on 1 m bars: after a last-minute move >= --impulse-pct, how often does the window end
     on that side, and what does the model say? (The live trigger uses an 8 s move; 1 m bars are a coarse proxy.)
  3. What did real Polymarket asks give? data/pm15_signals.jsonl = the shadow scanner's flagged signals, settled.
A binary bought at ask a pays 1 if it wins; with the 2026 crypto taker fee 0.07 a (1 - a) per share,
    EV per share = p - a - 0.07 a (1 - a),  break-even ask a* solves a + 0.07 a (1 - a) = p.
A model edge is only money if Polymarket sells below a*; only question 3 sees real asks.

    python -m research.pm15_benchmark
"""
from __future__ import annotations

import argparse
import json
import math
from pathlib import Path

import numpy as np

from research.polymarket_15m_fast_arb import p_up, fee
from research.smc_fibo_backtest import load_1m

ROOT = Path(__file__).resolve().parents[1]
FEE_RATE = 0.07
VOL_MINUTES = 120
ELAPSED = (5, 10, 13)


def breakeven_ask(p: float, rate: float = FEE_RATE) -> float:
    """Largest ask a in [0, 1] with p - a - rate a (1 - a) >= 0 (the fee term is increasing on [0, 0.5])."""
    if p <= 0:
        return 0.0
    if p >= 1:
        return 1.0
    lo, hi = 0.0, 1.0
    for _ in range(60):
        mid = 0.5 * (lo + hi)
        if p - mid - fee(mid, rate) >= 0:
            lo = mid
        else:
            hi = mid
    return lo


def window_points(m1: dict, elapsed=ELAPSED, vol_minutes: int = VOL_MINUTES) -> dict:
    """(p_up, outcome, last-minute return, elapsed) for every complete 15-minute window with enough history."""
    t, o, c = m1['t'], m1['o'], m1['c']
    logc = np.log(c)
    r1 = np.r_[np.nan, np.diff(logc)]
    cs, cs2, cnt = np.nancumsum(np.r_[0.0, r1]), np.nancumsum(np.r_[0.0, r1 ** 2]), np.cumsum(np.r_[0, ~np.isnan(r1)])
    starts = np.flatnonzero((t % 900_000 == 0))
    starts = starts[(starts >= vol_minutes + 1) & (starts + 14 < len(t))]
    starts = starts[t[starts + 14] - t[starts] == 14 * 60_000]          # 15 consecutive minutes
    out = {k: [] for k in ('p', 'y', 'r_last', 'e', 'start')}
    for e in elapsed:
        i = starts + e - 1                                                # decision at the close of minute e
        a, b = i - vol_minutes + 1, i + 1                                 # returns r1[a..i]
        n = cnt[b] - cnt[a]
        s1, s2 = cs[b] - cs[a], cs2[b] - cs2[a]
        var = (s2 - s1 ** 2 / n) / np.maximum(n - 1, 1)
        sig = np.sqrt(np.maximum(var, 0.0))
        y = c[starts + 14] > o[starts]
        keep = (n >= 10) & (c[starts + 14] != o[starts])
        for j in np.flatnonzero(keep):
            out['p'].append(p_up(c[i[j]], o[starts[j]], sig[j], 15 - e))
            out['y'].append(bool(y[j]))
            out['r_last'].append(float(r1[i[j]]))
            out['e'].append(e)
            out['start'].append(int(t[starts[j]]))
    return {k: np.asarray(v) for k, v in out.items()}


def calibration(p: np.ndarray, y: np.ndarray, bins: int = 10) -> dict:
    y = y.astype(float)
    base = float(y.mean())
    brier, clim = float(np.mean((p - y) ** 2)), float(np.mean((base - y) ** 2))
    edges = np.quantile(p, np.linspace(0, 1, bins + 1))
    rows = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (p >= lo) & (p <= hi)
        if m.any():
            rows.append({'p_mean': round(float(p[m].mean()), 4), 'freq': round(float(y[m].mean()), 4), 'n': int(m.sum())})
    return {'n': int(len(p)), 'base_rate': round(base, 4), 'brier': round(brier, 5), 'brier_base': round(clim, 5),
            'skill': round(1 - brier / clim, 4) if clim > 0 else None, 'reliability': rows}


def impulse_table(pts: dict, impulse_pct: float) -> dict:
    out = {}
    for e in sorted(set(pts['e'].tolist())):
        m = (pts['e'] == e) & (np.abs(pts['r_last']) >= impulse_pct / 100.0)
        if not m.any():
            out[f'{e}m'] = {'n': 0}
            continue
        d_up = pts['r_last'][m] > 0
        hit = np.where(d_up, pts['y'][m], ~pts['y'][m])
        p_dir = np.where(d_up, pts['p'][m], 1 - pts['p'][m])
        h = float(hit.mean())
        out[f'{e}m'] = {'n': int(m.sum()), 'hit_rate': round(h, 4), 'model_p_mean': round(float(p_dir.mean()), 4),
                        'breakeven_ask_at_hit_rate': round(breakeven_ask(h), 4)}
    return out


def real_signals(path: Path) -> dict:
    if not path.exists():
        return {'n': 0}
    rows = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]
    rows = [r for r in rows if r.get('won') is not None and r.get('pnl_per_share') is not None]
    out = {}
    for name, sel in (('all', rows), ('impulse', [r for r in rows if r.get('trigger') == 'IMPULSE']),
                      ('model', [r for r in rows if r.get('trigger') != 'IMPULSE'])):
        if not sel:
            out[name] = {'n': 0}
            continue
        pnl = np.asarray([r['pnl_per_share'] for r in sel], float)
        out[name] = {'n': len(sel), 'wins': int(sum(1 for r in sel if r['won'])),
                     'mean_ask': round(float(np.mean([r['price'] for r in sel])), 4),
                     'mean_model_edge': round(float(np.mean([r['edge'] for r in sel])), 4),
                     'pnl_per_share_mean': round(float(pnl.mean()), 4),
                     'pnl_per_share_se': round(float(pnl.std(ddof=1) / math.sqrt(len(pnl))), 4) if len(pnl) > 1 else None,
                     'pnl_per_share_sum': round(float(pnl.sum()), 4)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--symbols', default='BTCUSDT,ETHUSDT,SOLUSDT,XRPUSDT')
    ap.add_argument('--impulse-pct', type=float, default=0.3)
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'pm15_benchmark.json')
    a = ap.parse_args(argv)
    res = {'calibration': {}, 'impulse': {}, 'breakeven_ask': {f'{p:.2f}': round(breakeven_ask(p), 4)
                                                               for p in (0.1063, 0.2, 0.5, 0.6, 0.7, 0.8, 0.9)}}
    allp, ally = [], []
    for s in [x.strip().upper() for x in a.symbols.split(',') if x.strip()]:
        pts = window_points(load_1m(s))
        res['calibration'][s] = calibration(pts['p'], pts['y'])
        res['impulse'][s] = impulse_table(pts, a.impulse_pct)
        allp.append(pts['p'])
        ally.append(pts['y'])
        print(s, json.dumps({k: v for k, v in res['calibration'][s].items() if k != 'reliability'}),
              json.dumps(res['impulse'][s]), flush=True)
    res['calibration']['pooled'] = calibration(np.concatenate(allp), np.concatenate(ally))
    res['real_signals'] = real_signals(ROOT / 'data' / 'pm15_signals.jsonl')
    a.out.write_text(json.dumps(res, indent=1), encoding='utf-8')
    print(json.dumps({'pooled': {k: v for k, v in res['calibration']['pooled'].items() if k != 'reliability'},
                      'real_signals': res['real_signals'], 'breakeven_ask': res['breakeven_ask']}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
