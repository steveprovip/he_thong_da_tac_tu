"""Warm-start from Signal-Lab counterfactuals must be conservative and honest."""
import json
import math
import pytest

from core.consensus_engine import ConsensusEngine, WEIGHTS
from core.position_sizer import PositionSizer
from core.warm_start import ope_summary, policy_subset, seed_estimators, warm_start


def probe(net_bps, side='BUY', outcome='TP', conviction=.8, edge_bps=30., cost_bps=10., vpin=.5):
    return {'outcome': outcome, 'net_bps': net_bps, 'side': side, 'conviction': conviction,
            'edge_bps': edge_bps, 'cost_bps': cost_bps, 'vpin': vpin}


def write_lab(path, rows):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('\n'.join(json.dumps(r) for r in rows) + '\n', encoding='utf-8')


def test_policy_subset_mirrors_current_gates():
    probes = [probe(10), probe(10, conviction=.2), probe(10, edge_bps=5),
              probe(10, vpin=.99), probe(10, vpin=None), probe(10, side='HOLD')]
    subset = policy_subset(probes)
    assert len(subset) == 1 and ope_summary(subset)['n'] == 1


def test_assumed_fill_seed_is_rejected_without_mutation():
    sizer = PositionSizer()
    probes = [probe(40 if i % 2 else -15, outcome='TP' if i % 2 else 'SL') for i in range(25)]
    with pytest.raises(ValueError, match='cannot seed'):
        seed_estimators(sizer, None, probes, haircut=.5)
    assert sizer.kelly() is None and not sizer.outcomes


def test_consensus_alpha_budget_untouched_by_warm_start():
    engine = ConsensusEngine()
    before = dict(engine.weights)
    with pytest.raises(ValueError):
        seed_estimators(PositionSizer(), engine, [probe(30) for _ in range(30)])
    assert engine.weights == before
    assert math.fsum(engine.weights.values()) == pytest.approx(1)
    for name in set(WEIGHTS) - {'NORO', 'LUMEN', 'TIDAL'}:
        assert engine.weights[name] == WEIGHTS[name]


def test_malformed_or_undersized_lab_stays_in_warmup(tmp_path):
    lab = tmp_path/'lab.jsonl'
    lab.write_text('not json\n{"outcome":"TP"}\n\n' + json.dumps(probe(10)) + '\n', encoding='utf-8')
    sizer = PositionSizer()
    report = warm_start(sizer, None, [('testnet', lab, .75, True)])
    assert report['seeded'] == 0 and sizer.kelly() is None


def test_negative_ope_verdict_is_never_seeded(tmp_path):
    lab = tmp_path/'lab.jsonl'
    write_lab(lab, [probe(-20, outcome='SL') for _ in range(25)])
    sizer = PositionSizer()
    report = warm_start(sizer, None, [('testnet', lab, .75, True)])
    assert report['seeded'] == 0 and report['n'] == 25 and report['sources'][0]['mean_net_bps'] < 0
    assert sizer.kelly() is None


def test_reports_every_source_without_picking_profitable_logs(tmp_path):
    weak = tmp_path/'testnet-lab.jsonl'
    strong = tmp_path/'backtest-lab.jsonl'
    write_lab(weak, [probe(-5) for _ in range(25)])
    write_lab(strong, [probe(30 if i % 2 else -10, outcome='TP' if i % 2 else 'SL') for i in range(30)])
    sizer = PositionSizer()
    report = warm_start(sizer, None, [('testnet', weak, .75, True), ('backtest', strong, .5, False)])
    assert [r['source'] for r in report['sources']] == ['testnet', 'backtest']
    assert report['sources'][0]['mean_net_bps'] < 0
    assert report['seeded'] == 0 and sizer.kelly() is None


def test_runtime_excludes_its_own_counterfactual_lab_log(engine_factory, tmp_path):
    lab = tmp_path/'data'/'testnet-signal-lab.jsonl'
    write_lab(lab, [probe(35 if i % 3 else -12, outcome='TP' if i % 3 else 'SL') for i in range(30)])
    runtime = engine_factory()
    assert runtime.sizer.kelly() is None and not runtime.sizer.outcomes
    assert runtime.lab_warmup is None
