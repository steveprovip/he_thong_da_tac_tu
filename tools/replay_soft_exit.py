"""Replay of MARIN's protective soft exit on recorded mainnet bookTicker (data/l2/<SYM>/<day>.jsonl). Read-only.

    python tools/replay_soft_exit.py [--files data/l2/SOLUSDT/20260909.jsonl ...] [--json out.json]

Question (DOGE #37): once the fee-shield BE target is armed (MFE >= be_trigger) and price falls back to it,
what does each exit policy realise after fees?
  A  chase   : post-only SELL at the ask, re-peg every 0.5 s, no deadline (code before 2026-10-07r)
  B  bounded : A, but reduce-only taker at the bid once mid is slip% past the target or after deadline_s while
               mid is still at/past it (code after 2026-10-07r)
  C  stop    : exchange STOP_MARKET at the target (taker at the first bid <= target)
Entries are synthetic longs and shorts at the mid every --every seconds (entry fee = maker). Only events that
arm the BE tier and then come back to the target count. The hard SL (--sl) bounds all policies.

Fill model limits (stated, not hidden): the recorder kept bookTicker + depth diffs, no trades. A resting maker
exit fills only when the opposite best price trades through it ("conservative"); "optimistic" also fills it
when it has been the best price for >= 1 s. No queue position, no market impact; taker fills at the touch +
--slip_bps. Mainnet September books, not the demo venue and not DOGE.
"""
import argparse
import bisect
import json
import math
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FILES = [f'data/l2/{s}/{d}.jsonl' for s in ('SOLUSDT', 'LINKUSDT', 'BNBUSDT') for d in ('20260909', '20260910')]


def load_ticker(path: Path):
    t, b, a = [], [], []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if '"bookTicker"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue                                       # torn line (two records concatenated): skip
            bid, ask = float(r['b']), float(r['a'])
            if bid <= 0 or ask <= bid:
                continue
            t.append(r['E'] / 1000.0)
            b.append(bid)
            a.append(ask)
    return t, b, a


def tick_of(prices):
    diffs = sorted({round(abs(x - y), 10) for x, y in zip(prices[:5000], prices[1:5001]) if x != y})
    return diffs[0] if diffs else 0.01


def simulate(t, b, a, tick, d, i0, cfg):
    """One synthetic position opened at index i0, side d (+1 long, -1 short). -> dict per policy or None."""
    n = len(t)
    m0 = (b[i0] + a[i0]) / 2
    entry = m0
    arm = entry * (1 + d * cfg['be_trigger'] / 100)
    sl = entry * (1 - d * cfg['sl'] / 100)
    tgt = entry * (1 + d * cfg['target'] / 100)
    tgt = math.ceil(tgt / tick) * tick if d > 0 else math.floor(tgt / tick) * tick
    i, armed = i0, False
    t_end = t[i0] + cfg['arm_window_s']
    while i < n and t[i] <= t_end:
        mid = (b[i] + a[i]) / 2
        if d * (mid - sl) <= 0:
            return None                                        # stopped before arming: not this question
        if d * (mid - arm) >= 0:
            armed = True
            break
        i += 1
    if not armed:
        return None
    while i < n and t[i] <= t_end + cfg['max_hold_s']:
        mid = (b[i] + a[i]) / 2
        if d * (mid - tgt) <= 0:
            break
        i += 1
    else:
        return None
    if i >= n:
        return None
    s0 = i
    out = {}
    taker = lambda j: (b[j] if d > 0 else a[j]) * (1 - d * cfg['slip_bps'] / 1e4)
    # C: exchange stop at the target -> taker at the touch now (trigger already crossed)
    out['C_stop'] = (taker(s0), 'taker')
    # A / B: maker at the exit touch, re-peg every 0.5 s
    for name, bounded in (('A_chase', False), ('B_bounded', True)):
        for model in ('cons', 'opt'):
            j, px, posted, best_since = s0, (a[s0] if d > 0 else b[s0]), t[s0], t[s0]
            res = None
            while j < n:
                mid = (b[j] + a[j]) / 2
                opp = b[j] if d > 0 else a[j]                   # buyers' best (for our SELL) / sellers' best
                touch = a[j] if d > 0 else b[j]
                if d * (opp - px) >= 0:                         # traded through our resting exit
                    res = (px, 'maker')
                    break
                if model == 'opt' and touch == px and t[j] - best_since >= 1.0:
                    res = (px, 'maker')
                    break
                if d * (mid - sl) <= 0:
                    res = (taker(j), 'taker_sl')
                    break
                adverse = d * (tgt - mid) / tgt * 100
                if bounded and (adverse >= cfg['max_slip'] or (adverse >= 0 and t[j] - t[s0] >= cfg['deadline_s'])):
                    res = (taker(j), 'taker_esc')
                    break
                if t[j] - t[s0] > cfg['max_hold_s']:
                    res = (taker(j), 'taker_timeout')
                    break
                if touch != px and t[j] - posted >= 0.5:        # re-peg to the new touch
                    px, posted, best_since = touch, t[j], t[j]
                elif touch != px:
                    best_since = t[j]
                j += 1
            if res is None:
                return None
            out[f'{name}_{model}'] = res
    rows = {}
    for k, (px, how) in out.items():
        gross = d * (px / entry - 1) * 1e4
        fee = cfg['maker_bps'] + (cfg['maker_bps'] if how == 'maker' else cfg['taker_bps'])
        rows[k] = {'gross_bps': gross, 'net_bps': gross - fee, 'how': how}
    rows['_t'] = t[s0]
    return rows


