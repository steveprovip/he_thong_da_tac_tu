"""Calibrated net-EV decision head with a 95% lower-confidence-bound kill gate.

Replaces the uncalibrated ATR edge proxy. The contract is:

    EV  = p_fill * E[g - c | fill] - (1 - p_fill) * c_u
    LCB_95(EV) <= 0  =>  HOLD

Cost basis is taker/taker: both legs cross the book, so the round trip pays
2 x taker_fee = 10.0 bps at the venue's default 5 bps taker tier. A GTX maker
entry pays less (see phan_tich.ev_fill_adjusted.MEASURED_ROUNDTRIP_BPS = 6.0,
reconciled against /fapi/v1/userTrades); pass cost_fill_bps explicitly for it.

Uncertainty is propagated by the delta method over the two estimated inputs,
p_fill (Bernoulli) and the mean gross edge g, over n settled observations:

    dEV/dp  = (g - c) + c_u
    dEV/dg  = p
    Var(EV) = (dEV/dp)^2 * p(1-p)/n + (dEV/dg)^2 * sigma_g^2/n

A small sample or a wide gross dispersion widens the band, so the gate refuses
to trade on an unproven mean. This is the discipline PositionSizer already
applies to sizing: the interval, never the point estimate, grants permission.

Honesty rule: a confidence bound does not exist without a sample. With fewer
than `min_samples` settled observations the head reports UNCALIBRATED, returns
lcb_bps=None and does NOT gate -- callers keep whatever hurdle they already
had. It never silently substitutes a made-up interval for a missing one.
"""
import math
from dataclasses import dataclass

__all__ = [
    'NetEVResult', 'evaluate_net_ev', 'net_ev_bps', 'ev_standard_error_bps',
    'lower_confidence_bound_bps', 'fill_probability', 'roundtrip_cost_bps',
    'TAKER_FEE_BPS', 'TAKER_TAKER_ROUNDTRIP_BPS', 'Z_95_ONE_SIDED',
    'MIN_CALIBRATION_SAMPLES', 'DEFAULT_FILL_COEFFICIENTS', 'FILL_MODEL_DOMAIN',
]

TAKER_FEE_BPS = 5.0
TAKER_TAKER_ROUNDTRIP_BPS = 2 * TAKER_FEE_BPS   # 10.0 bps, both legs cross
Z_95_ONE_SIDED = 1.645                          # matches phan_tich.sample_size_for_ev
MIN_CALIBRATION_SAMPLES = 30                    # CLT floor for the delta method

# Logistic p_fill, shaped like phan_tich/_b2_pfill_model.py (spread and ATR both
# reduce fill odds). The intercept is anchored so the reference regime
# (spread 2.0 bps, ATR 10.0 bps) reproduces the measured 7.3% live GTX fill rate.
# Refit offline with _b2_pfill_model.py; these are a prior, not a substitute for
# observed fills.
DEFAULT_FILL_COEFFICIENTS = {'intercept': -2.2416, 'spread_bps': -0.05, 'atr_bps': -0.02}

# The B2 fit only saw ATR inside the F4 band and ordinary spreads. A logit is
# linear without bound, so evaluating far outside that domain is extrapolation,
# not prediction: inputs are clamped to the fitted range and the result is
# flagged so a caller can tell an interpolated estimate from a clamped one.
FILL_MODEL_DOMAIN = {'spread_bps': (0.0, 50.0), 'atr_bps': (0.0, 20.0)}


def _finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) for v in values)


def roundtrip_cost_bps(taker_fee=0.0005):
    """Taker/taker round-trip cost in bps; both legs cross the book."""
    if not _finite(taker_fee) or taker_fee < 0:
        raise ValueError('taker_fee must be a finite non-negative float')
    return 2 * taker_fee * 10000.0


def fill_probability(spread_bps, atr_bps, coefficients=None):
    """Logistic P(GTX entry fills), clamped to the B2 calibration domain.

    Returns (p_fill, extrapolated) where extrapolated is True when either input
    was outside the fitted range and had to be clamped.
    """
    if not _finite(spread_bps, atr_bps):
        raise ValueError('spread_bps and atr_bps must be finite')
    coef = DEFAULT_FILL_COEFFICIENTS if coefficients is None else coefficients
    for key in ('intercept', 'spread_bps', 'atr_bps'):
        if key not in coef or not _finite(coef[key]):
            raise ValueError(f'missing or non-finite coefficient {key!r}')

    lo_s, hi_s = FILL_MODEL_DOMAIN['spread_bps']
    lo_a, hi_a = FILL_MODEL_DOMAIN['atr_bps']
    clamped_spread = min(max(spread_bps, lo_s), hi_s)
    clamped_atr = min(max(atr_bps, lo_a), hi_a)
    extrapolated = (clamped_spread != spread_bps) or (clamped_atr != atr_bps)

    z = (coef['intercept'] + coef['spread_bps'] * clamped_spread
         + coef['atr_bps'] * clamped_atr)
    p = 1.0 / (1.0 + math.exp(-z)) if z >= 0 else math.exp(z) / (1.0 + math.exp(z))
    return min(max(p, 0.0), 1.0), extrapolated


