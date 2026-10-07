"""Loop breaker: SPRT/Wilson early stop, data budget, slow-brain permit, microprice quote."""
import math
import random
from decimal import Decimal
from types import SimpleNamespace

import pytest

from core.barrier_planner import maker_quote, microstructure_quote, queue_imbalance
from core.consensus_engine import ConsensusEngine, Vote, WEIGHTS
from core.early_stop import (BUDGET_EXHAUSTED, COST_ALL_IN_BPS, CONTINUE, EDGE_CONFIRMED,
                             GROSS_EDGE_HURDLE_BPS, MIN_SIGNALS, REGIME_UNPROFITABLE, DataBudget,
                             EarlyStopGate, breakeven_win_rate, directory_bytes, replay,
                             sprt_bounds, wilson_interval)
from core.groktopus_swarm import GroktopusSwarm
from core.models import Book
from core.real_alpha_engine import Features
from core.regime_permit import CARRY, SCALP, STAND_ASIDE, Permit, PermitPolicy, fast_hands_permit
from core.signal_lab import SignalLab
from phan_tich.slow_brain_contract import validate_verdict, verdict_from_rating


def gaussian(mean, sd, n, seed=1):
    rng = random.Random(seed)
    return [rng.gauss(mean, sd) for _ in range(n)]


# ------------------------------------------------------------------ constants
def test_cost_basis_matches_binance_usdm_and_hurdle_sits_above_it():
    assert COST_ALL_IN_BPS == pytest.approx(8.5)
    assert GROSS_EDGE_HURDLE_BPS == 9.0 > COST_ALL_IN_BPS
    assert MIN_SIGNALS == 200


# --------------------------------------------------------------------- Wilson
def test_wilson_interval_reference_values():
    assert wilson_interval(0, 0) == (0.0, 1.0)
    lo, hi = wilson_interval(5, 10)
    assert lo == pytest.approx(0.2366, abs=1e-4) and hi == pytest.approx(0.7634, abs=1e-4)
    lo, hi = wilson_interval(0, 96)
    assert lo == pytest.approx(0.0, abs=1e-12) and hi == pytest.approx(0.0385, abs=1e-3)
    with pytest.raises(ValueError):
        wilson_interval(3, 2)


def test_breakeven_and_sprt_bounds():
    assert breakeven_win_rate(10, 10) == .5
    assert breakeven_win_rate(20, 1) == pytest.approx(1 / 21)
    lower, upper = sprt_bounds(.05, .05)
    assert lower == pytest.approx(-2.944, abs=1e-3) and upper == pytest.approx(2.944, abs=1e-3)


# -------------------------------------------------------------- early stop gate
def test_no_verdict_before_200_signals_even_on_terrible_data():
    gate = EarlyStopGate()
    for x in gaussian(-20, 5, 199):
        assert gate.update(x).status == CONTINUE


def test_negative_regime_is_cut_at_exactly_200():
    idx, verdict = replay(gaussian(-8.0, 12.0, 5000))
    assert idx == 200 and verdict.status == REGIME_UNPROFITABLE
    assert 'EV_UCB95' in verdict.reason and verdict.ucb_bps <= 0


def test_fee_drag_regime_measured_on_this_repo_is_cut():
    """Lab: gross +0.96 bps, sd ~12 -> net at 8.5 bps is ~ -7.5 bps."""
    idx, verdict = replay(gaussian(0.96 - COST_ALL_IN_BPS, 12.0, 6795, seed=3))
    assert idx == 200 and verdict.status == REGIME_UNPROFITABLE
    waste = 1 - idx / 6795
    assert waste > .97


def test_marginal_regime_is_cut_by_sprt_not_left_running():
    idx, verdict = replay(gaussian(0.2, 6.0, 5000, seed=11), min_edge_bps=2.0)
    assert verdict.status == REGIME_UNPROFITABLE and idx is not None and idx < 5000


def test_real_edge_is_confirmed_and_collection_ends():
    idx, verdict = replay(gaussian(6.0, 10.0, 5000, seed=5))
    assert idx == 200 and verdict.status == EDGE_CONFIRMED and verdict.lcb_bps > 0


def test_wilson_gate_uses_explicit_minimum_win_rate():
    values = ([10.0] * 3 + [-1.0] * 7) * 30
    idx, verdict = replay(values, min_win_rate=.6)
    assert verdict.status == REGIME_UNPROFITABLE and 'WILSON' in verdict.reason
    assert verdict.mean_bps > 0


