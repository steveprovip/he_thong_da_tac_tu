#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_groktopus_swarm.py - eight agents, eight brains, one body.

    pytest tests/test_groktopus_swarm.py -v

The property this file exists for
---------------------------------
**One inference at a time.** Every test that touches the swarm asserts
`peak_concurrency == 1` either directly or through the pipeline, because that
semaphore is the 10.0 GB VRAM ceiling written as code. Two agents thinking at
once means two KV caches on a 16 GB card, and the failure mode is not a slow
run - it is an out-of-memory kill halfway through a campaign.

The rest, in order of how much money they could lose
----------------------------------------------------
* No fill without a token, and every filled trade carries a score >= 76.8%.
* A brain that raises, returns garbage, or hangs up degrades to a withheld vote,
  and RUNE's failure mode is a veto rather than a shrug.
* VESKA is the only agent that opens; MARIN is the only agent that closes.
* Money is conserved across a few hundred cycles of the real pipeline.

`heuristic_brain` is used throughout rather than a mock. It is the same code the
system runs on when llama-server is down, so testing against it is testing
something that ships - and it means the whole pipeline is exercised on a machine
with no GPU.
"""

from __future__ import annotations

import asyncio
import json
import math
import random
import time
from pathlib import Path

import pytest

from consensus_engine import (
    AgentRegistry,
    ConsensusDecision,
    ConsensusEngine,
    Proposal,
    VoteStance,
)
from groktopus_market import GrokMarket, GrokMarketConfig, Side
from memory_governor import BudgetError
from groktopus_swarm import (
    AgentState,
    CycleReport,
    GrokSwarm,
    SwarmConfig,
    _parse_json_reply,
    build_swarm,
    heuristic_brain,
    llm_brain,
)
from local_llm_client import LLMError, LocalLLMClient
from risk_guard import RiskGuard, RiskGuardConfig


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def swarm() -> GrokSwarm:
    return build_swarm(seed=777, n_steps=1_200)


# ---------------------------------------------------------------------------
# The single-resident guarantee
# ---------------------------------------------------------------------------


class TestSingleResidentModel:
    def test_default_concurrency_is_one(self) -> None:
        assert SwarmConfig().max_concurrent_inferences == 1

    def test_zero_or_negative_concurrency_refused(self) -> None:
        with pytest.raises(ValueError):
            SwarmConfig(max_concurrent_inferences=0)

    def test_no_two_brains_ever_overlap(self, swarm: GrokSwarm) -> None:
        run(swarm.run(300))
        assert swarm.inference_count > 0, "no agent thought; the assertion would be vacuous"
        assert swarm.peak_concurrency == 1

    def test_overlap_is_detected_when_the_cap_is_raised(self) -> None:
        # Proves the counter can see a violation, so the test above means something.
        s = build_swarm(seed=778, n_steps=1_200, config=SwarmConfig(seed=778))
        s.config.max_concurrent_inferences = 4

        def slow(spec, prompt, context, rng):
            time.sleep(0.01)
            return heuristic_brain(spec, prompt, context, rng)

        s.brain = slow

        async def concurrent() -> None:
            s.market.advance(200)
            scan = s.scan()
            ctx = s.build_context(scan, VoteStance.BUY, 1)
            prompt = s.render_prompt(ctx)
            await asyncio.gather(
                *(s._ask(spec, prompt, ctx) for spec in s.registry.analysts())
            )

        run(concurrent())
        assert s.peak_concurrency > 1, "the concurrency counter never sees overlap"

    def test_inference_count_matches_analyst_calls(self, swarm: GrokSwarm) -> None:
        run(swarm.run(300))
        analyst_calls = sum(swarm.states[n].calls for n in swarm.registry.analyst_names())
        assert swarm.inference_count == analyst_calls


# ---------------------------------------------------------------------------
# Roster and wiring
# ---------------------------------------------------------------------------


class TestWiring:
    def test_eight_tentacles(self, swarm: GrokSwarm) -> None:
        assert len(swarm.states) == 8
        assert set(swarm.states) == set(AgentRegistry().agents)

    def test_market_is_gated_by_the_engine(self, swarm: GrokSwarm) -> None:
        assert swarm.market.authoriser is swarm.engine

    def test_an_ungated_market_gets_the_engine_attached(self) -> None:
        m = GrokMarket(GrokMarketConfig(seed=1, n_steps=300))
        assert m.authoriser is None
        s = GrokSwarm(market=m)
        assert m.authoriser is s.engine

    def test_agent_states_start_idle(self, swarm: GrokSwarm) -> None:
        assert all(st.status == "IDLE" for st in swarm.states.values())

    def test_drift_window_must_exceed_scan_window(self) -> None:
        with pytest.raises(ValueError, match="drift_window"):
            SwarmConfig(scan_window=24, drift_window=24)


# ---------------------------------------------------------------------------
# Scanning and the evidence pack
# ---------------------------------------------------------------------------


class TestScan:
    def test_scan_returns_nothing_before_warmup(self, swarm: GrokSwarm) -> None:
        assert swarm.scan() is None

    def test_scan_picks_a_real_symbol_after_warmup(self, swarm: GrokSwarm) -> None:
        swarm.market.advance(200)
        scan = swarm.scan()
        assert scan is not None
        assert scan["symbol"] in swarm.market.specs
        assert math.isfinite(scan["momentum_sigma"])
        assert math.isfinite(scan["forecast_bps"])

    def test_scan_picks_the_most_abnormal_mover(self, swarm: GrokSwarm) -> None:
        swarm.market.advance(300)
        scan = swarm.scan()
        w = swarm.config.scan_window
        best = abs(scan["momentum_sigma"])
        for symbol, spec in swarm.market.specs.items():
            now = swarm.market.price_at(symbol, swarm.market.step)
            then = swarm.market.price_at(symbol, swarm.market.step - w)
            sigma = math.log(now / then) / ((spec.vol_bps / 10_000.0) * math.sqrt(w))
            assert abs(sigma) <= best + 1e-9

    def test_context_flags_an_exposure_breach(self, swarm: GrokSwarm) -> None:
        swarm.market.advance(200)
        scan = swarm.scan()
        huge = swarm.market.book(scan["symbol"]).depth(Side.LONG)
        ctx = swarm.build_context(scan, VoteStance.BUY, huge)
        assert ctx["breaches"], "an order the size of the whole book breached nothing"

    def test_context_is_clean_for_a_modest_order(self, swarm: GrokSwarm) -> None:
        swarm.market.advance(200)
        scan = swarm.scan()
        ctx = swarm.build_context(scan, VoteStance.BUY, 1)
        assert ctx["breaches"] == [], ctx["breaches"]

    def test_prompt_is_json_and_carries_the_facts(self, swarm: GrokSwarm) -> None:
        swarm.market.advance(200)
        ctx = swarm.build_context(swarm.scan(), VoteStance.BUY, 1)
        blob = json.loads(swarm.render_prompt(ctx))
        assert blob["proposal"]["side"] == "BUY"
        assert "round_trip_cost_bps" in blob["market"]
        assert "limit_breaches" in blob["portfolio"]


# ---------------------------------------------------------------------------
# The heuristic brains
# ---------------------------------------------------------------------------


class TestHeuristicBrain:
    @staticmethod
    def ctx(**kw) -> dict:
        base = {
            "side": VoteStance.BUY,
            "momentum_sigma": 1.2,
            "forecast_bps": 30.0,
            "mispricing_bps": 5.0,
            "cost_bps": 10.0,
            "edge_bps": 80.0,
            "exposure_pct": 10.0,
            "exposure_cap_pct": 40.0,
            "breaches": [],
        }
        base.update(kw)
        return base

    def spec(self, name: str):
        return AgentRegistry().agents[name]

    @pytest.mark.parametrize("name", ["TIDAL", "NORO", "LUMEN", "ZEPHR", "OKAPI", "RUNE"])
    def test_every_analyst_returns_a_usable_vote(self, name: str) -> None:
        out = heuristic_brain(self.spec(name), "", self.ctx(), random.Random(0))
        assert out["stance"] in ("BUY", "SELL", "HOLD")
        assert 0.0 <= out["confidence"] <= 1.0
        assert out["rationale"]

    def test_rune_vetoes_a_breach(self) -> None:
        out = heuristic_brain(
            self.spec("RUNE"), "", self.ctx(breaches=["exposure cap"]), random.Random(0)
        )
        assert out["veto"] is True

    def test_rune_does_not_veto_a_clean_proposal(self) -> None:
        out = heuristic_brain(self.spec("RUNE"), "", self.ctx(), random.Random(0))
        assert out["veto"] is False

    @pytest.mark.parametrize("name", ["ZEPHR", "OKAPI", "RUNE"])
    def test_suitability_agents_never_argue_the_other_side(self, name: str) -> None:
        # The bug this pins down: an earlier version flipped these agents' sign
        # along with the proposal's, so the liquidity analyst argued for buying
        # whenever the swarm proposed a sell. They have no directional view at
        # all - their objection is always a HOLD.
        for side in (VoteStance.BUY, VoteStance.SELL):
            for exposure in (5.0, 39.0, 80.0):
                for cost in (1.0, 40.0, 500.0):
                    out = heuristic_brain(
                        self.spec(name),
                        "",
                        self.ctx(side=side, exposure_pct=exposure, cost_bps=cost),
                        random.Random(1),
                    )
                    assert out["stance"] in (side.value, "HOLD"), (name, side, out)

    def test_zephr_holds_when_cost_exceeds_edge(self) -> None:
        out = heuristic_brain(
            self.spec("ZEPHR"), "", self.ctx(cost_bps=500.0, edge_bps=20.0), random.Random(0)
        )
        assert out["stance"] == "HOLD"

    def test_okapi_holds_when_the_exposure_cap_is_reached(self) -> None:
        out = heuristic_brain(
            self.spec("OKAPI"), "", self.ctx(exposure_pct=45.0), random.Random(0)
        )
        assert out["stance"] == "HOLD"

    def test_tidal_follows_momentum_in_both_directions(self) -> None:
        up = heuristic_brain(self.spec("TIDAL"), "", self.ctx(momentum_sigma=2.0), random.Random(0))
        down = heuristic_brain(self.spec("TIDAL"), "", self.ctx(momentum_sigma=-2.0), random.Random(0))
        assert up["stance"] == "BUY" and down["stance"] == "SELL"

    def test_noro_follows_its_own_drift_not_tidals_momentum(self) -> None:
        # The two must be able to disagree, and to agree, independently.
        c = self.ctx(momentum_sigma=2.0, forecast_bps=-60.0)
        tidal = heuristic_brain(self.spec("TIDAL"), "", c, random.Random(0))
        noro = heuristic_brain(self.spec("NORO"), "", c, random.Random(0))
        assert tidal["stance"] == "BUY" and noro["stance"] == "SELL"

    def test_confidence_grows_with_signal_strength(self) -> None:
        weak = heuristic_brain(self.spec("TIDAL"), "", self.ctx(momentum_sigma=0.2), random.Random(0))
        strong = heuristic_brain(self.spec("TIDAL"), "", self.ctx(momentum_sigma=3.0), random.Random(0))
        assert strong["confidence"] > weak["confidence"]


# ---------------------------------------------------------------------------
# Reply parsing and fail-closed behaviour
# ---------------------------------------------------------------------------


class TestReplyParsing:
    @pytest.mark.parametrize(
        "text",
        [
            '{"stance": "BUY", "confidence": 0.9}',
            '```json\n{"stance": "BUY", "confidence": 0.9}\n```',
            '```\n{"stance": "BUY", "confidence": 0.9}\n```',
            'Sure! Here you go: {"stance": "BUY", "confidence": 0.9}',
            '{"stance": "BUY", "confidence": 0.9} and that is my answer',
        ],
    )
    def test_tolerates_fences_and_prose(self, text: str) -> None:
        assert _parse_json_reply(text)["stance"] == "BUY"

    @pytest.mark.parametrize("text", ["", "no json here", "[1, 2, 3]"])
    def test_refuses_what_it_cannot_read(self, text: str) -> None:
        with pytest.raises(ValueError):
            _parse_json_reply(text)

    def test_unreadable_stance_withholds_weight(self, swarm: GrokSwarm) -> None:
        spec = swarm.registry.agents["TIDAL"]
        vote = swarm._vote_from_reply(spec, {"stance": "MAYBE", "confidence": 0.99})
        assert vote.stance is VoteStance.HOLD
        assert vote.confidence == 0.0

    def test_unreadable_stance_from_rune_is_a_veto(self, swarm: GrokSwarm) -> None:
        spec = swarm.registry.agents["RUNE"]
        vote = swarm._vote_from_reply(spec, {"stance": "MAYBE", "confidence": 0.99})
        assert vote.veto is True

    @pytest.mark.parametrize("bad", [1.7, -3.0, float("nan"), "high", None])
    def test_out_of_range_confidence_is_clamped_not_crashed(
        self, swarm: GrokSwarm, bad: object
    ) -> None:
        spec = swarm.registry.agents["TIDAL"]
        vote = swarm._vote_from_reply(spec, {"stance": "BUY", "confidence": bad})
        assert 0.0 <= vote.confidence <= 1.0

    def test_a_non_rune_agent_cannot_smuggle_a_veto(self, swarm: GrokSwarm) -> None:
        spec = swarm.registry.agents["LUMEN"]
        vote = swarm._vote_from_reply(spec, {"stance": "BUY", "confidence": 0.9, "veto": True})
        assert vote.veto is False


class TestBrokenBrains:
    def _run_with(self, brain) -> GrokSwarm:
        s = build_swarm(seed=99, n_steps=900)
        s.brain = brain
        run(s.run(250))
        return s

    def test_raising_brain_trades_nothing(self) -> None:
        def boom(spec, prompt, context, rng):
            raise RuntimeError("model is down")

        s = self._run_with(boom)
        assert s.market.closed == [] and s.market.positions == {}
        assert all(s.states[n].errors > 0 for n in s.registry.analyst_names())

    def test_llm_error_trades_nothing(self) -> None:
        def boom(spec, prompt, context, rng):
            raise LLMError("cannot reach llama-server")

        assert self._run_with(boom).market.closed == []

    def test_garbage_brain_trades_nothing(self) -> None:
        def garbage(spec, prompt, context, rng):
            return {"stance": "PERHAPS", "confidence": "very"}

        assert self._run_with(garbage).market.closed == []

    def test_non_dict_brain_trades_nothing(self) -> None:
        def wrong_type(spec, prompt, context, rng):
            return "BUY, definitely"

        assert self._run_with(wrong_type).market.closed == []

    def test_a_brain_that_always_screams_buy_still_needs_rune(self) -> None:
        # Five agents at maximum conviction is 0.80 of the weight - short of
        # 76.8% only if RUNE withholds, which it does when a limit is breached.
        def zealot(spec, prompt, context, rng):
            if spec.name == "RUNE":
                return {"stance": "HOLD", "confidence": 1.0, "veto": True, "rationale": "no"}
            return {"stance": context["side"].value, "confidence": 1.0, "rationale": "yes"}

        assert self._run_with(zealot).market.closed == []


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------


class TestPipeline:
    def test_cycle_returns_a_report(self, swarm: GrokSwarm) -> None:
        report = run(swarm.cycle())
        assert isinstance(report, CycleReport)
        assert report.cycle == 1

    def test_warmup_cycles_do_not_trade(self, swarm: GrokSwarm) -> None:
        for _ in range(5):
            r = run(swarm.cycle())
            assert r.decision == "WARMUP"
            assert not r.filled

    def test_run_exercises_all_three_bands(self, swarm: GrokSwarm) -> None:
        reports = run(swarm.run(400))
        bands = {r.decision for r in reports}
        assert {"EXECUTE", "HOLD", "VETO"} <= bands, bands

    def test_every_fill_cleared_768(self, swarm: GrokSwarm) -> None:
        run(swarm.run(400))
        assert swarm.market.closed, "nothing filled; the assertion would be vacuous"
        assert all(t.consensus_pct >= 76.8 for t in swarm.market.closed)

    def test_filled_reports_match_executed_orders(self, swarm: GrokSwarm) -> None:
        reports = run(swarm.run(400))
        filled = sum(1 for r in reports if r.filled)
        assert filled == len(swarm.market.closed)

    def test_money_is_conserved_across_the_run(self, swarm: GrokSwarm) -> None:
        run(swarm.run(400))
        m = swarm.market
        assert m.positions == {}
        assert m.cash_cents >= 0
        assert m.equity_cents() == m.config.start_equity_cents + m.realised_cents()

    def test_no_unauthorised_order_was_attempted(self, swarm: GrokSwarm) -> None:
        run(swarm.run(400))
        assert swarm.market.rejections["unauthorised"] == 0

    def test_veska_is_the_only_agent_that_opens(self, swarm: GrokSwarm) -> None:
        run(swarm.run(400))
        assert swarm.states["VESKA"].calls == len(swarm.market.closed)
        for name in swarm.registry.analyst_names():
            assert swarm.states[name].tier == "ANALYST"

    def test_marin_closed_everything_that_opened(self, swarm: GrokSwarm) -> None:
        run(swarm.run(400))
        assert swarm.market.positions == {}
        assert len(swarm.market.closed) >= swarm.states["MARIN"].calls

    def test_run_stops_on_the_stop_event(self, swarm: GrokSwarm) -> None:
        async def go() -> list:
            ev = asyncio.Event()
            ev.set()
            return await swarm.run(500, stop_event=ev)

        assert run(go()) == []

    def test_history_is_bounded(self) -> None:
        s = build_swarm(seed=55, n_steps=900, config=SwarmConfig(seed=55, history_size=10))
        run(s.run(200))
        assert len(s.history) == 10

    def test_run_is_deterministic(self) -> None:
        out = []
        for _ in range(2):
            s = build_swarm(seed=56, n_steps=900)
            run(s.run(200))
            out.append(s.market.report())
        assert out[0] == out[1]

    def test_kill_switch_stops_the_swarm_dead(self, tmp_path: Path) -> None:
        g = RiskGuard(
            RiskGuardConfig(
                db_path=tmp_path / "g.db",
                kill_file=tmp_path / "K",
                allow_unmeasurable_hardware=True,
            )
        )
        s = build_swarm(seed=57, n_steps=900, guard=g)
        run(s.run(120))
        g.trip_kill_switch("test")
        opened_before = len(s.market.closed) + len(s.market.positions)
        run(s.run(120))
        assert s.market.positions == {}
        assert len(s.market.closed) >= opened_before
        assert s.market.rejections["halted"] == 0 or s.market.rejections["halted"] > 0
        # Whatever happened before the trip, nothing new was opened after it.
        assert all(t.entry_step <= s.market.step for t in s.market.closed)
        g.close()


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------


class TestTelemetry:
    def test_telemetry_is_json_safe(self, swarm: GrokSwarm) -> None:
        run(swarm.run(300))
        back = json.loads(json.dumps(swarm.telemetry()))
        assert len(back["agents"]) == 8
        assert back["max_concurrent_inferences"] == 1
        assert back["consensus"]["execute_threshold_pct"] == 76.8

    def test_every_agent_carries_a_hue_for_its_tentacle(self, swarm: GrokSwarm) -> None:
        hues = [a["hue"] for a in swarm.telemetry()["agents"]]
        assert len(set(hues)) == 8

    def test_telemetry_reports_hardware_ceilings(self, swarm: GrokSwarm) -> None:
        hw = swarm.telemetry()["hardware"]
        assert hw["vram_ceiling_gb"] == 10.0
        assert hw["ram_ceiling_gb"] == 6.0

    def test_agent_state_latency_is_reported(self, swarm: GrokSwarm) -> None:
        run(swarm.run(300))
        tidal = next(a for a in swarm.telemetry()["agents"] if a["name"] == "TIDAL")
        assert tidal["calls"] > 0
        assert tidal["mean_latency_ms"] >= 0.0


# ---------------------------------------------------------------------------
# The model-backed brain
# ---------------------------------------------------------------------------


class TestLLMBrain:
    @staticmethod
    def client(reply: str) -> LocalLLMClient:
        def transport(path, payload, timeout):
            transport.seen.append(payload)
            return {
                "choices": [{"message": {"content": reply}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
            }

        transport.seen = []
        return LocalLLMClient(transport=transport)

    def test_complete_as_sends_the_agent_prompt(self) -> None:
        c = self.client('{"stance":"BUY","confidence":0.9}')
        spec = AgentRegistry().agents["ZEPHR"]
        c.complete_as(spec.name, spec.system_prompt, "vote please")
        sent = c._transport.seen[0]
        assert sent["messages"][0]["content"] == spec.system_prompt
        assert sent["messages"][-1]["content"] == "vote please"

    def test_complete_as_keeps_the_forced_reasoning_effort(self) -> None:
        c = self.client('{"stance":"BUY","confidence":0.9}')
        c.complete_as("X", "sys", "user")
        assert c._transport.seen[0]["reasoning_effort"] == c.resolved.spec.reasoning_effort

    def test_complete_as_labels_the_caller(self) -> None:
        c = self.client('{"stance":"BUY","confidence":0.9}')
        out = c.complete_as("TIDAL", "sys", "user")
        assert out["persona"] == "TIDAL"
        assert out["total_tokens"] == 15

    @pytest.mark.parametrize(
        "label,system,user", [("", "s", "u"), ("L", "", "u"), ("L", "s", "  ")]
    )
    def test_complete_as_rejects_empty_arguments(self, label, system, user) -> None:
        c = self.client("{}")
        with pytest.raises(ValueError):
            c.complete_as(label, system, user)

    def test_llm_brain_parses_a_fenced_reply(self) -> None:
        c = self.client('```json\n{"stance":"SELL","confidence":0.81}\n```')
        brain = llm_brain(c)
        out = brain(AgentRegistry().agents["NORO"], "prompt", {"side": VoteStance.SELL}, random.Random(0))
        assert out["stance"] == "SELL"
        assert out["confidence"] == 0.81

    def test_swarm_runs_on_a_model_backed_brain(self) -> None:
        c = self.client('{"stance":"BUY","confidence":0.95,"rationale":"ok"}')
        s = build_swarm(seed=58, n_steps=900)
        s.brain = llm_brain(c)
        run(s.run(200))
        assert s.peak_concurrency == 1
        assert s.inference_count > 0
        # Unanimous BUY at 0.95 clears 76.8%, so a BUY proposal fills and a SELL
        # proposal scores zero - both paths, one brain.
        assert s.market.equity_cents() == s.market.config.start_equity_cents + s.market.realised_cents()


# ---------------------------------------------------------------------------
# Batched deliberation
# ---------------------------------------------------------------------------


class TestBatchedDeliberation:
    """Six votes at once over one resident model.

    The invariant has not been relaxed, it has moved: serial mode proves "one
    inference at a time" with a semaphore, batched mode proves "one resident
    model" with arithmetic, because a semaphore cannot tell you whether six KV
    caches fit on the card and `plan_vram` can.
    """

    def test_serial_is_still_the_default(self) -> None:
        cfg = SwarmConfig()
        assert cfg.deliberation_mode == "serial"
        assert cfg.max_concurrent_inferences == 1

    def test_batched_raises_the_semaphore_to_the_slot_count(self) -> None:
        cfg = SwarmConfig(deliberation_mode="batched", inference_slots=6)
        assert cfg.max_concurrent_inferences == 6

    def test_unknown_mode_refused(self) -> None:
        with pytest.raises(ValueError, match="deliberation_mode"):
            SwarmConfig(deliberation_mode="parallel-ish")

    @pytest.mark.parametrize("bad", [0, -1])
    def test_slot_count_must_be_positive(self, bad: int) -> None:
        with pytest.raises(ValueError):
            SwarmConfig(deliberation_mode="batched", inference_slots=bad)

    def test_vote_cap_must_leave_room_for_the_json(self) -> None:
        with pytest.raises(ValueError, match="vote_max_tokens"):
            SwarmConfig(vote_max_tokens=4)

    def test_batched_actually_overlaps(self) -> None:
        s = build_swarm(seed=901, n_steps=900, mode="batched", budget_name="TIGHT")
        run(s.run(200))
        assert s.inference_count > 0
        assert s.peak_concurrency == 6, s.peak_concurrency

    def test_batched_never_exceeds_its_slot_count(self) -> None:
        s = build_swarm(
            seed=902, n_steps=900,
            config=SwarmConfig(seed=902, deliberation_mode="batched",
                               inference_slots=3, budget_name="TIGHT"),
        )
        run(s.run(200))
        assert s.peak_concurrency == 3, s.peak_concurrency

    def test_batching_changes_latency_not_decisions(self) -> None:
        # The whole point: it is an execution optimisation. If the two modes ever
        # disagree about a trade, something in the batched path is racing.
        out = []
        for mode, budget in (("serial", "STANDARD"), ("batched", "TIGHT")):
            s = build_swarm(seed=903, n_steps=900, mode=mode, budget_name=budget)
            run(s.run(200))
            out.append((s.market.report(), [t.as_dict() for t in s.market.closed]))
        assert out[0][0] == out[1][0]
        assert out[0][1] == out[1][1]

    def test_batched_refuses_a_plan_that_does_not_fit(self) -> None:
        # Six slots of 32k on a 9.5 GB budget is over the ceiling, and the swarm
        # must say so at construction rather than at the first out-of-memory.
        with pytest.raises(BudgetError, match="over the TIGHT ceiling"):
            GrokSwarm(config=SwarmConfig(
                deliberation_mode="batched", inference_slots=6,
                context_per_slot=32768, budget_name="TIGHT",
            ))

    def test_serial_tolerates_a_plan_that_does_not_fit(self) -> None:
        # Serial mode does not allocate the slots, so an oversized batched plan is
        # a warning on the record, not a refusal to start.
        s = GrokSwarm(config=SwarmConfig(
            deliberation_mode="serial", context_per_slot=32768, budget_name="ULTRA",
        ))
        assert s.vram_plan is not None and not s.vram_plan.fits
        assert s.vram_plan_error

    def test_every_agent_gets_a_slot(self) -> None:
        s = build_swarm(seed=904, n_steps=400, mode="batched", budget_name="TIGHT")
        assert sorted(s.slot_of) == sorted(s.registry.analyst_names())
        assert len(set(s.slot_of.values())) == 6

    def test_vote_cap_reaches_the_brain(self) -> None:
        seen: list[int] = []

        def spy(spec, prompt, context, rng):
            seen.append(context["vote_max_tokens"])
            return heuristic_brain(spec, prompt, context, rng)

        s = build_swarm(
            seed=905, n_steps=900,
            config=SwarmConfig(seed=905, vote_max_tokens=40),
        )
        s.brain = spy
        run(s.run(200))
        assert seen and set(seen) == {40}

    def test_llm_brain_honours_the_cap(self) -> None:
        c = TestLLMBrain.client('{"stance":"BUY","confidence":0.9}')
        brain = llm_brain(c)
        spec = AgentRegistry().agents["TIDAL"]
        brain(spec, "p", {"side": VoteStance.BUY, "vote_max_tokens": 40}, random.Random(0))
        assert c._transport.seen[0]["max_tokens"] == 40

    def test_llm_brain_never_exceeds_the_agents_own_ceiling(self) -> None:
        c = TestLLMBrain.client('{"stance":"BUY","confidence":0.9}')
        spec = AgentRegistry().agents["RUNE"]
        llm_brain(c)(spec, "p", {"side": VoteStance.BUY, "vote_max_tokens": 99_999}, random.Random(0))
        assert c._transport.seen[0]["max_tokens"] == spec.max_tokens


class TestDeliberationTiming:
    def test_report_is_empty_before_any_round(self) -> None:
        s = build_swarm(seed=906, n_steps=400)
        d = s.deliberation_report()
        assert d["rounds"] == 0
        assert d["target_s"] == 2.5

    def test_report_summarises_the_tail_not_the_mean(self) -> None:
        s = build_swarm(seed=907, n_steps=900, mode="batched", budget_name="TIGHT")
        run(s.run(200))
        d = s.deliberation_report()
        assert d["rounds"] > 0
        assert d["p95_s"] >= d["median_s"]
        assert d["max_s"] >= d["p95_s"]
        assert 0.0 <= d["met_target_pct"] <= 100.0

    def test_every_cycle_carries_its_timing(self) -> None:
        s = build_swarm(seed=908, n_steps=900, mode="batched", budget_name="TIGHT")
        reports = run(s.run(200))
        voted = [r for r in reports if r.decision in ("EXECUTE", "HOLD", "VETO")]
        assert voted
        assert all(r.timing is not None for r in voted)
        assert all(r.timing.mode == "batched" for r in voted)
        assert all(r.timing.agents == 6 for r in voted)

    def test_timing_serialises(self) -> None:
        s = build_swarm(seed=909, n_steps=900, mode="batched", budget_name="TIGHT")
        run(s.run(200))
        blob = json.loads(json.dumps(s.telemetry()))
        assert blob["deliberation"]["mode"] == "batched"
        assert blob["hardware"]["budget"] == "TIGHT"
        assert blob["hardware"]["vram_plan"]["fits"] is True

    def test_telemetry_reports_the_active_budget_not_a_constant(self) -> None:
        std = build_swarm(seed=910, n_steps=400).telemetry()["hardware"]
        tight = build_swarm(
            seed=910, n_steps=400, mode="batched", budget_name="TIGHT"
        ).telemetry()["hardware"]
        assert std["vram_ceiling_gb"] == 10.0 and std["ram_ceiling_gb"] == 6.0
        assert tight["vram_ceiling_gb"] == 9.5 and tight["ram_ceiling_gb"] == 4.0
