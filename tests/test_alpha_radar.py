"""Alpha Radar (Binance surge breakout) + Event Radar (Polymarket) + runner + /api/radar."""
import json
import math

import pytest

from research import alpha_radar as ar
from research import event_radar as er
from research import radar_runner as rr

CFG = ar.RadarConfig()
CFG_NT = ar.RadarConfig(trend_frames=())
LEGACY = ar.RadarConfig(sl_mode='money', max_loss_usdt=0.05, tp_pcts=(2.0, 5.0), hold_bars=48)          # trend gate off: isolates the 5m signal
TREND = {'dir': 1}
NOW_MS = 10 ** 13


# ----------------------------------------------------------------------------- synthetic tape
def rows(n=320, surge_at=None, surge_len=6, up=True, after=0, after_step=0.003,
         base_qv=100.0, osc=0.002, surge_step=0.004, forming=False):
    """Binance kline rows: oscillating quiet tape, optional volume-surge breakout."""
    out, p = [], 1.0
    surge_at = n - surge_len if surge_at is None else surge_at
    d = 1 if up else -1
    for i in range(n + (1 if forming else 0)):
        if surge_at <= i < surge_at + surge_len:
            step, qv, tb = d * surge_step, base_qv * 6, (0.7 if up else 0.3)
        elif surge_at + surge_len <= i < surge_at + surge_len + after:
            step, qv, tb = d * after_step, base_qv, 0.5
        else:
            step, qv, tb = (osc if i % 2 else -osc), base_qv * (1 + 0.1 * (i * 7 % 3)), 0.5
        o, c = p, p * (1 + step)
        h, l = max(o, c) * 1.0003, min(o, c) * 0.9997
        p = c
        t = i * 300_000
        close_t = NOW_MS + 1 if (forming and i == n) else t + 299_999
        out.append([t, str(o), str(h), str(l), str(c), str(qv / c), close_t, str(qv), 0, '0', str(qv * tb), '0'])
    return out


def bars(**kw):
    return ar.parse_klines(rows(**kw), NOW_MS)


# ----------------------------------------------------------------------------- features
def test_parse_klines_drops_forming_bar():
    assert len(ar.parse_klines(rows(n=50, forming=True), NOW_MS)) == 50


def test_rsi_extremes_and_balance():
    assert ar.rsi_series([float(i) for i in range(1, 40)])[-1] == 100.0
    alt = [1.0 + (0.01 if i % 2 else 0.0) for i in range(60)]
    assert 40 < ar.rsi_series(alt)[-1] < 60


def test_surge_breakout_fires_long():
    f = ar.features(bars(), CFG)
    assert f['fired'] and f['side'] == 'LONG'
    assert f['surge'] >= 3.0 and f['breakout'] and f['rsi_ok'] and f['taker_ok']
    assert f['price'] > f['level'] and not f['chase']


def test_mirror_short():
    f = ar.features(bars(up=False), CFG)
    assert f['fired'] and f['side'] == 'SHORT' and f['taker_ok']


def test_quiet_tape_does_not_fire():
    f = ar.features(bars(surge_len=0), CFG)
    assert not f['fired'] and f['surge'] < 3.0


def test_volume_without_breakout_does_not_fire():
    f = ar.features(bars(surge_step=0.0), CFG)
    assert f['surge'] >= 3.0 and not f['fired']


def test_short_history_returns_none():
    assert ar.features(bars(n=100), CFG) is None


def test_obi_from_depth():
    assert ar.obi_from_depth({'bids': [['1', '300']], 'asks': [['1', '100']]}) == pytest.approx(0.5)


