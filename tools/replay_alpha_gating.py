"""Replay of the "alpha gating" proposals on Binance USD-M public aggTrades (data.binance.vision). Read-only.

    python tools/replay_alpha_gating.py --data <dir with SYMBOL-aggTrades-YYYY-MM-DD.zip> [--json out.json]

Entries = the TREND flow proxy of tools/replay_chop_lanes.py (|CVD vel 10 s| >= 3 000 USD/s, price velocity 15 s
>= 0.025 % the same way, CVD 60 s the same sign); exits = the engine's current MARIN (tools/replay_exit_policies.py
PRE + TP1 50 % at +0.30 %).  Variants on the SAME candidate stream (one position per symbol, 30 s cool-down):
  BASE    the proxy as is
  SHIELD  hard macro CVD shield: no BUY if CVD 60 s < -300 k USD or CVD 3 m < -500 k; no SELL if > +300 k / +500 k
  ALIGN3  three layers with the trade: CVD 3 m, CVD 60 s and CVD 15 s all the same sign as the side
  EARLY   BASE + early adverse invalidation: within 45 s of the fill, CVD velocity 10 s >= 50 000 USD/s AGAINST
          the position -> maker exit at the touch (filled by the next print on our side)
  ALIGN3_EARLY
Order-book imbalance (layer 3 of the request) is not in aggTrades: not tested.  Fees maker 2, taker 5 + 1 slip.
Split dev 2026-10-01..05, holdout 2026-10-06..07 (fixed before running).  Mainnet prints; the demo venue differs.
"""
import argparse
import json
import math
import random
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import replay_chop_lanes as rcl  # noqa: E402
import replay_exit_policies as rx  # noqa: E402

HOLD, COOL, TTL = 300.0, 30.0, 3.0
HOLDOUT = ('2026-10-06', '2026-10-07')
SL = {'SOLUSDT': 0.35, 'DOGEUSDT': 0.50}
EARLY_S, EARLY_VEL = 45.0, 50_000.0
VARIANTS = ('BASE', 'SHIELD', 'ALIGN3', 'EARLY', 'ALIGN3_EARLY')


def early_exit(t, p, s, cs, k0, entry, d, maker):
    """-> (net bps excl. entry fee, index) if the early rule fires and the maker exit fills, else None."""
    n, end = len(t), t[k0] + EARLY_S
    k = k0 + 1
    while k < n and t[k] <= end:
        j = np.searchsorted(t, t[k] - 10.0, 'left')
        vel = (cs[k + 1] - cs[j]) / 10.0
        if d * vel <= -EARLY_VEL:
            px0 = p[k]
            m = k + 1
            while m < n and t[m] <= t[k] + 30.0:          # maker at the touch: next print on our side at >= our px
                if (s[m] > 0 if d > 0 else s[m] < 0) and d * (p[m] - px0) >= 0:
                    return d * (px0 / entry - 1) * 1e4 - maker, m
                m += 1
            return None
        k += 1
    return None


def block_ci(xs, n_boot=2000, seed=5):
    n = len(xs)
    if n < 10:
        return None
    b = max(1, round(math.sqrt(n)))
    rng, ms = random.Random(seed), []
    for _ in range(n_boot):
        o = []
        while len(o) < n:
            j = rng.randrange(0, n)
            o += [xs[(j + i) % n] for i in range(b)]
        ms.append(sum(o[:n]) / n)
    ms.sort()
    return [round(ms[int(.025 * n_boot)], 2), round(ms[int(.975 * n_boot) - 1], 2)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--data', required=True)
    ap.add_argument('--json', default='')
    a = ap.parse_args()
    maker, taker, slip = 2.0, 5.0, 1.0
    res = {(v, sp): [] for v in VARIANTS for sp in ('dev', 'holdout')}
    for f in sorted(Path(a.data).glob('*-aggTrades-*.zip')):
        sym, day = f.name.split('-aggTrades-')[0], f.name.split('-aggTrades-')[1][:10]
        split = 'holdout' if day in HOLDOUT else 'dev'
        t, p, q, s, df = rcl.load(f)
        g, pg, F = rcl.features(t, p, q, s, df)
        cs = np.concatenate([[0], np.cumsum(s * p * q)])
        ig = np.searchsorted(t, g, 'right') - 1
        c3m = cs[ig + 1] - cs[np.searchsorted(t, g - 180, 'right')]
        c15 = cs[ig + 1] - cs[np.searchsorted(t, g - 15, 'right')]
        sgn = np.sign(F['vel10'])
        base = (np.abs(F['vel10']) >= 3000.0) & (sgn * F['pv15'] >= 0.025) & (np.sign(F['cvd60']) == sgn)
        shield_ok = np.where(sgn > 0, (F['cvd60'] >= -3e5) & (c3m >= -5e5), (F['cvd60'] <= 3e5) & (c3m <= 5e5))
        align = (np.sign(c3m) == sgn) & (np.sign(F['cvd60']) == sgn) & (np.sign(c15) == sgn)
        masks = {'BASE': base, 'SHIELD': base & shield_ok, 'ALIGN3': base & align, 'EARLY': base,
                 'ALIGN3_EARLY': base & align}
        for v in VARIANTS:
            busy = -1e18
            for i in np.nonzero(masks[v])[0]:
                if g[i] < busy:
                    continue
                d = 1 if sgn[i] > 0 else -1
                px0 = pg[i]
                a0, b0 = np.searchsorted(t, g[i], 'right'), np.searchsorted(t, g[i] + TTL, 'right')
                hit = np.nonzero(d * (p[a0:b0] - px0) < 0)[0]
                if not len(hit):
                    busy = g[i] + 5.0
                    continue
                k0 = a0 + hit[0]
                ex = early_exit(t, p, s, cs, k0, px0, d, maker) if v.endswith('EARLY') else None
                if ex is not None:
                    net, why, t_end = ex[0], 'EARLY', t[ex[1]]
                else:
                    net, why = rx.run_exit(t, p, s, k0, px0, d, SL[sym], 0.30, True, maker, taker, slip)
                    t_end = t[min(k0 + 1, len(t) - 1)] + HOLD
                res[(v, split)].append({'net': net - maker, 'why': why, 'sym': sym})
                busy = t_end + COOL
        print(f.name, {v: len(res[(v, split)]) for v in VARIANTS}, flush=True)
    out = {'config': {'maker': maker, 'taker': taker, 'slip': slip, 'early_s': EARLY_S, 'early_vel_usd_s': EARLY_VEL,
                      'shield': {'cvd60': 3e5, 'cvd3m': 5e5}, 'holdout': HOLDOUT}, 'results': {}}
    for (v, sp), rows in res.items():
        xs = [r['net'] for r in rows]
        whys = {}
        for r in rows:
            whys[r['why']] = whys.get(r['why'], 0) + 1
        out['results'][f'{sp}|{v}'] = {'n': len(xs), 'net_bps': round(float(np.mean(xs)), 3) if xs else None,
                                       'ci95': block_ci(xs), 'win_rate': round(float(np.mean([x > 0 for x in xs])), 3)
                                       if xs else None, 'sum_bps': round(float(np.sum(xs)), 1), 'exits': whys}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding='utf-8')
    for k, v in sorted(out['results'].items()):
        print(f'{k:22s} {v}')


if __name__ == '__main__':
    main()
