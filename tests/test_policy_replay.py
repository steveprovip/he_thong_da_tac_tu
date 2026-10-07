"""Dream-RSI replay: parser strictness, re-pricing algebra, and objective maths.

The danger with an off-policy replay is not that it crashes; it is that it
returns a confident number nobody can reconstruct. So every quantity the engine
reports is checked here against an independent computation, and every row shape
the parser is allowed to accept is pinned.
"""
import ast
import inspect
import math
import statistics
import sys

import pytest

from research import policy_replay
from research.policy_replay import (ALPHA_BUDGET, BASELINE, BASELINE_WEIGHT, COST6,
                                    COST8, PolicyCandidate, PolicyReplayEngine,
                                    ReplayProbe, ReplaySchemaError, Z_95, cvar,
                                    edge_multiple_grid, lcb, parse_row, veto_grid)


def row(**over):
    base = {'schema': 2, 'source': 'testnet', 'evidence': 'counterfactual_assumed_fill',
            'symbol': 'BTCUSDT', 'side': 'BUY', 'opened': 1000.0, 'closed': 1300.0,
            'outcome': 'TIMEOUT', 'consensus': .78, 'conviction': .5, 'edge_bps': 12.0,
            'cost_bps': 6.0, 'gross_bps': 20.0, 'net_bps': 14.0, 'gated': False,
            'blockers': ['ZEPHR', 'RUNE'], 'top_alpha': 'NORO'}
    base.update(over)
    if 'net_bps' not in over and ('gross_bps' in over or 'cost_bps' in over):
        base['net_bps'] = base['gross_bps'] - base['cost_bps']
    return base


def probe(**over):
    return parse_row(row(**over))


# ==================================================================== parser
def test_schema_2_and_3_parse_and_legacy_rows_are_refused():
    assert probe(schema=2, top_alpha='').tier == 'schema2'
    assert probe(schema=2, top_alpha='NORO').tier == 'schema3'   # feature tier, not version
    assert probe(schema=3, top_alpha='').tier == 'schema3'
    for bad in (None, 1, 4, '2'):
        with pytest.raises(ReplaySchemaError):
            probe(schema=bad)
    legacy = row(); legacy.pop('schema')
    with pytest.raises(ReplaySchemaError):
        parse_row(legacy)


@pytest.mark.parametrize('field', ['symbol', 'side', 'opened', 'closed', 'outcome',
                                   'consensus', 'conviction', 'edge_bps', 'cost_bps',
                                   'gross_bps', 'net_bps', 'gated', 'blockers'])
def test_every_required_field_is_required(field):
    payload = row()
    payload.pop(field)
    with pytest.raises(ReplaySchemaError):
        parse_row(payload)


@pytest.mark.parametrize('over', [
    {'consensus': float('nan')}, {'edge_bps': float('inf')}, {'opened': None},
    {'side': 'HOLD'}, {'outcome': 'CANCELLED'}, {'gated': 'false'},
    {'blockers': 'ZEPHR'}, {'blockers': [7]}, {'consensus': 1.4},
    {'conviction': -0.1}, {'top_alpha': 'VESKA'}, {'cost_bps': -1.0, 'net_bps': 21.0},
])
def test_doubtful_rows_are_refused_not_coerced(over):
    with pytest.raises(ReplaySchemaError):
        parse_row(row(**over))


def test_net_must_equal_gross_minus_cost():
    """Without the identity, re-pricing at another cost model is fabrication."""
    with pytest.raises(ReplaySchemaError):
        parse_row(row(gross_bps=20.0, cost_bps=6.0, net_bps=13.5))
    assert probe(gross_bps=20.0, cost_bps=6.0, net_bps=14.0).net_bps == 14.0


@pytest.mark.parametrize('cost, model', [(6.0, 'cost6'), (8.0, 'cost8'), (8.25, 'cost:8.2500')])
def test_cost6_and_cost8_models_are_labelled(cost, model):
    assert probe(cost_bps=cost, gross_bps=20.0).cost_model == model


def test_repricing_is_exact_arithmetic_not_a_model():
    p = probe(cost_bps=COST8, gross_bps=20.0)          # net == 12.0 as recorded
    assert p.net_bps == 12.0
    assert p.net_at(COST6) == 14.0                     # 2 bps of legacy overstatement
    assert p.net_at(p.cost_bps) == p.net_bps


# ============================================================ selection rules
def test_zephr_hurdle_is_edge_versus_multiple_times_cost():
    p = probe(edge_bps=10.8, cost_bps=6.0, gross_bps=20.0)
    assert p.selected_by(PolicyCandidate(1.8, 0.70))            # 10.8 >= 1.8 * 6
    assert not p.selected_by(PolicyCandidate(1.81, 0.70))
    assert probe(edge_bps=10.79).selected_by(PolicyCandidate(1.8, 0.70)) is False


def test_rune_veto_is_consensus_versus_tau():
    assert probe(consensus=.768).selected_by(PolicyCandidate(1.0, 0.768))
    assert not probe(consensus=.7679).selected_by(PolicyCandidate(1.0, 0.768))
    assert probe(consensus=.7679).selected_by(PolicyCandidate(1.0, 0.70))


