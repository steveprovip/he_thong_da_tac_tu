#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_groktopus_market.py - the paper exchange's invariants.

    pytest tests/test_groktopus_market.py -v

Five properties, and every test here serves one of them:

1. **Money is integers and it is conserved.** Equity equals starting capital plus
   realised P&L, to the cent, after two thousand round trips. Net P&L equals
   gross minus both fees, exactly, on every single trade.
2. **Zero overdraft.** Cash is never negative. Not after a losing streak, not
   after a forced liquidation, not under a plan that tries to spend everything.
3. **No position exists without a consensus token.** A forged token, a spent
   token, an expired token, or a token minted for a different order all leave the
   book untouched - no partial fill, no smaller size, nothing.
4. **The book is finite.** Size beyond visible depth is refused rather than
   filled at an invented price, and slippage grows with size.
5. **The Sentry binds.** A tripped kill switch stops new risk and flattens what
   is open.

The campaign tests are the ones that would catch a subtle accounting drift: a
half-cent rounding error that is invisible on one trade is $10 after 2,000.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from consensus_engine import (
    AgentRegistry,
    ConsensusEngine,
    Proposal,
    VoteStance,
    build_quorum,
)
from groktopus_market import (
    DEFAULT_SYMBOLS,
    EXECUTION_MODE,
    START_EQUITY_CENTS,
    CampaignConfig,
    CloseReason,
    GrokMarket,
    GrokMarketConfig,
    LiquidityError,
    MarketError,
    MarketHalted,
    Side,
    SymbolSpec,
    UnauthorisedOrder,
    build_default_stack,
    run_paper_campaign,
    synthetic_votes,
)
from risk_guard import OverdraftError, RiskGuard, RiskGuardConfig


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def stack() -> tuple[GrokMarket, ConsensusEngine]:
    return build_default_stack(seed=4242, n_steps=800)


@pytest.fixture
def market(stack) -> GrokMarket:
    return stack[0]


@pytest.fixture
def engine(stack) -> ConsensusEngine:
    return stack[1]


def approve(
    market: GrokMarket,
    engine: ConsensusEngine,
    symbol: str,
    side: VoteStance,
    qty: int,
    *,
    confidence: float = 0.95,
) -> tuple[Proposal, str]:
    """Run a proposal through a genuine unanimous vote and hand back its token."""
    p = Proposal(f"t-{symbol}-{market.step}-{qty}", symbol, side, qty, market.mid(symbol))
    r = engine.evaluate(p, build_quorum(engine.registry, side, confidence))
    assert r.token is not None, r.reason
    return p, r.token.token


# ---------------------------------------------------------------------------
# Configuration and instruments
# ---------------------------------------------------------------------------


class TestConfiguration:
    def test_execution_mode_is_pinned(self) -> None:
        assert EXECUTION_MODE == "SIMULATION"

    def test_no_network_transport_exists_in_the_module(self) -> None:
        # The safety claim is "the capability does not exist", so assert on the
        # source rather than on a runtime flag a caller could flip.
        import groktopus_market  # noqa: PLC0415

        src = Path(groktopus_market.__file__).read_text(encoding="utf-8")
        for forbidden in ("import socket", "urllib.request", "import requests", "http.client"):
            assert forbidden not in src, f"{forbidden} appeared in a module that must not egress"

    def test_eight_symbols_matching_the_agent_config(self) -> None:
        assert len(DEFAULT_SYMBOLS) == 8
        declared = set(AgentRegistry().symbols)
        assert declared == {s.symbol for s in DEFAULT_SYMBOLS}

    def test_start_equity_is_150k_in_cents(self) -> None:
        assert START_EQUITY_CENTS == 15_000_000
        assert START_EQUITY_CENTS / 100 == 150_000.0

    def test_duplicate_symbol_refused(self) -> None:
        dup = (DEFAULT_SYMBOLS[0], DEFAULT_SYMBOLS[0])
        with pytest.raises(MarketError, match="duplicate"):
            GrokMarketConfig(symbols=dup)

    @pytest.mark.parametrize("bad", [15000000.0, "15000000", True])
    def test_start_equity_must_be_an_int(self, bad: object) -> None:
        with pytest.raises(MarketError):
            GrokMarketConfig(start_equity_cents=bad)

    def test_symbol_spec_validates(self) -> None:
        with pytest.raises(MarketError):
            SymbolSpec("BAD", 0, vol_bps=10.0, spread_bps=2.0)
        with pytest.raises(MarketError):
            SymbolSpec("BAD", 100, vol_bps=0.0, spread_bps=2.0)

    def test_same_seed_same_paths(self) -> None:
        a, _ = build_default_stack(seed=99, n_steps=200)
        b, _ = build_default_stack(seed=99, n_steps=200)
        assert a.paths == b.paths

    def test_different_seed_different_paths(self) -> None:
        a, _ = build_default_stack(seed=1, n_steps=200)
        b, _ = build_default_stack(seed=2, n_steps=200)
        assert a.paths != b.paths


