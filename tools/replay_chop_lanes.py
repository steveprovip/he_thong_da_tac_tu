"""Replay of the two "chop deadlock" proposals on Binance USD-M public aggTrades (data.binance.vision daily zips).
Read-only; no key, no order.

    python tools/replay_chop_lanes.py --data <dir with SYMBOL-aggTrades-YYYY-MM-DD.zip> [--json out.json]

Features on a 1 s grid from trade prints, as the engine defines them (core/micro_signals.py):
  KER 120 s on 5 s closes, VWAP 60 s, 60 s range / position, CVD velocity 10 s (USD/s), price velocity 15 s.

A. MR lane (signal exactly as msig.signals MR): KER < 0.38, |price - VWAP60| >= 0.12 %, price in the outer 20 % of a
   60 s range <= 0.40 %, side = toward VWAP.  Bracket variants (SL taker 5 bps + 1 slip, TP maker 2, entry maker 2):
     CUR    TP = clamp(|dev|, 0.12, 0.18) %, SL 0.08 %   (net RR <= 0.875)
     F22_10 / F24_11 / F26_12   form proposal TP 0.22-0.26 % / SL 0.10-0.12 %   (net RR 0.95-1.10 with real fees)
     R29_10 / R31_12            TP that really reaches net RR >= 1.35 with SL 0.10 / 0.12 %
B. TREND in chop (adaptive KER proxy): |CVD vel 10 s| >= 3 000 USD/s, price velocity 15 s >= 0.025 % the same way,
   CVD 60 s the same sign.  Bucketed by KER: >= 0.42 (open today), 0.362-0.42 (what the form formula unlocks at
   score 0.99, incl. hysteresis band), 0.15-0.362, < 0.15.  Bracket SOL 0.35 / 0.88 %, DOGE 0.50 / 1.25 % (asset floors).
Entry: post-only at the last price, filled if a print goes THROUGH it within 3 s (cons) or touches it (opt).  Exit:
TP / SL on prints, else taker at the last print after 300 s.  One position per symbol and lane, 30 s cool-down.
Unfilled = 0.  Split: dev = 2026-10-01..05, holdout = 2026-10-06..07 (fixed before running).
Limits: no book (queue, spread, depth unknown), the engine's swarm vote / OFI / soft exits are not replicated, mainnet
prints (the demo venue fills differently), one week of data.
"""
import argparse
import io
import json
import math
import random
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

MAKER, TAKER, SLIP = 2.0, 5.0, 1.0
HOLD, COOL, TTL = 300.0, 30.0, 3.0
HOLDOUT = ('2026-10-06', '2026-10-07')
MR_VARIANTS = {'CUR': None, 'F22_10': (0.22, 0.10), 'F24_11': (0.24, 0.11), 'F26_12': (0.26, 0.12),
               'R29_10': (0.29, 0.10), 'R31_12': (0.31, 0.12)}
TREND_BR = {'SOLUSDT': (0.88, 0.35), 'DOGEUSDT': (1.25, 0.50)}
KER_BUCKETS = (('open>=0.42', 0.42, 9.0), ('formula_0.362-0.42', 0.362, 0.42), ('chop_0.15-0.362', 0.15, 0.362),
               ('dead<0.15', -1.0, 0.15))


def net_rr(tp, sl):
    return (tp - 2 * MAKER / 100) / (sl + (MAKER + TAKER + SLIP) / 100)


def load(path):
    with zipfile.ZipFile(path) as z:
        df = pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
    t = df['transact_time'].to_numpy() / 1000.0
    p = df['price'].to_numpy(float)
    q = df['quantity'].to_numpy(float)
    s = np.where(df['is_buyer_maker'].astype(str).str.lower().to_numpy() == 'true', -1.0, 1.0)
    return t, p, q, s, df


