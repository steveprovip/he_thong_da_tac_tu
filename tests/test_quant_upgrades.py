"""Maker-exit engine, volatility gate and lab edge-segmentation upgrades."""
from types import SimpleNamespace
from unittest.mock import AsyncMock
import time
import pytest
from core.consensus_engine import TokenAuthority, ConsensusEngine, Vote, WEIGHTS
from core.models import Intent
from core.runtime import atr_bps_value, exit_reason
from core.signal_lab import SignalLab
from live.execution_gateway import ExecutionGateway, Journal
from live.binance_futures_client import BinanceError


@pytest.fixture
def gateway(tmp_path):
    client = SimpleNamespace(settings=SimpleNamespace(armed=True, mode='testnet'),
        place_entry=AsyncMock(return_value={'status': 'FILLED', 'executedQty': '1'}),
        protect=AsyncMock(return_value={'algoStatus': 'NEW'}),
        cancel=AsyncMock(return_value={'status': 'CANCELED', 'executedQty': '0'}),
        order=AsyncMock(return_value={'status': 'FILLED', 'executedQty': '1', 'avgPrice': '100'}),
        positions=AsyncMock(return_value=[{'positionAmt': '1'}]),
        reduce_position=AsyncMock(return_value={'status': 'FILLED'}),
        reduce_maker=AsyncMock(return_value={'status': 'NEW'}),
        cancel_algo=AsyncMock(return_value={}), open_algos=AsyncMock(return_value=[]))
    auth = TokenAuthority()
    journal = Journal(tmp_path/'journal.sqlite')
    gw = ExecutionGateway(client, auth, journal)
    intent = Intent('BTCUSDT', 'BUY', '1', '100', '99', '103', 300, 'testnet')
    decision = ConsensusEngine().evaluate([Vote(n, 'BUY', 1, 'test') for n in WEIGHTS])
    yield gw, client, intent, auth.issue(intent, decision)
    journal.close()


async def test_maker_exit_parks_passive_reduce_only_order(gateway):
    gw, client, intent, token = gateway
    await gw.submit(intent, token)
    await gw.exit(intent.symbol, maker_peg=100.1, grace=8.0)
    client.reduce_maker.assert_awaited_once()
    args = client.reduce_maker.await_args.args
    assert args[0] == 'BTCUSDT' and args[1] == 'SELL' and args[3] == 100.1
    assert intent.symbol in gw.maker_exits
    assert gw.maker_exits[intent.symbol]['deadline'] > time.time()
    assert gw.maker_exit_stats['placed'] == 1
    client.reduce_position.assert_not_awaited()


async def test_maker_exit_cross_rejection_falls_back_to_market(gateway):
    """A post-only peg that would cross is refused (-5022): the exit must still
    happen, immediately, via the market reduce-only path."""
    gw, client, intent, token = gateway
    await gw.submit(intent, token)
    client.reduce_maker.side_effect = BinanceError(-5022)
    await gw.exit(intent.symbol, maker_peg=99.5, grace=8.0)
    client.reduce_position.assert_awaited_once()
    assert not gw.maker_exits
    assert gw.maker_exit_stats['fallback'] == 1


async def test_maker_exit_grace_expiry_flattens_remainder(gateway):
    gw, client, intent, token = gateway
    await gw.submit(intent, token)
    await gw.exit(intent.symbol, maker_peg=100.1, grace=8.0)
    pending_id = gw.maker_exits[intent.symbol]['client_id']
    client.positions.return_value = [{'positionAmt': '1'}]
    await gw.sweep_maker_exits(time.time()+60, books={})
    assert any(call.args[1] == pending_id for call in client.cancel.await_args_list)
    client.reduce_position.assert_awaited_once()
    assert not gw.maker_exits
    assert gw.maker_exit_stats['fallback'] == 1

async def test_maker_exit_aborts_when_stop_is_crossed(gateway):
    """Grace or not: once price trades back through the stop, the peg is pulled
    and the position is market-flattened. Protection before cost."""
    gw, client, intent, token = gateway
    await gw.submit(intent, token)
    await gw.exit(intent.symbol, maker_peg=100.1, grace=30.0)
    book = SimpleNamespace(mid=98.5)
    await gw.sweep_maker_exits(time.time()+1, books={'BTCUSDT': book})
    assert intent.symbol not in gw.maker_exits
    client.reduce_position.assert_awaited_once()


async def test_maker_exit_fill_is_left_to_reconcile(gateway):
    gw, client, intent, token = gateway
    await gw.submit(intent, token)
    await gw.exit(intent.symbol, maker_peg=100.1, grace=8.0)
    client.positions.return_value = []
    await gw.sweep_maker_exits(time.time()+1)
    assert not gw.maker_exits
    assert gw.maker_exit_stats['filled'] == 1
    client.reduce_position.assert_not_awaited()
    await gw.reconcile()
    assert intent.symbol not in gw.active


async def test_maker_exit_reduceonly_rejection_falls_back_cleanly(gateway):
    """-2022 (ReduceOnly refused) during peg parking means the stop already
    closed the position while the peg was being submitted. Benign race: must
    fall back to the (no-op) flatten path, not raise into the banner."""
    gw, client, intent, token = gateway
    await gw.submit(intent, token)
    client.reduce_maker.side_effect = BinanceError(-2022)
    await gw.exit(intent.symbol, maker_peg=100.1, grace=8.0)
    client.reduce_position.assert_awaited_once()
    assert not gw.maker_exits
    assert gw.maker_exit_stats['fallback'] == 1
    assert not gw.halted


