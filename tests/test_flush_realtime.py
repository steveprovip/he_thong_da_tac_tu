"""Real-time layer of the DOGE flush engine: WebSocket state, posterior EV,
warm-start regime priming, the opt-in OBI/CVD gate and measured telemetry.

Everything runs offline: MicroFeed.on_message is fed recorded-shape frames and
the loop runs against a fake socket pump.
"""
import asyncio
import json

import research.flush_shadow as fs
from research.flush_regime import RANGE
from research.flush_shadow import FlushShadowEngine, MicroFeed, posterior_ev

from tests.test_flush_short_and_bootstrap import range_bars15


def book(b, a, bq=100.0, aq=100.0):
    return {'stream': 'dogeusdt@bookTicker',
            'data': {'e': 'bookTicker', 'b': str(b), 'B': str(bq), 'a': str(a), 'A': str(aq)}}


def depth(bids, asks):
    return {'stream': 'dogeusdt@depth5@100ms',
            'data': {'e': 'depthUpdate', 'b': [[ '0.2', str(q)] for q in bids],
                     'a': [['0.2001', str(q)] for q in asks]}}


def agg(p, q, buyer_maker):
    return {'stream': 'dogeusdt@aggTrade',
            'data': {'e': 'aggTrade', 'p': str(p), 'q': str(q), 'm': buyer_maker, 'T': 0}}


def kline(t, o, h, l, c, v=1000.0):
    return {'stream': 'dogeusdt@kline_1m',
            'data': {'e': 'kline', 'k': {'t': t, 'T': t + 59999, 'o': str(o), 'h': str(h),
                                         'l': str(l), 'c': str(c), 'v': str(v)}}}


def flat_1m(n=16, mid=0.2):
    return [{'time': i * 60000, 'open': mid, 'high': mid, 'low': mid, 'close': mid,
             'volume': 1000.0, 'close_time': i * 60000 + 59999} for i in range(n)]


# ------------------------------------------------------------------ MicroFeed
def test_book_depth_and_flow_update_state():
    f = MicroFeed()
    assert f.on_message(book(0.19990, 0.20000, bq=300, aq=100), now=100.0)
    assert (f.bid, f.ask) == (0.1999, 0.2)
    assert f.on_message(depth([30, 30, 20, 10, 10], [10, 10, 10, 10, 10]), now=100.0)
    assert abs(f.obi - (100 - 50) / 150) < 1e-12
    f.on_message(agg(0.2, 1000, buyer_maker=False), now=100.0)   # buyer aggressed: +200 USDT
    f.on_message(agg(0.2, 250, buyer_maker=True), now=100.0)     # seller aggressed: -50 USDT
    snap = f.snapshot(now=100.0)
    assert abs(snap['cvd_60s'] - 150.0) < 1e-9 and abs(snap['cvd_session'] - 150.0) < 1e-9
    assert snap['microprice'] > (f.bid + f.ask) / 2, 'bid-heavy top of book leans the microprice up'


def test_cvd_window_expires_but_session_total_keeps():
    f = MicroFeed(cvd_window=60.0)
    f.on_message(agg(0.2, 1000, False), now=0.0)
    f.on_message(agg(0.2, 500, True), now=50.0)
    assert abs(f.snapshot(now=59.0)['cvd_60s'] - 100.0) < 1e-9
    snap = f.snapshot(now=70.0)
    assert abs(snap['cvd_60s'] + 100.0) < 1e-9, 'the +200 left the 60 s window'
    assert abs(snap['cvd_session'] - 100.0) < 1e-9


def test_kline_stream_replaces_forming_bar_then_appends():
    f = MicroFeed()
    f.seed_klines(flat_1m(16))
    f.on_message(kline(15 * 60000, 0.2, 0.201, 0.199, 0.2005), now=1.0)
    assert len(f.klines) == 16 and f.klines[-1]['close'] == 0.2005
    f.on_message(kline(16 * 60000, 0.2005, 0.2005, 0.2005, 0.2005), now=2.0)
    assert len(f.klines) == 17
    f.on_message(kline(3 * 60000, 1, 1, 1, 1), now=3.0)
    assert len(f.klines) == 17, 'an out-of-order bar is ignored, never inserted'


