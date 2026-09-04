#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
inference_planner.py - how fast this can go, and why 300 tok/s means two things.

The directive asks for 300 tokens/second. That number is reachable or impossible
depending on which of two quantities it refers to, and the difference is not
pedantry - it decides the entire architecture.

**Single stream: impossible on this card, and not by a little.**
Decoding is memory-bandwidth bound: every token reads the whole weight tensor.
BRAIN.md section 3 anchors the model with a real measurement - 35 tok/s on
Qwen3.8-27B IQ3_XXS (10.9 GB) on this exact 5060 Ti - giving

    eta = 35.0 / (448 / 10.9) = 0.852

so `tok/s = 0.852 x 448 / weights_GB`. For 300 tok/s the weights would have to be
1.27 GB. The smallest published 27B quant is IQ1_S at 6.19 GB, which caps at
61.7 tok/s; the file on this disk is 8.39 GB, which caps at 45.5. Even at a
physically impossible eta of 1.0 the ceiling for IQ1_S is 72.4. Speculative
decoding at a generous 2.4x still lands at 148. **300 tok/s of single-stream
output is above the hardware's ceiling, not below its tuning.**

**Aggregate across the deliberation: reachable, and it is the right target.**
The six analysts are independent - that is the whole design. Batched decoding
reads the weights *once per step* and advances every slot, so N sequences cost
barely more than one. Six slots on this file model out around 195 tok/s
aggregate, and the quantity the swarm actually cares about - "all six votes
tallied in under 2.5 seconds" - follows from aggregate throughput and output
length, not from single-stream speed.

**The two levers are mutually exclusive.** See `explain_exclusivity()`: llama.cpp
requires `--parallel 1` with speculative decoding, and independently, the memory
governor shows a draft model plus six slots leaves only ~1.8k of context per slot
on a 9.5 GB budget. They are two modes to switch between, not a stack.

**The real bottleneck is prefill, and slot affinity is what fixes it.**
Six prompts of ~700 tokens is 4,200 tokens of prefill per round, which at a
plausible 1,500-3,000 tok/s is 1.4-2.8 s - the 2.5 s budget spent before a single
vote is generated. But each agent's system prompt never changes. Pin agent i to
slot i and llama-server keeps that prefix in the slot's KV cache across rounds,
so only the ~250-token evidence pack is prefilled. `DeliberationEstimate` models
cold and warm rounds separately because the difference between them is the
difference between meeting the target and missing it.

Every constant here that is not from BRAIN.md's measured table is marked
INFERRED, and `benchmarks/throughput_bench.py` exists to replace them with
measurements. Nothing in this module measures anything.

CLI:
    python core/inference_planner.py --selftest
    python core/inference_planner.py --report
    python core/inference_planner.py --target 300
    python core/inference_planner.py --argv batched
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from local_llm_client import DEFAULT_CONFIG  # noqa: E402
from memory_governor import (  # noqa: E402
    MemoryBudget,
    get_budget,
    max_context_per_slot,
    measured_weights_gb,
    plan_vram,
)

__all__ = [
    "HardwareModel",
    "ThroughputEstimate",
    "SpeculativePlan",
    "BatchPlan",
    "DeliberationEstimate",
    "TargetVerdict",
    "single_stream_tokps",
    "batched_aggregate_tokps",
    "speculative_speedup",
    "assess_target",
    "estimate_deliberation",
    "llama_server_argv",
    "explain_exclusivity",
    "DEFAULT_HARDWARE",
]

REPO_ROOT = _HERE.parent


@dataclass(frozen=True)
class HardwareModel:
    """The card, as one set of numbers with their provenance attached.

    `eta` and `bandwidth_gb_s` are MEASURED (BRAIN.md sections 1 and 3). The rest
    are INFERRED and exist to be overwritten by `benchmarks/throughput_bench.py`.
    """

    bandwidth_gb_s: float = 448.0          # MEASURED - RTX 5060 Ti spec, BRAIN.md s.1
    eta: float = 0.852                     # MEASURED - calibrated on 35 tok/s @ IQ3_XXS
    prefill_tokps: float = 2000.0          # INFERRED - compute-bound, never measured here
    batch_decay: float = 0.08              # INFERRED - per-extra-slot efficiency loss
    request_overhead_s: float = 0.015      # INFERRED - HTTP + scheduling per request

    def as_dict(self) -> dict[str, Any]:
        return {
            "bandwidth_gb_s": self.bandwidth_gb_s,
            "eta": self.eta,
            "eta_provenance": "MEASURED: 35 tok/s on Qwen3.8-27B IQ3_XXS (10.9 GB), BRAIN.md s.3",
            "prefill_tokps": self.prefill_tokps,
            "prefill_provenance": "INFERRED - measure with benchmarks/throughput_bench.py",
            "batch_decay": self.batch_decay,
            "batch_decay_provenance": "INFERRED - measure with --mode batched",
            "request_overhead_s": self.request_overhead_s,
        }