def net_ev_bps(p_fill, gross_bps, cost_fill_bps=TAKER_TAKER_ROUNDTRIP_BPS,
               cost_unfilled_bps=0.0):
    """EV = p_fill * E[g - c | fill] - (1 - p_fill) * c_u, in bps.

    Identical in contract to phan_tich.ev_fill_adjusted.ev_per_intent; that
    module stays offline-only, so the hot path keeps its own audited copy and
    tests/test_net_ev.py pins the two to agree.
    """
    if not _finite(p_fill, gross_bps, cost_fill_bps, cost_unfilled_bps):
        raise ValueError('non-finite EV input')
    if not 0.0 <= p_fill <= 1.0 or cost_fill_bps < 0 or cost_unfilled_bps < 0:
        raise ValueError('invalid EV inputs')
    return p_fill * (gross_bps - cost_fill_bps) - (1.0 - p_fill) * cost_unfilled_bps


def ev_standard_error_bps(p_fill, gross_bps, cost_fill_bps, cost_unfilled_bps,
                          gross_sigma_bps, samples):
    """Delta-method standard error of the EV estimate over `samples` observations."""
    if not _finite(p_fill, gross_bps, cost_fill_bps, cost_unfilled_bps, gross_sigma_bps):
        raise ValueError('non-finite standard-error input')
    if not isinstance(samples, int) or isinstance(samples, bool) or samples < 1:
        raise ValueError('samples must be a positive int')
    if gross_sigma_bps < 0 or not 0.0 <= p_fill <= 1.0:
        raise ValueError('invalid standard-error inputs')
    d_dp = (gross_bps - cost_fill_bps) + cost_unfilled_bps
    d_dg = p_fill
    variance = (d_dp ** 2 * p_fill * (1.0 - p_fill) / samples
                + d_dg ** 2 * gross_sigma_bps ** 2 / samples)
    return math.sqrt(max(variance, 0.0))


def lower_confidence_bound_bps(ev_bps, se_bps, z=Z_95_ONE_SIDED):
    """One-sided lower confidence bound: EV - z * SE."""
    if not _finite(ev_bps, se_bps, z) or se_bps < 0 or z < 0:
        raise ValueError('invalid confidence-bound inputs')
    return ev_bps - z * se_bps


@dataclass(frozen=True)
class NetEVResult:
    ev_bps: float
    lcb_bps: float | None
    se_bps: float | None
    p_fill: float
    gross_bps: float
    cost_fill_bps: float
    cost_unfilled_bps: float
    samples: int | None
    calibrated: bool
    extrapolated: bool
    model: str

    @property
    def hold(self):
        """Kill-criteria: a calibrated LCB_95(EV) at or below zero forbids the trade."""
        return bool(self.calibrated and self.lcb_bps is not None and self.lcb_bps <= 0.0)


def evaluate_net_ev(p_fill, gross_bps, cost_fill_bps=TAKER_TAKER_ROUNDTRIP_BPS,
                    cost_unfilled_bps=0.0, gross_sigma_bps=None, samples=None,
                    min_samples=MIN_CALIBRATION_SAMPLES, z=Z_95_ONE_SIDED,
                    extrapolated=False):
    """Net-EV point estimate plus its one-sided 95% lower bound when computable."""
    ev = net_ev_bps(p_fill, gross_bps, cost_fill_bps, cost_unfilled_bps)
    if not isinstance(min_samples, int) or isinstance(min_samples, bool) or min_samples < 1:
        raise ValueError('min_samples must be a positive int')

    calibrated = (isinstance(samples, int) and not isinstance(samples, bool)
                  and samples >= min_samples and gross_sigma_bps is not None)
    if not calibrated:
        return NetEVResult(ev, None, None, p_fill, gross_bps, cost_fill_bps,
                           cost_unfilled_bps, samples if isinstance(samples, int) else None,
                           False, bool(extrapolated), 'NET_EV_UNCALIBRATED')

    se = ev_standard_error_bps(p_fill, gross_bps, cost_fill_bps, cost_unfilled_bps,
                               gross_sigma_bps, samples)
    lcb = lower_confidence_bound_bps(ev, se, z)
    return NetEVResult(ev, lcb, se, p_fill, gross_bps, cost_fill_bps, cost_unfilled_bps,
                       samples, True, bool(extrapolated), 'NET_EV_LCB95')