# ----------------------------------------------------------------------------- plan for 3.85 USDT
def test_plan_respects_min_notional_and_5_cent_loss():
    pl = ar.trade_plan(0.2, 'LONG', {'step': 1, 'min_qty': 1, 'min_notional': 5}, LEGACY, atr=0.2)
    assert pl['notional'] >= 5.0 and pl['tradeable']
    assert pl['loss_usdt'] <= LEGACY.max_loss_usdt + 1e-9
    assert 0.5 < pl['sl_pct'] < 1.0 and pl['sl'] < 0.2
    assert pl['tps'][0]['pnl_usdt'] > 0.09 and pl['tps'][1]['pnl_usdt'] > 0.24
    assert pl['tps'][1]['rr'] > 5
    assert pl['margin'] <= CFG.equity * CFG.margin_frac


def test_plan_warns_when_stop_inside_noise_and_blocks_big_min_notional():
    pl = ar.trade_plan(0.2, 'SHORT', {'step': 1, 'min_qty': 1, 'min_notional': 5}, LEGACY, atr=1.0)
    assert pl['sl'] > 0.2 and any('ATR' in w for w in pl['warnings'])
    big = ar.trade_plan(60000, 'LONG', {'step': 0.001, 'min_qty': 0.001, 'min_notional': 100}, LEGACY)
    assert not big['tradeable'] and big['leverage'] is None


# ----------------------------------------------------------------------------- event study
def _b(o, h, l, c):
    return {'o': o, 'h': h, 'l': l, 'c': c, 'qv': 1, 'tbq': 0.5, 't': 0, 'T': 0}


def test_simulate_stop_checked_before_target_in_same_bar():
    series = [_b(1, 1, 1, 1), _b(1, 1.05, 0.98, 1.0)] + [_b(1, 1, 1, 1)] * 5
    assert ar.simulate(series, 0, 'LONG', 2.0, 1.0, 5, CFG)[0] == 'SL'


def test_simulate_tp_time_and_unresolved():
    up = [_b(1, 1, 1, 1), _b(1, 1.001, 0.999, 1), _b(1, 1.03, 1, 1.02)] + [_b(1, 1, 1, 1)] * 5
    res = ar.simulate(up, 0, 'LONG', 2.0, 1.0, 5, CFG)
    assert res[0] == 'TP' and res[1] == pytest.approx(2.0 - 0.07)
    flat = [_b(1, 1.001, 0.999, 1)] * 10
    res = ar.simulate(flat, 0, 'LONG', 2.0, 1.0, 5, CFG)
    assert res[0] == 'TIME' and res[1] < 0                            # costs make a flat hold negative
    assert ar.simulate(flat[:3], 0, 'LONG', 2.0, 1.0, 5, CFG) is None  # future incomplete: not counted


def test_simulate_enters_next_open_not_signal_close():
    series = [_b(1, 1, 1, 1), _b(1.1, 1.1, 1.1, 1.1)] + [_b(1.1, 1.1, 1.1, 1.1)] * 5
    res = ar.simulate(series, 0, 'LONG', 2.0, 1.0, 5, CFG)
    assert res[0] == 'TIME' and res[1] == pytest.approx(-(2 * CFG.taker_bps + CFG.slip_bps) / 100)


def test_event_study_finds_past_breakout_and_resolves_it():
    b = bars(n=700, surge_at=400, after=30, after_step=0.004)
    out = ar.event_study(b, CFG_NT, sl_pct=0.8, tp_pct=2.0)
    assert out and out[0]['side'] == 'LONG' and out[0]['outcome'] == 'TP'
    assert ar.summarize_study(out)['tp_rate'] > 0


# ----------------------------------------------------------------------------- live scan (fake HTTP)
def _spec(sym, mn=5.0, step='1'):
    return {'symbol': sym, 'status': 'TRADING', 'quoteAsset': 'USDT', 'contractType': 'PERPETUAL',
            'filters': [{'filterType': 'LOT_SIZE', 'stepSize': step, 'minQty': step},
                        {'filterType': 'MIN_NOTIONAL', 'notional': str(mn)},
                        {'filterType': 'PRICE_FILTER', 'tickSize': '0.0001'}]}


