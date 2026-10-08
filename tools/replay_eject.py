"""Replay of the time-decay eject on recorded mainnet bookTicker (data/l2). Read-only.

    python tools/replay_eject.py [--files ...] [--json out.json]

Synthetic positions (long and short at the mid every --every s; entry fee maker) with the SOL asset bracket
(SL 0.35 % stop -> taker, TP 0.875 % resting maker). Exit policies compared on the same positions:
  A  current : age >= 180 s and MFE < 0.06 % -> maker exit at fee-true BE (entry +/- 4 bps), re-pegged to the
               touch after 360 s
  B  fast    : as A, plus age >= 120 s, MFE < 0.08 % and |15 s velocity| < 0.025 % -> maker exit at the touch
Random entries carry no edge: the comparison isolates the cost of the exit mechanics (fees, spread, timing).
Fill models as tools/replay_soft_exit.py (cons: trade-through only; opt: + 1 s at the best price).
"""
import argparse
import bisect
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_soft_exit import ROOT, block_ci, load_ticker, tick_of  # noqa: E402

FILES = [f'data/l2/{s}/{d}.jsonl' for s in ('SOLUSDT', 'LINKUSDT', 'BNBUSDT') for d in ('20260909', '20260910')]
CFG = {'sl': 0.35, 'tp': 0.875, 'a_s': 180.0, 'a_mfe': 0.06, 'a_hard': 360.0, 'be_bps': 4.0,
       'b_s': 120.0, 'b_mfe': 0.08, 'b_vel': 0.025, 'max_s': 1800.0, 'maker': 2.0, 'taker': 5.0}


def sim(t, b, a, i0, d, policy, model, cfg):
    mid0 = (b[i0] + a[i0]) / 2
    entry = mid0
    sl, tp = entry * (1 - d * cfg['sl'] / 100), entry * (1 + d * cfg['tp'] / 100)
    be = entry * (1 + d * cfg['be_bps'] / 1e4)
    mfe, exit_px, posted, since, kind = 0.0, None, None, None, None
    j = i0 + 1
    while j < len(t):
        bid, ask = b[j], a[j]
        mid = (bid + ask) / 2
        age = t[j] - t[i0]
        mfe = max(mfe, d * (mid / entry - 1) * 100)
        opp, own = (bid, ask) if d > 0 else (ask, bid)                # exit is a SELL for longs
        if (d > 0 and bid >= tp) or (d < 0 and ask <= tp):
            return d * (tp / entry - 1) * 1e4 - 2 * cfg['maker'], 'tp'
        if d * (opp - sl) <= 0:
            return d * (opp / entry - 1) * 1e4 - cfg['maker'] - cfg['taker'], 'sl'
        if exit_px is None:
            k = bisect.bisect_left(t, t[j] - 15.0, 0, j)
            vel = abs(mid / ((b[k] + a[k]) / 2) - 1) * 100
            if policy == 'B' and age >= cfg['b_s'] and mfe < cfg['b_mfe'] and vel < cfg['b_vel']:
                kind, exit_px, posted = 'fast', own, t[j]
            elif age >= cfg['a_s'] and mfe < cfg['a_mfe']:
                kind, posted = 'be', t[j]
                exit_px = max(own, be) if d > 0 else min(own, be)
        else:
            if kind == 'be' and age >= cfg['a_hard']:
                kind = 'touch'
            want = own if kind in ('fast', 'touch') else (max(own, be) if d > 0 else min(own, be))
            if (d > 0 and opp >= exit_px) or (d < 0 and opp <= exit_px):
                return d * (exit_px / entry - 1) * 1e4 - 2 * cfg['maker'], kind
            if model == 'opt' and own == exit_px:
                since = since or t[j]
                if t[j] - since >= 1.0:
                    return d * (exit_px / entry - 1) * 1e4 - 2 * cfg['maker'], kind
            elif own != exit_px:
                since = None
            if want != exit_px and t[j] - posted >= 0.5:
                exit_px, posted, since = want, t[j], None
        if age > cfg['max_s']:
            return d * (opp / entry - 1) * 1e4 - cfg['maker'] - cfg['taker'], 'timeout'
        j += 1
    return None


def run(files, cfg, every=30.0):
    out, pairs = {'config': cfg, 'files': {}}, {'cons': [], 'opt': []}
    for f in files:
        p = ROOT / f
        if not p.exists():
            continue
        t, b, a = load_ticker(p)
        rec, last = {'cons': [], 'opt': []}, -1e18
        for i0 in range(len(t)):
            if t[i0] - last < every:
                continue
            last = t[i0]
            for d in (1, -1):
                for m in ('cons', 'opt'):
                    ra, rb = sim(t, b, a, i0, d, 'A', m, cfg), sim(t, b, a, i0, d, 'B', m, cfg)
                    if ra and rb:
                        rec[m].append((t[i0], ra, rb))
        out['files'][f] = {m: summ(rec[m]) for m in rec}
        for m in rec:
            pairs[m] += rec[m]
    out['all'] = {m: summ(pairs[m]) for m in pairs}
    for m in pairs:
        diff = [rb[0] - ra[0] for _, ra, rb in sorted(pairs[m])]
        out['all'][m]['paired_B_minus_A'] = block_ci(diff)
    return out


def summ(rec):
    if not rec:
        return {'n': 0}
    a = [x[1][0] for x in rec]
    bb = [x[2][0] for x in rec]
    how = {}
    for x in rec:
        how[x[2][1]] = how.get(x[2][1], 0) + 1
    changed = sum(1 for x in rec if x[1] != x[2])
    return {'n': len(rec), 'A_mean_net_bps': round(sum(a) / len(a), 3), 'B_mean_net_bps': round(sum(bb) / len(bb), 3),
            'changed': changed, 'B_exit_mix': how}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--files', nargs='*', default=FILES)
    ap.add_argument('--json', default=None)
    ap.add_argument('--every', type=float, default=30.0)
    a = ap.parse_args()
    rep = run(a.files, dict(CFG), a.every)
    s = json.dumps(rep, indent=1)
    if a.json:
        Path(a.json).write_text(s, encoding='utf-8')
    print(s)


if __name__ == '__main__':
    main()