DEFAULT_HARDWARE = HardwareModel()


# ---------------------------------------------------------------------------
# Throughput
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ThroughputEstimate:
    """One throughput figure with the arithmetic that produced it."""

    tokps: float
    kind: str                    # "single" | "batched" | "speculative"
    weights_gb: float
    slots: int
    basis: str
    confidence: str = "INFERRED"

    def as_dict(self) -> dict[str, Any]:
        return {
            "tokps": round(self.tokps, 2),
            "kind": self.kind,
            "weights_gb": round(self.weights_gb, 3),
            "slots": self.slots,
            "basis": self.basis,
            "confidence": self.confidence,
        }


def single_stream_tokps(weights_gb: float, hw: HardwareModel = DEFAULT_HARDWARE) -> ThroughputEstimate:
    """Bandwidth-bound decode rate for one sequence.

    One token requires reading every weight once, so the rate is bandwidth over
    bytes, scaled by the efficiency the card actually achieves.
    """
    if weights_gb <= 0:
        raise ValueError("weights_gb must be > 0")
    rate = hw.eta * hw.bandwidth_gb_s / weights_gb
    return ThroughputEstimate(
        tokps=rate,
        kind="single",
        weights_gb=weights_gb,
        slots=1,
        basis=f"{hw.eta} x {hw.bandwidth_gb_s} GB/s / {weights_gb:.2f} GB",
        confidence="INFERRED from a MEASURED anchor",
    )


def batch_efficiency(slots: int, hw: HardwareModel = DEFAULT_HARDWARE) -> float:
    """Per-slot efficiency at N slots. 1.0 at one slot, decaying from there.

    Batched decode reads the weights once and advances every slot, so aggregate
    throughput would be N x single if attention were free. It is not: attention
    is per-sequence work that grows with N, and the scheduler has its own cost.
    `batch_decay` is a single INFERRED knob standing in for all of that, and
    `--mode batched` in the benchmark is how it gets replaced with a measurement.
    """
    if slots < 1:
        raise ValueError("slots must be >= 1")
    return 1.0 / (1.0 + hw.batch_decay * (slots - 1))


def batched_aggregate_tokps(
    weights_gb: float, slots: int, hw: HardwareModel = DEFAULT_HARDWARE
) -> ThroughputEstimate:
    """Total tokens per second across all slots."""
    single = single_stream_tokps(weights_gb, hw).tokps
    eff = batch_efficiency(slots, hw)
    return ThroughputEstimate(
        tokps=single * slots * eff,
        kind="batched",
        weights_gb=weights_gb,
        slots=slots,
        basis=f"{single:.1f} tok/s x {slots} slots x {eff:.3f} efficiency",
    )


