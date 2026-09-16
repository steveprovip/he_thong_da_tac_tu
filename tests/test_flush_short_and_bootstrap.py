"""Short side and the 15m bootstrap that makes the regime gate real.

Two things are proved here. First, the ledger is symmetric: a short pays the
same fees, takes one slippage step, tightens its stop the other way, and loses
money when price rises. Second, the 15m series actually reaches the engine --
without it classify_regime returns TRANSITION forever and the filter blocks
nothing while appearing to be installed.
"""
import json
import time

import pytest

import research.flush_shadow as module
from research.flush_regime import DOWN, RANGE, TRANSITION
from research.flush_shadow import (MAKER_FEE_BPS, SLIPPAGE_BPS, TAKER_FEE_BPS, TP_BPS,
                                   FlushShadowEngine, ShadowBranch, _parse_klines,
                                   fetch_rest_market_data, round_tick)

E = 0.20000
QTY = 61.0


def at(bps, d=1):
    return round_tick(E * (1 + d * bps / 1e4))


def short(name='baseline', trigger=None, protect=None):
    b = ShadowBranch(name, trigger_bps=trigger, protect_sl_bps=protect)
    b.open_position(0.0, E, QTY, 0.0, 0.0, side='SELL')
    return b


def bar15(o, h, l, c, t=0, v=1000.0):
    return {'time': t, 'open': o, 'high': h, 'low': l, 'close': c, 'volume': v}


def down_bars15(n=90, start=0.20000, step=-0.0006, rng=0.0008):
    out, prev = [], None
    for i in range(n):
        c = start + i * step
        o = prev if prev is not None else c
        out.append(bar15(o, max(o, c) + rng / 2, min(o, c) - rng / 2, c, t=i))
        prev = c
    return out


def range_bars15(n=90, mid=0.20000, band=0.0010):
    offsets = (band * 0.3, -band * 0.25, 0.0)
    return [bar15(mid, mid + band, mid - band, mid + offsets[i % 3], t=i) for i in range(n)]


# ============================================================ short geometry
def test_a_short_mirrors_the_long_about_the_entry():
    pos = short().active_pos
    assert pos.side == 'SELL' and pos.d == -1
    assert pos.tp_price < E < pos.sl_price, 'tp below, stop above'


def test_an_unknown_side_is_refused_rather_than_assumed():
    with pytest.raises(ValueError):
        ShadowBranch('baseline').open_position(0.0, E, QTY, 0.0, 0.0, side='LONG')


def test_short_take_profit_pays_maker_on_both_legs():
    branch = short()
    trade = branch.update(E, 1.0, cur_ask=branch.active_pos.tp_price)['trade']
    assert trade.reason == 'TP_REACHED_GTX'
    assert trade.fee_bps == MAKER_FEE_BPS + MAKER_FEE_BPS == 4.0
    assert trade.gross_bps == pytest.approx(TP_BPS, abs=0.5), 'profit when price FALLS'
    assert trade.pnl_usdt > 0


def test_short_stop_pays_taker_and_one_slippage_step():
    branch = short()
    trade = branch.update(E, 1.0, cur_ask=branch.active_pos.sl_price)['trade']
    assert trade.reason == 'ORIGINAL_SL_TRIGGERED'
    assert trade.fee_bps == MAKER_FEE_BPS + TAKER_FEE_BPS == 7.0
    # -20.44 structural less one 2 bps slippage step, never two.
    assert trade.gross_bps == pytest.approx(-22.44, abs=0.1)
    assert trade.net_bps == pytest.approx(trade.gross_bps - 7.0)
    assert trade.pnl_usdt < 0


def test_a_short_exits_on_the_ask_not_the_bid():
    """Bid-only evaluation would hold a short through its own stop."""
    branch = short()
    assert branch.update(E, 1.0) is not None and branch.active_pos is not None
    assert branch.update(E, 2.0, cur_ask=branch.active_pos.sl_price)['status'] == 'TRADE_CLOSED'


def test_short_protection_tightens_downward():
    branch = short('be_15_10', trigger=15.0, protect=10.0)
    original = branch.active_pos.sl_price
    armed = branch.update(E, 1.0, cur_ask=at(16.0, -1))
    assert armed['protected'] is True
    assert branch.active_pos.sl_price < original, 'a short stop tightens by moving DOWN'
    assert branch.active_pos.sl_price < E

    closed = branch.update(E, 2.0, cur_ask=at(9.0, -1))['trade']
    assert closed.reason == 'PROTECTION_SL_HIT'
    assert closed.gross_bps == pytest.approx(7.0, abs=0.1)


