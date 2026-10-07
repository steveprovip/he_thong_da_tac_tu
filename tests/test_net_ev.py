"""Contract for the calibrated net-EV head and its LCB_95 kill gate."""
import math

import pytest

from core.net_ev import (
    DEFAULT_FILL_COEFFICIENTS,
    MIN_CALIBRATION_SAMPLES,
    TAKER_TAKER_ROUNDTRIP_BPS,
    Z_95_ONE_SIDED,
    ev_standard_error_bps,
    evaluate_net_ev,
    fill_probability,
    lower_confidence_bound_bps,
    net_ev_bps,
    roundtrip_cost_bps,
)
from phan_tich.ev_fill_adjusted import ev_per_intent


def test_taker_taker_roundtrip_is_ten_bps():
    """Spec cost basis: both legs cross, 2 x 5bps taker = 10bps."""
    assert TAKER_TAKER_ROUNDTRIP_BPS == pytest.approx(10.0)
    assert roundtrip_cost_bps(0.0005) == pytest.approx(10.0)
    assert roundtrip_cost_bps(0.0002) == pytest.approx(4.0)
    for bad in (-0.001, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            roundtrip_cost_bps(bad)


def test_net_ev_matches_the_specified_formula():
    """EV = p_fill * E[g - c | fill] - (1 - p_fill) * c_u."""
    p, g, c, cu = 0.3, 25.0, 10.0, 1.5
    assert net_ev_bps(p, g, c, cu) == pytest.approx(p * (g - c) - (1 - p) * cu)
    # No unfilled cost collapses to the filled branch only.
    assert net_ev_bps(p, g, c) == pytest.approx(p * (g - c))


def test_net_ev_agrees_with_the_offline_audit_module():
    """core.net_ev and phan_tich.ev_fill_adjusted must never drift apart."""
    for p, g, c, cu in ((0.073, 4.34, 6.0, 0.0), (0.5, 30.0, 10.0, 2.0), (0.0, 5.0, 6.0, 1.0)):
        assert net_ev_bps(p, g, c, cu) == pytest.approx(ev_per_intent(p, g, c, cu))


@pytest.mark.parametrize('p_fill,gross,cost,unfilled', [
    (1.5, 4.0, 6.0, 0.0), (-0.1, 4.0, 6.0, 0.0), (0.5, float('nan'), 6.0, 0.0),
    (0.5, 4.0, float('inf'), 0.0), (0.5, 4.0, -1.0, 0.0), (0.5, 4.0, 6.0, -1.0),
])
def test_net_ev_rejects_invalid_inputs(p_fill, gross, cost, unfilled):
    with pytest.raises(ValueError):
        net_ev_bps(p_fill, gross, cost, unfilled)


def test_fill_probability_reproduces_the_measured_live_rate():
    """Reference regime (spread 2bps, ATR 10bps) = the measured 7.3% GTX fill rate."""
    p, extrapolated = fill_probability(2.0, 10.0)
    assert p == pytest.approx(0.073, abs=5e-4)
    assert extrapolated is False


def test_fill_probability_falls_with_spread_and_volatility():
    base, _ = fill_probability(2.0, 10.0)
    wider, _ = fill_probability(8.0, 10.0)
    rougher, _ = fill_probability(2.0, 18.0)
    assert wider < base
    assert rougher < base
    assert DEFAULT_FILL_COEFFICIENTS['spread_bps'] < 0
    assert DEFAULT_FILL_COEFFICIENTS['atr_bps'] < 0


def test_fill_probability_clamps_outside_the_fitted_domain():
    """A logit extrapolated 25x past its fit is not a prediction; clamp and flag it."""
    clamped, extrapolated = fill_probability(1.0, 500.0)
    boundary, _ = fill_probability(1.0, 20.0)
    assert extrapolated is True
    assert clamped == pytest.approx(boundary)
    assert 0.0 < clamped < 1.0
    for bad in (float('nan'), float('inf')):
        with pytest.raises(ValueError):
            fill_probability(bad, 10.0)


def test_standard_error_shrinks_with_the_square_root_of_n():
    args = (0.2, 40.0, 10.0, 0.0, 15.0)
    se_100 = ev_standard_error_bps(*args, 100)
    se_400 = ev_standard_error_bps(*args, 400)
    assert se_400 == pytest.approx(se_100 / 2.0, rel=1e-9)
    for bad in (0, -5, 2.5, True):
        with pytest.raises(ValueError):
            ev_standard_error_bps(*args, bad)


def test_lower_bound_sits_below_the_point_estimate():
    assert lower_confidence_bound_bps(10.0, 2.0) == pytest.approx(10.0 - Z_95_ONE_SIDED * 2.0)
    assert lower_confidence_bound_bps(10.0, 0.0) == pytest.approx(10.0)
    with pytest.raises(ValueError):
        lower_confidence_bound_bps(10.0, -1.0)


def test_uncalibrated_head_reports_no_interval_and_does_not_gate():
    """No sample, no confidence bound -- and no pretending otherwise."""
    for samples in (None, 5, MIN_CALIBRATION_SAMPLES - 1):
        result = evaluate_net_ev(0.2, 40.0, samples=samples, gross_sigma_bps=15.0)
        assert result.calibrated is False
        assert result.lcb_bps is None
        assert result.se_bps is None
        assert result.hold is False
        assert result.model == 'NET_EV_UNCALIBRATED'
    # A sample without a dispersion estimate is equally uncalibrated.
    assert evaluate_net_ev(0.2, 40.0, samples=500).calibrated is False


def test_calibrated_head_holds_when_the_lower_bound_reaches_zero():
    """Kill-criteria: LCB_95(EV) <= 0 => HOLD, even when the mean looks positive."""
    thin = evaluate_net_ev(0.05, 12.0, gross_sigma_bps=20.0, samples=60)
    assert thin.calibrated is True
    assert thin.ev_bps > 0                  # positive point estimate
    assert thin.lcb_bps <= 0                # indistinguishable from zero
    assert thin.hold is True
    assert thin.model == 'NET_EV_LCB95'

    strong = evaluate_net_ev(0.0635, 450.0, gross_sigma_bps=20.0, samples=60)
    assert strong.lcb_bps > 0
    assert strong.hold is False


def test_negative_edge_is_always_held():
    losing = evaluate_net_ev(0.073, 4.34, cost_fill_bps=6.0, gross_sigma_bps=10.0, samples=200)
    assert losing.ev_bps < 0
    assert losing.lcb_bps < losing.ev_bps
    assert losing.hold is True


def test_more_evidence_can_convert_a_hold_into_a_trade():
    """The gate blocks unproven edge, not edge itself: n up, band down."""
    weak = evaluate_net_ev(0.25, 30.0, gross_sigma_bps=18.0, samples=MIN_CALIBRATION_SAMPLES)
    proven = evaluate_net_ev(0.25, 30.0, gross_sigma_bps=18.0, samples=20000)
    assert weak.ev_bps == pytest.approx(proven.ev_bps)
    assert proven.lcb_bps > weak.lcb_bps
    assert proven.hold is False


def test_result_fields_are_finite_and_consistent():
    r = evaluate_net_ev(0.2, 40.0, gross_sigma_bps=15.0, samples=120)
    assert math.isfinite(r.ev_bps) and math.isfinite(r.lcb_bps) and math.isfinite(r.se_bps)
    assert r.lcb_bps == pytest.approx(r.ev_bps - Z_95_ONE_SIDED * r.se_bps)
    assert r.cost_fill_bps == pytest.approx(TAKER_TAKER_ROUNDTRIP_BPS)
    assert r.samples == 120
    with pytest.raises(Exception):
        r.ev_bps = 1.0
