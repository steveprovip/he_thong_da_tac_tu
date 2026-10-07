from dataclasses import dataclass
import math
from core.models import quantize


@dataclass(frozen=True)
class Barriers:
    stop: float
    take_profit: float
    deadline: float


class BarrierPlanner:
    def __init__(self, tp_atr=2.5, sl_atr=1.5, horizon=300.0, roundtrip_fee=.0007):
        if any(not math.isfinite(x) or x <= 0 for x in (tp_atr, sl_atr, horizon)) or not math.isfinite(roundtrip_fee) or roundtrip_fee < 0:
            raise ValueError('Invalid barrier settings')
        self.tp_atr, self.sl_atr, self.horizon, self.fee = tp_atr, sl_atr, horizon, roundtrip_fee

    def plan(self, side, entry, atr, tick, now):
        if side not in ('BUY', 'SELL') or not all(math.isfinite(x) for x in (entry, atr, tick, now)) or min(entry, atr, tick) <= 0:
            raise ValueError('Invalid barrier inputs')
        sign = 1 if side == 'BUY' else -1
        stop = float(quantize(entry-sign*max(self.sl_atr*atr, 2*tick), tick, up=sign < 0))
        # Fee floor is a target constraint, NOT evidence that the move is attainable.
        tp = float(quantize(entry+sign*max(self.tp_atr*atr, entry*self.fee+2*tick), tick, up=sign > 0))
        if min(stop, tp) <= 0:
            raise ValueError('Non-positive barrier')
        return Barriers(stop, tp, now+self.horizon)

    def update(self, side, entry, mark, tick, opened, now, initial, current_stop):
        sign = 1 if side == 'BUY' else -1
        elapsed = max(0.0, min(1.0, (now-opened)/self.horizon))
        # Inventory-horizon-inspired policy, not the full A-S HJB solution.
        distance = max(abs(initial.take_profit-entry)*(1-.75*elapsed), entry*self.fee+2*tick)
        tp = entry+sign*distance
        stop = current_stop
        if sign*(mark-entry)/entry >= .0025:
            candidate = float(quantize(entry*(1+sign*self.fee), tick, up=sign > 0))
            if sign*(mark-candidate) >= 2*tick:
                stop = max(stop, candidate) if sign > 0 else min(stop, candidate)
        return Barriers(stop, float(quantize(tp, tick, up=sign > 0)), initial.deadline)


def reservation_price(mid, inventory, risk_aversion, variance_per_second, remaining_seconds):
    if not all(math.isfinite(x) for x in (mid, inventory, risk_aversion, variance_per_second, remaining_seconds)) or mid <= 0 or min(risk_aversion, variance_per_second, remaining_seconds) < 0:
        raise ValueError('Invalid A-S parameters')
    result = mid-inventory*risk_aversion*variance_per_second*remaining_seconds
    if not math.isfinite(result) or result <= 0:
        raise ValueError('Non-positive reservation price')
    return result


def maker_quote(book, side, tick, inventory, risk_aversion, variance_per_second, remaining_seconds):
    """A-S reservation skew, not a fitted HJB spread/arrival-intensity model.

    q is signed base quantity, gamma is inverse quote currency, sigma^2 is
    absolute price variance/second. No inferred queue alpha or crossed quotes.
    """
    if side not in ('BUY', 'SELL') or not math.isfinite(tick) or tick <= 0:
        raise ValueError('Invalid quote parameters')
    reserve = reservation_price(book.mid, inventory, risk_aversion, variance_per_second, remaining_seconds)
    half_spread = (book.ask-book.bid)/2
    price = min(book.bid, reserve-half_spread) if side == 'BUY' else max(book.ask, reserve+half_spread)
    quote = quantize(price, tick, up=side == 'SELL')
    if quote <= 0 or (side == 'BUY' and quote >= book.ask) or (side == 'SELL' and quote <= book.bid):
        raise ValueError('Invalid passive quote')
    return quote


def top_of_book(book):
    """(bid, ask, bid_qty, ask_qty) from a core.models.Book or any object exposing them."""
    bq = getattr(book, 'bid_qty', None)
    aq = getattr(book, 'ask_qty', None)
    if bq is None or aq is None:
        bq, aq = float(book.bids[0][1]), float(book.asks[0][1])
    return float(book.bid), float(book.ask), float(bq), float(aq)


def queue_imbalance(book):
    """I = (Qb - Qa) / (Qb + Qa) in [-1, 1]; > 0 means resting bids dominate."""
    _, _, bq, aq = top_of_book(book)
    total = bq + aq
    if not math.isfinite(total) or total <= 0:
        raise ValueError('Invalid top-of-book quantities')
    return (bq - aq) / total


def microstructure_quote(book, side, tick, inventory=0.0, risk_aversion=0.0,
                         variance_per_second=0.0, remaining_seconds=0.0, toxicity=0.30):
    """Microprice-anchored A-S quote with an adverse-selection stand-aside.

    maker_quote() anchors the reservation price on the MID, so with zero
    inventory it collapses to "join the touch" whatever the queue says -- the
    exact quote a one-way market runs over.  Here the reservation price is the
    queue-weighted microprice  m = (bid*Qa + ask*Qb) / (Qb + Qa),  which already
    leans toward the side about to break, then the A-S inventory skew is applied:

        r = m - q * gamma * sigma^2 * T
        BUY  quote = min(bid, r - s/2)      SELL quote = max(ask, r + s/2)

    When the queue leans against us (signed imbalance < -toxicity) the book is
    about to trade THROUGH our level: a fill there is adverse selection by
    construction, so the function returns None (stand aside) instead of a price.
    toxicity=None disables the stand-aside (skew only).
    """
    if side not in ('BUY', 'SELL') or not math.isfinite(tick) or tick <= 0:
        raise ValueError('Invalid quote parameters')
    if toxicity is not None and (not math.isfinite(toxicity) or not 0 <= toxicity <= 1):
        raise ValueError('toxicity must lie in [0, 1]')
    bid, ask, bq, aq = top_of_book(book)
    imbalance = queue_imbalance(book)
    signed = imbalance if side == 'BUY' else -imbalance
    if toxicity is not None and signed < -toxicity:
        return None
    micro = (bid * aq + ask * bq) / (bq + aq)
    reserve = reservation_price(micro, inventory, risk_aversion, variance_per_second, remaining_seconds)
    half_spread = (ask - bid) / 2
    price = min(bid, reserve - half_spread) if side == 'BUY' else max(ask, reserve + half_spread)
    quote = quantize(price, tick, up=side == 'SELL')
    if quote <= 0 or (side == 'BUY' and quote >= ask) or (side == 'SELL' and quote <= bid):
        raise ValueError('Invalid passive quote')
    return quote
