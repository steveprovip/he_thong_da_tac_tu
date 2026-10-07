"""Multi-asset micro-alpha scanner: sizing, signals, maker fills, barriers, kill, telemetry."""
import json

import pytest

from research import micro_scanner as ms
from research.micro_scanner import (DEFAULT_SPECS, Branch, MicroScanner, PendingEntry, ScannerConfig,
                                    SymbolBook, evaluate, maker_fill_on_book, maker_fill_on_trade,
                                    parse_exchange_info, plan_order, round_tick, ws_url)

CFG = ScannerConfig()


def depth(sym, bid, ask, bq=100.0, aq=100.0, levels=5, tick=0.00001):
    bids = [[f'{bid - i * tick:.8f}', str(bq)] for i in range(levels)]
    asks = [[f'{ask + i * tick:.8f}', str(aq)] for i in range(levels)]
    return {'stream': f'{sym.lower()}@depth5@100ms',
            'data': {'e': 'depthUpdate', 's': sym, 'b': bids, 'a': asks}}


def trade(sym, price, qty, buyer_is_maker):
    return {'stream': f'{sym.lower()}@aggTrade',
            'data': {'e': 'aggTrade', 's': sym, 'p': str(price), 'q': str(qty), 'm': buyer_is_maker}}


# ---------------------------------------------------------------- sizing for 3.85 USDT
def test_plan_doge_clears_min_notional_within_margin_and_risk():
    plan, why = plan_order(DEFAULT_SPECS['DOGEUSDT'], 0.2, CFG)
    assert why == 'OK'
    assert plan.notional >= 5.0 and plan.qty == int(plan.qty)
    assert plan.leverage in (2, 3) and plan.margin <= CFG.margin_cap_usdt
    assert CFG.equity - plan.margin >= 1.2                     # free buffer kept
    assert plan.worst_loss_usdt <= CFG.max_trade_loss_usdt


def test_plan_uses_3x_only_when_step_forces_bigger_notional():
    # BNB step 0.01 @ 700 -> 7 USDT notional: 2x margin 3.5 > cap, 3x margin 2.33 fits.
    plan, why = plan_order(DEFAULT_SPECS['BNBUSDT'], 700.0, CFG)
    assert why == 'OK' and plan.leverage == 3 and plan.margin <= CFG.margin_cap_usdt


@pytest.mark.parametrize('sym,price', [('BTCUSDT', 60000.0), ('ETHUSDT', 3000.0)])
def test_leaders_are_not_routable_for_small_account(sym, price):
    plan, why = plan_order(DEFAULT_SPECS[sym], price, CFG)
    assert plan is None and why == 'MIN_NOTIONAL'


def test_exchange_info_parsing_overrides_filters():
    info = {'symbols': [{'symbol': 'DOGEUSDT', 'filters': [
        {'filterType': 'PRICE_FILTER', 'tickSize': '0.000010'},
        {'filterType': 'LOT_SIZE', 'stepSize': '1', 'minQty': '1'},
        {'filterType': 'MIN_NOTIONAL', 'notional': '5'}]}, {'symbol': 'OTHER', 'filters': []}]}
    specs = parse_exchange_info(info, ['DOGEUSDT'])
    assert specs['DOGEUSDT'].tick == 0.00001 and specs['DOGEUSDT'].min_notional == 5.0


def test_ws_url_carries_depth_and_flow_for_every_symbol():
    url = ws_url(['DOGEUSDT', 'BTCUSDT'])
    assert url.startswith('wss://fstream.binance.com/stream?streams=')
    for s in ('dogeusdt@depth5@100ms', 'dogeusdt@aggTrade', 'btcusdt@depth5@100ms', 'btcusdt@aggTrade'):
        assert s in url


# ---------------------------------------------------------------- book features
def test_obi_l5_and_ema_converge():
    b = SymbolBook('DOGEUSDT', CFG)
    for i in range(40):
        b.on_depth([['0.2', '300']] * 5, [['0.20001', '100']] * 5, 1000 + i * 0.1)
    assert b.obi == pytest.approx(0.5)
    assert b.obi_ema == pytest.approx(0.5, abs=0.03)
    assert b.obi_source == 'L5'