def trend_rows(n, d):
    out, p = [], 100.0
    for i in range(n):
        o, p = p, p * (1 + d * 0.003)
        out.append([i * 3_600_000, str(o), str(max(o, p)), str(min(o, p)), str(p), '1', i * 3_600_000 + 3_599_999,
                    str(p), 0, '0', str(p / 2), '0'])
    return out


def fake_binance(url):
    if 'exchangeInfo' in url:
        return {'symbols': [_spec('AAAUSDT'), _spec('BBBUSDT'), _spec('THINUSDT'),
                            {**_spec('SPOTX'), 'contractType': 'CURRENT_QUARTER'}]}
    if 'ticker/24hr' in url:
        return [{'symbol': 'AAAUSDT', 'quoteVolume': '9e7', 'priceChangePercent': '4'},
                {'symbol': 'BBBUSDT', 'quoteVolume': '8e7', 'priceChangePercent': '0.1'},
                {'symbol': 'THINUSDT', 'quoteVolume': '1e5', 'priceChangePercent': '30'}]
    if 'depth' in url:
        return {'bids': [['1', '500']], 'asks': [['1', '100']]}
    if 'klines' in url and ('interval=1h' in url or 'interval=4h' in url):
        n = int(url.split('limit=')[1].split('&')[0])
        return trend_rows(n, TREND['dir'])
    if 'klines' in url:
        sym = url.split('symbol=')[1].split('&')[0]
        limit = int(url.split('limit=')[1].split('&')[0])
        if sym == 'AAAUSDT':
            return rows(n=limit) if limit < 1000 else rows(n=limit, surge_at=600, after=30)
        return rows(n=limit, surge_len=0)
    raise AssertionError(url)


def test_scan_ranks_surge_and_builds_plan():
    r = ar.scan(CFG, fake_binance, now_ms=NOW_MS)
    assert r['scanned'] == 2                               # THIN filtered by 24h volume
    assert [p['symbol'] for p in r['picks']] == ['AAAUSDT']
    p = r['picks'][0]
    assert p['obi_ok'] and 'taker_ok' in p['confirmations'] and p['plan']['tradeable']
    assert p['trend'] == {'1h': 'UP', '4h': 'UP'} and p['trend_ok']
    rt, rp = p['retest'], p['retest_plan']
    assert 0.5 <= rt['pullback_pct'] <= 1.2 and rt['price'] < p['price'] and rp['entry'] == rt['price']
    assert rp['loss_usdt'] <= CFG.max_loss_usdt + 1e-9
    assert r['watchlist'][0]['symbol'] == 'BBBUSDT'


def test_backtest_pools_symbols():
    bt = ar.backtest(['AAAUSDT', 'BBBUSDT'], CFG_NT, fake_binance, now_ms=NOW_MS)
    assert bt['by_tp']['ladder']['n'] >= 1 and bt['per_symbol']['BBBUSDT']['n'] == 0
    assert bt['by_tp_retest']['ladder']['signals'] >= 1      # retest measured beside market
    assert set(bt['split']) == {'is', 'oos'} and bt['config']['sl_mode'] == 'atr'


# ----------------------------------------------------------------------------- event radar math
@pytest.mark.parametrize('q,expect', [
    ('Will Bitcoin reach $150,000 by December 31, 2026?', ('BTCUSDT', 'touch_up', 150000.0)),
    ('Bitcoin above 120,000 on October 3?', ('BTCUSDT', 'above', 120000.0)),
    ('Will Ethereum dip to $3,000 in September?', ('ETHUSDT', 'touch_down', 3000.0)),
    ('Will BTC hit 130k in October?', ('BTCUSDT', 'touch_up', 130000.0)),
    ('Will the price of Solana be below $150 on Oct 1?', ('SOLUSDT', 'below', 150.0)),
    ('Bitcoin between $110k and $115k?', None),
    ('Will the Fed cut rates in October?', None),
])
def test_parse_threshold_question(q, expect):
    assert er.parse_threshold_question(q) == expect