def test_infrastructure_vetoes_are_never_replayed_away():
    """No value of m or tau reconnects a dropped private stream."""
    p = probe(blockers=['ZEPHR', 'RUNE', 'VESKA'], edge_bps=99.0, consensus=1.0)
    assert not p.replayable
    assert not p.selected_by(PolicyCandidate(1.0, 0.70))
    assert probe(blockers=['MARIN']).replayable is False
    assert probe(blockers=[]).replayable is True


def test_directional_reweighting_is_first_order_and_needs_attribution():
    p = probe(consensus=.70, conviction=.5, top_alpha='NORO')
    policy = PolicyCandidate(1.0, 0.70, (0.30, 0.20, 0.10))
    assert p.consensus_under(policy) == pytest.approx(.70 + (0.30 - BASELINE_WEIGHT) * .5)
    assert p.consensus_under(PolicyCandidate(1.0, 0.70)) == .70
    with pytest.raises(ReplaySchemaError):
        probe(top_alpha='').consensus_under(policy)


def test_policy_candidate_guards_its_ranges_and_budget():
    for bad in (0.99, 2.51, float('nan')):
        with pytest.raises(ValueError):
            PolicyCandidate(bad, 0.75)
    for bad in (0.69, 0.7681, float('inf')):
        with pytest.raises(ValueError):
            PolicyCandidate(1.5, bad)
    with pytest.raises(ValueError):
        PolicyCandidate(1.5, 0.75, (0.30, 0.20, 0.20))       # sums to 0.70
    with pytest.raises(ValueError):
        PolicyCandidate(1.5, 0.75, (0.60, 0.00))
    ok = PolicyCandidate(1.5, 0.75, (0.30, 0.20, 0.10))
    assert math.fsum(ok.weights) == ALPHA_BUDGET and ok.reweighted
    assert BASELINE.weight_map == {'NORO': .20, 'LUMEN': .20, 'TIDAL': .20}


def test_grids_stay_inside_the_specified_ranges():
    assert edge_multiple_grid(7)[0] == 1.0 and edge_multiple_grid(7)[-1] == 2.5
    assert veto_grid(5)[0] == 0.70 and veto_grid(5)[-1] == 0.768
    assert all(1.0 <= m <= 2.5 for m in edge_multiple_grid(11))
    assert all(0.70 <= t <= 0.768 for t in veto_grid(9))


# ================================================================ statistics
def test_lcb_is_the_one_sided_95_percent_bound_on_the_total():
    s = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    n = len(s)
    expected = n * statistics.mean(s) - Z_95 * statistics.stdev(s) * math.sqrt(n)
    assert lcb(s) == pytest.approx(expected)
    assert lcb([3.0] * 5) == pytest.approx(15.0)      # zero variance -> point total
    assert lcb([]) == 0.0
    assert lcb([1.0]) == float('-inf')                # one sample bounds nothing
    assert lcb(s) < math.fsum(s)                      # a bound, never the estimate


def test_cvar_is_the_mean_of_the_worst_tail_and_is_positive():
    s = [-10.0, -4.0, 0.0, 14.0]
    assert cvar(s, .95) == pytest.approx(10.0)        # ceil(4 * .05) = 1 worst
    assert cvar(s, .50) == pytest.approx(7.0)         # worst two
    assert cvar([1.0, 2.0, 3.0]) == 0.0               # no losing tail
    assert cvar([]) == 0.0


# ==================================================================== engine
def synthetic():
    """Four OOS probes with hand-computable outcomes, plus one in-sample row."""
    rows = [
        probe(opened=1.0, symbol='SKIP'),                                        # in-sample
        probe(opened=2.0, symbol='A', edge_bps=12.0, gross_bps=20.0, consensus=.78,
              conviction=.5, top_alpha='NORO'),                                  # net +14
        probe(opened=3.0, symbol='B', edge_bps=10.0, gross_bps=-4.0, consensus=.78,
              conviction=.4, top_alpha='LUMEN'),                                 # net -10
        probe(opened=4.0, symbol='C', edge_bps=20.0, gross_bps=2.0, consensus=.72,
              conviction=.6, top_alpha='TIDAL'),                                 # net -4
        probe(opened=5.0, symbol='D', edge_bps=30.0, gross_bps=50.0, consensus=.90,
              conviction=.9, top_alpha=''),                                      # net +44
    ]
    return PolicyReplayEngine(rows, train_fraction=0.2)


def test_split_is_chronological_and_never_shuffled():
    engine = synthetic()
    in_sample, oos = engine.split()
    assert [p.symbol for p in in_sample] == ['SKIP']
    assert [p.symbol for p in oos] == ['A', 'B', 'C', 'D']
    assert [p.opened for p in engine.probes] == sorted(p.opened for p in engine.probes)


def test_baseline_selection_matches_the_shipped_gate():
    engine = synthetic()
    _, oos = engine.split()
    taken = [p.symbol for p in oos if p.selected_by(BASELINE, p.cost_bps)]
    assert taken == ['A', 'D']            # B fails m=1.8, C fails tau=0.768