def test_budget_ends_an_inconclusive_run():
    gate = EarlyStopGate(budget=DataBudget(max_samples=250), min_edge_bps=50.0)
    rng = random.Random(9)
    verdict = None
    for _ in range(1000):
        verdict = gate.update(rng.gauss(0.0, 1e-3) + (0.5 if rng.random() < .5 else -0.4))
        if verdict.stop:
            break
    assert verdict.stop and gate.n <= 250


def test_gate_is_sticky_after_terminal_verdict():
    gate = EarlyStopGate(min_samples=10)
    for x in gaussian(-30, 1, 10):
        gate.update(x)
    assert gate.verdict.stop
    n = gate.n
    assert gate.update(1000.0).status == REGIME_UNPROFITABLE and gate.n == n


def test_gate_rejects_bad_input():
    with pytest.raises(ValueError):
        EarlyStopGate(min_samples=1)
    with pytest.raises(ValueError):
        EarlyStopGate().update(float('nan'))


# ---------------------------------------------------------------- data budget
def test_data_budget_dimensions(tmp_path):
    b = DataBudget(max_samples=2, max_bytes=100, max_seconds=10, started=0.0)
    assert b.exhausted(now=1) is None
    b.charge(1, 99)
    assert b.exhausted(now=1) is None
    b.charge(0, 1)
    assert b.exhausted(now=1).startswith('BYTES')
    assert DataBudget(max_seconds=10, started=0.0).exhausted(now=10).startswith('SECONDS')
    with pytest.raises(ValueError):
        b.charge(-1)
    (tmp_path / 'a').write_bytes(b'x' * 1234)
    assert directory_bytes(str(tmp_path)) == 1234
    assert directory_bytes(str(tmp_path / 'missing')) == 0


def test_l2_recorder_refuses_when_budget_spent(tmp_path, monkeypatch, capsys):
    import research.l2_recorder as rec
    (tmp_path / 'X').mkdir()
    (tmp_path / 'X' / 'd.jsonl').write_bytes(b'x' * 2_000_000)
    monkeypatch.setattr(rec, 'ROOT', tmp_path)
    assert rec.remaining_budget_bytes(1.0, root=tmp_path) < 0
    monkeypatch.setattr(rec, 'remaining_budget_bytes', lambda mb: -1)
    assert rec.main(['--seconds', '1']) == 3
    assert 'DATA_BUDGET_EXHAUSTED' in capsys.readouterr().out
    assert rec.DEFAULT_SECONDS <= 3600


def test_carry_scan_futility_stop():
    from phan_tich.carry_scan import futility_stop
    assert futility_stop(95) == (False, pytest.approx(wilson_interval(0, 95)[1]))
    stop, ucb = futility_stop(96)
    assert stop and ucb < .04


async def test_flush_shadow_loop_is_budgeted_not_open_ended(monkeypatch, capsys):
    import research.flush_shadow as fs
    monkeypatch.setattr(fs, 'fetch_rest_market_data', lambda: (None, None, None, None))
    await fs.run_shadow_loop(gate=EarlyStopGate(budget=DataBudget(max_seconds=1e-9)))
    assert 'BUDGET_EXHAUSTED' in capsys.readouterr().out
    assert fs.SHADOW_BUDGET_SECONDS <= 12 * 3600


def test_launcher_no_longer_defaults_to_eleven_hour_capture():
    import run_launcher
    assert run_launcher.L2_DEFAULT_HOURS <= 1


# ------------------------------------------------------------------ signal lab
def lab_features():
    return SimpleNamespace(ready=True, atr=1.0, spread_bps=1.0, vpin=.2, vpin_saturation=.1)


def test_signal_lab_stops_opening_probes_after_terminal_verdict():
    gate = EarlyStopGate(min_samples=10)
    lab = SignalLab(gate=gate, min_gap=1.0)
    book = Book('X', ((99.0, 1.),), ((99.02, 1.),), 0.)
    dec = SimpleNamespace(side='BUY', score=.5, allowed=False)
    plan = SimpleNamespace(stop=98.0, take_profit=110.0)
    now = 0.0
    while not lab.stopped:
        assert lab.observe('X', lab_features(), book, dec, [], plan, now, .5) is not None
        lab.resolve('X', Book('X', ((98.0, 1.),), ((98.02, 1.),), now + 1), now + 1)
        now += 2
    assert gate.verdict.status == REGIME_UNPROFITABLE and gate.n == 10
    assert lab.observe('X', lab_features(), book, dec, [], plan, now + 100, .5) is None
    assert lab.summary()['gate']['status'] == REGIME_UNPROFITABLE


