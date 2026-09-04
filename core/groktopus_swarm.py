#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
groktopus_swarm.py - eight agents, eight brains, one body.

The "one body" is not a metaphor. There is exactly one model resident in VRAM -
Profile S, Qwen3.8-27B-UD-IQ2_XXS, roughly 9.07 GB against a 10.0 GB ceiling -
and all eight agents are personas multiplexed over it. Two of them thinking at
once would mean two KV caches on a 16 GB card, so every inference in this module
passes through an `asyncio.Semaphore(1)`. That semaphore is the VRAM ceiling
expressed in code, and `peak_concurrency` exists so a test can prove it held.

Why asyncio at all, if inference is serial
------------------------------------------
Because everything *around* inference is not: settling positions, reading
telemetry, serving the dashboard, waiting on a web fetch for LUMEN. The event
loop keeps those responsive while one agent has the GPU. The blocking HTTP call
to llama-server runs in a worker thread (`asyncio.to_thread`) so it cannot stall
the loop; the semaphore is what keeps it single-file.

The pipeline, and why it cannot be short-circuited
--------------------------------------------------
    TIDAL scans -> a Proposal exists
    six analysts vote (serially, one GPU) -> ConsensusEngine scores
    >= 76.8% -> a single-use token is minted
    VESKA spends the token -> GrokMarket opens the position
    MARIN settles on stop / target / horizon / kill switch

VESKA has no code path to `GrokMarket.execute` that does not carry a token, and
`GrokMarket` mints none of its own. MARIN is the only caller of `close`. The
separation is structural rather than procedural: there is no "trusted" flag an
agent could set to skip the vote.

Offline by default
------------------
`brain=None` and no reachable llama-server means the swarm runs on
`heuristic_brain`, which derives each agent's vote from the market state with
arithmetic instead of a language model. That is how the test suite exercises the
full pipeline on a machine with no GPU, and how the dashboard demo runs before
the operator has started the server. `LocalLLMClient` is used the moment a
server answers `/health`.

CLI:
    python core/groktopus_swarm.py --selftest
    python core/groktopus_swarm.py --demo --cycles 40
    python core/groktopus_swarm.py --demo --live          # requires llama-server
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import random
import statistics
import sys
import threading
import time
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_SIM = str(_HERE.parent / "simulation")
if _SIM not in sys.path:
    sys.path.insert(0, _SIM)

from consensus_engine import (  # noqa: E402
    AdaptiveGateConfig,
    AgentRegistry,
    AgentSpec,
    MarketRegime,
    RegimeGate,
    ConsensusDecision,
    ConsensusEngine,
    ConsensusResult,
    Proposal,
    Vote,
    VoteStance,
)
from groktopus_market import (  # noqa: E402
    CloseReason,
    GrokMarket,
    GrokMarketConfig,
    LiquidityError,
    MarketHalted,
    Side,
    UnauthorisedOrder,
)
from local_llm_client import LLMError, LocalLLMClient  # noqa: E402
from barrier_planner import BarrierConfig, CostModel, minimum_viable_k_tp  # noqa: E402
from real_alpha_engine import Candle, RealAlphaEngine  # noqa: E402
from memory_governor import (  # noqa: E402
    BudgetError,
    MemoryBudget,
    get_budget,
    max_context_per_slot,
    measured_weights_gb,
    plan_vram,
)
from risk_guard import (  # noqa: E402
    OverdraftError,
    RiskGuard,
    read_process_tree_ram_gb,
    snapshot_hardware,
)

__all__ = [
    "AgentState",
    "SwarmConfig",
    "GrokSwarm",
    "CycleReport",
    "DeliberationTiming",
    "heuristic_brain",
    "llm_brain",
    "build_swarm",
]


# ---------------------------------------------------------------------------
# Per-agent live state (what the octopus dashboard renders)
# ---------------------------------------------------------------------------


@dataclass
class AgentState:
    """One tentacle's status, updated in place as the swarm runs."""

    name: str
    tier: str
    role: str
    weight: float
    hue: int
    status: str = "IDLE"          # IDLE | THINKING | VOTED | ACTING | VETO
    stance: Optional[str] = None
    confidence: float = 0.0
    rationale: str = ""
    calls: int = 0
    total_latency_s: float = 0.0
    errors: int = 0
    last_active_at: float = 0.0

    @property
    def mean_latency_s(self) -> float:
        return (self.total_latency_s / self.calls) if self.calls else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "tier": self.tier,
            "role": self.role,
            "weight": round(self.weight, 4),
            "hue": self.hue,
            "status": self.status,
            "stance": self.stance,
            "confidence": round(self.confidence, 4),
            "rationale": self.rationale,
            "calls": self.calls,
            "errors": self.errors,
            "mean_latency_ms": round(self.mean_latency_s * 1000.0, 1),
            "last_active_at": self.last_active_at,
        }


@dataclass(frozen=True)
class DeliberationTiming:
    """How long one six-analyst round took, and whether it met its budget.

    Recorded per cycle rather than averaged, because a deliberation budget is a
    latency guarantee and a mean hides the tail that actually breaks it.
    """

    seconds: float
    mode: str
    agents: int
    slots: int
    peak_concurrency: int
    target_s: float = 2.5

    @property
    def meets_target(self) -> bool:
        return self.seconds <= self.target_s

    def as_dict(self) -> dict[str, Any]:
        return {
            "seconds": round(self.seconds, 4),
            "mode": self.mode,
            "agents": self.agents,
            "slots": self.slots,
            "peak_concurrency": self.peak_concurrency,
            "target_s": self.target_s,
            "meets_target": self.meets_target,
        }


@dataclass
class CycleReport:
    """What one full propose-vote-execute-settle round did."""

    cycle: int
    proposal: Optional[dict[str, Any]]
    decision: str
    score_pct: float
    filled: bool
    reject_reason: Optional[str]
    closed: list[dict[str, Any]] = field(default_factory=list)
    equity_cents: int = 0
    step: int = 0
    timing: Optional[DeliberationTiming] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "cycle": self.cycle,
            "proposal": self.proposal,
            "decision": self.decision,
            "score_pct": round(self.score_pct, 2),
            "filled": self.filled,
            "reject_reason": self.reject_reason,
            "closed": self.closed,
            "equity_cents": self.equity_cents,
            "step": self.step,
            "timing": self.timing.as_dict() if self.timing else None,
        }


# ---------------------------------------------------------------------------
# Brains
# ---------------------------------------------------------------------------


def _clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)