# ---------------------------------------------------------------------------
# Speculative decoding
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SpeculativePlan:
    """Draft-then-verify, costed.

    The expected number of tokens produced per verification pass, for draft
    length `gamma` and per-token acceptance `alpha`, is the standard geometric
    sum from Leviathan et al. (2023):

        E[tokens] = (1 - alpha^(gamma+1)) / (1 - alpha)

    One iteration costs `gamma` draft passes plus one target pass. Both are
    bandwidth bound, so the draft pass costs roughly `draft_gb / target_gb` of a
    target pass. Speedup is E[tokens] divided by that cost.

    `alpha` is the parameter nobody knows for this pairing, and it dominates:
    at 0.8 the speedup is nearly 3x, at 0.4 it is 1.5x, and below roughly 0.25
    the drafting costs more than it saves. BRAIN.md QD-3 records that MTP - self
    speculation - makes 2-3 bit quants *slower*, which is what a low acceptance
    rate looks like from the outside. Measure before believing.
    """

    alpha: float
    gamma: int
    draft_gb: float
    target_gb: float
    hw: HardwareModel = DEFAULT_HARDWARE

    def __post_init__(self) -> None:
        if not 0.0 <= self.alpha < 1.0:
            raise ValueError("alpha must lie in [0, 1)")
        if self.gamma < 1:
            raise ValueError("gamma must be >= 1")
        if self.draft_gb <= 0 or self.target_gb <= 0:
            raise ValueError("both model sizes must be > 0")

    @property
    def cost_ratio(self) -> float:
        """Draft forward pass as a fraction of a target forward pass."""
        return self.draft_gb / self.target_gb

    @property
    def expected_tokens_per_iteration(self) -> float:
        if self.alpha == 0.0:
            return 1.0
        return (1.0 - self.alpha ** (self.gamma + 1)) / (1.0 - self.alpha)

    @property
    def iteration_cost(self) -> float:
        """In units of one target forward pass."""
        return 1.0 + self.gamma * self.cost_ratio

    @property
    def speedup(self) -> float:
        return self.expected_tokens_per_iteration / self.iteration_cost

    @property
    def helps(self) -> bool:
        return self.speedup > 1.0

    def tokps(self) -> ThroughputEstimate:
        base = single_stream_tokps(self.target_gb, self.hw).tokps
        return ThroughputEstimate(
            tokps=base * self.speedup,
            kind="speculative",
            weights_gb=self.target_gb,
            slots=1,
            basis=(
                f"{base:.1f} tok/s x {self.speedup:.2f} "
                f"(alpha={self.alpha}, gamma={self.gamma}, draft cost {self.cost_ratio:.3f})"
            ),
        )

    def break_even_alpha(self, tolerance: float = 1e-4) -> float:
        """The acceptance rate below which drafting is a net loss."""
        lo, hi = 0.0, 0.999
        while hi - lo > tolerance:
            mid = (lo + hi) / 2
            if SpeculativePlan(mid, self.gamma, self.draft_gb, self.target_gb, self.hw).speedup >= 1.0:
                hi = mid
            else:
                lo = mid
        return round(hi, 4)

    def as_dict(self) -> dict[str, Any]:
        return {
            "alpha": self.alpha,
            "gamma": self.gamma,
            "draft_gb": self.draft_gb,
            "target_gb": self.target_gb,
            "cost_ratio": round(self.cost_ratio, 4),
            "expected_tokens_per_iteration": round(self.expected_tokens_per_iteration, 4),
            "iteration_cost": round(self.iteration_cost, 4),
            "speedup": round(self.speedup, 4),
            "helps": self.helps,
            "break_even_alpha": self.break_even_alpha(),
            "tokps": self.tokps().as_dict(),
            "alpha_provenance": "UNMEASURED for this model pairing - the dominant unknown",
        }


def speculative_speedup(alpha: float, gamma: int, draft_gb: float, target_gb: float) -> float:
    return SpeculativePlan(alpha, gamma, draft_gb, target_gb).speedup


# ---------------------------------------------------------------------------
# Deliberation latency - the number the swarm actually cares about
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class BatchPlan:
    """A batched-deliberation configuration and what it costs in memory."""

    slots: int
    context_per_slot: int
    weights_gb: float
    budget: MemoryBudget
    hw: HardwareModel = DEFAULT_HARDWARE
    kv_type: str = "q4_0"
    kib_per_token_f16: float = 64.0

    @property
    def vram(self):
        return plan_vram(
            weights_gb=self.weights_gb,
            context_per_slot=self.context_per_slot,
            kv_type=self.kv_type,
            slots=self.slots,
            kib_per_token_f16=self.kib_per_token_f16,
            budget=self.budget,
            label=f"batched x{self.slots} @ {self.context_per_slot}",
        )

    @property
    def aggregate(self) -> ThroughputEstimate:
        return batched_aggregate_tokps(self.weights_gb, self.slots, self.hw)

    def as_dict(self) -> dict[str, Any]:
        return {
            "slots": self.slots,
            "context_per_slot": self.context_per_slot,
            "vram": self.vram.as_dict(),
            "aggregate": self.aggregate.as_dict(),
        }


@dataclass(frozen=True)
class DeliberationEstimate:
    """How long six analysts take to vote, split into where the time goes."""

    mode: str
    agents: int
    output_tokens_each: int
    prefill_tokens: int
    prefill_s: float
    decode_s: float
    overhead_s: float
    target_s: float

    @property
    def total_s(self) -> float:
        return round(self.prefill_s + self.decode_s + self.overhead_s, 4)

    @property
    def meets_target(self) -> bool:
        return self.total_s <= self.target_s

    @property
    def dominant(self) -> str:
        parts = {"prefill": self.prefill_s, "decode": self.decode_s, "overhead": self.overhead_s}
        return max(parts, key=lambda k: parts[k])

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "agents": self.agents,
            "output_tokens_each": self.output_tokens_each,
            "prefill_tokens": self.prefill_tokens,
            "prefill_s": round(self.prefill_s, 4),
            "decode_s": round(self.decode_s, 4),
            "overhead_s": round(self.overhead_s, 4),
            "total_s": self.total_s,
            "target_s": self.target_s,
            "meets_target": self.meets_target,
            "dominant_cost": self.dominant,
        }