def test_touch_vs_terminal_and_limits():
    s, k, v, t = 100.0, 110.0, 0.6, 0.1
    assert er.p_touch(s, k, v, t) > er.p_terminal_above(s, k, v, t)
    assert er.p_touch(s, 90.0, v, t) > 1 - er.p_terminal_above(s, 90.0, v, t)
    assert er.p_touch(s, k, 0.0, t) == 0.0 and er.p_touch(s, s, v, t) == 1.0
    # driftless-log limit: reflection principle ~ 2 * P(terminal)
    assert er.p_touch(s, 101.0, 0.01, 0.01) == pytest.approx(2 * er.norm_cdf(-math.log(1.01) / 0.001), abs=1e-3)


def test_implied_vol_round_trip():
    p = er.model_probability('touch_up', 100, 120, 0.55, 0.2)
    assert er.implied_vol('touch_up', 100, 120, 0.2, p) == pytest.approx(0.55, rel=1e-3)


def test_realized_vol_and_fee():
    closes = [100 * math.exp(0.01 * (1 if i % 2 else -1) * (i % 2)) for i in range(200)]
    assert er.realized_vol(closes) > 0
    assert er.realized_vol([100.0] * 10) is None
    assert er.taker_fee(0.5, 0.07) == pytest.approx(0.0175)
    assert er.fee_rate('Crypto') == 0.07 and er.fee_rate('Politics') == 0.04


def _mkt(q, bid, ask, **kw):
    return {'id': kw.get('id', q[:8]), 'question': q, 'outcomes': '["Yes", "No"]',
            'outcomePrices': json.dumps([str((bid + ask) / 2), str(1 - (bid + ask) / 2)]),
            'bestBid': bid, 'bestAsk': ask, 'volume24hr': kw.get('vol', 5000),
            'liquidityNum': kw.get('liq', 20000), 'endDate': kw.get('end', '2026-10-27T00:00:00Z'),
            'active': True, 'closed': False, 'acceptingOrders': True, 'category': kw.get('cat', 'Crypto')}


NOW = er.parse_time('2026-09-27T00:00:00Z')


def test_normalize_market_decodes_string_arrays():
    m = er.normalize_market(_mkt('Will Bitcoin reach $150,000 by October 27?', 0.10, 0.12))
    assert m['yes'] == pytest.approx(0.11) and m['spread'] == pytest.approx(0.02) and m['fee_rate'] == 0.07


def test_negrisk_arb_detection_and_warnings():
    ev = {'title': 'Winner', 'slug': 'w', 'negRisk': True}
    cheap = [er.normalize_market(_mkt(f'Will {n} win?', a - 0.01, a, cat='Sports')) for n, a in
             (('A', 0.30), ('B', 0.30), ('C', 0.30))]
    a = er.negrisk_arb(ev, cheap, er.EventConfig())
    assert a['type'] == 'BUY_ALL_YES' and a['edge'] > 0.01 and a['legs'] == 3
    assert a['min_shares'] == 5.0 and a['min_capital'] == pytest.approx(5.0 * a['cost'])
    assert a['fits_account'] is (a['min_capital'] <= 3.85) and a['locked_usdc'] > 0
    assert any('locked until' in w for w in a['warnings'])
    fair = [er.normalize_market(_mkt(f'Will {n} win?', a - 0.01, a, cat='Sports')) for n, a in
            (('A', 0.35), ('B', 0.35), ('C', 0.33))]
    assert er.negrisk_arb(ev, fair, er.EventConfig()) is None
    rich = [er.normalize_market(_mkt(f'Will {n} win?', b, b + 0.01, cat='Sports')) for n, b in
            (('A', 0.40), ('Other', 0.40), ('C', 0.40))]
    a = er.negrisk_arb(ev, rich, er.EventConfig())
    assert a['type'] == 'BUY_ALL_NO' and any('exhaustive' in w for w in a['warnings'])
    assert er.negrisk_arb({**ev, 'negRisk': False}, cheap, er.EventConfig()) is None


