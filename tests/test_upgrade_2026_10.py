"""2026-10 upgrade: repo layout, JEV recovery score, GTX maker sniper, PM15 impulse,
flow gauge, atomic telemetry IO, SSE stream."""
import json
import math
import os
import threading
import http.client
from dataclasses import replace
from pathlib import Path

import pytest

from core import atomic_io
from research import fast_liquidation_sniper as fls
from research import polymarket_15m_fast_arb as pm
from research.flow_gauge import FlowGauge

ROOT = Path(__file__).resolve().parents[1]
SPEC = {'step': 1.0, 'min_qty': 1.0, 'min_notional': 5.0, 'tick': 0.0001}


def liq(sym='DOGEUSDT', side='SELL', qty=300_000, price=0.2, t_ms=1_000_000):
    return {'stream': '!forceOrder@arr', 'data': {'e': 'forceOrder', 'E': t_ms, 'o': {
        's': sym, 'S': side, 'q': str(qty), 'p': str(price), 'ap': str(price), 'z': str(qty), 'T': t_ms}}}


def marks(pairs):
    return {'stream': '!markPrice@arr@1s', 'data': [{'e': 'markPriceUpdate', 's': s, 'p': str(p)} for s, p in pairs]}


def wicked(cfg, drop=0.008, sym='DOGEUSDT'):
    s = fls.Sniper(cfg, {sym: SPEC})
    for i in range(20):
        s.on_message(marks([(sym, 0.2)]), 1000.0 + i)
    s.on_message(marks([(sym, 0.2 * (1 - drop))]), 1021.0)
    return s


# ----------------------------------------------------------------------------- layout
def test_root_is_clean_and_launchers_live_in_scripts():
    allowed_files = {'README.md', 'CLAUDE.md', 'AGENTS.md', 'requirements.txt', 'pytest.ini', 'init.sh',
                     'run.bat', '.env.local', '.gitignore'}
    files = {p.name for p in ROOT.iterdir() if p.is_file()}
    assert files <= allowed_files, files - allowed_files
    assert not list(ROOT.glob('FORM_*.md')) and not list(ROOT.glob('CHAY_*.bat'))
    assert (ROOT / 'prompts').is_dir() and list((ROOT / 'prompts').glob('FORM_*.md'))
    for bat in (ROOT / 'scripts').glob('*.bat'):
        text = bat.read_text(errors='replace')
        assert '%~dp0..' in text, f'{bat.name} must resolve the repo root from scripts/'
    for bat in (ROOT / 'scripts' / 'legacy').glob('*.bat'):
        text = bat.read_text(errors='replace')
        assert '%~dp0..\\..' in text, f'{bat.name} must resolve the repo root from scripts/legacy/'
    for name in ('run_launcher.py', 'run_alpha_instant.py', 'run_concurrent_paper.py'):
        assert 'parents[1]' in (ROOT / 'scripts' / name).read_text()


def test_master_launcher_menu_never_arms_mainnet():
    text = (ROOT / 'run.bat').read_text()
    for target in ('-m core.engine --mode demo', '-m core.engine --mode live', '-m pytest -q',
                   'if not "%CONFIRM%"=="LIVE"'):
        assert target in text
    # the file never arms mainnet by itself: a human types LIVE each session
    assert '--arm' not in text and 'run_concurrent_live' not in text and 'ARM_LIVE=LIVE' not in text


def test_credential_file_still_ignored_after_move():
    gi = (ROOT / '.gitignore').read_text()
    assert 'prompts/FORM_GPT6_ASTRA_GROKTOPUS_REBUILD_MASTER.md' in gi
    assert 'xpl_' not in (ROOT / 'tools' / 'check_models_live.py').read_text()


# ----------------------------------------------------------------------------- JEV recovery score
def test_recovery_score_matches_jev_invariant_and_trendbreak():
    cfg = fls.SniperConfig()
    lo = fls.recovery_score(60_000, 1, 0.5, cfg)
    hi = fls.recovery_score(400_000, 6, 2.0, cfg)
    assert 0 <= lo['score'] < hi['score'] <= 10
    assert (hi['score'] >= 8.5) == (hi['ratio'] >= 3.0 - 1e-9)
    assert fls.recovery_score(1e6, 9, 5.0, cfg)['reason'] == 'TRENDBREAK'
    assert fls.recovery_score(1e6, 9, None, cfg)['score'] == 0.0