def estimate_deliberation(
    *,
    weights_gb: float,
    mode: str = "batched",
    agents: int = 6,
    output_tokens_each: int = 48,
    system_prompt_tokens: int = 190,
    evidence_tokens: int = 260,
    warm_slots: bool = True,
    slots: Optional[int] = None,
    speculative: Optional[SpeculativePlan] = None,
    hw: HardwareModel = DEFAULT_HARDWARE,
    target_s: float = 2.5,
) -> DeliberationEstimate:
    """Time one full six-analyst round.

    `warm_slots` is the slot-affinity optimisation and it is worth more than any
    decoding trick here. An agent's system prompt is identical every round; pin
    agent i to slot i and llama-server still holds that prefix in the slot's KV
    cache, so a warm round prefills only the evidence pack. Cold, the same round
    prefills the system prompt as well - six times over - and on this card that
    difference is roughly a second.
    """
    if agents < 1:
        raise ValueError("agents must be >= 1")
    if output_tokens_each < 1:
        raise ValueError("output_tokens_each must be >= 1")
    n_slots = slots if slots is not None else (agents if mode == "batched" else 1)

    per_agent_prefill = evidence_tokens if warm_slots else system_prompt_tokens + evidence_tokens
    prefill_tokens = per_agent_prefill * agents
    prefill_s = prefill_tokens / hw.prefill_tokps

    total_out = output_tokens_each * agents
    if mode == "batched":
        rate = batched_aggregate_tokps(weights_gb, n_slots, hw).tokps
    elif mode == "speculative":
        if speculative is None:
            raise ValueError("mode 'speculative' needs a SpeculativePlan")
        rate = speculative.tokps().tokps
    elif mode == "serial":
        rate = single_stream_tokps(weights_gb, hw).tokps
    else:
        raise ValueError(f"unknown mode {mode!r}; use serial, batched or speculative")
    decode_s = total_out / rate

    # Batched requests overlap, so their per-request overhead is paid once.
    overhead_s = hw.request_overhead_s * (1 if mode == "batched" else agents)

    return DeliberationEstimate(
        mode=mode,
        agents=agents,
        output_tokens_each=output_tokens_each,
        prefill_tokens=prefill_tokens,
        prefill_s=prefill_s,
        decode_s=decode_s,
        overhead_s=overhead_s,
        target_s=target_s,
    )


# ---------------------------------------------------------------------------
# Verdicts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TargetVerdict:
    """Whether a tok/s target is reachable, and the arithmetic either way."""

    target_tokps: float
    kind: str
    achievable_tokps: float
    weights_gb: float
    reachable: bool
    reason: str
    required_weights_gb: Optional[float] = None
    hard_ceiling_tokps: Optional[float] = None

    def as_dict(self) -> dict[str, Any]:
        return {
            "target_tokps": self.target_tokps,
            "kind": self.kind,
            "achievable_tokps": round(self.achievable_tokps, 2),
            "weights_gb": round(self.weights_gb, 3),
            "reachable": self.reachable,
            "reason": self.reason,
            "required_weights_gb": (
                round(self.required_weights_gb, 3) if self.required_weights_gb else None
            ),
            "hard_ceiling_tokps": (
                round(self.hard_ceiling_tokps, 2) if self.hard_ceiling_tokps else None
            ),
        }


def assess_target(
    target_tokps: float,
    *,
    weights_gb: float,
    kind: str = "single",
    slots: int = 6,
    speculative: Optional[SpeculativePlan] = None,
    hw: HardwareModel = DEFAULT_HARDWARE,
) -> TargetVerdict:
    """Is `target_tokps` reachable for this file, in this mode?

    The `hard_ceiling` figure is the honest bound: bandwidth divided by weights at
    a physically impossible efficiency of 1.0. When a target is above *that*, no
    amount of tuning, quantisation of the KV cache, or driver version will reach
    it, and saying so is more useful than promising to try.
    """
    if kind == "single":
        got = single_stream_tokps(weights_gb, hw).tokps
    elif kind == "batched":
        got = batched_aggregate_tokps(weights_gb, slots, hw).tokps
    elif kind == "speculative":
        if speculative is None:
            raise ValueError("kind 'speculative' needs a SpeculativePlan")
        got = speculative.tokps().tokps
    else:
        raise ValueError(f"unknown kind {kind!r}")

    hard = hw.bandwidth_gb_s / weights_gb  # eta = 1.0, physically unattainable
    if kind == "batched":
        hard *= slots

    if got >= target_tokps:
        return TargetVerdict(
            target_tokps, kind, got, weights_gb, True,
            f"{got:.1f} tok/s modelled against a {target_tokps:.0f} tok/s target",
            hard_ceiling_tokps=hard,
        )

    required = hw.eta * hw.bandwidth_gb_s / target_tokps
    if kind == "batched":
        required = hw.eta * hw.bandwidth_gb_s * slots * batch_efficiency(slots, hw) / target_tokps

    if target_tokps > hard:
        reason = (
            f"{target_tokps:.0f} tok/s is above the card's physical ceiling of "
            f"{hard:.0f} tok/s for {weights_gb:.2f} GB of weights (bandwidth / bytes at "
            f"100% efficiency). Not a tuning gap - no configuration reaches it."
        )
    else:
        reason = (
            f"modelled {got:.1f} tok/s against a {target_tokps:.0f} tok/s target. "
            f"Reaching it needs weights <= {required:.2f} GB; the smallest published "
            f"Qwen3.8-27B quant is IQ1_S at 6.19 GB."
        )
    return TargetVerdict(
        target_tokps, kind, got, weights_gb, False, reason,
        required_weights_gb=required, hard_ceiling_tokps=hard,
    )


