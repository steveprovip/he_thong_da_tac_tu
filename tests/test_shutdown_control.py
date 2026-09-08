"""Kill switch: dashboard shutdown endpoint, venue flatten, gateway full stop."""
import http.client
import json
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from core.consensus_engine import ConsensusEngine, TokenAuthority, Vote, WEIGHTS
from core.models import Intent
from core.runtime import console_stop_requested
from core.web_ui_server import Telemetry, start_server
from live.binance_futures_client import BinanceError
from live.execution_gateway import ExecutionGateway, Journal
from live.flatten import flatten_venue


def test_console_stop_requested_by_sigint_event():
    event = threading.Event()
    assert not console_stop_requested(event, keyboard=lambda: 'x')
    event.set()
    assert console_stop_requested(event, keyboard=lambda: 'x')


def test_console_stop_requested_by_q_hotkey_only():
    assert console_stop_requested(keyboard=lambda: 'q')
    assert console_stop_requested(keyboard=lambda: 'Q')
    assert not console_stop_requested(keyboard=lambda: 'x')
    # Stray typing never stops the system.
    assert not console_stop_requested(keyboard=lambda: '1')


def test_console_stop_requested_without_tty_or_windows():
    """No msvcrt (non-Windows) and no SIGINT event -> never stops."""
    assert not console_stop_requested(None, keyboard=None)


def test_mission_mode_flag_and_summary():
    """15-minute data mission: --seconds 900 --flatten-on-exit flattens the venue
    on exit and mission_summary exposes exactly what the learning loop needs."""
    from config.settings import Settings
    from core.position_sizer import PositionSizer
    from core.runtime import Runtime, arguments
    args = arguments('testnet', ['--bootstrap', '--seconds', '900', '--flatten-on-exit'])
    assert args.seconds == 900 and args.flatten_on_exit
    assert arguments('testnet', ['--bootstrap']).flatten_on_exit is False
    rt = Runtime(Settings.load('testnet'), args, Telemetry())
    report = rt.mission_summary()
    for key in ('mode', 'planned_seconds', 'settled_outcomes', 'kelly_target',
                'kelly_ready', 'session_pnl_usdt', 'session_fees_usdt',
                'authorized_entries', 'lab'):
        assert key in report
    assert report['planned_seconds'] == 900
    # Kelly needs 60 settled outcomes, not 20: at n=20 the ledger's SE is
    # 3.5 bps against a 15.7 bps spread, so the estimate is noise.
    assert report['kelly_target'] == PositionSizer().min_samples == 60
    assert report['kelly_stop_target'] == 120
    # Verdict moves with the live ledger: WARMUP while n<60, UNDETERMINED
    # once n>=60 with a CI still crossing zero (the demo ledger crossed 60
    # on 09/09 and this test broke on the pinned value, not on behaviour).
    # Pin the wiring -- the report must expose exactly what the runtime's
    # own sizer computes -- not the mutable value itself.
    assert report['edge_verdict'] == rt.sizer.edge_verdict()
    assert isinstance(report['kelly_ready'], bool)


def test_mission_target_stops_early_with_progress():
    """Mission target counts NEW settled outcomes; progress shows in the status bar."""
    from core.runtime import Runtime, arguments
    assert arguments('testnet', ['--bootstrap', '--mission-target', '8']).mission_target == 8
    assert arguments('testnet', ['--bootstrap']).mission_target == 0
    with pytest.raises(SystemExit):
        arguments('testnet', ['--bootstrap', '--mission-target', '-1'])
    from config.settings import Settings
    rt = Runtime(Settings.load('testnet'),
                 arguments('testnet', ['--bootstrap', '--mission-target', '8', '--flatten-on-exit']),
                 Telemetry())
    report = rt.mission_summary()
    assert report['mission_target'] == 8
    assert report['collected_this_session'] == 0  # nothing settled yet at construction


@pytest.fixture
def dashboard():
    telemetry = Telemetry()
    server = start_server(telemetry, '127.0.0.1', 0)
    port = server.server_address[1]
    yield telemetry, port
    server.shutdown()
    server.server_close()


def request(port, method, path, body=None, host_header='127.0.0.1'):
    conn = http.client.HTTPConnection('127.0.0.1', port, timeout=5)
    headers = {'Host': host_header}
    payload = json.dumps(body).encode() if body is not None else None
    if payload:
        headers['Content-Type'] = 'application/json'
    conn.request(method, path, payload, headers)
    response = conn.getresponse()
    data = response.read()
    conn.close()
    try:
        body = json.loads(data) if data else None
    except ValueError:
        body = data.decode(errors='replace')
    return response.status, body


async def test_state_exposes_shutdown_flag(dashboard):
    telemetry, port = dashboard
    status, state = request(port, 'GET', '/api/state')
    assert status == 200
    assert state['shutdown'] is False


async def test_shutdown_requires_confirm_token(dashboard):
    telemetry, port = dashboard
    status, _ = request(port, 'POST', '/api/shutdown', {})
    assert status == 400
    assert telemetry.snapshot()['shutdown'] is False