def test_threshold_edge_picks_mispriced_side():
    m = er.normalize_market(_mkt('Will Bitcoin reach $110,000 by October 27?', 0.05, 0.07))
    e = er.threshold_edge(m, 100_000.0, 0.6, NOW, er.EventConfig())
    assert e['side'] == 'YES' and e['p_model'] > 0.3 and e['edge'] > 0.2 and e['flag']
    assert e['implied_vol'] < 0.6 and e['shares'] == math.floor(2.0 / 0.07)
    m2 = er.normalize_market(_mkt('Will Bitcoin reach $200,000 by October 27?', 0.40, 0.42))
    e2 = er.threshold_edge(m2, 100_000.0, 0.6, NOW, er.EventConfig())
    assert e2['side'] == 'NO' and e2['price'] == pytest.approx(0.60)


def fake_poly(url):
    if 'gamma-api' in url:
        return [{'title': 'BTC Oct', 'slug': 'btc', 'negRisk': False, 'markets': [
                    _mkt('Will Bitcoin reach $110,000 by October 27?', 0.05, 0.07)]},
                {'title': 'Fed decision in October?', 'slug': 'fed', 'negRisk': True, 'markets': [
                    _mkt('Will the Fed cut 25 bps?', 0.30, 0.31, cat='Economics'),
                    _mkt('Will the Fed hold?', 0.30, 0.31, cat='Economics'),
                    _mkt('Will the Fed hike?', 0.30, 0.31, cat='Economics')]}]
    if 'klines' in url:
        return [[0, 0, 0, 0, str(100_000 * math.exp(0.01 * (i % 2)))] for i in range(720)]
    raise AssertionError(url)


def test_event_scan_end_to_end(tmp_path):
    r = er.scan(er.EventConfig(), fake_poly, now=NOW)
    assert r['markets'] == 4 and r['arbs'] and r['arbs'][0]['type'] == 'BUY_ALL_YES'
    assert r['edges'] and r['edges'][0]['symbol'] == 'BTCUSDT'
    assert {m['question'] for m in r['macro']} >= {'Will the Fed hold?'}
    assert er.log_predictions(r, tmp_path / 'p.jsonl') == 1


# ----------------------------------------------------------------------------- runner, report, dashboard
def test_run_once_writes_state_and_report(tmp_path):
    o = rr.run_once(CFG_NT, er.EventConfig(), fake_binance, fake_poly, backtest_symbols=2,
                    state_file=tmp_path / 's.json', report_dir=tmp_path / 'rep',
                    prediction_log=tmp_path / 'p.jsonl', backtest_cache=tmp_path / 'bt.json')
    s = json.loads((tmp_path / 's.json').read_text(encoding='utf-8'))
    assert s['radar']['picks'][0]['symbol'] == 'AAAUSDT' and s['events']['arbs']
    assert s['backtest']['by_tp']['ladder']['n'] >= 1 and (tmp_path / 'bt.json').exists()
    md = next((tmp_path / 'rep').glob('*.md')).read_text(encoding='utf-8')
    assert 'AAAUSDT' in md and 'Arbitrage' in md and 'Base rate' in md
    assert 'AAAUSDT' in rr.console(o)


def test_run_once_survives_network_failure(tmp_path):
    def down(url):
        raise OSError('blocked')
    o = rr.run_once(CFG, er.EventConfig(), down, down, state_file=tmp_path / 's.json', report_dir=None,
                    prediction_log=None, backtest_cache=tmp_path / 'bt.json')
    assert o['radar'] is None and o['events'] is None and len(o['errors']) == 2
    assert json.loads((tmp_path / 's.json').read_text(encoding='utf-8'))['errors']


def test_dashboard_serves_radar_file(tmp_path):
    import http.client
    import threading
    from core.web_ui_server import Telemetry, make_server
    f = tmp_path / 'alpha_radar.json'
    f.write_text(json.dumps({'ts': 1, 'radar': {'picks': []}}), encoding='utf-8')
    server = make_server(Telemetry(), port=0, radar_file=f)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
        c.request('GET', '/api/radar', headers={'Host': '127.0.0.1'})
        r = c.getresponse()
        assert r.status == 200 and json.loads(r.read())['radar'] == {'picks': []}
        f.unlink()
        c.request('GET', '/api/radar', headers={'Host': '127.0.0.1'})
        assert json.loads(c.getresponse().read())['status'] == 'no radar yet'
    finally:
        server.shutdown()


