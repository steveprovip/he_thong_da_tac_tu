"""Download Binance USD-M 1m klines after the monthly archive on disk ends (2026-11-14, MLS-2).  Public data, no keys.

    python tools/fetch_klines_fresh.py --symbols BTCUSDT,ETHUSDT --start 2026-10-01 --out data/klines/fresh

Finished months come as the monthly zip, the running month (and a finished month whose monthly zip is not published
yet) as daily zips up to yesterday UTC; a symbol stops at its first unpublished day.  Every zip is checked against the
archive's own .CHECKSUM (SHA-256) and written atomically; a verified file is skipped, so the run can be resumed.
File names match data/klines_1m (`SYMBOL-1m-YYYY-MM[-DD].zip`), so research.smc_fibo_backtest.load_1m reads both.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.fetch_klines_1m import fetch  # noqa: E402

BASE = 'https://data.binance.vision/data/futures/um'


def wanted(start: dt.date, end: dt.date) -> list:
    """-> [('monthly', 'YYYY-MM') | ('daily', 'YYYY-MM-DD')] covering start .. end; months before end's month are monthly."""
    out, d = [], start
    while d <= end:
        if (d.year, d.month) < (end.year, end.month) and d.day == 1:
            out.append(('monthly', f'{d:%Y-%m}'))
            d = (d.replace(day=28) + dt.timedelta(days=4)).replace(day=1)
        else:
            out.append(('daily', f'{d:%Y-%m-%d}'))
            d += dt.timedelta(days=1)
    return out


def month_days(ym: str, end: dt.date) -> list:
    d, out = dt.date.fromisoformat(ym + '-01'), []
    while f'{d:%Y-%m}' == ym and d <= end:
        out.append(('daily', f'{d:%Y-%m-%d}'))
        d += dt.timedelta(days=1)
    return out


def get(kind: str, symbol: str, stamp: str, out: Path) -> str:
    """-> 'have' | 'done' | 'missing' (not published: UNKNOWN, never an empty file)."""
    name = f'{symbol}-1m-{stamp}.zip'
    dst = out / name
    if dst.exists() and (out / (name + '.sha256')).exists():
        return 'have'
    url = f'{BASE}/{kind}/klines/{symbol}/1m/{name}'
    try:
        want = fetch(url + '.CHECKSUM', tries=2).decode().split()[0]
        blob = fetch(url, tries=2)
    except Exception:
        return 'missing'
    got = hashlib.sha256(blob).hexdigest()
    if got != want:
        raise SystemExit(f'checksum mismatch {name}: {got} != {want}')
    tmp = dst.with_suffix('.part')
    tmp.write_bytes(blob)
    tmp.replace(dst)
    (out / (name + '.sha256')).write_text(got)
    return 'done'


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--symbols', required=True)
    ap.add_argument('--start', default='2026-10-01')
    ap.add_argument('--end', default=None, help='last day, default yesterday UTC')
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'klines' / 'fresh')
    a = ap.parse_args(argv)
    end = dt.date.fromisoformat(a.end) if a.end else dt.datetime.now(dt.timezone.utc).date() - dt.timedelta(days=1)
    a.out.mkdir(parents=True, exist_ok=True)
    total = 0
    for s in [x.strip() for x in a.symbols.split(',') if x.strip()]:
        todo, got, last = wanted(dt.date.fromisoformat(a.start), end), {'have': 0, 'done': 0}, None
        while todo:
            kind, stamp = todo.pop(0)
            r = get(kind, s, stamp, a.out)
            if r == 'missing' and kind == 'monthly':
                todo = month_days(stamp, end) + todo             # monthly zip not out yet: take that month day by day
                continue
            if r == 'missing':
                break                                            # first unpublished day: later ones are not there either
            got[r] += 1
            last = stamp
        total += sum((a.out / f).stat().st_size for f in [p.name for p in a.out.glob(f'{s}-1m-*.zip')])
        print(f'{s}: {got["done"]} downloaded, {got["have"]} already there, last {last}', flush=True)
    print(f'{total / 1e6:.1f} MB of zips in {a.out}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
