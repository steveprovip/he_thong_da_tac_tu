import numpy as np
import pytest

from tools.replay_tick_alpha import (
    TRADE_DTYPE,
    Tape,
    _crossing_events,
    _first_trade_through,
    _to_ms,
    _quote_batch,
    quote_at,
    replay_events,
    rolling_flow,
)


def tape(rows):
    arr = np.array(rows, dtype=TRADE_DTYPE)
    return Tape(arr, "BTCUSDT")


def test_source_timestamp_uses_declared_millisecond_precision():
    assert _to_ms({"source_timestamp": 1_788_220_800_003, "source_time_unit": "ms",
                   "ts_ns": 1_788_220_800_003_000_000}) == 1_788_220_800_003
    with pytest.raises(ValueError, match="expected source_timestamp in ms"):
        _to_ms({"source_timestamp": 1_788_220_800_003_000, "source_time_unit": "us"})


def test_rolling_flow_uses_causal_inclusive_window():
    t = tape([(1000, 100.0, 100.0, 1), (1500, 100.0, 100.0, -1), (2000, 100.0, 100.0, 1)])
    assert t.t.flags.c_contiguous and t.t is t.t
    imbalance, total = rolling_flow(t, np.array([2000], dtype=np.int64), 1000)
    assert total[0] == 300.0  # the 1000 ms print is on the inclusive left edge
    assert imbalance[0] == pytest.approx(1 / 3)  # +100 -100 +100 signed notional


def test_maker_fill_requires_strict_trade_through_and_costs_are_deducted():
    t = tape([
        (700, 100.0, 1.0, 1),
        (900, 99.9, 1.0, -1),
        (1100, 99.9, 1.0, -1),  # at quote: not a fill
        (1200, 99.8, 1.0, -1),  # through quote: fill
        (1600, 100.0, 1.0, 1),
    ])
    trades, stats = replay_events(t, [(1000, 1)], 500)
    assert stats == {"signals": 1, "attempted": 1, "filled": 1, "unfilled": 0,
                     "skipped_busy": 0, "fill_rate_attempted": 1.0}
    assert trades[0]["quote"] == pytest.approx(99.9)
    assert trades[0]["gross_bps"] == pytest.approx((100.0 - 99.9) / 99.9 * 10_000)
    assert trades[0]["net_bps"] == pytest.approx(trades[0]["gross_bps"] - 8.5)


def test_vectorized_quote_reconstruction_matches_scalar_reference():
    t = tape([(100, 100.0, 1.0, 1), (200, 99.9, 1.0, -1),
              (300, 100.1, 1.0, 1), (400, 100.0, 1.0, -1)])
    times = np.array([50, 150, 250, 350, 450], dtype=np.int64)
    sides = np.array([1, 1, -1, -1, 1], dtype=np.int8)
    actual = _quote_batch(t, times, sides)
    expected = [quote_at(t, int(ts), int(side)) for ts, side in zip(times, sides)]
    assert np.isnan(actual[0]) and expected[0] is None
    assert actual[1:].tolist() == pytest.approx(expected[1:])


def test_touching_quote_without_crossing_does_not_fill():
    t = tape([(700, 100.0, 1.0, 1), (900, 99.9, 1.0, -1), (1100, 99.9, 1.0, -1)])
    trades, stats = replay_events(t, [(1000, 1)], 500)
    assert trades == []
    assert stats["attempted"] == 1
    assert stats["unfilled"] == 1
    assert stats["fill_rate_attempted"] == 0.0


def test_dense_busy_events_are_prefiltered_without_changing_ledger_counts():
    t = tape([(700, 100.0, 1.0, 1), (900, 99.9, 1.0, -1),
              (1100, 100.0, 1.0, 1), (1600, 100.0, 1.0, 1),
              (2100, 100.0, 1.0, 1)])
    trades, stats = replay_events(t, [(1000, 1), (1100, 1), (1200, 1), (1500, 1)], 500)
    assert trades == []
    assert stats == {"signals": 4, "attempted": 2, "filled": 0, "unfilled": 2,
                     "skipped_busy": 2, "fill_rate_attempted": 0.0}


@pytest.mark.parametrize("side", [-1, 1])
def test_block_indexed_fill_search_matches_brute_force(side):
    rows = [(i, 100.0 + ((i % 17) - 8) * 0.01, 1.0, 1 if i % 3 else -1)
            for i in range(9000)]
    t = tape(rows)
    for first, last, quote in ((0, 8999, 100.01), (4090, 8200, 100.0), (4096, 4097, 99.95)):
        px, aggr = t.p[first:last], t.s[first:last]
        crossed = ((aggr < 0) & (px < quote)) if side > 0 else ((aggr > 0) & (px > quote))
        hits = np.flatnonzero(crossed)
        expected = first + int(hits[0]) if len(hits) else None
        assert _first_trade_through(t, first, last, side, quote, block_size=256) == expected


def test_cvd_extreme_event_requires_notional_cross_and_skew_confirmation():
    times = np.array([1000, 2000, 3000, 4000], dtype=np.int64)
    signed_notional = np.array([0.0, 700.0, 900.0, 0.0])
    skew = np.array([0.0, 0.55, 0.75, 0.0])
    assert _crossing_events(times, signed_notional, 800.0, 0, 5000, skew, 0.60) == [(3000, 1)]