def test_score_gate_latency_recorded_and_dynamic_tp_band():
    cfg = replace(fls.SniperConfig(), dynamic_tp=True, min_score=0.0)
    s = wicked(cfg)
    cas = s.on_message(liq(), 1022.0)
    assert cas['jev']['us'] < 30_000 and s.pos is not None
    assert 0.8 <= s.pos['plan']['tp_pct'] <= 1.5
    blocked = wicked(replace(cfg, min_score=9.99)).on_message(liq(), 1022.0)
    assert blocked['action'].startswith('JEV')


def test_observe_mode_never_enters():
    s = wicked(replace(fls.SniperConfig(), enabled=False))
    assert s.on_message(liq(), 1022.0)['action'] == 'OBSERVE' and s.pos is None


def test_maker_gtx_fills_only_on_trade_through_and_pays_maker_fee():
    cfg = replace(fls.SniperConfig(), entry_mode='maker', maker_offset_bps=5.0, maker_ttl_s=3.0)
    s = wicked(cfg)
    cas = s.on_message(liq(), 1022.0)
    assert cas['action'] == 'GTX BUY' and s.pending and s.pos is None
    lim = s.pending['limit']
    assert lim < s.marks.last('DOGEUSDT')                       # post-only BUY rests below mark
    s.on_message(marks([('DOGEUSDT', lim * 1.0001)]), 1023.0)
    s.step(1023.0)
    assert s.pos is None and s.pending                          # not yet through
    s.on_message(marks([('DOGEUSDT', lim * 0.9999)]), 1024.0)
    s.step(1024.0)
    assert s.pos and s.pos['entry'] == lim and s.pos['entry_fee_bps'] == cfg.maker_bps
    assert s.stats['maker_filled'] == 1
    s.on_message(marks([('DOGEUSDT', s.pos['tp'] * 1.001)]), 1030.0)
    t = s.step(1030.0)
    assert t['reason'] == 'TP' and t['entry_mode'] == 'maker'
    assert t['net_pct'] == pytest.approx((t['exit'] / t['entry'] - 1) * 100 - 2 * cfg.maker_bps / 100)


def test_maker_gtx_missed_when_wick_bounces():
    s = wicked(replace(fls.SniperConfig(), entry_mode='maker', maker_ttl_s=2.0))
    s.on_message(liq(), 1022.0)
    s.on_message(marks([('DOGEUSDT', 0.2)]), 1023.0)
    s.step(1025.0)
    assert s.pending is None and s.pos is None and s.stats['maker_missed'] == 1


def test_liq_series_buckets_by_second_and_side():
    s = fls.Sniper(fls.SniperConfig(), {})
    s.on_message(liq(side='SELL', qty=100_000), 2000.2)
    s.on_message(liq(side='BUY', qty=50_000), 2000.7)
    s.on_message(liq(side='SELL', qty=10_000), 2001.1)
    snap = s.snapshot(2001.5)
    assert snap['liq_series'] == [[2000, 20_000.0, 10_000.0], [2001, 2_000.0, 0.0]]
    json.dumps(atomic_io.json_safe(snap), allow_nan=False)


def test_sniper_and_pm_cli_refuse_live_mode():
    with pytest.raises(SystemExit):
        fls.main(['--mode', 'live'])
    with pytest.raises(SystemExit):
        pm.main(['--mode', 'live'])


# ----------------------------------------------------------------------------- PM15 impulse
def book(ask, size=100):
    return {'asks': [{'price': str(ask), 'size': str(size)}], 'bids': []}


def test_impulse_from_history_window():
    hist = [(100.0, 100.0), (105.0, 100.1), (109.0, 100.35)]
    assert pm.impulse_from_history(hist, 109.0, 8.0) == pytest.approx(0.25 / 100.1 * 100)
    assert pm.impulse_from_history(hist[:1], 109.0, 8.0) is None


