#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_perf_planning.py - the memory and throughput arithmetic.

    pytest tests/test_perf_planning.py -v

These modules exist to say "no" accurately, so most of what is tested here is the
refusals: that ULTRA is reported unreachable by the right number of gigabytes,
that 300 tok/s single-stream is identified as above the card's physical ceiling
rather than merely unmet, that `--no-mmap` is blocked under a 4 GB host budget,
and that a benchmark with no server to talk to produces no number at all.

The one test that anchors everything else is
`test_eta_reproduces_the_measured_anchor`: BRAIN.md section 3 records 35 tok/s
for Qwen3.8-27B IQ3_XXS on this exact card, and the throughput model must
reproduce it to within rounding. If that test fails, every other number this
codebase prints about speed is wrong, and it is the only one derived from a real
measurement rather than from a chain of assumptions.
"""

from __future__ import annotations

import json
import math
import sqlite3
from pathlib import Path

import pytest

from inference_planner import (
    DEFAULT_HARDWARE,
    HardwareModel,
    SpeculativePlan,
    assess_target,
    batch_efficiency,
    batched_aggregate_tokps,
    estimate_deliberation,
    explain_exclusivity,
    llama_server_argv,
    render_report,
    single_stream_tokps,
)
from memory_governor import (
    FALLBACK_BUDGETS,
    BudgetError,
    MemoryBudget,
    MmapRefused,
    RamGovernor,
    assert_mmap_allowed,
    feasible_plans,
    get_budget,
    load_budgets,
    max_context_per_slot,
    max_slots,
    measured_weights_gb,
    plan_vram,
)
from throughput_bench import (
    BenchError,
    ServerUnavailable,
    ThroughputBench,
    _fake_transport,
)

#: The file as reported on this machine. BRAIN.md's published table says IQ2_XXS
#: is 7.27 GB; profiles.json records that what is actually on this disk is
#: 8.39 GB, which is IQ2_S-sized. Planning uses the measured number.
WEIGHTS = 8.39


# ---------------------------------------------------------------------------
# Budgets
# ---------------------------------------------------------------------------


class TestBudgets:
    def test_three_named_envelopes(self) -> None:
        assert {"STANDARD", "TIGHT", "ULTRA"} <= set(load_budgets())

    @pytest.mark.parametrize(
        "name,vram,ram", [("STANDARD", 10.0, 6.0), ("TIGHT", 9.5, 4.0), ("ULTRA", 7.8, 4.0)]
    )
    def test_ceilings(self, name: str, vram: float, ram: float) -> None:
        b = get_budget(name)
        assert (b.vram_ceiling_gb, b.ram_ceiling_gb) == (vram, ram)

    def test_fallbacks_match_the_config(self) -> None:
        for name, (vram, ram) in FALLBACK_BUDGETS.items():
            b = get_budget(name)
            assert (b.vram_ceiling_gb, b.ram_ceiling_gb) == (vram, ram), name

    def test_unknown_budget_raises(self) -> None:
        with pytest.raises(BudgetError, match="unknown budget"):
            get_budget("HUGE")

    def test_negative_ceiling_refused(self) -> None:
        with pytest.raises(BudgetError):
            MemoryBudget("bad", -1.0, 4.0)

    def test_weights_provenance_is_stated(self) -> None:
        gb, prov = measured_weights_gb("S")
        assert gb > 0
        assert "measured" in prov.lower() or "NOT measured" in prov


# ---------------------------------------------------------------------------
# VRAM
# ---------------------------------------------------------------------------


class TestVramPlans:
    def test_ultra_is_unreachable_by_over_a_gigabyte(self) -> None:
        p = plan_vram(weights_gb=WEIGHTS, context_per_slot=256, budget=get_budget("ULTRA"))
        assert not p.fits
        assert p.shortfall_gb == pytest.approx(1.14, abs=0.02)

    def test_ultra_would_fit_iq1_m(self) -> None:
        # 6.73 GB is the published IQ1_M size. The gap is the file, not the maths.
        p = plan_vram(weights_gb=6.73, context_per_slot=8192, budget=get_budget("ULTRA"))
        assert p.fits, p.verdict_line()

    def test_tight_fits_serial(self) -> None:
        assert plan_vram(weights_gb=WEIGHTS, context_per_slot=8192,
                         budget=get_budget("TIGHT")).fits

    def test_tight_fits_six_slots_at_4096(self) -> None:
        p = plan_vram(weights_gb=WEIGHTS, context_per_slot=4096, slots=6,
                      budget=get_budget("TIGHT"))
        assert p.fits
        assert p.total_gb == pytest.approx(9.31, abs=0.02)

    def test_kv_is_charged_per_slot(self) -> None:
        one = plan_vram(weights_gb=WEIGHTS, context_per_slot=4096, slots=1)
        six = plan_vram(weights_gb=WEIGHTS, context_per_slot=4096, slots=6)
        assert six.kv_total_gb == pytest.approx(6 * one.kv_total_gb)

    def test_q8_costs_twice_q4(self) -> None:
        a = plan_vram(weights_gb=WEIGHTS, context_per_slot=4096, kv_type="q4_0")
        b = plan_vram(weights_gb=WEIGHTS, context_per_slot=4096, kv_type="q8_0")
        assert b.kv_total_gb == pytest.approx(2 * a.kv_total_gb)

    def test_draft_costs_both_weights_and_cache(self) -> None:
        bare = plan_vram(weights_gb=WEIGHTS, context_per_slot=4096, slots=6)
        withd = plan_vram(weights_gb=WEIGHTS, context_per_slot=4096, slots=6,
                          draft_weights_gb=0.35, draft_kib_per_token_f16=12.0)
        assert withd.total_gb > bare.total_gb + 0.35
        assert withd.draft_kv_gb > 0

    @pytest.mark.parametrize("bad", [{"context_per_slot": 0}, {"slots": 0}, {"weights_gb": -1}])
    def test_nonsense_plans_refused(self, bad: dict) -> None:
        kw = {"weights_gb": WEIGHTS, "context_per_slot": 4096, **bad}
        with pytest.raises(BudgetError):
            plan_vram(**kw)

    def test_solver_returns_the_largest_context_that_fits(self) -> None:
        b = get_budget("TIGHT")
        ctx = max_context_per_slot(weights_gb=WEIGHTS, slots=6, budget=b)
        assert plan_vram(weights_gb=WEIGHTS, context_per_slot=ctx, slots=6, budget=b).fits
        assert not plan_vram(weights_gb=WEIGHTS, context_per_slot=ctx + 512,
                             slots=6, budget=b).fits

    def test_solver_returns_zero_when_nothing_fits(self) -> None:
        assert max_context_per_slot(weights_gb=WEIGHTS, budget=get_budget("ULTRA")) == 0

    def test_slot_solver_is_tight(self) -> None:
        b = get_budget("TIGHT")
        n = max_slots(weights_gb=WEIGHTS, context_per_slot=4096, budget=b)
        assert plan_vram(weights_gb=WEIGHTS, context_per_slot=4096, slots=n, budget=b).fits
        assert not plan_vram(weights_gb=WEIGHTS, context_per_slot=4096, slots=n + 1, budget=b).fits

    def test_enumeration_only_returns_what_fits(self) -> None:
        plans = feasible_plans(weights_gb=WEIGHTS, budget=get_budget("TIGHT"))
        assert plans and all(p.fits for p in plans)

    def test_enumeration_is_empty_for_ultra(self) -> None:
        assert feasible_plans(weights_gb=WEIGHTS, budget=get_budget("ULTRA")) == []

    def test_plan_serialises(self) -> None:
        json.dumps(plan_vram(weights_gb=WEIGHTS, context_per_slot=4096, slots=6).as_dict())


# ---------------------------------------------------------------------------
# Host RAM
# ---------------------------------------------------------------------------


class TestHostRam:
    def test_no_mmap_refused_under_a_4gb_ceiling(self) -> None:
        with pytest.raises(MmapRefused, match="host RAM"):
            assert_mmap_allowed(no_mmap=True, weights_gb=WEIGHTS, budget=get_budget("TIGHT"))

    def test_no_mmap_allowed_when_the_file_would_fit(self) -> None:
        assert_mmap_allowed(no_mmap=True, weights_gb=2.0, budget=get_budget("TIGHT"))

    def test_mmap_on_is_never_refused(self) -> None:
        assert_mmap_allowed(no_mmap=False, weights_gb=999.0, budget=get_budget("TIGHT"))

    def test_argv_does_not_add_no_mmap_by_default(self) -> None:
        assert "--no-mmap" not in llama_server_argv(model_path="m.gguf")

    def test_verdict_reports_a_ceiling_and_serialises(self) -> None:
        v = RamGovernor(get_budget("TIGHT")).measure()
        assert v.ceiling_gb == 4.0
        json.dumps(v.as_dict())

    def test_unmeasurable_is_not_ok(self) -> None:
        from memory_governor import RamVerdict  # noqa: PLC0415

        assert not RamVerdict(None, 4.0, "no psutil").ok

    def test_pragmas_are_bounded_and_negative_means_kib(self) -> None:
        p = RamGovernor(get_budget("TIGHT")).sqlite_pragmas()
        assert p["cache_size"] < 0, "a positive cache_size is pages, not KiB"
        assert abs(p["cache_size"]) <= 65_536
        assert p["temp_store"] == "FILE"

    def test_tuning_reports_what_the_connection_accepted(self) -> None:
        conn = sqlite3.connect(":memory:")
        try:
            applied = RamGovernor(get_budget("TIGHT")).tune_sqlite(conn)
            assert "cache_size" in applied and "journal_mode" in applied
        finally:
            conn.close()


# ---------------------------------------------------------------------------
# Throughput
# ---------------------------------------------------------------------------


class TestThroughputModel:
    def test_eta_reproduces_the_measured_anchor(self) -> None:
        # BRAIN.md s.3: 35 tok/s, Qwen3.8-27B IQ3_XXS, 10.9 GB, this card.
        assert single_stream_tokps(10.9).tokps == pytest.approx(35.0, abs=0.15)

    def test_rate_is_inversely_proportional_to_size(self) -> None:
        a = single_stream_tokps(5.0).tokps
        b = single_stream_tokps(10.0).tokps
        assert a == pytest.approx(2 * b)

    def test_zero_weights_refused(self) -> None:
        with pytest.raises(ValueError):
            single_stream_tokps(0)

    def test_batching_helps_but_sublinearly(self) -> None:
        s = single_stream_tokps(WEIGHTS).tokps
        agg = batched_aggregate_tokps(WEIGHTS, 6).tokps
        assert s < agg < 6 * s

    def test_efficiency_is_unity_at_one_slot(self) -> None:
        assert batch_efficiency(1) == 1.0

    def test_efficiency_decreases(self) -> None:
        assert batch_efficiency(2) > batch_efficiency(6) > batch_efficiency(12)


class TestTargets:
    def test_300_single_stream_is_above_the_physical_ceiling(self) -> None:
        v = assess_target(300, weights_gb=WEIGHTS, kind="single")
        assert not v.reachable
        assert v.hard_ceiling_tokps < 300
        assert "physical ceiling" in v.reason

    def test_even_the_smallest_quant_cannot_do_300_single(self) -> None:
        assert not assess_target(300, weights_gb=6.19, kind="single").reachable

    def test_300_batched_is_close_but_not_met_on_this_file(self) -> None:
        v = assess_target(300, weights_gb=WEIGHTS, kind="batched", slots=6)
        assert not v.reachable
        assert v.achievable_tokps > 150, v.achievable_tokps

    def test_a_reachable_target_is_reported_reachable(self) -> None:
        v = assess_target(40, weights_gb=WEIGHTS, kind="single")
        assert v.reachable

    def test_unknown_kind_refused(self) -> None:
        with pytest.raises(ValueError):
            assess_target(100, weights_gb=WEIGHTS, kind="magic")


class TestSpeculative:
    def test_closed_form_matches_the_hand_calculation(self) -> None:
        # alpha=0.5, gamma=3 -> (1 - 0.5^4)/(1 - 0.5) = 1.875
        assert SpeculativePlan(0.5, 3, 0.35, WEIGHTS).expected_tokens_per_iteration == \
            pytest.approx(1.875)

    def test_zero_acceptance_yields_one_token(self) -> None:
        assert SpeculativePlan(0.0, 9, 0.35, WEIGHTS).expected_tokens_per_iteration == 1.0

    def test_speedup_grows_with_acceptance(self) -> None:
        lo = SpeculativePlan(0.3, 4, 0.35, WEIGHTS).speedup
        hi = SpeculativePlan(0.8, 4, 0.35, WEIGHTS).speedup
        assert hi > lo > 1.0

    def test_a_useless_draft_is_a_net_loss(self) -> None:
        assert not SpeculativePlan(0.02, 12, 0.35, WEIGHTS).helps

    def test_break_even_is_a_real_boundary(self) -> None:
        p = SpeculativePlan(0.5, 4, 0.35, WEIGHTS)
        be = p.break_even_alpha()
        assert SpeculativePlan(be + 0.03, 4, 0.35, WEIGHTS).speedup > 1.0
        assert SpeculativePlan(be - 0.03, 4, 0.35, WEIGHTS).speedup < 1.0

    def test_a_heavier_draft_costs_more(self) -> None:
        light = SpeculativePlan(0.7, 4, 0.35, WEIGHTS).speedup
        heavy = SpeculativePlan(0.7, 4, 3.00, WEIGHTS).speedup
        assert light > heavy

    @pytest.mark.parametrize("bad", [(-0.1, 4), (1.0, 4), (0.5, 0)])
    def test_nonsense_parameters_refused(self, bad: tuple) -> None:
        with pytest.raises(ValueError):
            SpeculativePlan(bad[0], bad[1], 0.35, WEIGHTS)

    def test_even_generous_acceptance_cannot_reach_300(self) -> None:
        assert SpeculativePlan(0.9, 8, 0.35, 6.19).tokps().tokps < 300

    def test_serialises(self) -> None:
        json.dumps(SpeculativePlan(0.7, 4, 0.35, WEIGHTS).as_dict())


class TestDeliberationLatency:
    def test_serial_misses_the_directive_target(self) -> None:
        assert not estimate_deliberation(weights_gb=WEIGHTS, mode="serial").meets_target

    def test_batched_with_warm_slots_meets_it(self) -> None:
        est = estimate_deliberation(weights_gb=WEIGHTS, mode="batched", warm_slots=True)
        assert est.meets_target, est.as_dict()

    def test_warm_slots_are_worth_real_time(self) -> None:
        cold = estimate_deliberation(weights_gb=WEIGHTS, mode="batched", warm_slots=False)
        warm = estimate_deliberation(weights_gb=WEIGHTS, mode="batched", warm_slots=True)
        assert cold.total_s - warm.total_s > 0.3

    def test_output_cap_is_the_biggest_lever(self) -> None:
        short = estimate_deliberation(weights_gb=WEIGHTS, output_tokens_each=48)
        long = estimate_deliberation(weights_gb=WEIGHTS, output_tokens_each=512)
        assert long.total_s > 4 * short.decode_s

    def test_speculative_mode_needs_a_plan(self) -> None:
        with pytest.raises(ValueError):
            estimate_deliberation(weights_gb=WEIGHTS, mode="speculative")

    def test_unknown_mode_refused(self) -> None:
        with pytest.raises(ValueError):
            estimate_deliberation(weights_gb=WEIGHTS, mode="hyperdrive")

    def test_estimate_serialises(self) -> None:
        json.dumps(estimate_deliberation(weights_gb=WEIGHTS).as_dict())


class TestExclusivity:
    def test_the_two_modes_are_reported_exclusive(self) -> None:
        ex = explain_exclusivity(weights_gb=WEIGHTS, budget=get_budget("TIGHT"))
        assert ex["exclusive"] is True
        assert "--parallel 1" in ex["flag_conflict"]

    def test_the_memory_argument_carries_numbers(self) -> None:
        m = explain_exclusivity(weights_gb=WEIGHTS, budget=get_budget("TIGHT"))["memory"]
        assert m["both_max_ctx_per_slot"] < m["batched_only_max_ctx_per_slot"]
        assert m["both_max_ctx_per_slot"] < m["speculative_only_max_ctx"]

    def test_argv_refuses_batched_speculative(self) -> None:
        with pytest.raises(ValueError, match="parallel 1"):
            llama_server_argv(model_path="m.gguf", mode="speculative", slots=6, use_mtp=True)


class TestServerArgv:
    def test_ctx_size_is_total_not_per_slot(self) -> None:
        argv = llama_server_argv(model_path="m.gguf", mode="batched", slots=6,
                                 context_per_slot=4096)
        assert argv[argv.index("--ctx-size") + 1] == "24576"
        assert argv[argv.index("--parallel") + 1] == "6"

    def test_serial_uses_one_slot(self) -> None:
        argv = llama_server_argv(model_path="m.gguf", mode="serial", context_per_slot=8192)
        assert argv[argv.index("--parallel") + 1] == "1"
        assert argv[argv.index("--ctx-size") + 1] == "8192"

    def test_batched_enables_continuous_batching(self) -> None:
        assert "--cont-batching" in llama_server_argv(model_path="m.gguf", mode="batched")

    def test_speculative_needs_a_draft_source(self) -> None:
        with pytest.raises(ValueError, match="draft"):
            llama_server_argv(model_path="m.gguf", mode="speculative", slots=1)

    def test_mtp_uses_the_renamed_flag(self) -> None:
        argv = llama_server_argv(model_path="m.gguf", mode="speculative", slots=1, use_mtp=True)
        assert "draft-mtp" in argv

    def test_unknown_mode_refused(self) -> None:
        with pytest.raises(ValueError):
            llama_server_argv(model_path="m.gguf", mode="warp")


# ---------------------------------------------------------------------------
# The benchmark harness
# ---------------------------------------------------------------------------


class TestBenchmarkHarness:
    def test_no_server_means_no_number(self) -> None:
        def dead(path, payload, timeout):
            raise ServerUnavailable("nothing listening")

        with pytest.raises(ServerUnavailable):
            ThroughputBench(transport=dead).run(mode="serial", requests=1, warmup=0)

    def test_serial_counts_tokens(self) -> None:
        r = ThroughputBench(transport=_fake_transport()).run(
            mode="serial", requests=4, max_tokens=48, warmup=0)
        assert r.ok and r.completion_tokens == 192 and r.concurrency == 1

    def test_batched_actually_overlaps(self) -> None:
        b = ThroughputBench(transport=_fake_transport(latency_s=0.05))
        ser = b.run(mode="serial", requests=6, warmup=0)
        bat = b.run(mode="batched", requests=6, concurrency=6, warmup=0)
        assert bat.wall_s < ser.wall_s
        assert bat.wall_tokps > ser.wall_tokps

    def test_warmup_requests_are_excluded(self) -> None:
        r = ThroughputBench(transport=_fake_transport()).run(
            mode="serial", requests=2, max_tokens=48, warmup=3)
        assert r.completion_tokens == 96

    def test_a_failed_request_is_recorded(self) -> None:
        state = {"n": 0}

        def flaky(path, payload, timeout):
            state["n"] += 1
            if state["n"] == 2:
                raise ValueError("bad json")
            return _fake_transport()(path, payload, timeout)

        r = ThroughputBench(transport=flaky).run(mode="serial", requests=3, warmup=0)
        assert r.errors and not r.ok

    def test_calibration_round_trips_eta(self) -> None:
        rate = single_stream_tokps(WEIGHTS).tokps
        r = ThroughputBench(transport=_fake_transport(tokps=rate)).run(
            mode="serial", requests=3, warmup=0)
        cal = ThroughputBench.calibrate(r, weights_gb=WEIGHTS)
        assert cal["eta_measured"] == pytest.approx(DEFAULT_HARDWARE.eta, abs=1e-6)

    def test_calibration_reports_batch_efficiency(self) -> None:
        b = ThroughputBench(transport=_fake_transport(tokps=45.0, latency_s=0.02))
        ser = b.run(mode="serial", requests=6, warmup=0)
        bat = b.run(mode="batched", requests=6, concurrency=6, warmup=0)
        cal = ThroughputBench.calibrate(ser, bat, weights_gb=WEIGHTS)
        assert cal["measured_batch_efficiency"] > 0
        assert "batch_decay_measured" in cal

    def test_unknown_mode_refused(self) -> None:
        with pytest.raises(BenchError):
            ThroughputBench(transport=_fake_transport()).run(mode="ludicrous")

    def test_result_serialises_and_says_it_is_measured(self) -> None:
        d = ThroughputBench(transport=_fake_transport()).run(
            mode="serial", requests=2, warmup=0).as_dict()
        json.dumps(d)
        assert d["measurement"].startswith("MEASURED")


class TestReports:
    def test_planner_report_renders_and_refuses_300(self) -> None:
        text = render_report()
        assert "UNREACHABLE" in text
        assert "physical ceiling" in text

    def test_planner_report_shows_the_exclusivity_finding(self) -> None:
        assert "cannot be combined" in render_report()

    def test_every_modelled_constant_is_labelled(self) -> None:
        d = DEFAULT_HARDWARE.as_dict()
        assert d["eta_provenance"].startswith("MEASURED")
        assert d["prefill_provenance"].startswith("INFERRED")