def test_short_premature_watcher_tracks_the_original_target():
    branch = short('be_15_10', trigger=15.0, protect=10.0)
    branch.update(E, 1.0, cur_ask=at(16.0, -1))
    branch.update(E, 2.0, cur_ask=at(9.0, -1))
    assert len(branch.watchers) == 1 and branch.watchers[0].side == 'SELL'
    branch.update(E, 3.0, cur_ask=at(45.0, -1))     # price keeps falling to the target
    assert branch.premature_count == 1 and branch.watchers == []


def test_short_forced_exit_loses_when_price_rises():
    branch = short()
    trade = branch.force_exit(E, 9.0, 'KILL_FORCED_EXIT', cur_ask=at(30.0))
    assert trade.net_bps < 0 and trade.fee_bps == 7.0
    assert trade.exit_price > E, 'buying back higher than the short entry'


def test_the_long_path_is_untouched_by_the_short_work():
    branch = ShadowBranch('baseline')
    branch.open_position(0.0, E, QTY, 0.0, 0.0)
    trade = branch.update(at(TP_BPS), 1.0)['trade']
    assert trade.fee_bps == 4.0 and trade.gross_bps > 0


# =========================================================== engine wiring
def test_kill_flattens_a_short_cohort_using_the_ask():
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.open_shadow_position(1000.0, E, QTY, 0.0, 0.0, side='SELL')
    spike = at(250.0)
    result = engine.update_cycle({'bidPrice': str(spike - 0.00001), 'askPrice': str(spike)}, [])
    assert result['status'] == 'HALTED_FLAT'
    assert all(b.active_pos is None for b in engine.shadow_branches.values())
    assert all(b.trades[-1].net_bps < 0 for b in engine.shadow_branches.values())


def test_liquidation_equity_charges_slippage_as_well_as_fees():
    """Spec 2.2: the old figure reserved 7 bps and forgot the 2 bps market exit
    slippage, so the kill threshold read 2 bps kinder than any reachable exit."""
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.open_shadow_position(1000.0, E, QTY, 0.0, 0.0)
    flat = engine.get_liquidation_equity(cur_bid=E)
    expected = 3.85 - (MAKER_FEE_BPS + TAKER_FEE_BPS + SLIPPAGE_BPS) / 1e4 * (E * QTY)
    assert flat == pytest.approx(expected)


def test_liquidation_equity_is_side_aware():
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.open_shadow_position(1000.0, E, QTY, 0.0, 0.0, side='SELL')
    drop = at(50.0, -1)
    assert engine.get_liquidation_equity(cur_bid=drop, cur_ask=drop) > 3.80, \
        'a short gains when price falls'


# ========================================================= 15m bootstrap
def test_parse_klines_keeps_close_time_for_closed_bar_selection():
    raw = [[1, '0.2', '0.21', '0.19', '0.205', '100', 59999, '0', 0, '0', '0', '0']]
    parsed = _parse_klines(raw)[0]
    assert parsed['close'] == 0.205 and parsed['close_time'] == 59999


def test_fetch_returns_the_fifteen_minute_series(monkeypatch):
    """Without this series the gate has no input and silently permits nothing
    and blocks nothing -- an installed filter that does not exist."""
    calls = []

    class Resp:
        def __init__(self, payload):
            self._p = json.dumps(payload).encode()

        def read(self):
            return self._p

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def fake_urlopen(req, timeout=None):
        url = req.full_url
        calls.append(url)
        if 'bookTicker' in url:
            return Resp({'bidPrice': '0.2', 'askPrice': '0.2001'})
        if 'openInterest' in url:
            return Resp({'openInterest': '1234'})
        bar = [1, '0.2', '0.21', '0.19', '0.205', '100', 59999, '0', 0, '0', '0', '0']
        return Resp([bar] * (60 if 'interval=15m' in url else 20))

    monkeypatch.setattr(module.urllib.request, 'urlopen', fake_urlopen)
    ticker, klines, oi, bars15 = fetch_rest_market_data()
    assert ticker and len(klines) == 20 and oi == 1234.0
    assert bars15 is not None and len(bars15) == 60
    assert any('interval=15m' in u and 'limit=60' in u for u in calls)


def test_update_cycle_classifies_the_regime_from_the_passed_bars():
    engine = FlushShadowEngine(starting_equity=3.85)
    assert engine.regime == TRANSITION, 'no history means no permission'
    engine.update_cycle({'bidPrice': '0.2', 'askPrice': '0.2001'}, [], bars15=down_bars15())
    assert engine.regime == DOWN and engine.short_allowed() is True
    assert engine.long_allowed() is False


