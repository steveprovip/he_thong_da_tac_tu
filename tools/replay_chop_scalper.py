"""Replay of the proposed chop micro-scalper on recorded mainnet bookTicker (data/l2/<SYM>/<day>.jsonl). Read-only.

    python tools/replay_chop_scalper.py [--files ...] [--json out.json]

Rule (FORM ACTIVE_CONTINUOUS_TRADING_ENGINE, Pillar 1): when KER(2 min, 5 s buckets) < 0.40 and spread <= 1.5 bps,
post a post-only entry at the touch (BUY at the bid / SELL at the ask, both sides evaluated), TTL 3 s.
Exit: resting maker TP at entry +/- 0.08 %, exchange stop at -0.12 % (taker at the touch), and after 90 s a maker
exit at entry +/- 0.04 % (fee break-even); still open at 180 s -> taker at the touch. Fees maker 2 / taker 5 bps.

Fill models (no trades recorded, so both are reported):
  cons : a resting order fills only when the opposite best price trades through it
  opt  : also fills after resting >= 1 s as the best price on its side
No queue position, no impact, mainnet September books (not the demo venue).
"""
import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from replay_soft_exit import ROOT, block_ci, load_ticker, tick_of  # noqa: E402

DEFAULT_FILES = [f'data/l2/{s}/{d}.jsonl' for s in ('SOLUSDT', 'LINKUSDT', 'BNBUSDT')
                 for d in ('20260908', '20260909', '20260910')]
CFG = {'ker_max': 0.40, 'spread_max_bps': 1.5, 'ttl_s': 3.0, 'tp_pct': 0.08, 'sl_pct': 0.12, 'be_after_s': 90.0,
       'be_pct': 0.04, 'hard_s': 180.0, 'maker_bps': 2.0, 'taker_bps': 5.0, 'every_s': 20.0}


def ker_series(t, mid, span=120.0, bucket=5.0):
    """KER at each index from 5 s bucket closes over the last span seconds (None while warming)."""
    out, closes, j = [None] * len(t), [], 0
    last_b = None
    for i in range(len(t)):
        b = math.floor(t[i] / bucket)
        if last_b is None or b != last_b:
            closes.append((t[i], mid[i]))
            last_b = b
        else:
            closes[-1] = (t[i], mid[i])
        while closes and t[i] - closes[0][0] > span:
            closes.pop(0)
        if len(closes) >= 20:
            path = sum(abs(closes[k][1] - closes[k - 1][1]) for k in range(1, len(closes)))
            out[i] = abs(closes[-1][1] - closes[0][1]) / path if path > 0 else 0.0
    return out


def rest_fill(t, b, a, j, side, px, model, until):
    """Index where a resting order (side = the order's side) at px fills, else None."""
    since = None
    while j < len(t) and t[j] <= until:
        opp = a[j] if side == 'BUY' else b[j]
        own = b[j] if side == 'BUY' else a[j]
        if (side == 'BUY' and opp <= px) or (side == 'SELL' and opp >= px):
            return j
        if model == 'opt':
            if own == px:
                since = t[j] if since is None else since
                if t[j] - since >= 1.0:
                    return j
            else:
                since = None
        j += 1
    return None