def explain_exclusivity(
    *, weights_gb: float, budget: MemoryBudget, draft_gb: float = 0.35, slots: int = 6
) -> dict[str, Any]:
    """Why batching and speculative decoding are two modes, not one stack.

    Two independent reasons, either of which would be sufficient:

    1. **llama.cpp requires `--parallel 1` with speculative decoding.** BRAIN.md
       QD-3 records the flag set; the batching this plan depends on is exactly
       `--parallel N`. The two flags contradict each other.
    2. **Memory.** A draft model costs its weights and its own KV cache, out of
       the same ceiling. The numbers below are the governor's, not a guess.
    """
    ctx_batched = max_context_per_slot(
        weights_gb=weights_gb, slots=slots, budget=budget)
    ctx_both = max_context_per_slot(
        weights_gb=weights_gb, slots=slots, budget=budget,
        draft_weights_gb=draft_gb, draft_kib_per_token_f16=12.0)
    ctx_spec = max_context_per_slot(
        weights_gb=weights_gb, slots=1, budget=budget,
        draft_weights_gb=draft_gb, draft_kib_per_token_f16=12.0)
    return {
        "exclusive": True,
        "flag_conflict": (
            "llama.cpp requires --parallel 1 when speculative decoding is on "
            "(BRAIN.md QD-3); batched deliberation is --parallel N. The flags "
            "contradict each other, so this is not a memory problem that a bigger "
            "card would solve."
        ),
        "memory": {
            "batched_only_max_ctx_per_slot": ctx_batched,
            "speculative_only_max_ctx": ctx_spec,
            "both_max_ctx_per_slot": ctx_both,
            "verdict": (
                f"Both at once leaves {ctx_both:,} tokens per slot against "
                f"{ctx_batched:,} for batching alone - the draft model's weights and "
                f"KV come out of the same {budget.vram_ceiling_gb:.1f} GB."
            ),
        },
        "recommendation": (
            "Run batched for deliberation, where six independent votes make "
            "batching nearly free. Keep speculative decoding for single-stream "
            "work (one long generation, no fan-out) and switch modes by restarting "
            "llama-server - which is a config change, not a code change."
        ),
    }


# ---------------------------------------------------------------------------
# Server command lines
# ---------------------------------------------------------------------------


