"""Download Binance USD-M monthly 1m kline archives for a universe file (10-10 f).  Public data, no keys.

    python tools/fetch_klines_1m.py --universe data/universe_months.json --out data/klines_1m

Per symbol: every month it is in the universe plus the month before (indicator warm-up).  Symbols are fetched in
order of how many months they are in the universe (the core ~20 first).  Every zip is checked against the archive's
own .CHECKSUM (SHA256) and written atomically; an existing verified file is skipped, so the run can be resumed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import time
import urllib.request
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://data.binance.vision/data/futures/um/monthly/klines'


def prev_month(ym: str) -> str:
    y, m = map(int, ym.split('-'))
    return f'{y - 1}-12' if m == 1 else f'{y}-{m - 1:02d}'


def plan(months: dict) -> list:
    """-> [(symbol, 'YYYY-MM')] most-frequent symbols first, months ascending."""
    freq = Counter(s for v in months.values() for s in v)
    need = {}
    for ym, syms in months.items():
        for s in syms:
            need.setdefault(s, set()).update((ym, prev_month(ym)))
    return [(s, ym) for s in sorted(need, key=lambda s: (-freq[s], s)) for ym in sorted(need[s])]


def fetch(url: str, tries: int = 4) -> bytes:
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'research'}),
                                        timeout=60) as r:
                return r.read()
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2 * (k + 1))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--universe', type=Path, default=ROOT / 'data' / 'universe_months.json')
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'klines_1m')
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    todo = plan(json.loads(a.universe.read_text())['months'])
    done = missing = 0
    for i, (s, ym) in enumerate(todo):
        name = f'{s}-1m-{ym}.zip'
        dst = a.out / name
        if dst.exists() and (a.out / (name + '.sha256')).exists():
            continue
        url = f'{BASE}/{s}/1m/{name}'
        try:
            want = fetch(url + '.CHECKSUM').decode().split()[0]
            blob = fetch(url)
        except Exception as e:                              # e.g. 404 before the listing month: UNKNOWN, not zero
            missing += 1
            print(f'  MISSING {name}: {e}', flush=True)
            continue
        got = hashlib.sha256(blob).hexdigest()
        if got != want:
            raise SystemExit(f'checksum mismatch {name}: {got} != {want}')
        tmp = dst.with_suffix('.part')
        tmp.write_bytes(blob)
        tmp.replace(dst)
        (a.out / (name + '.sha256')).write_text(got)
        done += 1
        if done % 25 == 0:
            print(f'  {i + 1}/{len(todo)} ({s} {ym})', flush=True)
    print(f'done: {done} downloaded, {missing} missing, {len(todo)} planned')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
