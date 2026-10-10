"""PROTOTYPE Hyperliquid perps adapter.  Builds exchange actions; sends only with an injected async transport
(method, url, headers, body) -> parsed JSON AND an injected signer(action, nonce) -> {'r', 's', 'v'} (the L1 action
signature of an API / agent wallet - use the official SDK; no key handling lives here).  Without them: VenueDisabled.

Protocol (Hyperliquid API docs):
  exchange  POST /exchange {action, nonce (ms), signature}
  order     {'type': 'order', 'orders': [{a: asset index, b: is_buy, p: px str, s: size str, r: reduce_only,
            t: {'limit': {'tif': 'Alo'}}}], 'grouping': 'na'}            Alo = add-liquidity-only (post-only)
  stop      t: {'trigger': {'isMarket': True, 'triggerPx': str, 'tpsl': 'sl'}}, r: True
  cancel    {'type': 'cancel', 'cancels': [{a, o: oid}]}
  position  POST /info {'type': 'clearinghouseState', 'user': address} -> assetPositions[].position
  ticks     price <= 5 significant figures and <= 6 - szDecimals decimals (integers always valid);
            size rounded to szDecimals; minimum order value 10 USD.
"""
from __future__ import annotations

import json
import math
import time
from decimal import ROUND_CEILING, ROUND_FLOOR, Decimal
from typing import Callable, Optional

from core.venues.base import BaseVenue, MinSizeError, PostOnlyRejected, VenueDisabled, VenueError, close_side

BASE = 'https://api.hyperliquid.xyz'
MIN_NOTIONAL = 10.0
# /info meta read 2026-10-10: universe index and szDecimals
ASSETS = {'BTCUSDT': {'coin': 'BTC', 'a': 0, 'szd': 5}, 'SOLUSDT': {'coin': 'SOL', 'a': 5, 'szd': 2},
          'DOGEUSDT': {'coin': 'DOGE', 'a': 12, 'szd': 0}}


def wire(x) -> str:
    """Decimal string without trailing zeros (the venue rejects '1.50000000'-style noise in signed actions)."""
    s = format(Decimal(str(x)).normalize(), 'f')
    return s if s not in ('-0', '') else '0'


def round_px(px: float, szd: int, side: str) -> Decimal:
    """Passive rounding for a maker: a BUY never rounds up, a SELL never rounds down."""
    if px <= 0:
        raise ValueError(px)
    d = Decimal(str(px))
    int_digits = math.floor(math.log10(px)) + 1          # <= 0 below 1: leading zeros are not significant
    decimals = max(0, min(6 - szd, 5 - int_digits))
    q = Decimal(1).scaleb(-decimals)
    return d.quantize(q, rounding=ROUND_FLOOR if side == 'BUY' else ROUND_CEILING)


def round_sz(qty: float, szd: int) -> Decimal:
    return Decimal(str(qty)).quantize(Decimal(1).scaleb(-szd), rounding=ROUND_FLOOR)


