"""Tests for 2026-11-11 entry gate liberation, knife-catching shield and web_ui charting terminal."""
import pytest
from pathlib import Path
import config.settings as cs
from core.web_ui_server import CSP
from tests.test_micro_noise import align_engine


def test_micro_settings_env_parses_ofi_and_edge_knobs():
    env = {'MICRO_OFI_MIN': '0.70', 'MICRO_MIN_EDGE_BPS': '7.0'}         # 11-13: the floor may only be raised
    out = cs.micro_capital_env(env)
    assert out['micro_ofi_min'] == 0.70
    assert out['scalp_min_edge_bps'] == 7.0


def test_micro_settings_env_rejects_invalid_values():
    with pytest.raises(ValueError, match='MICRO_OFI_MIN must be in'):
        cs.micro_capital_env({'MICRO_OFI_MIN': '1.5'})
    with pytest.raises(ValueError, match='rejected'):                     # 11-13 owner decision: 0.45 is refused
        cs.micro_capital_env({'MICRO_OFI_MIN': '0.45'})
    assert cs.micro_capital_env({'MICRO_OFI_MIN': '0.65'}) == {'micro_ofi_min': 0.65}
    with pytest.raises(ValueError, match='MICRO_MIN_EDGE_BPS must be in'):
        cs.micro_capital_env({'MICRO_MIN_EDGE_BPS': '-5'})


def test_web_ui_server_csp_allows_binance_feeds():
    assert 'wss://fstream.binance.com' in CSP
    assert 'https://fapi.binance.com' in CSP
    assert 'https://api.binance.com' in CSP


def test_chart_assets_exist():
    chart_dir = Path(__file__).resolve().parents[1] / 'web_ui' / 'chart'
    index_html = chart_dir / 'index.html'
    assert chart_dir.is_dir(), 'web_ui/chart directory missing'
    assert index_html.is_file(), 'web_ui/chart/index.html missing'
    content = index_html.read_text(encoding='utf-8')
    assert 'KlineChart UI Core' in content or 'root' in content


def test_micro_entry_block_respects_custom_ofi_floor(tmp_path):
    e = align_engine(tmp_path, micro_ofi_min=0.45, micro_ker_min=0.28)
    e.macro = {'allow': ('BUY', 'SELL')}

    # OFI +0.50 passes with 0.45 floor (would have failed with old 0.65 floor)
    f_pass = {'ker': 0.35, 'ofi_10s': 0.50, 'cvd_3m': 50000.0}
    blk = e.micro_entry_block('BUY', f_pass)
    assert blk == [], f'Expected pass, got {blk}'

    # Opposing OFI -0.20 still blocked
    f_bad = {'ker': 0.35, 'ofi_10s': -0.20, 'cvd_3m': 50000.0}
    blk_bad = e.micro_entry_block('BUY', f_bad)
    assert any(gate == 'MICRO_ALIGN' for gate, _ in blk_bad)
