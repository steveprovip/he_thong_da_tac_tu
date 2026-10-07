"""Fast lane: liquidation cascade sniper (shadow) + Polymarket 15m fair-value scanner + /api/fast."""
import json
import math

import pytest

from research import fast_liquidation_sniper as fls
from research import polymarket_15m_fast_arb as pm

CFG = fls.SniperConfig()
SPEC = {'step': 1.0, 'min_qty': 1.0, 'min_notional': 5.0, 'tick': 0.0001}


def liq(sym='DOGEUSDT', side='SELL', qty=100_000, price=0.2, t_ms=1_000_000):
    return {'stream': '!forceOrder@arr', 'data': {'e': 'forceOrder', 'E': t_ms, 'o': {
        's': sym, 'S': side, 'o': 'LIMIT', 'f': 'IOC', 'q': str(qty), 'p': str(price), 'ap': str(price),
        'X': 'FILLED', 'l': str(qty), 'z': str(qty), 'T': t_ms}}}


def marks(pairs, t_ms=1_000_000):
    return {'stream': '!markPrice@arr@1s',
            'data': [{'e': 'markPriceUpdate', 'E': t_ms, 's': s, 'p': str(p)} for s, p in pairs]}


def sniper_with_wick(drop=0.006, sym='DOGEUSDT'):
    s = fls.Sniper(CFG, {sym: SPEC})
    for i in range(20):                                      # 20 s at 0.20 then a wick down
        s.on_message(marks([(sym, 0.2)]), 1000.0 + i)
    s.on_message(marks([(sym, 0.2 * (1 - drop))]), 1021.0)
    return s


# ----------------------------------------------------------------------------- sniper
def test_parse_force_order_direction_and_notional():
    ev = fls.parse_force_order(liq(side='SELL', qty=1000, price=2.0))
    assert ev['liquidated'] == 'LONG' and ev['notional'] == pytest.approx(2000.0)
    assert fls.parse_force_order(liq(side='BUY'))['liquidated'] == 'SHORT'
    assert fls.parse_force_order({'data': {'e': 'aggTrade'}}) is None
    assert fls.parse_force_order({'data': {'e': 'forceOrder', 'o': {'s': 'X'}}}) is None


def test_cascade_window_sums_one_side_and_expires():
    d = fls.CascadeDetector(CFG)
    d.add(fls.parse_force_order(liq(qty=100_000)), 100.0)          # $20k longs
    d.add(fls.parse_force_order(liq(side='BUY', qty=500_000)), 101.0)  # shorts: other bucket
    tot, n = d.add(fls.parse_force_order(liq(qty=200_000)), 102.0)   # +$40k longs
    assert tot == pytest.approx(60_000) and n == 2
    assert d.total('DOGEUSDT', 'LONG', 106.5)[0] == pytest.approx(40_000)   # first one aged out of 5 s
    assert d.total('DOGEUSDT', 'LONG', 200.0) == (0, 0)


def test_cascade_with_wick_opens_counter_trend_buy():
    s = sniper_with_wick()
    cas = s.on_message(liq(qty=300_000, price=0.1988), 1022.0)      # $59.6k longs liquidated
    assert cas['action'] == 'BUY' and s.pos['side'] == 'BUY'
    p = s.pos
    assert p['qty'] * p['entry'] >= 5.0 and p['plan']['leverage'] in (2, 3) and p['plan']['margin'] <= 2.6
    loss_at_stop = p['qty'] * p['entry'] * (p['plan']['sl_pct'] + (2 * CFG.taker_bps + 2 * CFG.slip_bps) / 100) / 100
    assert loss_at_stop <= CFG.max_loss + 1e-9
    assert p['tp'] == pytest.approx(p['entry'] * 1.01)


def test_short_liquidations_open_sell():
    s = fls.Sniper(CFG, {'DOGEUSDT': SPEC})
    for i in range(20):
        s.on_message(marks([('DOGEUSDT', 0.2)]), 1000.0 + i)
    s.on_message(marks([('DOGEUSDT', 0.2012)]), 1021.0)
    cas = s.on_message(liq(side='BUY', qty=300_000, price=0.2012), 1022.0)
    assert cas['action'] == 'SELL' and s.pos['side'] == 'SELL' and s.pos['tp'] < s.pos['entry']


