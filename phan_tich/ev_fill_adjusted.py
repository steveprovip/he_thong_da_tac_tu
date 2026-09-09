"""Offline fill-aware EV audit — read-only, never touches execution.

Implements Astra Q1/Q2 math without changing any hot-loop gate:
  EV/intent = p_fill * E[gross - full_cost | fill] - cost_unfilled
  fee_expected(q) = 6 - 2q  (maker entry 2bps + exit mix q*2 + (1-q)*4)

Cost basis: MEASURED_ROUNDTRIP_BPS = 6.0 in core/signal_lab.py
(2bps maker entry + 4bps taker exit, reconciled vs /fapi/v1/userTrades).
"""
import math


MEASURED_ROUNDTRIP_BPS = 6.0
MAKER_BPS = 2.0
TAKER_EXIT_BPS = 4.0


def fee_expected_bps(q):
    """Expected round-trip fee given maker-exit share q in [0,1]."""
    if not isinstance(q, (int, float)) or not math.isfinite(q) or not 0 <= q <= 1:
        raise ValueError('q must be within [0, 1]')
    return (MAKER_BPS + q * MAKER_BPS + (1 - q) * TAKER_EXIT_BPS)


def ev_per_intent(p_fill, mean_gross_fill_bps, cost_fill_bps=MEASURED_ROUNDTRIP_BPS,
                  cost_unfilled_bps=0.0):
    """EV per intent in bps. Partial fills must be pre-weighted into notional."""
    for v in (p_fill, mean_gross_fill_bps, cost_fill_bps, cost_unfilled_bps):
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError('non-finite EV input')
    if not 0 <= p_fill <= 1 or cost_fill_bps < 0 or cost_unfilled_bps < 0:
        raise ValueError('invalid EV inputs')
    return p_fill * (mean_gross_fill_bps - cost_fill_bps) - (1 - p_fill) * cost_unfilled_bps


def sample_size_proportion(margin=0.05, z=1.96, p=0.5):
    """Worst-case n for a proportion within +/-margin (default ~385)."""
    if not math.isfinite(margin) or margin <= 0:
        raise ValueError('invalid margin')
    return math.ceil((z ** 2 * p * (1 - p)) / (margin ** 2))


def sample_size_for_ev(sigma_bps, delta_bps, one_sided=True):
    """n ≈ C*(sigma/delta)^2; C=6.18 one-sided (alpha .05, power .8)."""
    if not all(math.isfinite(v) for v in (sigma_bps, delta_bps)) or sigma_bps <= 0 or delta_bps <= 0:
        raise ValueError('invalid sigma/delta')
    const = (1.645 + 0.842) ** 2 if one_sided else (1.96 + 0.842) ** 2
    return math.ceil(const * (sigma_bps / delta_bps) ** 2)
