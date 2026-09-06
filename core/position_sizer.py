from collections import deque
from dataclasses import dataclass
import math
from core.models import dec, quantize


@dataclass(frozen=True)
class SizeResult:
    quantity: str
    notional: float
    reason: str


class PositionSizer:
    def __init__(self, fraction=.25, min_samples=20, max_position=.15, risk_per_trade=.005):
        if not .10 <= fraction <= .25 or min_samples < 2 or not 0 < max_position <= .75 or not 0 < risk_per_trade < 1:
            raise ValueError('Invalid sizing configuration')
        self.fraction, self.min_samples = fraction, min_samples
        self.max_position, self.risk_per_trade = max_position, risk_per_trade
        self.outcomes = deque(maxlen=200)

    def record(self, net_pnl):
        if not math.isfinite(net_pnl):
            raise ValueError('Invalid PnL')
        self.outcomes.append(net_pnl)

    def kelly(self):
        if len(self.outcomes) < self.min_samples:
            return None
        wins, losses = [x for x in self.outcomes if x > 0], [-x for x in self.outcomes if x < 0]
        if not wins:
            return 0.0
        if not losses:
            return self.max_position
        p = len(wins)/len(self.outcomes)
        b = (sum(wins)/len(wins))/(sum(losses)/len(losses))
        return min(self.max_position, max(0, self.fraction*(p*b-(1-p))/b))

    def size(self, equity, used_margin, price, stop, filters, bootstrap=False, available=None, roundtrip_fee=.0007):
        if any(not math.isfinite(v) for v in (equity, used_margin, price, stop, roundtrip_fee)) or equity <= 0 or min(price, stop) <= 0 or used_margin < 0:
            return SizeResult('0', 0, 'INVALID_CAPITAL')
        kelly = self.kelly()
        if kelly is None and not bootstrap:
            return SizeResult('0', 0, 'KELLY_WARMUP')
        if kelly is not None and kelly <= 0:
            return SizeResult('0', 0, 'NONPOSITIVE_EDGE')
        minimum = filters.min_notional*dec('1.10')
        target = max(minimum, dec(equity*(kelly if kelly is not None else .01)))
        # Use unlevered capacity even when venue account leverage is larger.
        capacity = min(equity*self.max_position, max(0, equity*.75-used_margin),
                       available if available is not None else equity,
                       equity*self.risk_per_trade / max(abs(price-stop)/price+roundtrip_fee, 1e-9))
        qty = max(filters.min_qty, quantize(target/dec(price), filters.step, up=True))
        notional = qty*dec(price)
        if qty > filters.max_qty or notional > dec(capacity) or notional < minimum:
            return SizeResult('0', 0, 'MIN_NOTIONAL_EXCEEDS_RISK_CAP')
        return SizeResult(format(qty, 'f'), float(notional), 'OK')


class DrawdownBrake:
    def __init__(self, max_drawdown=.08, max_losses=10):
        self.max_drawdown, self.max_losses = max_drawdown, max_losses
        self.peak = 0.0
        self.losses = 0
        self.halted = False

    def update(self, equity, pnl=None):
        if not math.isfinite(equity) or equity <= 0:
            self.halted = True
            return True
        self.peak = max(self.peak, equity)
        if pnl is not None:
            self.losses = self.losses+1 if pnl < 0 else 0
        self.halted = self.halted or (1-equity/self.peak >= self.max_drawdown) or self.losses >= self.max_losses
        return self.halted
