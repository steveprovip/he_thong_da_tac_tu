#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_adaptive_and_probe.py - the new alpha signals, the adaptive gate, the probe.

    pytest tests/test_adaptive_and_probe.py -v

The theme running through this file is that a mechanism which can only loosen a
safety limit is not adaptive, and a probe which skips the machinery it is
probing proves nothing. Both are easy mistakes to make and both are tested here
directly:

* `test_volatile_regime_raises_the_bar` - the adaptive gate must be capable of
  making the threshold *stricter*, or it is a loophole with a dial on it.
* `test_the_default_is_off` - the gate ships disabled, because the measurement
  comparing it against the fixed bar came back inside one standard error.
* `test_a_refused_vote_sends_no_order` - the probe walks the real consensus path,
  so a failing vote must stop it. If it did not, the probe would be testing an
  HTTP call and nothing else.
"""

from __future__ import annotations

import json
import math
from typing import Any

import pytest

from consensus_engine import (
    EXECUTE_THRESHOLD_PCT,
    HOLD_THRESHOLD_PCT,
    AdaptiveGateConfig,
    AgentRegistry,
    ConsensusDecision,
    ConsensusEngine,
    ConsensusError,
    MarketRegime,
    Proposal,
    RegimeGate,
    VoteStance,
    build_quorum,
)
from groktopus_swarm import SwarmConfig, build_swarm
from live_execution_router import ExecutionMode, RouterError, _fake_venue
from probe_trade import PROBE_PREFIX, run_probe
from real_alpha_engine import Candle, RealAlphaEngine


def bars(specs: list[tuple[float, float, float, float, float]]) -> list[Candle]:
    """(open, high, low, close, volume) -> candles."""
    return [Candle(float(i), o, h, l, c, v) for i, (o, h, l, c, v) in enumerate(specs)]


# ---------------------------------------------------------------------------
# The new signals
# ---------------------------------------------------------------------------


class TestVolumeDelta:
    def test_closes_on_highs_read_as_buying(self) -> None:
        cvd = RealAlphaEngine.calculate_volume_delta(bars([(10, 12, 9, 12, 100)] * 5))
        assert cvd == pytest.approx(1.0)

    def test_closes_on_lows_read_as_selling(self) -> None:
        cvd = RealAlphaEngine.calculate_volume_delta(bars([(10, 12, 9, 9, 100)] * 5))
        assert cvd == pytest.approx(-1.0)

    def test_mid_range_closes_are_neutral(self) -> None:
        assert RealAlphaEngine.calculate_volume_delta(
            bars([(10, 12, 8, 10, 100)] * 5)) == pytest.approx(0.0)

    def test_volume_weights_the_bars(self) -> None:
        mixed = bars([(10, 12, 9, 12, 900), (10, 12, 9, 9, 100)])
        assert RealAlphaEngine.calculate_volume_delta(mixed) > 0.5

    def test_bounded_to_minus_one_and_one(self) -> None:
        for spec in ([(10, 12, 9, 12, 1e9)] * 30, [(10, 12, 9, 9, 1e9)] * 30):
            v = RealAlphaEngine.calculate_volume_delta(bars(spec))
            assert -1.0 <= v <= 1.0

    def test_degenerate_input_is_zero_not_a_crash(self) -> None:
        assert RealAlphaEngine.calculate_volume_delta([]) == 0.0
        assert RealAlphaEngine.calculate_volume_delta(bars([(10, 10, 10, 10, 0)] * 3)) == 0.0


class TestVwap:
    def test_vwap_is_volume_weighted_not_a_mean(self) -> None:
        b = bars([(10, 10, 10, 10, 1), (20, 20, 20, 20, 99)])
        assert RealAlphaEngine.calculate_vwap(b) > 19.0

    def test_zscore_is_zero_on_a_flat_series(self) -> None:
        assert RealAlphaEngine.calculate_vwap_zscore(bars([(10, 10, 10, 10, 5)] * 20)) == 0.0

    def test_zscore_is_positive_when_spot_leads_vwap(self) -> None:
        b = bars([(10 + i * 0.1, 10 + i * 0.1, 10 + i * 0.1, 10 + i * 0.1, 5) for i in range(40)])
        assert RealAlphaEngine.calculate_vwap_zscore(b) > 0

    def test_zscore_is_bounded(self) -> None:
        b = bars([(1, 1, 1, 1, 1)] * 39 + [(500, 500, 500, 500, 1)])
        assert abs(RealAlphaEngine.calculate_vwap_zscore(b)) <= 6.0

    def test_too_short_a_window_is_zero(self) -> None:
        assert RealAlphaEngine.calculate_vwap_zscore(bars([(10, 10, 10, 10, 5)] * 4)) == 0.0


class TestLiquiditySweep:
    def test_bullish_sweep_of_the_low(self) -> None:
        b = bars([(10, 10.2, 9.8, 10, 5)] * 20 + [(10, 10.1, 9.0, 10.05, 40)])
        assert RealAlphaEngine.detect_liquidity_sweep(b) > 0

    def test_bearish_sweep_of_the_high(self) -> None:
        b = bars([(10, 10.2, 9.8, 10, 5)] * 20 + [(10, 11.5, 9.95, 10.0, 40)])
        assert RealAlphaEngine.detect_liquidity_sweep(b) < 0

    def test_an_ordinary_bar_is_not_a_sweep(self) -> None:
        b = bars([(10, 10.2, 9.8, 10, 5)] * 21)
        assert RealAlphaEngine.detect_liquidity_sweep(b) == 0.0

    def test_a_clean_breakout_is_not_a_sweep(self) -> None:
        # Closes beyond the level with a big body: that is a breakout, not stops
        # being run, and reading it as a reversal would be exactly backwards.
        b = bars([(10, 10.2, 9.8, 10, 5)] * 20 + [(10, 11.5, 10.0, 11.45, 40)])
        assert RealAlphaEngine.detect_liquidity_sweep(b) == 0.0

    def test_bounded(self) -> None:
        b = bars([(10, 10.2, 9.8, 10, 5)] * 20 + [(10, 10.1, 0.5, 10.05, 40)])
        assert -1.0 <= RealAlphaEngine.detect_liquidity_sweep(b) <= 1.0

    def test_short_history_is_zero(self) -> None:
        assert RealAlphaEngine.detect_liquidity_sweep(bars([(10, 11, 9, 10, 5)] * 3)) == 0.0


class TestMarketVolume:
    def test_the_market_has_a_volume_series(self) -> None:
        s = build_swarm(seed=5, n_steps=400)
        v = [s.market.volume_at("BTC-USD", i) for i in range(10)]
        assert all(isinstance(x, int) and x > 0 for x in v)

    def test_volume_cannot_be_read_from_the_future_on_a_live_symbol(self) -> None:
        from groktopus_market import LiveModeError  # noqa: PLC0415
        from market_feed import MarketFeed, _fake_transport  # noqa: PLC0415

        s = build_swarm(seed=6, n_steps=400)
        feed = MarketFeed("coinbase", transport=_fake_transport({"BTC-USD": "64125.00"}))
        feed.fetch("BTC-USD")
        s.market.attach_live_feed(feed, ["BTC-USD"])
        with pytest.raises(LiveModeError):
            s.market.volume_at("BTC-USD", s.market.step + 5)

    def test_candles_carry_real_ranges_and_volume(self) -> None:
        s = build_swarm(seed=7, n_steps=800)
        s.market.advance(200)
        cs = s._build_candles("BTC-USD", 120)
        assert len(cs) >= 2
        assert all(c.high >= c.low for c in cs)
        assert any(c.high > c.low for c in cs), "every bar is flat; bar_steps may be 1"
        assert all(c.volume > 0 for c in cs)


# ---------------------------------------------------------------------------
# The adaptive gate
# ---------------------------------------------------------------------------


class TestAdaptiveGate:
    def test_the_default_is_off(self) -> None:
        assert AdaptiveGateConfig().enabled is False
        assert SwarmConfig().adaptive_gate is False

    def test_disabled_returns_the_fixed_bar(self) -> None:
        g = AdaptiveGateConfig().classify(atr_pct=0.01, momentum_sigma=0.0)
        assert g.regime is MarketRegime.UNKNOWN
        assert g.execute_pct == EXECUTE_THRESHOLD_PCT
        assert g.hold_pct == HOLD_THRESHOLD_PCT
        assert g.size_multiplier == 1.0

    def test_volatile_regime_raises_the_bar(self) -> None:
        # The property that makes this adaptive rather than a loophole.
        g = AdaptiveGateConfig(enabled=True).classify(atr_pct=1.2, momentum_sigma=2.0)
        assert g.regime is MarketRegime.VOLATILE
        assert g.execute_pct > EXECUTE_THRESHOLD_PCT
        assert g.size_multiplier < 1.0

    def test_volatility_outranks_trend(self) -> None:
        g = AdaptiveGateConfig(enabled=True).classify(atr_pct=1.5, momentum_sigma=5.0)
        assert g.regime is MarketRegime.VOLATILE

    def test_chop_lowers_the_bar_and_halves_size(self) -> None:
        g = AdaptiveGateConfig(enabled=True).classify(
            atr_pct=0.02, momentum_sigma=0.1, spread_bps=3.0)
        assert g.regime is MarketRegime.CHOP
        assert g.execute_pct < EXECUTE_THRESHOLD_PCT
        assert g.size_multiplier <= 0.5
        assert "unmeasured" in g.rationale

    def test_a_wide_book_forfeits_the_chop_discount(self) -> None:
        g = AdaptiveGateConfig(enabled=True).classify(
            atr_pct=0.02, momentum_sigma=0.1, spread_bps=50.0)
        assert g.regime is not MarketRegime.CHOP
        assert g.execute_pct == EXECUTE_THRESHOLD_PCT

    def test_trending_keeps_the_standard_bar(self) -> None:
        g = AdaptiveGateConfig(enabled=True).classify(atr_pct=0.2, momentum_sigma=1.5)
        assert g.regime is MarketRegime.TRENDING
        assert g.execute_pct == EXECUTE_THRESHOLD_PCT

    def test_inverted_thresholds_refused(self) -> None:
        with pytest.raises(ConsensusError):
            AdaptiveGateConfig(chop_execute_pct=50.0, chop_hold_pct=80.0)

    def test_overlapping_regime_bounds_refused(self) -> None:
        with pytest.raises(ConsensusError):
            AdaptiveGateConfig(chop_atr_max_pct=1.0, volatile_atr_min_pct=0.5)

    def test_a_bad_gate_object_is_refused(self) -> None:
        with pytest.raises(ConsensusError):
            RegimeGate(MarketRegime.CHOP, execute_pct=50.0, hold_pct=80.0)
        with pytest.raises(ConsensusError):
            RegimeGate(MarketRegime.CHOP, 80.0, 70.0, size_multiplier=2.0)


class TestGateAppliedToVotes:
    @pytest.fixture
    def setup(self):
        reg = AgentRegistry()
        return reg, ConsensusEngine(reg), Proposal("g-1", "BTC-USD", VoteStance.BUY, 1, 100)

    def test_no_gate_means_the_strict_bar(self, setup) -> None:
        reg, engine, p = setup
        r = engine.evaluate(p, build_quorum(reg, VoteStance.BUY, 0.74))
        assert r.decision is ConsensusDecision.HOLD
        assert r.as_dict()["execute_threshold_pct"] == 76.8

    def test_a_chop_gate_lets_74_percent_through(self, setup) -> None:
        reg, engine, p = setup
        gate = AdaptiveGateConfig(enabled=True).classify(
            atr_pct=0.02, momentum_sigma=0.1, spread_bps=3.0)
        r = engine.evaluate(p, build_quorum(reg, VoteStance.BUY, 0.74), gate=gate)
        assert r.decision is ConsensusDecision.EXECUTE
        assert r.gate.regime is MarketRegime.CHOP

    def test_a_volatile_gate_refuses_78_percent(self, setup) -> None:
        reg, engine, p = setup
        gate = AdaptiveGateConfig(enabled=True).classify(atr_pct=1.5, momentum_sigma=0.2)
        r = engine.evaluate(p, build_quorum(reg, VoteStance.BUY, 0.78), gate=gate)
        assert r.decision is not ConsensusDecision.EXECUTE

    def test_every_result_records_the_bar_it_was_judged_against(self, setup) -> None:
        reg, engine, p = setup
        gate = AdaptiveGateConfig(enabled=True).classify(
            atr_pct=0.02, momentum_sigma=0.1, spread_bps=3.0)
        for conf in (0.95, 0.70, 0.40):
            d = engine.evaluate(p, build_quorum(reg, VoteStance.BUY, conf), gate=gate).as_dict()
            assert d["gate"]["regime"] == "CHOP"
            assert d["execute_threshold_pct"] == 72.0
            assert d["fixed_execute_threshold_pct"] == 76.8

    def test_rune_veto_still_beats_any_gate(self, setup) -> None:
        reg, engine, p = setup
        gate = AdaptiveGateConfig(enabled=True).classify(
            atr_pct=0.02, momentum_sigma=0.1, spread_bps=3.0)
        r = engine.evaluate(p, build_quorum(reg, VoteStance.BUY, 0.99, veto=True), gate=gate)
        assert r.decision is ConsensusDecision.VETO

    def test_quorum_is_still_required_under_any_gate(self, setup) -> None:
        reg, engine, p = setup
        gate = AdaptiveGateConfig(enabled=True).classify(
            atr_pct=0.02, momentum_sigma=0.1, spread_bps=3.0)
        votes = build_quorum(reg, VoteStance.BUY, 0.99)[:-1]
        assert engine.evaluate(p, votes, gate=gate).decision is ConsensusDecision.VETO

    def test_points_short_is_reported(self, setup) -> None:
        reg, engine, p = setup
        d = engine.evaluate(p, build_quorum(reg, VoteStance.BUY, 0.60)).as_dict()
        assert d["points_short"] == pytest.approx(16.8, abs=0.01)


# ---------------------------------------------------------------------------
# Why-no-trade telemetry
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def swarm():
    import asyncio  # noqa: PLC0415

    s = build_swarm(seed=4242, n_steps=2_000)
    asyncio.run(s.run(400))
    return s


class TestBlockingReport:
    def test_reports_the_gap_to_the_bar(self, swarm) -> None:
        b = swarm.blocking_report()
        assert b["required_pct"] > 0
        assert b["points_short"] == pytest.approx(
            max(0.0, b["required_pct"] - b["score_pct"]), abs=0.01)
        assert 0 <= b["progress_pct"] <= 100

    def test_names_a_reason(self, swarm) -> None:
        assert swarm.blocking_report()["blocked_reason"]

    def test_breaks_the_vote_down_by_agent(self, swarm) -> None:
        rows = swarm.blocking_report()["agent_breakdown"]
        assert len(rows) == 6
        assert all(r["rationale"] for r in rows)

    def test_identifies_the_biggest_drag(self, swarm) -> None:
        b = swarm.blocking_report()
        assert b["biggest_drag"] in swarm.registry.analyst_names()

    def test_counts_every_decision_band(self, swarm) -> None:
        c = swarm.blocking_report()["decision_counts"]
        assert set(c) == {"EXECUTE", "HOLD", "VETO", "SKIP", "WARMUP"}
        assert sum(c.values()) > 0

    def test_reaches_telemetry_and_is_json_safe(self, swarm) -> None:
        blob = json.loads(json.dumps(swarm.telemetry()))
        assert "points_short" in blob["consensus"]
        assert "blocked_reason" in blob["consensus"]

    def test_a_fresh_swarm_says_so(self) -> None:
        b = build_swarm(seed=9, n_steps=400).blocking_report()
        assert "no proposal" in b["blocked_reason"]


# ---------------------------------------------------------------------------
# The probe
# ---------------------------------------------------------------------------


class TestProbeTrade:
    def test_the_paper_probe_walks_the_whole_path(self, tmp_path) -> None:
        r = run_probe(db_path=tmp_path / "p.db")
        assert r.completed, r.as_dict()
        assert [s.name for s in r.steps][:4] == [
            "1. build proposal", "2. synthetic quorum",
            "3. consensus gate", "4. single-use AuthToken",
        ]

    def test_a_refused_vote_sends_no_order(self, tmp_path) -> None:
        # If the probe could fire anyway it would be testing an HTTP call, not
        # the pipeline, which is the whole reason it exists.
        seen: list[str] = []
        r = run_probe(confidence=0.30, db_path=tmp_path / "p.db",
                      transport=_fake_venue(seen=seen))
        assert not r.completed
        assert r.failed_at == "3. consensus gate"
        assert r.order is None
        assert seen == []

    def test_dry_run_contacts_nothing(self, tmp_path) -> None:
        seen: list[str] = []
        r = run_probe(dry_run=True, db_path=tmp_path / "p.db",
                      transport=_fake_venue(seen=seen))
        assert r.completed and seen == []

    def test_testnet_sends_entry_then_stop(self, tmp_path) -> None:
        seen: list[str] = []
        r = run_probe(
            mode=ExecutionMode.TESTNET, transport=_fake_venue(seen=seen),
            db_path=tmp_path / "p.db",
            env={"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"},
        )
        assert r.completed and r.protected
        assert any("STOP_MARKET" in u for u in seen)

    def test_a_missing_stop_fails_the_probe(self, tmp_path) -> None:
        r = run_probe(
            mode=ExecutionMode.TESTNET, transport=_fake_venue(stop=RouterError("margin")),
            db_path=tmp_path / "p.db",
            env={"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"},
        )
        assert not r.completed

    def test_mainnet_is_refused_by_the_edge_gate(self, tmp_path) -> None:
        r = run_probe(mode=ExecutionMode.MAINNET, db_path=tmp_path / "p.db", env={})
        assert not r.completed
        assert r.failed_at == "5. router init"

    def test_probe_orders_are_tagged(self, tmp_path) -> None:
        r = run_probe(db_path=tmp_path / "p.db")
        assert r.order["client_order_id"].startswith(PROBE_PREFIX)

    def test_the_probe_never_raises(self, tmp_path) -> None:
        def explode(*a: Any, **k: Any) -> dict:
            raise RuntimeError("venue on fire")

        r = run_probe(mode=ExecutionMode.TESTNET, transport=explode,
                      db_path=tmp_path / "p.db",
                      env={"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"})
        assert not r.completed
        json.dumps(r.as_dict())
