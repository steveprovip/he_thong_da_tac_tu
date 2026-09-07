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
