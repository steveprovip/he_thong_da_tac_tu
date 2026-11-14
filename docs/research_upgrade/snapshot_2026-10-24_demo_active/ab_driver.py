"""Run arms through tools/ab_bench.run_chunk and KEEP the full per-position rows (ab_bench --json drops them).

    python ab_driver.py OUT.json '{"B": {"sets": []}, "C": {"sets": ["chop_scalper=\\"shadow\\""]}}' CHUNKS AGG_DIR JOBS
    python ab_driver.py --report OUT.json [OUT2.json ...]   -> per-arm / per-path / per-exit tables + paired verdicts
"""
import concurrent.futures as cf
import json
import random
import sys
from collections import defaultdict
from pathlib import Path

REPO = Path(r'F:\he_thong_da_tac_tu_2')
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / 'tools'))
import ab_bench as ab  # noqa: E402


def run(out, arms, chunks, data, jobs, source='agg', l2_dir=None, warmup=60.0):
    jl = [(arm, ch, source, Path(data), 'SOLUSDT', warmup, cfg.get('sets', []), cfg.get('code'), l2_dir, None, 5000.0)
          for arm, cfg in arms.items() for ch in ab.parse_chunks(chunks)]
    res = []
    with cf.ThreadPoolExecutor(max_workers=jobs) as ex:
        for r in ex.map(ab.run_chunk, jl):
            res.append(r)
            print(r['arm'], r['chunk'], 'n', r['summary'].get('n', 0), 'net', round(r['summary'].get('net_u', 0.0), 4),
                  f"{r['wall_s']:.0f}s", flush=True)
    Path(out).write_text(json.dumps(res, default=float))


def ci(xs, n=4000, seed=7):
    if len(xs) < 2:
        return float('nan'), float('nan')
    rnd = random.Random(seed)
    ms = sorted(sum(rnd.choice(xs) for _ in xs) / len(xs) for _ in range(n))
    return ms[int(0.025 * n)], ms[int(0.975 * n)]


def line(label, pos):
    bps = [p['bps'] for p in pos]
    if not bps:
        return f'{label}: n=0'
    lo, hi = ci(bps)
    return (f'{label}: n={len(bps)} mean {sum(bps) / len(bps):+.2f} bps CI95 [{lo:+.2f}, {hi:+.2f}] '
            f'win {sum(1 for x in bps if x > 0) / len(bps):.0%} net {sum(p.get("pnl_u", 0) for p in pos):+.3f} U')


def report(paths):
    res = [r for p in paths for r in json.loads(Path(p).read_text())]
    arms = sorted({r['arm'] for r in res})
    by = {a: {tuple(r['chunk']): r for r in res if r['arm'] == a} for a in arms}
    for a in arms:
        pos = [dict(p, pnl_u=p.get('pnl', 0.0)) for r in by[a].values() for p in r['positions']]
        hours = sum(len(r['hourly_trading']) for r in by[a].values())
        print(f'=== arm {a}: {len(by[a])} chunks, {hours} trading hours, {len(pos) / max(hours, 1) * 24:.1f} trades/day')
        print('  ' + line('ALL', pos))
        for key in ('path', 'reason'):
            g = defaultdict(list)
            for p in pos:
                g[p.get(key)].append(p)
            for k, ps in sorted(g.items(), key=lambda kv: -len(kv[1])):
                print('    ' + line(f'{key}={k}', ps))
    base = arms[0]
    for a in arms[1:]:
        ha, hb = [], []
        for ch in by[base]:
            if ch in by[a]:
                k = min(len(by[base][ch]['hourly_trading']), len(by[a][ch]['hourly_trading']))
                ha += by[base][ch]['hourly_trading'][:k]
                hb += by[a][ch]['hourly_trading'][:k]
        v = ab.verdict({'a': ha, 'b': hb}, ab.register_rows() + ab.logged_trials() + 1)
        print(f'--- paired {a} - {base}: units {v.get("n")} sum {base} {v.get("sum_a", 0):+.4f} U, {a} {v.get("sum_b", 0):+.4f} U'
              f' | mean diff {v.get("mean", float("nan")):+.4g} U/h CI95 {v.get("ci95")} | {v.get("decision")}')


if __name__ == '__main__':
    if sys.argv[1] == '--report':
        report(sys.argv[2:])
    else:
        kw = {}
        if len(sys.argv) > 6:
            kw = dict(source=sys.argv[6], l2_dir=Path(sys.argv[7]), warmup=float(sys.argv[8]))
        run(sys.argv[1], json.loads(sys.argv[2]), sys.argv[3], sys.argv[4], int(sys.argv[5]), **kw)