# ----------------------------------------------------------------------------- trend gate + retest
def test_trend_state_and_ema():
    assert ar.trend_state([float(i) for i in range(1, 80)], 50) == 'UP'
    assert ar.trend_state([float(80 - i) for i in range(79)], 50) == 'DOWN'
    assert ar.trend_state([1.0] * 10, 50) is None
    e = ar.ema_series([1.0] * 60, 50)
    assert e[48] is None and e[49] == 1.0 and e[-1] == pytest.approx(1.0)


def test_scan_blocks_long_against_higher_timeframe_downtrend():
    TREND['dir'] = -1
    try:
        r = ar.scan(CFG, fake_binance, now_ms=NOW_MS)
    finally:
        TREND['dir'] = 1
    assert r['picks'] == [] and r['fired'] == 0
    aaa = next(w for w in r['watchlist'] if w['symbol'] == 'AAAUSDT')
    assert aaa['blocked'] == 'TREND' and aaa['trend'] == {'1h': 'DOWN', '4h': 'DOWN'}


def test_trend_unknown_fails_closed():
    def no_history(url):
        if 'interval=1h' in url or 'interval=4h' in url:
            return trend_rows(10, 1)                         # < EMA50 history
        return fake_binance(url)
    r = ar.scan(CFG, no_history, now_ms=NOW_MS)
    assert r['picks'] == []


def test_hourly_trend_is_causal():
    b = ar.parse_klines(trend_rows(1, 1), NOW_MS)             # noqa: F841 (shape check below)
    series = bars(n=900, surge_len=0)
    base = ar.hourly_trend(series, 50)
    changed = [dict(x) for x in series]
    for x in changed[600:]:
        x['c'] *= 3.0                                         # rewrite the future
    assert ar.hourly_trend(changed, 50)[:599] == base[:599]
    assert base[100] is None                                  # < 50 closed hours


def test_retest_zone_sits_in_band_under_the_surge():
    f = ar.features(bars(), CFG)
    rt = f['retest']
    assert f['side'] == 'LONG' and rt['price'] < f['price']
    assert CFG.retest_min_pct <= rt['pullback_pct'] <= CFG.retest_max_pct
    assert set(rt['levels']) >= {'breakout', 'ema20', 'vwap', 'fib382'}
    fs = ar.features(bars(up=False), CFG)
    assert fs['side'] == 'SHORT' and fs['retest']['price'] > fs['price']


def test_simulate_limit_nofill_fill_and_gap():
    up = [_b(1, 1, 1, 1)] + [_b(1.01, 1.02, 1.005, 1.015)] * 6          # never trades down to 0.99
    assert ar.simulate(up, 0, 'LONG', 2.0, 0.8, 5, CFG, limit=0.99, ttl_bars=1) == ('NOFILL', 0.0)
    dip = [_b(1, 1, 1, 1), _b(1.0, 1.0, 0.989, 0.995), _b(0.995, 1.02, 0.994, 1.015)] + [_b(1, 1, 1, 1)] * 5
    res = ar.simulate(dip, 0, 'LONG', 2.0, 0.8, 5, CFG, limit=0.99, ttl_bars=1)
    assert res[0] == 'TP' and res[1] == pytest.approx(2.0 - 2 * CFG.maker_bps / 100)   # maker in, maker out
    gap = [_b(1, 1, 1, 1), _b(0.98, 0.985, 0.975, 0.98)] + [_b(0.98, 0.98, 0.98, 0.98)] * 5
    res = ar.simulate(gap, 0, 'LONG', 2.0, 1.0, 5, CFG, limit=0.99, ttl_bars=1)
    assert res[0] == 'TIME'                                    # filled at the 0.98 open, not at 0.99


