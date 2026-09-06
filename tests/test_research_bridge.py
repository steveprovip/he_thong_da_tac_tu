"""The research tier must measure production, not a copy of it.

Two properties carry the whole tier: the backtest drives the same strategy objects
the live runtime uses, and it records probes in the same schema the live SignalLab
writes -- otherwise backtest and live cannot be compared, which is the only reason
the tier exists.
"""
import json

import numpy as np
import pytest
from hftbacktest import (BacktestAsset, HashMapMarketDepthBacktest, BUY_EVENT, DEPTH_EVENT,
                         EXCH_EVENT, FILLED, GTX, LIMIT, LOCAL_EVENT, SELL_EVENT, TRADE_EVENT)
from hftbacktest.types import event_dtype

from core.groktopus_swarm import GroktopusSwarm
from core.models import Book
from core.real_alpha_engine import AlphaEngine
from core.signal_lab import SignalLab
from research.book_adapter import MinuteBars, build_book
from research.bridge import BacktestConfig, SwarmBacktest
from research.synthetic import synthetic_feed

MS = 1_000_000


class FakeDepth:
    """Minimal stand-in with the accessors the adapter uses."""
    def __init__(self, bids, asks, tick_size=0.1):
        self.tick_size = tick_size
        self._bids, self._asks = bids, asks
        self.best_bid_tick = max(bids) if bids else -1
        self.best_ask_tick = min(asks) if asks else -1

    def bid_qty_at_tick(self, tick):
        return self._bids.get(tick, 0.0)

    def ask_qty_at_tick(self, tick):
        return self._asks.get(tick, 0.0)


def run_backtest(minutes=6, **overrides):
    config = BacktestConfig(warmup_trades=150, min_notional=10.0, equity=5000.0, **overrides)
    data = synthetic_feed(seconds=minutes*60, tick_size=config.tick_size,
                          lot_size=config.lot_size, trade_rate=14.0)
    asset = (BacktestAsset().data([data]).linear_asset(1.0).power_prob_queue_model(3.0)
             .constant_latency(5*MS, 5*MS)
             .trading_value_fee_model(config.maker_fee, config.taker_fee)
             .tick_size(config.tick_size).lot_size(config.lot_size)
             .last_trades_capacity(config.last_trades_capacity)
             .no_partial_fill_exchange())
    hbt = HashMapMarketDepthBacktest([asset])
    engine = SwarmBacktest(config)
    try:
        return engine, engine.run(hbt)
    finally:
        hbt.close()


# ------------------------------------------------------------------- adapter
def test_book_adapter_builds_the_live_book_type():
    depth = FakeDepth({999: 2.0, 998: 3.0, 997: 1.5}, {1001: 2.5, 1002: 1.0})
    book = build_book('BTCUSDT', depth, 1.0, 1)
    assert isinstance(book, Book)
    assert book.bid == pytest.approx(99.9) and book.ask == pytest.approx(100.1)
    assert len(book.bids) == 3 and len(book.asks) == 2


def test_book_adapter_rejects_states_the_live_engine_would_reject():
    assert build_book('X', FakeDepth({}, {1001: 1.0}), 1.0, 1) is None
    assert build_book('X', FakeDepth({999: 1.0}, {}), 1.0, 1) is None
    # Crossed book: bid tick above ask tick.
    assert build_book('X', FakeDepth({1002: 1.0}, {1001: 1.0}), 1.0, 1) is None


def test_book_adapter_skips_empty_levels_without_inventing_liquidity():
    depth = FakeDepth({999: 1.0, 997: 2.0}, {1001: 1.0})
    book = build_book('X', depth, 1.0, 1)
    assert [round(p, 4) for p, _ in book.bids] == [99.9, 99.7]


def test_minute_bars_close_once_per_boundary():
    bars = MinuteBars(seconds=60)
    assert bars.update(0.0, 100.0) is None
    assert bars.update(30.0, 102.0) is None
    closed = bars.update(61.0, 101.0)
    assert closed is not None
    timestamp, high, low, close = closed
    assert (high, low) == (102.0, 100.0) and timestamp == 60.0
    assert bars.update(70.0, 99.0) is None


# ------------------------------------------------------------------ synthetic
def test_synthetic_feed_is_well_formed():
    data = synthetic_feed(seconds=90)
    assert data.dtype == event_dtype
    assert np.all(np.diff(data['exch_ts']) >= 0), 'events must be time ordered'
    assert (data['ev'] & DEPTH_EVENT).any() and (data['ev'] & TRADE_EVENT).any()
    assert (data['px'] > 0).all() and (data['qty'] > 0).all()


