"""Monthly point-in-time universe for the intraday replay (10-10 f).  Public REST only, no keys.

    python tools/universe_months.py --start 2025-01 --end 2026-09 --top 20 --out data/universe_months.json

For every currently listed USDT perpetual: 1d klines (limit 1000, cached in data/universe_daily.json), then
core.universe.screen(as_of = 00:00 UTC on the 1st of each month).  Output: {'YYYY-MM': [symbols by median volume]}.
Limitation: symbols delisted before today are not in exchangeInfo, so they cannot enter (survivorship; the top-20 by
median volume are majors, which rarely delist - the report states it).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.universe import bars_from_klines, listings_from_exchange_info, screen  # noqa: E402

FAPI = 'https://fapi.binance.com'


def get(url: str, tries: int = 3):
    for k in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'research'}),
                                        timeout=20) as r:
                return json.load(r)
        except Exception:
            if k == tries - 1:
                raise
            time.sleep(1 + 2 * k)


def months(start: str, end: str):
    y, m = map(int, start.split('-'))
    while f'{y:04d}-{m:02d}' <= end:
        yield f'{y:04d}-{m:02d}'
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)


def month_ms(ym: str) -> int:
    return int(datetime.strptime(ym + '-01', '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--start', default='2025-01')
    ap.add_argument('--end', default='2026-09')
    ap.add_argument('--top', type=int, default=20)
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'universe_months.json')
    ap.add_argument('--cache', type=Path, default=ROOT / 'data' / 'universe_daily.json')
    a = ap.parse_args(argv)
    info = get(FAPI + '/fapi/v1/exchangeInfo')
    listings = listings_from_exchange_info(info)
    raw = json.loads(a.cache.read_text()) if a.cache.exists() else {}
    want = [li.symbol for li in listings if li.symbol.isascii() and li.quote == 'USDT'
            and li.contract == 'PERPETUAL' and li.status == 'TRADING' and li.underlying == 'COIN']
    for i, s in enumerate(w for w in want if w not in raw):
        raw[s] = get(f'{FAPI}/fapi/v1/klines?symbol={s}&interval=1d&limit=1000')
        time.sleep(0.15)                                   # weight 5 each: stay under 2 400 / min
        if i % 50 == 49:
            print(f'  fetched {i + 1}', flush=True)
    a.cache.parent.mkdir(parents=True, exist_ok=True)
    a.cache.write_text(json.dumps(raw))
    daily = {s: bars_from_klines(r) for s, r in raw.items()}
    out = {}
    for ym in months(a.start, a.end):
        picked, why = screen(listings, daily, month_ms(ym), top_n=a.top)
        out[ym] = [p['symbol'] for p in picked]
        print(ym, len(out[ym]), ' '.join(x.replace('USDT', '') for x in out[ym]))
    a.out.write_text(json.dumps({'_rule': f'core.universe.screen defaults, top {a.top}, as_of 1st of month 00:00 UTC',
                                 '_generated': datetime.now(timezone.utc).isoformat(timespec='seconds'),
                                 'months': out}, indent=1))
    union = sorted({s for v in out.values() for s in v})
    print('union', len(union), ','.join(union))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
