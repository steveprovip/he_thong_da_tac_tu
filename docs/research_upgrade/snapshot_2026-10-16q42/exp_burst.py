"""RFC-2026-Q4.2 offline check: exit_maker_s variants + absorption gate on the burst replay (MR30, through fills)."""
import sys, json, numpy as np
sys.path.insert(0, r'F:\he_thong_da_tac_tu_2')
import tools.burst_scalper_replay as R
from pathlib import Path
days = [Path(r'F:\he_thong_da_tac_tu_2\data\raw\SOLUSDT-aggTrades-2026-09-05.zip'),
        Path(r'F:\he_thong_da_tac_tu_2\data\raw\SOLUSDT-aggTrades-2026-09-06.zip'),
        Path(r'F:\he_thong_da_tac_tu_2\data\replay_cache\agg_zips\SOLUSDT-aggTrades-2026-09-08.zip')]
D = [R.prep(p) for p in days]
def absorbed(day, cvd_usd, eps_bps, w=15):
    f = day['f']; n = len(f['burst'])
    # rebuild last price / cvd per second
    t, px, bm = day['t'], day['px'], day['bm']
    sec = np.floor(t).astype(np.int64) - f['s0']
    last = np.full(n, np.nan); last[sec] = px; last = R._ffill(last)
    q = day.get('q')
    return last
def run(day, gate=None, **cfg):
    old = dict(R.CFG); R.CFG.update(cfg)
    f0 = day['f']['burst']
    if gate is not None:
        day['f']['burst'] = f0 & ~gate
    try:
        tr = R.simulate(day, 'MR', 30.0, 'through', R.FEES['USDT'])
    finally:
        R.CFG.clear(); R.CFG.update(old); day['f']['burst'] = f0
    return tr
def summ(name, tr):
    if not tr: print(name, 'n=0'); return
    net = np.array([x['net_bps'] for x in tr]); g = np.array([x['gross_bps'] for x in tr])
    tk = np.mean([x['taker_frac'] for x in tr])
    by = {}
    for x in tr: by.setdefault(x['exit'], []).append(x['net_bps'])
    hold = np.median([x['hold_s'] for x in tr])
    # bootstrap CI over trades (rough)
    rng = np.random.default_rng(0); bs = [rng.choice(net, len(net)).mean() for _ in range(500)]
    print(f'{name:28s} n={len(tr):5d} net/tr={net.mean():6.2f} CI95=[{np.percentile(bs,2.5):6.2f},{np.percentile(bs,97.5):6.2f}] '
          f'gross/tr={g.mean():6.2f} taker={tk:.2f} hold_med={hold:5.0f}s sum={net.sum():8.0f} '
          + ' '.join(f'{k}:{len(v)}/{np.mean(v):.1f}' for k, v in sorted(by.items())))
# absorption gate per second: |CVD 15 s| >= thr USD and |move 15 s| < eps bps
def gate_of(day, thr, eps):
    t, px, bm = day['t'], day['px'], day['bm']; f = day['f']; n = len(f['burst'])
    sec = np.floor(t).astype(np.int64) - f['s0']
    last = np.full(n, np.nan); last[sec] = px; last = R._ffill(last)
    # cvd per second needs qty: recompute from cvd10 is impossible -> rebuild usd from load_day
    return last
import zipfile
for di, p in enumerate(days):
    t, px, q, bm = R.load_day(p); D[di]['q'] = q
def gate(day, thr, eps, w=15):
    t, px, q, bm = day['t'], day['px'], day['q'], day['bm']; f = day['f']; n = len(f['burst'])
    idx = np.floor(t).astype(np.int64) - f['s0']
    usd = px * q
    cvd = R._roll(np.bincount(idx, weights=usd * (1 - 2 * bm), minlength=n), w)
    last = np.full(n, np.nan); last[idx] = px; last = R._ffill(last)
    mv = np.zeros(n); mv[w:] = np.abs(last[w:] / last[:-w] - 1) * 1e4
    lam = mv / np.maximum(np.abs(cvd) / 1e5, 1e-9)        # bps per $100k of net taker flow
    g = (np.abs(cvd) >= thr) & (mv < eps)
    return g
for name, kw, gt in [('base exit_maker 10s', {}, None),
                     ('exit_maker 15s', {'exit_maker_s': 15.0}, None),
                     ('exit_maker 30s', {'exit_maker_s': 30.0}, None),
                     ('exit_maker until SL', {'exit_maker_s': 1e9}, None)]:
    summ(name, sum((run(d, gt, **kw) for d in D), []))
for thr in (25e3, 50e3, 100e3):
    for eps in (1.0, 2.0, 4.0):
        tr = sum((run(d, gate(d, thr, eps)) for d in D), [])
        summ(f'absorb |cvd15|>={thr/1e3:.0f}k mv<{eps}', tr)