# --------------------------------------------------------------- the fee trap
def test_post_only_entry_rests_and_is_charged_the_maker_fee():
    """BUY and MARKET are both 1, so a mis-ordered argument turns a post-only
    entry into a market order that is charged 5 bps instead of 2. That mistake is
    invisible in the PnL until it has eaten the entire edge budget."""
    rows, t0 = [], 1_700_000_000_000_000_000
    for i in range(300):
        ts = t0 + i * 100 * MS
        for level in range(3):
            rows.append((EXCH_EVENT | LOCAL_EVENT | DEPTH_EVENT | BUY_EVENT, ts, ts + 100_000,
                         round(99.99 - level * .01, 2), 10.0, 0, 0, 0.0))
            rows.append((EXCH_EVENT | LOCAL_EVENT | DEPTH_EVENT | SELL_EVENT, ts, ts + 100_000,
                         round(100.01 + level * .01, 2), 10.0, 0, 0, 0.0))
        if i > 40:
            rows.append((EXCH_EVENT | LOCAL_EVENT | TRADE_EVENT | SELL_EVENT, ts + 50 * MS,
                         ts + 50 * MS + 100_000, 99.99, 3.0, 0, 0, 0.0))
    data = np.array(rows, dtype=event_dtype)
    data.sort(order='exch_ts', kind='stable')
    asset = (BacktestAsset().data([data]).linear_asset(1.0).power_prob_queue_model(3.0)
             .constant_latency(5*MS, 5*MS).trading_value_fee_model(0.0002, 0.0005)
             .tick_size(0.01).lot_size(0.001).last_trades_capacity(1000)
             .no_partial_fill_exchange())
    hbt = HashMapMarketDepthBacktest([asset])
    try:
        sent = False
        while hbt.elapse(200 * MS) == 0:
            depth = hbt.depth(0)
            if depth.best_bid <= 0:
                continue
            if not sent:
                hbt.submit_buy_order(0, 1, depth.best_bid, 0.1, GTX, LIMIT, False)
                sent = True
                continue
            order = hbt.orders(0).get(1)
            if order is not None and order.status == FILLED:
                assert order.exec_price_tick == order.price_tick, 'entry crossed instead of resting'
                break
        state = hbt.state_values(0)
        assert state.fee / state.trading_value == pytest.approx(0.0002, rel=1e-3)
    finally:
        hbt.close()


# ---------------------------------------------------------------- the bridge
def test_backtest_drives_the_production_strategy_objects():
    """A second copy of the strategy would drift; the tier is only useful if the
    thing being measured is the thing that trades."""
    engine = SwarmBacktest(BacktestConfig())
    assert isinstance(engine.alpha, AlphaEngine)
    assert isinstance(engine.swarm, GroktopusSwarm)
    assert isinstance(engine.lab, SignalLab)


def test_vpin_bucket_is_calibrated_before_any_signal_is_trusted():
    engine, report = run_backtest(minutes=6)
    assert engine._calibrated, 'warm-up never completed'
    assert report.warmup_trades_seen >= 150
    # The default bucket is 10.0; calibration must move it off that default.
    assert report.bucket_volume > 0 and report.bucket_volume != 10.0
    assert engine.alpha.buckets.volume == report.bucket_volume


def test_short_run_refuses_to_pretend_it_measured_anything():
    engine = SwarmBacktest(BacktestConfig(warmup_trades=10_000_000))
    data = synthetic_feed(seconds=120)
    asset = (BacktestAsset().data([data]).linear_asset(1.0).power_prob_queue_model(3.0)
             .constant_latency(5*MS, 5*MS).trading_value_fee_model(.0002, .0005)
             .tick_size(.1).lot_size(.001).last_trades_capacity(10_000)
             .no_partial_fill_exchange())
    hbt = HashMapMarketDepthBacktest([asset])
    try:
        report = engine.run(hbt)
    finally:
        hbt.close()
    assert any('never calibrated' in w for w in report.warnings)
    assert any('provisional' in w for w in report.warnings)


def test_backtest_reports_the_fill_rate_paper_trading_cannot():
    # features.ready needs 20 one-minute bars, so anything under ~21 minutes
    # measures only the warm-up.
    engine, report = run_backtest(minutes=26)
    assert report.signals > 0, 'features never warmed up'
    if report.entries_submitted:
        assert report.fill_rate is not None and 0.0 <= report.fill_rate <= 1.0
        # Post-only entries must never execute by crossing.
        assert report.taker_fills == 0
        assert not any('maker assumption is broken' in w for w in report.warnings)
        assert report.entries_filled + report.entries_expired + report.entries_cancelled \
            <= report.entries_submitted


def test_probes_use_the_same_schema_as_the_live_lab(tmp_path):
    """calibrate_edge.py must read backtest and live output without knowing which."""
    path = tmp_path / 'backtest-lab.jsonl'
    engine, report = run_backtest(minutes=26, lab_path=str(path))
    if not path.exists():
        pytest.skip('no probe reached a barrier in this window')
    rows = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]
    assert rows
    live_fields = {'symbol', 'side', 'entry', 'stop', 'take_profit', 'conviction', 'consensus',
                   'edge_bps', 'cost_bps', 'atr_bps', 'spread_bps', 'vpin', 'saturation',
                   'gated', 'blockers', 'outcome', 'exit_price', 'mfe_bps', 'mae_bps',
                   'gross_bps', 'net_bps'}
    assert live_fields <= set(rows[0]), 'schema drifted from core.signal_lab.Probe'
    assert all(r['outcome'] in ('TP', 'SL', 'TIMEOUT') for r in rows)
