"""Fault injection: venue and transport failures must never leave naked exposure.

Four failure families are exercised here, each against the real gateway/runtime
state machines rather than mocks of our own logic:

  A. transport loss  - stream drops, reconnects, listenKey expiry, depth gaps
  B. partial fills   - protection must cover exactly the residual position
  C. stop rejection  - an unprotectable fill is flattened immediately
  D. ratchet races   - the replacement stop is live before the old one dies
"""
import asyncio
import json
import time
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

import core.runtime as runtime_module
import live.futures_streams as streams_module
from config.settings import Settings
from core.consensus_engine import ConsensusEngine, TokenAuthority, Vote, WEIGHTS
from core.models import Book, Intent
from core.runtime import Runtime, arguments
from core.web_ui_server import Telemetry
from live.binance_futures_client import BinanceError
from live.execution_gateway import ExecutionGateway, Journal
from live.futures_streams import consume_stream

SYMBOL = 'BTCUSDT'


def fake_client(**overrides):
    """A venue that behaves; individual tests break exactly one behaviour."""
    client = SimpleNamespace(
        settings=SimpleNamespace(armed=True, mode='testnet'),
        place_entry=AsyncMock(return_value={'status': 'NEW', 'executedQty': '0'}),
        protect=AsyncMock(return_value={'algoStatus': 'NEW'}),
        cancel=AsyncMock(return_value={'status': 'CANCELED', 'executedQty': '0'}),
        order=AsyncMock(return_value={'status': 'FILLED', 'executedQty': '0.010', 'avgPrice': '68000'}),
        positions=AsyncMock(return_value=[{'positionAmt': '0.003'}]),
        reduce_position=AsyncMock(return_value={'status': 'FILLED'}),
        cancel_algo=AsyncMock(return_value={}),
        algo=AsyncMock(return_value={'algoStatus': 'WORKING'}),
        open_algos=AsyncMock(return_value=[]),
        offset_ms=0)
    for key, value in overrides.items():
        setattr(client, key, value)
    return client


@pytest.fixture
def venue(tmp_path):
    client = fake_client()
    authority, journal = TokenAuthority(), Journal(tmp_path / 'fault-journal.sqlite')
    gateway = ExecutionGateway(client, authority, journal)
    intent = Intent(SYMBOL, 'BUY', '0.010', '68000', '67800', '68400', 300, 'testnet')
    decision = ConsensusEngine().evaluate([Vote(name, 'BUY', 1, 'unit') for name in WEIGHTS])
    yield gateway, client, intent, authority.issue(intent, decision)
    journal.close()


@pytest.fixture
def engine(monkeypatch, tmp_path):
    monkeypatch.setattr(runtime_module, 'ROOT', tmp_path)
    settings = Settings.load('testnet', env={})
    args = arguments('testnet', ['--seconds', '1', '--symbols', SYMBOL, '--no-ui'])
    rt = Runtime(settings, args, Telemetry())
    rt.client = SimpleNamespace(offset_ms=0)
    return rt


class FakeSocket:
    """Yields scripted frames, then closes the way a dropped socket does."""
    def __init__(self, frames):
        self.frames = list(frames)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def recv(self):
        if self.frames:
            return self.frames.pop(0)
        raise ConnectionError('socket closed by peer')


# --------------------------------------------------------------------------- A
async def test_public_disconnect_clears_books_and_cancels_resting_entries(engine):
    engine.gateway = SimpleNamespace(cancel_entries=AsyncMock())
    engine.books[SYMBOL] = Book(SYMBOL, ((68000., 3.),), ((68001., 3.),), 0., 7)
    engine.tracker.last[SYMBOL] = 7
    engine.alpha[SYMBOL].cvd = 42.0
    await engine.disconnected('public')
    assert engine.books == {} and engine.tracker.last == {}
    assert engine.alpha[SYMBOL].cvd == 0 and engine.alpha[SYMBOL].book is None
    engine.gateway.cancel_entries.assert_awaited_once()
    assert 'disconnected' in engine.telemetry.snapshot()['events'][0]['message']