def trade(t, b, a, i0, d, model, cfg):
    side = 'BUY' if d > 0 else 'SELL'
    entry = b[i0] if d > 0 else a[i0]
    jf = rest_fill(t, b, a, i0 + 1, side, entry, model, t[i0] + cfg['ttl_s'])
    if jf is None:
        return None
    t0 = t[jf]
    tp = entry * (1 + d * cfg['tp_pct'] / 100)
    sl = entry * (1 - d * cfg['sl_pct'] / 100)
    be = entry * (1 + d * cfg['be_pct'] / 100)
    exit_side = 'SELL' if d > 0 else 'BUY'
    j = jf + 1
    since = None
    while j < len(t):
        bid, ask = b[j], a[j]
        touch_out = bid if d > 0 else ask                    # where a taker exit trades
        age = t[j] - t0
        target = tp if age < cfg['be_after_s'] else be
        opp = ask if exit_side == 'BUY' else bid
        own = ask if exit_side == 'SELL' else bid
        if (exit_side == 'SELL' and opp >= target) or (exit_side == 'BUY' and opp <= target):
            return {'gross_bps': d * (target / entry - 1) * 1e4, 'how': 'tp' if target == tp else 'be', 'fee': 4.0}
        if model == 'opt':
            if own == round(target, 10) or abs(own - target) < 1e-12:
                since = t[j] if since is None else since
                if t[j] - since >= 1.0:
                    return {'gross_bps': d * (target / entry - 1) * 1e4, 'how': 'tp' if target == tp else 'be',
                            'fee': 4.0}
            else:
                since = None
        if d * (touch_out - sl) <= 0:
            return {'gross_bps': d * (touch_out / entry - 1) * 1e4, 'how': 'sl', 'fee': 7.0}
        if age >= cfg['hard_s']:
            return {'gross_bps': d * (touch_out / entry - 1) * 1e4, 'how': 'timeout', 'fee': 7.0}
        j += 1
    return None


def run(files, cfg):
    rep, allx = {'config': cfg, 'files': {}}, {'cons': [], 'opt': []}
    for f in files:
        p = ROOT / f
        if not p.exists():
            continue
        t, b, a = load_ticker(p)
        if len(t) < 1000:
            continue
        mid = [(x + y) / 2 for x, y in zip(b, a)]
        ker = ker_series(t, mid)
        res = {'cons': [], 'opt': []}
        attempts = 0
        last = -1e18
        for i in range(len(t)):
            if t[i] - last < cfg['every_s'] or ker[i] is None or ker[i] >= cfg['ker_max']:
                continue
            if (a[i] - b[i]) / mid[i] * 1e4 > cfg['spread_max_bps']:
                continue
            last = t[i]
            for d in (1, -1):
                attempts += 1
                for m in ('cons', 'opt'):
                    r = trade(t, b, a, i, d, m, cfg)
                    if r:
                        r['net_bps'] = r['gross_bps'] - r['fee']
                        r['_t'] = t[i]
                        res[m].append(r)
        rep['files'][f] = {'attempts': attempts, **{m: summary(res[m], attempts) for m in res}}
        for m in res:
            allx[m] += res[m]
        rep['files'][f]['_attempts'] = attempts
    tot = sum(v['_attempts'] for v in rep['files'].values())
    rep['all'] = {'attempts': tot, **{m: summary(allx[m], tot) for m in allx}}
    for m in allx:
        xs = [r['net_bps'] for r in sorted(allx[m], key=lambda r: r['_t'])]
        rep['all'][m]['net_ci95_block'] = block_ci(xs)
    return rep


def summary(rs, attempts):
    if not rs:
        return {'fills': 0, 'fill_rate': 0.0}
    xs = sorted(r['net_bps'] for r in rs)
    how = {}
    for r in rs:
        how[r['how']] = how.get(r['how'], 0) + 1
    return {'fills': len(rs), 'fill_rate': round(len(rs) / attempts, 3) if attempts else None,
            'mean_net_bps': round(sum(xs) / len(xs), 3), 'median_net_bps': round(xs[len(xs) // 2], 3),
            'win_rate': round(sum(x > 0 for x in xs) / len(xs), 3), 'p5_net_bps': round(xs[int(.05 * (len(xs) - 1))], 3),
            'how': how}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--files', nargs='*', default=DEFAULT_FILES)
    ap.add_argument('--json', default=None)
    a = ap.parse_args()
    rep = run(a.files, dict(CFG))
    s = json.dumps(rep, indent=1)
    if a.json:
        Path(a.json).write_text(s, encoding='utf-8')
    print(s)


if __name__ == '__main__':
    main()