def heuristic_brain(
    spec: AgentSpec, prompt: str, context: dict[str, Any], rng: random.Random
) -> dict[str, Any]:
    """Each agent's judgement as arithmetic on the market state, no model involved.

    This is not a stub standing in for the real thing - it is the fallback the
    system runs on whenever llama-server is not up, and it is what the test suite
    exercises the pipeline with. Each agent reads the signal its role actually
    owns, so the six votes are genuinely different views of the same proposal
    rather than one number with noise added six times:

      TIDAL  short-window momentum, in units of that window's own expected move
      NORO   a drift forecast fitted on a much longer window, as bps over the hold
      LUMEN  a sentiment proxy: weak signal, wide dispersion, smallest weight
      ZEPHR  the quoted round-trip cost of this size against the available edge
      OKAPI  how much of equity the proposal would put at risk
      RUNE   the hard limits, and a veto when one is breached

    Two kinds of vote, and the difference matters
    ---------------------------------------------
    TIDAL, NORO and LUMEN read *direction*: their signal says where price goes,
    so a negative reading is a vote for the opposite side, and against a BUY
    proposal it is genuine dissent that subtracts from the score.

    ZEPHR, OKAPI and RUNE read *suitability*: they have no view on direction at
    all. Their prompts say so - ZEPHR votes HOLD when the book is too thin,
    OKAPI votes HOLD when the exposure budget would be breached. So a negative
    reading from them is a HOLD that withholds weight, never a vote for the
    other side. An earlier version of this function flipped their sign along with
    the proposal's, which made the liquidity analyst argue for buying whenever
    the swarm proposed a sell. It could not have been right, and it made the
    76.8% bar unreachable on short trades.
    """
    side = context["side"]
    opposite = VoteStance.SELL if side is VoteStance.BUY else VoteStance.BUY

    def directional(signal: float) -> tuple[VoteStance, float]:
        """A view on where price goes. Sign picks the side; size picks confidence."""
        strength = math.tanh(signal)
        if abs(strength) < 0.02:
            return VoteStance.HOLD, 0.5
        stance = VoteStance.BUY if strength > 0 else VoteStance.SELL
        return stance, _clamp01(0.55 + 0.42 * min(1.0, abs(strength)))

    def suitability(headroom: float) -> tuple[VoteStance, float]:
        """A view on whether this trade is *allowed*, not on where price goes."""
        if headroom <= 0.0:
            return VoteStance.HOLD, 1.0
        return side, _clamp01(0.55 + 0.42 * min(1.0, headroom))

    momentum_sigma = context["momentum_sigma"]
    forecast_bps = context["forecast_bps"]
    cost_bps = context["cost_bps"]
    edge_bps = context["edge_bps"]
    exposure_pct = context["exposure_pct"]
    exposure_cap = context["exposure_cap_pct"]
    breaches = context["breaches"]

    name = spec.name

    # There was an oracle here, and its removal is the point of this function.
    #
    # An earlier revision read `context["true_bias"]`, which was computed in
    # `build_context` as `sign(price[step + horizon] - price[step])` - the
    # realised future move - and made TIDAL, NORO and LUMEN vote that direction
    # about 82% of the time. That is where the "74% win rate" came from. It was
    # not a strategy result; it was a measurement of the lookahead, and it was
    # active in every simulation because its only guard was `symbol not in
    # live_symbols`.
    #
    # Nothing below reads any price later than the current step. The proof is
    # mechanical rather than by inspection: `test_no_lookahead` rewrites the
    # future of the price path after a decision point and asserts every
    # proposal, vote and score is byte-identical.
    if name == "TIDAL":
        # Momentum, damped when RSI says the move is already stretched. A 2-sigma
        # push into RSI 82 is a different proposition from the same push into
        # RSI 55, and the scanner is the right place to notice that.
        rsi = float(context.get("rsi_14", 50.0) or 50.0)
        sweep = float(context.get("liquidity_sweep", 0.0) or 0.0)
        stretch = 1.0 - min(1.0, abs(rsi - 50.0) / 50.0) * 0.5
        # A confirmed sweep argues for the reversal, against the momentum that
        # produced it. That is the point of the pattern, so it is added rather
        # than used to damp: when the two disagree the scanner should say so and
        # let the weighted vote settle it.
        stance, conf = directional(momentum_sigma * stretch + sweep * 0.55)
        why = f"momentum {momentum_sigma:+.2f} sigma, RSI {rsi:.0f}, sweep {sweep:+.2f}"
    elif name == "NORO":
        # Drift forecast, corrected toward VWAP. A positive z-score means spot is
        # expensive against the volume-weighted consensus, so it pulls the
        # fair-value read down - the sign flip lives here rather than inside
        # `calculate_vwap_zscore`, so both call sites stay readable.
        vwap_z = float(context.get("vwap_zscore", 0.0) or 0.0)
        stance, conf = directional(forecast_bps / 40.0 - vwap_z * 0.30)
        why = f"drift {forecast_bps:+.1f} bps, VWAP z {vwap_z:+.2f}"
    elif name == "LUMEN":
        # No newsfeed is attached in simulation, so LUMEN's sentiment proxy is
        # order-flow pressure plus deliberate noise: order-book imbalance is the
        # closest observable to "what the crowd is doing right now". The noise
        # term stays because a sentiment read that is really a microstructure
        # read should not masquerade as an independent opinion.
        obi = float(context.get("orderbook_imbalance", 0.0) or 0.0)
        cvd = float(context.get("cvd_proxy", 0.0) or 0.0)
        tone = (math.tanh(momentum_sigma / 2.0) * 0.25 + obi * 0.35 + cvd * 0.40
                + rng.uniform(-0.30, 0.30))
        stance, conf = directional(tone)
        why = f"flow tone {tone:+.2f} (OBI {obi:+.2f}, CVD {cvd:+.2f})"
    elif name == "ZEPHR":
        stance, conf = suitability((edge_bps - cost_bps) / max(1.0, edge_bps + cost_bps))
        why = f"round trip costs {cost_bps:.1f} bps against {edge_bps:.1f} bps of edge"
    elif name == "OKAPI":
        stance, conf = suitability((exposure_cap - exposure_pct) / max(1.0, exposure_cap))
        why = f"portfolio would be {exposure_pct:.1f}% at risk against a {exposure_cap:.0f}% cap"
    elif name == "RUNE":
        if breaches:
            return {
                "stance": "HOLD",
                "confidence": 1.0,
                "veto": True,
                "rationale": "limit breached: " + "; ".join(breaches),
            }
        ratio = edge_bps / max(cost_bps, 1.0)
        room = (exposure_cap - exposure_pct) / max(1.0, exposure_cap)
        stance, conf = suitability(min(room, (ratio - 1.0) / 3.0))
        why = f"no limit breached; edge/cost {ratio:.2f}, {exposure_pct:.1f}% at risk"
    else:
        stance, conf = VoteStance.HOLD, 0.0
        why = "not a voting agent"

    return {
        "stance": stance.value,
        "confidence": round(conf, 4),
        "veto": False,
        "rationale": f"{name}: {why}",
    }


def llm_brain(client: LocalLLMClient) -> Callable[..., dict[str, Any]]:
    """A brain backed by the single resident model on llama-server.

    Returns a callable with the same signature as `heuristic_brain`, so the swarm
    does not care which one it is holding. The agent's system prompt comes from
    config/groktopus_agents.json via `LocalLLMClient.complete_as`, which keeps the
    config file as the one place those prompts live.
    """

    def brain(
        spec: AgentSpec, prompt: str, context: dict[str, Any], rng: random.Random
    ) -> dict[str, Any]:
        # Output length is the cheapest latency lever in the system: deliberation
        # time is linear in it, and a vote is a stance, a number and one short
        # sentence. The config cap wins over the agent's own generous default.
        cap = int(context.get("vote_max_tokens") or spec.max_tokens)
        out = client.complete_as(
            spec.name,
            spec.system_prompt,
            prompt,
            temperature=spec.temperature,
            max_tokens=max(8, min(spec.max_tokens, cap)),
        )
        text = (out.get("text") or "").strip()
        return _parse_json_reply(text)

    return brain


def _parse_json_reply(text: str) -> dict[str, Any]:
    """Pull a JSON object out of a model reply, tolerating fences and prose."""
    t = text.strip()
    if t.startswith("```"):
        lines = t.splitlines()
        if len(lines) >= 2:
            body = lines[1:]
            if body and body[-1].strip().startswith("```"):
                body = body[:-1]
            t = "\n".join(body).strip()
    if not t.startswith("{"):
        start = t.find("{")
        if start == -1:
            raise ValueError(f"no JSON object in reply: {text[:160]!r}")
        t = t[start:]
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        obj, _ = json.JSONDecoder().raw_decode(t)
        if not isinstance(obj, dict):
            raise ValueError(f"reply was JSON but not an object: {text[:160]!r}")
        return obj


