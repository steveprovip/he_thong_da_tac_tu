"""Public-endpoint latency and contract-spec probe for Binance / MEXC / Hyperliquid / Bybit (10-10 d).

    python tools/venue_probe.py [--n 15] [--json out.json]

Run it on the machine that will trade: latency depends on where you are (a cloud sandbox in another region tells
little), and Binance / Bybit geo-block some regions (HTTP 451 / 403).  Public data only - no keys, no orders.
"""
from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.error
import urllib.request

PING = {
    'binance': ('GET', 'https://fapi.binance.com/fapi/v1/time', None),
    'mexc': ('GET', 'https://contract.mexc.com/api/v1/contract/ping', None),
    'hyperliquid': ('POST', 'https://api.hyperliquid.xyz/info', {'type': 'meta'}),
    'bybit': ('GET', 'https://api.bybit.com/v5/market/time', None),
}


def call(method: str, url: str, body=None, timeout: float = 15.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={'Content-Type': 'application/json'})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            return r.status, (time.perf_counter() - t0) * 1e3, json.loads(raw or b'null')
    except urllib.error.HTTPError as e:
        return e.code, (time.perf_counter() - t0) * 1e3, None
    except (urllib.error.URLError, TimeoutError, ValueError) as e:
        return str(e)[:60], None, None


def latency(n: int) -> dict:
    out = {}
    for v, (m, url, body) in PING.items():
        ms, status = [], None
        for _ in range(n):
            status, dt, _ = call(m, url, body)
            if status == 200 and dt is not None:
                ms.append(dt)
        out[v] = ({'status': 200, 'n': len(ms), 'median_ms': statistics.median(ms),
                   'p90_ms': sorted(ms)[int(0.9 * (len(ms) - 1))]} if ms else {'status': status, 'n': 0})
    return out


def specs() -> dict:
    out = {}
    st, _, d = call('GET', 'https://fapi.binance.com/fapi/v1/exchangeInfo')
    if st == 200:
        for s in d['symbols']:
            if s['symbol'] in ('BTCUSDT', 'SOLUSDT', 'DOGEUSDT'):
                f = {x['filterType']: x for x in s['filters']}
                out.setdefault('binance', {})[s['symbol']] = {'min_notional': f.get('MIN_NOTIONAL', {}).get('notional'),
                                                              'step': f.get('LOT_SIZE', {}).get('stepSize')}
    else:
        out['binance'] = {'status': st}
    for s in ('BTC_USDT', 'SOL_USDT', 'DOGE_USDT'):
        st, _, d = call('GET', f'https://contract.mexc.com/api/v1/contract/detail?symbol={s}')
        dd = (d or {}).get('data') or {}
        out.setdefault('mexc', {})[s] = ({k: dd.get(k) for k in ('contractSize', 'minVol', 'priceUnit', 'makerFeeRate',
                                                                  'takerFeeRate', 'apiAllowed')} if st == 200 else {'status': st})
    st, _, d = call('POST', 'https://api.hyperliquid.xyz/info', {'type': 'meta'})
    out['hyperliquid'] = ({a['name']: {'index': i, 'szDecimals': a['szDecimals'], 'min_order_usd': 10}
                           for i, a in enumerate(d['universe']) if a['name'] in ('BTC', 'SOL', 'DOGE')}
                          if st == 200 else {'status': st})
    for s in ('BTCUSDT', 'SOLUSDT', 'DOGEUSDT'):
        st, _, d = call('GET', f'https://api.bybit.com/v5/market/instruments-info?category=linear&symbol={s}')
        lot = (((d or {}).get('result') or {}).get('list') or [{}])[0].get('lotSizeFilter', {})
        out.setdefault('bybit', {})[s] = ({k: lot.get(k) for k in ('minOrderQty', 'minNotionalValue', 'qtyStep')}
                                          if st == 200 else {'status': st})
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--n', type=int, default=15)
    ap.add_argument('--json')
    a = ap.parse_args(argv)
    out = {'t_utc': time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime()), 'latency': latency(a.n), 'specs': specs()}
    print(json.dumps(out, indent=1))
    if a.json:
        with open(a.json, 'w', encoding='utf-8') as fh:
            json.dump(out, fh, indent=1)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