def test_simulate_fill_bar_counts_stop_but_not_target():
    both = [_b(1, 1, 1, 1), _b(1.0, 1.05, 0.98, 1.0)] + [_b(1, 1, 1, 1)] * 5
    assert ar.simulate(both, 0, 'LONG', 2.0, 0.8, 5, CFG, limit=0.99, ttl_bars=1)[0] == 'SL'
    high_first = [_b(1, 1, 1, 1), _b(1.0, 1.03, 0.989, 0.995)] + [_b(0.995, 0.996, 0.994, 0.995)] * 5
    assert ar.simulate(high_first, 0, 'LONG', 2.0, 0.8, 5, CFG, limit=0.99, ttl_bars=1)[0] == 'TIME'


def test_maker_entry_plan_widens_stop_slightly_under_same_cap():
    spec = {'step': 1, 'min_qty': 1, 'min_notional': 5}
    taker = ar.trade_plan(0.2, 'LONG', spec, LEGACY)
    maker = ar.trade_plan(0.2, 'LONG', spec, LEGACY, entry_maker=True)
    assert maker['sl_pct'] == pytest.approx(taker['sl_pct'] + (CFG.taker_bps - CFG.maker_bps) / 100)
    assert maker['loss_usdt'] <= LEGACY.max_loss_usdt + 1e-9


# ----------------------------------------------------------------------------- v3: ATR stop, 5x/60 % gate, ladder
def test_v3_defaults_match_spec():
    c = ar.RadarConfig()
    assert (c.surge_min, c.taker_long, c.taker_short, c.breakout_lookback) == (5.0, 0.60, 0.40, 288)
    assert (c.sl_mode, c.atr_sl_mult, c.rr_min, c.tp_pcts, c.max_loss_usdt) == ('atr', 2.0, 2.5, (4.5, 8.0), 0.15)
    assert ar.min_history(c) == 301 and c.bars >= 301


def test_taker_is_required_not_just_confirming():
    weak = rows(n=320)
    for r in weak[-6:]:
        r[10] = str(float(r[7]) * 0.55)                        # 55 % taker buy: below the 60 % gate
    f = ar.features(ar.parse_klines(weak, NOW_MS), CFG)
    assert f['surge_ok'] and f['breakout'] and not f['taker_ok'] and not f['fired']
    assert ar.features(ar.parse_klines(weak, NOW_MS), ar.RadarConfig(require_taker=False))['fired']


def test_surge_under_5x_does_not_fire():
    f = ar.features(bars(), ar.RadarConfig(surge_min=6.0))
    assert f['surge'] < 6.0 and not f['fired']


def test_breakout_must_clear_24h_range():
    b = bars()
    b[-200]['h'] = b[-1]['c'] * 1.01                           # a 24 h high above the 4 h breakout
    assert not ar.features(b, CFG)['breakout']
    assert ar.features(b, ar.RadarConfig(breakout_lookback=48))['breakout']


def test_atr_plan_stop_outside_noise_and_rr():
    spec = {'step': 1, 'min_qty': 1, 'min_notional': 5}
    pl = ar.trade_plan(0.2, 'LONG', spec, CFG, atr=0.8)
    assert pl['tradeable'] and pl['sl_pct'] == pytest.approx(1.6)
    assert pl['tps'][0]['tp_pct'] == 4.5 and pl['tps'][1]['tp_pct'] == 8.0 and pl['tps'][0]['rr'] >= 2.5
    assert pl['loss_usdt'] <= 0.15
    wide = ar.trade_plan(0.2, 'LONG', spec, CFG, atr=1.2)           # stop 2.4 % -> TP1 scaled to 2.5R
    assert wide['tps'][0]['tp_pct'] == pytest.approx(6.0) and wide['tps'][1]['tp_pct'] == pytest.approx(6.0 * 8 / 4.5)
    too = ar.trade_plan(0.2, 'LONG', spec, CFG, atr=3.0)            # 6 % stop costs > 0.15 U: skip, never squeeze
    assert not too['tradeable'] and any('RISK' in w for w in too['warnings'])
    assert not ar.trade_plan(0.2, 'LONG', spec, CFG, atr=None)['tradeable']


