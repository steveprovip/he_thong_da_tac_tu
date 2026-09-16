from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
import math


def dec(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("Non-finite decimal")
    return result


def quantize(value, step, up=False):
    value, step = dec(value), dec(step)
    if step <= 0:
        raise ValueError("Step must be positive")
    return (value / step).to_integral_value(rounding=ROUND_CEILING if up else ROUND_FLOOR) * step


@dataclass(frozen=True)
class Filters:
    tick: Decimal
    step: Decimal
    min_qty: Decimal
    max_qty: Decimal
    min_notional: Decimal

    @classmethod
    def from_exchange(cls, item):
        fs = {f['filterType']: f for f in item['filters']}
        lot = fs['LOT_SIZE']
        return cls(dec(fs['PRICE_FILTER']['tickSize']), dec(lot['stepSize']),
                   dec(lot['minQty']), dec(lot['maxQty']), dec(fs['MIN_NOTIONAL']['notional']))


@dataclass(frozen=True)
class Book:
    symbol: str
    bids: tuple
    asks: tuple
    timestamp: float
    sequence: int = 0

    def __post_init__(self):
        if not self.bids or not self.asks or not math.isfinite(self.timestamp):
            raise ValueError("Empty/invalid book")
        for side in (self.bids, self.asks):
            if any(not math.isfinite(float(p)) or not math.isfinite(float(q)) or p <= 0 or q <= 0 for p, q in side):
                raise ValueError("Invalid price/volume")
        if any(self.bids[i][0] <= self.bids[i+1][0] for i in range(len(self.bids)-1)):
            raise ValueError("Bids must descend")
        if any(self.asks[i][0] >= self.asks[i+1][0] for i in range(len(self.asks)-1)):
            raise ValueError("Asks must ascend")
        if self.bid >= self.ask:
            raise ValueError("Crossed book")

    @property
    def bid(self):
        return float(self.bids[0][0])

    @property
    def ask(self):
        return float(self.asks[0][0])

    @property
    def mid(self):
        return (self.bid + self.ask) / 2

    @property
    def spread_bps(self):
        return (self.ask - self.bid) / self.mid * 10000


@dataclass(frozen=True)
class Intent:
    symbol: str
    side: str
    quantity: str
    price: str
    stop: str
    take_profit: str
    horizon: float
    mode: str

    def __post_init__(self):
        if self.side not in ('BUY', 'SELL') or self.mode not in ('paper', 'testnet', 'mainnet'):
            raise ValueError("Invalid order intent")
        if not self.symbol.isalnum() or not math.isfinite(self.horizon) or self.horizon <= 0:
            raise ValueError("Invalid symbol/horizon")
        q, p, sl, tp = map(dec, (self.quantity, self.price, self.stop, self.take_profit))
        if min(q, p, sl, tp) <= 0 or not (sl < p < tp if self.side == 'BUY' else tp < p < sl):
            raise ValueError("Invalid brackets/size")