# ---------------------------------------------------------------------------
# Swarm
# ---------------------------------------------------------------------------


@dataclass
class SwarmConfig:
    """How the swarm scans, sizes and gates itself."""

    scan_window: int = 24
    sma_window: int = 12
    #: NORO's fitting window. Deliberately several times `scan_window` - see the
    #: comment in `GrokSwarm.scan` for why the two must not share a horizon.
    drift_window: int = 120
    #: Steps per OHLCV bar. Indicators that need highs, lows and volume read
    #: bars, not raw steps; one step has no range at all.
    bar_steps: int = 4
    #: Regime-adaptive consensus thresholds. OFF by default - see the docstring
    #: on AdaptiveGateConfig for why the default matters, and run
    #: benchmarks/edge_report.py --adaptive to find out whether turning it on
    #: helps on your data before you turn it on.
    adaptive_gate: bool = False
    #: Triple Barrier (Lopez de Prado ch.3): ATR-scaled profit and loss barriers
    #: plus a vertical barrier in time. OFF by default so the flat-bps behaviour
    #: every existing test describes stays the behaviour those tests describe.
    triple_barrier: bool = False
    #: 4.0, not the 1.5 that looks natural on a chart. At a 9.5 bps round trip
    #: against a 6 bps bar range, k_tp = 1.5 puts the profit barrier INSIDE the
    #: cost band and needs a 100% win rate to break even; 4.17 is where a coin
    #: flip breaks even. `core/barrier_planner.py --report` prints the table.
    k_tp: float = 4.0
    k_sl: float = 1.0
    max_holding_bars: int = 15
    break_even_at: float = 0.40
    #: Fractional Kelly sizing (Thorp / Kelly 1956). OFF by default so existing
    #: tests that assert exact fixed-fraction quantities continue to see them.
    kelly_sizing: bool = False
    kelly_lam: float = 0.25
    kelly_min_trades: int = 30
    horizon_steps: int = 36
    risk_fraction: float = 0.16
    max_exposure_pct: float = 45.0
    stop_bps: float = 75.0
    target_bps: float = 110.0
    max_open_positions: int = 3
    #: One, in serial mode. This is the VRAM ceiling written as an integer: a
    #: second *model* on the card is not affordable, and the semaphore is what
    #: makes that structural rather than hoped-for.
    #:
    #: Batched mode raises it to `inference_slots`, and that is NOT a relaxation
    #: of the invariant - it is the same invariant, enforced one level up. Six
    #: concurrent requests still hit ONE resident model; what multiplies is the
    #: KV cache, and `GrokSwarm.__init__` costs those slots against the memory
    #: budget and refuses to start if they do not fit. A semaphore cannot check
    #: that; arithmetic can.
    max_concurrent_inferences: int = 1
    #: "serial" keeps the original one-request-at-a-time pipeline. "batched"
    #: issues all six analyst votes concurrently against one llama-server started
    #: with `--parallel N`, which is what brings a deliberation under 2.5 s -
    #: see core/inference_planner.py for the arithmetic.
    deliberation_mode: str = "serial"
    inference_slots: int = 6
    #: Output cap per vote. A vote is a stance, a confidence and one short
    #: sentence; nothing about it needs 768 tokens, and deliberation latency is
    #: linear in this number. It is the cheapest latency lever in the system.
    vote_max_tokens: int = 48
    #: Which envelope from config/profiles.json this swarm is planned against.
    budget_name: str = "STANDARD"
    context_per_slot: int = 4096
    #: The directive's deliberation budget. Recorded per cycle, never enforced by
    #: cutting a vote short - a truncated quorum is a cancelled proposal, and
    #: missing a latency target is better than voting with five opinions.
    deliberation_target_s: float = 2.5
    seed: Optional[int] = 20260904
    history_size: int = 200

    def __post_init__(self) -> None:
        if self.scan_window < 2 or self.sma_window < 2:
            raise ValueError("scan_window and sma_window must be >= 2")
        if self.drift_window <= self.scan_window:
            raise ValueError(
                "drift_window must be longer than scan_window, or NORO and TIDAL "
                "are reading the same signal with opposite signs"
            )
        if self.horizon_steps < 1:
            raise ValueError("horizon_steps must be >= 1")
        if not 0.0 < self.risk_fraction <= 1.0:
            raise ValueError("risk_fraction must lie in (0, 1]")
        if not 0.0 < self.max_exposure_pct <= 100.0:
            raise ValueError("max_exposure_pct must lie in (0, 100]")
        if self.max_concurrent_inferences < 1:
            raise ValueError("max_concurrent_inferences must be >= 1")
        if self.deliberation_mode not in ("serial", "batched"):
            raise ValueError(
                f"deliberation_mode must be 'serial' or 'batched', "
                f"got {self.deliberation_mode!r}"
            )
        if self.inference_slots < 1:
            raise ValueError("inference_slots must be >= 1")
        if self.vote_max_tokens < 8:
            raise ValueError("vote_max_tokens must be >= 8; a vote needs room for its JSON")
        if self.context_per_slot < 256:
            raise ValueError("context_per_slot must be >= 256")
        if self.deliberation_mode == "batched":
            # Keep the two knobs from disagreeing: batched mode means the
            # semaphore admits exactly one request per KV slot, no more.
            self.max_concurrent_inferences = self.inference_slots


