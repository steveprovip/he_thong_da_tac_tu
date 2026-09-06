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
