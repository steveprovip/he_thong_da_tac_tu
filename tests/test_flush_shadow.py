"""Deterministic unit tests for DOGEUSDT 1m Flush Shadow Engine."""
import pytest
from research.flush_shadow import (
    calculate_lot_size,
    calculate_atr14,
    round_tick,
    FlushShadowEngine,
    ShadowBranch,
    STARTING_EQUITY,
    KILL_LOSS_USDT,
    MIN_NOTIONAL,
    TP_BPS,
    SL_BPS,
    MAKER_FEE_BPS,
    TAKER_FEE_BPS,
    SLIPPAGE_BPS,
    BREAKEVEN_WINRATE,
)


def test_lot_size_clears_minimum_notional():
    """Verify that calculate_lot_size always satisfies Binance MIN_NOTIONAL >= 5.0 USDT."""
    test_prices = [0.05, 0.08, 0.08319, 0.10, 0.12, 0.15]
    for p in test_prices:
        qty = calculate_lot_size(p)
        assert qty.is_integer(), f"Qty {qty} must be integer lot"
        notional = qty * p
        assert notional >= MIN_NOTIONAL, f"Price {p}: notional {notional} < {MIN_NOTIONAL}"

    # Specifically for DOGE at ~0.08319
    qty_doge = calculate_lot_size(0.08319)
    assert qty_doge == 61.0, f"Expected 61 DOGE at 0.08319, got {qty_doge}"
    assert qty_doge * 0.08319 >= 5.0


def test_breakeven_winrate_formula():
    """Verify analytical breakeven win rate p = 45.21% for baseline 2-outcome."""
    win_net_bps = TP_BPS - (MAKER_FEE_BPS + MAKER_FEE_BPS)  # 39.67 - 4.0 = 35.67
    loss_net_bps = SL_BPS + (MAKER_FEE_BPS + TAKER_FEE_BPS + SLIPPAGE_BPS)  # 20.44 + 9.0 = 29.44

    p_be = loss_net_bps / (win_net_bps + loss_net_bps)
    assert abs(p_be - BREAKEVEN_WINRATE) < 0.0005, f"Calculated {p_be:.4f} != {BREAKEVEN_WINRATE}"


def test_realistic_fill_requires_trade_through():
    """Touching order price does NOT fill; market must trade through."""
    engine = FlushShadowEngine()
    import time
    now = time.time()
    engine.active_order = {
        'placed_time': now,
        'price': 0.08300,
        'qty': 61.0,
        'notional': 5.063,
        'reason': 'TEST'
    }

    # Ask is 0.08301 (1 tick above bid limit) -> NO FILL
    ticker_touch = {'bidPrice': '0.08300', 'askPrice': '0.08301'}
    res = engine.update_cycle(ticker_touch, [])
    assert res['status'] == 'ORDER_PENDING'
    assert engine.active_position is None

    # Ask drops to 0.08300 (trade through / match) -> FILLED
    ticker_cross = {'bidPrice': '0.08299', 'askPrice': '0.08300'}
    res_fill = engine.update_cycle(ticker_cross, [])
    assert res_fill['status'] == 'ORDER_FILLED'
    assert engine.active_position is not None
    assert engine.active_position['entry_price'] == 0.08300


def test_tp_execution_maker_accounting():
    """Verify TP execution records win and charges 4 bps total fee."""
    engine = FlushShadowEngine(starting_equity=3.85)
    entry_p = 0.08300
    tp_p = round_tick(entry_p * (1.0 + (TP_BPS / 1e4)))

    engine.open_shadow_position(1000.0, entry_p, 61.0, 0.0, 0.0)

    ticker_tp = {'bidPrice': str(tp_p), 'askPrice': str(tp_p + 0.00001)}
    res = engine.update_cycle(ticker_tp, [])

    assert res['status'] == 'TRADE_CLOSED'
    assert res['result'] in ('TP', 'WIN')
    trade = res['trade']
    assert trade.is_win is True
    assert trade.fee_bps == 4.0
    assert abs(trade.net_bps - 35.67) < 0.6
    assert engine.metrics.wins == 1
    assert engine.equity > 3.85