# ---------------------------------------------------------------------------
# The book
# ---------------------------------------------------------------------------


class TestOrderBook:
    def test_bid_below_ask(self, market: GrokMarket) -> None:
        for sym in market.specs:
            b = market.book(sym)
            assert b.best_bid < b.mid_cents < b.best_ask
            assert b.spread_cents > 0

    def test_prices_are_integers(self, market: GrokMarket) -> None:
        b = market.book("BTC-USD")
        assert all(isinstance(l.price_cents, int) for l in b.bids + b.asks)

    def test_asks_ascend_and_bids_descend(self, market: GrokMarket) -> None:
        b = market.book("ETH-USD")
        asks = [l.price_cents for l in b.asks]
        bids = [l.price_cents for l in b.bids]
        assert asks == sorted(asks)
        assert bids == sorted(bids, reverse=True)

    def test_walking_beyond_depth_refused(self, market: GrokMarket) -> None:
        b = market.book("BTC-USD")
        with pytest.raises(LiquidityError, match="cannot absorb"):
            b.walk(Side.LONG, b.depth(Side.LONG) + 1)

    def test_exact_depth_is_fillable(self, market: GrokMarket) -> None:
        b = market.book("BTC-USD")
        gross, touched = b.walk(Side.LONG, b.depth(Side.LONG))
        assert gross > 0
        assert touched == len(b.asks)

    @pytest.mark.parametrize("qty", [0, -5, 2.5, True])
    def test_bad_quantity_refused(self, market: GrokMarket, qty: object) -> None:
        with pytest.raises(MarketError):
            market.book("BTC-USD").walk(Side.LONG, qty)

    def test_small_order_pays_only_the_touch(self, market: GrokMarket) -> None:
        b = market.book("XAU-USD")
        gross, touched = b.walk(Side.LONG, 1)
        assert touched == 1
        assert gross == b.best_ask

    def test_slippage_grows_with_size(self, market: GrokMarket) -> None:
        small = market.quote("SOL-USD", Side.LONG, 100)["slippage_bps"]
        mid = market.quote("SOL-USD", Side.LONG, 3_000)["slippage_bps"]
        large = market.quote("SOL-USD", Side.LONG, 12_000)["slippage_bps"]
        assert small < mid < large

    def test_slippage_is_a_cost_on_both_sides(self, market: GrokMarket) -> None:
        assert market.quote("SOL-USD", Side.LONG, 500)["slippage_bps"] > 0
        assert market.quote("SOL-USD", Side.SHORT, 500)["slippage_bps"] > 0

    def test_quote_does_not_move_money(self, market: GrokMarket) -> None:
        before = market.cash_cents
        market.quote("BTC-USD", Side.LONG, 3)
        assert market.cash_cents == before
        assert market.positions == {}

    def test_unknown_symbol_refused(self, market: GrokMarket) -> None:
        with pytest.raises(MarketError, match="unknown symbol"):
            market.book("DOGE-MOON")


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