class HyperliquidVenue(BaseVenue):
    name = 'hyperliquid'

    def __init__(self, address: str = '', signer: Optional[Callable] = None, transport: Optional[Callable] = None,
                 assets: Optional[dict] = None, stop_slippage: float = 0.05, clock: Callable[[], float] = time.time):
        self.address, self.signer, self.transport = address, signer, transport
        self.assets = assets or ASSETS
        self.stop_slip, self.clock = stop_slippage, clock

    # ---------------------------------------------------------------- pure helpers (tested offline)
    def order_wire(self, symbol: str, side: str, qty: float, price: float, reduce_only: bool = False) -> dict:
        a = self.assets[symbol]
        px, sz = round_px(price, a['szd'], side), round_sz(qty, a['szd'])
        if sz <= 0 or float(px * sz) < MIN_NOTIONAL:
            raise MinSizeError(f'{symbol}: {sz} @ {px} < {MIN_NOTIONAL} USD minimum order value')
        return {'a': a['a'], 'b': side == 'BUY', 'p': wire(px), 's': wire(sz), 'r': bool(reduce_only),
                't': {'limit': {'tif': 'Alo'}}}

    def stop_wire(self, symbol: str, side: str, stop_px: float, qty: float) -> dict:
        a, out = self.assets[symbol], close_side(side)
        trig = round_px(stop_px, a['szd'], out)
        worst = stop_px * (1 - self.stop_slip) if out == 'SELL' else stop_px * (1 + self.stop_slip)
        sz = round_sz(qty, a['szd'])
        if sz <= 0:
            raise MinSizeError(f'{symbol}: stop size {qty} rounds to 0')
        return {'a': a['a'], 'b': out == 'BUY', 'p': wire(round_px(worst, a['szd'], 'SELL' if out == 'BUY' else 'BUY')),
                's': wire(sz), 'r': True, 't': {'trigger': {'isMarket': True, 'triggerPx': wire(trig), 'tpsl': 'sl'}}}

    # ---------------------------------------------------------------- network (only with transport + signer)
    async def _exchange(self, action: dict) -> dict:
        if self.transport is None or self.signer is None:
            raise VenueDisabled('HyperliquidVenue needs a transport and a signer (prototype: research only)')
        nonce = int(self.clock() * 1000)
        body = json.dumps({'action': action, 'nonce': nonce, 'signature': self.signer(action, nonce)})
        r = await self.transport('POST', f'{BASE}/exchange', {'Content-Type': 'application/json'}, body)
        if not isinstance(r, dict) or r.get('status') != 'ok':
            raise VenueError(f'hyperliquid: {r}')
        return r

    async def _info(self, req: dict) -> dict:
        if self.transport is None:
            raise VenueDisabled('HyperliquidVenue has no transport')
        return await self.transport('POST', f'{BASE}/info', {'Content-Type': 'application/json'}, json.dumps(req))

    @staticmethod
    def _status(r: dict) -> dict:
        st = r['response']['data']['statuses'][0]
        if 'error' in st:
            msg = st['error']
            if 'Post only' in msg or 'post only' in msg:
                raise PostOnlyRejected(msg)
            raise VenueError(msg)
        return st

    async def place_post_only(self, symbol: str, side: str, qty: float, price: float, cid: Optional[str] = None,
                              reduce_only: bool = False) -> dict:
        o = self.order_wire(symbol, side, qty, price, reduce_only)
        if cid:
            o['c'] = cid                                    # 128-bit hex client id (0x + 32 hex)
        st = self._status(await self._exchange({'type': 'order', 'orders': [o], 'grouping': 'na'}))
        if 'resting' in st:
            return {'venue': self.name, 'order_id': st['resting']['oid'], 'status': 'NEW'}
        return {'venue': self.name, 'order_id': st.get('filled', {}).get('oid'), 'status': 'FILLED'}

    async def cancel_order(self, symbol: str, order_id) -> None:
        r = await self._exchange({'type': 'cancel', 'cancels': [{'a': self.assets[symbol]['a'], 'o': int(order_id)}]})
        st = r['response']['data']['statuses'][0]
        if isinstance(st, dict) and 'error' in st and 'already canceled' not in st['error'] \
                and 'never placed' not in st['error'] and 'filled' not in st['error']:
            raise VenueError(st['error'])

    async def position_view(self, symbol: str) -> dict:
        coin = self.assets[symbol]['coin']
        r = await self._info({'type': 'clearinghouseState', 'user': self.address})
        for ap in (r or {}).get('assetPositions', []):
            p = ap.get('position', {})
            if p.get('coin') == coin:
                f = lambda k: float(p[k]) if p.get(k) is not None else None     # noqa: E731
                return {'amt': f('szi'), 'entry': f('entryPx'), 'mark': None, 'upnl': f('unrealizedPnl'),
                        'liq': f('liquidationPx')}
        return {'amt': 0.0, 'entry': None, 'mark': None, 'upnl': None, 'liq': None}

    async def set_disaster_stop(self, symbol: str, side: str, stop_px: float, qty: Optional[float] = None,
                                cid: Optional[str] = None) -> dict:
        if qty is None:
            qty = abs((await self.position_view(symbol))['amt'] or 0.0)
        st = self._status(await self._exchange({'type': 'order', 'orders': [self.stop_wire(symbol, side, stop_px, qty)],
                                                'grouping': 'na'}))
        return {'venue': self.name, 'order_id': st.get('resting', {}).get('oid'), 'status': 'NEW'}
