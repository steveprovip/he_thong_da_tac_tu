"""Cost and stop-ordering invariants of the shadow branch engine.

These are the four rules the whole 3-branch comparison rests on. If any of them
drifts, every EV number the dashboard shows is wrong in a way no test would
otherwise catch: the branches would still run, still report, and still disagree.
"""
import json

import pytest

from research.flush_shadow import (MAKER_FEE_BPS, SLIPPAGE_BPS, TAKER_FEE_BPS,
                                   FlushShadowEngine, ShadowBranch)

ENTRY = 0.20000
QTY = 61.0


def branch(name='be_15_10', trigger=15.0, protect=10.0):
    b = ShadowBranch(name, trigger_bps=trigger, protect_sl_bps=protect)
    b.open_position(0.0, ENTRY, QTY, 0.0, 0.0)
    return b


def at(bps):
    return ENTRY * (1.0 + bps / 1e4)


def test_the_old_stop_is_checked_before_the_new_one_is_armed():
    """A gap through the original stop on the very tick that would have armed
    protection must still settle at the original stop. Raising the stop first
    would let the engine book a profit the market never offered."""
    b = branch()
    result = b.update(at(-25.0), 1.0)
    assert result['trade'].reason == 'ORIGINAL_SL_TRIGGERED'
    assert result['trade'].net_bps < 0


def test_a_raised_stop_only_binds_from_the_next_observation():
    b = branch()
    armed = b.update(at(16.0), 1.0)
    assert armed['protected'] is True, 'MFE 16 bps arms the +15 trigger'
    assert b.active_pos.sl_price > ENTRY
    # Same tick did not close anything; the new stop bites on a later tick only.
    assert armed['status'] == 'IN_POSITION'
    closed = b.update(at(9.0), 2.0)
    assert closed['trade'].reason == 'PROTECTION_SL_HIT'


def test_slippage_is_deducted_exactly_once_and_only_on_the_market_stop():
    b = branch()
    b.update(at(16.0), 1.0)
    trade = b.update(at(9.0), 2.0)['trade']
    # Exit fills at min(bid, stop) less one slippage step, never two.
    expected = min(at(9.0), b_sl := ENTRY * (1.0 + 10.0 / 1e4)) - (SLIPPAGE_BPS / 1e4) * ENTRY
    assert trade.exit_price == pytest.approx(expected, abs=1e-5)
    assert trade.gross_bps == pytest.approx(7.0, abs=0.01), 'bid +9 less 2 bps slippage'
    assert b_sl > ENTRY


def test_stop_costs_seven_bps_and_take_profit_costs_four():
    """Maker in + taker out on a stop (2+5); maker both legs on a GTX take
    profit (2+2). The whole edge case for this strategy is 3 bps wide."""
    stop = branch()
    stop.update(at(-25.0), 1.0)
    assert stop.trades[0].fee_bps == MAKER_FEE_BPS + TAKER_FEE_BPS == 7.0

    win = ShadowBranch('baseline')
    win.open_position(0.0, ENTRY, QTY, 0.0, 0.0)
    take = win.update(at(45.0), 1.0)['trade']
    assert take.fee_bps == MAKER_FEE_BPS + MAKER_FEE_BPS == 4.0
    assert take.net_bps == pytest.approx(take.gross_bps - 4.0)


def test_resolved_premature_watchers_are_dropped_not_accumulated():
    """The watcher list is rescanned every 2s tick for the life of the process;
    keeping resolved entries turns it into an unbounded O(n) scan."""
    b = branch()
    b.update(at(16.0), 1.0)
    b.update(at(9.0), 2.0)
    assert len(b.watchers) == 1, 'a protected exit opens one watcher'
    b.update(at(50.0), 3.0)
    assert b.watchers == [], 'resolved watchers are counted then released'
    assert b.premature_count == 1


