"""RFC-2026-Q4.4 Task 3 offline check: fade the VPIN climax (SOLUSDT aggTrades 2026-09-05/06/08, through fills).
PRE-REGISTERED (before the first run): VPIN = MicroFeed buckets (SOL 250 k USD, 10 buckets, aggressor-signed).
  climax   a bucket close with VPIN >= 0.85 (also reported: >= 0.95); toxic side = sign of the 10-bucket net flow.
  trigger  within 10 min after the climax, the first bucket close where VPIN fell >= 0.15 versus the VPIN of the
           last bucket closed >= 60 s earlier (dVPIN/dt < -0.15 / min); one trigger per climax.
  quote    post-only on the touch AGAINST the toxic flow (toxic SELL -> BUY at the bid), live 10 s, re-peg 4 s.
  exits    the demo burst exits: TP +30 bps post-only, SL -30 taker, 60 s hold -> post-only exit pegged at our touch
           up to 240 s -> taker (tools/burst_scalper_replay.simulate, policy MR with the side forced).
  fees     maker 2 / taker 5 bps.  Baseline for comparison: the same exits on every burst second (demo MR30).
    python docs/research_upgrade/snapshot_2026-10-16q42/exp_vpin_fade.py"""
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
import tools.burst_scalper_replay as R  # noqa: E402

DAYS = [ROOT / 'data/raw/SOLUSDT-aggTrades-2026-09-05.zip', ROOT / 'data/raw/SOLUSDT-aggTrades-2026-09-06.zip',
        ROOT / 'data/replay_cache/agg_zips/SOLUSDT-aggTrades-2026-09-08.zip']
BUCKET, NB, LIVE_S = 250_000.0, 10, 10


def prep(p):
    d = R.prep(p)
    t, px, q, bm = R.load_day(p)
    usd, sgn = px * q, np.where(bm == 1, -1.0, 1.0)
    b = np.floor((np.cumsum(usd) - usd) / BUCKET).astype(np.int64)
    imb = np.bincount(b, weights=usd * sgn) / BUCKET
    close_t = np.zeros(len(imb))
    np.maximum.at(close_t, b, t)
    vp = np.convolve(np.abs(imb), np.ones(NB) / NB)[:len(imb)]
    net = np.convolve(imb, np.ones(NB))[:len(imb)]
    vp[:NB] = np.nan
    d['vp'], d['vp_t'], d['vp_side'] = vp[:-1], close_t[:-1], np.sign(net[:-1])   # last bucket may be partial
    return d


def triggers(d, climax):
    vp, vt, vs = d['vp'], d['vp_t'], d['vp_side']
    out, k, n = [], 0, len(vp)
    while k < n:
        if not (vp[k] >= climax):
            k += 1
            continue
        side, t0 = vs[k], vt[k]
        j, hit = k + 1, None
        while j < n and vt[j] <= t0 + 600:
            ref = np.searchsorted(vt, vt[j] - 60, side='right') - 1
            if ref >= 0 and vp[j] - vp[ref] <= -0.15:
                hit = j
                break
            j += 1
        if hit is not None:
            out.append((vt[hit], side))
        k = j + 1 if hit is not None else k + 1
        while k < n and vt[k] <= (vt[hit] if hit is not None else t0) + 1:
            k += 1
    return out


def run(d, trig=None):
    f, n = d['f'], len(d['f']['burst'])
    b0, m0 = f['burst'], f['dpx10']
    old = dict(R.CFG)
    R.CFG['exit_maker_s'] = 240.0
    try:
        if trig is not None:
            burst, dpx = np.zeros(n, bool), np.full(n, np.nan)
            for tt, side in trig:
                s = int(tt) - f['s0'] + 1                    # decisions use the last complete second
                if 0 <= s < n and side != 0:
                    burst[s:s + LIVE_S], dpx[s:s + LIVE_S] = True, side      # MR side = -sign(dpx) = against flow
            f['burst'], f['dpx10'] = burst, dpx
        return R.simulate(d, 'MR', 30.0, 'through', R.FEES['USDT'])
    finally:
        R.CFG.clear()
        R.CFG.update(old)
        f['burst'], f['dpx10'] = b0, m0


def summ(name, tr):
    if not tr:
        print(f'{name:34s} n=0')
        return
    net = np.array([x['net_bps'] for x in tr])
    g = np.array([x['gross_bps'] for x in tr])
    rng = np.random.default_rng(0)
    bs = [rng.choice(net, len(net)).mean() for _ in range(2000)]
    ex = {}
    for x in tr:
        ex[x['exit']] = ex.get(x['exit'], 0) + 1
    print(f'{name:34s} n={len(tr):5d} net/tr={net.mean():6.2f} CI95=[{np.percentile(bs, 2.5):6.2f},'
          f'{np.percentile(bs, 97.5):6.2f}] gross={g.mean():6.2f} win={np.mean(net > 0):.2f} exits={ex}')


D = [prep(p) for p in DAYS]
summ('baseline: every burst second (MR30)', sum((run(d) for d in D), []))
for c in (0.85, 0.95):
    trig = [triggers(d, c) for d in D]
    print(f'climax >= {c}: triggers per day', [len(x) for x in trig])
    summ(f'  fade after VPIN >= {c}', sum((run(d, tg) for d, tg in zip(D, trig)), []))
    summ(f'  same seconds, WITH the flow (control)', sum((run(d, [(t, -s) for t, s in tg]) for d, tg in zip(D, trig)), []))