def test_cvd_sign_imbalance_and_burst():
    b = SymbolBook('DOGEUSDT', CFG)
    for s in range(120):                                   # quiet balanced baseline
        b.on_trade(0.2, 100, s % 2 == 0, 1000 + s)
    for k in range(40):                                    # 10 s aggressive buying burst
        b.on_trade(0.2, 500, False, 1120 + k * 0.25)
    fl = b.flow(1130)
    assert fl['cvd_60s'] > 0 and fl['imbalance'] > 0.2
    assert fl['burst'] > CFG.cvd_burst_ratio


def test_lead_lag_votes_leader_direction_when_alt_lags():
    books = {s: SymbolBook(s, CFG) for s in ('BTCUSDT', 'DOGEUSDT')}
    for i in range(121):                                   # 12 s of history
        t = 1000 + i * 0.1
        btc = 60000 * (1 + (0.0012 if i > 100 else 0.0))   # +12 bps jump in last 2 s
        books['BTCUSDT'].on_depth([[str(btc), '1']], [[str(btc + 0.1), '1']], t)
        books['DOGEUSDT'].on_depth([['0.2', '100']], [['0.20001', '100']], t)
    sig = evaluate(books['DOGEUSDT'], books, ['BTCUSDT'], CFG, 1012.0)
    assert sig.components['votes'].get('lead') == 1
    assert sig.votes == 2                                  # 12 bps >= 1.5 x 6 -> strong lead counts 2
    assert sig.side == 'BUY' and sig.status == 'SIGNAL'


def test_no_lead_vote_when_alt_already_moved():
    books = {s: SymbolBook(s, CFG) for s in ('BTCUSDT', 'DOGEUSDT')}
    for i in range(121):
        t = 1000 + i * 0.1
        k = 1 + (0.0012 if i > 100 else 0.0)
        books['BTCUSDT'].on_depth([[str(60000 * k), '1']], [[str(60000 * k + 0.1), '1']], t)
        books['DOGEUSDT'].on_depth([[str(0.2 * k), '100']], [[str(0.2 * k + 0.00001), '100']], t)
    sig = evaluate(books['DOGEUSDT'], books, ['BTCUSDT'], CFG, 1012.0)
    assert 'lead' not in sig.components['votes']


def test_opposing_vote_blocks_signal():
    books = {s: SymbolBook(s, CFG) for s in ('BTCUSDT', 'DOGEUSDT')}
    for i in range(121):
        t = 1000 + i * 0.1
        btc = 60000 * (1 + (0.0008 if i > 100 else 0.0))   # +8 bps: normal-weight lead
        books['BTCUSDT'].on_depth([[str(btc), '1']], [[str(btc + 0.1), '1']], t)
        books['DOGEUSDT'].on_depth([['0.2', '10']] * 5, [['0.20001', '200']] * 5, t)  # OBI ~ -0.9
    sig = evaluate(books['DOGEUSDT'], books, ['BTCUSDT'], CFG, 1012.0)
    assert sig.components['votes'] == {'obi': -1, 'lead': 1}
    assert sig.status != 'SIGNAL'


# ---------------------------------------------------------------- maker fill model
def _pe(side='BUY', price=0.2, queue=50.0):
    plan, _ = plan_order(DEFAULT_SPECS['DOGEUSDT'], price, CFG)
    return PendingEntry('DOGEUSDT', side, price, plan, 0.0, queue, {})


def test_maker_fill_queue_then_trade_through():
    pe = _pe()
    assert not maker_fill_on_trade(pe, 0.2, 30, True)      # 20 still ahead of us
    assert not maker_fill_on_trade(pe, 0.2, 10, False)     # buyer aggressor does not hit our bid
    assert maker_fill_on_trade(pe, 0.2, 25, True)          # queue consumed
    assert maker_fill_on_trade(_pe(), 0.19999, 1, True)    # trade-through