def test_ladder_outcomes():
    B = _b
    head = [B(1, 1, 1, 1)]
    both = head + [B(1, 1.05, 1.001, 1.04), B(1.04, 1.09, 1.03, 1.08)] + [B(1, 1, 1, 1)] * 5
    res = ar.simulate_ladder(both, 0, 'LONG', 4.5, 8.0, 2.0, 5, CFG)
    exp = 0.5 * (4.5 - 0.02) + 0.5 * (8.0 - 0.02) - 0.05
    assert res[0] == 'TP1+TP2' and res[1] == pytest.approx(exp)
    be = head + [B(1, 1.05, 1.001, 1.04), B(1.04, 1.04, 0.99, 0.995)] + [B(1, 1, 1, 1)] * 5
    res = ar.simulate_ladder(be, 0, 'LONG', 4.5, 8.0, 2.0, 5, CFG)
    assert res[0] == 'TP1+BE' and res[1] > 2.0                      # half banked, rest out at entry
    sl = head + [B(1, 1.01, 0.97, 0.975)] + [B(1, 1, 1, 1)] * 5
    res = ar.simulate_ladder(sl, 0, 'LONG', 4.5, 8.0, 2.0, 5, CFG)
    assert res[0] == 'SL' and res[1] == pytest.approx(-2.0 - 0.15)
    same_bar = head + [B(1, 1.05, 0.999, 1.0)] + [B(1, 1, 1, 1)] * 5   # TP1 and BE in one bar: counted as BE
    assert ar.simulate_ladder(same_bar, 0, 'LONG', 4.5, 8.0, 2.0, 5, CFG)[0] == 'TP1+BE'


def test_event_study_skips_trades_the_plan_refuses():
    b = bars(n=700, surge_at=400, after=30, after_step=0.004)
    tight = ar.RadarConfig(trend_frames=(), max_loss_usdt=0.02)        # no ATR stop fits 0.02 U
    rows_ = ar.event_study(b, tight)
    assert rows_ and all(r['outcome'] == 'SKIP' for r in rows_)
    st = ar.summarize_study(rows_)
    assert st['n'] == 0 and st['skipped_risk'] == len(rows_)
    ok = ar.event_study(b, CFG_NT)
    assert ok and ok[0]['outcome'].startswith('TP1') and ok[0]['sl_pct'] > 0


def test_fetch_history_pages_backwards():
    calls = []

    def http(url):
        calls.append(url)
        end = int(url.split('endTime=')[1]) if 'endTime=' in url else None
        full = rows(n=40)
        if end is None:
            return full[20:]
        return [r for r in full if r[0] <= end][-20:]
    b = ar.fetch_history('X', http, limit=20, pages=3, now_ms=NOW_MS)
    assert [x['t'] for x in b] == sorted({x['t'] for x in b}) and len(b) == 40 and len(calls) == 3


def test_split_summary_is_time_ordered():
    rows_ = [{'t': i, 'outcome': 'TP', 'net_pct': 1.0 if i < 7 else -1.0} for i in range(10)]
    sp = ar.split_summary(rows_)
    assert sp['is']['n'] == 7 and sp['oos']['n'] == 3 and sp['oos']['mean_net_pct'] == -1.0


def test_polymarket_hot_alerts():
    r = er.scan(er.EventConfig(), fake_poly, now=NOW)
    kinds = {a['kind'] for a in r['alerts']}
    assert 'ARB' in kinds and 'EDGE' in kinds
    assert all(a['edge'] >= 0.15 for a in r['alerts'] if a['kind'] == 'EDGE')
    assert er.scan(er.EventConfig(hot_edge=0.99), fake_poly, now=NOW)['alerts'][0]['kind'] == 'ARB'
