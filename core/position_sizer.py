from collections import deque
from dataclasses import dataclass
import math
import random
from core.models import dec, quantize


@dataclass(frozen=True)
class SizeResult:
    quantity: str
    notional: float
    reason: str


class PositionSizer:
    """Three-state edge lifecycle; the interval, never the mean, grants position size.

    At n=20 the settled ledger has SE = 3.5 bps against a 15.7 bps spread, so a
    point estimate of the mean is indistinguishable from noise for anything
    smaller than a ~10 bps edge. Sizing off that mean is reading noise. So:

      n < min_samples ................. kelly() is None -> caller uses the fixed
                                        bootstrap fraction (small, uncalibrated)
      n >= min_samples, CI low > 0 .... kelly() returns fractional Kelly
      n >= min_samples, CI straddles 0  kelly() is None -> keep collecting at the
                                        bootstrap fraction; an undetermined edge
                                        is not permission to size up
      n >= stop_samples, CI high < 0 .. kelly() returns 0 -> proven negative,
                                        stop. This is the termination rule that
                                        keeps 'collect more data' from running
                                        forever.
    """

    def __init__(self, fraction=.25, min_samples=60, max_position=.15, risk_per_trade=.005,
                 stop_samples=120, ci_draws=2000, ci_seed=17):
        if not .10 <= fraction <= .25 or min_samples < 2 or not 0 < max_position <= .75 or not 0 < risk_per_trade < 1:
            raise ValueError('Invalid sizing configuration')
        if stop_samples < min_samples or ci_draws < 100:
            raise ValueError('Invalid edge-interval configuration')
        self.fraction, self.min_samples = fraction, min_samples
        self.max_position, self.risk_per_trade = max_position, risk_per_trade
        self.stop_samples, self.ci_draws, self.ci_seed = stop_samples, ci_draws, ci_seed
        self.outcomes = deque(maxlen=200)
        self._interval = None

    def record(self, net_pnl, notional=None):
        if not math.isfinite(net_pnl) or (notional is not None and
                                          (not math.isfinite(notional) or notional <= 0)):
            raise ValueError('Invalid PnL')
        # Returns make the estimator invariant to account size and position size.
        self.outcomes.append(net_pnl / notional if notional is not None else net_pnl)
        self._interval = None

    def edge_interval(self):
        """Seeded percentile bootstrap on the mean net return; cached per sample.

        Deterministic so the same ledger always yields the same permission, and
        cached so the per-cycle sizing path does not pay for 2000 resamples.
        """
        if self._interval is not None:
            return self._interval
        values = list(self.outcomes)
        if len(values) < 2:
            self._interval = (float('-inf'), float('inf'))
            return self._interval
        rng = random.Random(self.ci_seed)
        n = len(values)
        means = sorted(math.fsum(rng.choices(values, k=n)) / n for _ in range(self.ci_draws))
        self._interval = (means[int(.025 * self.ci_draws)], means[int(.975 * self.ci_draws) - 1])
        return self._interval

    def edge_verdict(self):
        """'WARMUP' | 'UNDETERMINED' | 'POSITIVE' | 'NEGATIVE' -- the sizing state."""
        if len(self.outcomes) < self.min_samples:
            return 'WARMUP'
        low, high = self.edge_interval()
        if low > 0:
            return 'POSITIVE'
        if high < 0 and len(self.outcomes) >= self.stop_samples:
            return 'NEGATIVE'
        return 'UNDETERMINED'

    def tail_statistics(self):
        values = list(self.outcomes)
        if len(values) < 2:
            return {'skewness': 0.0, 'excess_kurtosis': 0.0}
        mean = sum(values) / len(values)
        centered = [x - mean for x in values]
        variance = sum(x*x for x in centered) / len(values)
        if variance <= 1e-24:
            return {'skewness': 0.0, 'excess_kurtosis': 0.0}
        m3 = sum(x**3 for x in centered) / len(values)
        m4 = sum(x**4 for x in centered) / len(values)
        sigma = math.sqrt(variance)
        return {'skewness': m3 / sigma**3, 'excess_kurtosis': m4 / variance**2 - 3.0}

    def kelly(self):
        verdict = self.edge_verdict()
        if verdict in ('WARMUP', 'UNDETERMINED'):
            # None means "not calibrated": the caller may still use the fixed
            # bootstrap fraction. It never means "edge is zero".
            return None
        if verdict == 'NEGATIVE':
            # Proven negative over stop_samples. Zero is a stop, not a warm-up.
            return 0.0
        wins, losses = [x for x in self.outcomes if x > 0], [-x for x in self.outcomes if x < 0]
        if not wins:
            return 0.0
        if not losses:
            return self.max_position
        p = len(wins)/len(self.outcomes)
        b = (sum(wins)/len(wins))/(sum(losses)/len(losses))
        legacy = max(0, self.fraction*(p*b-(1-p))/b)
        tail = self.tail_statistics()
        # Penalize only a genuinely dangerous tail: negative skew together with
        # positive excess kurtosis. A thin, symmetric/binary distribution keeps
        # the legacy estimate so this adjustment cannot punish ordinary samples.
        tail_risk = (max(0., -tail['skewness']) + max(0., tail['excess_kurtosis'])
                     if tail['skewness'] < 0 and tail['excess_kurtosis'] > 0 else 0.)
        adjusted = legacy / (1 + tail_risk)
        return min(self.max_position, max(0, adjusted))

    def size(self, equity, used_margin, price, stop, filters, bootstrap=False, available=None, roundtrip_fee=.0007, bootstrap_fraction=.01):
        if any(not math.isfinite(v) for v in (equity, used_margin, price, stop, roundtrip_fee, bootstrap_fraction)) or equity <= 0 or min(price, stop) <= 0 or used_margin < 0 or (available is not None and (not math.isfinite(available) or available < 0)) or not 0 < bootstrap_fraction <= .25:
            return SizeResult('0', 0, 'INVALID_CAPITAL')
        kelly = self.kelly()
        if kelly is None and not bootstrap:
            return SizeResult('0', 0, 'KELLY_WARMUP')
        if kelly is not None and kelly <= 0:
            return SizeResult('0', 0, 'NONPOSITIVE_EDGE')
        minimum = filters.min_notional*dec('1.10')
        raw_target = dec(equity*(kelly if kelly is not None else bootstrap_fraction))
        # Never manufacture edge by rounding a sub-minimum Kelly allocation upward.
        if kelly is not None and raw_target < minimum:
            return SizeResult('0', 0, 'KELLY_BELOW_MIN_NOTIONAL')
        target = max(minimum, raw_target)
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
