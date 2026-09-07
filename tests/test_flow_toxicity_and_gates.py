"""Flow-toxicity measurement and the gates that consume it.

The system ran flat because VPIN was saturated, not because flow was toxic. These
tests pin the distinction: an undersized bucket reports 1.0 on ordinary tape, a
calibrated one reports a real number, and genuinely toxic flow still vetoes.
"""
import math
import random

import pytest

from core.consensus_engine import ConsensusEngine, Vote, WEIGHTS
from core.groktopus_swarm import GroktopusSwarm
from core.real_alpha_engine import Features, VolumeBuckets, calibrate_bucket_volume
from core.runtime import DIRECTIONAL, arguments, directional_score, volatility_block


def sweep_tape(count=20000, mean=.02, sigma=1.9, run_length=4.0, seed=11, start=1_700_000_000_000):
    """Heavy-tailed aggTrade sizes arriving in same-side bursts, as Binance delivers them."""
    rng = random.Random(seed)
    mu = math.log(mean) - sigma * sigma / 2
    trades, side, left = [], True, 0
    for i in range(count):
        if left <= 0:
            left = max(1, int(rng.expovariate(1 / run_length)))
            side = rng.random() < .5
        left -= 1
        trades.append({'q': f'{rng.lognormvariate(mu, sigma):.8f}', 'T': start + i * 50, 'm': not side})
    return trades


def fill(buckets, trades):
    for trade in trades:
        buckets.add(float(trade['q']), not trade['m'])
    return buckets


def features(**overrides):
    base = dict(obi=0., ofi=0., cvd=0., flow=0., vpin=.30, vpin_saturation=.05, vwap_z=0.,
                momentum=0., rsi=50., atr=10., sweep=0, spread_bps=1.5, ready=True)
    return Features(**{**base, **overrides})


# --------------------------------------------------------------- VPIN degeneracy
def test_undersized_buckets_saturate_vpin_on_ordinary_tape():
    """The old rule: bucket = 10x the mean trade. One sweep owns whole buckets."""
    trades = sweep_tape()
    legacy = math.fsum(float(t['q']) for t in trades[:1000]) / 100
    buckets = fill(VolumeBuckets(legacy, 50), trades)
    assert buckets.value > .70, 'expected the documented saturation artefact'
    assert buckets.saturation > .40, 'most buckets were filled by a single side'


def test_calibrated_buckets_return_vpin_to_a_measurable_band():
    trades = sweep_tape()
    # Wider buckets fill slower, so read the partial window the runtime uses.
    buckets = fill(VolumeBuckets(calibrate_bucket_volume(trades), 50, min_buckets=10), trades)
    legacy = math.fsum(float(t['q']) for t in trades[:1000]) / 100
    reference = fill(VolumeBuckets(legacy, 50), trades)
    assert 0 < buckets.value < .65, f'VPIN {buckets.value} should sit below the toxicity gate'
    assert buckets.value < reference.value / 2, 'calibration must remove the saturation inflation'
    assert buckets.saturation < .10


def test_calibration_floors_on_the_largest_prints():
    """A bucket must hold many p95-sized prints, whatever the time-based estimate says."""
    trades = [{'q': '0.001', 'T': 1_700_000_000_000 + i * 10} for i in range(1000)]
    trades += [{'q': '50.0', 'T': 1_700_000_000_000 + 10_000 + i} for i in range(60)]
    volume = calibrate_bucket_volume(trades)
    p95 = sorted(float(t['q']) for t in trades)[int(.95 * len(trades))]
    assert volume >= 25 * p95


def test_calibration_survives_a_tape_without_timestamps():
    trades = [{'q': '1.0'} for _ in range(1000)]
    assert calibrate_bucket_volume(trades) > 0
    assert calibrate_bucket_volume([]) > 0


def test_partial_window_reports_vpin_once_it_is_meaningful():
    buckets = VolumeBuckets(1.0, 50, min_buckets=10)
    for _ in range(9):
        buckets.add(.6, True)
        buckets.add(.4, False)
    assert buckets.value is None
    buckets.add(.6, True)
    buckets.add(.4, False)
    assert buckets.value == pytest.approx(.2)
    # A full window is still averaged over the buckets actually present.
    assert VolumeBuckets(1.0, 50).value is None


def test_saturation_is_none_before_any_bucket_closes():
    assert VolumeBuckets(10.0, 50).saturation is None


