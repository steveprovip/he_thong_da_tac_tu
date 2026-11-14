"""2026-11-13 liquidation tape (tools/record_market.py --liq): all-market !forceOrder@arr with the symbol's latest touch
attached, bookTicker + aggTrade per symbol, every write on a writer thread. Mock sockets only: no network here."""
import asyncio
import gzip
import json
import sys
import threading
import types
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools'))
import record_market as rm  # noqa: E402

PUB, MKT = rm.WS_ROUTES['public'][0], rm.WS_ROUTES['market'][0]
BOOK = {'e': 'bookTicker', 'u': 400900217, 'E': 1794297600102, 'T': 1794297600094, 's': 'DOGEUSDT',
        'b': '0.07004', 'B': '31021', 'a': '0.07005', 'A': '40066'}
TRADE = {'e': 'aggTrade', 'E': 1794297600285, 'a': 1043658398, 's': 'DOGEUSDT', 'p': '0.070040', 'q': '15376',
         'f': 3533003558, 'l': 3533003563, 'T': 1794297600139, 'm': True}
LIQ = {'e': 'forceOrder', 'E': 1794297601000, 'o': {'s': 'DOGEUSDT', 'S': 'SELL', 'o': 'LIMIT', 'f': 'IOC',
                                                    'q': '150000', 'p': '0.06990', 'ap': '0.06995', 'X': 'FILLED',
                                                    'l': '150000', 'z': '150000', 'T': 1794297600998}}


def frame(stream, data):
    return json.dumps({'stream': stream, 'data': data}, separators=(',', ':'))


class Capture:
    def __init__(self):
        self.items = []

    def put(self, dest, sym, ns, line):
        self.items.append((dest, sym, ns, line))


# ============================================================================= stream sets
def test_liq_mode_subscribes_the_touch_the_prints_and_every_liquidation():
    pub = rm.stream_url(['DOGEUSDT', 'XRPUSDT'], 'public', liq=True)
    mkt = rm.stream_url(['DOGEUSDT', 'XRPUSDT'], 'market', liq=True)
    assert pub == PUB + 'dogeusdt@bookTicker/xrpusdt@bookTicker'
    assert mkt == MKT + 'dogeusdt@aggTrade/xrpusdt@aggTrade/!forceOrder@arr'
    assert 'depth' not in pub + mkt and '@forceOrder' not in mkt.replace('!forceOrder@arr', '')
    assert rm.stream_url([], 'public', liq=True) == PUB                   # empty: run() opens no socket for it
    assert rm.stream_url([], 'market', liq=True) == MKT + '!forceOrder@arr'
    assert rm.stream_url(['DOGEUSDT'], 'public') == PUB + 'dogeusdt@depth@100ms/dogeusdt@bookTicker'   # default unchanged


def test_market_streams_use_the_split_route_not_the_retired_ws_root():
    assert MKT.startswith('wss://fstream.binance.com/market/') and '/ws/' not in MKT
    from research import fast_liquidation_sniper as fls
    assert fls.WS_FORCE_ORDER.startswith('wss://fstream.binance.com/market/')


# ============================================================================= hot path
def test_split_frame_slices_without_parsing_and_refuses_other_layouts():
    raw = frame('dogeusdt@aggTrade', TRADE)
    stream, data = rm.split_frame(raw)
    assert stream == 'dogeusdt@aggTrade' and json.loads(data) == TRADE
    assert rm.split_frame('{"result":null,"id":1}') is None
    assert rm.split_frame('{"stream": "dogeusdt@aggTrade", "data": {}}') is None      # spaces: slow path
    assert rm.split_frame('{"stream":"x@y","data":1}') is None


@pytest.mark.parametrize('stream, data', [('dogeusdt@aggTrade', TRADE), ('dogeusdt@bookTicker', BOOK),
                                          ('dogeusdt@depth@100ms', {'e': 'depthUpdate', 'U': 1, 'u': 2, 'pu': 0,
                                                                    'b': [['0.07', '10']], 'a': []})])