@pytest.mark.parametrize('why,setup', [
    ('NO_WICK', lambda: fls.Sniper(CFG, {'DOGEUSDT': SPEC})),
    ('NO_SPEC', lambda: sniper_with_wick(sym='DOGEUSDT').__class__(CFG, {})),
])
def test_cascade_blocked_without_wick_or_spec(why, setup):
    s = setup()
    for i in range(22):
        s.on_message(marks([('DOGEUSDT', 0.2)]), 1000.0 + i)
    cas = s.on_message(liq(qty=300_000), 1022.0)
    assert cas['action'].startswith(why) and s.pos is None


def test_small_liquidations_do_not_trigger_and_feed_filters():
    s = sniper_with_wick()
    assert s.on_message(liq(qty=10_000), 1022.0) is None             # $2k
    assert s.pos is None and not s.feed and s.stats['liquidations'] == 1


def test_one_position_and_cooldown():
    s = sniper_with_wick()
    s.on_message(liq(qty=300_000), 1022.0)
    s.on_message(marks([('XRPUSDT', 0.5)]), 1022.0)
    s.specs['XRPUSDT'] = SPEC
    cas = s.on_message(liq(sym='XRPUSDT', qty=200_000, price=0.5), 1022.5)
    assert cas['action'] == 'BUSY'
    s.pos = None
    cas = s.on_message(liq(qty=300_000), 1030.0)
    assert cas['action'] == 'COOLDOWN'


def test_exits_tp_sl_time():
    s = sniper_with_wick()
    s.on_message(liq(qty=300_000), 1022.0)
    tp = s.pos['tp']
    s.on_message(marks([('DOGEUSDT', tp)]), 1030.0)
    t = s.step(1030.0)
    assert t['reason'] == 'TP' and t['net_pct'] == pytest.approx(1.0 - (CFG.taker_bps + CFG.maker_bps) / 100)

    s = sniper_with_wick()
    s.on_message(liq(qty=300_000), 1022.0)
    sl = s.pos['sl']
    s.on_message(marks([('DOGEUSDT', sl)]), 1025.0)
    t = s.step(1025.0)
    assert t['reason'] == 'SL' and -CFG.max_loss - 1e-9 <= t['pnl'] < 0

    s = sniper_with_wick()
    s.on_message(liq(qty=300_000), 1022.0)
    assert s.step(1050.0) is None
    t = s.step(1022.0 + CFG.max_hold_s)
    assert t['reason'] == 'TIME' and t['hold_s'] == pytest.approx(CFG.max_hold_s)
    assert s.summary()['n'] == 1


def test_snapshot_is_json_safe():
    s = sniper_with_wick()
    s.on_message(liq(qty=300_000), 1022.0)
    snap = s.snapshot(1023.0)
    json.dumps(snap, allow_nan=False)
    assert snap['position']['symbol'] == 'DOGEUSDT' and snap['feed'][0]['notional'] >= CFG.big_usd
    assert snap['cascades'][0]['usd_window'] >= CFG.cascade_usd


# ----------------------------------------------------------------------------- polymarket 15m
def test_window_and_slug():
    assert pm.window_start(1768502700 + 899.9) == 1768502700
    assert pm.slug('btc', 1768502700) == 'btc-updown-15m-1768502700'


def test_p_up_symmetry_monotonic_and_twap():
    assert pm.p_up(100, 100, 0.001, 10) == pytest.approx(0.5)
    hi, lo = pm.p_up(100 * math.exp(0.003), 100, 0.001, 10), pm.p_up(100 * math.exp(-0.003), 100, 0.001, 10)
    assert hi > 0.5 > lo and hi + lo == pytest.approx(1.0, abs=1e-9)
    assert pm.p_up(100.3, 100, 0.001, 1) > pm.p_up(100.3, 100, 0.001, 10)     # less time -> more certain
    assert pm.p_up(100.3, 100, 0.001, 0) == 1.0
    # TWAP averaging lowers terminal variance vs a spot print at the bell
    x = math.log(100.1 / 100)
    assert pm.p_up(100.1, 100, 0.001, 1.0) == pytest.approx(pm.norm_cdf(x / (0.001 * math.sqrt(1 / 3))))


def test_evaluate_flags_only_after_fee():
    book = lambda ask: {'asks': [{'price': str(ask), 'size': '100'}], 'bids': [{'price': str(ask - 0.02), 'size': '50'}]}
    start, now = 1_000_000_200, 1_000_000_200 + 600                # 5 min left
    r = pm.evaluate('btc', start, now, 100.3, 100.0, 0.0008, book(0.40), book(0.62), pm.Pm15Config())
    assert r['p_up'] > 0.9 and r['side'] == 'UP' and r['flag']
    assert r['edge'] == pytest.approx(r['p_up'] - 0.40 - 0.07 * 0.4 * 0.6)
    fair = pm.evaluate('btc', start, now, 100.3, 100.0, 0.0008, book(r['p_up'] - 0.01), book(0.2),
                       pm.Pm15Config())
    assert not fair['flag']                                       # 1pp under fair is eaten by the fee
    late = pm.evaluate('btc', start, start + 890, 100.3, 100.0, 0.0008, book(0.40), book(0.62), pm.Pm15Config())
    assert not late['flag']                                       # < 20 s to the bell


