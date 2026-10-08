"""Replay of MARIN exit policies on Binance USD-M public aggTrades (data.binance.vision daily zips). Read-only.

    python tools/replay_exit_policies.py --data <dir with SYMBOL-aggTrades-YYYY-MM-DD.zip> [--json out.json]

Entries: the TREND flow proxy of tools/replay_chop_lanes.py (|CVD vel 10 s| >= 3 000 USD/s, price velocity 15 s
>= 0.025 % the same way, CVD 60 s the same sign), post-only entry filled when a print goes through it within 3 s,
one position per symbol, 30 s cool-down.  The SAME entries are replayed under every exit policy.

Exit state machine on prints (long shown; short mirrored), hold <= 300 s then taker at the last print:
  hard SL 0.35 % (SOL) / 0.50 % (DOGE) exchange stop, taker 5 bps + 1 slip;
  exchange ratchet: MFE >= 0.15 % -> stop at entry + 0.08 % (taker when hit);
  soft tiers (maker exit at the touch once the level is crossed, re-pegged with the price, never below the
  exchange stop, filled by the next BUYER-initiated print at or above our price): LOCK MFE 0.10 -> +0.04 %,
  BE MFE 0.12 -> +0.06 %, PROFIT MFE 0.25 -> +0.15 %, TRAIL from MFE 0.50 % at peak - 0.20 %;
  TP1: 50 % resting maker at the TP, filled by a print through it.
Policies:
  CUR      as the engine runs today (TP1 0.88 % SOL / 1.25 % DOGE): with the ratchet armed the exchange stop
           (+0.08 %) sits ABOVE the soft BE level (+0.06 %) -> a retrace hits the taker stop first (trade #49)
  PRE      pre-stop maker: once the exchange stop is in profit every soft level is >= stop + 0.04 %
  TP30     CUR + TP1 50 % at +0.30 % maker
  (--cons: maker exits fill only on a print THROUGH our price; default: a buyer print AT it)
  PRE_TP30 PRE + TP1 50 % at +0.30 %
Fees: maker 2 bps, taker 5 bps + 1 bps slip (config); demo ledger shows taker 4 bps (sensitivity: --taker 4).
Split: dev 2026-10-01..05, holdout 2026-10-06..07.  Limits: no book (touch = last print), no queue, mainnet prints.
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

HOLD, COOL, TTL = 300.0, 30.0, 3.0
CONS = False                     # --cons: the maker exit needs a buyer print strictly THROUGH our price
HOLDOUT = ('2026-10-06', '2026-10-07')
SL = {'SOLUSDT': 0.35, 'DOGEUSDT': 0.50}
TP = {'SOLUSDT': 0.88, 'DOGEUSDT': 1.25}
POLICIES = {'CUR': (False, None), 'PRE': (True, None), 'TP30': (False, 0.30), 'PRE_TP30': (True, 0.30)}


def run_exit(t, p, s, k0, entry, d, sl, tp1, pre, maker, taker, slip):
    """One position from index k0 (fill). -> (net bps on the full size, exit reason)."""
    end = t[k0] + HOLD
    stop = -sl                       # exchange stop, % from entry
    mfe, level, exiting, rem, real = 0.0, None, False, 1.0, 0.0
    peak_tr = None
    k = k0 + 1
    n = len(t)
    last = entry
    while k < n and t[k] <= end:
        px = p[k]
        last = px
        fav = d * (px / entry - 1) * 100
        mfe = max(mfe, fav)
        if tp1 is not None and rem > 0.75 and fav > tp1:                       # TP1 50 % resting maker
            real += 0.5 * (tp1 * 100 - maker)
            rem = 0.5
        if fav <= stop:                                                         # exchange stop (taker)
            return real + rem * (stop * 100 - taker - slip), 'STOP' if stop > 0 else 'SL'
        if mfe >= 0.15 and stop < 0.08:
            stop = 0.08
        cands = []
        if mfe >= 0.10:
            cands.append(0.04)
        if mfe >= 0.12:
            cands.append(0.06)
        if mfe >= 0.25:
            cands.append(0.15)
        if mfe >= 0.50:
            peak_tr = mfe - 0.20
            cands.append(peak_tr)
        if pre and stop > 0:
            cands.append(stop + 0.04)
        if cands:
            level = max(cands)
        if level is not None and not exiting and fav <= level:
            exiting = True
        if exiting:
            order = max(stop, min(level, fav))                                  # maker at the touch, floor stop
            if s[k] > 0 and (fav > order if CONS else fav >= order):            # a buyer lifts our offer
                return real + rem * (order * 100 - maker), 'SOFT'
        k += 1
    return real + rem * (d * (last / entry - 1) * 1e4 - taker), 'TIME'


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
    ap.add_argument('--taker', type=float, default=5.0)
    ap.add_argument('--cons', action='store_true')
    a = ap.parse_args()
    global CONS
    CONS = a.cons
    maker, taker, slip = 2.0, a.taker, 1.0
    rows = []
    for f in sorted(Path(a.data).glob('*-aggTrades-*.zip')):
        sym, day = f.name.split('-aggTrades-')[0], f.name.split('-aggTrades-')[1][:10]
        split = 'holdout' if day in HOLDOUT else 'dev'
        t, p, q, s, df = rcl.load(f)
        g, pg, F = rcl.features(t, p, q, s, df)
        sgn = np.sign(F['vel10'])
        sig = (np.abs(F['vel10']) >= 3000.0) & (sgn * F['pv15'] >= 0.025) & (np.sign(F['cvd60']) == sgn)
        busy = -1e18
        for i in np.nonzero(sig)[0]:
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
            r = {'sym': sym, 'day': day, 'split': split, 't': float(g[i])}
            for name, (pre, tp1) in POLICIES.items():
                net, why = run_exit(t, p, s, k0, px0, d, SL[sym], tp1 if tp1 is not None else TP[sym], pre,
                                    maker, taker, slip)
                r[name], r[name + '_why'] = net - maker, why                    # entry maker fee
            rows.append(r)
            busy = t[min(k0 + 1, len(t) - 1)] + HOLD + COOL
        print(f.name, len(rows), flush=True)
    out = {'config': {'maker_fill': 'through' if CONS else 'touch', 'maker': maker, 'taker': taker, 'slip': slip, 'hold_s': HOLD, 'holdout': HOLDOUT, 'sl': SL,
                      'tp1_cur': TP}, 'results': {}}
    for split in ('dev', 'holdout'):
        sel = [r for r in rows if r['split'] == split]
        for name in POLICIES:
            xs = [r[name] for r in sel]
            diff = [r[name] - r['CUR'] for r in sel]
            whys = {}
            for r in sel:
                whys[r[name + '_why']] = whys.get(r[name + '_why'], 0) + 1
            out['results'][f'{split}|{name}'] = {'n': len(xs), 'net_bps': round(sum(xs) / len(xs), 3) if xs else None,
                                                 'ci95': block_ci(xs), 'win_rate': round(sum(x > 0 for x in xs) / len(xs), 3)
                                                 if xs else None, 'diff_vs_cur': round(sum(diff) / len(diff), 3)
                                                 if diff else None, 'ci95_diff': block_ci(diff), 'exits': whys}
        # trades like #49: MFE reached the ratchet and the exit was the exchange stop under CUR
    sub = [r for r in rows if r['CUR_why'] == 'STOP']
    out['ratchet_stop_cases'] = {name: {'n': len(sub), 'net_bps': round(sum(r[name] for r in sub) / len(sub), 3)
                                        if sub else None,
                                        'exits': {w: sum(r[name + '_why'] == w for r in sub) for w in
                                                  ('STOP', 'SOFT', 'SL', 'TIME')}} for name in POLICIES}
    if a.json:
        Path(a.json).write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(json.dumps(out['config']))
    for k, v in out['results'].items():
        print(f'{k:18s} {v}')
    print('ratchet-stop cases:', json.dumps(out['ratchet_stop_cases']))


if __name__ == '__main__':
    main()