def test_router_writes_byte_identical_lines_to_the_old_recorder(stream, data):
    raw, cap = frame(stream, data), Capture()
    rm.Router(cap)(raw, 123)
    sym, rec = rm.record_line(raw, 123)
    assert cap.items == [('h', sym, 123, json.dumps(rec, separators=(',', ':')) + '\n')]


def test_a_liquidation_carries_the_latest_touch_or_unknown():
    cap = Capture()
    r = rm.Router(cap)
    r(frame('dogeusdt@bookTicker', BOOK), 1_000_000_000)
    r(frame('!forceOrder@arr', LIQ), 1_005_000_000)
    r(frame('!forceOrder@arr', {**LIQ, 'o': {**LIQ['o'], 's': 'BTCUSDT'}}), 1_006_000_000)
    liq = [x for x in cap.items if x[0] == 'liq']
    assert [x[1] for x in liq] == ['DOGEUSDT', 'BTCUSDT']
    row = json.loads(liq[0][3])
    assert row['r'] == 1_005_000_000 and row['s'] == 'forceOrder' and row['d'] == LIQ
    assert row['book'] == {'b': '0.07004', 'B': '31021', 'a': '0.07005', 'A': '40066', 'u': 400900217,
                           'T': 1794297600094, 'age_ms': 5.0}
    assert json.loads(liq[1][3])['book'] is None                         # not subscribed: UNKNOWN, not zero
    assert r.counts == {'bookTicker': 1, 'forceOrder': 2}


def test_unusual_layout_takes_the_slow_path_and_bad_liquidations_are_counted():
    cap = Capture()
    r = rm.Router(cap)
    r('{"stream": "dogeusdt@aggTrade", "data": {"p": "0.1"}}', 7)
    assert cap.items == [('h', 'DOGEUSDT', 7, '{"r":7,"s":"aggTrade","d":{"p":"0.1"}}\n')]
    r(frame('!forceOrder@arr', {'e': 'forceOrder'}), 8)                   # no 'o': cannot name the symbol
    r('{bad', 9)
    r('{"result":null,"id":1}', 10)
    assert len(cap.items) == 1 and r.counts['bad_liq'] == 1


