"""RFC-2026-Q4.3 offline check: does a CHOP (KER120 < 0.25) or VPIN gate improve the burst lane per trade?
Policy = current demo shadow: MR30, through fills, maker-pegged time exit (cap 240 s), engine absorption gate.
Data: SOLUSDT aggTrades 2026-09-05/06/08 (only local print days).  KER = micro_signals.ker definition (120 s window,
5 s buckets) on the per-second last price; VPIN = MicroFeed buckets (SOL 250 k USD, 10 buckets).
    python docs/research_upgrade/snapshot_2026-10-16q42/exp_chop_vpin.py"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import tools.burst_scalper_replay as R  # noqa: E402

DAYS = [ROOT / 'data/raw/SOLUSDT-aggTrades-2026-09-05.zip', ROOT / 'data/raw/SOLUSDT-aggTrades-2026-09-06.zip',
        ROOT / 'data/replay_cache/agg_zips/SOLUSDT-aggTrades-2026-09-08.zip']
VPIN_BUCKET, VPIN_N, VPIN_MAX = 250_000.0, 10, 0.80


def prep(p):
    d = R.prep(p)
    t, px, q, bm = R.load_day(p)
    f, n = d['f'], len(d['f']['burst'])
    idx = np.floor(t).astype(np.int64) - f['s0']
    last = np.full(n, np.nan)
    last[idx] = px
    last = R._ffill(last)
    # KER 120 s on 5 s samples: |P_s - P_{s-120}| / sum_j |P_{s-5j} - P_{s-5j-5}|
    d5 = np.zeros(n)
    d5[5:] = np.abs(last[5:] - last[:-5])
    path = np.zeros(n)
    for j in range(24):
        path[5 * j:] += d5[:n - 5 * j]
    ker = np.full(n, np.nan)
    ker[120:] = np.abs(last[120:] - last[:-120]) / np.where(path[120:] > 0, path[120:], np.nan)
    ker[120:][path[120:] == 0] = 0.0
    # VPIN on exact aggressor-signed USD buckets (a print is assigned to the bucket its USD starts in)
    usd = px * q
    sgn = np.where(bm == 1, -1.0, 1.0)
    b = np.floor((np.cumsum(usd) - usd) / VPIN_BUCKET).astype(np.int64)
    imb = np.bincount(b, weights=usd * sgn) / VPIN_BUCKET
    close_t = np.zeros(len(imb))
    np.maximum.at(close_t, b, t)
    vp = np.convolve(np.abs(imb), np.ones(VPIN_N) / VPIN_N)[:len(imb)]
    net = np.convolve(imb, np.ones(VPIN_N))[:len(imb)]
    vp[:VPIN_N - 1] = np.nan
    tox_sec = np.full(n, np.nan)              # last toxic bucket close second, and its flow side
    tox_side = np.zeros(n)
    hit = np.flatnonzero(vp > VPIN_MAX)
    for k in hit:
        s = int(close_t[k]) - f['s0']
        if 0 <= s < n:
            tox_sec[s], tox_side[s] = s, np.sign(net[k])
    ok = ~np.isnan(tox_sec)
    i = np.where(ok, np.arange(n), 0)
    np.maximum.accumulate(i, out=i)
    d.update(q=q, ker=ker, last_tox=np.where(ok[i], tox_sec[i], -1e9), last_tox_side=np.where(ok[i], tox_side[i], 0))
    d['want'] = -np.sign(np.nan_to_num(f['dpx10']))
    cvd = R._roll(np.bincount(idx, weights=usd * sgn, minlength=n), 15)
    mv = np.zeros(n)
    mv[15:] = (last[15:] / last[:-15] - 1) * 1e4
    d['absorb'] = ((d['want'] > 0) & (cvd >= 50e3) & (mv < 2.5)) | ((d['want'] < 0) & (cvd <= -50e3) & (mv > -2.5))
    return d


def run(d, extra=None):
    f0 = d['f']['burst']
    d['f']['burst'] = f0 & ~d['absorb'] & (~extra if extra is not None else True)
    old = dict(R.CFG)
    R.CFG['exit_maker_s'] = 240.0
    try:
        tr = R.simulate(d, 'MR', 30.0, 'through', R.FEES['USDT'])
    finally:
        R.CFG.clear()
        R.CFG.update(old)
        d['f']['burst'] = f0
    for x in tr:
        s = int(x['t']) - d['f']['s0'] - 1
        x['ker'] = d['ker'][max(s, 0)]
        x['tox_age'] = s - d['last_tox'][max(s, 0)]
    return tr


def summ(name, tr):
    if not tr:
        print(f'{name:40s} n=0')
        return
    net = np.array([x['net_bps'] for x in tr])
    rng = np.random.default_rng(0)
    bs = [rng.choice(net, len(net)).mean() for _ in range(1000)]
    print(f'{name:40s} n={len(tr):5d} net/tr={net.mean():6.2f} CI95=[{np.percentile(bs, 2.5):6.2f},'
          f'{np.percentile(bs, 97.5):6.2f}] win={np.mean(net > 0):.2f} sum={net.sum():8.0f}')


if __name__ == '__main__':
    D = [prep(p) for p in DAYS]
    base = sum((run(d) for d in D), [])
    summ('base (demo policy)', base)
    kn = [x for x in base if not np.isnan(x['ker'])]
    summ('  entry KER < 0.25 (CHOP)', [x for x in kn if x['ker'] < 0.25])
    summ('  entry KER >= 0.25', [x for x in kn if x['ker'] >= 0.25])
    summ('  entry KER >= 0.40', [x for x in kn if x['ker'] >= 0.40])
    summ('  entry within 60 s of VPIN > 0.8', [x for x in base if x['tox_age'] <= 60])
    summ('  entry not within 60 s of VPIN > 0.8', [x for x in base if x['tox_age'] > 60])
    summ('gate CHOP (KER < 0.25 -> no entry)', sum((run(d, np.nan_to_num(d['ker'], nan=0.0) < 0.25) for d in D), []))
    summ('gate VPIN any side 60 s', sum((run(d, (np.arange(len(d['ker'])) - d['last_tox']) <= 60) for d in D), []))
    summ('gate VPIN against flow 30 s (engine)',
         sum((run(d, ((np.arange(len(d['ker'])) - d['last_tox']) <= 30) & (d['want'] != d['last_tox_side'])) for d in D),
             []))
    print('VPIN > 0.8 bucket-closes per day:', [int(np.sum(np.diff(d['last_tox']) > 0)) for d in D])
