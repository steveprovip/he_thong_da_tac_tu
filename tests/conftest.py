import os

import pytest

from config.settings import Settings
from core.runtime import Runtime, arguments
from core.web_ui_server import Telemetry

# 11-07: tmp_path lives under %TEMP% (C: here), but tools/download_public_l2.py refuses paths off F: and test files stay
# off C:. Root pytest's numbered temp dirs on F:\gt_scratch (read lazily by pytest; --basetemp still overrides).
if 'PYTEST_DEBUG_TEMPROOT' not in os.environ and os.path.isdir('F:/'):
    os.makedirs('F:/gt_scratch', exist_ok=True)
    os.environ['PYTEST_DEBUG_TEMPROOT'] = 'F:/gt_scratch'


@pytest.fixture
def engine_factory(monkeypatch, tmp_path):
    """Runtime wired to a temporary ROOT, for tests that need the real cycle helpers."""
    def build(mode='testnet', argv=('--seconds', '1', '--symbols', 'BTCUSDT', '--no-ui')):
        import core.runtime as module
        monkeypatch.setattr(module, 'ROOT', tmp_path)
        return Runtime(Settings.load(mode, env={}), arguments(mode, list(argv)), Telemetry())
    return build


@pytest.fixture(autouse=True)
def _micro_lane_profile(request, monkeypatch):
    """10-09 (dd): production demo profile mutes the standalone micro lane (micro_standalone=False) and trades the
    4H trend lane (trend_lane='active').  The ~1 400 micro-lane tests keep testing the micro logic (still used by
    the live profile and whenever micro_standalone is switched back on), so they see the micro lane ON and the
    trend lane PAPER.  Tests of the production defaults opt out with @pytest.mark.production_profile.
    10-24: the shipped demo turned the micro lane back on with the DEMO ACTIVE gates (chop scalper 'on', score floors
    0.768, KER 0.42 scalp ceiling, fixed MR bracket, Kelly window 20, router CHOP -> scalp only); the micro-lane tests
    keep the gates they were written against.  The new gates: tests/test_demo_active_2026_10_24.py."""
    if request.node.get_closest_marker('production_profile'):
        return
    from dataclasses import replace
    from core import engine as eng
    monkeypatch.setitem(eng.PROFILES, 'demo', replace(eng.PROFILES['demo'], micro_standalone=True,
                                                       trend_lane='paper', chop_scalper='shadow', min_score=0.82,
                                                       mr_min_score=0.80, scalp_ker_max=0.40, mr_bracket='vwap',
                                                       kelly_window=0, chop_router_scalp=False,
                                                       burst_wake_hold_s=0.0, intraday_close_margin_s=10.0,
                                                       tox_guard='off', regime_gate='off',
                                                       scalp_min_edge_bps=0.0,    # 11-07 (d): test_cockpit_carry.py
                                                       scalp_net_rr=False,        # 11-07 (e): test_scalp_net_rr_gate.py
                                                       scalp_macro_lock=False, scalp_cvd3m_usd=0.0,   # 11-08 (i):
                                                       scalp_align3=False, scalp_sl_min_pct=0.0,      # test_scalp_
                                                       scalp_net_rr_min=0.0,                          # trend_lock.py
                                                       swarm_chop_override=False))  # legacy micro tests; dedicated override tests opt in
    # 11-07 Pillar 2 / 3 gates have their own tests: tests/test_toxicity_guard.py, tests/test_regime_gate.py
