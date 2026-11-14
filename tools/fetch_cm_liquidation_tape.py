#!/usr/bin/env python3
"""Historical liquidation tape for FLS-1-CM (2026-11-13): Binance COIN-M public archives -> the --liq recorder layout.

USD-M has NO liquidationSnapshot archive on data.binance.vision (listing checked 2026-11-13: um/daily holds aggTrades,
bookDepth, bookTicker, indexPriceKlines, klines, markPriceKlines, metrics, premiumIndexKlines, trades). COIN-M keeps
daily liquidationSnapshot files for 2023-06-25 .. 2024-10-14, plus aggTrades and bookTicker for the same days.

    python -I tools/fetch_cm_liquidation_tape.py --symbols SOLUSD_PERP,DOGEUSD_PERP --liq-only      # liquidations only
    python -I tools/fetch_cm_liquidation_tape.py --symbols SOLUSD_PERP --days 20240805,20240806 --max-mb 400

Writes under --out (default data/recordings/historical/cm):
    liquidations_<YYYYMMDD>.jsonl   {"r", "s": "forceOrder", "d": {...forceOrder payload...}, "book": null}
    <SYMBOL>/<YYYYMMDD-HH>.jsonl.gz {"r", "s": "aggTrade" | "bookTicker", "d": {...}}
so research/flash_liquidation_study.py reads it unchanged. Conversions (declared in the FLS-1-CM amendment):
  * quantities are contracts: z, l, q become coin-equivalent quantities (contracts x contractSize / price) so that
    z x ap is the USD notional, as on USD-M;
  * the archive repeats identical snapshot rows: exact duplicates are dropped;
  * r = exchange time (ns); liquidation rows get + --react-ms (default 250 ms): a bot learns of an event only after
    it is pushed, and the archive has no receive time.
Public data, GET only, no key; SHA-256 checked against the published .CHECKSUM; zips are not kept.
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import gzip
import io
import json
import re
import sys
import time
import urllib.request
import zipfile
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from fetch_aggtrades import checksum_ok, get  # noqa: E402  (same GET / SHA-256 helpers, neutral User-Agent)

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://data.binance.vision/data/futures/cm/daily/{k}/{s}/{s}-{k}-{d}.zip'
LIST = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision?prefix=data/futures/cm/daily/{k}/{s}/'
CONTRACT_USD = {'BTCUSD_PERP': 100.0, 'ETHUSD_PERP': 10.0, 'SOLUSD_PERP': 10.0, 'DOGEUSD_PERP': 10.0,
                'XRPUSD_PERP': 10.0}                       # dapi/v1/exchangeInfo contractSize, checked 2026-11-13
MAX_MEMBER_BYTES = 2_000_000_000


def listed_days(kind: str, sym: str, opener=urllib.request.urlopen) -> dict:
    """{YYYY-MM-DD: zip bytes} from the bucket listing (paged)."""
    out, marker = {}, ''
    while True:
        body = get(LIST.format(k=kind, s=sym) + (f'&marker={marker}' if marker else ''), opener)
        keys = re.findall(rb'<Key>([^<]+)</Key>', body)
        sizes = re.findall(rb'<Size>([^<]+)</Size>', body)
        for k, sz in zip(keys, sizes):
            k = k.decode()
            if k.endswith('.zip'):
                out[k.rsplit(f'-{kind}-', 1)[1][:10]] = int(sz)
        if b'<IsTruncated>true' in body and keys:
            marker = keys[-1].decode()
        else:
            return out


def fetch_csv(kind: str, sym: str, day: str, opener=urllib.request.urlopen) -> list:
    url = BASE.format(k=kind, s=sym, d=day)
    blob = get(url, opener)
    if not checksum_ok(blob, get(url + '.CHECKSUM', opener).decode('ascii', 'replace')):
        raise ValueError(f'SHA-256 mismatch {url}')
    z = zipfile.ZipFile(io.BytesIO(blob))
    names = z.namelist()
    if len(names) != 1 or z.getinfo(names[0]).file_size > MAX_MEMBER_BYTES:
        raise ValueError(f'unexpected archive layout {url}')
    text = z.read(names[0]).decode('utf-8', 'replace')
    rows = list(csv.reader(io.StringIO(text)))
    if rows and rows[0] and not rows[0][0].strip().lstrip('-').isdigit():
        rows = rows[1:]                                     # header line
    return rows


def liquidation_lines(rows: list, sym: str, react_ms: int) -> list:
    """time,side,order_type,time_in_force,original_quantity,price,average_price,order_status,last_fill_quantity,
    accumulated_fill_quantity (contracts) -> recorder forceOrder lines; exact duplicate rows dropped."""
    size = CONTRACT_USD[sym]
    seen, out = set(), []
    for r in rows:
        key = tuple(r)
        if len(r) < 10 or key in seen:
            continue
        seen.add(key)
        t, side, otype, tif, q, p, ap, st, lq, zq = r[:10]
        try:
            t, p, ap, q, lq, zq = int(t), float(p), float(ap), float(q), float(lq), float(zq)
        except ValueError:
            continue
        if ap <= 0 or zq <= 0 or side not in ('BUY', 'SELL'):
            continue
        coin = lambda c: c * size / ap                      # contracts -> coin-equivalent, so z x ap = USD notional
        d = {'e': 'forceOrder', 'E': t, 'o': {'s': sym, 'S': side, 'o': otype, 'f': tif, 'q': f'{coin(q):.8f}',
                                              'p': f'{p:g}', 'ap': f'{ap:g}', 'X': st, 'l': f'{coin(lq):.8f}',
                                              'z': f'{coin(zq):.8f}', 'T': t}}
        out.append(((t + react_ms) * 1_000_000,
                    json.dumps({'r': (t + react_ms) * 1_000_000, 's': 'forceOrder', 'd': d, 'book': None},
                               separators=(',', ':'))))
    out.sort(key=lambda x: x[0])
    return out


def trade_lines(rows: list) -> list:
    """agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker -> aggTrade lines."""
    out = []
    for r in rows:
        try:
            t, p = int(r[5]), float(r[1])
        except (ValueError, IndexError):
            continue
        out.append((t * 1_000_000, json.dumps({'r': t * 1_000_000, 's': 'aggTrade',
                                               'd': {'p': r[1], 'q': r[2], 'T': t, 'm': r[6].lower() == 'true'}},
                                              separators=(',', ':'))))
    return out


def book_lines(rows: list) -> list:
    """update_id,best_bid_price,best_bid_qty,best_ask_price,best_ask_qty,transaction_time,event_time -> bookTicker."""
    out = []
    for r in rows:
        try:
            t = int(r[5]) if r[5] else int(r[6])
            b, a = float(r[1]), float(r[3])
        except (ValueError, IndexError):
            continue
        if b <= 0 or a <= 0:
            continue
        out.append((t * 1_000_000, json.dumps({'r': t * 1_000_000, 's': 'bookTicker',
                                               'd': {'u': r[0], 'b': r[1], 'B': r[2], 'a': r[3], 'A': r[4], 'T': t}},
                                              separators=(',', ':'))))
    return out


def write_hourly(root: Path, sym: str, lines: list) -> int:
    by_hour = defaultdict(list)
    for ns, line in lines:
        by_hour[dt.datetime.fromtimestamp(ns / 1e9, dt.timezone.utc).strftime('%Y%m%d-%H')].append(line)
    (root / sym).mkdir(parents=True, exist_ok=True)
    for hk, ls in by_hour.items():
        with gzip.open(root / sym / f'{hk}.jsonl.gz', 'at', encoding='utf-8') as f:
            f.write('\n'.join(ls) + '\n')
    return len(lines)


def write_liquidations(root: Path, lines: list) -> int:
    by_day = defaultdict(list)
    for ns, line in lines:
        by_day[dt.datetime.fromtimestamp(ns / 1e9, dt.timezone.utc).strftime('%Y%m%d')].append(line)
    root.mkdir(parents=True, exist_ok=True)
    for day, ls in by_day.items():
        with open(root / f'liquidations_{day}.jsonl', 'a', encoding='utf-8') as f:
            f.write('\n'.join(ls) + '\n')
    return len(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--symbols', default='SOLUSD_PERP,DOGEUSD_PERP,XRPUSD_PERP')
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'recordings' / 'historical' / 'cm')
    ap.add_argument('--liq-only', action='store_true', help='every listed liquidationSnapshot day, no tape')
    ap.add_argument('--days', default='', help='YYYYMMDD,... : aggTrades + bookTicker of these days')
    ap.add_argument('--no-book', action='store_true', help='skip bookTicker (large)')
    ap.add_argument('--react-ms', type=int, default=250)
    ap.add_argument('--max-mb', type=float, default=500.0, help='refuse a tape plan larger than this (zip MB)')
    a = ap.parse_args(argv)
    syms = [s.strip().upper() for s in a.symbols.split(',') if s.strip()]
    unknown = [s for s in syms if s not in CONTRACT_USD]
    if unknown:
        ap.error(f'no contract size for {unknown}')
    manifest = a.out / 'manifest.jsonl'
    a.out.mkdir(parents=True, exist_ok=True)
    def log(rec):
        with manifest.open('a', encoding='utf-8') as f:
            f.write(json.dumps(rec) + '\n')
    if a.liq_only:
        for s in syms:
            days = sorted(listed_days('liquidationSnapshot', s))
            n = 0
            for d in days:
                n += write_liquidations(a.out, liquidation_lines(fetch_csv('liquidationSnapshot', s, d), s, a.react_ms))
                time.sleep(0.05)
            log({'kind': 'liquidationSnapshot', 'symbol': s, 'days': len(days), 'first': days[0] if days else None,
                 'last': days[-1] if days else None, 'rows': n})
            print(f'{s}: {len(days)} liquidation days, {n} rows (after duplicate drop)', flush=True)
        return 0
    want = [f'{d[:4]}-{d[4:6]}-{d[6:]}' for d in a.days.split(',') if d.strip()]
    kinds = ['aggTrades'] + ([] if a.no_book else ['bookTicker'])
    plan = []
    for s in syms:
        for k in kinds:
            avail = listed_days(k, s)
            plan += [(k, s, d, avail[d]) for d in want if d in avail]
    mb = sum(x[3] for x in plan) / 1e6
    print(f'plan: {len(plan)} archives, {mb:.1f} MB zipped', flush=True)
    if mb > a.max_mb:
        print(f'refused: {mb:.1f} MB > --max-mb {a.max_mb:g}')
        return 2
    for k, s, d, size in plan:
        rows = fetch_csv(k, s, d)
        n = write_hourly(a.out, s, trade_lines(rows) if k == 'aggTrades' else book_lines(rows))
        log({'kind': k, 'symbol': s, 'day': d, 'zip_bytes': size, 'rows': n})
        print(f'{s} {k} {d}: {n} rows', flush=True)
        time.sleep(0.2)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
