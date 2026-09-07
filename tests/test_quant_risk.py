"""Adaptive authorization must never weaken hard safety gates or capital caps."""
from dataclasses import replace
import math
import pytest

from core.barrier_planner import BarrierPlanner, maker_quote, reservation_price
from core.consensus_engine import ConsensusEngine, TokenAuthority, Vote, WEIGHTS
from core.models import Book, Intent, Filters, dec
from core.position_sizer import PositionSizer
from core.runtime import arguments


def votes(confidence=.74):
    return [Vote(n, 'BUY', confidence, 'test') for n in WEIGHTS]


def test_adaptive_quorum_and_authority_share_policy():
    policy = ConsensusEngine()
    auth = TokenAuthority(policy=policy)
    intent = Intent('BTCUSDT', 'BUY', '1', '100', '99', '102', 30, 'testnet')
    trend = policy.evaluate(votes(), regime='TREND')
    assert trend.allowed and .70 <= trend.threshold < .768
    assert not policy.evaluate(votes(), regime='CHOP').allowed
    auth.consume(auth.issue(intent, trend), intent)
    with pytest.raises(PermissionError):
        auth.issue(intent, replace(trend, threshold=.5, score=.6))
    assert policy.evaluate(votes(1), regime='TOXIC').veto
    assert policy.evaluate([replace(v, veto=v.agent == 'RUNE') for v in votes(1)], regime='TREND').veto
    demo = ConsensusEngine(threshold=.5, veto_threshold=.4)
    assert demo.threshold_for('CHOP') >= .70
    assert demo.threshold_for('TREND') >= .5


def test_bayesian_weights_keep_gate_budget_and_hard_veto():
    engine = ConsensusEngine()
    v = [Vote(n, 'BUY' if n == 'LUMEN' else 'SELL', 1, 'alpha') for n in WEIGHTS]
    for _ in range(40):
        engine.record_outcome(v, 'BUY', 2)
    assert engine.weights['LUMEN'] > engine.weights['NORO']
    assert sum(engine.weights.values()) == pytest.approx(1)
    for n in set(WEIGHTS) - {'NORO', 'LUMEN', 'TIDAL'}:
        assert engine.weights[n] == WEIGHTS[n]
    assert all(w > 0 for w in engine.weights.values())
    assert engine.evaluate([replace(x, veto=x.agent == 'RUNE') for x in votes(1)]).veto


@pytest.mark.parametrize('side,inventory', [('BUY', 2), ('SELL', -2)])
def test_reservation_quotes_are_tick_aligned_passive_and_inventory_skewed(side, inventory):
    book = Book('X', ((99.99, 1),), ((100.01, 1),), 0)
    flat = maker_quote(book, side, .01, 0, .1, .5, 10)
    tilted = maker_quote(book, side, .01, inventory, .1, .5, 10)
    assert tilted < flat if side == 'BUY' else tilted > flat
    assert tilted < dec(book.ask) if side == 'BUY' else tilted > dec(book.bid)
    assert tilted % dec('.01') == 0
    plan = BarrierPlanner().plan(side, float(tilted), .001, .01, 0)
    assert abs(plan.take_profit-float(tilted)) > float(tilted)*.0007


@pytest.mark.parametrize('bad', [math.nan, math.inf, -1])
def test_reservation_rejects_invalid_variance(bad):
    with pytest.raises(ValueError):
        reservation_price(100, 1, .1, bad, 10)


def test_fat_tail_kelly_only_reduces_legacy_ceiling_and_is_scale_invariant():
    values = [2]*35 + [1]*12 + [-1]*10 + [-12]*2
    # 59 samples: min_samples is pinned so this stays a test of the fat-tail
    # adjustment and scale invariance, not of the sample-size policy.
    s, scaled = PositionSizer(min_samples=20), PositionSizer(min_samples=20)
    for x in values:
        s.record(x)
        scaled.record(x*100)
    wins, losses = [x for x in values if x > 0], [-x for x in values if x < 0]
    p = len(wins)/len(values)
    b = (sum(wins)/len(wins))/(sum(losses)/len(losses))
    ceiling = min(.15, .25*(p-(1-p)/b))
    assert 0 < s.kelly() < ceiling
    assert s.kelly() == pytest.approx(scaled.kelly())
    assert s.tail_statistics()['skewness'] < 0
    assert s.tail_statistics()['excess_kurtosis'] > 0


def test_normalized_outcomes_and_kelly_cap_cannot_be_rounded_up():
    s = PositionSizer()
    for x in [2]*12+[-1]*8:
        s.record(x, notional=100)
    filters = Filters(*map(dec, ['.01', '.01', '.01', '10000', '100']))
    assert s.size(1000, 0, 100, 99, filters).quantity == '0'
    with pytest.raises(ValueError):
        s.record(1, notional=0)
    assert s.size(1000, 0, 100, 99, filters, available=math.nan).quantity == '0'


