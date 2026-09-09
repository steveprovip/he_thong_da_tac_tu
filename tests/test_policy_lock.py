"""Single strict policy tier — no weaker testnet population (Astra precondition)."""
import pytest

from core.runtime import POLICY_VERSION, Runtime


def test_policy_version_is_pinned():
    assert POLICY_VERSION == 'strict-2026-09-09'


def test_testnet_uses_strict_gates(engine_factory):
    engine = engine_factory('testnet')
    assert engine.simulated is True  # venue label only, not a weaker gate
    assert (engine.edge_multiple, engine.threshold, engine.veto_threshold) == (1.8, 0.768, 0.70)
    assert engine.trend_floor == engine.threshold  # TREND never dips below headline


def test_research_override_still_available(engine_factory):
    argv = ('--seconds', '1', '--symbols', 'BTCUSDT', '--no-ui',
            '--threshold', '0.50', '--edge-multiple', '0.6', '--veto-threshold', '0.45')
    engine = engine_factory('testnet', argv=argv)
    assert (engine.threshold, engine.edge_multiple, engine.veto_threshold) == (0.50, 0.6, 0.45)


def test_policy_snapshot_stamp(engine_factory):
    snap = engine_factory('testnet').policy_snapshot()
    assert snap == {'policy': 'STRICT', 'policy_version': POLICY_VERSION,
                    'edge_multiple': 1.8, 'toxic_vpin': 0.65,
                    'threshold': 0.768, 'veto_threshold': 0.70, 'trend_floor': 0.768}