def test_sl_execution_single_slippage_accounting():
    """Verify SL execution deducts slippage ONCE and charges only 7 bps fee (2 maker + 5 taker).

    Total cost = 2 bps slippage + 7 bps fee = 9 bps.
    Net loss = -20.44 - 9 = -29.44 bps (subject to DOGE 0.00001 tick quantization).
    """
    engine = FlushShadowEngine(starting_equity=3.85)
    entry_p = 0.08300
    sl_p = round_tick(entry_p * (1.0 - (SL_BPS / 1e4)))

    engine.open_shadow_position(1000.0, entry_p, 61.0, 0.0, 0.0)

    ticker_sl = {'bidPrice': str(sl_p), 'askPrice': str(sl_p + 0.00001)}
    res = engine.update_cycle(ticker_sl, [])

    assert res['status'] == 'TRADE_CLOSED'
    assert res['result'] == 'SL'
    trade = res['trade']
    assert trade.is_win is False
    # Fee is strictly 7 bps (2 maker in + 5 taker out); slippage is already in exit_p
    assert trade.fee_bps == 7.0
    # Net loss must be ~ -29.44 bps (within 1 tick ~ 1.2 bps quantization)
    assert abs(trade.net_bps - (-29.44)) < 0.8
    assert engine.metrics.losses == 1
    assert engine.equity < 3.85


def test_kill_switch_floating_liquidation_equity():
    """Verify Kill Switch halts on mark-to-market floating equity loss >= 0.05 USDT."""
    engine = FlushShadowEngine(starting_equity=3.85)
    entry_p = 0.08300
    engine.open_shadow_position(1000.0, entry_p, 61.0, 0.0, 0.0)

    # Initial mark-to-market liquidation equity incorporates 7 bps fee
    liq_eq = engine.get_liquidation_equity(cur_bid=entry_p)
    assert liq_eq < 3.85
    assert engine.check_kill_switch(cur_bid=entry_p) is False

    # Price crashes: cur_bid drops by 110 bps (loss exceeds 0.05 USDT on 5.06 notional)
    crash_bid = entry_p * (1.0 - 0.0110)
    assert engine.check_kill_switch(cur_bid=crash_bid) is True
    assert engine.metrics.is_killed is True
    assert 'KILL TRIGGERED' in engine.metrics.kill_reason

    # Reaching the threshold must FORCE A FLAT, not just relabel the run.
    # The previous expectation ('HALTED' with the position still open) pinned the
    # bug: quantity stayed live and no branch ever settled the loss.
    res = engine.update_cycle({'bidPrice': str(crash_bid), 'askPrice': str(crash_bid + 0.00001)}, [])
    assert res['status'] == 'HALTED_FLAT'
    assert res['halted'] is True
    assert all(b.active_pos is None for b in engine.shadow_branches.values()), 'HALTED must mean FLAT'
    assert res['settled'] == len(engine.shadow_branches), 'every open branch settles an exit'
    assert all(t.reason == 'KILL_FORCED_EXIT'
               for br in engine.shadow_branches.values() for t in br.trades)


def test_old_sl_checked_before_raising_to_protection():
    """Verify old SL is checked first, new protected SL has effect on subsequent cycle."""
    branch = ShadowBranch('be_15_10', trigger_bps=15.0, protect_sl_bps=10.0)
    entry_p = 0.08300
    branch.open_position(1000.0, entry_p, 61.0, 0.0, 0.0)
    old_sl = branch.active_pos.sl_price

    # Case 1: Market drops to old SL -> triggers old SL immediately
    res_sl = branch.update(cur_bid=old_sl, now=1001.0)
    assert res_sl['status'] == 'TRADE_CLOSED'
    assert res_sl['result'] == 'SL'
    assert res_sl['trade'].reason == 'ORIGINAL_SL_TRIGGERED'

    # Case 2: Fresh position, price rises to +16 bps (>= 15 bps trigger)
    branch2 = ShadowBranch('be_15_10', trigger_bps=15.0, protect_sl_bps=10.0)
    branch2.open_position(1000.0, entry_p, 61.0, 0.0, 0.0)
    bid_16 = round_tick(entry_p * (1.0 + 16.0 / 1e4))
    res_float = branch2.update(cur_bid=bid_16, now=1001.0)
    assert res_float['status'] == 'IN_POSITION'
    assert branch2.active_pos.is_protected is True
    protected_sl = round_tick(entry_p * (1.0 + 10.0 / 1e4))
    assert branch2.active_pos.sl_price == protected_sl

    # On next observation, if bid drops to +10 bps, protected SL triggers
    res_protect = branch2.update(cur_bid=protected_sl, now=1002.0)
    assert res_protect['status'] == 'TRADE_CLOSED'
    assert res_protect['result'] == 'PROTECT'
    assert res_protect['trade'].reason == 'PROTECTION_SL_HIT'
    # Protection net is positive (~ +1.0 bps gross 10 - 2 slippage - 7 fee, tick quantized)
    assert res_protect['trade'].net_bps > 0
    assert abs(res_protect['trade'].net_bps - 1.0) < 1.0


