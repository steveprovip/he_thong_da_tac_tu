#!/usr/bin/env python3
"""tools/record_market.py --light recordings -> top-of-book quote lines the ATLAS L2 loop reads. OFFLINE.

The light recorder stores depth@100ms diffs, a REST depth snapshot every 600 s and aggTrade, but no bookTicker;
scripts/autoresearch_agent_swarm.py reads bookTicker lines only. This rebuilds the book (snapshot, then the diffs
newer than it, with the diffs received just before the snapshot replayed on top of it) and writes one line per diff:

    {"lt": <receive s>, "s": SYM, "ch": "bookTicker", "e": "bookTicker", "b", "B", "a", "A", "src": "depth-rebuild"}

Nothing is written before the first snapshot, while the book is crossed, or after a sequence gap (`pu` != the last
`u`) until the next snapshot. A file cut off mid-stream (recorder killed) is read up to the damage.

    python tools/rec_to_l2_touch.py --symbol DOGEUSDT [--rec data/recordings] [--out data/l2]
"""
from __future__ import annotations

import argparse
import gzip
import json
import sys
import zlib
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = 'depth-rebuild'


class TouchBook:
    """Price-level book; `touch()` -> (bid, bid qty, ask, ask qty) or None when unknown / crossed / out of sync."""

    def __init__(self, replay: int = 100):
        self.b, self.a = {}, {}
        self.u = None                       # last applied update id; None = no snapshot yet or lost sync
        self.recent = deque(maxlen=replay)  # diffs received lately: replayed on top of a snapshot that lags them

    def _apply(self, d: dict) -> None:
        for side, book in (('b', self.b), ('a', self.a)):
            for p, q in d.get(side, ()):
                p, q = float(p), float(q)
                if q <= 0:
                    book.pop(p, None)
                else:
                    book[p] = q
        self.u = d['u']

    def snapshot(self, d: dict) -> None:
        self.b = {float(p): float(q) for p, q in d.get('bids', ()) if float(q) > 0}
        self.a = {float(p): float(q) for p, q in d.get('asks', ()) if float(q) > 0}
        self.u = d['lastUpdateId']
        for old in self.recent:
            if old['u'] > self.u:
                self._apply(old)

    def diff(self, d: dict) -> None:
        self.recent.append(d)
        if self.u is None or d['u'] <= self.u:
            return                                       # no book yet, or already inside the snapshot
        if d.get('pu') is not None and d['pu'] > self.u:
            self.u = None                                # a diff is missing: unknown until the next snapshot
            return
        self._apply(d)

    def touch(self):
        if self.u is None or not self.b or not self.a:
            return None
        bb, ba = max(self.b), min(self.a)
        return (bb, self.b[bb], ba, self.a[ba]) if bb < ba else None


def lines(path: Path):
    """Parsed records of one hourly gz; stops quietly at a truncated tail."""
    try:
        with gzip.open(path, 'rt', encoding='utf-8') as f:
            for raw in f:
                try:
                    yield json.loads(raw)
                except ValueError:
                    continue
    except (EOFError, OSError, zlib.error):
        return


def convert(files, symbol: str, out_dir: Path) -> dict:
    """-> {'lines': n, 'seconds': distinct seconds with a touch, 'files': [...]}; one output file per UTC day."""
    book, out, day, n, secs, written = TouchBook(), None, None, 0, set(), []
    out_dir.mkdir(parents=True, exist_ok=True)
    try:
        for p in sorted(files):
            for r in lines(Path(p)):
                k, d = r.get('s', ''), r.get('d')
                if k == 'snapshot':
                    book.snapshot(d)
                elif k.startswith('depth'):
                    book.diff(d)
                else:
                    continue
                t = book.touch()
                if t is None:
                    continue
                ts = r['r'] / 1e9
                dd = datetime.fromtimestamp(ts, timezone.utc).strftime('%Y%m%d')
                if dd != day:
                    if out is not None:
                        out.close()
                    day = dd
                    written.append(str(out_dir / f'{dd}.jsonl'))
                    out = open(written[-1], 'w', encoding='utf-8')
                out.write(json.dumps({'lt': ts, 's': symbol, 'ch': 'bookTicker', 'e': 'bookTicker', 'b': repr(t[0]),
                                      'B': repr(t[1]), 'a': repr(t[2]), 'A': repr(t[3]), 'src': SRC},
                                     separators=(',', ':')) + '\n')
                n += 1
                secs.add(int(ts))
    finally:
        if out is not None:
            out.close()
    return {'lines': n, 'seconds': len(secs), 'files': written}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--symbol', required=True)
    ap.add_argument('--rec', default=str(ROOT / 'data' / 'recordings'))
    ap.add_argument('--out', default=str(ROOT / 'data' / 'l2'))
    a = ap.parse_args(argv)
    files = sorted((Path(a.rec) / a.symbol).glob('*.jsonl.gz'))
    if not files:
        raise SystemExit(f'no recordings under {Path(a.rec) / a.symbol}')
    rep = convert(files, a.symbol, Path(a.out) / a.symbol)
    print(json.dumps({'symbol': a.symbol, 'inputs': len(files), **rep}))
    return 0


if __name__ == '__main__':
    sys.exit(main())