def features(t, p, q, s, df):
    g = np.arange(math.ceil(t[0]) + 130, math.floor(t[-1]) - HOLD - 10, 1.0)
    ig = np.searchsorted(t, g, 'right') - 1
    pg = p[ig]
    cpq, cq, cs = np.concatenate([[0], np.cumsum(p * q)]), np.concatenate([[0], np.cumsum(q)]), \
        np.concatenate([[0], np.cumsum(s * p * q)])
    i60 = np.searchsorted(t, g - 60, 'right')
    i10 = np.searchsorted(t, g - 10, 'right')
    vol = cq[ig + 1] - cq[i60]
    vwap = np.where(vol > 0, (cpq[ig + 1] - cpq[i60]) / np.maximum(vol, 1e-12), pg)
    dev = (pg / vwap - 1) * 100
    vel10 = (cs[ig + 1] - cs[i10]) / 10.0
    cvd60 = cs[ig + 1] - cs[i60]
    ser = pd.Series(p, index=pd.to_datetime(t, unit='s'))
    hi = ser.rolling('60s').max().to_numpy()[ig]
    lo = ser.rolling('60s').min().to_numpy()[ig]
    rng = (hi - lo) / lo * 100
    pos = np.where(hi > lo, (pg - lo) / np.maximum(hi - lo, 1e-12), 0.5)
    # KER: 5 s closes over 120 s on the 1 s grid (index i - 5k)
    n = len(g)
    d5 = np.zeros(n)
    d5[5:] = np.abs(pg[5:] - pg[:-5])
    path = np.zeros(n)
    for k in range(24):
        sh = 5 * k
        path[sh:] += d5[:n - sh] if sh else d5
    net = np.zeros(n)
    net[120:] = np.abs(pg[120:] - pg[:-120])
    ker = np.where(path > 0, net / np.maximum(path, 1e-12), 0.0)
    ker[:125] = np.nan
    pv15 = np.full(n, np.nan)
    pv15[15:] = (pg[15:] / pg[:-15] - 1) * 100
    return g, pg, dict(dev=dev, vel10=vel10, cvd60=cvd60, rng=rng, pos=pos, ker=ker, pv15=pv15)


def simulate(t, p, side, t0, px0, tp_pct, sl_pct, model):
    """Post-only entry at px0 from t0; -> None (unfilled) or net bps."""
    d = 1 if side == 'BUY' else -1
    i = np.searchsorted(t, t0, 'right')
    j = np.searchsorted(t, t0 + TTL, 'right')
    seg = p[i:j]
    hit = np.nonzero(d * (seg - px0) < 0 if model == 'cons' else d * (seg - px0) <= 0)[0]
    if not len(hit):
        return None, None
    k = i + hit[0]
    tf = t[k]
    tp, sl = px0 * (1 + d * tp_pct / 100), px0 * (1 - d * sl_pct / 100)
    e = np.searchsorted(t, tf + HOLD, 'right')
    seg = p[k + 1:e]
    sl_hit = np.nonzero(d * (seg - sl) <= 0)[0]
    tp_hit = np.nonzero(d * (seg - tp) > 0 if model == 'cons' else d * (seg - tp) >= 0)[0]
    a = sl_hit[0] if len(sl_hit) else 10 ** 9
    b = tp_hit[0] if len(tp_hit) else 10 ** 9
    if a == b == 10 ** 9:
        last = p[e - 1] if e - 1 > k else px0
        return d * (last / px0 - 1) * 1e4 - MAKER - TAKER, t[e - 1] if e - 1 > k else tf
    if a <= b:
        return -sl_pct * 100 - MAKER - TAKER - SLIP, t[k + 1 + a]
    return tp_pct * 100 - 2 * MAKER, t[k + 1 + b]


def run_lane(t, p, g, pg, sig_idx, sides, brk, model):
    out, busy = [], -1e18
    for i, side in zip(sig_idx, sides):
        if g[i] < busy:
            continue
        tp, sl = brk(i)
        net, t_end = simulate(t, p, side, g[i], pg[i], tp, sl, model)
        out.append({'t': float(g[i]), 'side': side, 'f': net is not None, 'net': 0.0 if net is None else float(net)})
        busy = (t_end + COOL) if net is not None else g[i] + 5.0
    return out


