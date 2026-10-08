"""Offline GTX entry-placement replay on recorded MAINNET bookTicker (data/l2/<SYM>/<day>.jsonl). Read-only.

    python tools/replay_gtx_placement.py [--files ...] [--every 15] [--json out.json]

Question (form KHOP_LENH_NET_EV 10-08 §B): for the SAME opportunities, which entry placement gives the best net
result per opportunity after fees - not the best fill rate?  Every opportunity is counted, filled or not.

Opportunities (no engine signal is recorded in these files, so two proxies):
  momentum : every --every s, side = sign of the 15 s mid move when |move| >= 3 bps (TREND velocity proxy)
  neutral  : every --every s, alternating BUY/SELL (zero alpha: pure execution cost + adverse selection)

Policies (decision at t0 on the book at t0; the order is live after ACK_S = 0.15 s; a post-only order that would
cross at ACK is rejected, as -5022 on Binance):
  SKIP          no order (net 0)
  JOIN_T3/5/10  post at our touch, TTL 3/5/10 s, no re-peg
  PEG_T3        current engine: join, re-peg every 0.5 s while the touch walks away (max drift 0.15 %), one 3 s
                deadline from the first post; a re-post loses queue priority (new order at the back)
  IMPROVE_T3/5  1 tick inside when spread >= 2 ticks (front of a new level), else join
  IOC           taker at the opposite touch + 1 bps slip (SHADOW ONLY: never enabled by this study)

Fill models (no trades / order ids in the files; the displayed size is aggregate and our order is synthetic):
  clear : fills only when our price level is cleared (opposite touch reaches it or our side moves through it).
          Lower bound for a resting order.
  prop  : + queue ahead = displayed size at our price when we join; a size decrease at our level advances us by
          the share ahead/(displayed) (cancels spread over the queue; hftbacktest ProbQueue-like, linear).
  front : + every decrease at our level is ahead of us (upper bound: all are trades / cancels in front).

Per opportunity: fill, wait, implementation shortfall vs decision mid, markouts 1/5/30 s from the fill,
net_H = side * (mid(t0+H) - fill) / mid0 - entry fee - 2 bps common maker exit (H = 30, 60 s), 0 when unfilled.
Fees maker 2 / taker 5 bps (account tier, no rebate).  Paired block-bootstrap CI of (policy - PEG_T3) per
opportunity.  Split: dev = 2026-09-09/10, holdout = 2026-09-08 (pre-registered, no tuning on it).
Mainnet September books, no DOGE file, demo venue books differ (basis, queue size): not evidence about demo fills.
"""
import argparse
import bisect
import json
import math
import random
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SYMS = ('SOLUSDT', 'LINKUSDT', 'BNBUSDT')
DAYS = ('20260908', '20260909', '20260910')
HOLDOUT = '20260908'
DEFAULT_FILES = [f'data/l2/{s}/{d}.jsonl' for s in SYMS for d in DAYS]
ACK_S, MAKER, TAKER, SLIP, EXIT_BPS = 0.15, 2.0, 5.0, 1.0, 2.0
PEG_EVERY, PEG_DRIFT = 0.5, 0.15
POLICIES = ('SKIP', 'JOIN_T3', 'JOIN_T5', 'JOIN_T10', 'PEG_T3', 'IMPROVE_T3', 'IMPROVE_T5', 'IOC')
MODELS = ('clear', 'prop', 'front')
BASE = 'PEG_T3'


def load(path):
    t, b, a, bq, aq = [], [], [], [], []
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if '"bookTicker"' not in line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue                                  # torn line
            bid, ask = float(r['b']), float(r['a'])
            if bid <= 0 or ask <= bid:
                continue
            ts = r['E'] / 1000.0
            if t and ts < t[-1]:
                continue
            t.append(ts); b.append(bid); a.append(ask); bq.append(float(r['B'])); aq.append(float(r['A']))
    return t, b, a, bq, aq


def tick_of(prices):
    diffs = sorted({round(abs(x - y), 10) for x, y in zip(prices[:20000], prices[1:20001]) if x != y})
    return diffs[0] if diffs else 0.01


class Book:
    def __init__(self, t, b, a, bq, aq):
        self.t, self.b, self.a, self.bq, self.aq = t, b, a, bq, aq
        self.tick = tick_of(b)

    def idx(self, ts):
        return max(0, bisect.bisect_right(self.t, ts) - 1)

    def mid(self, ts):
        i = self.idx(ts)
        return (self.b[i] + self.a[i]) / 2