def test_range_history_reopens_the_long_only_after_two_closes():
    engine = FlushShadowEngine(starting_equity=3.85)
    bars = range_bars15()
    engine.update_cycle({'bidPrice': '0.2', 'askPrice': '0.2001'}, [], bars15=bars)
    assert engine.regime == RANGE and engine.range_streak == 1
    assert engine.long_allowed() is False, 'one close is not the hysteresis'
    engine.update_cycle({'bidPrice': '0.2', 'askPrice': '0.2001'}, [], bars15=bars)
    assert engine.range_streak == 2 and engine.long_allowed() is True


def test_bars15_persist_across_cycles_that_carry_none():
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.update_cycle({'bidPrice': '0.2', 'askPrice': '0.2001'}, [], bars15=down_bars15())
    engine.update_cycle({'bidPrice': '0.2', 'askPrice': '0.2001'}, [{'time': 0, 'open': 0.2,
                        'high': 0.2, 'low': 0.2, 'close': 0.2, 'volume': 1.0}])
    assert engine.regime == DOWN, 'a cycle without a 15m refresh must not clear the regime'


# ===================================================== short entry sequence
def flat_1m(n=25, mid=E, half=0.00050, v=1000.0):
    return [{'time': i, 'open': mid, 'high': mid + half, 'low': mid - half,
             'close': mid, 'volume': v} for i in range(n)]


def test_a_break_alone_places_nothing_until_the_retest_confirms():
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.update_cycle({'bidPrice': '0.2', 'askPrice': '0.2001'}, [], bars15=down_bars15())
    assert engine.short_allowed() is True

    window = flat_1m(25)
    support = min(b['low'] for b in window)
    breaking = {'time': 25, 'open': E, 'high': E, 'low': support - 0.0010,
                'close': support - 0.0008, 'volume': 4000.0}
    forming = {'time': 26, 'open': E, 'high': E, 'low': E, 'close': E, 'volume': 1.0}

    ok, _, reason = engine.detect_short_setup(window + [breaking, forming], 0.2001)
    assert ok is False and reason == 'SHORT_BREAK_AWAITING_RETEST'
    assert engine.active_order is None, 'a break is not an entry'

    atr = engine._short_break['atr']
    retest = {'time': 27, 'open': support + 0.0001, 'high': support + 0.0002,
              'low': support - 0.0006, 'close': support - 0.2 * atr, 'volume': 1500.0}
    ok, entry, reason = engine.detect_short_setup(window + [breaking, retest, forming], 0.2001)
    assert ok is True and reason == 'SHORT_RETEST_CONFIRMED' and entry > 0


def test_a_retest_that_never_arrives_expires_the_event():
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.update_cycle({'bidPrice': '0.2', 'askPrice': '0.2001'}, [], bars15=down_bars15())
    window = flat_1m(25)
    support = min(b['low'] for b in window)
    breaking = {'time': 25, 'open': E, 'high': E, 'low': support - 0.0010,
                'close': support - 0.0008, 'volume': 4000.0}
    forming = {'time': 99, 'open': E, 'high': E, 'low': E, 'close': E, 'volume': 1.0}
    engine.detect_short_setup(window + [breaking, forming], 0.2001)
    assert engine._short_break is not None

    away = lambda t: {'time': t, 'open': support - 0.003, 'high': support - 0.0028,
                      'low': support - 0.0035, 'close': support - 0.0032, 'volume': 900.0}
    reasons = [engine.detect_short_setup(window + [breaking] + [away(30 + i)] + [forming], 0.2001)[2]
               for i in range(3)]
    assert reasons[-1] == 'SHORT_RETEST_EXPIRED'
    assert engine._short_break is None, 'the event is consumed, not left armed'


def test_a_short_entry_passes_the_same_capital_gate_as_a_long():
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.equity = 3.8032                      # no budget left below the floor
    result = engine._place_entry('SELL', E, 'SHORT_RETEST_CONFIRMED', [], 0.2, 0.2001, 0.0)
    assert result['status'] == 'BLOCKED' and 'RISK' in result['reason']
    assert result['side'] == 'SELL'


def test_a_resting_short_fills_only_when_the_bid_trades_up_through_it():
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.active_order = {'placed_time': time.time(), 'price': at(10.0), 'qty': QTY,
                           'notional': E * QTY, 'reason': 'x', 'side': 'SELL'}
    pending = engine.update_cycle({'bidPrice': str(E), 'askPrice': str(at(1.0))}, [])
    assert pending['status'] == 'ORDER_PENDING', 'the bid has not reached our ask'

    filled = engine.update_cycle({'bidPrice': str(at(12.0)), 'askPrice': str(at(13.0))}, [])
    assert filled['status'] == 'ORDER_FILLED' and filled['side'] == 'SELL'
    assert all(b.active_pos.side == 'SELL' for b in engine.shadow_branches.values())