def block_ci(xs, n_boot=2000, seed=3):
    n = len(xs)
    if n < 10:
        return None
    b = max(1, round(math.sqrt(n)))
    rng, ms = random.Random(seed), []
    for _ in range(n_boot):
        o = []
        while len(o) < n:
            s0 = rng.randrange(0, n - b + 1)
            o += xs[s0:s0 + b]
        ms.append(sum(o[:n]) / n)
    ms.sort()
    return [round(ms[int(.025 * n_boot)], 2), round(ms[int(.975 * n_boot) - 1], 2)]


def summ(rows):
    if not rows:
        return {'n': 0}
    fills = [r for r in rows if r['f']]
    nets = [r['net'] for r in rows]
    fn = [r['net'] for r in fills]
    return {'n': len(rows), 'fills': len(fills), 'win_rate': round(sum(x > 0 for x in fn) / len(fn), 3) if fn else None,
            'net_per_fill_bps': round(sum(fn) / len(fn), 2) if fn else None, 'ci95_fill': block_ci(fn),
            'net_per_opp_bps': round(sum(nets) / len(nets), 2)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--json', default='')
    a = ap.parse_args()
    res = {}
    for f in sorted(Path(a.data).glob('*-aggTrades-*.zip')):
        sym, day = f.name.split('-aggTrades-')[0], f.name.split('-aggTrades-')[1][:10]
        split = 'holdout' if day in HOLDOUT else 'dev'
        t, p, q, s, df = load(f)
        g, pg, F = features(t, p, q, s, df)
        # A. MR
        mr = (F['ker'] < 0.38) & (np.abs(F['dev']) >= 0.12) & (F['rng'] <= 0.40) & \
             (((F['dev'] < 0) & (F['pos'] <= 0.20)) | ((F['dev'] > 0) & (F['pos'] >= 0.80)))
        idx = np.nonzero(mr)[0]
        sides = ['SELL' if F['dev'][i] > 0 else 'BUY' for i in idx]
        for name, v in MR_VARIANTS.items():
            brk = (lambda i: (min(0.18, max(0.12, abs(F['dev'][i]))), 0.08)) if v is None else (lambda i, v=v: v)
            for model in ('cons', 'opt'):
                res.setdefault(f'MR|{name}|{model}|{split}', []).extend(run_lane(t, p, g, pg, idx, sides, brk, model))
        # B. TREND proxy by KER bucket
        sgn = np.sign(F['vel10'])
        tr = (np.abs(F['vel10']) >= 3000.0) & (sgn * F['pv15'] >= 0.025) & (np.sign(F['cvd60']) == sgn) & \
             ~np.isnan(F['ker'])
        tp_, sl_ = TREND_BR[sym]
        for name, lo, hi in KER_BUCKETS:
            idx = np.nonzero(tr & (F['ker'] >= lo) & (F['ker'] < hi))[0]
            sides = ['BUY' if sgn[i] > 0 else 'SELL' for i in idx]
            for model in ('cons', 'opt'):
                res.setdefault(f'TREND|{name}|{model}|{split}', []).extend(
                    run_lane(t, p, g, pg, idx, sides, lambda i: (tp_, sl_), model))
        res.setdefault('_time_share', []).append({'sym': sym, 'day': day, 'ker_lt_038': float(np.nanmean(F['ker'] < 0.38)),
                                                 'mr_signal_s': int(mr.sum()), 'grid_s': len(g)})
        print(f.name, len(t), 'ker<0.38 share', round(float(np.nanmean(F['ker'] < 0.38)), 3), 'mr sec', int(mr.sum()),
              flush=True)
    out = {'config': {'maker': MAKER, 'taker': TAKER, 'slip': SLIP, 'hold_s': HOLD, 'ttl_s': TTL, 'holdout': HOLDOUT,
                      'mr_variants_net_rr': {k: (None if v is None else round(net_rr(*v), 3)) for k, v in
                                             MR_VARIANTS.items()}, 'cur_net_rr_max': round(net_rr(0.18, 0.08), 3)},
           'time_share': res.pop('_time_share'),
           'results': {k: summ(v) for k, v in sorted(res.items()) if v}}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(json.dumps(out['config']))
    for k, v in out['results'].items():
        print(f'{k:45s} {v}')


if __name__ == '__main__':
    main()
