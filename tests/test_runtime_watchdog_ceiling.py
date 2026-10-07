"""Watchdog and hard-ceiling behaviour inside the real Runtime.cycle."""
import time
from types import SimpleNamespace

import pytest

from config.settings import Settings
from core.capital_gate import PAPER_ONLY, capital_verdict
from core.models import Book, Filters, dec
from core.runtime import Runtime, arguments
from core.web_ui_server import Telemetry

SYMBOL = 'BTCUSDT'


@pytest.fixture
def engine(monkeypatch, tmp_path):
    import core.runtime as module
    monkeypatch.setattr(module, 'ROOT', tmp_path)
    args = arguments('paper', ['--offline', '--seconds', '1', '--symbols', SYMBOL])
    eng = Runtime(Settings.load('paper', env={}), args, Telemetry())
    eng.client = SimpleNamespace(offset_ms=0)
    eng.filters[SYMBOL] = Filters(dec('.01'), dec('.001'), dec('.001'), dec('10000'), dec('5'))
    return eng


def _heartbeat(engine, sequence):
    """Identical book content, advancing timestamp: the stall freshness cannot see."""
    engine.books[SYMBOL] = Book(SYMBOL, ((68000., 3.),), ((68001., 3.),),
                                time.time(), sequence)


async def test_frozen_content_parks_the_runtime(engine):
    for i in range(3):
        _heartbeat(engine, i)
        await engine.cycle()
    assert engine.watchdog.parked is True
    assert engine.watchdog.repeats >= 3
    assert engine.block_counts['WATCHDOG_PARKED'] >= 1


async def test_a_moving_market_never_parks(engine):
    for i in range(6):
        price = 68000. + i
        engine.books[SYMBOL] = Book(SYMBOL, ((price, 3.),), ((price + 1, 3.),), time.time(), i)
        await engine.cycle()
    assert engine.watchdog.parked is False
    assert engine.watchdog.parks == 0
    assert 'WATCHDOG_PARKED' not in engine.block_counts


async def test_new_data_wakes_a_parked_runtime(engine):
    for i in range(4):
        _heartbeat(engine, i)
        await engine.cycle()
    assert engine.watchdog.parked is True
    engine.books[SYMBOL] = Book(SYMBOL, ((68050., 3.),), ((68051., 3.),), time.time(), 99)
    await engine.cycle()
    assert engine.watchdog.parked is False
    assert engine.watchdog.wakes == 1


async def test_watchdog_state_is_published_to_telemetry(engine):
    _heartbeat(engine, 1)
    await engine.cycle()
    diagnostics = engine.telemetry.snapshot()['diagnostics']
    assert 'watchdog' in diagnostics
    assert set(diagnostics['watchdog']) >= {'parked', 'repeats', 'limit', 'parks', 'wakes'}
    assert diagnostics['watchdog']['limit'] == 3


async def test_parking_never_touches_open_inventory(engine):
    """A data stall must not become a market order: park suppresses entries only."""
    for i in range(4):
        _heartbeat(engine, i)
        await engine.cycle()
    assert engine.watchdog.parked is True
    assert engine.paper.orders == {}          # nothing opened
    assert engine.brake.halted is False       # and nothing force-flattened


def test_hard_ceiling_is_wired_from_the_spec(engine):
    assert engine.b_hard == pytest.approx(3.85)
    assert engine.position_fraction == pytest.approx(0.15)


def test_this_book_cannot_fund_a_compliant_live_order(engine):
    """Venue minimum 5 USDT vs N_cap 0.5775: structurally PAPER_ONLY, by arithmetic."""
    verdict = capital_verdict(float(engine.filters[SYMBOL].min_notional) * 1.10,
                              spend=0.0, reserved=0.0,
                              b_hard=engine.b_hard, fraction=engine.position_fraction)
    assert verdict.n_cap == pytest.approx(0.5775)
    assert verdict.min_notional == pytest.approx(5.5)
    assert verdict.mode == PAPER_ONLY
    assert verdict.live_allowed is False


async def test_ev_head_reports_through_market_rows(engine):
    _heartbeat(engine, 1)
    await engine.cycle()
    row = engine.telemetry.snapshot()['markets'][0]
    assert row['edge_model'].startswith('NET_EV_')
    for key in ('net_ev_bps', 'p_fill', 'ev_cost_bps', 'ev_hold', 'edge_bps', 'cost_bps'):
        assert key in row
    assert row['ev_cost_bps'] == pytest.approx(10.0)