def test_telemetry_snapshot_is_published_atomically(tmp_path, monkeypatch):
    """The dashboard polls this file about once a second while the engine
    rewrites it every two. A partial read shows a stale board, so the write
    goes to a temp file and is renamed into place."""
    import research.flush_shadow as module
    target = tmp_path / 'telemetry_state.json'
    monkeypatch.setattr(module, 'TELEMETRY_STATE_FILE', target)
    engine = FlushShadowEngine()
    engine.write_telemetry_snapshot(0.2, 0.2001, 'WAITING')
    payload = json.loads(target.read_text(encoding='utf-8'))
    assert set(payload['branches']) == {'baseline', 'be_15_10', 'be_20_10', 'be_adaptive'}, \
        'the ATR-scaled rule competes as a fourth branch; the fixed three keep their meaning'
    assert not list(tmp_path.glob('*.tmp')), 'no temp file is left behind'
    engine.write_telemetry_snapshot(0.2, 0.2001, 'WAITING')
    assert json.loads(target.read_text(encoding='utf-8'))['history']


def test_history_and_event_buffers_stay_bounded(tmp_path, monkeypatch):
    import research.flush_shadow as module
    monkeypatch.setattr(module, 'TELEMETRY_STATE_FILE', tmp_path / 'telemetry_state.json')
    engine = FlushShadowEngine()
    for _ in range(700):
        engine.write_telemetry_snapshot(0.2, 0.2001, 'WAITING')
    assert len(engine.history_buffer) == 600
    assert len(engine.event_buffer) == 20


# ============================================================================
# Cohort integrity, kill settlement and the regime gate (spec 2026-09-15)
# ============================================================================
from research.flush_regime import DOWN, RANGE          # noqa: E402
from research.flush_shadow import S_MAX_BPS, round_tick  # noqa: E402

TICK_UP = 0.00001


def quote(bid):
    return {'bidPrice': str(bid), 'askPrice': str(bid + TICK_UP)}


def flush_klines(n=20, mid=0.08300):
    """1m bars whose last CLOSED bar is a deep flush with a strong bounce."""
    bars = [{'time': i, 'open': mid, 'high': mid + 0.00005, 'low': mid - 0.00005,
             'close': mid, 'volume': 1000.0} for i in range(n - 2)]
    bars.append({'time': n - 2, 'open': mid, 'high': mid + 0.00005,
                 'low': mid * (1 - 0.0030), 'close': mid, 'volume': 9000.0})
    bars.append({'time': n - 1, 'open': mid, 'high': mid, 'low': mid,
                 'close': mid, 'volume': 100.0})       # ongoing, excluded
    return bars


def down_bars15(n=90, start=0.20000, step=-0.0006, rng=0.0008):
    out, prev = [], None
    for i in range(n):
        c = start + i * step
        o = prev if prev is not None else c
        out.append({'open': o, 'high': max(o, c) + rng / 2,
                    'low': min(o, c) - rng / 2, 'close': c, 'volume': 1000.0})
        prev = c
    return out


# ------------------------------------------------- 1 · baseline is never lost
def test_a_branch_refuses_to_open_over_its_own_live_position():
    branch = ShadowBranch('baseline')
    branch.open_position(0.0, ENTRY, QTY, 0.0, 0.0)
    with pytest.raises(RuntimeError, match='refusing to overwrite'):
        branch.open_position(1.0, ENTRY, QTY, 0.0, 0.0)


def test_a_protected_exit_on_one_branch_cannot_overwrite_baseline():
    """The bug this pins: BE_15_10 protects out, the primary view reads flat, the
    next fill opens a cohort over baseline's still-running trade, and the two
    trades that were about to disagree both vanish."""
    engine = FlushShadowEngine(starting_equity=3.85)
    entry = 0.08300
    assert engine.open_shadow_position(1000.0, entry, 61.0, 0.0, 0.0) is True

    engine.update_cycle(quote(round_tick(entry * (1 + 16.0 / 1e4))), [])   # arms BE_15_10
    engine.update_cycle(quote(round_tick(entry * (1 + 10.0 / 1e4))), [])   # protects it out
    assert engine.shadow_branches['be_15_10'].active_pos is None
    baseline_entry_time = engine.shadow_branches['baseline'].active_pos.entry_time

    assert engine.cohort_open() is True, 'baseline and be_20_10 are still running'
    assert engine.open_shadow_position(2000.0, entry * 1.01, 61.0, 0.0, 0.0) is False
    assert engine.shadow_branches['baseline'].active_pos is not None
    assert engine.shadow_branches['baseline'].active_pos.entry_time == baseline_entry_time


def test_the_cycle_refuses_a_new_signal_while_any_branch_is_open():
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.open_shadow_position(1000.0, 0.08300, 61.0, 0.0, 0.0)
    engine.shadow_branches['be_15_10'].active_pos = None      # primary looks flat
    result = engine.update_cycle(quote(0.08300), flush_klines())
    assert result['status'] == 'BLOCKED' and result['reason'] == 'COHORT_STILL_OPEN'


