#!/usr/bin/env python3
"""NDG-1: two-sided post-only quoting at +-k sigma, gated by the ATLAS volatility forecast. OFFLINE research only.

Frozen in docs/research_upgrade/plan_non_directional_grid_2026_11.md BEFORE any quoting outcome was computed (SHA-256 of
the LF-normalised plan in docs/research_upgrade/trial_log.jsonl, kind `ndg1-prereg`).

    python -m research.non_directional_grid_study          # TRAIN choice, then HOLDOUT, then FRESH -> data/

One window = the 15 one-minute bars after a decision close. Quotes rest at -d / +d (d = k x predicted sd); a quote
fills when a bar trades through it; the opposite quote is the exit; a bar that leaves the forecast band freezes the
window (inventory closed as a taker at the band edge); what is left at the end is closed as a taker. Nothing here
places an order, starts an engine or changes a gate.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / 'scripts'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from research import atlas_density_study as k  # noqa: E402

PLAN = 'docs/research_upgrade/plan_non_directional_grid_2026_11.md'
# The final ATLAS-K1 ensemble (data/atlas_density_study.json, 60 TRAIN-only rounds). Constants: nothing is re-fitted.
ATLAS_FINAL = [k.KAgent('NORO-1', 'NORO', 1440, 5, 240, -0.3, 0.9633864138417552),
               k.KAgent('NORO-2', 'NORO', 720, 3, 60, -0.2, 1.0),
               k.KAgent('NORO-3', 'NORO', 2183, 10, 499, 0.08643045462817167, 0.9988229569927742),
               k.KAgent('LUMEN-1', 'LUMEN', 1440, 5, 15, 0.3, 1.0),
               k.KAgent('LUMEN-2', 'LUMEN', 360, 1, 5, 0.2, 1.0),
               k.KAgent('LUMEN-3', 'LUMEN', 720, 3, 60, 0.4, 1.0),
               k.KAgent('TIDAL-1', 'TIDAL', 1007, 5, 91, 0.0989577557719849, 1.0),
               k.KAgent('TIDAL-2', 'TIDAL', 1635, 13, 164, -0.23515644602327263, 1.0)]
ATLAS_WEIGHTS = {'NORO-1': 0.46329123015975293, 'NORO-2': 1.0, 'NORO-3': 0.3, 'LUMEN-1': 1.0, 'LUMEN-2': 2.5,
                 'LUMEN-3': 2.5, 'TIDAL-1': 0.6634204312890623, 'TIDAL-2': 0.3}
REF_Z = (-1.546326, 1.507967)                           # TRAIN 5 % / 95 % quantiles of y / EWMA scale (ATLAS-K1 REF)
K_GRID = (0.25, 0.5, 1.0)
FREEZES = ('BAND', 'NONE')
SIGMA_DIV = 3.29                                        # width of a 90 % normal band in sd units
GATE_LO, GATE_HI = 20, 80
MAKER, TAKER, SLIP = 2.0, 5.0, 1.5                      # core.early_stop
SCHEDULES = {'VIP0 2/5 (primary)': (MAKER, TAKER + SLIP), 'BNB 1.8/4.5': (1.8, 4.5 + SLIP),
             'maker 0 / taker 5': (0.0, TAKER + SLIP), 'hypothetical rebate -0.5 / 5': (-0.5, TAKER + SLIP),
             'all-maker exits (bound)': (MAKER, MAKER), 'gross': (0.0, 0.0)}      # (per maker fill, per forced exit)
PRIMARY = 'VIP0 2/5 (primary)'
N_TRIALS = 12
MIN_FILL_WINDOWS = 300
MICRO_NOTIONAL = k.MICRO_NOTIONAL


# ------------------------------------------------------------------------------------------------- simulator
def simulate(lo: np.ndarray, hi: np.ndarray, cl: np.ndarray, d: np.ndarray, qlo: np.ndarray, qhi: np.ndarray,
             freeze: bool) -> dict:
    """Windows x bars arrays of log(low / P0), log(high / P0), log(close / P0); quotes at -d / +d; band (qlo, qhi).
    -> per-window gross (log-return units), maker fills, forced exits (freeze / window end), completed pairs."""
    n = d.size
    pos = np.zeros(n, np.int8)
    live = np.ones(n, bool)
    gross = np.zeros(n)
    makers, t_frz, t_end, pairs = (np.zeros(n, np.int32) for _ in range(4))
    never = np.zeros(n, bool)
    bid_ok = (qlo < -d) if freeze else ~never           # a quote beyond the band edge is not posted
    ask_ok = (qhi > d) if freeze else ~never
    c_prev = np.zeros(n)
    for j in range(lo.shape[1]):
        low, high = lo[:, j], hi[:, j]
        out_lo = (low < qlo) if freeze else never
        out_hi = (high > qhi) if freeze else never
        flat, lng, sht = live & (pos == 0), live & (pos == 1), live & (pos == -1)
        # inventory: the band exit is taken before a favourable fill of the same bar
        m = lng & out_lo
        gross[m] += qlo[m] + d[m]; t_frz[m] += 1; pos[m] = 0; live[m] = False
        m = lng & ~out_lo & (high > d)
        gross[m] += 2 * d[m]; makers[m] += 1; pairs[m] += 1; pos[m] = 0; live[m & out_hi] = False
        m = sht & out_hi
        gross[m] += d[m] - qhi[m]; t_frz[m] += 1; pos[m] = 0; live[m] = False
        m = sht & ~out_hi & (low < -d)
        gross[m] += 2 * d[m]; makers[m] += 1; pairs[m] += 1; pos[m] = 0; live[m & out_lo] = False
        # flat: a quote rests only if the previous close is on its passive side
        fb = flat & bid_ok & (c_prev > -d) & (low < -d)
        fa = flat & ask_ok & (c_prev < d) & (high > d)
        both, ob, oa = fb & fa, fb & ~fa, fa & ~fb
        m = (both | ob) & out_lo                                      # bought, then the band broke below
        gross[m] += qlo[m] + d[m]; makers[m] += 1; t_frz[m] += 1; live[m] = False
        m = (both & ~out_lo & out_hi) | (oa & out_hi)                # sold, then the band broke above
        gross[m] += d[m] - qhi[m]; makers[m] += 1; t_frz[m] += 1; live[m] = False
        m = both & ~out_lo & ~out_hi
        gross[m] += 2 * d[m]; makers[m] += 2; pairs[m] += 1
        m = ob & ~out_lo & ~out_hi
        pos[m] = 1; makers[m] += 1
        m = oa & ~out_hi & ~out_lo
        pos[m] = -1; makers[m] += 1
        # a band exit on the far side, or with no fill at all: quotes are cancelled, nothing is filled
        live[(ob & ~out_lo & out_hi) | (oa & ~out_hi & out_lo) | (flat & ~fb & ~fa & (out_lo | out_hi))] = False
        c_prev = cl[:, j]
    m = pos == 1
    gross[m] += cl[m, -1] + d[m]; t_end[m] += 1
    m = pos == -1
    gross[m] += d[m] - cl[m, -1]; t_end[m] += 1
    return {'gross': gross, 'makers': makers, 't_frz': t_frz, 't_end': t_end, 'pairs': pairs}


def net_bps(sim: dict, schedule: str = PRIMARY) -> np.ndarray:
    maker, forced = SCHEDULES[schedule]
    return sim['gross'] * 1e4 - maker * sim['makers'] - forced * (sim['t_frz'] + sim['t_end'])


def cell_stats(sim: dict, mask: np.ndarray, day: np.ndarray, n_days: int) -> dict:
    """Statistics of one cell on the windows of `mask` (the regime): per ACTIVE window, clustered by UTC day."""
    n = int(mask.sum())
    if not n:
        return {'windows': 0, 'fill_windows': 0, 'mean_net_bps': None}
    net = net_bps(sim)[mask]
    mean, se = k.mean_se(net, day[mask])
    sd = float(net.std(ddof=1)) if n > 1 else 0.0
    filled = sim['makers'][mask] > 0
    out = {'windows': n, 'fill_windows': int(filled.sum()), 'fill_share': float(filled.mean()),
           'mean_net_bps': mean, 'se': se, 'lcb95': mean - 1.645 * se,
           'mean_net_bps_filled': float(net[filled].mean()) if filled.any() else None,
           'gross_bps': float(sim['gross'][mask].mean() * 1e4),
           'gross_se': k.mean_se(sim['gross'][mask] * 1e4, day[mask])[1],
           'makers_per_window': float(sim['makers'][mask].mean()), 'pairs_per_window': float(sim['pairs'][mask].mean()),
           'freeze_exits_per_window': float(sim['t_frz'][mask].mean()),
           'end_exits_per_window': float(sim['t_end'][mask].mean()),
           'net_bps_per_day': float(net.sum() / n_days), 'windows_per_day': n / n_days,
           'usdt_per_day_at_micro_lot': float(net.sum() / n_days * 1e-4 * MICRO_NOTIONAL),
           'sr_window': mean / sd if sd > 0 else 0.0,
           'skew': float(((net - mean) ** 3).mean() / sd ** 3) if sd > 0 else 0.0,
           'kurt': float(((net - mean) ** 4).mean() / sd ** 4) if sd > 0 else 3.0,
           'schedules': {s: float(net_bps(sim, s)[mask].mean()) for s in SCHEDULES}}
    return out


def verdict(ho: dict, fr: dict) -> dict:
    from core.fast_eval import dsr
    if not ho['windows']:
        return {'verdict': 'NO EDGE', 'checks': {'fill_windows>=300': False}}
    d = dsr(ho['sr_window'], ho['windows'], ho['skew'], ho['kurt'], 1.0 / ho['windows'], N_TRIALS)
    checks = {'fill_windows>=300': ho['fill_windows'] >= MIN_FILL_WINDOWS, 'lcb95>0': ho['lcb95'] > 0,
              'dsr>=0.95': bool(d >= 0.95),
              'fresh_windows>=100_and_mean>0': bool(fr['windows'] >= 100 and fr['mean_net_bps'] > 0)}
    return {'verdict': 'EDGE' if all(checks.values()) else 'NO EDGE', 'checks': checks, 'dsr': d}


# ------------------------------------------------------------------------------------------------- inputs
def window_paths(bars: list, pts) -> tuple:
    """(low, high, close) of the 15 bars after each decision, as log returns from the decision close."""
    lo, hi, cl = (np.empty((pts.idx.size, k.H)) for _ in range(3))
    j = np.arange(1, k.H + 1)
    for s, b in enumerate(bars):
        m = pts.seg == s
        if m.any():
            at = pts.idx[m][:, None] + j[None]
            p0 = b.logc[pts.idx[m]][:, None]
            lo[m], hi[m], cl[m] = np.log(b.low[at]) - p0, np.log(b.high[at]) - p0, b.logc[at] - p0
    return lo, hi, cl


def gate_inputs(panel) -> dict:
    """Per symbol: the ATLAS band (Q05, Q95) and width, the EWMA scale, and the window paths."""
    fits = {a.name: panel.fit(a) for a in ATLAS_FINAL}
    e = panel.ensemble(fits, ATLAS_WEIGHTS)
    out = {}
    for s in panel.bars:
        p = panel.pts[s]
        lo, hi, cl = window_paths(panel.bars[s], p)
        out[s] = {'qlo': e[s][:, 0], 'qhi': e[s][:, 6], 'w': e[s][:, 6] - e[s][:, 0],
                  'ewma': k.ewma_scale(panel.bars[s], p), 'lo': lo, 'hi': hi, 'cl': cl, 'day': p.day}
    return out


def thresholds(train_inputs: dict) -> dict:
    return {s: {'w': (float(np.percentile(v['w'], GATE_LO)), float(np.percentile(v['w'], GATE_HI))),
                'ewma': (float(np.percentile(v['ewma'], GATE_LO)), float(np.percentile(v['ewma'], GATE_HI)))}
            for s, v in train_inputs.items()}


def quote_inputs(v: dict, source: str) -> tuple:
    """(sigma, qlo, qhi) of one symbol for the ATLAS or the EWMA gate."""
    if source == 'ATLAS':
        return v['w'] / SIGMA_DIV, v['qlo'], v['qhi']
    return v['ewma'], v['ewma'] * REF_Z[0], v['ewma'] * REF_Z[1]


def pooled(inputs: dict, thr: dict, source: str, kk: float, freeze: bool) -> tuple:
    """Simulate every window of every symbol; -> (sim, day, masks {LOW, MID, HIGH, ALL}, symbol index)."""
    sims, days, lowm, highm, sym = [], [], [], [], []
    for n, (s, v) in enumerate(inputs.items()):
        sigma, qlo, qhi = quote_inputs(v, source)
        sims.append(simulate(v['lo'], v['hi'], v['cl'], kk * sigma, qlo, qhi, freeze))
        x, (t_lo, t_hi) = (v['w'], thr[s]['w']) if source == 'ATLAS' else (v['ewma'], thr[s]['ewma'])
        lowm.append(x < t_lo); highm.append(x > t_hi); days.append(v['day']); sym.append(np.full(x.size, n))
    sim = {key: np.concatenate([q[key] for q in sims]) for key in sims[0]}
    low, high = np.concatenate(lowm), np.concatenate(highm)
    return sim, np.concatenate(days), {'LOW': low, 'MID': ~low & ~high, 'HIGH': high, 'ALL': np.ones(low.size, bool)}, \
        np.concatenate(sym)


def variance_ratio(inputs: dict, thr: dict) -> dict:
    """VR(15) = E[(15 m return)^2] / E[sum of squared 1 m returns] by ATLAS regime, SE by day cluster (linearised);
    and the realised mean |15 m return| in bps."""
    cl = np.concatenate([v['cl'] for v in inputs.values()])
    day = np.concatenate([v['day'] for v in inputs.values()])
    low = np.concatenate([v['w'] < thr[s]['w'][0] for s, v in inputs.items()])
    high = np.concatenate([v['w'] > thr[s]['w'][1] for s, v in inputs.items()])
    r1 = np.diff(np.concatenate([np.zeros((cl.shape[0], 1)), cl], axis=1), axis=1)
    a, b = cl[:, -1] ** 2, (r1 ** 2).sum(axis=1)
    out = {}
    for name, m in (('LOW', low), ('MID', ~low & ~high), ('HIGH', high), ('ALL', np.ones(low.size, bool))):
        vr = float(a[m].sum() / b[m].sum())
        e = (a[m] - vr * b[m]) / b[m].mean()
        out[name] = {'share': float(m.mean()), 'vr15': vr, 'se': k.cluster_se(e / e.size, day[m]),
                     'abs_ret_bps': float(np.abs(cl[m, -1]).mean() * 1e4),
                     'sd_1m_bps': float(math.sqrt(b[m].mean() / k.H) * 1e4)}
    return out


# ------------------------------------------------------------------------------------------------- run
def evaluate(inputs: dict, thr: dict, n_days: int) -> dict:
    """Every cell under ATLAS LOW and EWMA LOW, plus the regime ablations of each ATLAS cell."""
    out = {'cells': {}, 'ablation': {}, 'per_symbol': {}}
    for source in ('ATLAS', 'EWMA'):
        for kk in K_GRID:
            for fz in FREEZES:
                sim, day, masks, sym = pooled(inputs, thr, source, kk, fz == 'BAND')
                name = f'{source}|k{kk:g}|{fz}'
                out['cells'][name] = cell_stats(sim, masks['LOW'], day, n_days)
                if source == 'ATLAS':
                    out['ablation'][name] = {r: cell_stats(sim, masks[r], day, n_days) for r in ('ALL', 'MID', 'HIGH')}
                    out['per_symbol'][name] = {s: cell_stats(sim, masks['LOW'] & (sym == n), day, n_days)
                                               for n, s in enumerate(inputs)}
    return out


def run(symbols=k.SYMBOLS, log=print) -> dict:
    t0 = time.time()
    u = k.uniforms(k.SEED)
    bars = {s: k.load_bars(s) for s in symbols}
    reps, inputs, thr = {}, {}, None
    for part in k.PARTS:
        panel = k.Panel(bars, part, u)
        inputs[part] = gate_inputs(panel)
        if part == 'TRAIN':
            thr = thresholds(inputs[part])
        n_days = int(np.unique(np.concatenate([v['day'] for v in inputs[part].values()])).size)
        reps[part] = {'days': n_days, 'windows': {s: int(v['w'].size) for s, v in inputs[part].items()},
                      **evaluate(inputs[part], thr, n_days), 'regimes': variance_ratio(inputs[part], thr)}
        log(part, 'windows', reps[part]['windows'], f'{time.time() - t0:.0f} s')
    atlas = {c: v for c, v in reps['TRAIN']['cells'].items() if c.startswith('ATLAS|')}
    t1 = max(atlas, key=lambda c: atlas[c]['mean_net_bps'])
    out = {'plan': PLAN, 'symbols': list(symbols), 'k_grid': K_GRID, 'freezes': FREEZES, 'schedules': SCHEDULES,
           'thresholds': thr, 'partitions': reps,
           'T1': {'cell': t1, **{p: reps[p]['cells'][t1] for p in k.PARTS},
                  **verdict(reps['HOLDOUT']['cells'][t1], reps['FRESH']['cells'][t1])},
           'seconds': round(time.time() - t0, 1)}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--out', default=str(ROOT / 'data' / 'non_directional_grid_study.json'))
    a = ap.parse_args(argv)
    out = run()
    Path(a.out).write_text(json.dumps(out, indent=1, default=float) + '\n', encoding='utf-8')
    t1 = out['T1']
    print('T1', t1['cell'], t1['verdict'], json.dumps(t1.get('checks')))
    for p in k.PARTS:
        c = t1[p]
        print(f"  {p:8s} windows {c['windows']} fills {c['fill_windows']} net {c['mean_net_bps']:.3f} +- {c['se']:.3f} "
              f"gross {c['gross_bps']:.3f} | per day {c['net_bps_per_day']:.2f} bps")
    print(f"wrote {a.out} ({out['seconds']:.0f} s)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