def test_maker_fill_on_book_is_side_aware():
    assert maker_fill_on_book(_pe('BUY'), 0.19999, 0.2)    # bid fell through our level
    assert not maker_fill_on_book(_pe('BUY'), 0.2, 0.20001)
    assert maker_fill_on_book(_pe('SELL'), 0.2, 0.20001)   # ask lifted through our level
    assert not maker_fill_on_book(_pe('SELL'), 0.19999, 0.2)


# ---------------------------------------------------------------- triple barrier
def _branch(trigger=None, protect=None):
    b = Branch('x', trigger, protect, CFG)
    b.open('DOGEUSDT', 'BUY', 0.2, 26, 0.00001, 0.0)
    return b


def test_tp_nets_35_bps_after_two_maker_legs():
    b = _branch()
    t = b.update('DOGEUSDT', b.pos.tp, b.pos.tp + 0.00001, 10.0)
    assert t.reason == 'TP' and t.net_bps == pytest.approx(35.0, abs=0.6)


def test_sl_nets_about_minus_29():
    b = _branch()
    t = b.update('DOGEUSDT', b.pos.sl, b.pos.sl + 0.00001, 10.0)
    assert t.reason == 'SL' and t.net_bps == pytest.approx(-29.0, abs=0.6)


def test_breakeven_lock_exits_at_plus_one_net():
    b = _branch(15.0, 10.0)
    assert b.update('DOGEUSDT', 0.2 * 1.0016, 0.2 * 1.0017, 5.0) is None     # MFE 16 bps
    assert b.pos.protected and b.pos.sl > 0.2
    t = b.update('DOGEUSDT', 0.2002, 0.20021, 6.0)            # touches the +10 bps stop
    assert t.reason == 'PROTECT' and t.net_bps == pytest.approx(1.0, abs=0.6)


def test_gap_through_stop_fills_at_quote_not_at_stop():
    b = _branch(15.0, 10.0)
    b.update('DOGEUSDT', 0.20032, 0.20033, 5.0)
    t = b.update('DOGEUSDT', 0.2001, 0.20011, 6.0)            # gapped below the +10 stop
    assert t.reason == 'PROTECT' and t.net_bps < 1.0


def test_time_barrier_closes_at_max_hold():
    b = _branch()
    assert b.update('DOGEUSDT', 0.20002, 0.20003, CFG.max_hold_s - 1) is None
    t = b.update('DOGEUSDT', 0.20002, 0.20003, CFG.max_hold_s)
    assert t.reason == 'TIME' and t.hold_s == CFG.max_hold_s


def test_short_mirrors_long():
    b = Branch('x', None, None, CFG)
    b.open('DOGEUSDT', 'SELL', 0.2, 26, 0.00001, 0.0)
    assert b.pos.tp < 0.2 < b.pos.sl
    t = b.update('DOGEUSDT', b.pos.tp - 0.00001, b.pos.tp, 3.0)
    assert t.reason == 'TP' and t.net_bps > 30


def test_branch_refuses_overwrite():
    b = _branch()
    with pytest.raises(RuntimeError):
        b.open('DOGEUSDT', 'BUY', 0.2, 26, 0.00001, 1.0)


def test_round_tick_handles_tiny_ticks():
    assert round_tick(0.0000123456, 0.0000001) == pytest.approx(0.0000123)


# ---------------------------------------------------------------- end to end
def _warm(sc, t0, seconds=20.0, doge_bq=100.0, doge_aq=100.0, btc_jump_at=None):
    t = t0
    while t < t0 + seconds:
        btc = 60000.0 * (1.0012 if btc_jump_at is not None and t >= btc_jump_at else 1.0)
        sc.on_message(depth('BTCUSDT', btc, btc + 0.1, tick=0.1), t)
        sc.on_message(depth('DOGEUSDT', 0.2, 0.20001, doge_bq, doge_aq), t)
        sc.step(t)
        t = round(t + 0.1, 3)
    return t