def test_loop_cost_is_microseconds():
    class Null:
        def put(self, *a):
            pass

    r = rm.Router(Null())
    frames = [frame('dogeusdt@bookTicker', BOOK), frame('dogeusdt@aggTrade', TRADE)] * 10_000
    for i, f in enumerate(frames):
        r(f, i)
    hot = sorted(r.hot_ns)
    assert len(hot) == 20_000 and hot[len(hot) // 2] < 50_000            # p50 < 50 us (measured ~2 us)
    for i in range(500):
        r(frame('!forceOrder@arr', LIQ), 10 ** 9 + i)
    assert sorted(list(r.hot_ns)[-500:])[250] < 500_000                  # a liquidation is parsed: p50 < 0.5 ms


# ============================================================================= writers
def test_daily_writer_rotates_by_utc_day_and_flushes_each_line(tmp_path):
    w = rm.DailyWriter(tmp_path)
    d1 = 1_794_297_600 * 10 ** 9                                         # 2026-11-10 08:00 UTC
    w.write_line('DOGEUSDT', d1 + 1, '{"a":1}\n')
    p1 = tmp_path / 'liquidations_20261110.jsonl'
    assert p1.read_text(encoding='utf-8') == '{"a":1}\n'                 # readable before close: line-buffered
    w.write_line('XRPUSDT', d1 + 86_400 * 10 ** 9, '{"a":2}\n')
    w.close()
    assert (tmp_path / 'liquidations_20261111.jsonl').read_text(encoding='utf-8') == '{"a":2}\n'
    assert w.counts == {'DOGEUSDT': 1, 'XRPUSDT': 1}


def test_threaded_sink_writes_off_the_loop_thread_and_survives_a_bad_write(tmp_path):
    seen = []

    class Spy(rm.DailyWriter):
        def write_line(self, sym, ns, line):
            seen.append(threading.current_thread().name)
            if sym == 'BAD':
                raise OSError('disk')
            super().write_line(sym, ns, line)

    t0 = 1_794_297_600 * 10 ** 9
    sink = rm.ThreadedSink(rm.HourlyWriter(tmp_path), Spy(tmp_path))
    sink.put('liq', 'DOGEUSDT', t0, '{"x":1}\n')
    sink.put('liq', 'BAD', t0, '{"x":2}\n')
    sink.put('h', 'DOGEUSDT', t0, '{"y":1}\n')
    sink.close()
    assert seen == ['recorder-writer', 'recorder-writer'] and sink.errors == 1
    assert (tmp_path / 'liquidations_20261110.jsonl').read_text(encoding='utf-8') == '{"x":1}\n'
    assert gzip.open(tmp_path / 'DOGEUSDT' / f'{rm.hour_key(t0)}.jsonl.gz', 'rt').read() == '{"y":1}\n'   # 20261110-08
    assert len(sink.lag_ns) == 3


# ============================================================================= mock sockets
def fake_websockets(script, urls, stop=None):
    """websockets stand-in: each connect() pops the next item of `script` (an exception to raise, or a list of
    frames to serve); when the frames run out it sets `stop`."""

    class WS:
        def __init__(self, frames):
            self.frames = list(frames)

        async def recv(self):
            if self.frames:
                return self.frames.pop(0)
            if stop is not None:
                stop.set()
            await asyncio.sleep(0.05)
            raise asyncio.TimeoutError

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

    def connect(url, **kw):
        urls.append(url)
        item = script.pop(0) if script else []
        if isinstance(item, Exception):
            raise item
        return WS(item)

    return types.SimpleNamespace(connect=connect)


def test_stream_loop_reconnects_after_a_dropped_socket(monkeypatch):
    urls, got = [], []

    async def go():
        stop = asyncio.Event()
        monkeypatch.setitem(sys.modules, 'websockets',
                            fake_websockets([ConnectionResetError('drop'), [frame('dogeusdt@aggTrade', TRADE)]], urls, stop))
        await asyncio.wait_for(rm.stream_loop(['DOGEUSDT'], lambda raw, ns: got.append(raw), stop, 'market', liq=True), 10)

    asyncio.run(go())
    assert len(urls) == 2 and urls[0] == urls[1] == MKT + 'dogeusdt@aggTrade/!forceOrder@arr'
    assert got == [frame('dogeusdt@aggTrade', TRADE)]


def test_run_liq_end_to_end_writes_both_files_and_calls_no_rest(tmp_path, monkeypatch):
    urls = []
    script = {'public': [[frame('dogeusdt@bookTicker', BOOK)]],
              'market': [[frame('dogeusdt@aggTrade', TRADE), frame('!forceOrder@arr', LIQ)]]}

    def connect(url, **kw):
        route = 'public' if url.startswith(PUB) else 'market'
        return fake_websockets(script[route], urls).connect(url, **kw)

    monkeypatch.setitem(sys.modules, 'websockets', types.SimpleNamespace(connect=connect))
    monkeypatch.setattr(rm, 'fetch_snapshot', lambda *a, **k: pytest.fail('no REST snapshot in --liq mode'))
    counts = asyncio.run(rm.run(['DOGEUSDT'], tmp_path, duration=1.5, snap_every=600, liq=True, stats_every=0))
    assert counts == {'DOGEUSDT': 2, 'liquidations': 1}
    liq_rows = [json.loads(x) for p in tmp_path.glob('liquidations_*.jsonl') for x in p.read_text(encoding='utf-8').splitlines()]
    assert len(liq_rows) == 1 and liq_rows[0]['d'] == LIQ and liq_rows[0]['book']['a'] == '0.07005'
    hourly = [json.loads(x) for p in (tmp_path / 'DOGEUSDT').glob('*.jsonl.gz') for x in gzip.open(p, 'rt').read().splitlines()]
    assert sorted(r['s'] for r in hourly) == ['aggTrade', 'bookTicker']
    assert sorted(urls) == sorted([MKT + 'dogeusdt@aggTrade/!forceOrder@arr', PUB + 'dogeusdt@bookTicker'])


def test_cli_refuses_liq_with_light():
    with pytest.raises(SystemExit):
        rm.main(['--liq', '--light', '--duration', '1'])
