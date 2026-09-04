#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_market_feed.py - read-only public data, and the wall between it and the ledger.

    pytest tests/test_market_feed.py -v

Nothing in this file touches the network. Every test injects a transport, which
is the only honest way to test a client against a venue that is someone else's
production system: hitting a real exchange from a test suite is rude, flaky, and
tells you about their uptime rather than about your code.

What is actually being protected
--------------------------------
1. **Prices are integers before they touch anything.** A ledger that holds
   integer cents cannot be fed a float from an HTTP response, and the conversion
   happens once, at the boundary, with half-up rounding.
2. **Untrusted input is validated as untrusted.** Zero, negative, NaN, a string,
   a missing field, and a 10x print are all refused - the last one because a
   decimal error at a venue must not become a position here.
3. **Stale is not usable.** Past `max_age_s` the price reads as None and the
   market refuses to open on it. Settlement keeps working, deliberately.
4. **Live mode forbids the oracle.** The reference campaign chooses its trade
   direction by looking at the realised future move. On live data there is no
   realised future, so the campaign refuses to run rather than producing a
   number that looks like a backtest result.
"""

from __future__ import annotations

import json
import time
from typing import Any

import pytest

from consensus_engine import ConsensusEngine, Proposal, VoteStance, build_quorum
from groktopus_market import (
    CampaignConfig,
    LiveModeError,
    MarketHalted,
    build_default_stack,
    run_paper_campaign,
)
from market_feed import (
    VENUES,
    FeedError,
    MarketFeed,
    RateLimited,
    RateLimiter,
    SuspiciousTick,
    Tick,
    VenueUnavailable,
    _fake_transport,
)


def feed(price: Any = "64125.00", **kw: Any) -> MarketFeed:
    kw.setdefault("transport", _fake_transport({"BTC-USD": price, "ETH-USD": "3124.00"}))
    return MarketFeed("coinbase", **kw)


class TestPriceHandling:
    def test_price_is_integer_cents(self) -> None:
        t = feed().fetch("BTC-USD")
        assert isinstance(t.price_cents, int) and t.price_cents == 6_412_500

    @pytest.mark.parametrize(
        "raw,cents", [("1.005", 101), ("1.004", 100), ("0.01", 1), ("64125", 6_412_500)]
    )
    def test_half_up_rounding(self, raw: str, cents: int) -> None:
        assert feed(raw).fetch("BTC-USD").price_cents == cents

    @pytest.mark.parametrize("bad", ["0", "-1", "abc", "nan", "inf", "", None, "1e40", "1e-9"])
    def test_bad_prices_refused(self, bad: Any) -> None:
        with pytest.raises(SuspiciousTick):
            feed(bad).fetch("BTC-USD")

    def test_tick_refuses_a_float(self) -> None:
        with pytest.raises(SuspiciousTick):
            Tick("BTC-USD", 100.5, "coinbase", time.time())  # type: ignore[arg-type]

    def test_tick_refuses_non_positive(self) -> None:
        with pytest.raises(SuspiciousTick):
            Tick("BTC-USD", 0, "coinbase", time.time())


class TestSanityGuard:
    def test_absurd_jump_refused_and_not_stored(self) -> None:
        f = feed()
        f.fetch("BTC-USD")
        f._transport = _fake_transport({"BTC-USD": "641250.00"})
        with pytest.raises(SuspiciousTick, match="more often a venue error"):
            f.fetch("BTC-USD")
        assert f.last("BTC-USD").price_cents == 6_412_500
        assert f.stats["suspicious"] == 1

    def test_first_tick_has_nothing_to_compare_against(self) -> None:
        assert feed("999999.00").fetch("BTC-USD").price_cents == 99_999_900

    def test_move_inside_the_guard_is_accepted(self) -> None:
        f = feed()
        f.fetch("BTC-USD")
        f._transport = _fake_transport({"BTC-USD": "70000.00"})  # +9.2%
        assert f.fetch("BTC-USD").price_cents == 7_000_000

    def test_guard_is_configurable(self) -> None:
        f = feed(max_jump_pct=50.0)
        f.fetch("BTC-USD")
        f._transport = _fake_transport({"BTC-USD": "80000.00"})  # +24.8%
        assert f.fetch("BTC-USD").price_cents == 8_000_000


class TestFreshness:
    def test_fresh_price_reads_back(self) -> None:
        f = feed(max_age_s=30)
        f.fetch("BTC-USD")
        assert f.price_cents("BTC-USD") == 6_412_500
        assert f.fresh("BTC-USD")

    def test_stale_price_is_none(self) -> None:
        f = feed(max_age_s=10)
        f.fetch("BTC-USD")
        later = time.time() + 60
        assert f.price_cents("BTC-USD", now=later) is None
        assert not f.fresh("BTC-USD", now=later)

    def test_never_fetched_is_none(self) -> None:
        assert feed().price_cents("BTC-USD") is None


class TestPoliteness:
    def test_limiter_refills_over_time(self) -> None:
        rl = RateLimiter(per_minute=60.0, burst=1)
        assert rl.try_acquire()
        assert not rl.try_acquire()
        time.sleep(1.1)
        assert rl.try_acquire()

    def test_limiter_refuses_rather_than_blocking(self) -> None:
        rl = RateLimiter(per_minute=1.0, burst=1)
        rl.acquire()
        t0 = time.perf_counter()
        with pytest.raises(RateLimited):
            rl.acquire()
        assert time.perf_counter() - t0 < 0.5, "the limiter slept instead of refusing"

    @pytest.mark.parametrize("bad", [0, -1])
    def test_limiter_rejects_a_nonsense_rate(self, bad: float) -> None:
        with pytest.raises(ValueError):
            RateLimiter(per_minute=bad)

    def test_burst_is_bounded(self) -> None:
        f = feed(per_minute=1.0)
        ok = 0
        for _ in range(30):
            try:
                f.fetch("BTC-USD")
                ok += 1
            except RateLimited:
                pass
        assert ok < 30
        assert f.stats["rate_limited"] > 0

    def test_venue_429_puts_the_feed_in_backoff(self) -> None:
        f = MarketFeed("coinbase", transport=_fake_transport({"BTC-USD": RateLimited("429")}))
        with pytest.raises(RateLimited):
            f.fetch("BTC-USD")
        assert f.cold_until > time.time()
        with pytest.raises(RateLimited, match="backoff"):
            f.fetch("BTC-USD")

    def test_user_agent_identifies_the_tool(self) -> None:
        import market_feed  # noqa: PLC0415

        assert "GrokTOPUS" in market_feed.USER_AGENT
        assert "read-only" in market_feed.USER_AGENT


class TestCoverage:
    def test_only_crypto_pairs_are_supported(self) -> None:
        assert feed().supported_symbols() == ["BTC-USD", "ETH-USD", "SOL-USD"]

    def test_the_other_five_are_named_not_hidden(self) -> None:
        missing = MarketFeed.unsupported_symbols(
            ["BTC-USD", "ETH-USD", "SOL-USD", "XAU-USD", "EUR-USD",
             "SPX-IDX", "NDX-IDX", "OIL-WTI"]
        )
        assert missing == ["EUR-USD", "NDX-IDX", "OIL-WTI", "SPX-IDX", "XAU-USD"]

    def test_unsupported_symbol_raises_rather_than_guessing(self) -> None:
        with pytest.raises(FeedError, match="no public ticker"):
            feed().fetch("XAU-USD")

    def test_poll_omits_failures(self) -> None:
        out = feed().poll(["BTC-USD", "ETH-USD", "XAU-USD"])
        assert set(out) == {"BTC-USD", "ETH-USD"}

    def test_unknown_venue_refused(self) -> None:
        with pytest.raises(FeedError):
            MarketFeed("not-a-venue")

    @pytest.mark.parametrize("venue", sorted(VENUES))
    def test_every_venue_is_https_and_documented(self, venue: str) -> None:
        spec = VENUES[venue]
        assert spec["base"].startswith("https://")
        assert spec["docs"].startswith("https://")
        assert spec["products"]

    @pytest.mark.parametrize("venue", sorted(VENUES))
    def test_no_venue_url_carries_a_credential(self, venue: str) -> None:
        f = MarketFeed(venue, transport=_fake_transport({}))
        for sym in f.supported_symbols():
            url = f.url_for(sym).lower()
            for token in ("key", "secret", "signature", "token", "passphrase", "apikey"):
                assert token not in url, url


class TestFailClosed:
    def test_unreachable_venue_raises(self) -> None:
        f = MarketFeed("coinbase", transport=_fake_transport({"BTC-USD": VenueUnavailable("down")}))
        with pytest.raises(VenueUnavailable):
            f.fetch("BTC-USD")
        assert f.stats["unavailable"] == 1

    def test_non_object_response_refused(self) -> None:
        f = MarketFeed("coinbase", transport=lambda url, t: ["not", "an", "object"])
        with pytest.raises(VenueUnavailable):
            f.fetch("BTC-USD")

    def test_arbitrary_transport_fault_becomes_unavailability(self) -> None:
        def boom(url: str, t: float) -> dict:
            raise KeyError("something odd")

        with pytest.raises(VenueUnavailable):
            MarketFeed("coinbase", transport=boom).fetch("BTC-USD")

    def test_status_is_json_safe_and_declares_read_only(self) -> None:
        f = feed()
        f.fetch("BTC-USD")
        s = json.loads(json.dumps(f.status()))
        assert s["read_only"] is True and s["authenticated"] is False


# ---------------------------------------------------------------------------
# The wall between live data and the paper ledger
# ---------------------------------------------------------------------------


class TestLiveMarketIntegration:
    def stack(self):
        market, engine = build_default_stack(seed=515, n_steps=600)
        f = feed()
        f.fetch("BTC-USD")
        return market, engine, f

    def test_attach_takes_only_supported_symbols(self) -> None:
        market, _, f = self.stack()
        taken = market.attach_live_feed(f)
        assert taken == ["BTC-USD", "ETH-USD", "SOL-USD"]
        assert market.live_symbols == set(taken)

    def test_attach_with_nothing_supported_refuses(self) -> None:
        market, _, f = self.stack()
        with pytest.raises(LiveModeError, match="has a public ticker"):
            market.attach_live_feed(f, ["XAU-USD", "EUR-USD"])

    def test_mid_comes_from_the_feed(self) -> None:
        market, _, f = self.stack()
        market.attach_live_feed(f, ["BTC-USD"])
        assert market.mid("BTC-USD") == 6_412_500

    def test_synthetic_symbols_are_untouched(self) -> None:
        market, _, f = self.stack()
        before = market.mid("XAU-USD")
        market.attach_live_feed(f, ["BTC-USD"])
        assert market.mid("XAU-USD") == before

    def test_no_future_price_for_a_live_symbol(self) -> None:
        market, _, f = self.stack()
        market.attach_live_feed(f, ["BTC-USD"])
        with pytest.raises(LiveModeError, match="has not happened yet"):
            market.price_at("BTC-USD", market.step + 4)

    def test_past_and_present_still_readable(self) -> None:
        market, _, f = self.stack()
        market.advance(10)
        market.attach_live_feed(f, ["BTC-USD"])
        assert market.price_at("BTC-USD", market.step) > 0
        assert market.price_at("BTC-USD", market.step - 5) > 0

    def test_campaign_refuses_to_run_on_live_data(self) -> None:
        market, engine, f = self.stack()
        market.attach_live_feed(f, ["BTC-USD"])
        with pytest.raises(LiveModeError, match="future does not exist"):
            run_paper_campaign(market, engine, CampaignConfig(target_trades=5))

    def test_campaign_still_runs_without_a_feed(self) -> None:
        market, engine = build_default_stack(seed=516, n_steps=1_500)
        res = run_paper_campaign(market, engine, CampaignConfig(target_trades=40, seed=516))
        assert res.executed == 40

    def test_stale_live_data_blocks_opening(self) -> None:
        market, engine, f = self.stack()
        f.max_age_s = 0.0  # everything is instantly stale
        market.attach_live_feed(f, ["BTC-USD"])
        p = Proposal("live-1", "BTC-USD", VoteStance.BUY, 1, market.mid("BTC-USD"))
        r = engine.evaluate(p, build_quorum(engine.registry, VoteStance.BUY, 0.95))
        with pytest.raises(MarketHalted, match="stopped being true"):
            market.execute(p, r.token.token)
        assert market.positions == {}
        assert market.live_stale_blocks == 1

    def test_stale_live_data_does_not_block_settlement(self) -> None:
        # The asymmetry that matters: a feed hiccup must never trap a position.
        market, engine, f = self.stack()
        market.attach_live_feed(f, ["BTC-USD"])
        p = Proposal("live-2", "BTC-USD", VoteStance.BUY, 1, market.mid("BTC-USD"))
        r = engine.evaluate(p, build_quorum(engine.registry, VoteStance.BUY, 0.95))
        market.execute(p, r.token.token)
        assert len(market.positions) == 1
        f.max_age_s = 0.0
        closed = market.flatten_all()
        assert len(closed) == 1
        assert market.positions == {}

    def test_live_state_is_reported(self) -> None:
        market, _, f = self.stack()
        market.attach_live_feed(f, ["BTC-USD", "ETH-USD"])
        rep = market.report()
        assert rep["live_symbols"] == ["BTC-USD", "ETH-USD"]
        assert rep["live_stale_blocks"] == 0

    def test_ledger_stays_integer_under_live_prices(self) -> None:
        market, engine, f = self.stack()
        market.attach_live_feed(f, ["BTC-USD"])
        p = Proposal("live-3", "BTC-USD", VoteStance.BUY, 1, market.mid("BTC-USD"))
        r = engine.evaluate(p, build_quorum(engine.registry, VoteStance.BUY, 0.95))
        market.execute(p, r.token.token)
        market.flatten_all()
        assert isinstance(market.cash_cents, int)
        assert market.equity_cents() == market.config.start_equity_cents + market.realised_cents()
