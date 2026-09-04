#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_barrier_and_sizer.py - tests for Triple Barrier Method and Fractional Kelly Sizing.

Covers:
1. Barrier economics: mathematical viability against transaction costs.
2. TripleBarrier execution: TP, SL, Break-Even ratchet, and vertical Time decay.
3. PositionSizer: Fractional Kelly, negative-edge zeroing, warm-up, and cap limits.
4. GrokMarket & Swarm integration: position barriers and Kelly sizing in simulation.
"""

from __future__ import annotations

import pytest

from barrier_planner import (
    BarrierConfig,
    BarrierEconomics,
    BarrierError,
    BarrierTouch,
    CostModel,
    TripleBarrier,
    breakeven_win_rate,
    evaluate,
    minimum_viable_k_tp,
    sweep,
)
from position_sizer import (
    KellyStats,
    PositionSizer,
    SizerError,
    kelly_fraction,
)
from groktopus_market import (
    CloseReason,
    GrokMarket,
    GrokMarketConfig,
    OrderBook,
    Proposal,
    Side,
    VoteStance,
)
from groktopus_swarm import SwarmConfig, build_swarm


class TestBarrierEconomics:
    def test_fee_band_arithmetic(self) -> None:
        # At 6 bps ATR and 9.5 bps round trip cost, k_tp=1.5, k_sl=1.0 needs >100% win rate
        costs = CostModel(entry_fee_bps=4.0, exit_fee_bps=4.0, spread_bps=1.0, slippage_bps=0.5)
        econ = evaluate(BarrierConfig(k_tp=1.5, k_sl=1.0), atr_pct=0.06, costs=costs)
        assert not econ.viable
        assert econ.breakeven_p >= 1.0
        assert "NOT VIABLE" in econ.verdict

    def test_minimum_viable_k_tp(self) -> None:
        # Coin flip break-even (50% win rate)
        k_tp_50 = minimum_viable_k_tp(atr_pct=0.06, k_sl=1.0, cost_bps=9.5, target_win_rate=0.50)
        assert 4.15 < k_tp_50 < 4.20

    def test_viable_k_tp_4_clears_cost_band(self) -> None:
        costs = CostModel(entry_fee_bps=4.0, exit_fee_bps=4.0, spread_bps=1.0, slippage_bps=0.5)
        econ = evaluate(BarrierConfig(k_tp=4.0, k_sl=1.0), atr_pct=0.06, costs=costs)
        assert econ.viable
        assert 0.50 < econ.breakeven_p < 0.55

    def test_sweep_returns_expected_rows(self) -> None:
        rows = sweep(atr_pct=0.06, k_sl=1.0)
        assert len(rows) == 8
        assert not rows[0].viable  # k_tp = 1.0 is impossible
        assert rows[-1].viable     # k_tp = 20.0 is very viable


class TestTripleBarrier:
    def test_long_take_profit(self) -> None:
        b = TripleBarrier.from_atr(
            side="LONG", entry_cents=10_000_000, atr_cents=6000, entry_bar=10,
            config=BarrierConfig(k_tp=4.0, k_sl=1.0, max_holding_bars=15),
        )
        assert b.tp_cents == 10_000_000 + 24_000
        assert b.sl_cents == 10_000_000 - 6_000
        # Check inside range
        assert b.check(10_010_000, 12) is BarrierTouch.NONE
        # Touch TP
        assert b.check(b.tp_cents + 100, 13) is BarrierTouch.TAKE_PROFIT

    def test_long_stop_loss(self) -> None:
        b = TripleBarrier.from_atr(
            side="LONG", entry_cents=10_000_000, atr_cents=6000, entry_bar=10,
            config=BarrierConfig(k_tp=4.0, k_sl=1.0, max_holding_bars=15),
        )
        assert b.check(b.sl_cents - 10, 12) is BarrierTouch.STOP_LOSS

    def test_break_even_ratchet(self) -> None:
        b = TripleBarrier.from_atr(
            side="LONG", entry_cents=10_000_000, atr_cents=6000, entry_bar=10,
            config=BarrierConfig(k_tp=4.0, k_sl=1.0, break_even_at=0.40),
        )
        initial_sl = b.sl_cents
        # Move up past break-even trigger
        assert b.break_even_trigger_cents is not None
        touch = b.check(b.break_even_trigger_cents + 500, 12)
        assert touch is BarrierTouch.NONE
        assert b.break_even_armed is True
        assert b.sl_cents > initial_sl
        assert b.sl_cents == b.break_even_cents
        # Subsequent retracement to break-even level
        assert b.check(b.break_even_cents - 10, 13) is BarrierTouch.BREAK_EVEN

    def test_vertical_time_decay_barrier(self) -> None:
        b = TripleBarrier.from_atr(
            side="LONG", entry_cents=10_000_000, atr_cents=6000, entry_bar=10,
            config=BarrierConfig(k_tp=4.0, k_sl=1.0, max_holding_bars=15),
        )
        # Bar 24: 14 bars held -> NONE
        assert b.check(10_005_000, 24) is BarrierTouch.NONE
        # Bar 25: 15 bars held -> TIME
        assert b.check(10_005_000, 25) is BarrierTouch.TIME

    def test_short_barrier(self) -> None:
        # Test TP
        b1 = TripleBarrier.from_atr(
            side="SHORT", entry_cents=10_000_000, atr_cents=6000, entry_bar=5,
            config=BarrierConfig(k_tp=4.0, k_sl=1.0, max_holding_bars=10),
        )
        assert b1.tp_cents == 10_000_000 - 24_000
        assert b1.sl_cents == 10_000_000 + 6_000
        assert b1.check(b1.tp_cents - 10, 7) is BarrierTouch.TAKE_PROFIT

        # Test SL on fresh barrier
        b2 = TripleBarrier.from_atr(
            side="SHORT", entry_cents=10_000_000, atr_cents=6000, entry_bar=5,
            config=BarrierConfig(k_tp=4.0, k_sl=1.0, max_holding_bars=10),
        )
        assert b2.check(b2.sl_cents + 10, 7) is BarrierTouch.STOP_LOSS


class TestPositionSizer:
    def test_kelly_fraction_textbook(self) -> None:
        assert kelly_fraction(0.60, 1.0) == pytest.approx(0.20)
        assert kelly_fraction(0.50, 2.0) == pytest.approx(0.25)

    def test_negative_edge_returns_negative_fraction(self) -> None:
        assert kelly_fraction(0.42, 1.0) < 0.0

    def test_position_sizer_refuses_negative_edge(self) -> None:
        sizer = PositionSizer(min_trades=10)
        st = KellyStats(wins=42, losses=58, avg_win_bps=100.0, avg_loss_bps=100.0)
        dec = sizer.size(equity_cents=500_000, price_cents=10_000_000, stats=st)
        assert not dec.trades
        assert dec.quantity_units == 0
        assert dec.fraction == 0.0
        assert "negative" in dec.reason.lower()

    def test_position_sizer_warmup_below_min_trades(self) -> None:
        sizer = PositionSizer(min_trades=30, warmup_fraction=0.01)
        st = KellyStats(wins=5, losses=3, avg_win_bps=100.0, avg_loss_bps=100.0)
        dec = sizer.size(equity_cents=1_000_000, price_cents=10_000_000, stats=st)
        assert dec.trades
        assert dec.fraction == 0.01

    def test_position_sizer_quarter_kelly(self) -> None:
        sizer = PositionSizer(lam=0.25, max_fraction=0.15, min_trades=10)
        st = KellyStats(wins=60, losses=40, avg_win_bps=100.0, avg_loss_bps=100.0)
        # f* = 0.20 -> 0.25 * 0.20 = 0.05
        dec = sizer.size(equity_cents=1_000_000, price_cents=10_000_000, stats=st)
        assert dec.fraction == pytest.approx(0.05)
        assert dec.notional_cents in (49_999, 50_000)


class TestMarketAndSwarmBarrierIntegration:
    def test_market_executes_with_triple_barrier(self) -> None:
        from groktopus_market import build_default_stack
        from consensus_engine import build_quorum

        market, engine = build_default_stack(seed=4242, n_steps=800)
        symbol = "BTC-USD"
        p = Proposal(f"t-{symbol}-{market.step}-2", symbol, VoteStance.BUY, 2, market.mid(symbol))
        r = engine.evaluate(p, build_quorum(engine.registry, VoteStance.BUY, 0.95))
        assert r.token is not None

        pos = market.execute(
            p, r.token.token, atr_cents=5000,
            barrier_config=BarrierConfig(k_tp=4.0, k_sl=1.0, max_holding_bars=5),
        )
        assert pos.barrier is not None
        assert pos.target_cents == pos.barrier.tp_cents
        assert pos.stop_cents == pos.barrier.sl_cents
        assert pos.horizon_steps == 5

        # Force price at next step to touch take-profit
        market.paths[symbol][market.step + 1] = pos.barrier.tp_cents + 100
        market.advance(1)
        closed = market.settle()
        assert len(closed) == 1
        assert closed[0].reason is CloseReason.TAKE_PROFIT

    @pytest.mark.asyncio
    async def test_swarm_with_kelly_sizing(self) -> None:
        cfg = SwarmConfig(
            seed=778,
            adaptive_gate=False,
            triple_barrier=True,
            kelly_sizing=True,
            kelly_min_trades=5,
            risk_fraction=0.10,
        )
        swarm = build_swarm(seed=778, n_steps=100, config=cfg)
        rep = await swarm.run(10)
        assert rep is not None
        assert swarm.cycles_run > 0


