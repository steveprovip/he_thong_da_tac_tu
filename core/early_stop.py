"""Early-exit discipline for data collection: SPRT + Wilson 95% + data budget.

The loop this module breaks:  collect -> sweep -> trade -> fee drag / veto ->
loosen filter -> lose -> collect more "to find alpha".  Every collector (L2
recorder, shadow harvester, signal lab, carry scan) now answers ONE question
per sample -- "is there still a reason to keep collecting?" -- and stops the
moment the answer is no:

  REGIME_UNPROFITABLE  after >= min_samples signals, the 95% evidence says the
                       net-of-cost EV is not positive (UCB95 <= 0), or the
                       optimistic Wilson win rate cannot reach break-even, or
                       Wald's SPRT accepts H0 (mu = 0) against H1 (mu = delta).
  EDGE_CONFIRMED       LCB95 > 0 and SPRT accepts H1: more data buys nothing;
                       move to the next (human) gate instead of re-sampling.
  BUDGET_EXHAUSTED     the data budget (samples / seconds / bytes) is spent
                       without significance -- stop, do not extend.

Cost basis (Binance USD-M): maker 2 bps + taker 5 bps + ~1.5 bps spread and
slippage = COST_ALL_IN_BPS 8.5.  A strategy must show out-of-sample gross
edge > GROSS_EDGE_HURDLE_BPS (9.0) to be worth anything.
"""
from dataclasses import dataclass, field
import math
import os
import time

MAKER_FEE_BPS = 2.0
TAKER_FEE_BPS = 5.0
SPREAD_SLIPPAGE_BPS = 1.5
COST_ALL_IN_BPS = MAKER_FEE_BPS + TAKER_FEE_BPS + SPREAD_SLIPPAGE_BPS   # 8.5
GROSS_EDGE_HURDLE_BPS = 9.0
MIN_SIGNALS = 200
Z_95 = 1.959963984540054

CONTINUE = 'CONTINUE'
REGIME_UNPROFITABLE = 'REGIME_UNPROFITABLE'
EDGE_CONFIRMED = 'EDGE_CONFIRMED'
BUDGET_EXHAUSTED = 'BUDGET_EXHAUSTED'
TERMINAL = (REGIME_UNPROFITABLE, EDGE_CONFIRMED, BUDGET_EXHAUSTED)


def _finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def wilson_interval(successes, n, z=Z_95):
    """Wilson score interval for a binomial proportion. (0, 1) when n == 0."""
    if not isinstance(n, int) or isinstance(n, bool) or n < 0 or not 0 <= successes <= n:
        raise ValueError('invalid binomial counts')
    if n == 0:
        return 0.0, 1.0
    p = successes / n
    denom = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denom
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
    return max(0.0, centre - half), min(1.0, centre + half)


def breakeven_win_rate(avg_win_bps, avg_loss_bps):
    """p* = L / (W + L): the win rate at which E[net] = 0 for the observed payoffs."""
    if not _finite(avg_win_bps, avg_loss_bps) or avg_win_bps < 0 or avg_loss_bps < 0:
        raise ValueError('payoffs must be finite and non-negative')
    if avg_win_bps + avg_loss_bps == 0:
        return 1.0
    return avg_loss_bps / (avg_win_bps + avg_loss_bps)


def sprt_bounds(alpha=0.05, beta=0.05):
    """Wald boundaries (lower B, upper A) on the log-likelihood ratio."""
    if not 0 < alpha < 1 or not 0 < beta < 1:
        raise ValueError('alpha and beta must lie in (0, 1)')
    return math.log(beta / (1 - alpha)), math.log((1 - beta) / alpha)


@dataclass
class DataBudget:
    """Hard ceiling on what a collector may spend. 0 disables a dimension."""
    max_samples: int = 0
    max_seconds: float = 0.0
    max_bytes: int = 0
    started: float = field(default_factory=time.monotonic)
    samples: int = 0
    bytes: int = 0

    def __post_init__(self):
        if min(self.max_samples, self.max_seconds, self.max_bytes) < 0:
            raise ValueError('budget limits must be non-negative')

    def charge(self, samples=0, nbytes=0):
        if samples < 0 or nbytes < 0:
            raise ValueError('cannot refund a budget')
        self.samples += samples
        self.bytes += nbytes

    def exhausted(self, now=None):
        now = time.monotonic() if now is None else now
        if self.max_samples and self.samples >= self.max_samples:
            return f'SAMPLES {self.samples}>={self.max_samples}'
        if self.max_seconds and now - self.started >= self.max_seconds:
            return f'SECONDS {now - self.started:.0f}>={self.max_seconds:.0f}'
        if self.max_bytes and self.bytes >= self.max_bytes:
            return f'BYTES {self.bytes}>={self.max_bytes}'
        return None


def directory_bytes(path):
    """Bytes already on disk under `path` -- prior sessions count against the budget."""
    total = 0
    if not os.path.isdir(path):
        return 0
    for root, _, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                pass
    return total