async def test_shutdown_confirm_sets_flag(dashboard):
    telemetry, port = dashboard
    status, body = request(port, 'POST', '/api/shutdown', {'confirm': 'SHUTDOWN'})
    assert status == 200 and body['ok'] is True
    snapshot = telemetry.snapshot()
    assert snapshot['shutdown'] is True
    assert any('KILL SWITCH' in e['message'] for e in snapshot['events'])


async def test_foreign_host_header_is_forbidden(dashboard):
    _, port = dashboard
    status, _ = request(port, 'POST', '/api/shutdown', {'confirm': 'SHUTDOWN'}, host_header='evil.example')
    assert status == 403


async def test_unknown_post_path_is_404(dashboard):
    _, port = dashboard
    status, _ = request(port, 'POST', '/api/exec', {'confirm': 'SHUTDOWN'})
    assert status == 404


class FakeVenue:
    def __init__(self):
        self.calls = []
        self.positions_book = {'BTCUSDT': [{'symbol': 'BTCUSDT', 'positionAmt': '2.5'}],
                               'ETHUSDT': [{'symbol': 'ETHUSDT', 'positionAmt': '0'}]}
        self.algo_error = None

    async def positions(self, symbol=None):
        self.calls.append(('positions', symbol))
        if symbol is None:
            return [p for book in self.positions_book.values() for p in book]
        return self.positions_book.get(symbol, [])

    async def open_orders(self, symbol):
        self.calls.append(('open_orders', symbol))
        return [{'origClientOrderId': 'gt-1'}]

    async def cancel(self, symbol, client_id):
        self.calls.append(('cancel', symbol, client_id))
        return {'status': 'CANCELED'}

    async def open_algos(self, symbol):
        self.calls.append(('open_algos', symbol))
        return [{'clientAlgoId': 'gs-1'}]

    async def cancel_algo(self, client_id):
        self.calls.append(('cancel_algo', client_id))
        if self.algo_error:
            raise self.algo_error
        return {}

    async def reduce_position(self, symbol, side, quantity, client_id):
        self.calls.append(('reduce', symbol, side, quantity, client_id))
        return {'status': 'FILLED'}


async def test_flatten_venue_closes_only_nonzero_positions():
    venue = FakeVenue()
    report = await flatten_venue(venue, symbols=('BTCUSDT', 'ETHUSDT'))
    assert report == [{'symbol': 'BTCUSDT', 'quantity': 2.5, 'client_id': report[0]['client_id']}]
    assert report[0]['client_id'].startswith('gf-')
    calls = venue.calls
    assert ('cancel', 'BTCUSDT', 'gt-1') in calls
    assert ('reduce', 'BTCUSDT', 'SELL', 2.5, report[0]['client_id']) in calls
    assert not any(c[0] == 'reduce' and c[1] == 'ETHUSDT' for c in calls)
    assert ('cancel_algo', 'gs-1') in calls


async def test_flatten_venue_discovers_symbols_from_positions():
    venue = FakeVenue()
    report = await flatten_venue(venue)
    # Only the symbol actually holding a position is swept.
    symbols = {call[1] for call in venue.calls if call[0] == 'positions' and call[1]}
    assert symbols == {'BTCUSDT'}
    assert len(report) == 1


async def test_flatten_venue_tolerates_known_cancel_codes_only():
    venue = FakeVenue()
    venue.algo_error = BinanceError(-2011)
    await flatten_venue(venue, symbols=('BTCUSDT',))
    venue.algo_error = BinanceError('TRANSPORT', uncertain=True)
    with pytest.raises(BinanceError):
        await flatten_venue(venue, symbols=('BTCUSDT',))


async def test_gateway_full_shutdown_flattens_and_unprotects(tmp_path):
    client = SimpleNamespace(settings=SimpleNamespace(armed=True, mode='testnet'),
        place_entry=AsyncMock(return_value={'status': 'FILLED', 'executedQty': '1', 'avgPrice': '100'}),
        protect=AsyncMock(return_value={'algoStatus': 'NEW'}),
        cancel=AsyncMock(return_value={'status': 'CANCELED', 'executedQty': '0'}),
        order=AsyncMock(return_value={'status': 'FILLED', 'executedQty': '1', 'avgPrice': '100'}),
        positions=AsyncMock(return_value=[{'positionAmt': '1'}]),
        reduce_position=AsyncMock(return_value={'status': 'FILLED'}),
        cancel_algo=AsyncMock(return_value={}), algo=AsyncMock(return_value={'algoStatus': 'NEW'}),
        open_algos=AsyncMock(return_value=[]))
    auth, journal = TokenAuthority(), Journal(tmp_path / 'journal.sqlite')
    gateway = ExecutionGateway(client, auth, journal)
    intent = Intent('BTCUSDT', 'BUY', '1', '100', '99', '103', 300, 'testnet')
    decision = ConsensusEngine().evaluate([Vote(n, 'BUY', 1, 'test') for n in WEIGHTS])
    await gateway.submit(intent, auth.issue(intent, decision))
    await gateway.full_shutdown()
    client.reduce_position.assert_awaited_once()
    client.cancel_algo.assert_awaited_once()
    assert not gateway.active
    journal.close()