class GrokSwarm:
    """The orchestrator: eight personas, one resident model, one consensus gate."""

    def __init__(
        self,
        *,
        registry: Optional[AgentRegistry] = None,
        engine: Optional[ConsensusEngine] = None,
        market: Optional[GrokMarket] = None,
        config: Optional[SwarmConfig] = None,
        brain: Optional[Callable[..., dict[str, Any]]] = None,
        guard: Optional[RiskGuard] = None,
    ) -> None:
        self.config = config or SwarmConfig()
        self.registry = registry or AgentRegistry()
        self.engine = engine or ConsensusEngine(
            self.registry, adaptive=AdaptiveGateConfig(enabled=self.config.adaptive_gate)
        )
        if market is None:
            market = GrokMarket(
                GrokMarketConfig(seed=self.config.seed, guard=guard), authoriser=self.engine
            )
        self.market = market
        if self.market.authoriser is None:
            self.market.authoriser = self.engine
        self.guard = guard if guard is not None else self.market.config.guard
        self.brain = brain or heuristic_brain
        self._rng = random.Random(self.config.seed)

        # The one-resident-model invariant, enforced by arithmetic rather than by
        # hope. Batched mode multiplies KV caches, not models, and this is where
        # that claim is checked against the actual envelope before a single
        # request is issued.
        self.budget: MemoryBudget = get_budget(self.config.budget_name)
        self.vram_plan = None
        self.vram_plan_error: Optional[str] = None
        try:
            weights_gb, self.weights_provenance = measured_weights_gb("S")
            self.vram_plan = plan_vram(
                weights_gb=weights_gb,
                context_per_slot=self.config.context_per_slot,
                kv_type="q4_0",
                slots=self.config.inference_slots
                if self.config.deliberation_mode == "batched"
                else 1,
                budget=self.budget,
                label=f"{self.config.deliberation_mode} x{self.config.inference_slots}",
            )
            if not self.vram_plan.fits:
                self.vram_plan_error = (
                    f"{self.config.deliberation_mode} mode with "
                    f"{self.config.inference_slots} slots at "
                    f"{self.config.context_per_slot} tokens needs "
                    f"{self.vram_plan.total_gb:.2f} GB, over the {self.budget.name} "
                    f"ceiling of {self.budget.vram_ceiling_gb:.2f} GB by "
                    f"{self.vram_plan.shortfall_gb:.2f} GB"
                )
        except Exception as exc:  # noqa: BLE001 - an unplannable swarm still runs offline
            self.weights_provenance = f"unavailable: {exc}"
        if self.vram_plan_error and self.config.deliberation_mode == "batched":
            raise BudgetError(
                self.vram_plan_error
                + ". Lower context_per_slot or inference_slots, or move to a larger "
                  "budget; see core/memory_governor.py --solve."
            )
        # Stable agent -> slot mapping. llama-server picks slots by longest common
        # prefix, so an agent whose system prompt never changes keeps landing on
        # the slot that already has it cached. This mapping does not *control*
        # that - it records the intent and lets telemetry show it.
        self.slot_of: dict[str, int] = {
            name: i % max(1, self.config.inference_slots)
            for i, name in enumerate(self.registry.analyst_names())
        }
        self.deliberation_timings: list[DeliberationTiming] = []
        self.last_gate: Optional[RegimeGate] = None
        self._last_atr_pct: float = 0.06
        self._last_timing: Optional[DeliberationTiming] = None

        self.states: dict[str, AgentState] = {
            name: AgentState(
                name=name,
                tier=spec.tier.value,
                role=spec.role,
                weight=spec.weight,
                hue=spec.tentacle_hue,
            )
            for name, spec in self.registry.agents.items()
        }
        self._sem: Optional[asyncio.Semaphore] = None
        self._inflight = 0
        self.peak_concurrency = 0
        self.inference_count = 0
        self.cycles_run = 0
        self.history: list[dict[str, Any]] = []
        self.trade_log: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        self.started_at = time.time()

    # -- the single-resident gate -----------------------------------------

    def _semaphore(self) -> asyncio.Semaphore:
        if self._sem is None:
            self._sem = asyncio.Semaphore(self.config.max_concurrent_inferences)
        return self._sem

    async def _ask(self, spec: AgentSpec, prompt: str, context: dict[str, Any]) -> dict[str, Any]:
        """One agent's turn with the model. Serialised; never two at once."""
        state = self.states[spec.name]
        async with self._semaphore():
            self._inflight += 1
            self.peak_concurrency = max(self.peak_concurrency, self._inflight)
            state.status = "THINKING"
            state.last_active_at = time.time()
            t0 = time.perf_counter()
            try:
                reply = await asyncio.to_thread(self.brain, spec, prompt, context, self._rng)
                if not isinstance(reply, dict):
                    raise ValueError(f"{spec.name} brain returned {type(reply).__name__}, not a dict")
                return reply
            except Exception as exc:  # noqa: BLE001 - recorded, then handled by the caller
                state.errors += 1
                raise
            finally:
                state.total_latency_s += time.perf_counter() - t0
                state.calls += 1
                self.inference_count += 1
                self._inflight -= 1

    # -- TIDAL's scan ------------------------------------------------------

    def barrier_config(self) -> BarrierConfig:
        return BarrierConfig(
            k_tp=self.config.k_tp,
            k_sl=self.config.k_sl,
            max_holding_bars=self.config.max_holding_bars,
            break_even_at=self.config.break_even_at,
        )

    def barrier_health(self, atr_pct: float) -> dict[str, Any]:
        """Is the configured barrier placement above its own cost band?

        Surfaced rather than merely checked, because the failure mode is silent:
        a strategy whose barriers sit inside the cost band still trades, still
        shows wins, and still loses on every round trip. The number that matters
        is the break-even win rate, and it belongs on the dashboard.
        """
        from barrier_planner import evaluate  # noqa: PLC0415

        costs = CostModel(
            entry_fee_bps=self.market.config.taker_fee_bps,
            exit_fee_bps=self.market.config.taker_fee_bps,
        )
        econ = evaluate(self.barrier_config(), atr_pct=max(1e-6, atr_pct), costs=costs)
        return {
            **econ.as_dict(),
            "min_viable_k_tp_at_coin_flip": round(minimum_viable_k_tp(
                atr_pct=max(1e-6, atr_pct), k_sl=self.config.k_sl,
                cost_bps=costs.round_trip_bps), 3),
            "configured_k_tp": self.config.k_tp,
        }

    def _build_candles(self, symbol: str, look: int) -> list[Candle]:
        """Aggregate the step series into OHLCV bars ending at the current step.

        Every indicator added in this pass needs highs, lows and volume, and the
        market's price series is a sequence of single points. Grouping
        `bar_steps` points into a bar recovers the wick structure that liquidity
        sweep detection reads and the volume that CVD and VWAP weight by.

        The last bar ends at `m.step` and is complete: no partially-formed bar is
        handed to an indicator, because a live bar's high and low keep moving and
        a signal computed on one is a signal that changes its own past.
        """
        m = self.market
        bar = max(1, self.config.bar_steps)
        n_bars = max(2, look // bar)
        out: list[Candle] = []
        for b in range(n_bars):
            end = m.step - (n_bars - 1 - b) * bar
            start = end - bar + 1
            if start < 0:
                continue
            prices = [m.price_at(symbol, t) for t in range(start, end + 1)]
            try:
                vol = float(sum(m.volume_at(symbol, t) for t in range(start, end + 1)))
            except Exception:  # noqa: BLE001 - a market with no volume series still charts
                vol = 1.0
            out.append(Candle(
                timestamp=float(end),
                open=float(prices[0]),
                high=float(max(prices)),
                low=float(min(prices)),
                close=float(prices[-1]),
                volume=vol,
            ))
        return out

    def scan(self, exclude: Optional[set[str]] = None) -> Optional[dict[str, Any]]:
        """Pick the symbol moving most abnormally and describe the trade it implies.

        Momentum is measured in units of the symbol's own expected move over the
        window, so a 0.4% move in EUR-USD and a 2% move in SOL-USD are comparable
        numbers rather than one drowning out the other.
        """
        m = self.market
        w = self.config.scan_window
        # Two scan windows of history, not a full drift window: NORO's fit uses
        # whatever history exists, so waiting 120 steps before the swarm may act
        # would buy a marginally better drift estimate at the cost of a dashboard
        # that does nothing for two minutes. At the minimum, NORO's window is
        # still twice TIDAL's, which is the separation that matters.
        if m.step < max(2 * w, self.config.sma_window + 1):
            return None
        best: Optional[dict[str, Any]] = None
        for symbol, spec in m.specs.items():
            if exclude and symbol in exclude:
                continue
            now = m.price_at(symbol, m.step)
            then = m.price_at(symbol, m.step - w)
            if then <= 0:
                continue
            ret = math.log(now / then)
            expected = (spec.vol_bps / 10_000.0) * math.sqrt(w)
            sigma = ret / expected if expected > 0 else 0.0
            if best is None or abs(sigma) > abs(best["momentum_sigma"]):
                # NORO's anchor is a drift fitted over a much longer window than
                # TIDAL's, and projected over the holding horizon. Using a short
                # moving average instead - the obvious choice - would make NORO
                # the arithmetic negation of TIDAL: price above its own recent
                # mean is exactly what a momentum reading is. Two agents that
                # cannot ever agree are one agent and a minus sign.
                lw = min(self.config.drift_window, m.step)
                base = m.price_at(symbol, m.step - lw)
                drift_per_step = math.log(now / base) / lw if base > 0 and lw > 0 else 0.0
                forecast_bps = drift_per_step * self.config.horizon_steps * 10_000.0
                sma_w = min(self.config.sma_window, m.step)
                sma = statistics.fmean(
                    [m.price_at(symbol, m.step - i) for i in range(sma_w)]
                )
                best = {
                    "symbol": symbol,
                    "momentum_sigma": sigma,
                    "forecast_bps": forecast_bps,
                    "mispricing_bps": (sma - now) / now * 10_000.0,
                    "price_cents": now,
                }
        return best

    def build_context(self, scan: dict[str, Any], side: VoteStance, qty: int) -> dict[str, Any]:
        """The evidence pack all six analysts see. Same facts, different readings."""
        m = self.market
        symbol = scan["symbol"]
        spec = m.specs[symbol]
        pos_side = Side.from_stance(side)
        equity = max(1, m.equity_cents())
        try:
            q = m.quote(symbol, pos_side, qty)
            cost_bps = abs(q["slippage_bps"]) * 2 + m.config.taker_fee_bps * 2
            notional = q["gross_cents"]
        except LiquidityError:
            cost_bps = 1e4
            notional = qty * scan["price_cents"]
        edge_bps = (spec.vol_bps / 1.0) * math.sqrt(self.config.horizon_steps) * 0.8
        exposure_pct = (m.position_value_cents() + notional) / equity * 100.0

        # Real indicators, over history at or before the current step. `window`
        # deliberately ends at `m.step`: the slice is inclusive of now and cannot
        # reach further, which is what makes the no-lookahead property checkable
        # rather than merely asserted.
        look = min(self.config.drift_window, m.step)
        closes = [m.price_at(symbol, m.step - i) for i in range(look, -1, -1)]
        rsi_14 = RealAlphaEngine.calculate_rsi(closes, 14)
        candles = self._build_candles(symbol, look)
        atr_pct = (RealAlphaEngine.calculate_atr(candles, 14) / max(1.0, closes[-1])) * 100.0
        self._last_atr_pct = atr_pct
        cvd = RealAlphaEngine.calculate_volume_delta(candles, 20)
        vwap_z = RealAlphaEngine.calculate_vwap_zscore(candles, 40)
        sweep = RealAlphaEngine.detect_liquidity_sweep(candles, 20)
        book = m.book(symbol)
        spread_bps = (book.spread_cents / max(1, book.mid_cents)) * 10_000.0
        obi = RealAlphaEngine.calculate_orderbook_imbalance(
            [(float(l.price_cents), float(l.qty)) for l in book.bids],
            [(float(l.price_cents), float(l.qty)) for l in book.asks],
            depth_levels=10,
        )

        breaches: list[str] = []
        if exposure_pct > self.config.max_exposure_pct:
            breaches.append(
                f"exposure would reach {exposure_pct:.1f}% of equity, cap is "
                f"{self.config.max_exposure_pct:.1f}%"
            )
        if notional + (notional * m.config.taker_fee_bps) // 10_000 > m.cash_cents:
            breaches.append("insufficient cash; zero overdraft")
        if cost_bps >= edge_bps:
            breaches.append(f"round-trip cost {cost_bps:.1f} bps >= edge {edge_bps:.1f} bps")
        if m.halted:
            breaches.append("market halted / kill switch tripped")
        if self.guard is not None:
            hw = self.guard.check_hardware()
            if not hw.allowed:
                breaches.append(f"hardware: {hw.reason}")

        return {
            "symbol": symbol,
            "side": side,
            "qty": qty,
            "price_cents": scan["price_cents"],
            "momentum_sigma": scan["momentum_sigma"],
            "forecast_bps": scan["forecast_bps"],
            "mispricing_bps": scan["mispricing_bps"],
            "cost_bps": cost_bps,
            "edge_bps": edge_bps,
            "rsi_14": rsi_14,
            "orderbook_imbalance": obi,
            "atr_volatility_pct": atr_pct,
            "cvd_proxy": cvd,
            "vwap_zscore": vwap_z,
            "liquidity_sweep": sweep,
            "spread_bps": spread_bps,
            "atr_cents": max(1, int(round(scan["price_cents"] * atr_pct / 100.0))),
            "exposure_pct": exposure_pct,
            "exposure_cap_pct": self.config.max_exposure_pct,
            "notional_cents": notional,
            "equity_cents": equity,
            "breaches": breaches,
            "step": m.step,
            "vote_max_tokens": self.config.vote_max_tokens,
        }

    @staticmethod
    def render_prompt(context: dict[str, Any]) -> str:
        """The user turn every analyst receives. Facts only; no leading question."""
        return json.dumps(
            {
                "proposal": {
                    "symbol": context["symbol"],
                    "side": context["side"].value,
                    "qty": context["qty"],
                    "price_cents": context["price_cents"],
                    "notional_cents": context["notional_cents"],
                },
                "market": {
                    "momentum_sigma": round(context["momentum_sigma"], 3),
                    "drift_forecast_bps": round(context["forecast_bps"], 2),
                    "mispricing_bps": round(context["mispricing_bps"], 2),
                    "round_trip_cost_bps": round(context["cost_bps"], 2),
                    "expected_move_bps": round(context["edge_bps"], 2),
                },
                "portfolio": {
                    "equity_cents": context["equity_cents"],
                    "exposure_pct_if_filled": round(context["exposure_pct"], 2),
                    "exposure_cap_pct": context["exposure_cap_pct"],
                    "limit_breaches": context["breaches"],
                },
                "instruction": "Vote on this proposal. JSON only, no prose.",
            },
            ensure_ascii=False,
        )

    # -- deliberation ------------------------------------------------------

    def _vote_from_reply(self, spec: AgentSpec, reply: dict[str, Any]) -> Vote:
        """Turn one agent's JSON into a Vote, refusing to invent support.

        A reply this method cannot read becomes a HOLD at zero confidence, which
        withholds that agent's weight rather than fabricating agreement - except
        from RUNE, whose own instructions say that what cannot be verified is
        vetoed. Fail-closed means the failure mode of each agent matches its job.
        """
        raw_stance = str(reply.get("stance", "")).strip().upper()
        try:
            stance = VoteStance(raw_stance)
        except ValueError:
            if spec.veto_power:
                return Vote(spec.name, VoteStance.HOLD, 1.0,
                            rationale=f"unreadable reply {raw_stance!r}; vetoing", veto=True)
            return Vote(spec.name, VoteStance.HOLD, 0.0,
                        rationale=f"unreadable stance {raw_stance!r}; weight withheld")
        try:
            conf = float(reply.get("confidence", 0.0))
        except (TypeError, ValueError):
            conf = 0.0
        if not math.isfinite(conf):
            conf = 0.0
        conf = _clamp01(conf)
        veto = bool(reply.get("veto", False)) and spec.veto_power
        return Vote(
            agent=spec.name,
            stance=stance,
            confidence=conf,
            rationale=str(reply.get("rationale", ""))[:280],
            veto=veto,
        )

    async def _vote_for(
        self, spec: AgentSpec, prompt: str, context: dict[str, Any]
    ) -> Vote:
        """One analyst's vote, with every failure mode collapsed to a safe vote.

        The exception handler is deliberately broad. A brain can fail in any way a
        model server, a network, or a badly written adapter can fail, and every
        one of those must degrade to a withheld vote - or, for RUNE, to a veto -
        rather than propagating and leaving a half-deliberated proposal, or an
        open position, behind. The agent's error counter still records it.
        """
        try:
            reply = await self._ask(spec, prompt, context)
            vote = self._vote_from_reply(spec, reply)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            vote = (
                Vote(spec.name, VoteStance.HOLD, 1.0, rationale=f"brain failed: {exc}", veto=True)
                if spec.veto_power
                else Vote(spec.name, VoteStance.HOLD, 0.0, rationale=f"brain failed: {exc}")
            )
        state = self.states[spec.name]
        state.stance = vote.stance.value
        state.confidence = vote.confidence
        state.rationale = vote.rationale
        state.status = "VETO" if vote.veto else "VOTED"
        return vote

    async def deliberate(self, proposal: Proposal, context: dict[str, Any]) -> ConsensusResult:
        """Collect all six analyst votes and score them.

        Serial mode walks the analysts one at a time behind a semaphore of one.
        Batched mode issues all six at once against a llama-server started with
        `--parallel N`: the votes are independent by construction, decoding is
        memory-bandwidth bound, and batched decode reads the weights once per step
        for every slot - so six votes cost barely more than one. That is the whole
        reason a deliberation can come in under 2.5 s; see
        core/inference_planner.py for the arithmetic and for why this cannot be
        combined with speculative decoding.
        """
        prompt = self.render_prompt(context)
        analysts = self.registry.analysts()
        gate = self.engine.adaptive.classify(
            atr_pct=float(context.get("atr_volatility_pct", 0.0) or 0.0),
            momentum_sigma=float(context.get("momentum_sigma", 0.0) or 0.0),
            spread_bps=float(context.get("spread_bps", 0.0) or 0.0),
        )
        self.last_gate = gate
        t0 = time.perf_counter()

        if self.config.deliberation_mode == "batched":
            gathered = await asyncio.gather(
                *(self._vote_for(spec, prompt, context) for spec in analysts),
                return_exceptions=True,
            )
            votes: list[Vote] = []
            for spec, item in zip(analysts, gathered):
                if isinstance(item, Vote):
                    votes.append(item)
                    continue
                if isinstance(item, asyncio.CancelledError):
                    raise item
                # _vote_for already swallows brain faults, so reaching here means
                # the gather itself failed. Fail closed anyway.
                votes.append(
                    Vote(spec.name, VoteStance.HOLD, 1.0,
                         rationale=f"deliberation failed: {item}", veto=True)
                    if spec.veto_power
                    else Vote(spec.name, VoteStance.HOLD, 0.0,
                              rationale=f"deliberation failed: {item}")
                )
        else:
            votes = [await self._vote_for(spec, prompt, context) for spec in analysts]

        timing = DeliberationTiming(
            seconds=time.perf_counter() - t0,
            mode=self.config.deliberation_mode,
            agents=len(analysts),
            slots=self.config.inference_slots
            if self.config.deliberation_mode == "batched"
            else 1,
            peak_concurrency=self.peak_concurrency,
            target_s=self.config.deliberation_target_s,
        )
        self.deliberation_timings.append(timing)
        del self.deliberation_timings[:-200]
        self._last_timing = timing
        return self.engine.evaluate(proposal, votes, gate=gate)

    # -- one full round ----------------------------------------------------

    async def cycle(self) -> CycleReport:
        """Scan, vote, execute if authorised, advance the clock, settle."""
        self.cycles_run += 1
        m = self.market
        for s in self.states.values():
            if s.status in ("VOTED", "VETO", "ACTING"):
                s.status = "IDLE"

        # Single position per symbol + max concurrent positions across the portfolio
        open_symbols = {p.symbol for p in m.positions.values()}
        max_positions = getattr(self.config, "max_open_positions", 3)
        if len(open_symbols) >= max_positions:
            m.advance(1)
            closed = [t.as_dict() for t in m.settle()]
            return self._record(CycleReport(
                cycle=self.cycles_run, proposal=None, decision="SKIP", score_pct=0.0,
                filled=False, reject_reason=f"portfolio full: {len(open_symbols)} open positions",
                closed=closed, equity_cents=m.equity_cents(), step=m.step,
            ))

        scan = self.scan(exclude=open_symbols)
        if scan is None:
            m.advance(1)
            closed = [t.as_dict() for t in m.settle()]
            return self._record(CycleReport(
                cycle=self.cycles_run, proposal=None, decision="WARMUP", score_pct=0.0,
                filled=False, reject_reason="not enough history to scan or all candidates open",
                closed=closed, equity_cents=m.equity_cents(), step=m.step,
            ))

        symbol = scan["symbol"]

        # Trend alignment: only propose where short-term momentum agrees with the
        # longer-horizon drift estimate, and only above a conviction floor. Both
        # inputs are computed from history at or before the current step.
        #
        # This filter is the *entire* direction decision now. The branch it
        # replaced consulted the realised future move and is gone; see the note
        # in `heuristic_brain`.
        if scan["momentum_sigma"] * scan["forecast_bps"] < 0 or abs(scan["momentum_sigma"]) < 0.6:
            m.advance(1)
            closed = [t.as_dict() for t in m.settle()]
            return self._record(CycleReport(
                cycle=self.cycles_run, proposal=None, decision="SKIP", score_pct=0.0,
                filled=False, reject_reason="trend filter: momentum opposes drift or low conviction",
                closed=closed, equity_cents=m.equity_cents(), step=m.step,
            ))
        side = VoteStance.BUY if scan["momentum_sigma"] >= 0 else VoteStance.SELL
        pos_side = Side.from_stance(side)
        symbol = scan["symbol"]
        if self.config.kelly_sizing:
            from position_sizer import PositionSizer  # noqa: PLC0415
            sizer, stats = PositionSizer.from_market(
                m, lam=self.config.kelly_lam, max_fraction=self.config.risk_fraction,
                min_trades=self.config.kelly_min_trades,
                warmup_fraction=min(self.config.risk_fraction, 0.02),
                qty_scale_pow10=0,
            )
            decision = sizer.size(
                equity_cents=m.equity_cents(),
                price_cents=scan["price_cents"],
                stats=stats,
            )
            want = decision.quantity_units
            if want < 1:
                m.advance(1)
                closed = [t.as_dict() for t in m.settle()]
                return self._record(CycleReport(
                    cycle=self.cycles_run, proposal=None, decision="SKIP", score_pct=0.0,
                    filled=False, reject_reason=f"kelly sizing: {decision.reason}",
                    closed=closed, equity_cents=m.equity_cents(), step=m.step,
                ))
        else:
            want = max(1, int(m.equity_cents() * self.config.risk_fraction) // scan["price_cents"])
        qty = min(want, m.book(symbol).depth(pos_side), m.max_affordable_qty(symbol, pos_side))

        if qty < 1:
            m.advance(1)
            closed = [t.as_dict() for t in m.settle()]
            return self._record(CycleReport(
                cycle=self.cycles_run, proposal=None, decision="SKIP", score_pct=0.0,
                filled=False, reject_reason="no affordable size at current depth",
                closed=closed, equity_cents=m.equity_cents(), step=m.step,
            ))

        proposal = Proposal(
            proposal_id=f"SW-{self.cycles_run:06d}",
            symbol=symbol,
            side=side,
            qty=qty,
            price_cents=scan["price_cents"],
            meta={"momentum_sigma": round(scan["momentum_sigma"], 4)},
        )
        context = self.build_context(scan, side, qty)
        result = await self.deliberate(proposal, context)

        filled = False
        reject: Optional[str] = None
        if result.decision is ConsensusDecision.EXECUTE and result.token is not None:
            veska = self.states["VESKA"]
            veska.status = "ACTING"
            veska.last_active_at = time.time()
            veska.rationale = (
                f"routing {side.value} {qty} {symbol} on a {result.score_pct:.2f}% mandate"
            )
            try:
                m.execute(
                    proposal,
                    result.token.token,
                    stop_bps=self.config.stop_bps,
                    target_bps=self.config.target_bps,
                    horizon_steps=self.config.horizon_steps,
                    consensus_pct=result.score_pct,
                    atr_cents=(
                        max(1, int(round(scan["price_cents"]
                                         * float(context.get("atr_volatility_pct", 0.0) or 0.0)
                                         / 100.0)))
                        if self.config.triple_barrier else None
                    ),
                    barrier_config=self.barrier_config() if self.config.triple_barrier else None,
                )
                filled = True
                veska.calls += 1
            except (UnauthorisedOrder, LiquidityError, OverdraftError, MarketHalted) as exc:
                reject = f"{type(exc).__name__}: {exc}"
                veska.errors += 1
                veska.rationale = reject
        else:
            reject = result.reason

        m.advance(1)
        marin = self.states["MARIN"]
        closed_trades = m.settle()
        if closed_trades:
            marin.status = "ACTING"
            marin.calls += len(closed_trades)
            marin.last_active_at = time.time()
            marin.rationale = "; ".join(
                f"{t.symbol} {t.reason.value} {t.net_pnl_cents / 100:+,.2f}" for t in closed_trades[:3]
            )
        closed = [t.as_dict() for t in closed_trades]
        self.trade_log.extend(closed)
        del self.trade_log[:-500]

        return self._record(CycleReport(
            cycle=self.cycles_run,
            proposal=proposal.as_dict(),
            decision=result.decision.value,
            score_pct=result.score_pct,
            filled=filled,
            reject_reason=reject,
            closed=closed,
            equity_cents=m.equity_cents(),
            step=m.step,
            timing=self._last_timing,
        ))

    def _record(self, report: CycleReport) -> CycleReport:
        with self._lock:
            self.history.append(report.as_dict())
            if len(self.history) > self.config.history_size:
                del self.history[: len(self.history) - self.config.history_size]
        return report

    async def run(
        self,
        cycles: int,
        *,
        on_cycle: Optional[Callable[[CycleReport], None]] = None,
        delay_s: float = 0.0,
        stop_event: Optional[asyncio.Event] = None,
    ) -> list[CycleReport]:
        """Run `cycles` rounds. Flattens the book at the end so nothing is orphaned."""
        out: list[CycleReport] = []
        for _ in range(cycles):
            if stop_event is not None and stop_event.is_set():
                break
            if self.market.step + self.config.horizon_steps + 1 >= self.market.last_step:
                break
            report = await self.cycle()
            out.append(report)
            if on_cycle is not None:
                on_cycle(report)
            if delay_s > 0:
                await asyncio.sleep(delay_s)
        final = self.market.flatten_all(CloseReason.END_OF_RUN)
        self.trade_log.extend(t.as_dict() for t in final)
        del self.trade_log[:-500]
        return out

    # -- telemetry ---------------------------------------------------------

    def blocking_report(self) -> dict[str, Any]:
        """Why no order fired. The question the dashboard exists to answer.

        A swarm that sits at CHO with a still octopus and no explanation looks
        broken even when it is working exactly as designed - and "working as
        designed" is the common case here, because the gate refusing a
        negative-expectancy trade is the gate doing its job. So the dashboard
        gets the arithmetic: the score, the bar it was measured against, the gap
        between them, and which agents are responsible for the gap.
        """
        recent = [c for c in self.history if c.get("decision") in ("EXECUTE", "HOLD", "VETO")]
        last = recent[-1] if recent else None
        gate = self.last_gate
        bar = gate.execute_pct if gate else self.engine.execute_threshold_pct
        score = float((last or {}).get("score_pct", 0.0))

        dissent: list[dict[str, Any]] = []
        for name in self.registry.analyst_names():
            st = self.states[name]
            if st.stance is None:
                continue
            weight = self.registry.agents[name].weight
            contribution = (
                0.0 if st.stance == "HOLD"
                else weight * st.confidence * 100.0 * (1.0 if st.stance == "BUY" else -1.0)
            )
            dissent.append({
                "name": name,
                "stance": st.stance,
                "confidence": round(st.confidence, 4),
                "contribution_pct": round(contribution, 2),
                "rationale": st.rationale,
                "vetoed": st.status == "VETO",
            })
        # Whoever withheld or opposed the most is the reason the bar was missed.
        holders = sorted(dissent, key=lambda d: d["contribution_pct"])

        counts = {"EXECUTE": 0, "HOLD": 0, "VETO": 0, "SKIP": 0, "WARMUP": 0}
        for c in self.history:
            d = c.get("decision")
            if d in counts:
                counts[d] += 1

        blocked = None
        if last is None:
            blocked = "no proposal has completed a vote yet"
        elif last.get("decision") == "EXECUTE" and not last.get("filled"):
            blocked = f"consensus passed but the order was refused: {last.get('reject_reason')}"
        elif last.get("decision") != "EXECUTE":
            short = max(0.0, bar - score)
            lead = (
                f"consensus {score:.2f}% is {short:.2f} points below the "
                f"{bar:.1f}% bar"
            )
            if holders and holders[0]["contribution_pct"] <= 0:
                h = holders[0]
                lead += f"; {h['name']} is the largest drag ({h['contribution_pct']:+.2f} pts)"
            blocked = lead

        return {
            "score_pct": round(score, 2),
            "required_pct": round(bar, 2),
            "points_short": round(max(0.0, bar - score), 2),
            "progress_pct": round(min(100.0, (score / bar) * 100.0), 2) if bar else 0.0,
            "regime": gate.regime.value if gate else "UNKNOWN",
            "barrier": self.barrier_health(self._last_atr_pct),
            "regime_rationale": gate.rationale if gate else "",
            "size_multiplier": gate.size_multiplier if gate else 1.0,
            "blocked_reason": blocked,
            "decision_counts": counts,
            "agent_breakdown": dissent,
            "biggest_drag": holders[0]["name"] if holders else None,
        }

    def deliberation_report(self) -> dict[str, Any]:
        """Latency against the deliberation budget, as a distribution not a mean.

        p95 rather than the average, because a latency budget is about the tail.
        A swarm that averages 1.8 s and spikes to 6 s has not met a 2.5 s target;
        it has met it most of the time, which is a different claim.
        """
        rows = self.deliberation_timings
        if not rows:
            return {
                "mode": self.config.deliberation_mode,
                "slots": self.config.inference_slots,
                "target_s": self.config.deliberation_target_s,
                "vote_max_tokens": self.config.vote_max_tokens,
                "rounds": 0,
                "agent_slot_map": dict(self.slot_of),
            }
        secs = sorted(t.seconds for t in rows)
        idx = min(len(secs) - 1, int(round(0.95 * (len(secs) - 1))))
        met = sum(1 for t in rows if t.meets_target)
        return {
            "mode": self.config.deliberation_mode,
            "slots": rows[-1].slots,
            "target_s": self.config.deliberation_target_s,
            "vote_max_tokens": self.config.vote_max_tokens,
            "rounds": len(rows),
            "last_s": round(rows[-1].seconds, 4),
            "median_s": round(secs[len(secs) // 2], 4),
            "p95_s": round(secs[idx], 4),
            "max_s": round(secs[-1], 4),
            "met_target_pct": round(100.0 * met / len(rows), 2),
            "agent_slot_map": dict(self.slot_of),
        }

    def telemetry(self) -> dict[str, Any]:
        """One JSON blob the dashboard can render without asking a second question."""
        last = self.history[-1] if self.history else None
        snap = snapshot_hardware()
        proc_ram = read_process_tree_ram_gb()
        hw_info = snap.as_dict()
        if proc_ram > 0:
            hw_info["ram_used_gb"] = round(proc_ram, 2)
        return {
            "generated_at": time.time(),
            "uptime_s": round(time.time() - self.started_at, 2),
            "cycles_run": self.cycles_run,
            "inference_count": self.inference_count,
            "peak_concurrency": self.peak_concurrency,
            "max_concurrent_inferences": self.config.max_concurrent_inferences,
            "brain": getattr(self.brain, "__name__", type(self.brain).__name__),
            "deliberation": self.deliberation_report(),
            "agents": [self.states[n].as_dict() for n in self.registry.agents],
            "consensus": {
                **self.engine.status(),
                "last_score_pct": (last or {}).get("score_pct", 0.0),
                "last_decision": (last or {}).get("decision", "IDLE"),
                **self.blocking_report(),
            },
            "market": self.market.telemetry(),
            "hardware": {
                **hw_info,
                "budget": self.budget.name,
                "vram_ceiling_gb": self.budget.vram_ceiling_gb,
                "ram_ceiling_gb": self.budget.ram_ceiling_gb,
                "vram_plan": self.vram_plan.as_dict() if self.vram_plan else None,
                "weights_provenance": getattr(self, "weights_provenance", "unknown"),
            },
            "recent_cycles": self.history[-25:][::-1],
        }


def build_swarm(
    *,
    seed: Optional[int] = 20260904,
    guard: Optional[RiskGuard] = None,
    live: bool = False,
    n_steps: int = 12_000,
    config: Optional[SwarmConfig] = None,
    mode: Optional[str] = None,
    budget_name: Optional[str] = None,
    start_equity_cents: Optional[int] = None,
    symbols: Optional[tuple[Any, ...]] = None,
) -> GrokSwarm:
    """Wire a swarm. `live=True` uses llama-server if it answers, else says why not."""
    registry = AgentRegistry()
    engine = ConsensusEngine(registry)
    m_kwargs: dict[str, Any] = {"seed": seed, "guard": guard, "n_steps": n_steps}
    if start_equity_cents is not None:
        m_kwargs["start_equity_cents"] = start_equity_cents
    if symbols is not None:
        m_kwargs["symbols"] = symbols
    market = GrokMarket(
        GrokMarketConfig(**m_kwargs), authoriser=engine
    )
    brain = None
    if live:
        client = LocalLLMClient()
        if client.health():
            brain = llm_brain(client)
        else:
            print(
                "llama-server is not answering at "
                f"{client.base_url}; falling back to the heuristic brain.",
                file=sys.stderr,
            )
    cfg = config or SwarmConfig(seed=seed)
    if mode is not None or budget_name is not None:
        cfg = SwarmConfig(
            **{
                **{f.name: getattr(cfg, f.name) for f in fields(SwarmConfig)},
                **({"deliberation_mode": mode} if mode else {}),
                **({"budget_name": budget_name} if budget_name else {}),
            }
        )
    return GrokSwarm(
        registry=registry, engine=engine, market=market, brain=brain, guard=guard,
        config=cfg,
    )


# ---------------------------------------------------------------------------
# Self-test and CLI
# ---------------------------------------------------------------------------


def _selftest() -> int:
    passed = 0
    failed: list[str] = []

    def check(name: str, fn: Callable[[], Any]) -> None:
        nonlocal passed
        try:
            fn()
            passed += 1
        except AssertionError as exc:
            failed.append(f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}: unexpected {type(exc).__name__}: {exc}")

    def t_eight_tentacles() -> None:
        s = build_swarm(seed=1, n_steps=400)
        assert len(s.states) == 8, len(s.states)

    def t_single_resident_default() -> None:
        assert SwarmConfig().max_concurrent_inferences == 1

    def t_run_completes_and_conserves() -> None:
        s = build_swarm(seed=2, n_steps=800)
        asyncio.run(s.run(200))
        m = s.market
        assert m.cash_cents >= 0
        assert m.positions == {}
        assert m.equity_cents() == m.config.start_equity_cents + m.realised_cents()

    def t_never_two_brains_at_once() -> None:
        s = build_swarm(seed=3, n_steps=800)
        asyncio.run(s.run(200))
        assert s.inference_count > 0, "no agent ever thought; the check proves nothing"
        assert s.peak_concurrency == 1, s.peak_concurrency

    def t_every_fill_cleared_the_gate() -> None:
        s = build_swarm(seed=4, n_steps=1_200)
        asyncio.run(s.run(300))
        assert s.market.closed, "no trade was ever filled; the check proves nothing"
        assert all(t.consensus_pct >= 76.8 for t in s.market.closed)

    def t_broken_brain_vetoes_rather_than_trades() -> None:
        def broken(spec, prompt, context, rng):
            raise RuntimeError("model is down")

        s = build_swarm(seed=5, n_steps=800)
        s.brain = broken
        asyncio.run(s.run(200))
        assert s.market.closed == [] and s.market.positions == {}

    def t_telemetry_is_json_safe() -> None:
        s = build_swarm(seed=6, n_steps=800)
        asyncio.run(s.run(200))
        blob = json.dumps(s.telemetry())
        assert len(json.loads(blob)["agents"]) == 8

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    print(f"groktopus_swarm self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def _demo(cycles: int, seed: int, live: bool) -> int:
    swarm = build_swarm(seed=seed, live=live, n_steps=max(2_000, cycles * 2))
    print("=" * 78)
    print(" GROKTOPUS SWARM - 8 agents, 8 brains, 1 body")
    print(f" brain: {getattr(swarm.brain, '__name__', type(swarm.brain).__name__)}"
          f"   |   inference concurrency cap: {swarm.config.max_concurrent_inferences}")
    print("=" * 78)

    reports = asyncio.run(swarm.run(cycles))
    m = swarm.market
    decided = [r for r in reports if r.decision in ("EXECUTE", "HOLD", "VETO")]
    bands = {"EXECUTE": 0, "HOLD": 0, "VETO": 0}
    for r in decided:
        bands[r.decision] += 1

    for r in reports[-12:]:
        if r.proposal is None:
            continue
        p = r.proposal
        mark = {"EXECUTE": "FILLED " if r.filled else "REFUSED", "HOLD": "hold   ",
                "VETO": "vetoed "}.get(r.decision, "       ")
        print(f" #{r.cycle:<4} {p['side']:<4} {p['qty']:>6} {p['symbol']:<8} "
              f"{r.score_pct:6.2f}%  {mark}  equity ${r.equity_cents/100:>12,.2f}")

    print("-" * 78)
    print(f" cycles                : {len(reports)}")
    print(f" inferences (serial)   : {swarm.inference_count}   peak concurrency: {swarm.peak_concurrency}")
    print(f" EXECUTE / HOLD / VETO : {bands['EXECUTE']} / {bands['HOLD']} / {bands['VETO']}")
    print(f" round trips closed    : {len(m.closed)}   win rate {m.report()['win_rate']*100:.1f}%")
    print(f" equity                : ${m.config.start_equity_cents/100:,.2f} -> ${m.equity_cents()/100:,.2f}"
          f"  ({m.report()['return_pct']:+.2f}%)")
    print(f" ledger check          : equity == start + realised -> "
          f"{m.equity_cents() == m.config.start_equity_cents + m.realised_cents()}")
    print(f" zero overdraft        : {m.cash_cents >= 0}")
    print("=" * 78)
    for name in swarm.registry.agents:
        st = swarm.states[name]
        print(f"  {name:<6} {st.tier:<10} calls={st.calls:<5} errors={st.errors:<3} "
              f"last={st.stance or '-':<5} {st.rationale[:44]}")
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GrokTOPUS 8-agent swarm")
    ap.add_argument("--selftest", action="store_true", help="run the built-in checks")
    ap.add_argument("--demo", action="store_true", help="run a short live-pipeline demo")
    ap.add_argument("--cycles", type=int, default=120, help="cycles to run in the demo")
    ap.add_argument("--seed", type=int, default=20260904, help="RNG seed")
    ap.add_argument("--live", action="store_true",
                    help="use llama-server for the eight brains if it answers")
    ap.add_argument("--telemetry", action="store_true", help="print one telemetry blob and exit")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.telemetry:
        s = build_swarm(seed=args.seed, live=args.live, n_steps=2_000)
        asyncio.run(s.run(60))
        print(json.dumps(s.telemetry(), indent=2))
        return 0
    return _demo(args.cycles, args.seed, args.live)


if __name__ == "__main__":
    raise SystemExit(main())
