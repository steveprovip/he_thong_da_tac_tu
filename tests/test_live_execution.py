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