# ------------------------------------------------------------------- cost model
def implied_cost_ratio(spread_a, spread_b):
    """cost(a)/cost(b), recovered from ZEPHR's reason with the edge term cancelled."""
    swarm = GroktopusSwarm()
    ratios = []
    for spread in (spread_a, spread_b):
        _, votes = swarm.evaluate(features(obi=.5, spread_bps=spread), 10000., True, True, True, True)
        zephr = next(v for v in votes if v.agent == 'ZEPHR')
        ratios.append(float(zephr.reason.split('edge/cost=')[1].split()[0]))
    return ratios[1] / ratios[0]


def test_maker_entry_is_not_charged_the_full_spread():
    # New model: fees 7.0 + spread/2 + slippage 1.0 -> cost(4.0)/cost(0.0) = 10.0/8.0.
    assert implied_cost_ratio(4.0, 0.0) == pytest.approx(1.25, abs=.02)
    # The old model charged the whole spread on both legs: 13.0/9.0 = 1.444.
    assert implied_cost_ratio(4.0, 0.0) < 1.40


def conviction():
    """All three alpha agents strongly long, so only the gates decide."""
    return features(obi=.9, vwap_z=-2., momentum=1.5, rsi=30., atr=400., spread_bps=1.0)


def test_cost_thresholds_are_configurable_without_editing_logic():
    swarm = GroktopusSwarm()
    strict, _ = swarm.evaluate(conviction(), 10000., True, True, True, True, edge_multiple=1.8)
    loose, _ = swarm.evaluate(conviction(), 10000., True, True, True, True, edge_multiple=50.0)
    assert strict.allowed, 'high conviction in a volatile tape should clear 1.8x'
    assert not loose.allowed and 'ZEPHR' in {v.agent for v in
        swarm.evaluate(conviction(), 10000., True, True, True, True, edge_multiple=50.0)[1] if v.veto}


# ------------------------------------------------------------- toxicity still bites
def test_genuinely_toxic_flow_still_vetoes_after_recalibration():
    swarm = GroktopusSwarm()
    toxic = features(obi=.9, vwap_z=-2., momentum=1.5, rsi=30., atr=400.,
                     spread_bps=1.0, vpin=.92, vpin_saturation=.05)
    decision, votes = swarm.evaluate(toxic, 10000., True, True, True, True)
    blockers = {v.agent for v in votes if v.veto}
    assert {'ZEPHR', 'RUNE'} <= blockers
    assert decision.veto and not decision.allowed
    assert 'VPIN' in next(v.reason for v in votes if v.agent == 'ZEPHR')


def test_warmup_is_reported_separately_from_toxicity():
    swarm = GroktopusSwarm()
    _, votes = swarm.evaluate(features(vpin=None, ready=False), 10000., True, True, True, True)
    assert 'WARMUP' in next(v.reason for v in votes if v.agent == 'ZEPHR')


# --------------------------------------------------------------- consensus optics
def test_directional_score_strips_the_gate_subsidy():
    """A 0.768 headline only implies 0.613 of real directional conviction."""
    votes = [Vote(a, 'BUY', .613, 'alpha') for a in DIRECTIONAL]
    votes += [Vote(a, 'BUY', 1.0, 'gate') for a in WEIGHTS if a not in DIRECTIONAL]
    headline = ConsensusEngine().evaluate(votes)
    assert headline.score == pytest.approx(.768, abs=.002)
    assert directional_score(votes) == pytest.approx(.613, abs=.002)


def test_removing_the_subsidy_would_tighten_not_loosen_the_gate():
    gate_weight = math.fsum(w for a, w in WEIGHTS.items() if a not in DIRECTIONAL)
    directional_weight = math.fsum(WEIGHTS[a] for a in DIRECTIONAL)
    assert gate_weight == pytest.approx(.40) and directional_weight == pytest.approx(.60)
    # Stratified voting would demand 0.768 of the trio instead of today's 0.613.
    assert (.768 - gate_weight) / directional_weight < .768


def test_red_gates_alone_explain_the_observed_score_band():
    """Dashboard showed 27.6-43.8% with 'RUNE VETO'; that is gates at zero, not dilution."""
    votes = [Vote('NORO', 'BUY', .19, 'x'), Vote('LUMEN', 'BUY', .89, 'x'), Vote('TIDAL', 'SELL', .25, 'x')]
    votes += [Vote(a, 'HOLD', 0.0, 'gate', veto=a in ('ZEPHR', 'RUNE'))
              for a in WEIGHTS if a not in DIRECTIONAL]
    decision = ConsensusEngine().evaluate(votes)
    assert decision.veto and decision.score < .45
    assert directional_score(votes) > decision.score


