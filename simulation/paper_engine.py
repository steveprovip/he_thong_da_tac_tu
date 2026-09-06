from dataclasses import dataclass
import time
from core.models import Intent


@dataclass
class PaperOrder:
    intent: Intent
    submitted: float
    queue_ahead: float
    remaining: float
    filled: float = 0
    active: bool = True
    fees: float = 0
    realized: float = 0


class PaperEngine:
    """FIFO approximation: visible queue must trade away; book disappearance is not a fill."""
    def __init__(self, equity=10000.0, maker_fee=.0002, taker_fee=.0005, latency=.15):
        self.initial = self.cash = equity
        self.maker_fee, self.taker_fee, self.latency = maker_fee, taker_fee, latency
        self.orders = {}
        self.trade_ids = {}
        self.closed = []
        self.volume = self.fees = 0.0

    def submit(self, intent, book, now):
        if intent.mode != 'paper' or intent.symbol in self.orders:
            raise ValueError('Paper intent unavailable')
        price = float(intent.price)
        if (intent.side == 'BUY' and price >= book.ask) or (intent.side == 'SELL' and price <= book.bid):
            raise ValueError('Post-only would cross')
        levels = book.bids if intent.side == 'BUY' else book.asks
        queue = sum(q for p, q in levels if p == price)
        self.orders[intent.symbol] = PaperOrder(intent, now, queue, float(intent.quantity))

    def trade(self, symbol, price, quantity, buyer_aggressor, now, trade_id):
        if trade_id <= self.trade_ids.get(symbol, -1):
            return 0.0
        self.trade_ids[symbol] = trade_id
        order = self.orders.get(symbol)
        if not order or not order.active or now < order.submitted+self.latency:
            return 0.0
        entry = float(order.intent.price)
        eligible = (not buyer_aggressor and price <= entry) if order.intent.side == 'BUY' else (buyer_aggressor and price >= entry)
        if not eligible:
            return 0.0
        consumed = min(order.queue_ahead, quantity)
        order.queue_ahead -= consumed
        fill = min(order.remaining, quantity-consumed)
        order.remaining -= fill
        order.filled += fill
        fee = fill*entry*self.maker_fee
        order.fees += fee
        self.fees += fee
        self.cash -= fee
        self.volume += fill*entry
        if order.remaining <= 1e-12:
            order.active = False
        return fill

    def cancel(self, symbol):
        order = self.orders.get(symbol)
        if order:
            order.active = False
            if order.filled <= 1e-12:
                self.orders.pop(symbol)

    def close(self, symbol, book):
        order = self.orders.get(symbol)
        if not order:
            return None
        self.cancel(symbol)
        if order.filled <= 1e-12:
            return None
        levels = book.bids if order.intent.side == 'BUY' else book.asks
        remaining = order.filled
        gross = exit_value = 0.0
        sign = 1 if order.intent.side == 'BUY' else -1
        for price, available in levels:
            size = min(remaining, available)
            gross += sign*(price-float(order.intent.price))*size
            exit_value += price*size
            remaining -= size
            if remaining <= 1e-12:
                break
        # No invented liquidity beyond visible depth: residual is retained for next book.
        fee = exit_value*self.taker_fee
        self.cash += gross-fee
        self.fees += fee
        self.volume += exit_value
        order.realized += gross
        order.fees += fee
        order.filled = remaining
        if remaining > 1e-12:
            return None
        result = {'symbol': symbol, 'net_pnl': order.realized-order.fees, 'fees': order.fees, 'timestamp': book.timestamp}
        self.closed.append(result)
        self.orders.pop(symbol, None)
        return result

    def equity(self, books):
        return self.cash+sum((1 if o.intent.side == 'BUY' else -1)*(books[s].mid-float(o.intent.price))*o.filled
                             for s, o in self.orders.items() if s in books)

    def transfer(self, amount):
        if self.orders or self.cash+amount <= 0:
            raise ValueError('Virtual cash adjustment requires flat positive account')
        self.cash += amount
        self.initial += amount