def rest(bk, side, px, t_on, t_end, model, i_from):
    """Resting post-only order at px live from t_on. -> (fill_time or None, rejected)."""
    t, b, a, bq, aq = bk.t, bk.b, bk.a, bk.bq, bk.aq
    j = bk.idx(t_on)
    own, opp = (b[j], a[j]) if side == 'BUY' else (a[j], b[j])
    if (side == 'BUY' and px >= opp) or (side == 'SELL' and px <= opp):
        return None, True                                  # would take liquidity at ACK: GTX rejected
    q = bq if side == 'BUY' else aq
    ahead = q[j] if abs(own - px) < 1e-12 else 0.0         # joined behind the displayed size; better level: front
    prev = q[j] if abs(own - px) < 1e-12 else 0.0
    j += 1
    n = len(t)
    while j < n and t[j] <= t_end:
        ob, oa = b[j], a[j]
        if side == 'BUY':
            if oa <= px or ob < px:
                return t[j], False
            at = abs(ob - px) < 1e-12
        else:
            if ob >= px or oa > px:
                return t[j], False
            at = abs(oa - px) < 1e-12
        if at and model != 'clear':
            cur = q[j]
            if cur < prev:
                dec = prev - cur
                ahead -= dec if model == 'front' else dec * (ahead / prev if prev > 0 else 1.0)
                if ahead <= 1e-12:
                    return t[j], False
            prev = cur
            ahead = min(ahead, cur)
        elif at:
            prev = q[j]
        j += 1
    return None, False


def run_policy(bk, pol, model, t0, side):
    """-> dict(filled, t_fill, px, fee, rejects, reprices)"""
    i0 = bk.idx(t0)
    bid, ask, tick = bk.b[i0], bk.a[i0], bk.tick
    d = 1 if side == 'BUY' else -1
    if pol == 'SKIP':
        return {'filled': False}
    if pol == 'IOC':
        j = bk.idx(t0 + ACK_S)
        touch = bk.a[j] if d > 0 else bk.b[j]
        return {'filled': True, 't_fill': t0 + ACK_S, 'px': touch * (1 + d * SLIP / 1e4), 'fee': TAKER}
    ttl = float(pol.split('_T')[1])
    if pol.startswith('IMPROVE'):
        wide = ask - bid >= 2 * tick - 1e-12
        px = (bid + tick if d > 0 else ask - tick) if wide else (bid if d > 0 else ask)
    else:
        px = bid if d > 0 else ask
    t_end = t0 + ACK_S + ttl
    if not pol.startswith('PEG'):
        tf, rej = rest(bk, side, px, t0 + ACK_S, t_end, model, i0)
        return {'filled': tf is not None, 't_fill': tf, 'px': px, 'fee': MAKER, 'rejects': int(rej)}
    px0, t_on, reprices = px, t0 + ACK_S, 0
    while t_on < t_end:
        seg_end = min(t_on + PEG_EVERY, t_end)
        tf, rej = rest(bk, side, px, t_on, seg_end, model, i0)
        if tf is not None:
            return {'filled': True, 't_fill': tf, 'px': px, 'fee': MAKER, 'reprices': reprices}
        if rej:
            return {'filled': False, 'rejects': 1, 'reprices': reprices}
        j = bk.idx(seg_end)
        new = bk.b[j] if d > 0 else bk.a[j]
        if d * (new - px) > 0 and abs(new / px0 - 1) * 100 <= PEG_DRIFT:
            px, reprices = new, reprices + 1                 # cancel + re-post: back of the new level
            t_on = seg_end + ACK_S
        else:
            # keep resting: continue the same order (queue state approximated by restarting at the level size;
            # conservative for prop/front, exact for clear)
            t_on = seg_end
    return {'filled': False, 'reprices': reprices}


def opportunities(bk, every, kind, warm=60.0):
    t0, out, k = bk.t[0] + warm, [], 0
    end = bk.t[-1] - 70.0
    while t0 < end:
        if kind == 'neutral':
            out.append((t0, 'BUY' if k % 2 == 0 else 'SELL'))
            k += 1
        else:
            m0, m15 = bk.mid(t0), bk.mid(t0 - 15.0)
            mv = (m0 / m15 - 1) * 1e4
            if abs(mv) >= 3.0:
                out.append((t0, 'BUY' if mv > 0 else 'SELL'))
        t0 += every
    return out


def evaluate(bk, opps):
    rows = []
    for t0, side in opps:
        d = 1 if side == 'BUY' else -1
        m0 = bk.mid(t0)
        mh = {h: bk.mid(t0 + h) for h in (30, 60)}
        r = {'t0': t0, 'side': side, 'missed30': d * (mh[30] / m0 - 1) * 1e4}
        for pol in POLICIES:
            for model in (MODELS if pol not in ('SKIP', 'IOC') else ('clear',)):
                x = run_policy(bk, pol, model, t0, side)
                key = f'{pol}|{model}'
                if x['filled']:
                    px, tf = x['px'], x['t_fill']
                    mk = {f'mk{h}': d * (bk.mid(tf + h) / px - 1) * 1e4 for h in (1, 5, 30)}
                    net = {f'net{h}': d * (mh[h] - px) / m0 * 1e4 - x['fee'] - EXIT_BPS for h in (30, 60)}
                    r[key] = {'f': 1, 'wait': tf - t0, 'is': d * (px / m0 - 1) * 1e4, **mk, **net,
                              'rej': x.get('rejects', 0)}
                else:
                    r[key] = {'f': 0, 'net30': 0.0, 'net60': 0.0, 'rej': x.get('rejects', 0)}
        # identical results for SKIP / IOC across models
        for pol in ('SKIP', 'IOC'):
            for model in MODELS[1:]:
                r[f'{pol}|{model}'] = r[f'{pol}|clear']
        rows.append(r)
    return rows


