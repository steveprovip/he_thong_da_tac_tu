"""Circuit breaker on a frozen snapshot, and the 3.85 USDT hard ceiling."""
import pytest

from core.capital_gate import (
    B_HARD_USDT, BUDGET_EXHAUSTED, EXECUTE, N_CAP_USDT, PAPER_ONLY,
    POSITION_FRACTION, capital_verdict, position_cap,
)
from core.watchdog import DEFAULT_PARK_AFTER, SnapshotWatchdog, snapshot_digest

SNAP = {'BTCUSDT': (100.0, 1.5, 0.2)}
OTHER = {'BTCUSDT': (100.5, 1.5, 0.2)}


# ----------------------------------------------------------------- watchdog

def test_parks_on_the_third_identical_snapshot():
    dog = SnapshotWatchdog()
    assert DEFAULT_PARK_AFTER == 3
    assert dog.observe(SNAP) == 'ACTIVE'
    assert dog.observe(SNAP) == 'ACTIVE'
    assert dog.observe(SNAP) == 'PARKED'
    assert dog.parked is True
    assert dog.repeats == 3
    assert dog.parks == 1


def test_a_new_snapshot_wakes_it_immediately():
    dog = SnapshotWatchdog()
    for _ in range(5):
        dog.observe(SNAP)
    assert dog.parked is True
    assert dog.observe(OTHER) == 'ACTIVE'
    assert dog.parked is False
    assert dog.repeats == 1
    assert dog.wakes == 1


def test_alternating_snapshots_never_park():
    dog = SnapshotWatchdog()
    for _ in range(10):
        assert dog.observe(SNAP) == 'ACTIVE'
        assert dog.observe(OTHER) == 'ACTIVE'
    assert dog.parked is False
    assert dog.parks == 0


def test_park_counts_once_per_episode():
    dog = SnapshotWatchdog()
    for _ in range(6):
        dog.observe(SNAP)
    assert dog.parks == 1
    dog.observe(OTHER)
    for _ in range(3):
        dog.observe(OTHER)
    assert dog.parks == 2


def test_digest_is_stable_and_order_independent():
    assert snapshot_digest({'a': 1, 'b': 2}) == snapshot_digest({'b': 2, 'a': 1})
    assert snapshot_digest(SNAP) != snapshot_digest(OTHER)


def test_frozen_nan_snapshot_still_parks():
    """NaN != NaN would make a frozen feed look new every cycle and defeat the breaker."""
    nan_snap = {'BTCUSDT': (float('nan'), 1.5)}
    dog = SnapshotWatchdog()
    states = [dog.observe(nan_snap) for _ in range(3)]
    assert states[-1] == 'PARKED'


def test_reset_and_limit_validation():
    dog = SnapshotWatchdog(limit=2)
    dog.observe(SNAP)
    dog.observe(SNAP)
    assert dog.parked is True
    dog.reset()
    assert dog.parked is False and dog.repeats == 0
    assert dog.snapshot()['limit'] == 2
    for bad in (0, -1, 2.5, True):
        with pytest.raises(ValueError):
            SnapshotWatchdog(limit=bad)


# ------------------------------------------------------------ capital gate

def test_n_cap_is_fifteen_percent_of_the_hard_budget():
    assert B_HARD_USDT == pytest.approx(3.85)
    assert POSITION_FRACTION == pytest.approx(0.15)
    assert position_cap() == pytest.approx(0.5775)
    assert N_CAP_USDT == pytest.approx(position_cap())


def test_binance_minimum_forces_paper_only():
    """Venue minimum 5 USDT (+10% buffer) vs a 0.5775 cap: no compliant live order exists."""
    verdict = capital_verdict(5.0 * 1.10)
    assert verdict.mode == PAPER_ONLY
    assert verdict.live_allowed is False
    assert 'N_min' in verdict.reason and 'N_cap' in verdict.reason


def test_order_within_the_cap_executes():
    verdict = capital_verdict(0.50)
    assert verdict.mode == EXECUTE
    assert verdict.live_allowed is True
    assert verdict.n_cap == pytest.approx(0.5775)


def test_spend_plus_reserved_may_not_exceed_b_hard():
    assert capital_verdict(0.5, spend=3.0, reserved=0.9).mode == BUDGET_EXHAUSTED
    assert capital_verdict(0.5, spend=3.85, reserved=0.0).mode == BUDGET_EXHAUSTED
    # Committed exactly at the ceiling is inside it, but leaves zero headroom,
    # so the next order still cannot be funded.
    assert capital_verdict(0.5, spend=3.35, reserved=0.5).mode == BUDGET_EXHAUSTED
    # Room for the order and the ceiling both respected.
    assert capital_verdict(0.5, spend=3.0, reserved=0.0).mode == EXECUTE


def test_headroom_smaller_than_the_order_is_exhausted_not_paper():
    verdict = capital_verdict(0.5, spend=3.6, reserved=0.0)
    assert verdict.mode == BUDGET_EXHAUSTED
    assert verdict.headroom == pytest.approx(0.25)


def test_leverage_cannot_be_used_to_slip_under_the_cap():
    """N_cap is unlevered notional: a 5x-levered 5 USDT order is still 5 USDT of exposure."""
    assert capital_verdict(5.5).mode == PAPER_ONLY
    # Shrinking only the *margin* posted does not shrink the notional the gate reads.
    assert capital_verdict(5.5, spend=0.0, reserved=0.0).mode == PAPER_ONLY


@pytest.mark.parametrize('bad', [0, -1, float('nan'), float('inf')])
def test_capital_gate_rejects_invalid_inputs(bad):
    with pytest.raises(ValueError):
        capital_verdict(bad)


def test_negative_spend_is_rejected():
    with pytest.raises(ValueError):
        capital_verdict(0.5, spend=-1.0)
    with pytest.raises(ValueError):
        position_cap(fraction=0)
