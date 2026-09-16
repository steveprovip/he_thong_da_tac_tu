"""Regime gates: what the engine is allowed to do, before it asks what it wants.

Fixtures are deterministic and offline. The point of every test here is that a
missing or ambiguous input resolves to LESS permission, never more: an unknown
regime is TRANSITION, and TRANSITION does not open trades.
"""
import pytest

from research.flush_regime import (DOWN, RANGE, TRANSITION, UP, adx_di, classify_regime,
                                   confirm_long, ema_series, is_fast_down, long_permitted,
                                   long_reclaim_signal, short_break_signal, short_permitted,
                                   short_retest, wilder_atr)

MID = 0.20000
BAND = 0.00100


def bar(o, h, l, c, v=1000.0):
    return {'open': o, 'high': h, 'low': l, 'close': c, 'volume': v}


def trend_bars(n=90, step=-0.0006, start=MID, rng=0.0008):
    out, prev = [], None
    for i in range(n):
        c = start + i * step
        o = prev if prev is not None else c
        out.append(bar(o, max(o, c) + rng / 2, min(o, c) - rng / 2, c))
        prev = c
    return out


def range_bars(n=90, mid=MID, band=BAND):
    """Overlapping highs/lows: directional movement is zero, so ADX collapses."""
    offsets = (band * 0.3, -band * 0.25, 0.0)
    return [bar(mid, mid + band, mid - band, mid + offsets[i % 3]) for i in range(n)]


def flat_bars(n=30, mid=MID, half=0.0005, v=100.0):
    return [bar(mid, mid + half, mid - half, mid, v) for _ in range(n)]


# ----------------------------------------------------------------- indicators
def test_wilder_atr_seeds_with_sma_then_smooths():
    bars = flat_bars(40)
    assert wilder_atr(bars) == pytest.approx(0.0010, abs=1e-9)
    assert wilder_atr(flat_bars(10)) == 0.0, 'too little history yields 0, not a guess'


def test_ema_is_seeded_with_the_sma_of_the_first_window():
    assert ema_series([1.0] * 30, 20)[0] == pytest.approx(1.0)
    assert ema_series([1.0] * 5, 20) == []


def test_flat_tape_has_no_directional_movement():
    adx, di_p, di_m = adx_di(flat_bars(60))
    assert (adx, di_p, di_m) == (0.0, 0.0, 0.0), 'zero range must not divide by zero'


# --------------------------------------------------------------------- regime
def test_downtrend_classifies_as_down():
    reg, diag = classify_regime(trend_bars(step=-0.0006))
    assert reg == DOWN
    assert diag['di_minus'] > diag['di_plus'] and diag['slope'] <= -0.10


def test_uptrend_classifies_as_up_and_opens_nothing():
    reg, _ = classify_regime(trend_bars(step=+0.0006))
    assert reg == UP
    assert not long_permitted(reg, False, 5), 'UP is out of scope in this version'
    assert not short_permitted(reg)


def test_overlapping_bars_classify_as_range():
    reg, diag = classify_regime(range_bars())
    assert reg == RANGE and diag['adx'] <= 20.0


def test_insufficient_history_is_transition_not_permission():
    reg, _ = classify_regime(trend_bars(n=30))
    assert reg == TRANSITION
    assert not long_permitted(reg, False, 9) and not short_permitted(reg)


# ------------------------------------------------------------------ fast down
def test_fast_down_fires_on_a_break_below_the_20_bar_low():
    calm = flat_bars(30)
    atr_prev = wilder_atr(calm[:-1])
    crash = calm[:-1] + [bar(MID, MID + 0.00010, 0.19000, 0.19050, 900.0)]
    assert is_fast_down(crash, atr_prev) is True
    assert is_fast_down(calm, atr_prev) is False


def test_fast_down_forbids_long_even_in_range():
    assert long_permitted(RANGE, True, 5) is False


