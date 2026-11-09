"""Causal GARCH(1,1) tail monitor; its only action is to block via RUNE.

`escalate` folds a core.regime_classifier state into a MarketRisk (2026-11-07): CRASH -> TOXIC and
not allowed, JUMPY -> at least ELEVATED. It never clears a block or lowers a label.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, replace
import math


@dataclass(frozen=True)
class MarketRisk:
    regime: str
    allowed: bool
    reason: str
    conditional_vol_bps: float | None
    vol_ratio: float | None
    standardized_return: float | None
    book_skew: float | None
    observations: int

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def escalate(risk: MarketRisk, regime=None) -> MarketRisk:
    """Add a regime-classifier verdict to `risk`; it can only add risk. `regime` is a state name or an
    object with `.state` (core.regime_classifier.RegimeState). None or an unrecognised name changes nothing."""
    from core.regime_classifier import CRASH, JUMPY       # lazy: regime_classifier imports this module
    name = getattr(regime, 'state', regime)
    if name == CRASH and (risk.allowed or risk.regime != 'TOXIC'):
        return replace(risk, regime='TOXIC', allowed=False, reason='REGIME_CRASH')
    if name == JUMPY and risk.regime == 'CALM':
        return replace(risk, regime='ELEVATED', reason='REGIME_JUMPY')
    return risk


class GarchTailRisk:
    """Online GARCH filter on one-second log returns, with bounded warmup state."""

    MIN_VARIANCE_BPS2 = 4.0             # 2 bps/s floor prevents flat warmup from implying zero risk

    def __init__(self, *, alpha: float = 0.08, beta: float = 0.90, warmup: int = 30,
                 tail_z: float = 4.5, elevated_vol_ratio: float = 1.8,
                 toxic_vol_ratio: float = 3.0):
        params = (alpha, beta, tail_z, elevated_vol_ratio, toxic_vol_ratio)
        if (not all(math.isfinite(x) for x in params) or not 0 < alpha or not 0 < beta or
                alpha + beta >= 1 or warmup < 10 or tail_z <= 0 or elevated_vol_ratio <= 1 or
                toxic_vol_ratio <= elevated_vol_ratio):
            raise ValueError("invalid GARCH policy")
        self.alpha, self.beta, self.omega = alpha, beta, 0.0
        self.warmup, self.tail_z = warmup, tail_z
        self.elevated_vol_ratio, self.toxic_vol_ratio = elevated_vol_ratio, toxic_vol_ratio
        self.returns: deque[float] = deque(maxlen=600)
        self.variance: float | None = None
        self.long_run_variance: float | None = None
        self.last_z: float | None = None
        self.last_tail = False

    def update(self, return_bps: float) -> float | None:
        value = float(return_bps)
        if not math.isfinite(value):
            raise ValueError("return must be finite")
        self.returns.append(value)
        if len(self.returns) < self.warmup:
            return None
        if self.variance is None:
            sample_var = max(self.MIN_VARIANCE_BPS2,
                             sum(x * x for x in self.returns) / len(self.returns))
            self.long_run_variance = sample_var
            self.variance = sample_var
            self.omega = sample_var * (1.0 - self.alpha - self.beta)
        variance = max(float(self.variance), 1e-6)
        self.last_z = value / math.sqrt(variance)
        self.last_tail = abs(self.last_z) >= self.tail_z
        self.variance = max(1e-6, self.omega + self.alpha * value * value + self.beta * variance)
        return self.last_z

    def assess(self, *, book_skew: float | None, vpin: float | None,
               skew_toxic: float = 0.95, vpin_elevated: float = 0.65,
               vpin_toxic: float = 0.80, regime=None) -> MarketRisk:
        """`regime` (a core.regime_classifier state) can only escalate the result; see `escalate`."""
        return escalate(self._assess(book_skew=book_skew, vpin=vpin, skew_toxic=skew_toxic,
                                     vpin_elevated=vpin_elevated, vpin_toxic=vpin_toxic), regime)

    def _assess(self, *, book_skew: float | None, vpin: float | None,
                skew_toxic: float, vpin_elevated: float, vpin_toxic: float) -> MarketRisk:
        for value in (book_skew, vpin):
            if value is not None and not math.isfinite(float(value)):
                return MarketRisk("UNKNOWN", False, "INVALID_RISK_FEATURE", None, None,
                                  self.last_z, book_skew, len(self.returns))
        if self.variance is None or self.long_run_variance is None:
            return MarketRisk("UNKNOWN", True, "GARCH_WARMUP", None, None,
                              self.last_z, book_skew, len(self.returns))
        vol = math.sqrt(self.variance)
        ratio = vol / math.sqrt(max(self.long_run_variance, 1e-12))
        skew_toxic_now = (book_skew is not None and abs(float(book_skew)) >= skew_toxic and
                          vpin is not None and float(vpin) >= vpin_elevated)
        toxic = self.last_tail or ratio >= self.toxic_vol_ratio or (
            skew_toxic_now and vpin is not None and float(vpin) >= vpin_toxic)
        if toxic:
            why = ("GARCH_TAIL" if self.last_tail else "GARCH_VOL_SPIKE" if ratio >= self.toxic_vol_ratio
                   else "BOOK_SKEW_VPIN_TOXIC")
            return MarketRisk("TOXIC", False, why, vol, ratio, self.last_z,
                              book_skew, len(self.returns))
        elevated = (ratio >= self.elevated_vol_ratio or
                    (book_skew is not None and abs(float(book_skew)) >= 0.80) or
                    (vpin is not None and float(vpin) >= vpin_elevated))
        regime = "ELEVATED" if elevated else "CALM"
        return MarketRisk(regime, True, "RISK_ELEVATED" if elevated else "RISK_CALM",
                          vol, ratio, self.last_z, book_skew, len(self.returns))
