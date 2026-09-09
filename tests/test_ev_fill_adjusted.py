"""Fill-aware EV audit math (Astra Q1/Q2) — pure offline, no execution path."""
import pytest

from phan_tich.ev_fill_adjusted import (
    ev_per_intent,
    fee_expected_bps,
    sample_size_for_ev,
    sample_size_proportion,
)


def test_fee_expected_matches_venue_law():
    # q=0 -> 2+4 = 6.0 bps; q=1 -> 2+2 = 4.0 bps
    assert fee_expected_bps(0.0) == pytest.approx(6.0)
    assert fee_expected_bps(1.0) == pytest.approx(4.0)
    assert fee_expected_bps(0.28) == pytest.approx(6.0 - 2 * 0.28)
    for bad in (-0.1, 1.1, float('nan'), float('inf')):
        with pytest.raises(ValueError):
            fee_expected_bps(bad)


def test_ev_per_intent_formula():
    # Current live regime: p_fill 7.3%, gross F4 ~4.34bps, cost 6bps, no unfilled cost
    ev = ev_per_intent(0.073, 4.34)
    assert ev == pytest.approx(0.073 * (4.34 - 6.0))
    assert ev < 0  # negative: documents why scalping is blocked
    with pytest.raises(ValueError):
        ev_per_intent(1.5, 4.0)
    with pytest.raises(ValueError):
        ev_per_intent(0.5, float('nan'))


def test_sample_sizes_reproduce_astra_numbers():
    assert sample_size_proportion(0.05) == 385
    # Astra example: sigma=20bps, delta=1bps one-sided -> ~2474 (6.18*400)
    assert sample_size_for_ev(20.0, 1.0) == 2475
    with pytest.raises(ValueError):
        sample_size_for_ev(0, 1.0)
