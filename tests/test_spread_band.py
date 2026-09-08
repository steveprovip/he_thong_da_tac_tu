"""F5 spread band: entry gate on bid-ask spread + cancel-unconfirmed retry."""
import asyncio
from types import SimpleNamespace

import pytest

from core.runtime import spread_block
from live.binance_futures_client import BinanceError
from live.execution_gateway import ExecutionGateway


# ------------------------------------------------------------- spread_block
def test_spread_block_ceiling_drops_wide_tail():
    assert spread_block(29.24, floor=2, ceiling=8) == 'WIDE_SPREAD'
    assert spread_block(41.0, ceiling=8) == 'WIDE_SPREAD'


def test_spread_block_floor_keeps_filter_family_band():
    assert spread_block(1.11, floor=2, ceiling=8) == 'TIGHT_SPREAD'


def test_spread_block_allows_inside_band_and_defaults_off():
    assert spread_block(5.0, floor=2, ceiling=8) == ''
    assert spread_block(900.0) == ''
    assert spread_block(0.5) == ''


def test_spread_block_unmeasurable_fails_configured_sides():
    assert spread_block(None, floor=2, ceiling=8) == 'TIGHT_SPREAD'
    assert spread_block(None, ceiling=8) == 'WIDE_SPREAD'
    assert spread_block(None) == ''


# --------------------------------------------- cancel-unconfirmed hardening
def _gateway():
    gw = object.__new__(ExecutionGateway)
    gw.halted = False
    return gw


def _order():
    return SimpleNamespace(status='NEW', filled=0.0,
                           intent=SimpleNamespace(symbol='SOLUSDT'), client_id='gt-abc')


def test_cancel_entry_recovers_when_venue_state_trails_ack():
    """First query still NEW (lag), second confirms CANCELED -> no halt."""
    gw, order = _gateway(), _order()
    calls = {'cancel': 0, 'order': 0}

    class FakeClient:
        async def cancel(self, symbol, client_id):
            calls['cancel'] += 1
            if calls['cancel'] == 1:
                raise BinanceError(-2013, 400, False)
            return {'status': 'CANCELED', 'executedQty': 0}

        async def order(self, symbol, client_id):
            calls['order'] += 1
            return {'status': 'NEW', 'executedQty': 0}

    gw.client = FakeClient()
    asyncio.get_event_loop().run_until_complete(gw._cancel_entry(order))
    assert calls['cancel'] == 2 and calls['order'] == 1
    assert not gw.halted


def test_cancel_entry_halts_only_after_second_unconfirmed_check():
    gw, order = _gateway(), _order()

    class FakeClient:
        async def cancel(self, symbol, client_id):
            return {'status': 'NEW', 'executedQty': 0}

        async def order(self, symbol, client_id):
            return {'status': 'NEW', 'executedQty': 0}

    gw.client = FakeClient()
    with pytest.raises(RuntimeError, match='SOLUSDT.*gt-abc.*NEW'):
        asyncio.get_event_loop().run_until_complete(gw._cancel_entry(order))
    assert gw.halted