def test_end_to_end_signal_fill_close_and_telemetry(tmp_path):
    state = tmp_path / 'state.json'
    log = tmp_path / 'trades.jsonl'
    sc = MicroScanner(CFG, symbols=['DOGEUSDT', 'BTCUSDT'], state_file=state, trade_log=log)
    t = _warm(sc, 1000.0, 20.0, doge_bq=300.0, doge_aq=60.0, btc_jump_at=1018.0)
    assert sc.pending is not None and sc.pending.symbol == 'DOGEUSDT' and sc.pending.side == 'BUY'
    assert sc.intents == 1
    # sellers hit our bid through the queue -> maker fill, all three branches open together
    sc.on_message(trade('DOGEUSDT', 0.2, 10000, True), t)
    assert sc.fills == 1 and all(b.pos is not None for b in sc.branches.values())
    # price rips to TP
    tp = sc.branches['baseline'].pos.tp
    sc.on_message(depth('DOGEUSDT', tp, tp + 0.00001, tick=0.00001), t + 1)
    sc.step(t + 1)
    assert all(b.pos is None for b in sc.branches.values())
    assert sc.branches['baseline'].counts['TP'] == 1
    assert sc.equity() > CFG.equity
    sc.publish(t + 1)
    s = json.loads(state.read_text(encoding='utf-8'))
    assert {m['symbol'] for m in s['markets']} == {'DOGEUSDT', 'BTCUSDT'}
    btc = next(m for m in s['markets'] if m['symbol'] == 'BTCUSDT')
    assert btc['status'] == 'LEAD_ONLY' and 'MIN_NOTIONAL' in btc['reasons']
    assert s['branches']['be_15_10']['tp_count'] == 1 and len(s['trades']) == 3
    assert s['history'] and s['pnl'] > 0 and s['scanner']['fills'] == 1
    assert len(log.read_text(encoding='utf-8').strip().splitlines()) == 3


def test_unfilled_gtx_is_cancelled_after_ttl(tmp_path):
    sc = MicroScanner(CFG, symbols=['DOGEUSDT', 'BTCUSDT'], state_file=tmp_path / 's.json')
    t = _warm(sc, 1000.0, 20.0, doge_bq=300.0, doge_aq=60.0, btc_jump_at=1018.0)
    assert sc.pending is not None
    for k in range(int(CFG.entry_ttl_s / 0.25) + 2):        # price holds at our bid, nobody sells
        sc.on_message(depth('DOGEUSDT', 0.2, 0.20001, 300, 60), t + k * 0.25)
        sc.step(t + k * 0.25)
    assert sc.pending is None and sc.cancels == 1 and sc.fills == 0


def test_kill_switch_flattens_and_halts(tmp_path):
    cfg = ScannerConfig(kill_loss_usdt=0.005)                # tiny kill to trip on one stop
    sc = MicroScanner(cfg, symbols=['DOGEUSDT', 'BTCUSDT'], state_file=tmp_path / 's.json')
    t = _warm(sc, 1000.0, 20.0, doge_bq=300.0, doge_aq=60.0, btc_jump_at=1018.0)
    sc.on_message(trade('DOGEUSDT', 0.19999, 1, True), t)   # trade-through fill
    assert sc.fills == 1
    sc.on_message(depth('DOGEUSDT', 0.1998, 0.19981), t + 1)  # -10 bps: MTM loss > 0.005
    status = sc.step(t + 1)
    assert status == 'HALTED' and sc.killed
    assert all(b.pos is None for b in sc.branches.values())
    assert sc.step(t + 2) == 'HALTED' and sc.pending is None


def test_one_cohort_at_a_time(tmp_path):
    sc = MicroScanner(CFG, symbols=['DOGEUSDT', 'XRPUSDT', 'BTCUSDT'], state_file=tmp_path / 's.json')
    t = 1000.0
    while t < 1020.0:
        btc = 60000.0 * (1.0012 if t >= 1018.0 else 1.0)
        sc.on_message(depth('BTCUSDT', btc, btc + 0.1, tick=0.1), t)
        sc.on_message(depth('DOGEUSDT', 0.2, 0.20001, 300, 60), t)
        sc.on_message(depth('XRPUSDT', 0.5, 0.5001, 300, 60, tick=0.0001), t)
        sc.step(t)
        t = round(t + 0.1, 3)
    assert sc.intents == 1
    other = 'XRPUSDT' if sc.pending.symbol == 'DOGEUSDT' else 'DOGEUSDT'
    assert sc.signals[other].status == 'SIGNAL_BLOCKED' and 'BUSY' in sc.signals[other].blockers


