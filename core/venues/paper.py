"""In-memory perp venue for the carry lane's paper mode and tests (2026-11-07). No network, no keys, no real order.

Maker orders (GTX on Binance, Alo on Hyperliquid) rest and are rejected if they would cross; they fill only when the
caller says a trade went through them (`fill_resting`). IOC limit orders fill at the touch, up to the touch depth
(`ioc_fill_fraction` lets a test starve the hedge), never through the limit. One cross-margin account per venue:
equity = cash + unrealized P&L - fees + funding; maintenance = sum |qty| x mark x mmr. With a single open position
the liquidation price has a closed form; with several it is UNKNOWN (None), never guessed.
"""
from __future__ import annotations

import itertools
from typing import Optional

from core.venues.base import MinSizeError, PostOnlyRejected, VenueError


class PaperPerpVenue:
    def __init__(self, name: str, maker_bps: float, taker_bps: float, cash_usdt: float, mmr: float = 0.005,
                 min_qty: float = 0.0, auto_fill: bool = False):
        self.name = name
        self.auto_fill = auto_fill          # simulation: a resting maker order fills at its price on the next status read
        self.maker_bps, self.taker_bps = float(maker_bps), float(taker_bps)
        self.cash, self.mmr, self.min_qty = float(cash_usdt), float(mmr), float(min_qty)
        self.books: dict = {}
        self.positions: dict = {}           # symbol -> {'amt': signed base, 'entry': px}
        self.orders: dict = {}
        self.fees = self.realized = self.funding = 0.0
        self.income: list = []              # funding cash flows {'symbol', 't', 'amount'}
        self.calls: list = []               # (method, symbol, side, qty, px, reduce_only) for audits
        self.ioc_fill_fraction = 1.0
        self._ids = itertools.count(1)

    # ---------------------------------------------------------------- market state (set by the caller)
    def set_book(self, symbol: str, bid: float, ask: float, bid_qty: float = 1e12, ask_qty: float = 1e12) -> None:
        if not 0 < bid < ask:
            raise ValueError('need 0 < bid < ask')
        self.books[symbol] = {'bid': float(bid), 'ask': float(ask), 'bid_qty': float(bid_qty),
                              'ask_qty': float(ask_qty)}

    def mark(self, symbol: str) -> float:
        b = self.books[symbol]
        return 0.5 * (b['bid'] + b['ask'])

    async def best_bid_ask(self, symbol: str) -> tuple:
        b = self.books[symbol]
        return b['bid'], b['ask']

    # ---------------------------------------------------------------- orders
    def _check(self, symbol: str, side: str, qty: float, reduce_only: bool) -> None:
        if side not in ('BUY', 'SELL') or not qty > 0:
            raise VenueError(f'bad order {side} {qty}')
        if qty < self.min_qty:
            raise MinSizeError(f'{self.name} {symbol}: {qty} < minimum {self.min_qty}')
        if reduce_only:
            amt = self.positions.get(symbol, {}).get('amt', 0.0)
            if (side == 'BUY' and amt >= 0) or (side == 'SELL' and amt <= 0) or qty > abs(amt) + 1e-12:
                raise VenueError(f'{self.name} {symbol}: reduce-only {side} {qty} would not reduce {amt}')

    async def place_post_only(self, symbol: str, side: str, qty: float, price: float, cid: Optional[str] = None,
                              reduce_only: bool = False) -> dict:
        self.calls.append(('post_only', symbol, side, qty, price, reduce_only))
        self._check(symbol, side, qty, reduce_only)
        b = self.books[symbol]
        if (side == 'BUY' and price >= b['ask']) or (side == 'SELL' and price <= b['bid']):
            raise PostOnlyRejected(f'{self.name} {symbol}: {side} {price} would take liquidity')
        oid = cid or f'{self.name}-{next(self._ids)}'
        self.orders[oid] = {'symbol': symbol, 'side': side, 'qty': float(qty), 'price': float(price),
                            'filled': 0.0, 'notional': 0.0, 'status': 'NEW', 'reduce_only': reduce_only, 'maker': True}
        return {'venue': self.name, 'order_id': oid, 'status': 'NEW'}

    def fill_resting(self, order_id: str, qty: Optional[float] = None) -> float:
        """A trade went through the resting order: fill `qty` (default: the rest) at its own price. -> qty filled."""
        o = self.orders[order_id]
        if o['status'] not in ('NEW', 'PARTIALLY_FILLED'):
            return 0.0
        q = min(o['qty'] - o['filled'], o['qty'] if qty is None else float(qty))
        if q <= 0:
            return 0.0
        self._book_fill(o, q, o['price'], self.maker_bps)
        return q

    async def place_ioc_limit(self, symbol: str, side: str, qty: float, limit_px: float, cid: Optional[str] = None,
                              reduce_only: bool = False) -> dict:
        self.calls.append(('ioc', symbol, side, qty, limit_px, reduce_only))
        self._check(symbol, side, qty, reduce_only)
        b = self.books[symbol]
        touch, depth = (b['ask'], b['ask_qty']) if side == 'BUY' else (b['bid'], b['bid_qty'])
        ok = touch <= limit_px if side == 'BUY' else touch >= limit_px
        q = min(float(qty), depth * self.ioc_fill_fraction) if ok else 0.0
        oid = cid or f'{self.name}-{next(self._ids)}'
        o = {'symbol': symbol, 'side': side, 'qty': float(qty), 'price': float(limit_px), 'filled': 0.0,
             'notional': 0.0, 'status': 'NEW', 'reduce_only': reduce_only, 'maker': False}
        self.orders[oid] = o
        if q > 0:
            self._book_fill(o, q, touch, self.taker_bps)
        o['status'] = 'FILLED' if o['filled'] >= o['qty'] - 1e-12 else ('EXPIRED' if o['filled'] == 0 else
                                                                         'PARTIALLY_FILLED_EXPIRED')
        return {'venue': self.name, 'order_id': oid, 'status': o['status'], 'filled_qty': o['filled'],
                'avg_px': o['notional'] / o['filled'] if o['filled'] else None}

    async def order_status(self, symbol: str, order_id: str) -> dict:
        o = self.orders[order_id]
        if self.auto_fill and o.get('maker') and o['status'] in ('NEW', 'PARTIALLY_FILLED'):
            self.fill_resting(order_id)
        return {'status': o['status'], 'filled_qty': o['filled'],
                'avg_px': o['notional'] / o['filled'] if o['filled'] else None}

    async def cancel_order(self, symbol: str, order_id) -> None:
        o = self.orders.get(order_id)
        if o and o['status'] in ('NEW', 'PARTIALLY_FILLED'):
            o['status'] = 'CANCELED' if o['filled'] == 0 else 'PARTIALLY_FILLED_CANCELED'

    def _book_fill(self, o: dict, q: float, px: float, fee_bps: float) -> None:
        sym, sign = o['symbol'], 1.0 if o['side'] == 'BUY' else -1.0
        o['filled'] += q
        o['notional'] += q * px
        if o['status'] == 'NEW' or o['status'] == 'PARTIALLY_FILLED':
            o['status'] = 'FILLED' if o['filled'] >= o['qty'] - 1e-12 else 'PARTIALLY_FILLED'
        self.fees += q * px * fee_bps / 1e4
        p = self.positions.setdefault(sym, {'amt': 0.0, 'entry': 0.0})
        amt, new = p['amt'], p['amt'] + sign * q
        if amt == 0 or (amt > 0) == (sign > 0):                       # open / add: average the entry
            p['entry'] = (abs(amt) * p['entry'] + q * px) / abs(new)
        else:                                                         # reduce / flip: realize the closed part
            closed = min(q, abs(amt))
            self.realized += closed * (px - p['entry']) * (1.0 if amt > 0 else -1.0)
            if abs(new) > 1e-12 and (new > 0) != (amt > 0):
                p['entry'] = px
        p['amt'] = 0.0 if abs(new) < 1e-12 else new
        self.calls.append(('fill', sym, o['side'], q, px, o['reduce_only']))

    # ---------------------------------------------------------------- account
    def equity(self) -> float:
        upnl = sum(p['amt'] * (self.mark(s) - p['entry']) for s, p in self.positions.items() if p['amt'])
        return self.cash + self.realized + upnl - self.fees + self.funding

    def settle_funding(self, symbol: str, rate: float, t_ms: int) -> float:
        """Funding print `rate` (fraction per the venue's interval): longs pay shorts when rate > 0."""
        amt = self.positions.get(symbol, {}).get('amt', 0.0)
        cash = -amt * self.mark(symbol) * float(rate)
        self.funding += cash
        self.income.append({'symbol': symbol, 't': int(t_ms), 'amount': cash})
        return cash

    async def funding_income(self, since_ms: int = 0) -> list:
        return [dict(x) for x in self.income if x['t'] >= since_ms]

    async def margin_view(self) -> dict:
        eq = self.equity()
        maint = sum(abs(p['amt']) * self.mark(s) * self.mmr for s, p in self.positions.items() if p['amt'])
        return {'equity': eq, 'maint_margin': maint, 'margin_ratio': maint / eq if eq > 0 else None}

    async def position_view(self, symbol: str) -> dict:
        p = self.positions.get(symbol, {'amt': 0.0, 'entry': 0.0})
        amt, mark = p['amt'], self.mark(symbol)
        open_syms = [s for s, x in self.positions.items() if x['amt']]
        liq = None
        if amt and open_syms == [symbol]:
            cash = self.cash + self.realized - self.fees + self.funding
            a = abs(amt)
            liq = ((amt * p['entry'] - cash) / (amt * (1 - self.mmr)) if amt > 0 else
                   (cash + a * p['entry']) / (a * (1 + self.mmr)))
            liq = liq if liq > 0 else None
        return {'amt': amt, 'entry': p['entry'] if amt else None, 'mark': mark,
                'upnl': amt * (mark - p['entry']) if amt else 0.0, 'liq': liq}