def test_impulse_trigger_needs_stale_ask_and_non_negative_edge():
    start = 1_768_502_700
    cfg = pm.Pm15Config(min_edge=0.9)                             # fair-value gate effectively off
    args = ('btc', start, start + 600, 100.35, 100.0, 0.0008)
    hit = pm.evaluate(*args, book(0.40), book(0.62), cfg, impulse_pct=0.35)
    assert hit['flag'] and hit['trigger'] == 'IMPULSE' and hit['side'] == 'UP' and hit['exp_roi'] > 0
    assert not pm.evaluate(*args, book(0.50), book(0.62), cfg, impulse_pct=0.35)['flag']    # not stale
    assert not pm.evaluate(*args, book(0.40), book(0.62), cfg, impulse_pct=0.10)['flag']    # no impulse
    down = pm.evaluate('btc', start, start + 600, 99.65, 100.0, 0.0008, book(0.62), book(0.40), cfg,
                       impulse_pct=-0.35)
    assert down['trigger'] == 'IMPULSE' and down['side'] == 'DOWN'
    # model says Up is unlikely (spot far below open): stale-looking ask is NOT a buy
    bad = pm.evaluate('btc', start, start + 600, 99.0, 100.0, 0.0008, book(0.40), book(0.62), cfg,
                      impulse_pct=0.35)
    assert not bad['flag']


def test_scan_once_caches_window_and_runs_assets_in_parallel():
    now = 1_768_502_700 + 400
    start = pm.window_start(now)
    calls = []

    def http(url):
        calls.append(url)
        if 'gamma-api' in url:
            return [{'markets': [{'outcomes': '["Up","Down"]', 'clobTokenIds': '["U","D"]'}]}]
        if 'klines' in url:
            return [[(start - 120 * 60 + i * 60) * 1000, '100', '0', '0', str(100 * (1 + 0.0004 * math.sin(i)))]
                    for i in range(121)] + [[start * 1000, '100', '0', '0', '100']]
        if 'ticker/price' in url:
            return {'price': '100.4'}
        return {'asks': [{'price': '0.45', 'size': '50'}], 'bids': []}

    cache = {}
    cfg = pm.Pm15Config(assets=('btc', 'eth'))
    r1 = pm.scan_once(cfg, http, now=now, cache=cache, impulses={'btc': 0.4})
    n1 = len(calls)
    r2 = pm.scan_once(cfg, http, now=now + 2, cache=cache)
    assert not r1['errors'] and [r['asset'] for r in r1['rows']] == ['btc', 'eth']
    assert r1['rows'][0]['impulse_pct'] == 0.4 and r2['rows'][0]['impulse_pct'] is None
    assert len(calls) - n1 == 2 * 3                                # ticker + 2 books per asset
    pm.scan_once(cfg, http, now=now + 900, cache=cache)
    assert all(k[1] == start + 900 for k in cache)                 # old window dropped


# ----------------------------------------------------------------------------- flow gauge
def test_flow_gauge_cvd_obi_pressure_and_expiry():
    g = FlowGauge('BTCUSDT', window_s=10)
    assert g.route({'stream': 'btcusdt@aggTrade', 'data': {'p': '100', 'q': '2', 'm': False}}, 1.0)
    g.route({'stream': 'btcusdt@aggTrade', 'data': {'p': '100', 'q': '1', 'm': True}}, 2.0)
    assert g.cvd_window() == pytest.approx(100.0) and g.cvd_session == pytest.approx(100.0)
    g.route({'stream': 'btcusdt@depth5@100ms', 'data': {'b': [['99', '3']], 'a': [['101', '1']]}}, 2.5)
    assert g.obi == pytest.approx(0.5) and -1 <= g.pressure() <= 1
    assert not g.route({'stream': 'ethusdt@aggTrade', 'data': {}}, 3.0)
    snap = g.snapshot(20.0)
    assert snap['cvd_window'] == 0 and snap['buy_usd'] == 0 and snap['cvd_session'] == pytest.approx(100.0)
    assert snap['series'] and json.dumps(snap)


