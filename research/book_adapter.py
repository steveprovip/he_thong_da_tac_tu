"""Turn an hftbacktest market depth into the same Book the live engine consumes.

The point of the research tier is that the *same* AlphaEngine and swarm code runs
in backtest and live. That only holds if both are fed identical structures, so the
adapter builds a real `core.models.Book` -- validation and all -- rather than a
look-alike. A book the live engine would reject is rejected here too.
"""
import math

from core.models import Book

DEPTH_LEVELS = 5


def top_levels(depth, best_tick, step, quantity_at, tick_size, levels=DEPTH_LEVELS, scan=400):
    """Walk outward from the touch collecting the first `levels` non-empty prices."""
    out = []
    tick = best_tick
    for _ in range(scan):
        if len(out) >= levels:
            break
        quantity = quantity_at(tick)
        if quantity > 0:
            price = tick * tick_size
            if price > 0 and math.isfinite(price) and math.isfinite(quantity):
                out.append((price, float(quantity)))
        tick += step
    return tuple(out)


def build_book(symbol, depth, timestamp, sequence, levels=DEPTH_LEVELS):
    """Best-effort Book, or None when the venue state is unusable.

    Returning None rather than a patched-up book matters: a crossed or empty book
    in the backtest must be skipped exactly as the live runtime skips it, otherwise
    the research tier would trade on states production never sees.
    """
    best_bid_tick, best_ask_tick = depth.best_bid_tick, depth.best_ask_tick
    if best_bid_tick <= 0 or best_ask_tick <= 0 or best_bid_tick >= best_ask_tick:
        return None
    tick_size = depth.tick_size
    bids = top_levels(depth, best_bid_tick, -1, depth.bid_qty_at_tick, tick_size, levels)
    asks = top_levels(depth, best_ask_tick, 1, depth.ask_qty_at_tick, tick_size, levels)
    if len(bids) < 1 or len(asks) < 1:
        return None
    try:
        return Book(symbol, bids, asks, timestamp, sequence)
    except ValueError:
        return None


class MinuteBars:
    """Close 1-minute OHLC bars from the mid price, for the ATR/RSI/momentum block."""

    def __init__(self, seconds=60.0):
        self.seconds = seconds
        self.bucket = None
        self.high = self.low = self.close = 0.0

    def update(self, timestamp, mid):
        """Return a closed (timestamp, high, low, close) bar, or None."""
        bucket = math.floor(timestamp / self.seconds)
        if self.bucket is None:
            self.bucket, self.high, self.low, self.close = bucket, mid, mid, mid
            return None
        if bucket == self.bucket:
            self.high, self.low, self.close = max(self.high, mid), min(self.low, mid), mid
            return None
        closed = ((self.bucket + 1) * self.seconds, self.high, self.low, self.close)
        self.bucket, self.high, self.low, self.close = bucket, mid, mid, mid
        return closed