# ---------------------------------------------------------------- permit
def test_permit_stands_aside_without_out_of_sample_evidence():
    p = fast_hands_permit(4.0, 10.0)
    assert not p.allowed and p.mode == STAND_ASIDE and p.reason == 'NO_OOS_EVIDENCE'


def test_permit_blocks_below_hurdle_and_on_unprofitable_regime():
    assert 'hurdle' in fast_hands_permit(4.0, 10.0, oos_gross_bps=4.39, oos_gross_lcb_bps=2.37).reason
    assert 'LCB95' in fast_hands_permit(4.0, 10.0, oos_gross_bps=9.5, oos_gross_lcb_bps=8.0).reason
    p = fast_hands_permit(4.0, 10.0, oos_gross_bps=12, oos_gross_lcb_bps=10, lab_status=REGIME_UNPROFITABLE)
    assert not p.allowed and 'REGIME_UNPROFITABLE' in p.reason


def test_permit_bands_and_scalp_grant():
    assert 'SPREAD' in fast_hands_permit(1.0, 10.0, 12, 10).reason
    assert 'ATR' in fast_hands_permit(4.0, 30.0, 12, 10).reason
    p = fast_hands_permit(4.0, 10.0, oos_gross_bps=12.0, oos_gross_lcb_bps=9.0)
    assert p.allowed and p.mode == SCALP


def test_permit_carry_requires_g_over_3c():
    policy = PermitPolicy()
    need = policy.carry_cost_multiple * policy.pair_cost_bps
    assert not fast_hands_permit(1.0, 3.0, funding_bps_per_period=need / 21, funding_periods=21).allowed
    p = fast_hands_permit(1.0, 3.0, funding_bps_per_period=-(need / 21 + .1), funding_periods=21)
    assert p.allowed and p.mode == CARRY


def test_slow_brain_verdict_can_only_block():
    now = 1000.0
    good = verdict_from_rating('Buy', 'BTCUSDT', now - 60, now - 30)
    assert validate_verdict(good, now) == (True, 'SLOW_BRAIN_PASS')
    assert not fast_hands_permit(4.0, 10.0, verdict=good, intent_ts=now).allowed   # PASS grants nothing
    for rating in ('Hold', 'REVIEW', 'garbage'):
        v = verdict_from_rating(rating, 'BTCUSDT', now - 60, now - 30)
        p = fast_hands_permit(4.0, 10.0, 12, 10, verdict=v, intent_ts=now)
        assert not p.allowed and p.reason.startswith('SLOW_BRAIN')
    stale = verdict_from_rating('Buy', 'BTCUSDT', now - 7200, now - 7100)
    assert fast_hands_permit(4.0, 10.0, 12, 10, verdict=stale, intent_ts=now).reason.startswith('SLOW_BRAIN')


def test_permit_policy_cannot_license_fee_drag():
    with pytest.raises(ValueError):
        PermitPolicy(gross_hurdle_bps=5.0)


# ---------------------------------------------------------------- swarm hook
def conviction():
    base = dict(obi=.9, ofi=0., cvd=0., flow=0., vpin=.30, vpin_saturation=.05, vwap_z=-2.,
                momentum=1.5, rsi=30., atr=400., sweep=0, spread_bps=1.0, ready=True)
    return Features(**base)


def test_permit_block_is_a_rune_veto_and_permit_none_is_unchanged():
    swarm = GroktopusSwarm()
    legacy, _ = swarm.evaluate(conviction(), 10000., True, True, True, True)
    assert legacy.allowed and legacy.blockers == ()
    blocked, votes = swarm.evaluate(conviction(), 10000., True, True, True, True,
                                    permit=Permit(False, STAND_ASIDE, 'NO_OOS_EVIDENCE'))
    rune = next(v for v in votes if v.agent == 'RUNE')
    assert rune.veto and 'SLOW_BRAIN' in rune.reason
    assert not blocked.allowed and blocked.blockers == ('RUNE',)
    assert blocked.reason == 'RUNE VETO [RUNE]'
    granted, _ = swarm.evaluate(conviction(), 10000., True, True, True, True,
                                permit=Permit(True, SCALP, 'ok'))
    assert granted.allowed and granted.score == pytest.approx(legacy.score)


def test_decision_names_every_blocking_agent():
    votes = [Vote(a, 'BUY', 1.0, 'x', veto=a in ('ZEPHR', 'OKAPI')) for a in WEIGHTS]
    d = ConsensusEngine().evaluate(votes)
    assert d.veto and d.blockers == ('ZEPHR', 'OKAPI') and d.reason == 'RUNE VETO [ZEPHR,OKAPI]'