def test_three_shadow_branches_and_premature_tracking():
    """Verify simultaneous execution of Baseline, BE_15_10 and BE_20_10, with premature stopout tracking."""
    engine = FlushShadowEngine(starting_equity=3.85)
    entry_p = 0.08300
    engine.open_shadow_position(1000.0, entry_p, 61.0, 0.0, 0.0)

    # 1. Price moves to +16 bps
    bid_16 = round_tick(entry_p * (1.0 + 16.0 / 1e4))
    engine.update_cycle({'bidPrice': str(bid_16), 'askPrice': str(bid_16 + 0.00001)}, [])

    # BE_15_10 should have raised SL to +10 bps; BE_20_10 has not (16 < 20); Baseline has no protection
    assert engine.shadow_branches['be_15_10'].active_pos.is_protected is True
    assert engine.shadow_branches['be_20_10'].active_pos.is_protected is False
    assert engine.shadow_branches['baseline'].active_pos.is_protected is False

    # 2. Price retraces to +10 bps
    bid_10 = round_tick(entry_p * (1.0 + 10.0 / 1e4))
    engine.update_cycle({'bidPrice': str(bid_10), 'askPrice': str(bid_10 + 0.00001)}, [])

    # BE_15_10 is stopped out at protection (+1.0 bps net)
    assert engine.shadow_branches['be_15_10'].active_pos is None
    assert engine.shadow_branches['be_15_10'].protect_count == 1
    # Baseline and BE_20_10 remain open
    assert engine.shadow_branches['baseline'].active_pos is not None
    assert engine.shadow_branches['be_20_10'].active_pos is not None

    # 3. Market subsequently rallies to TP (+39.67 bps)
    tp_p = round_tick(entry_p * (1.0 + (TP_BPS / 1e4)))
    engine.update_cycle({'bidPrice': str(tp_p), 'askPrice': str(tp_p + 0.00001)}, [])

    # Baseline and BE_20_10 hit TP
    assert engine.shadow_branches['baseline'].tp_count == 1
    assert engine.shadow_branches['be_20_10'].tp_count == 1

    # BE_15_10 premature stopout watcher must have caught this!
    stats_15 = engine.shadow_branches['be_15_10'].get_stats()
    assert stats_15['premature_count'] == 1
    assert stats_15['premature_rate'] == 1.0  # 100% premature stopout

    # Verify analytical 3-outcome EV formula: EV = 35.67*p_TP + 1.0*p_protect - 29.44*p_SL
    # For BE_15_10: 0 TP, 1 Protect, 0 SL -> EV = 1.0 bps
    assert abs(stats_15['analytical_ev_bps'] - 1.0) < 0.01

    # For Baseline: 1 TP, 0 Protect, 0 SL -> EV = +35.67 bps
    stats_base = engine.shadow_branches['baseline'].get_stats()
    assert abs(stats_base['analytical_ev_bps'] - 35.67) < 0.01


def test_calculate_atr14():
    """Verify ATR14 calculation on synthetic 1m bars."""
    bars = []
    base_time = 1700000000
    for i in range(20):
        bars.append({
            'time': base_time + i * 60,
            'open': 0.08300,
            'high': 0.08350,
            'low': 0.08250,
            'close': 0.08310,
            'volume': 1000.0,
        })
    atr_raw, atr_bps = calculate_atr14(bars)
    assert atr_raw > 0.0
    # bar high - low = 0.00100 on 0.08310 is ~120 bps
    assert 100.0 < atr_bps < 150.0
