from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from core.consensus_engine import TokenAuthority, ConsensusEngine, Vote, WEIGHTS
from core.models import Intent
from live.execution_gateway import ExecutionGateway, Journal
from live.binance_futures_client import BinanceError, BinanceFuturesClient
from config.settings import Settings


@pytest.fixture
def system(tmp_path):
    client = SimpleNamespace(settings=SimpleNamespace(armed=True, mode='testnet'),
        place_entry=AsyncMock(return_value={'status':'NEW', 'executedQty':'0'}),
        protect=AsyncMock(return_value={'algoStatus':'NEW'}),
        cancel=AsyncMock(return_value={'status':'CANCELED', 'executedQty':'0'}),
        order=AsyncMock(return_value={'status':'FILLED', 'executedQty':'1', 'avgPrice':'100'}),
        positions=AsyncMock(return_value=[{'positionAmt':'1'}]),
        reduce_position=AsyncMock(return_value={'status':'FILLED'}),
        cancel_algo=AsyncMock(return_value={}), algo=AsyncMock(return_value={'algoStatus':'NEW'}),
        open_algos=AsyncMock(return_value=[]))
    auth, journal = TokenAuthority(), Journal(tmp_path/'journal.sqlite')
    gateway = ExecutionGateway(client, auth, journal)
    intent = Intent('BTCUSDT','BUY','1','100','99','103',300,'testnet')
    decision = ConsensusEngine().evaluate([Vote(n,'BUY',1,'test') for n in WEIGHTS])
    yield gateway, client, intent, auth.issue(intent, decision)
    journal.close()


async def test_partial_fill_is_protected_once(system):
    gateway, client, intent, token = system
    order_id = await gateway.submit(intent, token)
    event = {'e':'ORDER_TRADE_UPDATE','o':{'s':intent.symbol,'c':order_id,'z':'.2','ap':'100','X':'PARTIALLY_FILLED'}}
    await gateway.on_user_event(event)
    await gateway.on_user_event(event)
    client.protect.assert_awaited_once()
    assert gateway.active[intent.symbol].filled == .2


async def test_unknown_entry_never_resubmits(system):
    gateway, client, intent, token = system
    client.place_entry.side_effect = BinanceError('TIMEOUT', uncertain=True)
    await gateway.submit(intent, token)
    client.place_entry.assert_awaited_once()
    client.order.assert_awaited_once()
    client.protect.assert_awaited_once()
    assert gateway.halted


class _PagedClient(BinanceFuturesClient):
    """Serves deterministic userTrades pages without network access."""

    def __init__(self, pages):
        super().__init__(SimpleNamespace(api_key='k', secret='s', rest='http://x', mode='testnet', armed=False))
        self.pages = list(pages)
        self.calls = []

    async def request(self, method, path, params=None, signed=False, key=False, weight=1):
        self.calls.append(dict(params or {}))
        if '/fapi/v1/userTrades' not in path:
            return []
        if self.calls and 'fromId' in (self.calls[-1] or {}):
            self.calls[-1] = None  # marker consumed
        return self.pages.pop(0) if self.pages else []


async def test_history_since_paginates_past_page_cap():
    page = [{'id': i, 'time': 1000+i, 'commissionAsset': 'USDT'} for i in range(1000)]
    tail = [{'id': 1000+i, 'time': 2000+i, 'commissionAsset': 'USDT'} for i in range(3)]
    client = _PagedClient([list(page), list(tail)])
    trades, complete = await client.history_since('BTCUSDT', 500)
    assert complete and len(trades) == 1003
    assert trades[-1]['id'] == 1002


async def test_history_since_marks_full_final_page_incomplete():
    full = [{'id': i, 'time': 1000+i, 'commissionAsset': 'USDT'} for i in range(1000)]
    # 11 full pages available but max_pages=10: cap reached while the feed is
    # still returning full pages -> the window is truncated (incomplete).
    client = _PagedClient([list(full) for _ in range(11)])
    trades, complete = await client.history_since('BTCUSDT', 500)
    assert not complete and len(trades) == 11000


async def test_history_since_full_page_then_empty_is_complete():
    full = [{'id': i, 'time': 1000+i, 'commissionAsset': 'USDT'} for i in range(1000)]
    client = _PagedClient([list(full), []])
    trades, complete = await client.history_since('BTCUSDT', 500)
    assert complete and len(trades) == 1000


async def test_history_since_short_window_is_complete():
    few = [{'id': i, 'time': 1000+i, 'commissionAsset': 'USDT'} for i in range(7)]
    client = _PagedClient([list(few)])
    trades, complete = await client.history_since('BTCUSDT', 500)
    assert complete and len(trades) == 7 and len(client.calls) == 1



async def test_postonly_rejection_never_becomes_market(system):
    gateway, client, intent, token = system
    client.place_entry.side_effect = BinanceError(-5022)
    assert await gateway.submit(intent, token) == 'REQUOTE'
    client.reduce_position.assert_not_awaited()
    assert not gateway.active


async def test_stop_rejection_cancels_then_flattens(system):
    gateway, client, intent, token = system
    client.place_entry.return_value = {'status':'PARTIALLY_FILLED','executedQty':'.2'}
    client.protect.side_effect = BinanceError(-2021)
    with pytest.raises(RuntimeError, match='Protective stop failure'):
        await gateway.submit(intent, token)
    client.cancel.assert_awaited_once()
    client.reduce_position.assert_awaited_once()
    assert gateway.halted


