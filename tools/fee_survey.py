"""Zero-fee / wide-spread survey of Binance USD-M perpetuals (request 10-10, task 3).  Read-only, GET only.

    python tools/fee_survey.py [--quote USDC] [--symbols SOLUSDC,DOGEUSDC,...] [--json out.json]

Per symbol: touch spread (bookTicker), 24 h quote volume, and YOUR maker / taker commission from the signed
/fapi/v1/commissionRate (mainnet key from .env.local: BINANCE_MAINNET_API_KEY / _SECRET_KEY; never sent anywhere
else).  Without a key the fee is UNKNOWN - promotions change, so a 0 % maker fee is only trusted when this endpoint
returns it for your account.  Avellaneda-Stoikov quoting is worth testing only where maker = 0 AND the replay
(tools/burst_scalper_replay.py, MM rows) is positive after adverse selection; on 2026-09/10 aggTrades it was not
(USDC MM -2.2 to -5.0 bps per round trip even at 0 % maker).
"""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = 'https://fapi.binance.com'


def get(path: str, params=None, key: str = '', secret: str = '', opener=urllib.request.urlopen, timeout=12.0):
    q = dict(params or {})
    headers = {}
    if secret:
        q['timestamp'] = int(time.time() * 1000)
        qs = urllib.parse.urlencode(q)
        q_s = qs + '&signature=' + hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
        headers['X-MBX-APIKEY'] = key
    else:
        q_s = urllib.parse.urlencode(q)
    req = urllib.request.Request(f'{BASE}{path}' + (f'?{q_s}' if q_s else ''), headers=headers)
    with opener(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def survey(quote: str = 'USDC', symbols=None, key: str = '', secret: str = '', opener=urllib.request.urlopen) -> list:
    info = get('/fapi/v1/exchangeInfo', opener=opener)
    perps = [s['symbol'] for s in info.get('symbols', []) if s.get('contractType') == 'PERPETUAL'
             and s.get('status') == 'TRADING' and s.get('marginAsset') == quote]
    if symbols:
        perps = [s for s in perps if s in symbols]
    books = {b['symbol']: b for b in get('/fapi/v1/ticker/bookTicker', opener=opener)}
    vols = {t['symbol']: float(t.get('quoteVolume') or 0) for t in get('/fapi/v1/ticker/24hr', opener=opener)}
    out = []
    for s in perps:
        b = books.get(s, {})
        bid, ask = float(b.get('bidPrice') or 0), float(b.get('askPrice') or 0)
        row = {'symbol': s, 'spread_bps': (ask - bid) / ((ask + bid) / 2) * 1e4 if bid and ask else None,
               'quote_volume_24h': vols.get(s), 'maker_bps': None, 'taker_bps': None, 'fee_source': 'UNKNOWN (no key)'}
        if key and secret:
            try:
                c = get('/fapi/v1/commissionRate', {'symbol': s}, key, secret, opener=opener)
                row.update(maker_bps=float(c['makerCommissionRate']) * 1e4, taker_bps=float(c['takerCommissionRate']) * 1e4,
                           fee_source='commissionRate (your account)')
            except Exception as exc:                       # noqa: BLE001 - report, keep surveying
                row['fee_source'] = f'error {type(exc).__name__}'
        row['mm_candidate'] = row['maker_bps'] == 0.0 and (row['spread_bps'] or 0) >= 1.0
        out.append(row)
    return sorted(out, key=lambda r: -(r['quote_volume_24h'] or 0))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--quote', default='USDC')
    ap.add_argument('--symbols', default='')
    ap.add_argument('--json', type=Path)
    a = ap.parse_args(argv)
    from core.engine import credentials, read_env
    key, secret = credentials('live', read_env())
    rows = survey(a.quote, set(a.symbols.split(',')) if a.symbols else None, key, secret)
    print(f'{len(rows)} {a.quote}-margined perpetuals (fees: {"your account" if key else "UNKNOWN - no mainnet key"})')
    for r in rows[:40]:
        f = (f"maker {r['maker_bps']:+.2f} taker {r['taker_bps']:+.2f} bps" if r['maker_bps'] is not None
             else r['fee_source'])
        print(f"{r['symbol']:<14} spread {r['spread_bps'] or 0:6.2f} bps  vol24h {(r['quote_volume_24h'] or 0) / 1e6:9.1f} M"
              f"  {f}{'  <- 0 % maker' if r['mm_candidate'] else ''}")
    if a.json:
        a.json.write_text(json.dumps(rows, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