async def test_flatten_treats_reduceonly_rejection_as_already_flat(gateway):
    """-2022 from the market flatten means the position vanished between the
    snapshot and the order (stop or peg won the race). Nothing to reduce --
    continue with closing bookkeeping instead of raising."""
    gw, client, intent, token = gateway
    await gw.submit(intent, token)
    client.reduce_position.side_effect = BinanceError(-2022)
    await gw.exit(intent.symbol)  # taker path
    assert not gw.halted


async def test_sweep_counts_filled_peg_even_when_cancel_raced(gateway):
    """A peg that fills while its own cancel is in flight was booked as a
    fallback, biasing p_fill downward. It must count as filled."""
    gw, client, intent, token = gateway
    await gw.submit(intent, token)
    await gw.exit(intent.symbol, maker_peg=100.1, grace=8.0)
    pending_id = gw.maker_exits[intent.symbol]['client_id']
    client.positions.side_effect = [[{'positionAmt': '1'}], [{'positionAmt': '0'}]]
    await gw.sweep_maker_exits(time.time()+60, books={})
    assert any(call.args[1] == pending_id for call in client.cancel.await_args_list)
    client.reduce_position.assert_not_awaited()
    assert gw.maker_exit_stats['filled'] == 1
    assert gw.maker_exit_stats['fallback'] == 0


async def test_full_shutdown_cancels_parked_maker_exit(gateway):
    gw, client, intent, token = gateway
    await gw.submit(intent, token)
    await gw.exit(intent.symbol, maker_peg=100.1, grace=30.0)
    pending_id = gw.maker_exits[intent.symbol]['client_id']
    await gw.full_shutdown()
    assert any(call.args[1] == pending_id for call in client.cancel.await_args_list)
    assert not gw.maker_exits


def test_exit_reason_classifies_triple_barrier():
    assert exit_reason('BUY', 98.9, 99, 103, 0, 300) == 'STOP'
    assert exit_reason('BUY', 103.5, 99, 103, 0, 300) == 'TP'
    assert exit_reason('BUY', 100.0, 99, 103, 400, 300) == 'DEADLINE'
    assert exit_reason('BUY', 100.0, 99, 103, 100, 300) == ''
    assert exit_reason('SELL', 99.2, 99, 95, 0, 300) == 'STOP'
    assert exit_reason('SELL', 94.8, 99, 95, 0, 300) == 'TP'


def test_atr_bps_value_guards_bad_inputs():
    assert atr_bps_value(None, 100) is None
    assert atr_bps_value(0.01, 0) is None
    assert atr_bps_value(float('nan'), 100) is None
    assert atr_bps_value(0.2, 100) == pytest.approx(20.0)


def test_lab_segments_edge_by_top_alpha(tmp_path):
    lab = SignalLab(path=str(tmp_path/'lab.jsonl'), max_open=4, min_gap=0.001,
                    horizon=100.0, source='test')
    feats = SimpleNamespace(ready=True, atr=0.02, spread_bps=1.0, vpin=0.2, vpin_saturation=False)
    book = SimpleNamespace(bid=99.9, ask=100.1, mid=100.0)
    plan = SimpleNamespace(stop=99.0, take_profit=103.0)
    decision = SimpleNamespace(side='BUY', score=0.8, allowed=True)
    lumen_votes = [SimpleNamespace(agent='LUMEN', side='BUY', confidence=0.9, veto=False),
                   SimpleNamespace(agent='NORO', side='BUY', confidence=0.4, veto=False)]
    noro_votes = [SimpleNamespace(agent='NORO', side='BUY', confidence=0.9, veto=False)]
    gate_votes = [SimpleNamespace(agent='RUNE', side='HOLD', confidence=0.0, veto=False)]
    now = 1000.0
    assert lab.observe('BTCUSDT', feats, book, decision, lumen_votes, plan, now, 0.8).top_alpha == 'LUMEN'
    assert lab.observe('ETHUSDT', feats, book, decision, noro_votes, plan, now, 0.8).top_alpha == 'NORO'
    assert lab.observe('XRPUSDT', feats, book, decision, gate_votes, plan, now, 0.8).top_alpha == ''
    lab.resolve('BTCUSDT', SimpleNamespace(bid=103.2, ask=103.3, mid=103.2), now+50)
    sl_book = SimpleNamespace(bid=98.5, ask=98.7, mid=98.6)
    lab.resolve('ETHUSDT', sl_book, now+50)
    lab.resolve('XRPUSDT', sl_book, now+50)
    by_alpha = lab.summary()['by_alpha']
    assert by_alpha['LUMEN']['n'] == 1 and by_alpha['LUMEN']['tp_rate'] == 1.0
    assert by_alpha['NORO']['n'] == 1 and by_alpha['NORO']['tp_rate'] == 0.0
    assert by_alpha['mixed']['n'] == 1
    assert by_alpha['LUMEN']['net_bps'] > by_alpha['NORO']['net_bps']