# ------------------------------------------------------------ volatility band
def test_volatility_band_blocks_both_tails():
    """ATR >= 20 bps measured -4.03 bps gross (95% CI [-7.44, -0.66]) over 5178
    probes, so the ceiling excludes a measured-negative tail, not a guess."""
    assert volatility_block(12.0, 6.0, 20.0) == ''
    assert volatility_block(6.0, 6.0, 20.0) == '', 'the floor is inclusive'
    assert volatility_block(20.0, 6.0, 20.0) == '', 'the ceiling is inclusive'
    assert volatility_block(5.9, 6.0, 20.0) == 'LOW_VOLATILITY'
    assert volatility_block(20.1, 6.0, 20.0) == 'HIGH_VOLATILITY'


def test_either_bound_at_zero_disables_that_side():
    assert volatility_block(0.4, 0.0, 20.0) == ''
    assert volatility_block(4000.0, 6.0, 0.0) == ''
    assert volatility_block(4000.0, 0.0, 0.0) == ''


def test_missing_atr_fails_whichever_bound_is_configured():
    """An unmeasurable move is not evidence that the move is tradeable."""
    assert volatility_block(None, 6.0, 20.0) == 'LOW_VOLATILITY'
    assert volatility_block(None, 0.0, 20.0) == 'HIGH_VOLATILITY'
    assert volatility_block(None, 0.0, 0.0) == ''


def test_volatility_band_arguments_are_validated():
    base = ['--seconds', '1', '--symbols', 'BTCUSDT', '--no-ui']
    args = arguments('testnet', base + ['--min-atr-bps', '6', '--max-atr-bps', '20'])
    assert (args.min_atr_bps, args.max_atr_bps) == (6.0, 20.0)
    assert arguments('testnet', base).max_atr_bps == 0.0
    for bad in (['--max-atr-bps', '-1'], ['--min-atr-bps', '20', '--max-atr-bps', '20'],
                ['--min-atr-bps', '20', '--max-atr-bps', '6'], ['--max-atr-bps', 'nan']):
        with pytest.raises(SystemExit):
            arguments('testnet', base + bad)


def test_runtime_wires_the_volatility_band(engine_factory):
    engine = engine_factory(argv=('--seconds', '1', '--symbols', 'BTCUSDT', '--no-ui',
                                  '--min-atr-bps', '6', '--max-atr-bps', '20'))
    assert (engine.min_atr_bps, engine.max_atr_bps) == (6.0, 20.0)
    assert engine.mission_summary()['max_atr_bps'] == 20.0


# ----------------------------------------------------------- conviction gate
def test_conviction_gate_arguments_are_validated():
    base = ['--seconds', '1', '--symbols', 'BTCUSDT', '--no-ui']
    assert arguments('testnet', base).min_conviction == 0.0, 'absent flag must be inert'
    assert arguments('testnet', base + ['--min-conviction', '0.3575']).min_conviction == 0.3575
    assert arguments('testnet', base + ['--min-conviction', '1']).min_conviction == 1.0
    for bad in ('-0.1', '1.1', 'nan'):
        with pytest.raises(SystemExit):
            arguments('testnet', base + ['--min-conviction', bad])


def test_conviction_gate_reads_alpha_conviction_not_the_headline_score():
    """directional_score strips the 0.40 of gate weight every authorized trade
    carries, so a 0.768 headline only implies 0.613 alpha conviction. Gating on
    the headline would be gating mostly on the gates agreeing with themselves."""
    votes = [Vote('NORO', 'BUY', .9, 'x'), Vote('LUMEN', 'BUY', .9, 'x'), Vote('TIDAL', 'BUY', .9, 'x')]
    votes += [Vote(a, 'HOLD', 0.0, 'gate') for a in WEIGHTS if a not in DIRECTIONAL]
    assert directional_score(votes) == pytest.approx(.9)
    assert ConsensusEngine().evaluate(votes).score == pytest.approx(.9 * .60)


def test_runtime_wires_the_conviction_gate(engine_factory):
    off = engine_factory(argv=('--seconds', '1', '--symbols', 'BTCUSDT', '--no-ui'))
    assert off.min_conviction == 0.0 and off.mission_summary()['min_conviction'] == 0.0
    on = engine_factory(argv=('--seconds', '1', '--symbols', 'BTCUSDT', '--no-ui',
                              '--min-conviction', '0.3575'))
    assert on.min_conviction == 0.3575
    assert on.mission_summary()['min_conviction'] == 0.3575
