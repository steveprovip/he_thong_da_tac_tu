#!/usr/bin/env python3
"""Download Binance USD-M public daily aggTrades (data.binance.vision), verify the published SHA-256, keep only a compact array file.

    python -I tools/fetch_aggtrades.py --symbols DOGEUSDT,XRPUSDT,1000PEPEUSDT --start 2026-10-12 --end 2026-11-08 --out data/aggtrades_20261110 [--max-mb 600] [--dry-run]

Per (symbol, day): <out>/<SYMBOL>/<day>.npz with t (int64 ms), p (float64), q (float32 coins), m (bool is_buyer_maker, True = a seller hit the
bid) and <out>/manifest.json with the zip's SHA-256, its size and the row count.  The zip is deleted after the conversion.  Public data, GET only,
no key; sequential, ~1 s apart.  Everything downloaded is untrusted: the archive must hold exactly one CSV member of a bounded size, the
numbers are parsed with pandas under fixed dtypes, rows are validated (positive price / quantity, non-decreasing time) before anything is saved.
--dry-run only sends HEAD requests and prints the total size, which --max-mb checks BEFORE the first download."""
import argparse
import datetime as dt
import hashlib
import io
import json
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd

BASE = 'https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip'
UA = {'User-Agent': 'fetch-aggtrades/1'}
MAX_MEMBER_BYTES = 3_000_000_000
COLS = ['agg_trade_id', 'price', 'quantity', 'first_trade_id', 'last_trade_id', 'transact_time', 'is_buyer_maker']


def days(start: str, end: str) -> list:
    a, b = dt.date.fromisoformat(start), dt.date.fromisoformat(end)
    return [(a + dt.timedelta(days=i)).isoformat() for i in range((b - a).days + 1)]


def head(url: str, opener=urllib.request.urlopen) -> int:
    with opener(urllib.request.Request(url, method='HEAD', headers=UA), timeout=20) as r:
        return int(r.headers.get('Content-Length') or 0)


def get(url: str, opener=urllib.request.urlopen) -> bytes:
    with opener(urllib.request.Request(url, headers=UA), timeout=120) as r:
        return r.read()


def parse_zip(blob: bytes) -> dict:
    """One CSV member -> validated arrays.  Raises ValueError on anything unexpected."""
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        infos = [i for i in z.infolist() if not i.is_dir()]
        if len(infos) != 1 or not infos[0].filename.endswith('.csv') or infos[0].file_size > MAX_MEMBER_BYTES:
            raise ValueError(f'unexpected archive layout: {[(i.filename, i.file_size) for i in infos][:3]}')
        with z.open(infos[0]) as fh:
            first = fh.readline().decode('ascii', 'replace')
            has_header = first.lower().startswith('agg_trade_id')
        with z.open(infos[0]) as fh:
            df = pd.read_csv(fh, header=0 if has_header else None, names=None if has_header else COLS,
                             usecols=['price', 'quantity', 'transact_time', 'is_buyer_maker'],
                             dtype={'price': 'float64', 'quantity': 'float64', 'transact_time': 'int64', 'is_buyer_maker': 'str'})
    t, p, q = df['transact_time'].to_numpy(), df['price'].to_numpy(), df['quantity'].to_numpy()
    m = (df['is_buyer_maker'].str.strip().str.lower() == 'true').to_numpy(bool)
    if len(t) == 0 or not (np.isfinite(p).all() and np.isfinite(q).all() and (p > 0).all() and (q > 0).all()):
        raise ValueError('empty or non-positive / non-finite rows')
    if (np.diff(t) < 0).any():
        order = np.argsort(t, kind='stable')                    # aggTrade ids are ordered; times can tie but should not go back: sort defensively
        t, p, q, m = t[order], p[order], q[order], m[order]
    return {'t': t.astype(np.int64), 'p': p, 'q': q.astype(np.float32), 'm': m}


def checksum_ok(blob: bytes, text: str) -> bool:
    want = text.strip().split()[0].lower() if text.strip() else ''
    return len(want) == 64 and hashlib.sha256(blob).hexdigest() == want


def run(symbols, start, end, out: Path, max_mb: float, dry: bool, opener=urllib.request.urlopen, sleep=time.sleep, log=print) -> dict:
    jobs = [(s, d) for s in symbols for d in days(start, end)]
    sizes = {}
    for s, d in jobs:
        try:
            sizes[(s, d)] = head(BASE.format(s=s, d=d), opener)
        except Exception as exc:                                # a day that is not published (yet) is skipped, not fatal
            log(f'skip {s} {d}: {type(exc).__name__}')
    total = sum(sizes.values()) / 1e6
    log(f'{len(sizes)} of {len(jobs)} archives published, {total:.0f} MB')
    if dry:
        return {'files': len(sizes), 'mb': total}
    if total > max_mb:
        raise SystemExit(f'{total:.0f} MB > --max-mb {max_mb:g}: trim --start / --symbols first')
    out.mkdir(parents=True, exist_ok=False)
    manifest = []
    for (s, d) in sizes:
        url = BASE.format(s=s, d=d)
        blob = get(url, opener)
        if not checksum_ok(blob, get(url + '.CHECKSUM', opener).decode('ascii', 'replace')):
            raise SystemExit(f'checksum mismatch: {s} {d}')
        arr = parse_zip(blob)
        (out / s).mkdir(exist_ok=True)
        np.savez_compressed(out / s / f'{d}.npz', **arr)
        manifest.append({'symbol': s, 'day': d, 'zip_bytes': len(blob), 'sha256': hashlib.sha256(blob).hexdigest(), 'rows': int(len(arr['t']))})
        log(f'{s} {d}: {len(blob) / 1e6:.1f} MB zip, {len(arr["t"])} rows')
        sleep(1.0)
    (out / 'manifest.json').write_text(json.dumps({'source': BASE, 'fetched_at': time.time(), 'files': manifest}), encoding='utf-8')
    return {'files': len(manifest), 'mb': total}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--symbols', required=True)
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--out', required=True, type=Path)
    ap.add_argument('--max-mb', type=float, default=600.0)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    run([s.strip().upper() for s in a.symbols.split(',') if s.strip()], a.start, a.end, a.out, a.max_mb, a.dry_run)
    return 0


if __name__ == '__main__':
    sys.exit(main())
