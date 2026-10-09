import pytest

from config.settings import Settings
from core.runtime import Runtime, arguments
from core.web_ui_server import Telemetry


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
    trend lane PAPER.  Tests of the production defaults opt out with @pytest.mark.production_profile."""
    if request.node.get_closest_marker('production_profile'):
        return
    from dataclasses import replace
    from core import engine as eng
    monkeypatch.setitem(eng.PROFILES, 'demo', replace(eng.PROFILES['demo'], micro_standalone=True,
                                                       trend_lane='paper'))