def test_objective_is_lcb_minus_lambda_times_cvar():
    engine = synthetic()
    loose = PolicyCandidate(1.5, 0.70, label='LOOSE')
    result = engine.evaluate(loose, lam=1.0)

    assert result.trades == 4 and result.baseline_trades == 2
    assert result.net_bps == pytest.approx(44.0)          # 14 - 10 - 4 + 44
    assert result.baseline_net_bps == pytest.approx(58.0)  # 14 + 44
    assert result.delta_bps == pytest.approx(-14.0)
    assert result.hit_rate == pytest.approx(0.5)

    deltas = [0.0, -10.0, -4.0, 0.0]
    taken = [14.0, -10.0, -4.0, 44.0]
    assert result.lcb_95 == pytest.approx(lcb(deltas))
    assert result.cvar_95 == pytest.approx(cvar(taken)) == pytest.approx(10.0)
    assert result.objective == pytest.approx(result.lcb_95 - 1.0 * result.cvar_95)
    assert engine.evaluate(loose, lam=0.0).objective == pytest.approx(result.lcb_95)
    assert engine.evaluate(loose, lam=3.0).objective == pytest.approx(
        result.lcb_95 - 3.0 * result.cvar_95)
    with pytest.raises(ValueError):
        engine.evaluate(loose, lam=-1.0)


def test_baseline_against_itself_has_zero_delta_and_zero_bound():
    result = synthetic().evaluate(BASELINE)
    assert result.delta_bps == 0.0 and result.lcb_95 == 0.0
    assert result.trades == result.baseline_trades == 2


def test_cost_model_swap_changes_pnl_and_selection_coherently():
    engine = synthetic()
    at_eight = PolicyReplayEngine(engine.probes, cost_model='cost8', train_fraction=0.2)
    loose = PolicyCandidate(1.0, 0.70)
    # Same probes, 2 bps more cost on each of the four trades taken.
    assert at_eight.evaluate(loose).net_bps == pytest.approx(
        engine.evaluate(loose).net_bps - 4 * 2.0)
    assert at_eight.cost_for(engine.probes[0]) == COST8
    assert engine.cost_for(engine.probes[0]) == COST6
    with pytest.raises(ValueError):
        PolicyReplayEngine(engine.probes, cost_model='-1').cost_for(engine.probes[0])


def test_reweighted_run_drops_rows_it_cannot_attribute():
    engine = synthetic()
    weighted = PolicyCandidate(1.5, 0.70, (0.30, 0.20, 0.10))
    result = engine.evaluate(weighted)
    assert result.skipped_unattributable == 1          # probe D has no top_alpha
    assert result.oos_probes == 3 and 'D' not in result.name
    assert engine.evaluate(PolicyCandidate(1.5, 0.70)).skipped_unattributable == 0


def test_sweep_ranks_by_objective_and_grid_is_the_full_product():
    engine = synthetic()
    ranked = engine.sweep(engine.grid(edge_steps=3, veto_steps=2))
    assert len(ranked) == 6
    assert [r.objective for r in ranked] == sorted((r.objective for r in ranked), reverse=True)
    assert engine.best(engine.grid(3, 2)).objective == ranked[0].objective
    assert '| policy |' in engine.report(ranked)


def test_train_fraction_must_leave_both_halves_non_empty():
    with pytest.raises(ValueError):
        PolicyReplayEngine([], train_fraction=0.0)
    with pytest.raises(ValueError):
        PolicyReplayEngine([], train_fraction=1.0)


# ================================================== the real recorded dataset
def test_engine_reads_the_recorded_testnet_probes():
    engine = PolicyReplayEngine.load('testnet')
    summary = engine.summary()
    assert summary['probes'] == 2550 and summary['rejected'] == 0
    assert summary['tiers']['schema2'] + summary['tiers']['schema3'] == 2550
    assert summary['cost_models']['cost6'] + summary['cost_models']['cost8'] == 2550
    assert summary['replayable'] + summary['infrastructure_vetoed'] == 2550
    assert summary['in_sample'] + summary['out_of_sample'] == summary['replayable']
    assert summary['live_orders'] == 0 and summary['llm_calls'] == 0
    result = engine.evaluate(BASELINE)
    assert result.delta_bps == 0.0 and result.trades == result.baseline_trades
    with pytest.raises(FileNotFoundError):
        PolicyReplayEngine.load('no-such-mode')


def test_replay_touches_no_gateway_and_no_model():
    """0 live orders, 0 LLM calls -- enforced structurally, not by promise.

    Read the module's own import graph rather than trusting the docstring: the
    engine may only reach the standard library.
    """
    tree = ast.parse(inspect.getsource(policy_replay))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split('.')[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split('.')[0])
    assert imported <= {'dataclasses', 'json', 'math', 'os', 'pathlib', 'argparse'}
    for banned in ('live', 'core', 'aiohttp', 'websockets', 'requests', 'urllib',
                   'socket', 'http', 'subprocess', 'openai', 'anthropic'):
        assert banned not in imported, banned
    assert not hasattr(policy_replay, 'submit')
    assert sys.modules['research.policy_replay'].__dict__.get('live') is None
