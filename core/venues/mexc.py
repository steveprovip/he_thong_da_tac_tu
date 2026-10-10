"""PROTOTYPE MEXC USDT-M futures adapter (contract API v1).  Builds and signs requests; sends only through an
injected async transport (method, url, headers, body) -> parsed JSON.  Without one every call raises VenueDisabled.

Protocol (MEXC contract API docs; field names to re-check against the current docs before any use):
  sign        HMAC-SHA256(secret, api_key + request_time_ms + param_string), hex; headers ApiKey, Request-Time,
              Signature.  param_string = the JSON body (POST) or the sorted query string (GET).
  order       POST /api/v1/private/order/submit {symbol 'SOL_USDT', price, vol (contracts), side 1 open long |
              2 close short | 3 open short | 4 close long, type 2 = post-only maker, openType 1 isolated | 2 cross}
  cancel      POST /api/v1/private/order/cancel [order_id]
  position    GET  /api/v1/private/position/open_positions?symbol=  (holdVol contracts, positionType 1 long 2 short)
  stop        POST /api/v1/private/planorder/place {triggerPrice, triggerType 1 >= | 2 <=, trend 1 last price,
              orderType 5 market, executeCycle 2 (7 days), side close-*}
Fees: API orders have their own schedule (0.06 % / 0.08 % from 2026-06-01 per MEXC's notice), not the 0 % maker
web/app rate - see core.venues.base.VENUE_SPECS.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from typing import Callable, Optional
from urllib.parse import urlencode

from core.venues.base import BaseVenue, MinSizeError, VenueDisabled, VenueError, close_side

BASE = 'https://contract.mexc.com'
# contract/detail read 2026-10-10: contractSize (base units per contract), minVol (contracts), priceUnit
CONTRACTS = {'BTCUSDT': {'contract': 0.0001, 'min_vol': 1, 'tick': 0.1},
             'SOLUSDT': {'contract': 0.1, 'min_vol': 1, 'tick': 0.01},
             'DOGEUSDT': {'contract': 100.0, 'min_vol': 1, 'tick': 0.00001}}


def mexc_symbol(symbol: str) -> str:
    return symbol[:-4] + '_USDT' if symbol.endswith('USDT') else symbol


def sign(secret: str, api_key: str, req_time: str, param_string: str) -> str:
    return hmac.new(secret.encode(), (api_key + req_time + param_string).encode(), hashlib.sha256).hexdigest()


class MexcVenue(BaseVenue):
    name = 'mexc'

    def __init__(self, api_key: str = '', secret: str = '', transport: Optional[Callable] = None,
                 contracts: Optional[dict] = None, open_type: int = 2, leverage: int = 5,
                 clock: Callable[[], float] = time.time):
        self.key, self.secret, self.transport = api_key, secret, transport
        self.contracts = contracts or CONTRACTS
        self.open_type, self.leverage, self.clock = open_type, leverage, clock

    # ---------------------------------------------------------------- pure helpers (tested offline)
    def vol(self, symbol: str, qty: float) -> int:
        c = self.contracts[symbol]
        v = math.floor(qty / c['contract'] + 1e-9)
        if v < c['min_vol']:
            raise MinSizeError(f'{symbol}: {qty} < {c["min_vol"]} contract(s) of {c["contract"]}')
        return v

    def request(self, method: str, path: str, params) -> tuple:
        """-> (method, url, headers, body) signed; body is the exact string that was signed (POST)."""
        t = str(int(self.clock() * 1000))
        if method == 'GET':
            qs = urlencode(sorted((params or {}).items()))
            url, body, ps = f'{BASE}{path}' + (f'?{qs}' if qs else ''), None, qs
        else:
            body = json.dumps(params, separators=(',', ':'))
            url, ps = f'{BASE}{path}', body
        headers = {'ApiKey': self.key, 'Request-Time': t, 'Signature': sign(self.secret, self.key, t, ps),
                   'Content-Type': 'application/json'}
        return method, url, headers, body

    def order_params(self, symbol: str, side: str, qty: float, price: float, reduce_only: bool = False) -> dict:
        if side not in ('BUY', 'SELL'):
            raise ValueError(side)
        if reduce_only:
            code = 2 if side == 'BUY' else 4                    # BUY closes a short, SELL closes a long
        else:
            code = 1 if side == 'BUY' else 3
        return {'symbol': mexc_symbol(symbol), 'price': price, 'vol': self.vol(symbol, qty), 'side': code,
                'type': 2, 'openType': self.open_type, 'leverage': self.leverage}

    def stop_params(self, symbol: str, side: str, stop_px: float, qty: float) -> dict:
        long_ = close_side(side) == 'SELL'
        return {'symbol': mexc_symbol(symbol), 'vol': self.vol(symbol, qty), 'side': 4 if long_ else 2,
                'openType': self.open_type, 'leverage': self.leverage, 'triggerPrice': stop_px,
                'triggerType': 2 if long_ else 1, 'trend': 1, 'orderType': 5, 'executeCycle': 2}

    # ---------------------------------------------------------------- network (only with a transport)
    async def _send(self, method: str, path: str, params) -> dict:
        if self.transport is None:
            raise VenueDisabled('MexcVenue has no transport (prototype: research only)')
        r = await self.transport(*self.request(method, path, params))
        if not isinstance(r, dict) or not r.get('success'):
            raise VenueError(f'mexc {path}: {r}', (r or {}).get('code') if isinstance(r, dict) else None)
        return r

    async def place_post_only(self, symbol: str, side: str, qty: float, price: float, cid: Optional[str] = None,
                              reduce_only: bool = False) -> dict:
        params = self.order_params(symbol, side, qty, price, reduce_only)
        if cid:
            params['externalOid'] = cid
        r = await self._send('POST', '/api/v1/private/order/submit', params)
        # a post-only order that would cross is cancelled by the venue AFTER the ack: query its state before use
        return {'venue': self.name, 'order_id': r.get('data'), 'status': 'ACK'}

    async def cancel_order(self, symbol: str, order_id) -> None:
        await self._send('POST', '/api/v1/private/order/cancel', [order_id])

    async def position_view(self, symbol: str) -> dict:
        r = await self._send('GET', '/api/v1/private/position/open_positions', {'symbol': mexc_symbol(symbol)})
        rows = r.get('data') or []
        if not rows:
            return {'amt': 0.0, 'entry': None, 'mark': None, 'upnl': None, 'liq': None}
        c = self.contracts[symbol]['contract']
        amt = sum((1 if p.get('positionType') == 1 else -1) * float(p.get('holdVol') or 0) * c for p in rows)
        p = rows[0]
        return {'amt': amt, 'entry': float(p['holdAvgPrice']) if p.get('holdAvgPrice') is not None else None,
                'mark': None, 'upnl': None,
                'liq': float(p['liquidatePrice']) if p.get('liquidatePrice') is not None else None}

    async def set_disaster_stop(self, symbol: str, side: str, stop_px: float, qty: Optional[float] = None,
                                cid: Optional[str] = None) -> dict:
        if qty is None:
            qty = abs((await self.position_view(symbol))['amt'] or 0.0)
        r = await self._send('POST', '/api/v1/private/planorder/place', self.stop_params(symbol, side, stop_px, qty))
        return {'venue': self.name, 'order_id': r.get('data'), 'status': 'ACK'}