def test_market_tokens_from_gamma_strings():
    ev = {'markets': [{'outcomes': '["Up", "Down"]', 'clobTokenIds': '["111", "222"]'}]}
    assert pm.market_tokens(ev) == {'UP': '111', 'DOWN': '222'}
    assert pm.market_tokens({'markets': [{'outcomes': '["Yes","No"]', 'clobTokenIds': '["1","2"]'}]}) is None


def fake_http(now):
    start = pm.window_start(now)

    def http(url):
        if 'gamma-api' in url:
            assert f'updown-15m-{start}' in url
            return [{'markets': [{'outcomes': '["Up","Down"]', 'clobTokenIds': '["U","D"]'}]}]
        if 'klines' in url:
            rows = []
            for i in range(121):
                t = (start - 100 * 60 + i * 60) * 1000
                p = 100.0 * (1 + 0.0005 * math.sin(i))
                rows.append([t, str(100.0 if t // 1000 == start else p), '0', '0', str(p)])
            return rows
        if 'ticker/price' in url:
            return {'price': '100.4'}
        if 'book?token_id=U' in url:
            return {'asks': [{'price': '0.45', 'size': '200'}], 'bids': [{'price': '0.43', 'size': '10'}]}
        if 'book?token_id=D' in url:
            return {'asks': [{'price': '0.58', 'size': '200'}], 'bids': []}
        raise AssertionError(url)
    return http


def test_scan_once_end_to_end():
    now = 1_768_502_700 + 400
    r = pm.scan_once(pm.Pm15Config(assets=('btc',)), fake_http(now), now=now)
    assert not r['errors'] and r['rows'][0]['open'] == 100.0 and r['rows'][0]['side'] == 'UP'
    assert r['rows'][0]['flag'] and r['rows'][0]['up_ask'] == 0.45


def test_ledger_records_once_and_scores_after_settlement(tmp_path):
    lg = pm.SignalLedger(tmp_path / 'pm.jsonl')
    row = {'slug': 's', 'side': 'UP', 'flag': True, 'start': 0, 'asset': 'btc', 'open': 100.0, 'price': 0.45,
           'edge': 0.3}
    assert lg.record(row) and not lg.record(row)
    assert lg.settle(500.0, lambda a, e: 101.0) == []                # window not over yet
    done = lg.settle(900 + 91.0, lambda a, e: 101.0)
    assert done[0]['won'] and done[0]['pnl_per_share'] == pytest.approx(1 - 0.45 - 0.07 * 0.45 * 0.55)
    sc = lg.score()
    assert sc['n'] == 1 and sc['hit_rate'] == 1.0 and (tmp_path / 'pm.jsonl').exists()


# ----------------------------------------------------------------------------- runner + dashboard
def test_runner_state_is_json_safe(tmp_path):
    from research import fast_runner as fr
    s = sniper_with_wick()
    s.on_message(liq(qty=300_000), 1022.0)
    fr.write_state(tmp_path / 'f.json', fr.build_state(s, {'rows': [{'edge': float('nan')}]}, pm.SignalLedger(),
                                                      {'connected': True}, 1023.0))
    st = json.loads((tmp_path / 'f.json').read_text(encoding='utf-8'))
    assert st['pm15']['rows'][0]['edge'] is None and st['sniper']['position']['side'] == 'BUY'


def test_dashboard_serves_fast_file(tmp_path):
    import http.client
    import threading
    from core.web_ui_server import Telemetry, make_server
    f = tmp_path / 'fast.json'
    f.write_text(json.dumps({'updated_at': 1, 'sniper': {}}), encoding='utf-8')
    server = make_server(Telemetry(), port=0, fast_file=f)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        c = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
        c.request('GET', '/api/fast', headers={'Host': '127.0.0.1'})
        assert json.loads(c.getresponse().read())['sniper'] == {}
        f.unlink()
        c.request('GET', '/api/fast', headers={'Host': '127.0.0.1'})
        assert json.loads(c.getresponse().read())['status'] == 'no fast lane yet'
    finally:
        server.shutdown()