# --------------------------------------------------- 2 · HALTED means FLAT
def test_kill_settles_every_branch_before_reporting_halted_flat():
    engine = FlushShadowEngine(starting_equity=3.85)
    entry = 0.08300
    engine.open_shadow_position(1000.0, entry, 61.0, 0.0, 0.0)
    crash = round_tick(entry * (1 - 0.0200))
    result = engine.update_cycle(quote(crash), [])
    assert result['status'] == 'HALTED_FLAT' and result['halted'] is True
    assert all(b.active_pos is None for b in engine.shadow_branches.values())
    assert result['settled'] == len(engine.shadow_branches)
    for branch in engine.shadow_branches.values():
        assert branch.trades[-1].reason == 'KILL_FORCED_EXIT'
        assert branch.trades[-1].fee_bps == 7.0, 'a forced exit is taker, never maker'


def test_kill_cancels_a_pending_entry_instead_of_leaving_it_working():
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.open_shadow_position(1000.0, 0.08300, 61.0, 0.0, 0.0)
    engine.active_order = {'placed_time': 0.0, 'price': 0.08200, 'qty': 61.0, 'notional': 5.0}
    engine.update_cycle(quote(round_tick(0.08300 * (1 - 0.02))), [])
    assert engine.active_order is None


def test_an_unexecutable_quote_reports_kill_pending_not_flat():
    """Exit failure must not be relabelled as FLAT; the latch stays and unwinding
    continues on later observations."""
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.open_shadow_position(1000.0, 0.08300, 61.0, 0.0, 0.0)
    engine.metrics.is_killed = True
    engine.metrics.kill_reason = 'forced'
    result = engine.update_cycle({'bidPrice': '0', 'askPrice': '0'}, [])
    assert result['status'] == 'KILL_PENDING'
    assert result['remaining_branches'] == len(engine.shadow_branches)
    assert engine.metrics.is_killed is True, 'the latch survives a failed unwind'


# ------------------------------------------------------- 3 · regime gate
def test_a_downtrend_blocks_the_long_however_strong_the_flush():
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.update_regime(down_bars15(), flush_klines())
    assert engine.regime == DOWN and engine.long_allowed() is False
    result = engine.update_cycle(quote(0.08300), flush_klines())
    # The invariant is about the ORDER, not the status string: in DOWN the engine
    # looks for a short and never for a long, so whatever it reports, no BUY may
    # rest and no long cohort may open.
    assert result['status'] != 'ORDER_PLACED' or result['side'] == 'SELL'
    assert engine.active_order is None or engine.active_order['side'] == 'SELL'
    assert engine.cohort_open() is False


def test_without_regime_history_the_engine_keeps_its_legacy_path():
    """No 15m bars means the gate has no input; it must not invent permission,
    but it also must not silently change behaviour for existing regressions."""
    engine = FlushShadowEngine(starting_equity=3.85)
    assert engine.bars15 == [] and engine.regime == 'TRANSITION'
    result = engine.update_cycle(quote(0.08300), flush_klines())
    assert result['status'] in ('ORDER_PLACED', 'ORDER_PENDING', 'BLOCKED', 'NO_SIGNAL')


# ------------------------------------------- 4 · adaptive breakeven branch
def test_the_adaptive_branch_scales_its_trigger_with_atr():
    engine = FlushShadowEngine(starting_equity=3.85)
    engine.open_shadow_position(1000.0, 0.20000, 61.0, 0.0, 0.0)
    assert engine.shadow_branches['be_adaptive'].active_pos.trigger_bps == 15.0

    engine = FlushShadowEngine(starting_equity=3.85)
    engine.open_shadow_position(1000.0, 0.20000, 61.0, 0.0016, 80.0)
    adaptive = engine.shadow_branches['be_adaptive'].active_pos.trigger_bps
    assert adaptive == pytest.approx(34.0), '10 + 0.3*80'
    assert engine.shadow_branches['be_15_10'].active_pos.trigger_bps == 15.0, \
        'the fixed branches keep their meaning as the benchmark'


def test_capital_ceiling_is_exposed_on_the_engine():
    assert S_MAX_BPS == 20.82