def test_bad_or_unknown_frames_change_nothing():
    f = MicroFeed()
    assert not f.on_message({'stream': 'x', 'data': {'e': 'markPriceUpdate'}}, now=1.0)
    assert not f.on_message(book(0, 0), now=1.0)
    assert not f.on_message({'data': {'e': 'aggTrade', 'p': 'nan?'}}, now=1.0)
    assert f.frames == 0 and not f.ready(now=1.0)


def test_ready_needs_quotes_bars_and_a_fresh_frame():
    f = MicroFeed()
    f.on_message(book(0.1999, 0.2), now=10.0)
    assert not f.ready(now=10.0), 'no 1m history yet'
    f.seed_klines(flat_1m(16))
    assert f.ready(now=10.0)
    assert not f.ready(now=10.0 + fs.FEED_STALE_SECONDS + 0.1), 'stale socket falls back to REST'


# --------------------------------------------------------------- posterior EV
def test_posterior_ev_shrinks_small_samples():
    mean0, p0 = posterior_ev(0, 0, 0)
    assert abs(mean0 - (35.67 + 1.0 - 29.44) / 3) < 1e-9
    _, p_one_tp = posterior_ev(1, 0, 0)
    _, p_many_tp = posterior_ev(30, 5, 10)
    _, p_losing = posterior_ev(5, 5, 30)
    assert p_losing < 0.05 < p0 < p_one_tp < p_many_tp
    assert posterior_ev(3, 1, 2) == posterior_ev(3, 1, 2), 'deterministic'


def test_branch_stats_carry_posterior_only_after_a_trade():
    b = fs.ShadowBranch('baseline')
    assert b.get_stats()['p_ev_positive'] is None
    b.open_position(0.0, 0.2, 26.0, 0.0, 0.0)
    b.update(fs.round_tick(0.2 * (1 + fs.TP_BPS / 1e4)), 1.0)
    st = b.get_stats()
    assert st['tp_count'] == 1 and 0.5 < st['p_ev_positive'] < 1.0
    assert abs(st['fees_usdt'] - 4.0 / 1e4 * 0.2 * 26.0) < 1e-12


# ------------------------------------------------------------ regime priming
def test_prime_regime_reads_two_closes_from_history():
    e = FlushShadowEngine()
    assert e.prime_regime(range_bars15(99)) == RANGE
    assert e.range_streak == 2 and e.long_allowed(), 'hysteresis met from history at t=0'
    e2 = FlushShadowEngine()
    e2.prime_regime(range_bars15(60))
    assert e2.range_streak == 0, 'too little history primes nothing'


# ----------------------------------------------------------------- micro gate
def flush_klines():
    k = flat_1m(15)
    k.append({'time': 15 * 60000, 'open': 0.2, 'high': 0.2, 'low': 0.1996, 'close': 0.1999,
              'volume': 3000.0, 'close_time': 0})
    k.append({'time': 16 * 60000, 'open': 0.1999, 'high': 0.1999, 'low': 0.1999, 'close': 0.1999,
              'volume': 10.0, 'close_time': 0})
    return k


def test_micro_gate_is_opt_in_and_fails_closed():
    t = {'bidPrice': '0.19990', 'askPrice': '0.19991'}
    e = FlushShadowEngine()
    e.micro = {'obi': -0.4, 'cvd_60s': -500.0}
    assert e.update_cycle(t, flush_klines()).get('reason') != 'MICRO_UNCONFIRMED', 'off by default'
    e = FlushShadowEngine()
    e.micro_confirm = True
    e.micro = {'obi': -0.4, 'cvd_60s': -500.0}
    assert e.update_cycle(t, flush_klines())['reason'] == 'MICRO_UNCONFIRMED'
    e.micro = {}
    assert e.update_cycle(t, flush_klines())['reason'] == 'MICRO_UNCONFIRMED', 'no data never means yes'
    assert e.micro_confirms('BUY') is False
    e.micro = {'obi': 0.3, 'cvd_60s': 120.0}
    assert e.micro_confirms('BUY') and not e.micro_confirms('SELL')


