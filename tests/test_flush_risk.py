"""Capital risk gates at 3.85 USDT.

The binding constraint is the loss budget, not the signal. Every test here
checks the same principle from a different angle: when a setup does not fit the
account, the account is not what gets adjusted.
"""
import pytest

from research.flush_risk import (KILL_LOSS_USDT, S_MAX_BPS, breakeven_trigger_bps,
                                 desired_protection_bps, entry_risk_gate, payoff_ratio,
                                 resolve_stop_bps, risk_allowed_usdt, risk_usdt,
                                 stop_width_gate, structural_stop_bps, target_bps,
                                 tick_bps, trail_distance_bps)

ENTRY = 0.08240
QTY = 61.0
TICK = 0.00001
START = 3.85
TICKB = tick_bps(TICK, ENTRY)


def test_s_max_is_the_documented_capital_ceiling():
    assert S_MAX_BPS == 20.82
    assert stop_width_gate(20.82)[0] is True
    assert stop_width_gate(20.83)[0] is False
    assert 'STOP_TOO_WIDE_FOR_CAPITAL' in stop_width_gate(20.83)[1]


def test_a_wide_atr_blocks_the_trade_instead_of_shrinking_the_stop():
    """Spec acceptance 9: ATR 80 bps at N=5.03 and risk 0.015 must BLOCK.

    s floors at 0.8*a, so an 80 bps ATR forces s=64 bps. Clipping that back to
    20.82 would not reduce risk, only move the same loss behind a stop that no
    longer marks the level which invalidates the setup.
    """
    s = resolve_stop_bps(0.0, 80.0, TICKB)
    assert s == pytest.approx(64.0)
    allowed, reason, _ = entry_risk_gate(QTY, ENTRY, s, START, START)
    assert allowed is False and 'STOP_TOO_WIDE_FOR_CAPITAL' in reason


def test_stop_floors_only_widen_never_narrow():
    assert resolve_stop_bps(30.0, 10.0, TICKB) == 30.0, 'structural stop wins when widest'
    assert resolve_stop_bps(5.0, 25.0, TICKB) == pytest.approx(20.0), '0.8*a floor'
    assert resolve_stop_bps(0.1, 0.0, TICKB) == pytest.approx(3.0 * TICKB), 'tick floor'


def test_structural_distance_is_symmetric_in_bps():
    assert structural_stop_bps(ENTRY, ENTRY * 0.998) == pytest.approx(20.0, abs=1e-6)
    assert structural_stop_bps(ENTRY, ENTRY * 1.002) == pytest.approx(20.0, abs=1e-6)


def test_budget_shrinks_to_zero_before_the_kill_floor():
    """Spec acceptance 10: equity 3.8032 is 0.0468 down -- not yet killed, but
    with a 0.005 reserve there is no room left to open anything."""
    assert START - 3.8032 < KILL_LOSS_USDT
    assert risk_allowed_usdt(3.8032, START) == 0.0
    allowed, reason, _ = entry_risk_gate(QTY, ENTRY, 10.0, 3.8032, START)
    assert allowed is False and 'RISK_EXCEEDS_BUDGET' in reason


def test_a_fitting_setup_passes_the_same_gate():
    allowed, reason, diag = entry_risk_gate(QTY, ENTRY, 15.0, START, START)
    assert allowed is True and reason == 'OK'
    assert diag['risk_usdt'] <= diag['risk_allowed_usdt']


def test_one_stop_costs_what_the_ledger_says_it_costs():
    # s + maker 2 + taker 5 + slippage 2
    assert risk_usdt(QTY, ENTRY, 20.44) == pytest.approx(QTY * ENTRY * 29.44 / 1e4)


def test_targets_and_payoff_screen():
    assert target_bps('BUY', 10.0) == 25.0, 'floor at 25 bps'
    assert target_bps('BUY', 20.0) == pytest.approx(30.0)
    assert target_bps('SELL', 20.0) == pytest.approx(40.0), 'shorts need 2.0*s'
    assert payoff_ratio(25.0, 20.44) == pytest.approx(21.0 / 29.44, abs=1e-6)
    assert payoff_ratio(25.0, 20.44) < 1.2, 'a 25 bps target on a 20 bps stop does not clear the screen'


# ------------------------------------------------- adaptive breakeven trigger
def test_adaptive_trigger_matches_the_specified_formula():
    """trigger = max(15, 10 + max(3*tick, 0.3*a))."""
    assert breakeven_trigger_bps(0.0, TICKB) == 15.0, 'quiet tape keeps the 15 bps floor'
    assert breakeven_trigger_bps(25.0, TICKB) == pytest.approx(17.5)
    assert breakeven_trigger_bps(80.0, TICKB) == pytest.approx(34.0)


def test_adaptive_trigger_rises_with_volatility():
    """A fixed +15 arms inside the noise when ATR expands, which is how a trade
    gets stopped at breakeven and then runs to target without you."""
    quiet, fast = breakeven_trigger_bps(10.0, TICKB), breakeven_trigger_bps(60.0, TICKB)
    assert fast > quiet
    assert all(breakeven_trigger_bps(a, TICKB) >= 15.0 for a in (0, 1, 5, 25, 100))


def test_trail_distance_scales_with_atr():
    assert trail_distance_bps(0.0, TICKB) == pytest.approx(3.0 * TICKB)
    assert trail_distance_bps(40.0, TICKB) == pytest.approx(20.0)


def test_protection_only_ever_tightens():
    first = desired_protection_bps(0.0, 30.0, 20.0, TICKB)
    assert desired_protection_bps(first, 12.0, 20.0, TICKB) == first, 'a lower MFE cannot loosen a stop'
    assert desired_protection_bps(first, 60.0, 20.0, TICKB) > first
    assert desired_protection_bps(0.0, 0.0, 0.0, TICKB) >= 10.0, 'never below the cost floor b'