def llama_server_argv(
    *,
    model_path: str,
    mode: str = "batched",
    slots: int = 6,
    context_per_slot: int = 4096,
    kv_type: str = "q4_0",
    n_gpu_layers: int = 99,
    threads: int = 8,
    host: str = "127.0.0.1",
    port: int = 8080,
    flash_attn: bool = True,
    draft_model_path: Optional[str] = None,
    draft_n_max: int = 4,
    use_mtp: bool = False,
    no_mmap: bool = False,
) -> list[str]:
    """The exact argv for a mode. `--ctx-size` is the TOTAL across slots.

    llama-server divides `--ctx-size` by `--parallel`, so a six-slot server that
    wants 4096 tokens each is started with 24576, not 4096. Getting this backwards
    gives every agent 682 tokens of context and a stream of truncation errors that
    look like a prompt bug.
    """
    if mode not in ("serial", "batched", "speculative"):
        raise ValueError(f"unknown mode {mode!r}")
    if mode == "speculative" and slots != 1:
        raise ValueError(
            "speculative decoding requires --parallel 1; see explain_exclusivity()"
        )
    n_slots = slots if mode == "batched" else 1
    total_ctx = context_per_slot * n_slots

    argv = [
        "llama-server",
        "-m", model_path,
        "--ctx-size", str(total_ctx),
        "--parallel", str(n_slots),
        "--cache-type-k", kv_type,
        "--cache-type-v", kv_type,
        "--n-gpu-layers", str(n_gpu_layers),
        "--threads", str(threads),
        "--host", host,
        "--port", str(port),
    ]
    if flash_attn:
        argv.append("--flash-attn")
    if no_mmap:
        # Only reachable when a caller passes it explicitly; memory_governor's
        # assert_mmap_allowed is what refuses it under a tight host budget.
        argv.append("--no-mmap")
    if mode == "speculative":
        if use_mtp:
            argv += ["--spec-type", "draft-mtp", "--spec-draft-n-max", str(draft_n_max), "-ngld", "99"]
        elif draft_model_path:
            argv += ["--model-draft", draft_model_path, "--draft-max", str(draft_n_max), "-ngld", "99"]
        else:
            raise ValueError("speculative mode needs draft_model_path or use_mtp=True")
    if mode == "batched":
        argv += ["--cont-batching"]
    return argv


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def render_report(
    *,
    budget_name: str = "TIGHT",
    profile_key: str = "S",
    slots: int = 6,
    target_tokps: float = 300.0,
    target_s: float = 2.5,
    hw: HardwareModel = DEFAULT_HARDWARE,
    config_path: Path | str = DEFAULT_CONFIG,
) -> str:
    budget = get_budget(budget_name, config_path)
    weights, provenance = measured_weights_gb(profile_key, config_path)
    raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    draft = (raw.get("draft_models") or {}).get("qwen2.5-0.5b-q4km") or {}
    draft_gb = float(draft.get("weights_gb_claimed") or 0.35)

    single = single_stream_tokps(weights, hw)
    batched = batched_aggregate_tokps(weights, slots, hw)
    ctx_batched = max_context_per_slot(weights_gb=weights, slots=slots, budget=budget)

    lines: list[str] = []
    add = lines.append
    add("=" * 78)
    add(f" INFERENCE PLANNER - profile {profile_key}, budget {budget_name}, {slots} slots")
    add("=" * 78)
    add(f" weights          : {weights:.2f} GB   [{provenance}]")
    add(f" bandwidth x eta  : {hw.bandwidth_gb_s:.0f} GB/s x {hw.eta}"
        f"   [MEASURED: 35 tok/s @ IQ3_XXS 10.9 GB]")
    add("-" * 78)
    add(" Decode rate")
    add(f"   single stream  : {single.tokps:6.1f} tok/s   ({single.basis})")
    add(f"   batched x{slots}     : {batched.tokps:6.1f} tok/s aggregate   ({batched.basis})")
    add(f"                    {batched.tokps / slots:6.1f} tok/s per agent")
    add(f"   physical cap   : {hw.bandwidth_gb_s / weights:6.1f} tok/s single stream at eta=1.0 "
        f"(unattainable)")
    add("-" * 78)

    add(f" Target: {target_tokps:.0f} tok/s")
    for kind in ("single", "batched"):
        v = assess_target(target_tokps, weights_gb=weights, kind=kind, slots=slots, hw=hw)
        mark = "REACHABLE  " if v.reachable else "UNREACHABLE"
        add(f"   {kind:<9} {mark} {v.achievable_tokps:6.1f} tok/s")
        for chunk in _wrap(v.reason, 68):
            add(f"      {chunk}")
    add("-" * 78)

    add(" Speculative decoding - acceptance is the whole question")
    spec_rows = []
    for alpha in (0.3, 0.5, 0.7, 0.85):
        p = SpeculativePlan(alpha, 4, draft_gb, weights, hw)
        spec_rows.append((alpha, p))
        add(f"   alpha={alpha:<5} gamma=4  E[tok/iter]={p.expected_tokens_per_iteration:5.2f}  "
            f"speedup {p.speedup:4.2f}x  ->{p.tokps().tokps:6.1f} tok/s")
    be = spec_rows[0][1].break_even_alpha()
    add(f"   break-even acceptance: {be:.3f}  (below this, drafting costs more than it saves)")
    add(f"   NOTE: alpha is UNMEASURED for this pairing. BRAIN.md QD-3 records that MTP")
    add(f"         makes 2-3 bit quants SLOWER, which is what low acceptance looks like.")
    add("-" * 78)

    add(f" Deliberation - 6 analysts, target {target_s:.1f} s")
    for mode, warm, label in (
        ("serial", True, "serial, warm slots"),
        ("batched", False, "batched, cold slots"),
        ("batched", True, "batched, warm slots"),
    ):
        est = estimate_deliberation(
            weights_gb=weights, mode=mode, warm_slots=warm, slots=slots,
            hw=hw, target_s=target_s,
        )
        mark = "MEETS  " if est.meets_target else "MISSES "
        add(f"   {label:<22} {mark} {est.total_s:5.2f} s   "
            f"(prefill {est.prefill_s:.2f} + decode {est.decode_s:.2f} + "
            f"overhead {est.overhead_s:.2f}; {est.dominant} dominates)")
    add("-" * 78)

    ex = explain_exclusivity(weights_gb=weights, budget=budget, draft_gb=draft_gb, slots=slots)
    add(" Batching and speculative decoding cannot be combined")
    for chunk in _wrap(ex["flag_conflict"], 72):
        add(f"   {chunk}")
    m = ex["memory"]
    add(f"   batched alone : {m['batched_only_max_ctx_per_slot']:,} tok/slot")
    add(f"   spec alone    : {m['speculative_only_max_ctx']:,} tok")
    add(f"   both          : {m['both_max_ctx_per_slot']:,} tok/slot")
    add("-" * 78)
    add(f" Recommended batched configuration: {slots} slots x {min(4096, ctx_batched):,} tokens")
    argv = llama_server_argv(
        model_path=str((raw.get("profiles") or {}).get(profile_key, {}).get("explicit_path")
                       or "D:/models/Qwen3.8-27B-UD-IQ2_XXS.gguf"),
        mode="batched", slots=slots, context_per_slot=min(4096, ctx_batched),
    )
    add("   " + " ".join(argv))
    add("=" * 78)
    add(" Every figure above is MODELLED. Replace them with measurements:")
    add("   python benchmarks/throughput_bench.py --mode serial --mode batched --slots 6")
    add("=" * 78)
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    words, out, cur = text.split(), [], ""
    for w in words:
        if len(cur) + len(w) + 1 > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out