# ----------------------------------------------------------------------------- atomic IO
def test_atomic_write_is_nan_safe_and_retries_windows_lock(tmp_path, monkeypatch):
    f = tmp_path / 'd' / 's.json'
    assert atomic_io.atomic_write_json(f, {'x': float('nan'), 'y': [1.0, float('inf')]})
    assert atomic_io.read_json(f) == {'x': None, 'y': [1.0, None]}
    real, fails = os.replace, [2]

    def flaky(a, b):
        if fails[0]:
            fails[0] -= 1
            raise PermissionError('locked by reader')
        return real(a, b)
    monkeypatch.setattr(atomic_io.os, 'replace', flaky)
    assert atomic_io.atomic_write_json(f, {'v': 2}, delay=0.0) and atomic_io.read_json(f) == {'v': 2}
    monkeypatch.setattr(atomic_io.os, 'replace', lambda a, b: (_ for _ in ()).throw(PermissionError()))
    assert not atomic_io.atomic_write_json(f, {'v': 3}, retries=2, delay=0.0)
    assert atomic_io.read_json(f) == {'v': 2} and not list(f.parent.glob('.*.tmp'))
    assert atomic_io.read_json(tmp_path / 'missing.json') is None


def test_runner_state_carries_pnl_and_flow(tmp_path):
    from research import fast_runner as fr
    s = wicked(fls.SniperConfig())
    s.on_message(liq(), 1022.0)
    g = FlowGauge('BTCUSDT')
    st = fr.build_state(s, {'rows': []}, pm.SignalLedger(), {'connected': True}, 1023.0, g)
    fr.write_state(tmp_path / 'f.json', st)
    got = json.loads((tmp_path / 'f.json').read_text(encoding='utf-8'))
    assert got['pnl']['equity_start'] == 3.85 and got['pnl']['curve'] == [3.85]
    assert got['flow']['symbol'] == 'BTCUSDT' and got['sniper']['liq_series']
    m, pub = fr.ws_urls('BTCUSDT')
    assert m.startswith('wss://fstream.binance.com/market/stream?streams=') and m.endswith('/btcusdt@aggTrade')
    assert pub == 'wss://fstream.binance.com/public/stream?streams=btcusdt@depth5@100ms'


# ----------------------------------------------------------------------------- server: SSE + assets
def test_sse_stream_and_terminal_asset(tmp_path):
    from core.web_ui_server import Telemetry, make_server
    f = tmp_path / 'fast.json'
    f.write_text(json.dumps({'updated_at': 1, 'pnl': {'balance': 3.9}}), encoding='utf-8')
    server = make_server(Telemetry(), port=0, fast_file=f, stream_max_s=0.6)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        c.request('GET', '/terminal.js', headers={'Host': '127.0.0.1'})
        r = c.getresponse()
        assert r.status == 200 and b'EventSource' in r.read()
        assert 'fonts.googleapis.com' in r.getheader('Content-Security-Policy')
        c = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
        c.request('GET', '/api/stream', headers={'Host': '127.0.0.1'})
        r = c.getresponse()
        assert r.getheader('Content-Type').startswith('text/event-stream')
        body = r.read().decode()                                   # server closes after stream_max_s
        frame = next(line for line in body.splitlines() if line.startswith('data: '))
        data = json.loads(frame[6:])
        assert data['fast']['pnl']['balance'] == 3.9 and 'mode' in data['state']
    finally:
        server.shutdown()
        server.server_close()


def test_telemetry_persist_is_readable_by_standalone_server(tmp_path, monkeypatch):
    import core.web_ui_server as ui
    monkeypatch.setattr(ui, 'STATE_FILE', tmp_path / 'telemetry_state.json')
    t = ui.Telemetry()
    t.update(mode='SHADOW', equity=3.86, status='running')
    assert t.persist()
    got = ui._read_state_file()
    assert got['equity'] == 3.86 and got['updated_at'] > 0