# ------------------------------------------------- long: AND, never the old OR
def long_setup(volume=2000.0, close=MID, low=None, high=None):
    """20 quiet bars then one flush-and-reclaim bar."""
    window = flat_bars(25, v=1000.0)
    support = min(b['low'] for b in window)
    atr_prev = wilder_atr(window)
    signal = bar(MID, high if high is not None else MID + 0.0005,
                 low if low is not None else support - 0.5 * atr_prev,
                 close, volume)
    return window + [signal], atr_prev


def test_long_needs_flush_and_reclaim_and_volume_together():
    bars, atr_prev = long_setup()
    ok, info = long_reclaim_signal(bars, atr_prev)
    assert ok and info['reason'] == 'LONG_CANDIDATE'

    thin, atr_prev = long_setup(volume=1000.0)          # volume fails alone
    ok, info = long_reclaim_signal(thin, atr_prev)
    assert not ok and info['volume'] is False and info['flush'] is True

    no_flush = flat_bars(25) + [bar(MID, MID + 0.0005, MID - 0.0001, MID, 5000.0)]
    ok, _ = long_reclaim_signal(no_flush, wilder_atr(flat_bars(25)))
    assert not ok, 'a big volume bounce without a flush is not the setup'


def test_zero_range_signal_bar_is_undefined_not_true():
    window = flat_bars(25)
    bars = window + [bar(MID, MID, MID, MID, 9000.0)]
    ok, info = long_reclaim_signal(bars, wilder_atr(window))
    assert not ok and info['reason'] == 'ZERO_RANGE'


def test_long_requires_next_bar_confirmation():
    signal = bar(MID, MID + 0.0005, MID - 0.0010, MID, 2000.0)
    assert confirm_long(signal, bar(MID, MID + 0.0012, MID - 0.0005, MID + 0.0008)) is True
    assert confirm_long(signal, bar(MID, MID + 0.0004, MID - 0.0005, MID + 0.0002)) is False
    assert confirm_long(signal, bar(MID, MID + 0.0012, MID - 0.0020, MID + 0.0008)) is False


# --------------------------------------------------- short: break then retest
def test_short_break_requires_weak_close_and_volume():
    window = flat_bars(25, v=1000.0)
    atr_prev = wilder_atr(window)
    support = min(b['low'] for b in window)
    broke = bar(MID, MID, support - 0.0010, support - 0.0008, 3000.0)
    ok, info = short_break_signal(window + [broke], atr_prev)
    assert ok and info['reason'] == 'SHORT_BREAK'

    strong_close = bar(MID, MID, support - 0.0010, support - 0.00005, 3000.0)
    ok, _ = short_break_signal(window + [strong_close], atr_prev)
    assert not ok, 'a close back near the high is not a breakdown'


def test_short_retest_must_land_inside_the_window_and_expire_after_three():
    support, atr = MID, 0.0010
    good = bar(support + 0.0001, support + 0.0002, support - 0.0006, support - 0.0004)
    assert short_retest([good], support, atr)[0] is True

    too_high = bar(support, support + 0.0009, support - 0.0002, support - 0.0004)
    assert short_retest([too_high], support, atr)[0] is False

    filler = bar(support - 0.002, support - 0.0018, support - 0.0025, support - 0.0022)
    assert short_retest([filler, filler, filler, good], support, atr)[0] is False, \
        'a retest on the fourth bar is a different setup, not this one'


# ----------------------------------------------------------------- hysteresis
def test_long_reopens_only_after_two_consecutive_range_closes():
    assert long_permitted(RANGE, False, 0) is False
    assert long_permitted(RANGE, False, 1) is False
    assert long_permitted(RANGE, False, 2) is True


def test_down_never_permits_long_however_strong_the_bounce():
    for streak in range(0, 10):
        assert long_permitted(DOWN, False, streak) is False
    assert short_permitted(DOWN) is True