async def test_private_disconnect_pauses_entries_until_reconciled(engine):
    engine.gateway = SimpleNamespace(cancel_entries=AsyncMock(), reconcile=AsyncMock())
    engine.private_ready = True
    engine.books[SYMBOL] = Book(SYMBOL, ((68000., 3.),), ((68001., 3.),), 0., 7)
    await engine.disconnected('private')
    assert not engine.private_ready
    # Market state survives a user-stream drop; only execution is paused.
    assert SYMBOL in engine.books
    engine.gateway.reconcile.assert_not_awaited()
    await engine.event({'e': '_CONNECTED', 'category': 'private'})
    engine.gateway.reconcile.assert_awaited_once()
    assert engine.private_ready


async def test_disconnect_cancels_entries_but_never_the_exchange_stop(engine, venue):
    gateway, client, intent, token = venue
    client.place_entry.return_value = {'status': 'PARTIALLY_FILLED', 'executedQty': '0.003'}
    await gateway.submit(intent, token)
    stop_id = gateway.active[SYMBOL].stop_id
    assert stop_id
    engine.gateway = gateway
    await engine.disconnected('public')
    client.cancel.assert_awaited_once()
    client.cancel_algo.assert_not_awaited()
    assert gateway.active[SYMBOL].stop_id == stop_id
    assert not gateway.halted


async def test_stream_outage_reconnects_and_resubscribes(monkeypatch):
    attempts, disconnects, received = [], [], []
    stop = asyncio.Event()

    def connect(url, **kwargs):
        attempts.append(url)
        if len(attempts) == 1:
            raise OSError('transport lost for 5s')
        return FakeSocket([json.dumps({'stream': 's', 'data': {'e': 'depthUpdate', 's': SYMBOL}})])

    monkeypatch.setattr(streams_module.websockets, 'connect', connect)

    async def callback(event):
        received.append(event['e'])
        if event['e'] == 'depthUpdate':
            stop.set()

    async def on_disconnect(label):
        disconnects.append(label)

    await asyncio.wait_for(consume_stream('wss://x/public', callback, on_disconnect, stop, 'public'), 10)
    assert disconnects == ['public']
    assert len(attempts) == 2 and attempts[0] == attempts[1]
    # Reconnect announces itself so the runtime can re-warm and reconcile.
    assert received == ['_CONNECTED', 'depthUpdate']


async def test_listen_key_expiry_forces_private_reconnect(monkeypatch):
    disconnects = []
    stop = asyncio.Event()
    monkeypatch.setattr(streams_module.websockets, 'connect',
                        lambda url, **kw: FakeSocket([json.dumps({'e': 'listenKeyExpired'})]))

    async def on_disconnect(label):
        disconnects.append(label)
        stop.set()

    await asyncio.wait_for(consume_stream('wss://x/private/ws/k', AsyncMock(), on_disconnect, stop, 'private'), 10)
    assert disconnects == ['private']


async def test_depth_sequence_gap_restarts_flow_warmup(engine):
    engine.alpha[SYMBOL].cvd = 13.0
    frame = {'e': 'depthUpdate', 's': SYMBOL, 'u': 10, 'pu': 9, 'E': 0,
             'b': [['68000', '2']], 'a': [['68001', '2']]}

    async def feed(update):
        payload = dict(frame, **update)
        payload['E'] = payload['T'] = int(time.time() * 1000)
        await engine.event(payload)

    await feed({'u': 10, 'pu': 9})
    assert SYMBOL in engine.books
    await feed({'u': 20, 'pu': 15, 'b': [['67990', '4']]})
    assert engine.alpha[SYMBOL].cvd == 0
    assert any('gap' in e['message'] for e in engine.telemetry.snapshot()['events'])
    # The replacement top-of-book is still adopted, so pricing resumes at once.
    assert engine.books[SYMBOL].bid == 67990.