def test_policy_thresholds_unchanged():
    engine = ConsensusEngine()
    assert engine.threshold == .768 and engine.veto_threshold == .70


# ---------------------------------------------------------- microprice quote
def book(bq, aq, bid=100.00, ask=100.01):
    return Book('X', ((bid, bq),), ((ask, aq),), 0.)


def test_microprice_quote_stands_aside_when_queue_runs_against_us():
    assert queue_imbalance(book(1., 9.)) == pytest.approx(-.8)
    assert microstructure_quote(book(1., 9.), 'BUY', .01, toxicity=.7) is None
    assert microstructure_quote(book(9., 1.), 'SELL', .01, toxicity=.7) is None


def test_microprice_quote_skews_deeper_on_adverse_lean_and_joins_on_support():
    assert microstructure_quote(book(9., 1.), 'BUY', .01) == Decimal('100.00')
    assert microstructure_quote(book(4., 6.), 'BUY', .01, toxicity=.7) == Decimal('99.99')
    assert microstructure_quote(book(6., 4.), 'SELL', .01, toxicity=.7) == Decimal('100.02')
    assert microstructure_quote(book(1., 9.), 'BUY', .01, toxicity=None) < Decimal('100.00')


def test_microprice_quote_never_crosses_and_inventory_skews():
    for bq in (1., 3., 5., 7., 9.):
        for side in ('BUY', 'SELL'):
            q = microstructure_quote(book(bq, 10 - bq), side, .01, toxicity=None)
            assert (q < Decimal('100.01')) if side == 'BUY' else (q > Decimal('100.00'))
    long_inv = microstructure_quote(book(5., 5.), 'BUY', .01, inventory=10., risk_aversion=.1,
                                    variance_per_second=.01, remaining_seconds=10., toxicity=None)
    assert long_inv < maker_quote(book(5., 5.), 'BUY', .01, 0., 0., 0., 0.)
    with pytest.raises(ValueError):
        microstructure_quote(book(5., 5.), 'BUY', .01, toxicity=1.5)


# ---------------------------------------------------------------- runtime CLI
def test_runtime_defaults_are_budgeted_and_permit_gated():
    from core.runtime import arguments
    args = arguments('paper', [])
    assert args.lab_budget == 2000 and not args.no_early_stop and not args.no_permit
    assert args.quote_model == 'touch'
    with pytest.raises(SystemExit):
        arguments('paper', ['--quote-toxicity', '2'])


# ------------------------------------------------------- L2 markout simulator
def test_markout_simulator_counts_trade_through_fill_as_adverse():
    from array import array
    from phan_tich.markout_l2 import simulate, summarize
    t, b, bq, a, aq = (array('d') for _ in range(5))
    for i in range(400):                       # 400 s flat book, then a one-way drop
        px = 100.0 if i < 301 else 99.98 if i < 341 else 99.96
        t.append(float(i)); b.append(px); a.append(px + .01); bq.append(5.); aq.append(5.)
    res = simulate((t, b, bq, a, aq), .01, gap=40., ttl=5., policy='touch')
    row = summarize(res, 400 / 3600)
    assert res['quotes'] == 6 and res['fills'] == 2          # only the BUYs are run over
    assert row['markout_1s_bps'] < 0 and row['net_30s_after_cost_bps'] < -COST_ALL_IN_BPS


def test_runtime_permit_follows_the_lab_gate():
    from core.runtime import Runtime
    feats = SimpleNamespace(atr=0.1)                       # 10 bps on a 100 mid
    bk = Book('X', ((99.98, 1.),), ((100.02, 1.),), 0.)     # 4 bps spread
    rt = SimpleNamespace(early_stop=EarlyStopGate())
    assert Runtime.permit_for(rt, feats, bk).reason == 'NO_OOS_EVIDENCE'
    for x in gaussian(12.0 - COST_ALL_IN_BPS, 2.0, 200, seed=2):   # proven 12 bps gross
        rt.early_stop.update(x)
    assert Runtime.permit_for(rt, feats, bk).mode == SCALP
    bad = SimpleNamespace(early_stop=EarlyStopGate())
    for x in gaussian(0.96 - COST_ALL_IN_BPS, 12.0, 200, seed=4):
        bad.early_stop.update(x)
    p = Runtime.permit_for(bad, feats, bk)
    assert not p.allowed and 'REGIME_UNPROFITABLE' in p.reason
