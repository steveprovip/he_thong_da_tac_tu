"""Offline replay of research.micro_scanner over L2 files recorded by research/l2_recorder.py.

    python tools/replay_micro_scanner.py data/l2/BTCUSDT/20260909.jsonl data/l2/SOLUSDT/20260909.jsonl ...
        [--hours 4] [--obi 0.55] [--lead-bps 6] [--out artifacts/micro_replay]

Same engine, same code path as live, driven by recorded frames in local-time order
(heapq merge on "lt"), clock stepped every 0.25 s of recorded time: no lookahead.

Limits (stated in the output, not hidden):
  * recorder kept bookTicker + depth DIFFS; L5 needs a snapshot, so OBI here is the
    L1 queue imbalance (noisier, fires more often than live L5);
  * no aggTrade in the recording -> the CVD vote never fires and maker fills are
    book-through only (more conservative than live, which also fills on queue depletion).
Reads local files only. No network, no orders.
"""
import argparse
import heapq
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research import micro_scanner as ms  # noqa: E402


def frames(path):
    """Yield (lt, msg) for bookTicker lines; tolerate truncated lines."""
    with open(path, encoding='utf-8') as fh:
        for line in fh:
            if '"bookTicker"' not in line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            lt = d.get('lt')
            if lt is None:
                continue
            yield float(lt), {'data': d}


def replay(paths, cfg, hours=None, step=ms.STEP_SECONDS, state_file=None):
    symbols = []
    for p in paths:
        sym = Path(p).parent.name.upper()
        if sym not in symbols:
            symbols.append(sym)
    specs = {s: ms.DEFAULT_SPECS[s] for s in symbols if s in ms.DEFAULT_SPECS}
    sc = ms.MicroScanner(cfg, specs, symbols, state_file=state_file or Path('/dev/null'))
    sc.feed_label, sc.spec_source = 'replay-l2', 'static'
    merged = heapq.merge(*(frames(p) for p in paths), key=lambda x: x[0])
    t_first = t_next = None
    n = 0
    for lt, msg in merged:
        if t_first is None:
            t_first = t_next = lt
        if hours and lt - t_first > hours * 3600:
            break
        while t_next <= lt:
            sc.step(t_next)
            t_next += step
            if sc.killed:
                break
        if sc.killed:
            break
        sc.on_message(msg, lt)
        n += 1
    return sc, symbols, n, (t_first or 0.0), (t_next or 0.0)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('files', nargs='+')
    ap.add_argument('--hours', type=float, default=None)
    ap.add_argument('--obi', type=float, default=0.55)
    ap.add_argument('--lead-bps', type=float, default=6.0)
    ap.add_argument('--min-votes', type=int, default=2)
    ap.add_argument('--kill', type=float, default=1e9, help='default: no kill, measure the whole tape')
    ap.add_argument('--out', default=str(ROOT / 'artifacts' / 'micro_replay'))
    a = ap.parse_args(argv)
    cfg = ms.ScannerConfig(obi_threshold=a.obi, lead_move_bps=a.lead_bps, min_votes=a.min_votes,
                           kill_loss_usdt=a.kill)
    t0 = time.time()
    sc, symbols, n, t_first, t_last = replay(a.files, cfg, a.hours)
    span_h = max(1e-9, (t_last - t_first) / 3600)
    out = {'files': a.files, 'symbols': symbols, 'frames': n, 'hours': span_h,
           'config': {'obi': a.obi, 'lead_bps': a.lead_bps, 'min_votes': a.min_votes},
           'limits': 'OBI=L1 bookTicker imbalance; no aggTrade -> no CVD vote, book-through fills only',
           'intents': sc.intents, 'fills': sc.fills, 'cancels': sc.cancels,
           'intents_per_hour': sc.intents / span_h, 'fills_per_hour': sc.fills / span_h,
           'branches': {k: b.stats() for k, b in sc.branches.items()},
           'by_symbol': {}, 'elapsed_s': time.time() - t0}
    prim = sc.branches[ms.PRIMARY]
    for s in symbols:
        tr = [t for t in prim.trades if t.symbol == s]
        if tr:
            out['by_symbol'][s] = {'n': len(tr), 'mean_net_bps': sum(t.net_bps for t in tr) / len(tr),
                                   'reasons': {r: sum(1 for t in tr if t.reason == r)
                                               for r in ('TP', 'PROTECT', 'SL', 'TIME', 'KILL')}}
    Path(a.out).mkdir(parents=True, exist_ok=True)
    tag = '-'.join(Path(f).stem for f in a.files[:1]) + f'-obi{a.obi}-lead{a.lead_bps}-v{a.min_votes}'
    path = Path(a.out) / f'{tag}.json'
    path.write_text(json.dumps(ms._json_safe(out), indent=1), encoding='utf-8')
    print(f'{n} frames · {span_h:.2f} h · {", ".join(symbols)} · {out["elapsed_s"]:.0f}s wall')
    print(f'intents {sc.intents} ({out["intents_per_hour"]:.1f}/h) · fills {sc.fills} '
          f'({out["fills_per_hour"]:.1f}/h) · cancels {sc.cancels}')
    print(ms.summary(sc))
    for s, v in out['by_symbol'].items():
        print(f'  {s:<10} n={v["n"]:<3} mean {v["mean_net_bps"]:+.2f} bps  {v["reasons"]}')
    print(f'  limits: {out["limits"]}')
    print(f'  -> {path}')


if __name__ == '__main__':
    main()
