"""The net-EV head inside GroktopusSwarm.evaluate, and its LCB_95 kill gate."""
from dataclasses import replace

import pytest

from core.groktopus_swarm import GroktopusSwarm
from core.real_alpha_engine import Features

# atr/price = 22 bps of gross excursion: comfortably past the edge/cost hurdle,
# but thin enough that a 40 bps dispersion leaves the mean unproven at n=60.
THIN = Features(obi=.9, ofi=1, flow=.9, vpin=.5, vwap_z=-8, momentum=4, rsi=50,
                atr=.22, spread_bps=2, ready=True)
RICH = Features(obi=.9, ofi=1, flow=.9, vpin=.5, vwap_z=-8, momentum=4, rsi=50,
                atr=5, spread_bps=1, ready=True)


def test_diagnostics_keep_their_existing_contract():
    """Downstream (runtime rows, research bridge) still reads the old keys."""
    swarm = GroktopusSwarm()
    swarm.evaluate(RICH, 100, True, True, True, True)
    for key in ('edge_bps', 'cost_bps', 'edge_ratio', 'atr_bps', 'edge_model'):
        assert key in swarm.diagnostics


def test_head_is_uncalibrated_without_a_settled_sample():
    """No sample, no interval: the head says so and leaves the old hurdle in charge."""
    swarm = GroktopusSwarm()
    decision, _ = swarm.evaluate(RICH, 100, True, True, True, True)
    assert swarm.diagnostics['edge_model'] == 'NET_EV_UNCALIBRATED'
    assert swarm.diagnostics['lcb_95_bps'] is None
    assert swarm.diagnostics['ev_hold'] is False
    assert decision.allowed


def test_cost_basis_is_taker_taker_ten_bps():
    swarm = GroktopusSwarm()
    swarm.evaluate(RICH, 100, True, True, True, True)
    assert swarm.diagnostics['ev_cost_bps'] == pytest.approx(10.0)
    swarm.evaluate(RICH, 100, True, True, True, True, taker_fee=.0002)
    assert swarm.diagnostics['ev_cost_bps'] == pytest.approx(4.0)


def test_unproven_edge_is_held_even_though_it_clears_the_cost_hurdle():
    """Kill-criteria in situ: edge/cost passes, LCB_95 <= 0 still forces HOLD."""
    swarm = GroktopusSwarm()
    decision, votes = swarm.evaluate(THIN, 100, True, True, True, True,
                                     ev_samples=60, ev_gross_sigma_bps=40.0)
    diag = swarm.diagnostics
    assert diag['edge_model'] == 'NET_EV_LCB95'
    assert diag['edge_ratio'] >= 1.8          # the old proxy would have allowed this
    assert diag['net_ev_bps'] > 0             # and the point estimate is positive
    assert diag['lcb_95_bps'] <= 0            # but the interval straddles zero
    assert diag['ev_hold'] is True
    assert not decision.allowed
    blockers = {v.agent for v in votes if v.veto}
    assert {'ZEPHR', 'RUNE'} <= blockers


def test_proven_edge_survives_the_gate():
    swarm = GroktopusSwarm()
    decision, _ = swarm.evaluate(RICH, 100, True, True, True, True,
                                 ev_samples=60, ev_gross_sigma_bps=20.0)
    assert swarm.diagnostics['edge_model'] == 'NET_EV_LCB95'
    assert swarm.diagnostics['lcb_95_bps'] > 0
    assert swarm.diagnostics['ev_hold'] is False
    assert decision.allowed


def test_more_evidence_releases_the_same_setup():
    """The gate blocks unproven edge, not edge: identical EV, narrower band."""
    swarm = GroktopusSwarm()
    swarm.evaluate(THIN, 100, True, True, True, True, ev_samples=60, ev_gross_sigma_bps=40.0)
    held = dict(swarm.diagnostics)
    decision, _ = swarm.evaluate(THIN, 100, True, True, True, True,
                                 ev_samples=40000, ev_gross_sigma_bps=40.0)
    assert swarm.diagnostics['net_ev_bps'] == pytest.approx(held['net_ev_bps'])
    assert swarm.diagnostics['lcb_95_bps'] > held['lcb_95_bps']
    assert swarm.diagnostics['ev_hold'] is False
    assert decision.allowed


def test_unfilled_cost_reduces_expected_value():
    swarm = GroktopusSwarm()
    swarm.evaluate(THIN, 100, True, True, True, True)
    free = swarm.diagnostics['net_ev_bps']
    swarm.evaluate(THIN, 100, True, True, True, True, cost_unfilled_bps=2.0)
    assert swarm.diagnostics['net_ev_bps'] < free


def test_extreme_volatility_is_flagged_as_extrapolated_not_predicted():
    swarm = GroktopusSwarm()
    swarm.evaluate(RICH, 100, True, True, True, True)   # atr/price = 500 bps
    assert swarm.diagnostics['ev_extrapolated'] is True
    assert 0.0 < swarm.diagnostics['p_fill'] < 1.0


def test_invalid_features_never_reach_the_ev_head():
    """Non-finite input must not raise out of the hot path, and must not trade."""
    swarm = GroktopusSwarm()
    decision, _ = swarm.evaluate(replace(RICH, spread_bps=float('nan')), 100,
                                 True, True, True, True, ev_samples=60,
                                 ev_gross_sigma_bps=20.0)
    assert swarm.diagnostics['edge_model'] == 'NET_EV_UNCALIBRATED'
    assert not decision.allowed
