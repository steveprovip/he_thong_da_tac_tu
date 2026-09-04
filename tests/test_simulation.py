#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_simulation.py - the paper-run sandbox.

    pytest tests/test_simulation.py --budget-cap 10000

Two things are being defended here. First, that no real-money path exists: the
mode constant is pinned and `authorize_real_funds` refuses every argument shape,
including a plausible two-factor token. Second, that the accounting is integral
and floored at zero - a simulator that can go negative teaches the wrong lesson
about what the budget allows.

The Monte Carlo tests assert distribution *properties* (bounded, monotone in
budget, reproducible under a seed) rather than specific numbers. Asserting a mean
of 8875.78 would make the suite fail the moment anyone tunes the demo plan, which
tests the fixture rather than the code.
"""

from __future__ import annotations

import json
import random

import pytest

from risk_guard import RiskGuard, RiskGuardConfig
from sandbox_env import (
    EXECUTION_MODE,
    Action,
    MonteCarloResult,
    Outcome,
    RealFundsRefused,
    SandboxConfig,
    SandboxEnv,
    authorize_real_funds,
    demo_plan,
    monte_carlo,
    sample_bounded,
)


# ---------------------------------------------------------------------------
# No real money. This is the invariant the whole module exists to protect.
# ---------------------------------------------------------------------------


class TestNoRealFunds:
    def test_mode_is_pinned(self):
        assert EXECUTION_MODE == "SIMULATION"

    def test_bare_call_refused(self):
        with pytest.raises(RealFundsRefused):
            authorize_real_funds()

    def test_token_refused(self):
        with pytest.raises(RealFundsRefused):
            authorize_real_funds("looks-like-a-real-token")

    def test_two_factor_refused(self):
        # The directive allowed real funds behind 2FA; this build does not
        # implement it, and a convincing token must not change that.
        with pytest.raises(RealFundsRefused):
            authorize_real_funds("token", second_factor="654321")

    def test_refusal_explains_what_is_missing(self):
        with pytest.raises(RealFundsRefused) as exc:
            authorize_real_funds()
        msg = str(exc.value)
        assert "not implemented" in msg
        assert "SIMULATION" in msg

    def test_report_declares_simulation(self):
        env = SandboxEnv(SandboxConfig(budget_minor=1_000, seed=1))
        env.run_action(Action(name="a", cost_minor=100))
        assert env.report()["mode"] == "SIMULATION"


# ---------------------------------------------------------------------------
# Integer accounting
# ---------------------------------------------------------------------------


class TestIntegerAccounting:
    @pytest.mark.parametrize("bad", [100.5, 0.1, -3.7])
    def test_fractional_cost_rejected(self, bad):
        with pytest.raises((TypeError, ValueError)):
            Action(name="x", cost_minor=bad)

    def test_bool_cost_rejected(self):
        with pytest.raises(TypeError):
            Action(name="x", cost_minor=True)

    def test_negative_cost_rejected(self):
        with pytest.raises(ValueError):
            Action(name="x", cost_minor=-1)

    @pytest.mark.parametrize("p", [-0.01, 1.01, 2.0])
    def test_impossible_probability_rejected(self, p):
        with pytest.raises(ValueError):
            Action(name="x", cost_minor=1, p_success=p)

    def test_inverted_payoff_bounds_rejected(self):
        with pytest.raises(ValueError):
            Action(name="x", cost_minor=1, payoff_low=3.0, payoff_high=1.0)

    def test_all_reported_amounts_are_ints(self, budget_cap):
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=7))
        env.run_plan(demo_plan())
        assert isinstance(env.balance, int)
        assert isinstance(env.spent_minor, int)
        assert isinstance(env.returned_minor, int)
        for r in env.history:
            assert isinstance(r.cost_minor, int)
            assert isinstance(r.return_minor, int)
            assert isinstance(r.balance_after, int)

    def test_money_is_conserved(self, budget_cap):
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=11))
        env.run_plan(demo_plan())
        assert env.balance == budget_cap - env.spent_minor + env.returned_minor

    @pytest.mark.parametrize("bad", [-1, 10.5, True])
    def test_bad_budget_rejected(self, bad):
        with pytest.raises((TypeError, ValueError)):
            SandboxConfig(budget_minor=bad)


# ---------------------------------------------------------------------------
# Zero overdraft in the simulator too
# ---------------------------------------------------------------------------


class TestZeroOverdraft:
    def test_balance_never_negative(self, budget_cap):
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=3))
        drain = Action(name="drain", cost_minor=max(1, budget_cap // 3),
                       p_success=0.0, payoff_low=0.0, payoff_high=0.0)
        for _ in range(30):
            env.run_action(drain)
            assert env.balance >= 0, env.balance

    def test_unaffordable_moves_no_money(self, budget_cap):
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=1))
        res = env.run_action(Action(name="huge", cost_minor=budget_cap + 1))
        assert res.outcome is Outcome.UNAFFORDABLE
        assert res.cost_minor == 0
        assert env.balance == budget_cap

    def test_spending_exact_balance_is_legal(self, budget_cap):
        if budget_cap == 0:
            pytest.skip("nothing to spend")
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=1))
        res = env.run_action(
            Action(name="all-in", cost_minor=budget_cap, p_success=0.0,
                   payoff_low=0.0, payoff_high=0.0)
        )
        assert res.outcome is not Outcome.UNAFFORDABLE
        assert env.balance == 0

    def test_ruin_flag_set_on_refusal(self, budget_cap):
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=1))
        env.run_action(Action(name="huge", cost_minor=budget_cap + 10_000))
        assert env.ruined is True

    def test_halt_stops_after_first_refusal(self, budget_cap):
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=1, halt_on_unaffordable=True))
        big = budget_cap + 1
        result = env.run_plan([Action(name=f"a{i}", cost_minor=big) for i in range(5)])
        assert result.actions_executed == 0
        assert len(env.history) == 1, "halt means one refusal recorded, not five"

    def test_no_halt_records_every_refusal(self, budget_cap):
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=1, halt_on_unaffordable=False))
        big = budget_cap + 1
        env.run_plan([Action(name=f"a{i}", cost_minor=big) for i in range(4)])
        assert len(env.history) == 4


# ---------------------------------------------------------------------------
# Determinism
# ---------------------------------------------------------------------------


class TestDeterminism:
    def test_same_seed_same_trial(self, budget_cap):
        plan = demo_plan()
        a = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=42)).run_plan(plan)
        b = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=42)).run_plan(plan)
        assert a == b

    def test_different_seeds_diverge(self, budget_cap):
        if budget_cap < 1_000:
            pytest.skip("cap too small for the demo plan to differentiate")
        plan = demo_plan()
        a = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=1)).run_plan(plan)
        b = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=2)).run_plan(plan)
        assert a.final_balance != b.final_balance

    def test_monte_carlo_reproducible(self, budget_cap):
        r1 = monte_carlo(demo_plan(), trials=80, budget_minor=budget_cap, seed=9)
        r2 = monte_carlo(demo_plan(), trials=80, budget_minor=budget_cap, seed=9)
        assert r1.finals == r2.finals
        assert r1.p_ruin == r2.p_ruin

    def test_unseeded_runs_still_valid(self, budget_cap):
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=None))
        env.run_plan(demo_plan())
        assert env.balance >= 0


# ---------------------------------------------------------------------------
# Payoff sampling
# ---------------------------------------------------------------------------


class TestSampling:
    def test_uniform_within_bounds(self):
        rng = random.Random(1)
        for _ in range(1_000):
            assert 0.5 <= sample_bounded(rng, low=0.5, high=2.5) <= 2.5

    def test_triangular_within_bounds(self):
        rng = random.Random(2)
        for _ in range(1_000):
            assert 0.0 <= sample_bounded(rng, low=0.0, high=3.0, mode=1.0) <= 3.0

    def test_degenerate_range_constant(self):
        assert sample_bounded(random.Random(1), low=2.0, high=2.0) == 2.0

    def test_inverted_range_rejected(self):
        with pytest.raises(ValueError):
            sample_bounded(random.Random(1), low=5.0, high=1.0)

    def test_mode_outside_range_rejected(self):
        with pytest.raises(ValueError):
            sample_bounded(random.Random(1), low=0.0, high=1.0, mode=9.0)

    def test_expected_return_is_closed_form(self):
        act = Action(name="ev", cost_minor=1_000, p_success=0.5,
                     payoff_low=0.0, payoff_high=2.0, partial_ratio=0.0)
        assert act.expected_return_minor() == 500
        assert act.expected_net_minor() == -500

    def test_losing_action_has_negative_expectation(self):
        bad = Action(name="bad", cost_minor=1_000, p_success=0.05,
                     payoff_low=0.0, payoff_high=1.0)
        assert bad.expected_net_minor() < 0

    def test_empirical_mean_near_expectation(self):
        act = Action(name="ev", cost_minor=1_000, p_success=0.5,
                     payoff_low=0.0, payoff_high=2.0)
        rng = random.Random(5)
        total = 0
        n = 3_000
        for _ in range(n):
            ok = rng.random() < act.p_success
            m = sample_bounded(rng, low=act.payoff_low, high=act.payoff_high)
            total += int(m * act.cost_minor) if ok else 0
        assert abs(total / n - act.expected_return_minor()) < 50


# ---------------------------------------------------------------------------
# Monte Carlo
# ---------------------------------------------------------------------------


class TestMonteCarlo:
    def test_result_shape(self, budget_cap):
        r = monte_carlo(demo_plan(), trials=100, budget_minor=budget_cap, seed=1)
        assert isinstance(r, MonteCarloResult)
        d = r.as_dict()
        json.dumps(d)
        for key in ("p_ruin", "p_ruin_stderr", "p_complete", "median_final",
                    "p05_final", "p95_final", "mean_spent"):
            assert key in d

    def test_probabilities_in_range(self, budget_cap):
        r = monte_carlo(demo_plan(), trials=100, budget_minor=budget_cap, seed=1)
        assert 0.0 <= r.p_ruin <= 1.0
        assert 0.0 <= r.p_complete <= 1.0

    def test_percentiles_ordered(self, budget_cap):
        r = monte_carlo(demo_plan(), trials=200, budget_minor=budget_cap, seed=1)
        assert r.min_final <= r.p05_final <= r.median_final <= r.p95_final <= r.max_final

    def test_no_negative_finals(self, budget_cap):
        r = monte_carlo(demo_plan(), trials=300, budget_minor=budget_cap, seed=4)
        assert r.min_final >= 0
        assert all(v >= 0 for v in r.finals)

    def test_ruin_certain_when_plan_exceeds_budget(self, budget_cap):
        r = monte_carlo([Action(name="huge", cost_minor=budget_cap + 1_000_000)],
                        trials=20, budget_minor=budget_cap, seed=1)
        assert r.p_ruin == 1.0
        assert r.p_complete == 0.0

    def test_no_ruin_when_plan_is_trivial(self, budget_cap):
        if budget_cap < 1:
            pytest.skip("cannot afford even a 1-unit action")
        r = monte_carlo([Action(name="tiny", cost_minor=1, p_success=1.0,
                               payoff_low=1.0, payoff_high=1.0)],
                        trials=20, budget_minor=budget_cap, seed=1)
        assert r.p_ruin == 0.0
        assert r.p_complete == 1.0

    def test_bigger_budget_never_increases_ruin(self, budget_cap):
        plan = demo_plan()
        tight = monte_carlo(plan, trials=200, budget_minor=max(1, budget_cap // 3), seed=17)
        loose = monte_carlo(plan, trials=200, budget_minor=budget_cap * 3, seed=17)
        assert loose.p_ruin <= tight.p_ruin

    def test_stderr_is_reported_and_shrinks(self, budget_cap):
        plan = [Action(name="coin", cost_minor=max(1, int(budget_cap * 0.6)),
                       p_success=0.5, payoff_low=0.0, payoff_high=1.0)]
        small = monte_carlo(plan, trials=100, budget_minor=budget_cap, seed=3)
        large = monte_carlo(plan, trials=900, budget_minor=budget_cap, seed=3)
        assert small.p_ruin_stderr >= 0.0
        if 0.0 < small.p_ruin < 1.0 and 0.0 < large.p_ruin < 1.0:
            assert large.p_ruin_stderr < small.p_ruin_stderr

    def test_zero_trials_rejected(self):
        with pytest.raises(ValueError):
            monte_carlo(demo_plan(), trials=0)

    def test_empty_plan_rejected(self):
        with pytest.raises(ValueError):
            monte_carlo([], trials=5)

    def test_progress_callback_invoked(self, budget_cap):
        seen: list[tuple[int, int]] = []
        monte_carlo(demo_plan(), trials=50, budget_minor=budget_cap, seed=1,
                    progress=lambda d, t: seen.append((d, t)))
        assert seen, "progress callback should fire"
        assert seen[-1][1] == 50

    @pytest.mark.slow
    def test_large_sweep_is_stable(self, budget_cap):
        r = monte_carlo(demo_plan(), trials=5_000, budget_minor=budget_cap, seed=99)
        assert r.trials == 5_000
        assert r.min_final >= 0
        assert r.p_ruin_stderr < 0.02


# ---------------------------------------------------------------------------
# RiskGuard integration
# ---------------------------------------------------------------------------


class TestGuardIntegration:
    def test_guard_mirrors_spend_only(self, guard, budget_cap):
        """The guard holds the pre-funded ceiling; paper revenue must not raise it."""
        if budget_cap < 1_000:
            pytest.skip("cap too small for the demo plan")
        guard.credit(budget_cap)
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=13, guard=guard))
        env.run_plan(demo_plan())
        assert guard.spent_total() == env.spent_minor
        assert guard.balance() == budget_cap - env.spent_minor
        assert guard.balance() >= 0

    def test_guard_ceiling_binds_before_paper_balance(self, tmp_path):
        """A winner can lift the paper balance, but the ceiling still caps spend."""
        cap = 1_000
        g = RiskGuard(
            RiskGuardConfig(
                budget_cap_minor=cap,
                db_path=tmp_path / "bind.db",
                kill_file=tmp_path / "bind_kill",
                allow_unmeasurable_hardware=True,
            )
        )
        g.credit(cap)
        env = SandboxEnv(SandboxConfig(budget_minor=cap, seed=5, guard=g))
        winner = Action(name="win", cost_minor=600, p_success=1.0,
                        payoff_low=3.0, payoff_high=3.0)
        assert env.run_action(winner).outcome is Outcome.SUCCESS
        assert env.balance > cap, "paper balance should exceed the ceiling after a win"
        second = env.run_action(winner)
        assert second.outcome is Outcome.UNAFFORDABLE
        assert "risk guard refused" in second.note
        g.close()

    def test_kill_switch_freezes_the_sandbox(self, funded_guard, budget_cap):
        if budget_cap < 100:
            pytest.skip("cap too small")
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=5, guard=funded_guard))
        funded_guard.trip_kill_switch("simulation test")
        res = env.run_action(Action(name="after-kill", cost_minor=100))
        assert res.outcome is Outcome.UNAFFORDABLE
        assert env.balance == budget_cap, "no money moves once the switch is tripped"

    def test_actions_are_labelled_in_the_ledger(self, funded_guard, budget_cap):
        if budget_cap < 500:
            pytest.skip("cap too small")
        env = SandboxEnv(SandboxConfig(budget_minor=budget_cap, seed=1, guard=funded_guard))
        env.run_action(Action(name="labelled", cost_minor=500, p_success=1.0,
                              payoff_low=1.0, payoff_high=1.0))
        blob = " ".join(str(e) for e in funded_guard.ledger_entries())
        assert "sim:labelled" in blob

    def test_monte_carlo_uses_no_guard(self, budget_cap):
        # 1000 fsyncs per sweep would be pointless; the sweep is in-memory.
        r = monte_carlo(demo_plan(), trials=25, budget_minor=budget_cap, seed=1)
        assert r.trials == 25