# ------------------------------------------------------------------ telemetry
def test_telemetry_is_measured_not_invented(tmp_path, monkeypatch):
    monkeypatch.setattr(fs, 'TELEMETRY_STATE_FILE', tmp_path / 'telemetry_state.json')
    e = FlushShadowEngine()
    e.micro = {'obi': 0.25, 'cvd_60s': 1234.0, 'microprice': 0.20004}
    e.open_shadow_position(0.0, 0.2, 26.0, side='SELL')
    e.write_telemetry_snapshot(0.1990, 0.1991, 'IN_POSITION')
    st = json.loads((tmp_path / 'telemetry_state.json').read_text(encoding='utf-8'))
    assert st['agents'] == [], 'no swarm votes are fabricated'
    mk = st['markets'][0]
    assert mk['obi'] == 0.25 and mk['cvd_60s'] == 1234.0 and mk['vpin'] is None
    assert mk['edge_bps'] is None, 'no realized edge before a closed trade'
    pos = st['positions'][0]
    assert pos['side'] == 'SELL' and pos['pnl_bps'] > 0, 'a short gains when price falls'
    assert st['equity'] > st['wallet'], 'equity is marked to market; wallet is realized'
    assert st['history'][-1]['value'] == round(st['equity'], 6) or abs(st['history'][-1]['value'] - st['equity']) < 1e-4


# ------------------------------------------------------------------ the loop
async def test_loop_boots_on_the_socket_and_publishes(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(fs, 'TELEMETRY_STATE_FILE', tmp_path / 'telemetry_state.json')
    monkeypatch.setattr(fs, 'COHORT_LOG_FILE', tmp_path / 'cohorts.jsonl')
    monkeypatch.setattr(fs, 'TICK_SECONDS', 0.02)
    monkeypatch.setattr(fs, 'REST_POLL_SECONDS', 0.02)
    monkeypatch.setattr(fs, 'fetch_rest_market_data',
                        lambda: ({'bidPrice': '0.2', 'askPrice': '0.20001'}, flat_1m(30), 1.0, None))
    monkeypatch.setattr(fs, 'fetch_bars15', lambda limit=60: range_bars15(max(limit, 61)))

    async def fake_run(self):
        i = 0
        while True:
            self.on_message(book(0.2 + (i % 3) * 1e-5, 0.20001 + (i % 3) * 1e-5))
            self.on_message(depth([50] * 5, [30] * 5))
            self.on_message(agg(0.2, 100, i % 2 == 0))
            i += 1
            await asyncio.sleep(0.005)

    monkeypatch.setattr(fs.MicroFeed, 'run', fake_run)
    engine = await fs.run_shadow_loop(seconds=1)
    out = capsys.readouterr().out
    assert '[BOOT] first tick' in out
    assert engine.feed_label == 'binance-ws', 'the socket took over from the REST warm start'
    assert engine.micro.get('obi') == 0.25
    assert engine.regime == RANGE and engine.range_streak == 2
    st = json.loads((tmp_path / 'telemetry_state.json').read_text(encoding='utf-8'))
    assert st['market_data'] == 'binance-ws' and st['markets'][0]['obi'] == 0.25


# ------------------------------------------------------------- dashboard path
def test_standalone_dashboard_serves_a_fresh_engine_file_without_probing(tmp_path, monkeypatch):
    import time
    import urllib.request
    import core.web_ui_server as ui
    state = tmp_path / 'telemetry_state.json'
    state.write_text(json.dumps({'mode': 'SHADOW', 'equity': 3.8512, 'updated_at': time.time()}),
                     encoding='utf-8')
    monkeypatch.setattr(ui, 'STATE_FILE', state)
    probes = []
    real = urllib.request.urlopen

    def spy(url, *a, **k):
        if isinstance(url, str) and ':8766' in url:
            probes.append(url)
        return real(url, *a, **k)

    monkeypatch.setattr(urllib.request, 'urlopen', spy)
    server = ui.start_server(ui.Telemetry(), port=0)
    try:
        got = json.load(real(f'http://127.0.0.1:{server.server_port}/api/state'))
    finally:
        server.shutdown()
        server.server_close()
    assert got['equity'] == 3.8512 and got['shutdown'] is False
    assert probes == [], 'a fresh engine file must not wait on the :8766 probe'


def test_run_alpha_instant_accepts_the_flush_route():
    import run_alpha_instant as rai
    assert rai.arguments(['--flush', '--max-minutes', '3']).flush is True
    assert rai.arguments([]).flush is False