# --------------------------------------------------------------------------- B
async def test_partial_fill_is_protected_for_the_residual_only(venue):
    gateway, client, intent, token = venue
    client.place_entry.return_value = {'status': 'PARTIALLY_FILLED', 'executedQty': '0.003'}
    await gateway.submit(intent, token)
    order = gateway.active[SYMBOL]
    assert order.filled == 0.003
    client.protect.assert_awaited_once()
    symbol, side, stop, _ = client.protect.await_args.args
    # closePosition stops track the live position, so 0.007 cancelled qty is never covered.
    assert (symbol, side, stop) == (SYMBOL, 'SELL', float(intent.stop))
    client.reduce_position.assert_not_awaited()
    assert not gateway.halted


async def test_quote_ttl_cancel_keeps_partial_fill_protected(venue):
    gateway, client, intent, token = venue
    client.place_entry.return_value = {'status': 'PARTIALLY_FILLED', 'executedQty': '0.003'}
    await gateway.submit(intent, token)
    stop_id = gateway.active[SYMBOL].stop_id
    client.cancel.return_value = {'status': 'CANCELED', 'executedQty': '0.003'}
    await gateway.cancel_entries()
    order = gateway.active[SYMBOL]
    assert order.filled == 0.003 and order.stop_id == stop_id
    assert client.protect.await_count == 1
    client.cancel_algo.assert_not_awaited()


# --------------------------------------------------------------------------- C
async def test_stop_rejection_after_fill_flattens_immediately(venue):
    gateway, client, intent, token = venue
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '0.010', 'avgPrice': '68000'}
    await gateway.submit(intent, token)
    client.protect.side_effect = BinanceError(-2021)
    with pytest.raises(RuntimeError, match='Protective stop failure'):
        await gateway.ratchet(SYMBOL, 67900.0)
    client.reduce_position.assert_awaited_once()
    symbol, side, quantity, _ = client.reduce_position.await_args.args
    assert (symbol, side, str(quantity)) == (SYMBOL, 'SELL', '0.003')
    assert gateway.halted and 'flatten' in gateway.alert


async def test_stop_acknowledged_but_not_working_is_a_failure(venue):
    gateway, client, intent, token = venue
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '0.010', 'avgPrice': '68000'}
    await gateway.submit(intent, token)
    client.protect.return_value = {'algoStatus': 'PENDING'}
    with pytest.raises(RuntimeError):
        await gateway.ratchet(SYMBOL, 67900.0)
    client.reduce_position.assert_awaited_once()
    assert gateway.halted


async def test_uncertain_stop_is_resolved_by_lookup_not_resubmitted(venue):
    gateway, client, intent, token = venue
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '0.010', 'avgPrice': '68000'}
    client.protect.side_effect = BinanceError('TIMEOUT', uncertain=True)
    await gateway.submit(intent, token)
    client.protect.assert_awaited_once()
    client.algo.assert_awaited_once()
    assert gateway.active[SYMBOL].stop_id and not gateway.halted
    client.reduce_position.assert_not_awaited()


async def test_algo_rejection_event_halts_the_gateway(venue):
    gateway, client, intent, token = venue
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '0.010', 'avgPrice': '68000'}
    await gateway.submit(intent, token)
    stop_id = gateway.active[SYMBOL].stop_id
    await gateway.on_user_event({'e': 'ALGO_UPDATE', 'o': {'X': 'REJECTED', 'caid': stop_id}})
    assert gateway.halted and 'reconciliation' in gateway.alert


# --------------------------------------------------------------------------- D
async def test_failed_ratchet_leaves_the_previous_stop_uncancelled(venue):
    gateway, client, intent, token = venue
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '0.010', 'avgPrice': '68000'}
    await gateway.submit(intent, token)
    original = gateway.active[SYMBOL].stop_id
    client.protect.side_effect = BinanceError(-2021)
    with pytest.raises(RuntimeError):
        await gateway.ratchet(SYMBOL, 67900.0)
    client.cancel_algo.assert_not_awaited()
    assert gateway.active[SYMBOL].stop_id == original
    assert gateway.active[SYMBOL].current_stop == float(intent.stop)