def test_stale_feed_never_signals(tmp_path):
    sc = MicroScanner(CFG, symbols=['DOGEUSDT', 'BTCUSDT'], state_file=tmp_path / 's.json')
    _warm(sc, 1000.0, 20.0, doge_bq=300.0, doge_aq=60.0)
    sc.pending = None
    assert sc.step(1100.0) == 'WAITING_FEED'
    assert all(s.status == 'STALE' for s in sc.signals.values())


def test_shutdown_file_detection(tmp_path):
    f = tmp_path / 'shutdown_request.json'
    assert not ms.shutdown_requested(0.0, f)
    f.write_text('{}', encoding='utf-8')
    assert ms.shutdown_requested(0.0, f)
    assert not ms.shutdown_requested(f.stat().st_mtime + 10, f)


def test_cli_defaults_match_mission_spec():
    cfg = ms.build_config(ms.build_parser().parse_args([]))
    assert cfg.equity == 3.85 and cfg.kill_loss_usdt == 0.05 and cfg.obi_threshold == 0.55
    assert cfg.tp_gross_bps - 2 * cfg.maker_fee_bps == 35.0 and cfg.sl_bps == 20.0
    assert set(cfg.leverage_choices) == {2, 3}


# ---------------------------------------------------------------- replay + dashboard wiring
def test_replay_drives_same_engine_from_recorded_book(tmp_path):
    from tools import replay_micro_scanner as rp
    rows = {'BTCUSDT': [], 'DOGEUSDT': []}
    for i in range(300):                                    # 30 s of 100 ms book updates
        t = 1000 + i * 0.1
        btc = 60000.0 * (1.0012 if t >= 1020 else 1.0)
        rows['BTCUSDT'].append({'lt': t, 'ch': 'bookTicker', 'e': 'bookTicker', 's': 'BTCUSDT',
                                'b': str(btc), 'B': '5', 'a': str(btc + 0.1), 'A': '5'})
        rows['DOGEUSDT'].append({'lt': t, 'ch': 'bookTicker', 'e': 'bookTicker', 's': 'DOGEUSDT',
                                 'b': '0.2', 'B': '9000', 'a': '0.20001', 'A': '1000'})
    paths = []
    for sym, lines in rows.items():
        d = tmp_path / sym
        d.mkdir()
        p = d / '20260101.jsonl'
        p.write_text('\n'.join(json.dumps(r) for r in lines) + '\n{"truncated', encoding='utf-8')
        paths.append(str(p))
    sc, symbols, n, t0, t1 = rp.replay(paths, CFG, state_file=tmp_path / 's.json')
    assert symbols == ['BTCUSDT', 'DOGEUSDT'] and n == 600
    assert sc.books['DOGEUSDT'].obi_source == 'L1'
    assert sc.intents >= 1                                  # L1 OBI + strong BTC lead -> GTX intent


def test_dashboard_shutdown_writes_flag_file_for_scanner(tmp_path):
    import http.client
    import threading
    from core.web_ui_server import Telemetry, make_server
    flag = tmp_path / 'shutdown_request.json'
    server = make_server(Telemetry(), port=0, shutdown_file=flag)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        conn = http.client.HTTPConnection('127.0.0.1', server.server_address[1], timeout=3)
        body = json.dumps({'confirm': 'SHUTDOWN'})
        conn.request('POST', '/api/shutdown', body, {'Content-Type': 'application/json',
                                                      'Host': '127.0.0.1'})
        assert conn.getresponse().status == 200
    finally:
        server.shutdown()
    assert flag.exists() and ms.shutdown_requested(0.0, flag)
