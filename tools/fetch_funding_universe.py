"""Funding history of every Binance USD-M USDT perpetual (2026-11-14, CCX-1). Public endpoints, no keys, no orders.

    python tools/fetch_funding_universe.py [--start 2024-12-20] [--end 2026-11-13] [--out data/funding_universe]

Writes exchangeInfo.json, fundingInfo.json and one fr_<SYMBOL>.json = [[t_ms, rate_bps], ...] per symbol; a symbol
whose file exists is skipped (resume). /fapi/v1/fundingRate allows 500 requests per 5 minutes per IP: one request
per 0.7 s. Only symbols the venue lists today are known, so delisted contracts are missing (survivorship).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import quote                            # a few symbols are not ASCII
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = 'https://fapi.binance.com'
PAUSE_S = 0.65                                            # 500 requests / 5 min = one per 0.6 s


def ms(day: str) -> int:
    return int(datetime.strptime(day, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)


def on_minute(t_ms: int) -> int:
    return int(round(t_ms / 60000)) * 60000


def get(url: str, tries: int = 6):
    for k in range(tries):
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'research'})
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):                      # rate limited: honour Retry-After, then back off
                time.sleep(float(e.headers.get('Retry-After') or 30) + 5 * k)
            elif e.code == 400:
                return None
            elif k == tries - 1:
                raise
            else:
                time.sleep(2 + 3 * k)
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(2 + 3 * k)
    raise RuntimeError(f'rate limited for {tries} tries: {url}')


def perp_symbols(info: dict) -> list:
    """USDT-margined perpetuals of an exchangeInfo payload, any status, sorted."""
    return sorted(s['symbol'] for s in info.get('symbols', [])
                  if s.get('contractType') == 'PERPETUAL' and s.get('quoteAsset') == 'USDT')


_next_ok = [0.0]


def paced(url: str):
    """One request per PAUSE_S measured start to start (the latency counts toward the pause)."""
    time.sleep(max(0.0, _next_ok[0] - time.monotonic()))
    _next_ok[0] = time.monotonic() + PAUSE_S
    return get(url)


def funding(sym: str, a: int, b: int) -> list:
    out, s = [], a
    while s < b:
        page = paced(f'{BASE}/fapi/v1/fundingRate?symbol={quote(sym)}&startTime={s}&endTime={b}&limit=1000')
        if not page:
            break
        out += [[on_minute(int(x['fundingTime'])), float(x['fundingRate']) * 1e4] for x in page]
        s = int(page[-1]['fundingTime']) + 1
        if len(page) < 1000:
            break
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--start', default='2024-12-20')
    ap.add_argument('--end', default='2026-11-13')
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'funding_universe')
    a = ap.parse_args(argv)
    a.out.mkdir(parents=True, exist_ok=True)
    info_p = a.out / 'exchangeInfo.json'
    if not info_p.exists():
        info_p.write_text(json.dumps(get(f'{BASE}/fapi/v1/exchangeInfo')))
        (a.out / 'fundingInfo.json').write_text(json.dumps(get(f'{BASE}/fapi/v1/fundingInfo')))
    syms = perp_symbols(json.loads(info_p.read_text()))
    lo, hi = ms(a.start), ms(a.end) + 86_400_000
    t0, done = time.time(), 0
    for i, sym in enumerate(syms):
        p = a.out / f'fr_{sym}.json'
        if p.exists():
            continue
        rows = funding(sym, lo, hi)
        tmp = p.with_suffix('.tmp')
        tmp.write_text(json.dumps(rows))
        tmp.replace(p)                                    # a killed run never leaves a half-written symbol
        done += 1
        if done % 25 == 0:
            print(f'{i + 1}/{len(syms)} {sym} rows {len(rows)} elapsed {time.time() - t0:.0f} s', flush=True)
    print(f'done: {len(syms)} symbols, {done} fetched, {time.time() - t0:.0f} s', flush=True)
    return 0


if __name__ == '__main__':
    sys.exit(main())