def summarize(events, key):
    xs = sorted(e[key]['net_bps'] for e in events)
    if not xs:
        return None
    n = len(xs)
    how = {}
    for e in events:
        how[e[key]['how']] = how.get(e[key]['how'], 0) + 1
    return {'n': n, 'mean_net_bps': round(sum(xs) / n, 3), 'median_net_bps': round(xs[n // 2], 3),
            'p5_net_bps': round(xs[int(0.05 * (n - 1))], 3), 'worst_net_bps': round(xs[0], 3),
            'mean_gross_bps': round(sum(e[key]['gross_bps'] for e in events) / n, 3), 'how': how}


def run(files, cfg, every_s=20.0):
    report = {'config': cfg, 'files': {}}
    allev = []
    for f in files:
        path = ROOT / f
        if not path.exists():
            continue
        t, b, a = load_ticker(path)
        if len(t) < 1000:
            continue
        tick = tick_of(b)
        ev, last = [], -1e18
        for i0 in range(len(t)):
            if t[i0] - last < every_s:
                continue
            last = t[i0]
            for d in (1, -1):
                r = simulate(t, b, a, tick, d, i0, cfg)
                if r:
                    ev.append(r)
        keys = [k for k in (ev[0] if ev else {}) if not k.startswith('_')]
        report['files'][f] = {'ticks': len(t), 'tick': tick, 'events': len(ev),
                              **{k: summarize(ev, k) for k in keys}}
        allev += ev
    keys = [k for k in (allev[0] if allev else {}) if not k.startswith('_')]
    report['all'] = {'events': len(allev), **{k: summarize(allev, k) for k in keys}}
    # paired difference B - A (same events) with a block bootstrap over time-ordered events
    for model in ('cons', 'opt'):
        diff = [e[f'B_bounded_{model}']['net_bps'] - e[f'A_chase_{model}']['net_bps']
                for e in sorted(allev, key=lambda e: e['_t'])]
        report['all'][f'paired_B_minus_A_{model}'] = block_ci(diff)
    return report


def block_ci(xs, n_boot=3000, seed=11):
    import random
    n = len(xs)
    if n < 10:
        return None
    blk = max(1, round(math.sqrt(n)))
    rng, means = random.Random(seed), []
    for _ in range(n_boot):
        out = []
        while len(out) < n:
            s = rng.randrange(0, n - blk + 1)
            out.extend(xs[s:s + blk])
        means.append(sum(out[:n]) / n)
    means.sort()
    return {'n': n, 'mean_bps': round(sum(xs) / n, 3), 'ci95': [round(means[int(.025 * n_boot)], 3),
                                                               round(means[int(.975 * n_boot) - 1], 3)]}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--files', nargs='*', default=DEFAULT_FILES)
    ap.add_argument('--json', default=None)
    ap.add_argument('--every', type=float, default=20.0)
    ap.add_argument('--max_slip', type=float, default=0.03)
    ap.add_argument('--deadline_s', type=float, default=5.0)
    a = ap.parse_args()
    cfg = {'be_trigger': 0.12, 'target': 0.06, 'sl': 0.35, 'arm_window_s': 300.0, 'max_hold_s': 600.0,
           'max_slip': a.max_slip, 'deadline_s': a.deadline_s, 'maker_bps': 2.0, 'taker_bps': 5.0, 'slip_bps': 0.0}
    rep = run(a.files, cfg, a.every)
    s = json.dumps(rep, indent=1)
    if a.json:
        Path(a.json).write_text(s, encoding='utf-8')
    print(s)


if __name__ == '__main__':
    main()