def block_ci(xs, n_boot=2000, seed=7):
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
    return [round(means[int(.025 * n_boot)], 3), round(means[int(.975 * n_boot) - 1], 3)]


def pct(xs, q):
    if not xs:
        return None
    xs = sorted(xs)
    return round(xs[min(len(xs) - 1, int(q * len(xs)))], 3)


def summarize(rows):
    out = {}
    if not rows:
        return out
    for pol in POLICIES:
        for model in MODELS:
            key = f'{pol}|{model}'
            xs = [r[key] for r in rows]
            fills = [x for x in xs if x['f']]
            n = len(xs)
            net = [x['net30'] for x in xs]
            base = [r[f'{BASE}|{model}']['net30'] for r in rows]
            diff = [p - q for p, q in zip(net, base)]
            avg = lambda k: round(sum(x[k] for x in fills) / len(fills), 3) if fills else None
            out[key] = {'n': n, 'fill_rate': round(len(fills) / n, 4), 'rejects': sum(x['rej'] for x in xs),
                        'wait_p50': pct([x['wait'] for x in fills], .5), 'wait_p95': pct([x['wait'] for x in fills], .95),
                        'is_bps': avg('is'), 'mk1': avg('mk1'), 'mk5': avg('mk5'), 'mk30': avg('mk30'),
                        'net30_per_opp': round(sum(net) / n, 3), 'net60_per_opp': round(sum(x['net60'] for x in xs) / n, 3),
                        'net30_per_fill': avg('net30'), 'tail_p5_fill': pct([x['net30'] for x in fills], .05),
                        'ci95_net30': block_ci(net), 'diff_vs_base': round(sum(diff) / n, 3),
                        'ci95_diff_vs_base': block_ci(diff) if pol != BASE else None}
    out['missed30_unfilled_base'] = {m: round(sum(r['missed30'] for r in rows if not r[f'{BASE}|{m}']['f'])
                                              / max(1, sum(1 for r in rows if not r[f'{BASE}|{m}']['f'])), 3)
                                     for m in MODELS}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--files', nargs='*', default=DEFAULT_FILES)
    ap.add_argument('--every', type=float, default=15.0)
    ap.add_argument('--json', default='')
    ap.add_argument('--fresh', action='store_true', help='ignore data/replay_cache')
    ap.add_argument('--only-cached', action='store_true', help='aggregate cached files only (no simulation)')
    args = ap.parse_args()
    groups = {}
    meta = []
    cache_dir = ROOT / 'data' / 'replay_cache'
    for f in args.files:
        p = ROOT / f
        if not p.exists():
            continue
        day = p.stem
        split = 'holdout' if day == HOLDOUT else 'dev'
        cache = cache_dir / f'gtx_{p.parent.name}_{day}_every{args.every:g}.json'
        if cache.exists() and not args.fresh:
            c = json.loads(cache.read_text(encoding='utf-8'))
        else:
            if args.only_cached:
                print(f'{f}: not cached', file=sys.stderr)
                continue
            bk = Book(*load(p))
            if len(bk.t) < 1000:
                continue
            c = {'meta': {'file': f, 'rows': len(bk.t), 'hours': round((bk.t[-1] - bk.t[0]) / 3600, 2),
                          'tick': bk.tick, 'split': split},
                 'rows': {k: evaluate(bk, opportunities(bk, args.every, k)) for k in ('momentum', 'neutral')}}
            cache_dir.mkdir(parents=True, exist_ok=True)
            cache.write_text(json.dumps(c), encoding='utf-8')
        meta.append(c['meta'])
        for kind, rows in c['rows'].items():
            groups.setdefault((kind, split), []).extend(rows)
            groups.setdefault((kind, split, p.parent.name), []).extend(rows)
        print(f'{f}: {c["meta"]["rows"]} rows', file=sys.stderr, flush=True)
    report = {'config': {'ack_s': ACK_S, 'maker_bps': MAKER, 'taker_bps': TAKER, 'slip_bps': SLIP,
                         'exit_bps': EXIT_BPS, 'peg_every_s': PEG_EVERY, 'peg_drift_pct': PEG_DRIFT,
                         'every_s': args.every, 'base': BASE, 'holdout': HOLDOUT},
              'files': meta, 'results': {'/'.join(k): summarize(v) for k, v in groups.items()}}
    if args.json:
        Path(args.json).write_text(json.dumps(report, indent=1), encoding='utf-8')
    for k in sorted(report['results']):
        if k.count('/') != 1:
            continue
        print('==', k)
        res = report['results'][k]
        for key, v in res.items():
            if '|' not in key:
                continue
            print(f'{key:18s} n={v["n"]:5d} fill={v["fill_rate"]:.3f} wait50={v["wait_p50"]} is={v["is_bps"]} '
                  f'mk5={v["mk5"]} net30/opp={v["net30_per_opp"]} ci={v["ci95_net30"]} '
                  f'd={v["diff_vs_base"]} ci_d={v["ci95_diff_vs_base"]}')


if __name__ == '__main__':
    main()
