"""System-1 fast reflex: deterministic Boolean / Score gates in microseconds.

System 2 (Claude / TradingAgents slow brain) decides regime and portfolio on a
minutes-to-hours clock.  System 1 answers two questions on every tick, with
no network, no LLM and no token cost:

  is_order_adverse_selected(book_depth, trade_flow, side) -> bool
      True  = do NOT rest/keep this quote: the resting queue or the aggressor
      flow leans against our side (the queue is about to be traded THROUGH).

  regime_profitability_score(spread_bps, atr_bps, funding_bps_8h, ...) -> 0..10
      Monotone map of the best channel's expected gross / all-in cost ratio r:
          score = 10 * (1 - exp(-r / K)),  K chosen so that r = 3  <=>  8.5
      so "score >= 8.5" is exactly the invariant "gross edge > 3x cost".

JevGatekeeper wraps both with a latency ledger and an optional external
backend (e.g. the JEV typesafe SDK).  An external call that errors or blows
the 10 ms budget is ignored and the local rule answers -- System 1 must never
be slower or less available than the deterministic floor it replaces.
"""
from dataclasses import dataclass, field
import math
import statistics
import time

from core.early_stop import COST_ALL_IN_BPS

PERMIT_SCORE = 8.5
RATIO_AT_PERMIT = 3.0
SCORE_K = RATIO_AT_PERMIT / -math.log(1 - PERMIT_SCORE / 10)     # ~1.5813
LATENCY_BUDGET_NS = 10_000_000                                  # 10 ms


def _finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def _levels(side_levels, depth):
    total = 0.0
    for level in list(side_levels)[:depth]:
        qty = float(level[1])
        if not math.isfinite(qty) or qty < 0:
            raise ValueError('invalid depth quantity')
        total += qty
    return total


def depth_imbalance(book_depth, depth=5):
    """(Qbid - Qask) / (Qbid + Qask) over the first `depth` levels, in [-1, 1].

    `book_depth` is a core.models.Book or any object/dict with `bids`/`asks`
    as [(price, qty), ...] best first."""
    bids = book_depth['bids'] if isinstance(book_depth, dict) else book_depth.bids
    asks = book_depth['asks'] if isinstance(book_depth, dict) else book_depth.asks
    qb, qa = _levels(bids, depth), _levels(asks, depth)
    if qb + qa <= 0:
        raise ValueError('empty book')
    return (qb - qa) / (qb + qa)


def flow_imbalance(trade_flow):
    """Signed aggressor imbalance in [-1, 1]; > 0 = buyers lifting offers.

    `trade_flow` is either an iterable of signed quantities (+buy / -sell
    aggressor) or a dict {'buy': qty, 'sell': qty}."""
    if isinstance(trade_flow, dict):
        buy, sell = float(trade_flow.get('buy', 0.0)), float(trade_flow.get('sell', 0.0))
    else:
        buy = sell = 0.0
        for q in trade_flow:
            q = float(q)
            if q >= 0:
                buy += q
            else:
                sell -= q
    if not _finite(buy, sell) or buy < 0 or sell < 0:
        raise ValueError('invalid trade flow')
    return 0.0 if buy + sell == 0 else (buy - sell) / (buy + sell)


def is_order_adverse_selected(book_depth, trade_flow, side, depth_toxicity=0.7,
                              flow_toxicity=0.6, depth=5):
    """True when a passive `side` quote would be run over (stand aside / cancel).

    Fail-closed: malformed input answers True."""
    if side not in ('BUY', 'SELL'):
        return True
    try:
        d = depth_imbalance(book_depth, depth)
        f = flow_imbalance(trade_flow)
    except (ValueError, TypeError, KeyError, IndexError, AttributeError):
        return True
    sign = 1.0 if side == 'BUY' else -1.0
    return sign * d < -depth_toxicity or sign * f < -flow_toxicity


def score_from_ratio(ratio):
    if not _finite(ratio) or ratio <= 0:
        return 0.0
    return 10.0 * (1.0 - math.exp(-ratio / SCORE_K))


