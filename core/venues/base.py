"""BaseVenue: the four calls a lane needs from any perp venue, plus the venue facts the 10-10 d survey checked.

Conventions (every adapter):
  side           'BUY' | 'SELL' of the ORDER for place_post_only; of the POSITION for set_disaster_stop
                 (the stop closes it: a long's stop sells).
  qty            base-asset units (BTC, SOL, DOGE); each adapter converts to its own unit (MEXC: contracts).
  return values  place_post_only / set_disaster_stop -> {'venue', 'order_id', 'status'};
                 position_view -> {'amt' (signed base units), 'entry', 'mark', 'upnl', 'liq'} (None = UNKNOWN).
  errors         PostOnlyRejected (would have crossed), MinSizeError (below the venue minimum, raised BEFORE any
                 request), VenueDisabled (no transport / signer: the adapter never sends by default),
                 VenueError (anything the venue refused).
"""
from __future__ import annotations

import abc
from typing import Optional


class VenueError(RuntimeError):
    def __init__(self, msg: str, code=None):
        super().__init__(msg)
        self.code = code


class PostOnlyRejected(VenueError):
    pass


class MinSizeError(VenueError):
    pass


class VenueDisabled(VenueError):
    pass


class BaseVenue(abc.ABC):
    name = 'base'

    @abc.abstractmethod
    async def place_post_only(self, symbol: str, side: str, qty: float, price: float, cid: Optional[str] = None,
                              reduce_only: bool = False) -> dict:
        """Maker-only limit order; must never take liquidity (rejected / cancelled instead)."""

    @abc.abstractmethod
    async def cancel_order(self, symbol: str, order_id) -> None:
        """Cancel a resting order (already gone = success)."""

    @abc.abstractmethod
    async def position_view(self, symbol: str) -> dict:
        """Signed position in base units; missing fields are None (UNKNOWN), never 0."""

    @abc.abstractmethod
    async def set_disaster_stop(self, symbol: str, side: str, stop_px: float, qty: Optional[float] = None,
                                cid: Optional[str] = None) -> dict:
        """Exchange-side reduce-only stop-market for the POSITION `side`, resting on the venue (survives our crash)."""


def close_side(position_side: str) -> str:
    if position_side not in ('BUY', 'SELL'):
        raise ValueError(f'side must be BUY or SELL, got {position_side!r}')
    return 'SELL' if position_side == 'BUY' else 'BUY'


# Venue facts as checked on 2026-10-10 (bps; 'verified' = read from the venue's own API / docs that day).
# They are inputs for research tables, not constants to trade on: read the account's real rate before any use.
VENUE_SPECS = {
    'binance': {
        'fees_bps': {'maker': 2.0, 'taker': 5.0}, 'bnb_discount': 0.10,
        'fee_status': 'VIP0 2/5 per Binance FAQ (2026-05-01); BNB 10 % verified; USDC-M 0 % maker promo ended '
                      '2025-12-10, current USDC-M rate UNVERIFIED',
        'post_only': "timeInForce='GTX'", 'min_order': 'MIN_NOTIONAL filter in exchangeInfo (5 USDT in the docs '
                                                          'example; BTCUSDT 100 USDT is third-party, UNVERIFIED)',
        'rate_limits': 'REQUEST_WEIGHT 2400/min, ORDERS 1200/min (10 s cap in exchangeInfo)',
        'stop': 'algoOrder STOP_MARKET closePosition', 'api_access': 'yes (the bot\'s current venue)'},
    'mexc': {
        'fees_bps': {'maker': 6.0, 'taker': 8.0},
        'fees_web_bps': {'maker': 0.0, 'taker': 2.0},
        'fee_status': 'contract/detail (web/app rate) 2026-10-10: 0 %/0.02 % for BTC, SOL, DOGE (BTC read 0.01 %/'
                      '0.04 % once at 11:45 UTC, 0 %/0.02 % on 7 later reads: a public field, not your rate). '
                      'API orders pay a separate schedule since 2026-03-31; announcement of 2026-05-28: 0.06 % / '
                      '0.08 % from 2026-06-01, overriding 0-fee events (found by web research; direct fetch blocked '
                      'here) -> read the account\'s rate before any use',
        'post_only': 'order type=2', 'min_order': 'minVol 1 contract: BTC 0.0001 BTC, SOL 0.1 SOL, DOGE 100 DOGE',
        'rate_limits': 'order submit 20 req / 2 s (legacy docs)', 'stop': 'plan order (trigger + market)',
        'api_access': 'futures API orders since 2026-03-31 for KYC users; may vary by region'},
    'hyperliquid': {
        'fees_bps': {'maker': 1.5, 'taker': 4.5},
        'fee_status': 'base tier verified (docs); maker rebates need >= 0.5 % of 14-day venue maker volume; '
                      'HYPE staking 5-40 % and referral 4 % discounts',
        'post_only': "tif='Alo'", 'min_order': '10 USD notional; size in szDecimals (BTC 5, SOL 2, DOGE 0)',
        'rate_limits': '1200 weight/min per IP; per address 1 request per 1 USDC traded (+10 000 buffer)',
        'stop': 'trigger order isMarket tpsl=sl, reduce-only', 'api_access': 'agent (API) wallet signs; no KYC'},
    'bybit': {
        'fees_bps': {'maker': 2.0, 'taker': 5.5},
        'fee_status': 'VIP0 0.02 %/0.055 % third-party only (official page 503); no USDC 0 % maker promo found',
        'post_only': "timeInForce='PostOnly'", 'min_order': 'instruments-info (geo-blocked here, UNVERIFIED)',
        'rate_limits': '600 req / 5 s per IP; order create 10-20/s per UID', 'stop': 'conditional / tpsl',
        'api_access': 'yes; no adapter built (same fee class as Binance)'},
}
