#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_consensus_engine.py - the 76.8% gate.

    pytest tests/test_consensus_engine.py -v

What these tests are actually protecting
----------------------------------------
1. The three bands are exactly where the directive puts them, boundaries
   included. 76.8 executes; 76.79 does not. A gate whose edge is "about right"
   is not a gate.
2. Dissent subtracts. Six agents at high confidence pointing in opposite
   directions must not score like six agents who agree.
3. RUNE's veto is absolute - it beats a 99% score, or it is not a veto.
4. Every illegal quorum cancels the proposal rather than producing a number.
5. No agent can move money without a token, and a token authorises exactly one
   order, exactly once, for a bounded time.

The weights in config/groktopus_agents.json are deliberately read through
`normalised_weights()` rather than hard-coded here, except in the two arithmetic
tests that need a concrete number to be meaningful - those name the weight they
depend on in a comment so a config change fails loudly instead of silently
weakening the assertion.
"""

from __future__ import annotations

import json
import math
import time
from pathlib import Path

import pytest

from consensus_engine import (
    EXECUTE_THRESHOLD_PCT,
    HOLD_THRESHOLD_PCT,
    AgentRegistry,
    AgentTier,
    AuthToken,
    ConsensusDecision,
    ConsensusEngine,
    ConsensusError,
    Proposal,
    QuorumError,
    TokenError,
    Vote,
    VoteStance,
    build_quorum,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def registry() -> AgentRegistry:
    return AgentRegistry()


@pytest.fixture
def engine(registry: AgentRegistry) -> ConsensusEngine:
    return ConsensusEngine(registry)


@pytest.fixture
def buy_proposal() -> Proposal:
    return Proposal("p-buy", "BTC-USD", VoteStance.BUY, qty=3, price_cents=6_412_500)


@pytest.fixture
def sell_proposal() -> Proposal:
    return Proposal("p-sell", "ETH-USD", VoteStance.SELL, qty=12, price_cents=312_400)


# ---------------------------------------------------------------------------
# Roster
# ---------------------------------------------------------------------------


class TestAgentRegistry:
    def test_eight_agents_exactly(self, registry: AgentRegistry) -> None:
        assert len(registry.agents) == 8

    def test_six_analysts_in_directive_order(self, registry: AgentRegistry) -> None:
        assert registry.analyst_names() == [
            "TIDAL",
            "NORO",
            "LUMEN",
            "ZEPHR",
            "OKAPI",
            "RUNE",
        ]

    def test_veska_and_marin_do_not_vote(self, registry: AgentRegistry) -> None:
        for name in ("VESKA", "MARIN"):
            spec = registry.agents[name]
            assert spec.votes is False
            assert spec.weight == 0.0

    def test_exactly_one_veto_one_executor_one_settler(self, registry: AgentRegistry) -> None:
        assert [n for n, s in registry.agents.items() if s.veto_power] == ["RUNE"]
        assert [n for n, s in registry.agents.items() if s.sole_executor] == ["VESKA"]
        assert [n for n, s in registry.agents.items() if s.sole_settler] == ["MARIN"]

    def test_weights_normalise_to_one(self, registry: AgentRegistry) -> None:
        assert math.isclose(sum(registry.normalised_weights().values()), 1.0, abs_tol=1e-12)

    def test_every_agent_has_a_prompt_and_kpis(self, registry: AgentRegistry) -> None:
        for name, spec in registry.agents.items():
            assert len(spec.system_prompt) > 80, f"{name} prompt is a stub"
            assert spec.kpis, f"{name} declares no KPIs"

    def test_every_agent_has_a_distinct_tentacle_hue(self, registry: AgentRegistry) -> None:
        hues = [s.tentacle_hue for s in registry.agents.values()]
        assert len(set(hues)) == 8, "two tentacles would render the same colour"

    def test_eight_market_symbols(self, registry: AgentRegistry) -> None:
        assert len(registry.symbols) == 8
        assert len(set(registry.symbols)) == 8

    def test_missing_agent_refuses_to_load(self, tmp_path: Path, registry: AgentRegistry) -> None:
        raw = json.loads(Path(registry.config_path).read_text(encoding="utf-8"))
        del raw["agents"]["LUMEN"]
        p = tmp_path / "broken.json"
        p.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ConsensusError, match="LUMEN"):
            AgentRegistry(p)

    def test_second_executor_refuses_to_load(self, tmp_path: Path, registry: AgentRegistry) -> None:
        raw = json.loads(Path(registry.config_path).read_text(encoding="utf-8"))
        raw["agents"]["MARIN"]["sole_executor"] = True
        p = tmp_path / "two_executors.json"
        p.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ConsensusError, match="execute"):
            AgentRegistry(p)

    def test_analyst_with_zero_weight_refuses_to_load(
        self, tmp_path: Path, registry: AgentRegistry
    ) -> None:
        raw = json.loads(Path(registry.config_path).read_text(encoding="utf-8"))
        raw["agents"]["TIDAL"]["weight"] = 0.0
        p = tmp_path / "zero_weight.json"
        p.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ConsensusError, match="weight 0"):
            AgentRegistry(p)

    def test_voting_non_analyst_refuses_to_load(
        self, tmp_path: Path, registry: AgentRegistry
    ) -> None:
        raw = json.loads(Path(registry.config_path).read_text(encoding="utf-8"))
        raw["agents"]["VESKA"]["weight"] = 0.3
        p = tmp_path / "voting_executor.json"
        p.write_text(json.dumps(raw), encoding="utf-8")
        with pytest.raises(ConsensusError, match="only analysts vote"):
            AgentRegistry(p)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            AgentRegistry(tmp_path / "nope.json")


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------


class TestThresholds:
    def test_directive_constants(self) -> None:
        assert EXECUTE_THRESHOLD_PCT == 76.8
        assert HOLD_THRESHOLD_PCT == 70.0

    def test_config_matches_the_directive(self, registry: AgentRegistry) -> None:
        cfg = registry.consensus_cfg
        assert cfg["execute_threshold_pct"] == 76.8
        assert cfg["hold_threshold_pct"] == 70.0

    @pytest.mark.parametrize(
        "confidence, expected",
        [
            (1.00, ConsensusDecision.EXECUTE),
            (0.90, ConsensusDecision.EXECUTE),
            (0.769, ConsensusDecision.EXECUTE),
            (0.768, ConsensusDecision.EXECUTE),   # exactly on the line: authorised
            (0.7679, ConsensusDecision.HOLD),     # one ten-thousandth under: not
            (0.75, ConsensusDecision.HOLD),
            (0.70, ConsensusDecision.HOLD),       # exactly on the hold line
            (0.6999, ConsensusDecision.VETO),
            (0.50, ConsensusDecision.VETO),
            (0.00, ConsensusDecision.VETO),
        ],
    )
    def test_bands(
        self,
        engine: ConsensusEngine,
        registry: AgentRegistry,
        buy_proposal: Proposal,
        confidence: float,
        expected: ConsensusDecision,
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, confidence))
        assert r.decision is expected, f"{confidence} -> {r.score_pct}% {r.decision}"

    def test_unanimous_score_is_the_confidence(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        # With normalised weights, sum(w_i * c_i) at uniform c is exactly c.
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.834))
        assert math.isclose(r.score_pct, 83.4, abs_tol=1e-9)

    def test_execute_mints_a_token_and_hold_does_not(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        ok = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        hold = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.72))
        assert isinstance(ok.token, AuthToken)
        assert hold.token is None
        assert ok.authorised is True and bool(ok) is True
        assert hold.authorised is False and bool(hold) is False

    def test_thresholds_must_be_ordered(self, registry: AgentRegistry) -> None:
        with pytest.raises(ConsensusError):
            ConsensusEngine(registry, execute_threshold_pct=50.0, hold_threshold_pct=70.0)


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


class TestScoring:
    def test_dissent_subtracts(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        # NORO carries weight 0.20 of 1.00: 0.80*1.0 - 0.20*1.0 = 0.60.
        w = registry.normalised_weights()["NORO"]
        votes = build_quorum(registry, VoteStance.BUY, 1.0, overrides={"NORO": (VoteStance.SELL, 1.0)})
        r = engine.evaluate(buy_proposal, votes)
        assert math.isclose(r.score_pct, (1.0 - 2 * w) * 100.0, abs_tol=1e-9)
        assert r.decision is ConsensusDecision.VETO

    def test_hold_withholds_weight_without_opposing(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        w = registry.normalised_weights()["LUMEN"]
        votes = build_quorum(registry, VoteStance.BUY, 1.0, overrides={"LUMEN": (VoteStance.HOLD, 1.0)})
        r = engine.evaluate(buy_proposal, votes)
        assert math.isclose(r.score_pct, (1.0 - w) * 100.0, abs_tol=1e-9)

    def test_split_high_confidence_never_looks_unanimous(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        # Three long, three short, everyone at 0.95. A naive mean of confidence
        # would score 95% and authorise a trade nobody agreed on.
        votes = build_quorum(
            registry,
            VoteStance.BUY,
            0.95,
            overrides={
                "ZEPHR": (VoteStance.SELL, 0.95),
                "OKAPI": (VoteStance.SELL, 0.95),
                "RUNE": (VoteStance.SELL, 0.95),
            },
        )
        r = engine.evaluate(buy_proposal, votes)
        assert r.score_pct < HOLD_THRESHOLD_PCT
        assert r.decision is ConsensusDecision.VETO

    def test_opposite_direction_scores_zero_not_negative(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.SELL, 1.0))
        assert r.score_pct == 0.0
        assert r.support_pct["SELL"] == pytest.approx(100.0)
        assert r.decision is ConsensusDecision.VETO

    def test_all_hold_gives_no_mandate(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.HOLD, 1.0))
        assert r.score_pct == 0.0
        assert "no directional mandate" in r.reason

    def test_sell_proposal_is_scored_on_sell_support(
        self, engine: ConsensusEngine, registry: AgentRegistry, sell_proposal: Proposal
    ) -> None:
        r = engine.evaluate(sell_proposal, build_quorum(registry, VoteStance.SELL, 0.88))
        assert r.decision is ConsensusDecision.EXECUTE
        assert math.isclose(r.score_pct, 88.0, abs_tol=1e-9)

    def test_contributions_sum_to_buy_support(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        votes = build_quorum(
            registry, VoteStance.BUY, 0.8, overrides={"TIDAL": (VoteStance.SELL, 0.4)}
        )
        r = engine.evaluate(buy_proposal, votes)
        assert math.isclose(sum(r.contributions.values()), r.support_pct["BUY"], abs_tol=1e-9)

    def test_support_directions_are_mirror_images(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.6))
        assert math.isclose(r.support_pct["BUY"], -r.support_pct["SELL"], abs_tol=1e-12)

    def test_higher_weight_agent_moves_the_score_more(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        w = registry.normalised_weights()
        heavy = max(w, key=lambda k: w[k])
        light = min(w, key=lambda k: w[k])
        assert w[heavy] > w[light]
        r_heavy = engine.evaluate(
            buy_proposal,
            build_quorum(registry, VoteStance.BUY, 1.0, overrides={heavy: (VoteStance.HOLD, 1.0)}),
        )
        r_light = engine.evaluate(
            buy_proposal,
            build_quorum(registry, VoteStance.BUY, 1.0, overrides={light: (VoteStance.HOLD, 1.0)}),
        )
        assert r_heavy.score_pct < r_light.score_pct


# ---------------------------------------------------------------------------
# The veto
# ---------------------------------------------------------------------------


class TestRuneVeto:
    def test_veto_beats_a_near_perfect_score(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.99, veto=True))
        assert r.decision is ConsensusDecision.VETO
        assert r.score_pct == pytest.approx(99.0)
        assert r.token is None
        assert r.veto_source == "RUNE"

    def test_veto_source_recorded_for_threshold_cancellations(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.4))
        assert r.veto_source == "RUNE_THRESHOLD"

    def test_only_rune_may_raise_the_flag(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        votes = build_quorum(registry, VoteStance.BUY, 0.99)
        votes[0] = Vote(votes[0].agent, VoteStance.BUY, 0.99, veto=True)
        r = engine.evaluate(buy_proposal, votes)
        assert r.decision is ConsensusDecision.VETO
        assert "holds no veto power" in r.reason

    def test_rune_voting_hold_is_not_a_veto(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        votes = build_quorum(registry, VoteStance.BUY, 1.0, overrides={"RUNE": (VoteStance.HOLD, 1.0)})
        r = engine.evaluate(buy_proposal, votes)
        assert r.veto_source is None
        assert r.decision is ConsensusDecision.EXECUTE  # 80% still clears 76.8%


# ---------------------------------------------------------------------------
# Quorum discipline
# ---------------------------------------------------------------------------


class TestQuorum:
    @pytest.mark.parametrize("drop", ["TIDAL", "NORO", "LUMEN", "ZEPHR", "OKAPI", "RUNE"])
    def test_any_missing_analyst_cancels(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal, drop: str
    ) -> None:
        votes = [v for v in build_quorum(registry, VoteStance.BUY, 0.99) if v.agent != drop]
        r = engine.evaluate(buy_proposal, votes)
        assert r.decision is ConsensusDecision.VETO
        assert r.veto_source == "QUORUM"
        assert drop in r.reason

    def test_duplicate_vote_cancels(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        votes = build_quorum(registry, VoteStance.BUY, 0.99)
        votes.append(Vote("RUNE", VoteStance.BUY, 0.99))
        r = engine.evaluate(buy_proposal, votes)
        assert r.decision is ConsensusDecision.VETO
        assert "more than once" in r.reason

    def test_unknown_agent_cancels(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        votes = build_quorum(registry, VoteStance.BUY, 0.99)
        votes.append(Vote("GHOST", VoteStance.BUY, 1.0))
        r = engine.evaluate(buy_proposal, votes)
        assert r.decision is ConsensusDecision.VETO
        assert "unknown agent" in r.reason

    @pytest.mark.parametrize("agent", ["VESKA", "MARIN"])
    def test_executor_and_settler_may_not_vote(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal, agent: str
    ) -> None:
        votes = build_quorum(registry, VoteStance.BUY, 0.99)
        votes.append(Vote(agent, VoteStance.BUY, 1.0))
        r = engine.evaluate(buy_proposal, votes)
        assert r.decision is ConsensusDecision.VETO
        assert "not permitted to vote" in r.reason

    def test_empty_quorum_cancels(
        self, engine: ConsensusEngine, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, [])
        assert r.decision is ConsensusDecision.VETO

    def test_non_vote_object_is_a_programming_error(
        self, engine: ConsensusEngine, buy_proposal: Proposal
    ) -> None:
        with pytest.raises(TypeError):
            engine.evaluate(buy_proposal, [{"agent": "RUNE"}])

    def test_non_proposal_is_a_programming_error(
        self, engine: ConsensusEngine, registry: AgentRegistry
    ) -> None:
        with pytest.raises(TypeError):
            engine.evaluate("BTC-USD", build_quorum(registry, VoteStance.BUY, 0.9))


# ---------------------------------------------------------------------------
# Vote and proposal validation
# ---------------------------------------------------------------------------


class TestValidation:
    @pytest.mark.parametrize("bad", [1.5, -0.1, float("nan"), float("inf"), True, "0.9", None])
    def test_bad_confidence_rejected(self, bad: object) -> None:
        with pytest.raises(QuorumError):
            Vote("RUNE", VoteStance.BUY, bad)

    @pytest.mark.parametrize("good", [0.0, 0.5, 1.0, 1, 0])
    def test_boundary_confidence_accepted(self, good: object) -> None:
        assert Vote("RUNE", VoteStance.BUY, good).confidence == float(good)

    def test_stance_must_be_a_vote_stance(self) -> None:
        with pytest.raises(QuorumError):
            Vote("RUNE", "BUY", 0.9)

    def test_empty_agent_rejected(self) -> None:
        with pytest.raises(QuorumError):
            Vote("  ", VoteStance.BUY, 0.9)

    @pytest.mark.parametrize("qty", [0, -1, 2.5, True])
    def test_proposal_quantity_must_be_a_positive_int(self, qty: object) -> None:
        with pytest.raises(ConsensusError):
            Proposal("p", "BTC-USD", VoteStance.BUY, qty=qty, price_cents=100)

    def test_proposal_side_must_be_directional(self) -> None:
        with pytest.raises(ConsensusError):
            Proposal("p", "BTC-USD", VoteStance.HOLD, qty=1, price_cents=100)

    def test_notional_is_integer_cents(self) -> None:
        p = Proposal("p", "BTC-USD", VoteStance.BUY, qty=3, price_cents=6_412_500)
        assert p.notional_cents == 19_237_500
        assert isinstance(p.notional_cents, int)

    def test_digest_is_stable_and_field_sensitive(self) -> None:
        a = Proposal("p", "BTC-USD", VoteStance.BUY, qty=3, price_cents=100)
        b = Proposal("p", "BTC-USD", VoteStance.BUY, qty=3, price_cents=100)
        c = Proposal("p", "BTC-USD", VoteStance.BUY, qty=4, price_cents=100)
        d = Proposal("p", "BTC-USD", VoteStance.SELL, qty=3, price_cents=100)
        assert a.digest() == b.digest()
        assert len({a.digest(), c.digest(), d.digest()}) == 3


# ---------------------------------------------------------------------------
# Authorisation tokens
# ---------------------------------------------------------------------------


class TestAuthToken:
    def test_token_authorises_exactly_once(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        assert engine.consume(r.token.token, buy_proposal).proposal_id == "p-buy"
        with pytest.raises(TokenError, match="single-use"):
            engine.consume(r.token.token, buy_proposal)

    def test_verify_does_not_spend(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        engine.verify(r.token.token, buy_proposal)
        engine.verify(r.token.token, buy_proposal)
        engine.consume(r.token.token, buy_proposal)

    def test_token_will_not_authorise_a_bigger_order(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        inflated = Proposal("p-buy", "BTC-USD", VoteStance.BUY, qty=300, price_cents=6_412_500)
        with pytest.raises(TokenError):
            engine.consume(r.token.token, inflated)

    def test_token_will_not_authorise_a_different_symbol(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        swapped = Proposal("p-buy", "SOL-USD", VoteStance.BUY, qty=3, price_cents=6_412_500)
        with pytest.raises(TokenError):
            engine.consume(r.token.token, swapped)

    def test_token_will_not_authorise_the_other_side(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        flipped = Proposal("p-buy", "BTC-USD", VoteStance.SELL, qty=3, price_cents=6_412_500)
        with pytest.raises(TokenError):
            engine.consume(r.token.token, flipped)

    def test_token_expires(
        self, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        e = ConsensusEngine(registry, token_ttl_s=0.5)
        r = e.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        e.verify(r.token.token, buy_proposal)  # still fresh
        with pytest.raises(TokenError, match="expired"):
            e.consume(r.token.token, buy_proposal, now=time.time() + 60.0)

    @pytest.mark.parametrize("forged", ["", "not-a-token", "abc.def", "." , "x" * 96])
    def test_forged_tokens_refused(
        self, engine: ConsensusEngine, buy_proposal: Proposal, forged: str
    ) -> None:
        with pytest.raises(TokenError):
            engine.consume(forged, buy_proposal)

    def test_token_from_another_engine_is_refused(
        self, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        a = ConsensusEngine(registry)
        b = ConsensusEngine(registry)
        r = a.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        with pytest.raises(TokenError):
            b.consume(r.token.token, buy_proposal)

    def test_concurrent_consumers_only_one_wins(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor

        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        token = r.token.token

        def attempt(_: int) -> bool:
            try:
                engine.consume(token, buy_proposal)
                return True
            except TokenError:
                return False

        with ThreadPoolExecutor(max_workers=16) as pool:
            wins = sum(pool.map(attempt, range(64)))
        assert wins == 1, f"{wins} threads each spent the same authorisation"

    def test_outstanding_count_tracks_spend(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        assert engine.outstanding_tokens() == 0
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        assert engine.outstanding_tokens() == 1
        engine.consume(r.token.token, buy_proposal)
        assert engine.outstanding_tokens() == 0


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


class TestReporting:
    def test_stats_count_every_band(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.95))
        engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.73))
        engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.30))
        assert engine.stats == {"evaluated": 3, "execute": 1, "hold": 1, "veto": 1}

    def test_result_serialises_to_json(
        self, engine: ConsensusEngine, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        r = engine.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        blob = json.dumps(r.as_dict())
        back = json.loads(blob)
        assert back["decision"] == "EXECUTE"
        assert back["execute_threshold_pct"] == 76.8
        assert len(back["votes"]) == 6

    def test_history_is_bounded(
        self, registry: AgentRegistry, buy_proposal: Proposal
    ) -> None:
        e = ConsensusEngine(registry, history_size=5)
        for _ in range(20):
            e.evaluate(buy_proposal, build_quorum(registry, VoteStance.BUY, 0.9))
        assert len(e.history(limit=100)) == 5

    def test_status_shape(self, engine: ConsensusEngine) -> None:
        s = engine.status()
        assert s["execute_threshold_pct"] == 76.8
        assert s["hold_threshold_pct"] == 70.0
        assert len(s["analysts"]) == 6
        assert math.isclose(sum(s["weights"].values()), 1.0, abs_tol=1e-12)

    def test_registry_serialises(self, registry: AgentRegistry) -> None:
        d = registry.as_dict()
        assert len(d["agents"]) == 8
        json.dumps(d)  # must be JSON-safe for the web UI telemetry feed