class TestAuthorisation:
    def test_forged_token_opens_nothing(self, market: GrokMarket, engine: ConsensusEngine) -> None:
        p, _ = approve(market, engine, "BTC-USD", VoteStance.BUY, 2)
        before = market.cash_cents
        with pytest.raises(UnauthorisedOrder):
            market.execute(p, "deadbeef.cafe")
        assert market.positions == {}
        assert market.cash_cents == before
        assert market.rejections["unauthorised"] == 1

    def test_token_spends_once(self, market: GrokMarket, engine: ConsensusEngine) -> None:
        p, tok = approve(market, engine, "BTC-USD", VoteStance.BUY, 2)
        market.execute(p, tok)
        assert len(market.positions) == 1
        with pytest.raises(UnauthorisedOrder):
            market.execute(p, tok)
        assert len(market.positions) == 1

    def test_token_will_not_cover_a_bigger_order(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        p, tok = approve(market, engine, "BTC-USD", VoteStance.BUY, 2)
        inflated = Proposal(p.proposal_id, p.symbol, p.side, 20, p.price_cents)
        with pytest.raises(UnauthorisedOrder):
            market.execute(inflated, tok)
        assert market.positions == {}

    def test_hold_band_mints_nothing_to_execute_with(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        p = Proposal("hold-1", "BTC-USD", VoteStance.BUY, 1, market.mid("BTC-USD"))
        r = engine.evaluate(p, build_quorum(engine.registry, VoteStance.BUY, 0.73))
        assert r.token is None
        with pytest.raises(UnauthorisedOrder):
            market.execute(p, "")

    def test_market_without_an_authoriser_refuses_everything(self) -> None:
        m = GrokMarket(GrokMarketConfig(seed=1, n_steps=100))
        p = Proposal("x", "BTC-USD", VoteStance.BUY, 1, m.mid("BTC-USD"))
        with pytest.raises(UnauthorisedOrder, match="no consensus authoriser"):
            m.execute(p, "anything")


# ---------------------------------------------------------------------------
# Accounting
# ---------------------------------------------------------------------------


class TestAccounting:
    def test_all_money_is_integer_cents(self, market: GrokMarket) -> None:
        for value in (market.cash_cents, market.equity_cents(), market.position_value_cents()):
            assert isinstance(value, int) and not isinstance(value, bool)

    def test_opening_costs_only_the_fee_and_the_spread(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        before = market.equity_cents()
        p, tok = approve(market, engine, "XAU-USD", VoteStance.BUY, 10)
        pos = market.execute(p, tok)
        after = market.equity_cents()
        assert after < before, "crossing the spread must cost something"
        assert before - after < pos.entry_gross_cents // 100, "but not a hundredth of notional"

    @pytest.mark.parametrize("side", [VoteStance.BUY, VoteStance.SELL])
    def test_round_trip_net_equals_gross_minus_fees(
        self, market: GrokMarket, engine: ConsensusEngine, side: VoteStance
    ) -> None:
        before = market.equity_cents()
        p, tok = approve(market, engine, "ETH-USD", side, 20)
        market.execute(p, tok)
        market.advance(3)
        trade = market.flatten_all()[0]
        assert trade.net_pnl_cents == trade.gross_pnl_cents - trade.fees_cents
        assert market.equity_cents() == before + trade.net_pnl_cents
        assert market.positions == {}

    def test_short_is_fully_collateralised(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        cash_before = market.cash_cents
        p, tok = approve(market, engine, "ETH-USD", VoteStance.SELL, 20)
        pos = market.execute(p, tok)
        # A short reduces cash by the notional, exactly as a long does. Selling
        # something you do not own must not create spendable money.
        assert market.cash_cents == cash_before - pos.locked_cents
        assert market.cash_cents >= 0

    def test_equity_equals_start_plus_realised_when_flat(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        for i, sym in enumerate(list(market.specs)[:4]):
            side = VoteStance.BUY if i % 2 else VoteStance.SELL
            qty = max(1, min(4, market.max_affordable_qty(sym, Side.from_stance(side))))
            p, tok = approve(market, engine, sym, side, qty)
            market.execute(p, tok)
            market.advance(2)
        market.flatten_all()
        assert market.equity_cents() == market.config.start_equity_cents + market.realised_cents()

    def test_overdraft_refused_and_leaves_no_trace(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        qty = market.max_affordable_qty("BTC-USD", Side.LONG) + 1
        p, tok = approve(market, engine, "BTC-USD", VoteStance.BUY, qty)
        before = market.cash_cents
        with pytest.raises(OverdraftError, match="zero overdraft"):
            market.execute(p, tok)
        assert market.cash_cents == before
        assert market.positions == {}
        assert market.rejections["overdraft"] == 1

    def test_max_affordable_is_actually_affordable(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        qty = market.max_affordable_qty("SOL-USD", Side.LONG)
        assert qty >= 1
        p, tok = approve(market, engine, "SOL-USD", VoteStance.BUY, qty)
        market.execute(p, tok)
        assert market.cash_cents >= 0

    def test_cash_never_negative_under_a_greedy_plan(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        for _ in range(30):
            qty = market.max_affordable_qty("EUR-USD", Side.LONG)
            if qty < 1:
                break
            p, tok = approve(market, engine, "EUR-USD", VoteStance.BUY, qty)
            try:
                market.execute(p, tok)
            except (OverdraftError, LiquidityError):
                break
            market.advance(1)
            assert market.cash_cents >= 0
        assert market.cash_cents >= 0

    def test_fees_are_always_paid_never_earned(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        p, tok = approve(market, engine, "NDX-IDX", VoteStance.BUY, 3)
        market.execute(p, tok)
        market.advance(2)
        trade = market.flatten_all()[0]
        assert trade.fees_cents > 0


# ---------------------------------------------------------------------------
# Settlement (MARIN)
# ---------------------------------------------------------------------------


class TestSettlement:
    def test_horizon_closes_the_position(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        p, tok = approve(market, engine, "XAU-USD", VoteStance.BUY, 5)
        market.execute(p, tok, horizon_steps=3, stop_bps=9_000, target_bps=9_000)
        market.advance(2)
        assert market.settle() == []
        market.advance(1)
        closed = market.settle()
        assert len(closed) == 1
        assert closed[0].reason is CloseReason.HORIZON

    def test_stop_fires_before_target_when_both_are_touched(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        p, tok = approve(market, engine, "SOL-USD", VoteStance.BUY, 50)
        pos = market.execute(p, tok, horizon_steps=99, stop_bps=1.0, target_bps=1.0)
        market.advance(1)
        closed = market.settle()
        assert len(closed) == 1
        assert closed[0].reason is CloseReason.STOP_LOSS

    def test_take_profit_fires(self, market: GrokMarket, engine: ConsensusEngine) -> None:
        # A one-basis-point target with the stop and horizon pushed far out, so the
        # only rule that can fire is the target. The mid still has to climb past the
        # ask it was filled at, which takes a few steps of a driftless walk.
        p, tok = approve(market, engine, "SOL-USD", VoteStance.BUY, 50)
        market.execute(p, tok, horizon_steps=10_000, stop_bps=9_000, target_bps=1.0)
        closed: list = []
        for _ in range(80):
            market.advance(1)
            closed = market.settle()
            if closed:
                break
        assert len(closed) == 1, "the target never came within reach in 80 steps"
        assert closed[0].reason is CloseReason.TAKE_PROFIT

    def test_settle_is_idempotent_when_nothing_is_due(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        p, tok = approve(market, engine, "XAU-USD", VoteStance.BUY, 5)
        market.execute(p, tok, horizon_steps=50, stop_bps=9_000, target_bps=9_000)
        assert market.settle() == []
        assert market.settle() == []
        assert len(market.positions) == 1

    def test_closing_an_unknown_position_raises(self, market: GrokMarket) -> None:
        with pytest.raises(MarketError, match="no open position"):
            market.close("POS-999999")

    def test_flatten_all_empties_the_book(
        self, market: GrokMarket, engine: ConsensusEngine
    ) -> None:
        for sym in list(market.specs)[:3]:
            p, tok = approve(market, engine, sym, VoteStance.BUY, 2)
            market.execute(p, tok)
        assert len(market.positions) == 3
        closed = market.flatten_all()
        assert len(closed) == 3
        assert market.positions == {}
        assert all(t.reason is CloseReason.END_OF_RUN for t in closed)


# ---------------------------------------------------------------------------
# The Sentry
# ---------------------------------------------------------------------------


@pytest.fixture
def guarded(tmp_path: Path) -> tuple[GrokMarket, ConsensusEngine, RiskGuard]:
    g = RiskGuard(
        RiskGuardConfig(
            budget_cap_minor=10_000,
            db_path=tmp_path / "guard.db",
            kill_file=tmp_path / "KILL_SWITCH",
            allow_unmeasurable_hardware=True,
        )
    )
    e = ConsensusEngine(AgentRegistry())
    m = GrokMarket(GrokMarketConfig(seed=808, n_steps=400, guard=g), authoriser=e)
    yield m, e, g
    g.close()


class TestSentryIntegration:
    def test_kill_switch_blocks_new_risk(self, guarded) -> None:
        m, e, g = guarded
        g.trip_kill_switch("test halt")
        p, tok = approve(m, e, "BTC-USD", VoteStance.BUY, 1)
        with pytest.raises(MarketHalted, match="kill switch"):
            m.execute(p, tok)
        assert m.positions == {}
        assert m.rejections["halted"] == 1

    def test_kill_switch_flattens_open_positions(self, guarded) -> None:
        m, e, g = guarded
        p, tok = approve(m, e, "BTC-USD", VoteStance.BUY, 1)
        m.execute(p, tok, horizon_steps=99, stop_bps=9_000, target_bps=9_000)
        m.advance(1)
        g.trip_kill_switch("emergency")
        closed = m.settle()
        assert len(closed) == 1
        assert closed[0].reason is CloseReason.KILL_SWITCH
        assert m.positions == {}

    def test_operator_halt_stops_new_risk(self, guarded) -> None:
        m, e, _ = guarded
        m.halt("maintenance")
        p, tok = approve(m, e, "BTC-USD", VoteStance.BUY, 1)
        with pytest.raises(MarketHalted, match="operator"):
            m.execute(p, tok)

    def test_compute_cost_is_charged_to_the_ledger(self, tmp_path: Path) -> None:
        g = RiskGuard(
            RiskGuardConfig(
                budget_cap_minor=10_000,
                db_path=tmp_path / "g.db",
                kill_file=tmp_path / "K",
                allow_unmeasurable_hardware=True,
            )
        )
        g.credit(1_000, label="test funding")
        e = ConsensusEngine(AgentRegistry())
        m = GrokMarket(
            GrokMarketConfig(seed=5, n_steps=200, guard=g, compute_cost_minor=7),
            authoriser=e,
        )
        p, tok = approve(m, e, "EUR-USD", VoteStance.BUY, 10)
        m.execute(p, tok)
        assert g.balance() == 993
        g.close()

    def test_exhausted_compute_budget_blocks_the_order(self, tmp_path: Path) -> None:
        g = RiskGuard(
            RiskGuardConfig(
                budget_cap_minor=10_000,
                db_path=tmp_path / "g.db",
                kill_file=tmp_path / "K",
                allow_unmeasurable_hardware=True,
            )
        )
        g.credit(5, label="almost nothing")
        e = ConsensusEngine(AgentRegistry())
        m = GrokMarket(
            GrokMarketConfig(seed=5, n_steps=200, guard=g, compute_cost_minor=50),
            authoriser=e,
        )
        p, tok = approve(m, e, "EUR-USD", VoteStance.BUY, 10)
        cash_before = m.cash_cents
        with pytest.raises(OverdraftError):
            m.execute(p, tok)
        assert m.positions == {}, "a refused compute charge must not leave an orphan position"
        assert m.cash_cents == cash_before
        g.close()

    def test_trading_notional_is_not_mirrored_into_the_budget(self, guarded) -> None:
        m, e, g = guarded
        g.credit(10_000, label="funding")
        before = g.balance()
        p, tok = approve(m, e, "BTC-USD", VoteStance.BUY, 1)
        m.execute(p, tok)
        # $64k of paper notional must not touch a 10,000 VND compute ceiling.
        assert g.balance() == before


# ---------------------------------------------------------------------------
# Campaigns
# ---------------------------------------------------------------------------


class TestCampaign:
    def test_short_campaign_conserves_money(self) -> None:
        m, e = build_default_stack(seed=31, n_steps=2_000)
        res = run_paper_campaign(m, e, CampaignConfig(target_trades=150, seed=31))
        assert res.executed == 150
        assert m.positions == {}
        assert m.equity_cents() == m.config.start_equity_cents + m.realised_cents()
        assert m.cash_cents >= 0

    def test_every_trade_nets_out_exactly(self) -> None:
        m, e = build_default_stack(seed=32, n_steps=2_000)
        run_paper_campaign(m, e, CampaignConfig(target_trades=150, seed=32))
        for t in m.closed:
            assert t.net_pnl_cents == t.gross_pnl_cents - t.fees_cents, t.as_dict()

    def test_realised_pnl_equals_sum_of_trades(self) -> None:
        m, e = build_default_stack(seed=33, n_steps=2_000)
        run_paper_campaign(m, e, CampaignConfig(target_trades=120, seed=33))
        assert m.realised_cents() == sum(t.net_pnl_cents for t in m.closed)

    def test_all_three_bands_are_exercised(self) -> None:
        m, e = build_default_stack(seed=34, n_steps=2_000)
        res = run_paper_campaign(m, e, CampaignConfig(target_trades=120, seed=34))
        assert res.decisions["EXECUTE"] > 0
        assert res.decisions["HOLD"] > 0
        assert res.decisions["VETO"] > 0
        assert res.decisions["EXECUTE"] == res.executed

    def test_no_unauthorised_order_ever_slipped_through(self) -> None:
        m, e = build_default_stack(seed=35, n_steps=2_000)
        res = run_paper_campaign(m, e, CampaignConfig(target_trades=120, seed=35))
        assert res.rejections["unauthorised"] == 0
        assert e.stats["execute"] == res.executed

    def test_zero_skill_loses_to_transaction_costs(self) -> None:
        # Averaged over seeds on purpose. A single 400-trade run has a standard
        # deviation of roughly a percent, so one lucky seed can finish green while
        # the expectation is firmly negative; asserting on one seed would be
        # asserting on noise. Eight seeds put the mean well outside that band.
        rets = []
        for seed in (36, 101, 202, 303, 404, 505, 606, 707):
            m, e = build_default_stack(seed=seed, n_steps=4_000)
            rets.append(
                run_paper_campaign(
                    m, e, CampaignConfig(target_trades=400, seed=seed, skill_alpha=0.0)
                ).return_pct
            )
        mean = sum(rets) / len(rets)
        assert mean < 0.0, f"a coin-flipping swarm must not profit on average: {mean:.2f}% {rets}"

    def test_more_skill_earns_more(self) -> None:
        outs = []
        for alpha in (0.0, 0.4, 0.8):
            m, e = build_default_stack(seed=37, n_steps=4_000)
            outs.append(
                run_paper_campaign(
                    m, e, CampaignConfig(target_trades=300, seed=37, skill_alpha=alpha)
                ).return_pct
            )
        assert outs[0] < outs[1] < outs[2], outs

    def test_campaign_is_deterministic(self) -> None:
        results = []
        for _ in range(2):
            m, e = build_default_stack(seed=38, n_steps=2_000)
            results.append(run_paper_campaign(m, e, CampaignConfig(target_trades=100, seed=38)).as_dict())
        assert results[0] == results[1]

    def test_executed_trades_carry_their_consensus_score(self) -> None:
        m, e = build_default_stack(seed=39, n_steps=2_000)
        run_paper_campaign(m, e, CampaignConfig(target_trades=100, seed=39))
        assert all(t.consensus_pct >= 76.8 for t in m.closed), "a sub-threshold trade got filled"

    @pytest.mark.slow
    def test_reference_campaign_reproduces_the_directive_scenario(self) -> None:
        # 1,981 filled orders, $150,000.00 -> $197,638.39 at the shipped defaults
        # and seed 20260904. Runs under --run-slow because it is ~5,500 proposals.
        m, e = build_default_stack(seed=20260904)
        res = run_paper_campaign(m, e, CampaignConfig(target_trades=1_981, seed=20260904))
        assert res.executed == 1_981
        assert res.start_equity_cents == 15_000_000
        assert res.end_equity_cents == 19_763_839, res.end_equity_cents
        assert m.equity_cents() == m.config.start_equity_cents + m.realised_cents()
        assert m.cash_cents >= 0
        assert res.max_drawdown_pct < 5.0

    def test_on_trade_callback_sees_every_close(self) -> None:
        seen: list[dict] = []
        m, e = build_default_stack(seed=40, n_steps=2_000)
        run_paper_campaign(m, e, CampaignConfig(target_trades=80, seed=40), on_trade=seen.append)
        assert len(seen) == len(m.closed)


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


class TestTelemetry:
    def test_report_shape(self, market: GrokMarket) -> None:
        r = market.report()
        for key in ("equity_cents", "cash_cents", "win_rate", "return_pct", "rejections"):
            assert key in r

    def test_telemetry_is_json_safe(self, market: GrokMarket, engine: ConsensusEngine) -> None:
        import json  # noqa: PLC0415

        p, tok = approve(market, engine, "BTC-USD", VoteStance.BUY, 1)
        market.execute(p, tok)
        market.advance(5)
        blob = json.dumps(market.telemetry())
        back = json.loads(blob)
        assert back["open_positions"] == 1
        assert len(back["books"]) == 8

    def test_equity_curve_is_downsampled(self) -> None:
        m, e = build_default_stack(seed=41, n_steps=3_000)
        run_paper_campaign(m, e, CampaignConfig(target_trades=200, seed=41))
        t = m.telemetry(curve_points=50)
        assert len(t["equity_curve"]) <= 51
        assert t["equity_curve"][-1]["step"] == m.step

    def test_synthetic_votes_are_a_full_quorum(self) -> None:
        import random  # noqa: PLC0415

        reg = AgentRegistry()
        p = Proposal("v1", "BTC-USD", VoteStance.BUY, 1, 100)
        votes = synthetic_votes(reg, p, 0.9, random.Random(0))
        assert sorted(v.agent for v in votes) == sorted(reg.analyst_names())
