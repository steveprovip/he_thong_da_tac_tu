"""BaseVenue over the bot's existing Binance venue (core.engine.BinanceVenue / LiveVenue): no new order code."""
from __future__ import annotations

import uuid
from typing import Optional

from core.venues.base import BaseVenue, PostOnlyRejected, VenueError, close_side

POST_ONLY_REJECT = -5022                                   # Binance: GTX order would immediately match


def _cid(prefix: str) -> str:
    return f'{prefix}{uuid.uuid4().hex[:20]}'


class BinanceAdapter(BaseVenue):
    """Delegates to an object with the DemoVenue primitives (limit_gtx, cancel, position_view, stop)."""
    name = 'binance'

    def __init__(self, venue, fmt_qty=str, fmt_px=str):
        self.v = venue
        self.fmt_qty, self.fmt_px = fmt_qty, fmt_px          # lot / tick rounding stays with the caller's spec

    async def place_post_only(self, symbol: str, side: str, qty: float, price: float, cid: Optional[str] = None,
                              reduce_only: bool = False) -> dict:
        if reduce_only:
            raise VenueError('reduce-only maker orders go through the venue take_profit() path')
        cid = cid or _cid('vb')
        try:
            await self.v.limit_gtx(symbol, side, self.fmt_qty(qty), self.fmt_px(price), cid)
        except Exception as e:                              # noqa: BLE001  (BinanceError lives in live/)
            if getattr(e, 'code', None) == POST_ONLY_REJECT:
                raise PostOnlyRejected(str(e), POST_ONLY_REJECT) from e
            raise
        return {'venue': self.name, 'order_id': cid, 'status': 'NEW'}

    async def cancel_order(self, symbol: str, order_id) -> None:
        await self.v.cancel(symbol, order_id)

    async def position_view(self, symbol: str) -> dict:
        p = await self.v.position_view(symbol)
        return {'amt': p.get('amt'), 'entry': p.get('entry'), 'mark': p.get('mark'), 'upnl': p.get('upnl'),
                'liq': p.get('liq')}

    async def set_disaster_stop(self, symbol: str, side: str, stop_px: float, qty: Optional[float] = None,
                                cid: Optional[str] = None) -> dict:
        cid = cid or _cid('vs')
        await self.v.stop(symbol, close_side(side), self.fmt_px(stop_px), cid)   # closePosition: qty not needed
        return {'venue': self.name, 'order_id': cid, 'status': 'NEW'}