def regime_profitability_score(spread_bps, atr_bps, funding_bps_8h=0.0, funding_periods=21,
                               carry_cost_bps=2 * COST_ALL_IN_BPS, scalp_gross_bps=None,
                               scalp_cost_bps=COST_ALL_IN_BPS, spread_band=(2.0, 8.0),
                               atr_band=(6.0, 20.0)):
    """Best-channel profitability score in [0, 10]; >= 8.5 <=> gross > 3x cost.

    Channels: CARRY (|funding| x settlements vs two-leg cost) and SCALP (only
    with measured forward gross, and only inside the spread/ATR bands).  No
    evidence contributes zero -- the score never invents an edge."""
    if not _finite(spread_bps, atr_bps, funding_bps_8h, carry_cost_bps, scalp_cost_bps):
        return 0.0
    if not isinstance(funding_periods, int) or funding_periods < 0 or carry_cost_bps <= 0 or scalp_cost_bps <= 0:
        return 0.0
    carry_ratio = abs(funding_bps_8h) * funding_periods / carry_cost_bps
    scalp_ratio = 0.0
    if (scalp_gross_bps is not None and _finite(scalp_gross_bps)
            and spread_band[0] <= spread_bps <= spread_band[1] and atr_band[0] <= atr_bps <= atr_band[1]):
        scalp_ratio = max(0.0, scalp_gross_bps) / scalp_cost_bps
    return score_from_ratio(max(carry_ratio, scalp_ratio))


@dataclass
class LatencyLedger:
    samples_ns: list = field(default_factory=list)
    fallbacks: int = 0
    calls: int = 0

    def add(self, ns):
        self.calls += 1
        self.samples_ns.append(ns)
        if len(self.samples_ns) > 100_000:
            del self.samples_ns[:50_000]

    def summary(self):
        if not self.samples_ns:
            return {'calls': self.calls, 'fallbacks': self.fallbacks}
        s = sorted(self.samples_ns)
        pick = lambda q: s[min(len(s) - 1, int(q * len(s)))] / 1e3
        return {'calls': self.calls, 'fallbacks': self.fallbacks,
                'p50_us': round(pick(.50), 2), 'p99_us': round(pick(.99), 2),
                'max_us': round(s[-1] / 1e3, 2), 'mean_us': round(statistics.fmean(s) / 1e3, 2)}


class JevGatekeeper:
    """System-1 gate. `backend(kind, payload) -> value` is optional (JEV SDK hook).

    The backend answer is used only when it returns a value of the right type
    inside `budget_ns`; otherwise the deterministic local rule is authoritative.
    For the adverse-selection Boolean the two are OR-ed: an external model may
    add a block, never remove one."""

    def __init__(self, backend=None, budget_ns=LATENCY_BUDGET_NS, permit_score=PERMIT_SCORE):
        if budget_ns <= 0 or not 0 < permit_score <= 10:
            raise ValueError('invalid gatekeeper configuration')
        self.backend = backend
        self.budget_ns = budget_ns
        self.permit_score = permit_score
        self.ledger = {'adverse': LatencyLedger(), 'score': LatencyLedger()}

    def _external(self, kind, payload, expected):
        if self.backend is None:
            return None
        t0 = time.perf_counter_ns()
        try:
            value = self.backend(kind, payload)
        except Exception:
            self.ledger[kind].fallbacks += 1
            return None
        if time.perf_counter_ns() - t0 > self.budget_ns or not isinstance(value, expected) or isinstance(value, bool) != (expected is bool):
            self.ledger[kind].fallbacks += 1
            return None
        return value

    def adverse(self, book_depth, trade_flow, side, **kw):
        t0 = time.perf_counter_ns()
        local = is_order_adverse_selected(book_depth, trade_flow, side, **kw)
        ext = self._external('adverse', {'book': book_depth, 'flow': trade_flow, 'side': side}, bool)
        result = local or bool(ext)
        self.ledger['adverse'].add(time.perf_counter_ns() - t0)
        return result

    def score(self, spread_bps, atr_bps, funding_bps_8h=0.0, **kw):
        t0 = time.perf_counter_ns()
        local = regime_profitability_score(spread_bps, atr_bps, funding_bps_8h, **kw)
        ext = self._external('score', {'spread_bps': spread_bps, 'atr_bps': atr_bps,
                                       'funding_bps_8h': funding_bps_8h, **kw}, float)
        # External may only LOWER the score (block), never raise it past evidence.
        result = local if ext is None or not math.isfinite(ext) else min(local, max(0.0, min(10.0, ext)))
        self.ledger['score'].add(time.perf_counter_ns() - t0)
        return result

    def permit(self, *args, **kw):
        return self.score(*args, **kw) >= self.permit_score

    def latency(self):
        return {k: v.summary() for k, v in self.ledger.items()}