@pytest.mark.parametrize('argv', [['--threshold', 'nan'], ['--veto-threshold', '.9', '--threshold', '.5'], ['--edge-multiple', '-1']])
def test_invalid_policy_cli_rejected(argv):
    with pytest.raises(SystemExit):
        arguments('testnet', argv)


def test_unknown_flags_are_rejected():
    """Only two run modes exist (live, simulated); the old relaxed policy flag is gone."""
    with pytest.raises(SystemExit):
        arguments('testnet', ['--demo-flow'])


def test_two_tier_policy_live_strict_simulated_active():
    """Owner-approved two-tier policy: live keeps survival gates untouched; the
    simulated demo venue trades actively so the full pipeline can be observed."""
    from core.web_ui_server import Telemetry
    from config.settings import Settings
    from core.runtime import Runtime
    live = Runtime(Settings.load('mainnet'), arguments('mainnet', []), Telemetry())
    assert not live.simulated_flow
    assert live.edge_multiple == 1.8 and live.toxic_vpin == .65 and live.trend_floor == .768
    sim = Runtime(Settings.load('testnet'), arguments('testnet', ['--bootstrap']), Telemetry())
    assert sim.simulated_flow
    assert sim.edge_multiple == .6 and sim.trend_floor == .55
    assert sim.swarm.consensus.threshold_for('CHOP') == pytest.approx(.55)
    assert sim.swarm.consensus.threshold_for('TREND') == pytest.approx(.55)
    override = Runtime(Settings.load('testnet'),
                       arguments('testnet', ['--bootstrap', '--edge-multiple', '1.2']), Telemetry())
    assert override.edge_multiple == 1.2


def test_saturation_does_not_bypass_vpin_risk_gate():
    """Live: absolute gate. Simulated demo: saturation tolerated via baseline-relative
    gate, but genuine toxicity spikes (jump >= .15) still veto."""
    from core.real_alpha_engine import Features
    from core.groktopus_swarm import adverse_selection
    from core.web_ui_server import Telemetry
    from config.settings import Settings
    from core.runtime import Runtime
    sim = Runtime(Settings.load('testnet'), arguments('testnet', ['--bootstrap']), Telemetry())
    saturated = Features(vpin=.999, vpin_baseline=.985)
    assert sim.vpin_gate(saturated) == pytest.approx(1.0)
    assert not adverse_selection(saturated, 'BUY', sim.vpin_gate(saturated))
    spike = Features(vpin=.60, vpin_jump=.20, vpin_baseline=.45)
    assert sim.vpin_gate(spike) == pytest.approx(.65)
    assert adverse_selection(spike, 'BUY', sim.vpin_gate(spike))
    live = Runtime(Settings.load('mainnet'), arguments('mainnet', []), Telemetry())
    assert live.vpin_gate(saturated) == .65
    assert adverse_selection(saturated, 'BUY', live.vpin_gate(saturated))
    cli = Runtime(Settings.load('testnet'),
                  arguments('testnet', ['--bootstrap', '--toxic-vpin', '.8']), Telemetry())
    assert cli.vpin_gate(saturated) == .8


def test_bootstrap_fraction_scales_demo_size_only():
    """Observation knob for the demo venue: larger bootstrap sizing on request.
    Default sizing is unchanged, out-of-range values are rejected everywhere."""
    filters = Filters(dec('.01'), dec('.01'), dec('.01'), dec('10000'), dec('100'))
    base = PositionSizer().size(10000, 0, 100, 99.5, filters, bootstrap=True).notional
    assert base == pytest.approx(110, rel=.01)  # 1% of equity, clamped up to min notional
    big = PositionSizer().size(10000, 0, 100, 99.5, filters, bootstrap=True, bootstrap_fraction=.06).notional
    assert big == pytest.approx(600, rel=.01)
    assert PositionSizer().size(10000, 0, 100, 99.5, filters, bootstrap=True, bootstrap_fraction=.5).quantity == '0'
    with pytest.raises(SystemExit):
        arguments('testnet', ['--bootstrap', '--bootstrap-fraction', '0.5'])
    from config.settings import Settings
    from core.runtime import Runtime
    from core.web_ui_server import Telemetry
    sim = Runtime(Settings.load('testnet'),
                  arguments('testnet', ['--bootstrap', '--bootstrap-fraction', '.06']), Telemetry())
    assert sim.bootstrap_fraction == pytest.approx(.06)
    live = Runtime(Settings.load('mainnet'), arguments('mainnet', []), Telemetry())
    assert live.bootstrap_fraction == pytest.approx(.01)


def test_trend_floor_parameter_preserves_default_adaptive_quorum():
    assert ConsensusEngine().threshold_for('TREND') >= .70
    assert ConsensusEngine().threshold_for('CHOP') >= .768
    with pytest.raises(ValueError):
        ConsensusEngine(trend_floor=1.5)