@dataclass(frozen=True)
class GateVerdict:
    status: str
    reason: str
    n: int
    mean_bps: float
    lcb_bps: float
    ucb_bps: float
    win_rate: float
    win_ucb: float
    breakeven: float
    llr: float

    @property
    def stop(self):
        return self.status in TERMINAL

    def as_dict(self):
        return {k: (round(v, 4) if isinstance(v, float) and math.isfinite(v) else v)
                for k, v in self.__dict__.items()}


class EarlyStopGate:
    """Sequential stop rule over per-signal NET bps (already after all costs).

    Streaming (Welford) mean/variance; O(1) per update, no sample storage.
    Nothing is decided before `min_samples` except the budget -- a small
    sample never licenses a verdict in either direction.
    """

    def __init__(self, min_samples=MIN_SIGNALS, min_edge_bps=2.0, alpha=0.05, beta=0.05,
                 min_win_rate=None, budget=None, z=Z_95):
        if not isinstance(min_samples, int) or min_samples < 2:
            raise ValueError('min_samples must be an int >= 2')
        if not _finite(min_edge_bps, z) or min_edge_bps <= 0 or z <= 0:
            raise ValueError('min_edge_bps and z must be positive')
        if min_win_rate is not None and not 0 <= min_win_rate <= 1:
            raise ValueError('min_win_rate must lie in [0, 1]')
        self.min_samples, self.delta, self.z = min_samples, float(min_edge_bps), z
        self.lower, self.upper = sprt_bounds(alpha, beta)
        self.min_win_rate = min_win_rate
        self.budget = budget
        self.n = 0
        self.mean = 0.0
        self._m2 = 0.0
        self.wins = 0
        self.win_sum = 0.0
        self.loss_sum = 0.0
        self.verdict = None

    def update(self, net_bps, nbytes=0, now=None):
        if not _finite(net_bps):
            raise ValueError('net_bps must be finite')
        if self.verdict is not None and self.verdict.stop:
            return self.verdict
        self.n += 1
        d = net_bps - self.mean
        self.mean += d / self.n
        self._m2 += d * (net_bps - self.mean)
        if net_bps > 0:
            self.wins += 1
            self.win_sum += net_bps
        else:
            self.loss_sum -= net_bps
        if self.budget is not None:
            self.budget.charge(1, nbytes)
        self.verdict = self.evaluate(now)
        return self.verdict

    @property
    def sd(self):
        return math.sqrt(self._m2 / (self.n - 1)) if self.n > 1 else 0.0

    def llr(self):
        """Gaussian SPRT, H0: mu=0 vs H1: mu=delta, plug-in sigma (floored at 1 bps)."""
        if self.n < 2:
            return 0.0
        var = max(self.sd, 1.0) ** 2
        return self.delta / var * (self.n * self.mean - self.n * self.delta / 2)

    def evaluate(self, now=None):
        n = self.n
        se = self.sd / math.sqrt(n) if n > 1 else math.inf
        lcb, ucb = self.mean - self.z * se, self.mean + self.z * se
        _, win_ucb = wilson_interval(self.wins, n, self.z)
        losses = n - self.wins
        breakeven = (self.min_win_rate if self.min_win_rate is not None else
                     breakeven_win_rate(self.win_sum / self.wins if self.wins else 0.0,
                                        self.loss_sum / losses if losses else 0.0))
        llr = self.llr()
        base = dict(n=n, mean_bps=self.mean, lcb_bps=lcb, ucb_bps=ucb,
                    win_rate=self.wins / n if n else 0.0, win_ucb=win_ucb,
                    breakeven=breakeven, llr=llr)
        spent = self.budget.exhausted(now) if self.budget is not None else None
        if n >= self.min_samples:
            if ucb <= 0:
                return GateVerdict(REGIME_UNPROFITABLE, f'EV_UCB95 {ucb:.2f}<=0 bps', **base)
            if self.wins < n and win_ucb < breakeven:
                return GateVerdict(REGIME_UNPROFITABLE,
                                   f'WILSON_UCB95 {win_ucb:.3f}<breakeven {breakeven:.3f}', **base)
            if llr <= self.lower:
                return GateVerdict(REGIME_UNPROFITABLE, f'SPRT_ACCEPT_H0 llr {llr:.2f}<={self.lower:.2f}', **base)
            if lcb > 0 and llr >= self.upper:
                return GateVerdict(EDGE_CONFIRMED, f'LCB95 {lcb:.2f}>0 and SPRT_ACCEPT_H1', **base)
        if spent:
            tag = 'INCONCLUSIVE' if not (n and lcb > 0) else 'LCB95>0'
            return GateVerdict(BUDGET_EXHAUSTED, f'{spent} · {tag}', **base)
        return GateVerdict(CONTINUE, f'n={n}', **base)


def replay(values, **gate_kwargs):
    """Run the gate over a recorded sequence. Returns (stop_index or None, verdict).

    stop_index is the 1-based count of samples after which collection would
    have ended; len(values) - stop_index is what was collected for nothing.
    """
    gate = EarlyStopGate(**gate_kwargs)
    verdict = None
    for i, v in enumerate(values, 1):
        verdict = gate.update(v)
        if verdict.stop:
            return i, verdict
    return None, verdict