# ---------------------------------------------------------------------------
# Self-test
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

    W = 8.39
    tight = get_budget("TIGHT")

    def t_eta_reproduces_the_measured_anchor() -> None:
        # The whole model hangs off this: 35 tok/s measured at IQ3_XXS, 10.9 GB.
        got = single_stream_tokps(10.9).tokps
        assert abs(got - 35.0) < 0.15, got

    def t_single_stream_falls_with_size() -> None:
        assert single_stream_tokps(6.19).tokps > single_stream_tokps(8.39).tokps

    def t_batched_beats_single_but_not_linearly() -> None:
        s = single_stream_tokps(W).tokps
        b = batched_aggregate_tokps(W, 6).tokps
        assert s < b < 6 * s, (s, b)

    def t_batch_efficiency_is_one_at_one_slot() -> None:
        assert batch_efficiency(1) == 1.0

    def t_300_single_is_unreachable() -> None:
        v = assess_target(300, weights_gb=W, kind="single")
        assert not v.reachable
        assert v.required_weights_gb is not None and v.required_weights_gb < 6.19

    def t_300_single_is_above_the_physical_ceiling() -> None:
        v = assess_target(300, weights_gb=W, kind="single")
        assert v.hard_ceiling_tokps is not None and v.hard_ceiling_tokps < 300
        assert "physical ceiling" in v.reason

    def t_smallest_quant_still_cannot_do_300() -> None:
        v = assess_target(300, weights_gb=6.19, kind="single")
        assert not v.reachable, v.reason

    def t_speculative_alone_cannot_do_300() -> None:
        p = SpeculativePlan(0.85, 4, 0.35, 6.19)
        assert p.tokps().tokps < 300, p.tokps().tokps

    def t_speculative_math_matches_the_closed_form() -> None:
        p = SpeculativePlan(0.5, 3, 0.35, 8.39)
        assert abs(p.expected_tokens_per_iteration - 1.875) < 1e-9

    def t_alpha_zero_is_one_token_per_iteration() -> None:
        assert SpeculativePlan(0.0, 5, 0.35, 8.39).expected_tokens_per_iteration == 1.0

    def t_low_acceptance_is_a_net_loss() -> None:
        assert not SpeculativePlan(0.05, 8, 0.35, 8.39).helps

    def t_break_even_alpha_is_a_real_boundary() -> None:
        p = SpeculativePlan(0.5, 4, 0.35, 8.39)
        be = p.break_even_alpha()
        assert SpeculativePlan(be + 0.02, 4, 0.35, 8.39).speedup > 1.0
        assert SpeculativePlan(max(0.0, be - 0.02), 4, 0.35, 8.39).speedup < 1.0

    def t_warm_slots_beat_cold() -> None:
        cold = estimate_deliberation(weights_gb=W, mode="batched", warm_slots=False)
        warm = estimate_deliberation(weights_gb=W, mode="batched", warm_slots=True)
        assert warm.total_s < cold.total_s

    def t_batched_beats_serial_on_latency() -> None:
        ser = estimate_deliberation(weights_gb=W, mode="serial")
        bat = estimate_deliberation(weights_gb=W, mode="batched")
        assert bat.total_s < ser.total_s

    def t_serial_misses_the_25s_target() -> None:
        assert not estimate_deliberation(weights_gb=W, mode="serial").meets_target

    def t_argv_total_context_is_per_slot_times_slots() -> None:
        argv = llama_server_argv(model_path="m.gguf", mode="batched", slots=6, context_per_slot=4096)
        assert argv[argv.index("--ctx-size") + 1] == str(6 * 4096)
        assert argv[argv.index("--parallel") + 1] == "6"

    def t_argv_refuses_batched_speculative() -> None:
        try:
            llama_server_argv(model_path="m.gguf", mode="speculative", slots=6, use_mtp=True)
        except ValueError:
            return
        raise AssertionError("speculative mode accepted --parallel 6")

    def t_argv_speculative_needs_a_draft() -> None:
        try:
            llama_server_argv(model_path="m.gguf", mode="speculative", slots=1)
        except ValueError:
            return
        raise AssertionError("speculative mode accepted no draft source")

    def t_exclusivity_is_reported_with_numbers() -> None:
        ex = explain_exclusivity(weights_gb=W, budget=tight)
        assert ex["exclusive"] is True
        assert ex["memory"]["both_max_ctx_per_slot"] < ex["memory"]["batched_only_max_ctx_per_slot"]

    def t_report_renders() -> None:
        text = render_report()
        assert "UNREACHABLE" in text and "batched" in text

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    print(f"inference_planner self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GrokTOPUS inference planner")
    ap.add_argument("--budget", default="TIGHT")
    ap.add_argument("--profile", default="S")
    ap.add_argument("--slots", type=int, default=6)
    ap.add_argument("--target", type=float, default=300.0, help="tok/s target to assess")
    ap.add_argument("--target-seconds", type=float, default=2.5, help="deliberation budget")
    ap.add_argument("--argv", choices=["serial", "batched", "speculative"],
                    help="print the llama-server command line for a mode and exit")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    weights, provenance = measured_weights_gb(args.profile)
    budget = get_budget(args.budget)
    raw = json.loads(Path(DEFAULT_CONFIG).read_text(encoding="utf-8"))
    model_path = str((raw.get("profiles") or {}).get(args.profile, {}).get("explicit_path")
                     or "D:/models/Qwen3.8-27B-UD-IQ2_XXS.gguf")
    draft = (raw.get("draft_models") or {}).get("qwen2.5-0.5b-q4km") or {}

    if args.argv:
        ctx = max_context_per_slot(
            weights_gb=weights,
            slots=args.slots if args.argv == "batched" else 1,
            budget=budget,
            draft_weights_gb=draft.get("weights_gb_claimed", 0.35) if args.argv == "speculative" else 0.0,
            draft_kib_per_token_f16=12.0,
        )
        print(" ".join(llama_server_argv(
            model_path=model_path,
            mode=args.argv,
            slots=args.slots if args.argv == "batched" else 1,
            context_per_slot=min(4096, ctx) if ctx else 512,
            draft_model_path=str(draft.get("explicit_path")) if args.argv == "speculative" else None,
        )))
        return 0

    if args.json:
        print(json.dumps({
            "hardware": DEFAULT_HARDWARE.as_dict(),
            "weights_gb": weights,
            "weights_provenance": provenance,
            "single": single_stream_tokps(weights).as_dict(),
            "batched": batched_aggregate_tokps(weights, args.slots).as_dict(),
            "target_single": assess_target(args.target, weights_gb=weights, kind="single").as_dict(),
            "target_batched": assess_target(
                args.target, weights_gb=weights, kind="batched", slots=args.slots).as_dict(),
            "deliberation_batched_warm": estimate_deliberation(
                weights_gb=weights, mode="batched", slots=args.slots,
                target_s=args.target_seconds).as_dict(),
            "exclusivity": explain_exclusivity(weights_gb=weights, budget=budget),
        }, indent=2))
        return 0

    print(render_report(
        budget_name=args.budget, profile_key=args.profile, slots=args.slots,
        target_tokps=args.target, target_s=args.target_seconds,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