async def test_demo_stop_needs_position_is_deferred_not_halt(system):
    """-4509 (closePosition stop requires an open position) is transient on the
    demo venue: keep the fill, defer protection, retry via reconcile."""
    gateway, client, intent, token = system
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '1'}
    client.protect.side_effect = BinanceError(-4509)
    await gateway.submit(intent, token)
    assert not gateway.halted
    client.reduce_position.assert_not_awaited()
    assert gateway.active[intent.symbol].stop_id == ''
    client.protect.side_effect = None
    client.protect.return_value = {'algoStatus': 'NEW'}
    await gateway.reconcile()
    client.protect.assert_awaited()
    assert gateway.active[intent.symbol].stop_id


async def test_deliberate_stop_cancel_does_not_halt(system):
    """Exit/ratchet cancels emit ALGO_UPDATE CANCELED for the live stop; that is
    expected bookkeeping, not a protection failure."""
    gateway, client, intent, token = system
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '1'}
    await gateway.submit(intent, token)
    order = gateway.active[intent.symbol]
    event = {'e': 'ALGO_UPDATE', 'o': {'caid': order.stop_id, 'X': 'CANCELED'}}
    await gateway.on_user_event(event)
    assert not gateway.halted
    assert order.stop_id == ''


async def test_algo_rejection_halts_and_flags_reconciliation(system):
    gateway, client, intent, token = system
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '1'}
    await gateway.submit(intent, token)
    order = gateway.active[intent.symbol]
    event = {'e': 'ALGO_UPDATE', 'o': {'caid': order.stop_id, 'X': 'REJECTED'}}
    await gateway.on_user_event(event)
    assert gateway.halted
    assert gateway.alert.startswith('Protective algo inactive')


async def test_ratchet_places_new_before_cancel(system):
    gateway, client, intent, token = system
    client.place_entry.return_value = {'status':'FILLED','executedQty':'1'}
    await gateway.submit(intent, token)
    sequence=[]
    async def protect(*args):
        sequence.append('new')
        return {'algoStatus':'NEW'}
    async def cancel(*args):
        sequence.append('cancel')
    client.protect.side_effect=protect
    client.cancel_algo.side_effect=cancel
    await gateway.ratchet(intent.symbol, 100.1)
    assert sequence == ['new','cancel']
    await gateway.ratchet(intent.symbol, 99)
    assert sequence == ['new','cancel']


async def test_advisory_blocks_before_token_and_http(system):
    gateway, client, intent, token = system
    client.settings.armed=False
    with pytest.raises(PermissionError):
        await gateway.submit(intent, token)
    client.place_entry.assert_not_awaited()
    rest=BinanceFuturesClient(Settings.load('mainnet',env={}))
    with pytest.raises(PermissionError):
        await rest.request('POST','/fapi/v1/order')


async def test_cancel_fill_race_is_protected(system):
    gateway, client, intent, token = system
    await gateway.submit(intent, token)
    client.cancel.return_value = {'status':'CANCELED','executedQty':'.5'}
    await gateway.cancel_entries()
    client.protect.assert_awaited_once()
    assert gateway.active[intent.symbol].filled == .5


async def test_missing_protection_reconciled(system):
    gateway, client, intent, token = system
    client.place_entry.return_value = {'status':'FILLED','executedQty':'1'}
    await gateway.submit(intent, token)
    await gateway.reconcile()
    assert client.protect.await_count == 2


async def test_wrong_environment_and_tampered_token(system):
    gateway, client, intent, token = system
    with pytest.raises(PermissionError):
        await gateway.submit(replace(intent,mode='mainnet'),token)
    with pytest.raises(PermissionError):
        await gateway.submit(replace(intent,quantity='2'),token)
    client.place_entry.assert_not_awaited()
    client.place_entry.assert_not_awaited()


async def test_algo_expiry_after_natural_close_is_benign(system):
    """A closedPosition algo auto-expires once its position closed naturally;
    venue bookkeeping must not halt the gateway while exposure is flat."""
    gateway, client, intent, token = system
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '1'}
    await gateway.submit(intent, token)
    order = gateway.active[intent.symbol]
    client.positions.return_value = []
    event = {'e': 'ALGO_UPDATE', 'o': {'caid': order.stop_id, 'X': 'EXPIRED'}}
    await gateway.on_user_event(event)
    assert not gateway.halted
    assert order.stop_id == ''


async def test_reconcile_clears_protection_halt_once_covered(system):
    """After reconcile re-establishes protection for every tracked fill, a
    protection halt is lifted instead of freezing the mission forever."""
    gateway, client, intent, token = system
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '1'}
    await gateway.submit(intent, token)
    order = gateway.active[intent.symbol]
    event = {'e': 'ALGO_UPDATE', 'o': {'caid': order.stop_id, 'X': 'REJECTED'}}
    await gateway.on_user_event(event)
    assert gateway.halted
    await gateway.reconcile()
    assert not gateway.halted and not gateway.alert


async def test_stale_ratchet_cancel_does_not_halt(system):
    """The acknowledged new stop must survive a failed cancel of an old stop
    the venue already deleted (-2011)."""
    gateway, client, intent, token = system
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '1'}
    await gateway.submit(intent, token)
    client.cancel_algo.side_effect = BinanceError(-2011)
    await gateway.ratchet(intent.symbol, 100.1)
    assert not gateway.halted
    assert gateway.active[intent.symbol].stop_id