async def test_ratchet_sequence_is_place_confirm_then_cancel(venue):
    gateway, client, intent, token = venue
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '0.010', 'avgPrice': '68000'}
    await gateway.submit(intent, token)
    trace = []

    async def protect(*args):
        trace.append(('place', args[2]))
        return {'algoStatus': 'WORKING'}

    async def cancel_algo(*args):
        trace.append(('cancel', args[0]))

    client.protect.side_effect = protect
    client.cancel_algo.side_effect = cancel_algo
    first = gateway.active[SYMBOL].stop_id
    await gateway.ratchet(SYMBOL, 67900.0)
    assert [step for step, _ in trace] == ['place', 'cancel']
    assert trace[1][1] == first and trace[0][1] == 67900.0
    assert gateway.active[SYMBOL].current_stop == 67900.0


async def test_ratchet_refuses_to_loosen_protection(venue):
    gateway, client, intent, token = venue
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '0.010', 'avgPrice': '68000'}
    await gateway.submit(intent, token)
    client.protect.reset_mock()
    await gateway.ratchet(SYMBOL, 67700.0)
    client.protect.assert_not_awaited()
    await gateway.ratchet(SYMBOL, float(intent.stop))
    client.protect.assert_not_awaited()
    assert gateway.active[SYMBOL].current_stop == float(intent.stop)


async def test_unfilled_and_closing_orders_are_never_ratcheted(venue):
    gateway, client, intent, token = venue
    await gateway.submit(intent, token)
    client.protect.reset_mock()
    await gateway.ratchet(SYMBOL, 67900.0)
    client.protect.assert_not_awaited()
    gateway.active[SYMBOL].filled = 0.003
    gateway.active[SYMBOL].closing = True
    await gateway.ratchet(SYMBOL, 67900.0)
    client.protect.assert_not_awaited()


async def test_short_side_ratchet_only_tightens_downward(venue, tmp_path):
    gateway, client, _, _ = venue
    short = Intent(SYMBOL, 'SELL', '0.010', '68000', '68200', '67600', 300, 'testnet')
    decision = ConsensusEngine().evaluate([Vote(name, 'SELL', 1, 'unit') for name in WEIGHTS])
    token = gateway.authority.issue(short, decision)
    client.place_entry.return_value = {'status': 'FILLED', 'executedQty': '0.010', 'avgPrice': '68000'}
    await gateway.submit(short, token)
    client.protect.reset_mock()
    await gateway.ratchet(SYMBOL, 68300.0)
    client.protect.assert_not_awaited()
    await gateway.ratchet(SYMBOL, 68100.0)
    client.protect.assert_awaited_once()
    assert client.protect.await_args.args[1] == 'BUY'
    assert gateway.active[SYMBOL].current_stop == 68100.0


async def test_tampered_or_replayed_token_never_reaches_the_venue(venue):
    gateway, client, intent, token = venue
    await gateway.submit(intent, token)
    gateway.active.clear()
    with pytest.raises(PermissionError):
        await gateway.submit(intent, token)
    with pytest.raises(PermissionError):
        await gateway.submit(replace(intent, stop='67000'), token)
    assert client.place_entry.await_count == 1


# ------------------------------------------------------------------- mainnet
async def test_unarmed_mainnet_never_builds_a_gateway_or_journal(monkeypatch, tmp_path):
    """Advisory mainnet must reach the end of a session having placed nothing."""
    monkeypatch.setattr(runtime_module, 'ROOT', tmp_path)
    settings = Settings.load('mainnet', env={})
    assert not settings.armed
    rt = Runtime(settings, arguments('mainnet', ['--seconds', '1', '--symbols', SYMBOL, '--no-ui']), Telemetry())
    forbidden = AsyncMock(side_effect=AssertionError('advisory mainnet reached the venue'))
    monkeypatch.setattr(runtime_module.BinanceFuturesClient, 'request', forbidden)
    monkeypatch.setattr(Runtime, 'initialize', AsyncMock())
    monkeypatch.setattr(runtime_module, 'consume_stream', AsyncMock())
    await rt.run()
    assert rt.gateway is None
    assert not list(tmp_path.rglob('*.sqlite'))
    forbidden.assert_not_awaited()
