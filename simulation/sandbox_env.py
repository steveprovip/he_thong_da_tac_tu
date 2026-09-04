#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
sandbox_env.py - paper-run simulator for the swarm's spending decisions.

There is no real-money code path in this file, by design
--------------------------------------------------------
The directive allowed real funds behind a two-factor human token. The chosen
option was simulation only, so `EXECUTION_MODE` is a module constant pinned to
"SIMULATION" and `authorize_real_funds()` unconditionally refuses. That refusal
is a tested invariant, not a stub: adding real execution would require editing
this file and deleting a test, which is exactly the amount of friction it should
take.

Why integers everywhere
-----------------------
Every amount is an integer count of minor currency units (VND has no subunit, so
1 unit = 1 VND). Floats do not belong in a ledger: 0.1 + 0.2 != 0.3, and a
rounding error that appears once per node compounds across a Monte Carlo sweep
into a number someone might believe. Probabilities are the only floats here, and
they never touch a balance - outcomes are converted to integers at the boundary
via `_round_half_up`.

What the simulation actually models
-----------------------------------
An `Action` costs a known integer amount and returns a stochastic payoff drawn
from a bounded distribution the caller supplies. This is deliberately not a
market model. It answers one question: given a hard ceiling of N units and zero
overdraft, how often does a proposed sequence of spends run out of money before
finishing, and what does the ending balance distribution look like? That is the
question the Sentry needs answered before it approves anything.

On reading the output
--------------------
`MonteCarloResult.p_ruin` is the fraction of trials that hit the budget floor.
It is an estimate from `trials` samples; its own standard error is reported as
`p_ruin_stderr`. With the default 1000 trials a p_ruin of 0.02 carries roughly
+/-0.004. Do not quote the third decimal.

CLI:
    python simulation/sandbox_env.py --selftest
    python simulation/sandbox_env.py --demo
    python simulation/sandbox_env.py --montecarlo --trials 2000 --budget 10000
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
    from risk_guard import OverdraftError, RiskGuard, RiskGuardConfig  # type: ignore
else:  # pragma: no cover
    from ..core.risk_guard import OverdraftError, RiskGuard, RiskGuardConfig

__all__ = [
    "EXECUTION_MODE",
    "RealFundsRefused",
    "authorize_real_funds",
    "Action",
    "Outcome",
    "ActionResult",
    "SandboxEnv",
    "SandboxConfig",
    "TrialResult",
    "MonteCarloResult",
    "monte_carlo",
    "sample_bounded",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Pinned. See the module docstring - real execution is not implemented.
EXECUTION_MODE = "SIMULATION"

DEFAULT_BUDGET_MINOR = 10_000
DEFAULT_CURRENCY = "VND"


class RealFundsRefused(RuntimeError):
    """Raised whenever anything asks to spend real money."""


def authorize_real_funds(token: Optional[str] = None, *, second_factor: Optional[str] = None) -> None:
    """Always raises.

    Kept as a named function so the refusal is discoverable and greppable rather
    than an absence. If real execution is ever built, this is the single place
    that has to change, and the accompanying test will fail loudly to say so.
    """
    raise RealFundsRefused(
        "Real-fund execution is not implemented in this build. "
        f"EXECUTION_MODE is pinned to {EXECUTION_MODE!r}. "
        "A real-money path would require: a two-factor human token, an audited "
        "broker adapter, and removal of this guard - none of which exist here."
    )


def _round_half_up(x: float) -> int:
    """Round to the nearest integer, .5 away from zero.

    Python's round() is banker's rounding, which biases a long series of payoffs
    toward even numbers. Money conventions expect half-up.
    """
    if x >= 0:
        return int(math.floor(x + 0.5))
    return -int(math.floor(-x + 0.5))


class Outcome(str, Enum):
    SUCCESS = "SUCCESS"
    PARTIAL = "PARTIAL"
    FAILURE = "FAILURE"
    UNAFFORDABLE = "UNAFFORDABLE"


def sample_bounded(
    rng: random.Random,
    *,
    low: float,
    high: float,
    mode: Optional[float] = None,
) -> float:
    """Draw from a bounded distribution.

    Triangular when a mode is given, uniform otherwise. Bounded on purpose: an
    unbounded distribution (lognormal, say) produces occasional payoffs large
    enough to dominate the mean of a 1000-trial sweep, which makes the summary
    statistics a description of the tail rather than of the plan.
    """
    if high < low:
        raise ValueError(f"high ({high}) must be >= low ({low})")
    if high == low:
        return low
    if mode is None:
        return rng.uniform(low, high)
    if not (low <= mode <= high):
        raise ValueError(f"mode ({mode}) must lie in [{low}, {high}]")
    return rng.triangular(low, high, mode)


@dataclass(frozen=True)
class Action:
    """One spend decision the swarm might make.

    cost_minor      integer units spent up front, unconditionally
    p_success       probability the action pays off at all
    payoff_low/high bounds of the return multiple applied to cost_minor
    payoff_mode     optional triangular mode; None means uniform
    partial_ratio   fraction of payoff recovered when the roll fails
    """

    name: str
    cost_minor: int
    p_success: float = 0.5
    payoff_low: float = 0.0
    payoff_high: float = 2.0
    payoff_mode: Optional[float] = None
    partial_ratio: float = 0.0
    tags: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.cost_minor, int) or isinstance(self.cost_minor, bool):
            raise TypeError(f"{self.name}: cost_minor must be an int, got {type(self.cost_minor).__name__}")
        if self.cost_minor < 0:
            raise ValueError(f"{self.name}: cost_minor must be >= 0")
        if not 0.0 <= self.p_success <= 1.0:
            raise ValueError(f"{self.name}: p_success must be in [0, 1], got {self.p_success}")
        if self.payoff_low < 0 or self.payoff_high < self.payoff_low:
            raise ValueError(f"{self.name}: need 0 <= payoff_low <= payoff_high")
        if self.payoff_mode is not None and not (self.payoff_low <= self.payoff_mode <= self.payoff_high):
            raise ValueError(f"{self.name}: payoff_mode outside [payoff_low, payoff_high]")
        if not 0.0 <= self.partial_ratio <= 1.0:
            raise ValueError(f"{self.name}: partial_ratio must be in [0, 1]")

    def expected_return_minor(self) -> int:
        """Expected gross return, integer units. Ignores variance entirely."""
        if self.payoff_mode is None:
            mean_mult = (self.payoff_low + self.payoff_high) / 2.0
        else:
            mean_mult = (self.payoff_low + self.payoff_high + self.payoff_mode) / 3.0
        expected = self.p_success * mean_mult + (1.0 - self.p_success) * self.partial_ratio * mean_mult
        return _round_half_up(expected * self.cost_minor)

    def expected_net_minor(self) -> int:
        return self.expected_return_minor() - self.cost_minor


@dataclass
class ActionResult:
    """What one simulated action did to the balance."""

    action: str
    outcome: Outcome
    cost_minor: int
    return_minor: int
    balance_after: int
    note: str = ""

    @property
    def net_minor(self) -> int:
        return self.return_minor - self.cost_minor

    def as_dict(self) -> dict[str, Any]:
        d = {
            "action": self.action,
            "outcome": self.outcome.value,
            "cost_minor": self.cost_minor,
            "return_minor": self.return_minor,
            "net_minor": self.net_minor,
            "balance_after": self.balance_after,
        }
        if self.note:
            d["note"] = self.note
        return d


@dataclass
class SandboxConfig:
    budget_minor: int = DEFAULT_BUDGET_MINOR
    currency: str = DEFAULT_CURRENCY
    seed: Optional[int] = None
    #: Stop the run the first time an action is unaffordable, instead of skipping
    #: it and continuing. Zero overdraft is enforced either way.
    halt_on_unaffordable: bool = True
    #: Optional RiskGuard to mirror *spending* into a durable ledger. None keeps
    #: the sandbox purely in-memory, which is what Monte Carlo wants - 1000
    #: trials x an fsync per write would take minutes for no benefit.
    #:
    #: Only charges are mirrored, never simulated returns. The guard's cap is a
    #: pre-funded ceiling: hypothetical revenue does not raise it, so crediting
    #: paper payoffs into the guard would be claiming money that does not exist.
    #: The consequence is deliberate - the guard tracks cumulative real-world
    #: spend against the ceiling, while `SandboxEnv.balance` tracks the
    #: hypothetical position including returns. The two differ by design, and
    #: the guard is the one that binds.
    guard: Optional[RiskGuard] = None

    def __post_init__(self) -> None:
        if not isinstance(self.budget_minor, int) or isinstance(self.budget_minor, bool):
            raise TypeError("budget_minor must be an int")
        if self.budget_minor < 0:
            raise ValueError("budget_minor must be >= 0")


class SandboxEnv:
    """A paper-money world with a hard floor at zero.

    The invariant this class exists to hold: `balance` is never negative and
    never exceeds `budget_minor + total_returns`. Every method that moves money
    asserts it before returning.
    """

    def __init__(self, config: Optional[SandboxConfig] = None) -> None:
        self.config = config or SandboxConfig()
        self.rng = random.Random(self.config.seed)
        self.balance: int = self.config.budget_minor
        self.history: list[ActionResult] = []
        self.ruined = False
        self._spent = 0
        self._returned = 0
        if self.config.guard is not None:
            # Fund the mirrored ledger so its own overdraft check agrees with ours.
            existing = self.config.guard.balance()
            if existing < self.config.budget_minor:
                self.config.guard.credit(
                    self.config.budget_minor - existing, label="sandbox funding"
                )

    # -- invariants --------------------------------------------------------

    def _assert_invariants(self) -> None:
        if self.balance < 0:
            raise OverdraftError(
                f"sandbox balance went negative ({self.balance}); this is a bug, "
                f"not a scenario - zero overdraft is an invariant"
            )
        ceiling = self.config.budget_minor + self._returned
        if self.balance > ceiling:
            raise AssertionError(
                f"balance {self.balance} exceeds budget+returns {ceiling}: money was created"
            )

    @property
    def spent_minor(self) -> int:
        return self._spent

    @property
    def returned_minor(self) -> int:
        return self._returned

    @property
    def net_minor(self) -> int:
        return self.balance - self.config.budget_minor

    def can_afford(self, cost_minor: int) -> bool:
        return cost_minor <= self.balance

    # -- execution ---------------------------------------------------------

    def run_action(self, action: Action) -> ActionResult:
        """Execute one action against the paper balance.

        An unaffordable action is refused before any money moves - the cost is
        never partially deducted, so there is no state in which the balance is
        below zero even transiently.
        """
        def refuse(note: str) -> ActionResult:
            res = ActionResult(
                action=action.name,
                outcome=Outcome.UNAFFORDABLE,
                cost_minor=0,
                return_minor=0,
                balance_after=self.balance,
                note=note,
            )
            self.history.append(res)
            self.ruined = True
            return res

        if not self.can_afford(action.cost_minor):
            return refuse(f"needs {action.cost_minor}, have {self.balance}")

        # The guard, when attached, is the binding constraint - it holds the
        # pre-funded ceiling and knows nothing of hypothetical returns. Charge it
        # first so a refusal happens before the sandbox balance moves.
        if self.config.guard is not None:
            try:
                self.config.guard.charge(action.cost_minor, label=f"sim:{action.name}")
            except Exception as exc:  # noqa: BLE001 - overdraft, cap, or kill switch
                return refuse(f"risk guard refused {action.cost_minor}: {exc}")

        self.balance -= action.cost_minor
        self._spent += action.cost_minor

        succeeded = self.rng.random() < action.p_success
        multiplier = sample_bounded(
            self.rng,
            low=action.payoff_low,
            high=action.payoff_high,
            mode=action.payoff_mode,
        )
        if not succeeded:
            multiplier *= action.partial_ratio
        gross = _round_half_up(multiplier * action.cost_minor)
        gross = max(0, gross)

        self.balance += gross
        self._returned += gross
        # Simulated returns are NOT credited to the guard. See SandboxConfig.guard.

        if succeeded:
            outcome = Outcome.SUCCESS
        elif gross > 0:
            outcome = Outcome.PARTIAL
        else:
            outcome = Outcome.FAILURE

        self._assert_invariants()
        res = ActionResult(
            action=action.name,
            outcome=outcome,
            cost_minor=action.cost_minor,
            return_minor=gross,
            balance_after=self.balance,
        )
        self.history.append(res)
        return res

    def run_plan(self, actions: Sequence[Action]) -> "TrialResult":
        """Run a sequence of actions, honouring the budget floor."""
        for action in actions:
            res = self.run_action(action)
            if res.outcome is Outcome.UNAFFORDABLE and self.config.halt_on_unaffordable:
                break
        return self.trial_result(len(actions))

    def trial_result(self, planned: int) -> "TrialResult":
        executed = sum(1 for r in self.history if r.outcome is not Outcome.UNAFFORDABLE)
        return TrialResult(
            final_balance=self.balance,
            spent=self._spent,
            returned=self._returned,
            actions_planned=planned,
            actions_executed=executed,
            ruined=self.ruined,
            outcomes=tuple(r.outcome for r in self.history),
        )

    def report(self) -> dict[str, Any]:
        counts: dict[str, int] = {}
        for r in self.history:
            counts[r.outcome.value] = counts.get(r.outcome.value, 0) + 1
        return {
            "mode": EXECUTION_MODE,
            "currency": self.config.currency,
            "budget_minor": self.config.budget_minor,
            "balance_minor": self.balance,
            "spent_minor": self._spent,
            "returned_minor": self._returned,
            "net_minor": self.net_minor,
            "ruined": self.ruined,
            "outcome_counts": counts,
            "actions": [r.as_dict() for r in self.history],
        }


@dataclass(frozen=True)
class TrialResult:
    """One Monte Carlo trial."""

    final_balance: int
    spent: int
    returned: int
    actions_planned: int
    actions_executed: int
    ruined: bool
    outcomes: tuple[Outcome, ...] = ()

    @property
    def completed(self) -> bool:
        return self.actions_executed == self.actions_planned


@dataclass
class MonteCarloResult:
    """Distribution over many trials. All balances are integer minor units."""

    trials: int
    budget_minor: int
    p_ruin: float
    p_complete: float
    mean_final: float
    median_final: int
    stdev_final: float
    min_final: int
    max_final: int
    p05_final: int
    p95_final: int
    mean_spent: float
    seed: Optional[int] = None
    finals: list[int] = field(default_factory=list, repr=False)

    @property
    def p_ruin_stderr(self) -> float:
        """Standard error of the ruin estimate.

        Reported because p_ruin is a sample statistic. Quoting it to three
        decimals from 1000 trials would be reading noise as signal.
        """
        if self.trials <= 0:
            return 0.0
        return math.sqrt(max(self.p_ruin * (1.0 - self.p_ruin), 0.0) / self.trials)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": EXECUTION_MODE,
            "trials": self.trials,
            "seed": self.seed,
            "budget_minor": self.budget_minor,
            "p_ruin": round(self.p_ruin, 4),
            "p_ruin_stderr": round(self.p_ruin_stderr, 4),
            "p_complete": round(self.p_complete, 4),
            "mean_final": round(self.mean_final, 2),
            "median_final": self.median_final,
            "stdev_final": round(self.stdev_final, 2),
            "min_final": self.min_final,
            "max_final": self.max_final,
            "p05_final": self.p05_final,
            "p95_final": self.p95_final,
            "mean_spent": round(self.mean_spent, 2),
        }


def _percentile(sorted_vals: Sequence[int], q: float) -> int:
    """Nearest-rank percentile. Returns an actual observed value, not an
    interpolation, so the result is always a balance that really occurred."""
    if not sorted_vals:
        return 0
    idx = int(math.ceil(q * len(sorted_vals))) - 1
    return sorted_vals[max(0, min(idx, len(sorted_vals) - 1))]


def monte_carlo(
    actions: Sequence[Action],
    *,
    trials: int = 1000,
    budget_minor: int = DEFAULT_BUDGET_MINOR,
    seed: Optional[int] = 1337,
    halt_on_unaffordable: bool = True,
    progress: Optional[Callable[[int, int], None]] = None,
) -> MonteCarloResult:
    """Sweep a plan across many trials.

    Each trial gets its own derived seed so the whole sweep is reproducible from
    one `seed` while individual trials stay independent. No RiskGuard is attached:
    the durable ledger is for real runs, not for a thousand hypotheticals.
    """
    if trials < 1:
        raise ValueError("trials must be >= 1")
    if not actions:
        raise ValueError("nothing to simulate: actions is empty")

    finals: list[int] = []
    spends: list[int] = []
    ruins = 0
    completes = 0

    base = random.Random(seed)
    for i in range(trials):
        env = SandboxEnv(
            SandboxConfig(
                budget_minor=budget_minor,
                seed=base.randrange(2**31),
                halt_on_unaffordable=halt_on_unaffordable,
            )
        )
        result = env.run_plan(actions)
        finals.append(result.final_balance)
        spends.append(result.spent)
        if result.ruined:
            ruins += 1
        if result.completed:
            completes += 1
        if progress is not None and (i + 1) % max(1, trials // 10) == 0:
            progress(i + 1, trials)

    ordered = sorted(finals)
    mean = sum(finals) / len(finals)
    if len(finals) > 1:
        var = sum((v - mean) ** 2 for v in finals) / (len(finals) - 1)
        stdev = math.sqrt(var)
    else:
        stdev = 0.0

    return MonteCarloResult(
        trials=trials,
        budget_minor=budget_minor,
        p_ruin=ruins / trials,
        p_complete=completes / trials,
        mean_final=mean,
        median_final=_percentile(ordered, 0.5),
        stdev_final=stdev,
        min_final=ordered[0],
        max_final=ordered[-1],
        p05_final=_percentile(ordered, 0.05),
        p95_final=_percentile(ordered, 0.95),
        mean_spent=sum(spends) / len(spends),
        seed=seed,
        finals=finals,
    )


def demo_plan() -> list[Action]:
    """A plausible campaign plan for the five personas, in minor units of 10,000."""
    return [
        Action(
            name="research_market",
            cost_minor=800,
            p_success=0.85,
            payoff_low=0.8,
            payoff_high=1.6,
            payoff_mode=1.1,
            partial_ratio=0.3,
            tags=("researcher",),
        ),
        Action(
            name="generate_assets",
            cost_minor=2_200,
            p_success=0.7,
            payoff_low=0.5,
            payoff_high=2.2,
            payoff_mode=1.2,
            partial_ratio=0.2,
            tags=("designer",),
        ),
        Action(
            name="write_copy",
            cost_minor=1_200,
            p_success=0.75,
            payoff_low=0.6,
            payoff_high=2.0,
            payoff_mode=1.15,
            partial_ratio=0.25,
            tags=("copywriter",),
        ),
        Action(
            name="test_campaign",
            cost_minor=3_000,
            p_success=0.45,
            payoff_low=0.2,
            payoff_high=3.0,
            payoff_mode=1.0,
            partial_ratio=0.15,
            tags=("orchestrator",),
        ),
        Action(
            name="scale_winner",
            cost_minor=2_500,
            p_success=0.5,
            payoff_low=0.3,
            payoff_high=2.8,
            payoff_mode=1.2,
            partial_ratio=0.1,
            tags=("orchestrator",),
        ),
    ]


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _selftest() -> int:
    import shutil
    import tempfile

    passed = 0
    failed: list[str] = []

    def check(name: str, fn: Callable[[], Any]) -> None:
        nonlocal passed
        try:
            fn()
            passed += 1
            print(f"  [ok] {name}")
        except AssertionError as exc:
            failed.append(f"{name}: {exc}")
            print(f"  [FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"  [ERR ] {name}: {type(exc).__name__}: {exc}")

    tmp = Path(tempfile.mkdtemp(prefix="sandbox_selftest_"))

    print("sandbox_env.py selftest")
    print("-" * 60)

    # --- the no-real-money invariant ---

    def t_mode_is_pinned_to_simulation() -> None:
        assert EXECUTION_MODE == "SIMULATION", EXECUTION_MODE

    def t_real_funds_always_refused() -> None:
        for args in [(), ("some-token",)]:
            try:
                authorize_real_funds(*args)
                raise AssertionError(f"authorize_real_funds{args} must refuse")
            except RealFundsRefused:
                pass

    def t_real_funds_refused_even_with_two_factor() -> None:
        try:
            authorize_real_funds("token", second_factor="123456")
            raise AssertionError("a two-factor token must still be refused in this build")
        except RealFundsRefused:
            pass

    # --- integer discipline ---

    def t_float_cost_rejected() -> None:
        try:
            Action(name="x", cost_minor=100.5)  # type: ignore[arg-type]
            raise AssertionError("expected TypeError")
        except TypeError:
            pass

    def t_bool_cost_rejected() -> None:
        # True is an int subclass; a bool in a ledger is always a mistake.
        try:
            Action(name="x", cost_minor=True)  # type: ignore[arg-type]
            raise AssertionError("expected TypeError")
        except TypeError:
            pass

    def t_negative_cost_rejected() -> None:
        try:
            Action(name="x", cost_minor=-1)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def t_bad_probability_rejected() -> None:
        for p in (-0.1, 1.1):
            try:
                Action(name="x", cost_minor=1, p_success=p)
                raise AssertionError(f"expected ValueError for p_success={p}")
            except ValueError:
                pass

    def t_bad_payoff_bounds_rejected() -> None:
        try:
            Action(name="x", cost_minor=1, payoff_low=2.0, payoff_high=1.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def t_bad_mode_rejected() -> None:
        try:
            Action(name="x", cost_minor=1, payoff_low=0.0, payoff_high=1.0, payoff_mode=5.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def t_all_balances_are_ints() -> None:
        env = SandboxEnv(SandboxConfig(budget_minor=10_000, seed=1))
        for r in [env.run_action(a) for a in demo_plan()]:
            assert isinstance(r.balance_after, int), type(r.balance_after)
            assert isinstance(r.return_minor, int), type(r.return_minor)
            assert isinstance(r.cost_minor, int), type(r.cost_minor)
        assert isinstance(env.balance, int)

    def t_round_half_up_not_bankers() -> None:
        assert _round_half_up(0.5) == 1, "half-up, not banker's rounding"
        assert _round_half_up(1.5) == 2
        assert _round_half_up(2.5) == 3, "banker's rounding would give 2"
        assert _round_half_up(-0.5) == -1

    # --- the zero-overdraft invariant ---

    def t_balance_never_negative() -> None:
        env = SandboxEnv(SandboxConfig(budget_minor=1_000, seed=7))
        for _ in range(50):
            env.run_action(Action(name="drain", cost_minor=300, p_success=0.0, payoff_low=0, payoff_high=0))
            assert env.balance >= 0, env.balance

    def t_unaffordable_action_moves_no_money() -> None:
        env = SandboxEnv(SandboxConfig(budget_minor=100, seed=1))
        before = env.balance
        res = env.run_action(Action(name="big", cost_minor=101))
        assert res.outcome is Outcome.UNAFFORDABLE, res.outcome
        assert env.balance == before, f"balance moved: {before} -> {env.balance}"
        assert res.cost_minor == 0, "an unaffordable action must not be charged"

    def t_exact_budget_is_affordable() -> None:
        # Zero overdraft means <= is allowed; spending the last unit is legal.
        env = SandboxEnv(SandboxConfig(budget_minor=100, seed=1))
        res = env.run_action(Action(name="all-in", cost_minor=100, p_success=0.0, payoff_low=0, payoff_high=0))
        assert res.outcome is not Outcome.UNAFFORDABLE, res.as_dict()
        assert env.balance == 0, env.balance

    def t_ruin_flag_set() -> None:
        env = SandboxEnv(SandboxConfig(budget_minor=50, seed=1))
        env.run_action(Action(name="big", cost_minor=500))
        assert env.ruined is True

    def t_halt_stops_the_plan() -> None:
        env = SandboxEnv(SandboxConfig(budget_minor=100, seed=3, halt_on_unaffordable=True))
        plan = [
            Action(name="a", cost_minor=100, p_success=0.0, payoff_low=0, payoff_high=0),
            Action(name="b", cost_minor=100),
            Action(name="c", cost_minor=100),
        ]
        result = env.run_plan(plan)
        assert result.actions_executed == 1, result
        assert len(env.history) == 2, "one executed, one refusal recorded"

    def t_no_halt_continues() -> None:
        env = SandboxEnv(SandboxConfig(budget_minor=100, seed=3, halt_on_unaffordable=False))
        plan = [Action(name=f"a{i}", cost_minor=1_000) for i in range(4)]
        env.run_plan(plan)
        assert len(env.history) == 4, len(env.history)
        assert all(r.outcome is Outcome.UNAFFORDABLE for r in env.history)

    def t_money_is_conserved() -> None:
        env = SandboxEnv(SandboxConfig(budget_minor=10_000, seed=42))
        env.run_plan(demo_plan())
        expected = env.config.budget_minor - env.spent_minor + env.returned_minor
        assert env.balance == expected, f"{env.balance} != {expected}"

    def t_zero_cost_action_is_free() -> None:
        env = SandboxEnv(SandboxConfig(budget_minor=10, seed=1))
        res = env.run_action(Action(name="free", cost_minor=0, p_success=1.0, payoff_low=5, payoff_high=5))
        assert res.return_minor == 0, "zero cost times any multiple is zero"
        assert env.balance == 10

    # --- determinism ---

    def t_same_seed_same_result() -> None:
        plan = demo_plan()
        a = SandboxEnv(SandboxConfig(budget_minor=10_000, seed=99)).run_plan(plan)
        b = SandboxEnv(SandboxConfig(budget_minor=10_000, seed=99)).run_plan(plan)
        assert a == b, f"{a}\n!=\n{b}"

    def t_different_seed_different_result() -> None:
        plan = demo_plan()
        a = SandboxEnv(SandboxConfig(budget_minor=10_000, seed=1)).run_plan(plan)
        b = SandboxEnv(SandboxConfig(budget_minor=10_000, seed=2)).run_plan(plan)
        assert a.final_balance != b.final_balance, "seeds should diverge"

    def t_monte_carlo_reproducible() -> None:
        plan = demo_plan()
        r1 = monte_carlo(plan, trials=120, seed=5)
        r2 = monte_carlo(plan, trials=120, seed=5)
        assert r1.finals == r2.finals, "same seed must reproduce the whole sweep"
        assert r1.p_ruin == r2.p_ruin

    # --- distribution sampling ---

    def t_uniform_stays_in_bounds() -> None:
        rng = random.Random(1)
        for _ in range(500):
            v = sample_bounded(rng, low=0.5, high=2.5)
            assert 0.5 <= v <= 2.5, v

    def t_triangular_stays_in_bounds() -> None:
        rng = random.Random(1)
        for _ in range(500):
            v = sample_bounded(rng, low=0.0, high=3.0, mode=1.0)
            assert 0.0 <= v <= 3.0, v

    def t_degenerate_range_is_constant() -> None:
        rng = random.Random(1)
        assert sample_bounded(rng, low=1.5, high=1.5) == 1.5

    def t_inverted_range_rejected() -> None:
        rng = random.Random(1)
        try:
            sample_bounded(rng, low=2.0, high=1.0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def t_expected_value_matches_simulation() -> None:
        # A tight sanity check on the payoff model: the empirical mean of many
        # draws should land near the closed-form expectation.
        act = Action(
            name="ev", cost_minor=1_000, p_success=0.5, payoff_low=0.0, payoff_high=2.0, partial_ratio=0.0
        )
        closed = act.expected_return_minor()  # 0.5 * 1.0 * 1000 = 500
        assert closed == 500, closed
        rng = random.Random(11)
        total = 0
        n = 4000
        for _ in range(n):
            ok = rng.random() < act.p_success
            m = sample_bounded(rng, low=act.payoff_low, high=act.payoff_high)
            if not ok:
                m *= act.partial_ratio
            total += _round_half_up(m * act.cost_minor)
        empirical = total / n
        assert abs(empirical - closed) < 40, f"empirical {empirical} vs closed {closed}"

    def t_expected_net_can_be_negative() -> None:
        losing = Action(name="bad", cost_minor=1_000, p_success=0.1, payoff_low=0.0, payoff_high=1.0)
        assert losing.expected_net_minor() < 0, losing.expected_net_minor()

    # --- Monte Carlo ---

    def t_monte_carlo_shape() -> None:
        r = monte_carlo(demo_plan(), trials=200, budget_minor=10_000, seed=3)
        d = r.as_dict()
        for k in ("trials", "p_ruin", "p_complete", "median_final", "p05_final", "p95_final"):
            assert k in d, f"missing {k}"
        assert 0.0 <= r.p_ruin <= 1.0
        assert r.min_final <= r.median_final <= r.max_final
        assert r.p05_final <= r.p95_final

    def t_monte_carlo_ruin_is_certain_when_plan_too_big() -> None:
        plan = [Action(name="huge", cost_minor=1_000_000)]
        r = monte_carlo(plan, trials=50, budget_minor=10_000, seed=1)
        assert r.p_ruin == 1.0, r.p_ruin
        assert r.p_complete == 0.0, r.p_complete

    def t_monte_carlo_no_ruin_when_plan_cheap() -> None:
        plan = [Action(name="tiny", cost_minor=1, p_success=1.0, payoff_low=1.0, payoff_high=1.0)]
        r = monte_carlo(plan, trials=50, budget_minor=10_000, seed=1)
        assert r.p_ruin == 0.0, r.p_ruin
        assert r.p_complete == 1.0

    def t_monte_carlo_never_reports_negative_balance() -> None:
        r = monte_carlo(demo_plan(), trials=300, budget_minor=5_000, seed=8)
        assert r.min_final >= 0, r.min_final
        assert all(v >= 0 for v in r.finals)

    def t_stderr_shrinks_with_trials() -> None:
        plan = [Action(name="coin", cost_minor=6_000, p_success=0.5, payoff_low=0.0, payoff_high=1.0)]
        small = monte_carlo(plan, trials=100, budget_minor=10_000, seed=4)
        large = monte_carlo(plan, trials=1600, budget_minor=10_000, seed=4)
        if 0.0 < small.p_ruin < 1.0 and 0.0 < large.p_ruin < 1.0:
            assert large.p_ruin_stderr < small.p_ruin_stderr, (
                small.p_ruin_stderr,
                large.p_ruin_stderr,
            )

    def t_zero_trials_rejected() -> None:
        try:
            monte_carlo(demo_plan(), trials=0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def t_empty_plan_rejected() -> None:
        try:
            monte_carlo([], trials=10)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def t_percentile_returns_observed_value() -> None:
        vals = [10, 20, 30, 40, 50]
        for q in (0.05, 0.5, 0.95, 1.0):
            assert _percentile(vals, q) in vals, q

    def t_bigger_budget_lowers_ruin() -> None:
        plan = demo_plan()
        tight = monte_carlo(plan, trials=300, budget_minor=4_000, seed=21)
        loose = monte_carlo(plan, trials=300, budget_minor=20_000, seed=21)
        assert loose.p_ruin <= tight.p_ruin, (tight.p_ruin, loose.p_ruin)

    # --- RiskGuard mirroring ---

    def t_guard_mirrors_spend_not_returns() -> None:
        # The guard tracks spend against the pre-funded ceiling; it must not be
        # credited with hypothetical revenue.
        guard = RiskGuard(
            RiskGuardConfig(
                db_path=tmp / "g_mirror.db",
                kill_file=tmp / "k_mirror",
                budget_cap_minor=10_000,
                allow_unmeasurable_hardware=True,
            )
        )
        env = SandboxEnv(SandboxConfig(budget_minor=10_000, seed=13, guard=guard))
        env.run_plan(demo_plan())
        assert guard.spent_total() == env.spent_minor, (guard.spent_total(), env.spent_minor)
        assert guard.balance() == 10_000 - env.spent_minor, guard.balance()
        assert guard.balance() >= 0
        guard.close()

    def t_guard_ceiling_is_the_binding_constraint() -> None:
        # Paper returns can lift SandboxEnv.balance, but the guard's ceiling still
        # caps total spend - so an action affordable on paper can still be refused.
        guard = RiskGuard(
            RiskGuardConfig(
                db_path=tmp / "g_bind.db",
                kill_file=tmp / "k_bind",
                budget_cap_minor=1_000,
                allow_unmeasurable_hardware=True,
            )
        )
        env = SandboxEnv(SandboxConfig(budget_minor=1_000, seed=5, guard=guard))
        winner = Action(name="win", cost_minor=600, p_success=1.0, payoff_low=3.0, payoff_high=3.0)
        first = env.run_action(winner)
        assert first.outcome is Outcome.SUCCESS, first.as_dict()
        assert env.balance == 1_000 - 600 + 1_800, env.balance
        # On paper there is plenty; the guard has only 400 left.
        second = env.run_action(winner)
        assert second.outcome is Outcome.UNAFFORDABLE, second.as_dict()
        assert "risk guard refused" in second.note, second.note
        assert guard.spent_total() == 600, guard.spent_total()
        guard.close()

    def t_guard_kill_switch_stops_the_sandbox() -> None:
        guard = RiskGuard(
            RiskGuardConfig(
                db_path=tmp / "g_kill.db",
                kill_file=tmp / "k_kill",
                budget_cap_minor=10_000,
                allow_unmeasurable_hardware=True,
            )
        )
        env = SandboxEnv(SandboxConfig(budget_minor=10_000, seed=5, guard=guard))
        guard.trip_kill_switch("sandbox test")
        res = env.run_action(Action(name="after-kill", cost_minor=100))
        assert res.outcome is Outcome.UNAFFORDABLE, res.as_dict()
        assert env.balance == 10_000, "no money may move once the kill switch is tripped"
        guard.close()

    def t_guard_records_every_action() -> None:
        guard = RiskGuard(
            RiskGuardConfig(
                db_path=tmp / "g_entries.db",
                kill_file=tmp / "k_entries",
                budget_cap_minor=10_000,
                allow_unmeasurable_hardware=True,
            )
        )
        env = SandboxEnv(SandboxConfig(budget_minor=10_000, seed=13, guard=guard))
        env.run_action(Action(name="one", cost_minor=500, p_success=1.0, payoff_low=1.0, payoff_high=1.0))
        entries = guard.ledger_entries()
        labels = " ".join(str(e.get("label", "")) for e in entries)
        assert "sim:one" in labels, labels
        guard.close()

    def t_report_shape() -> None:
        env = SandboxEnv(SandboxConfig(budget_minor=10_000, seed=2))
        env.run_plan(demo_plan())
        rep = env.report()
        assert rep["mode"] == "SIMULATION"
        assert rep["balance_minor"] == env.balance
        assert len(rep["actions"]) == len(env.history)
        json.dumps(rep)  # must be serializable

    def t_bad_budget_rejected() -> None:
        for bad in (-1, 10.5, True):
            try:
                SandboxConfig(budget_minor=bad)  # type: ignore[arg-type]
                raise AssertionError(f"expected rejection for {bad!r}")
            except (TypeError, ValueError):
                pass

    for name, fn in [
        ("EXECUTION_MODE pinned to SIMULATION", t_mode_is_pinned_to_simulation),
        ("real funds always refused", t_real_funds_always_refused),
        ("two-factor token still refused", t_real_funds_refused_even_with_two_factor),
        ("float cost rejected", t_float_cost_rejected),
        ("bool cost rejected", t_bool_cost_rejected),
        ("negative cost rejected", t_negative_cost_rejected),
        ("out-of-range probability rejected", t_bad_probability_rejected),
        ("inverted payoff bounds rejected", t_bad_payoff_bounds_rejected),
        ("out-of-range mode rejected", t_bad_mode_rejected),
        ("every balance is an int", t_all_balances_are_ints),
        ("rounding is half-up", t_round_half_up_not_bankers),
        ("balance never goes negative", t_balance_never_negative),
        ("unaffordable action moves no money", t_unaffordable_action_moves_no_money),
        ("spending the exact budget is legal", t_exact_budget_is_affordable),
        ("ruin flag is set", t_ruin_flag_set),
        ("halt stops the plan", t_halt_stops_the_plan),
        ("no-halt continues past refusal", t_no_halt_continues),
        ("money is conserved", t_money_is_conserved),
        ("zero-cost action is free", t_zero_cost_action_is_free),
        ("same seed reproduces a trial", t_same_seed_same_result),
        ("different seeds diverge", t_different_seed_different_result),
        ("monte carlo is reproducible", t_monte_carlo_reproducible),
        ("uniform draws stay in bounds", t_uniform_stays_in_bounds),
        ("triangular draws stay in bounds", t_triangular_stays_in_bounds),
        ("degenerate range is constant", t_degenerate_range_is_constant),
        ("inverted range rejected", t_inverted_range_rejected),
        ("expected value matches simulation", t_expected_value_matches_simulation),
        ("expected net can be negative", t_expected_net_can_be_negative),
        ("monte carlo result shape", t_monte_carlo_shape),
        ("ruin certain when plan exceeds budget", t_monte_carlo_ruin_is_certain_when_plan_too_big),
        ("no ruin when plan is cheap", t_monte_carlo_no_ruin_when_plan_cheap),
        ("never reports a negative balance", t_monte_carlo_never_reports_negative_balance),
        ("stderr shrinks with more trials", t_stderr_shrinks_with_trials),
        ("zero trials rejected", t_zero_trials_rejected),
        ("empty plan rejected", t_empty_plan_rejected),
        ("percentile returns an observed value", t_percentile_returns_observed_value),
        ("bigger budget lowers ruin", t_bigger_budget_lowers_ruin),
        ("guard mirrors spend, not returns", t_guard_mirrors_spend_not_returns),
        ("guard ceiling is the binding constraint", t_guard_ceiling_is_the_binding_constraint),
        ("guard kill switch stops the sandbox", t_guard_kill_switch_stops_the_sandbox),
        ("guard records each action", t_guard_records_every_action),
        ("report shape is serializable", t_report_shape),
        ("bad budget rejected", t_bad_budget_rejected),
    ]:
        check(name, fn)

    shutil.rmtree(tmp, ignore_errors=True)

    print("-" * 60)
    total = passed + len(failed)
    print(f"{passed}/{total} passed")
    if failed:
        print("\nFailures:")
        for f in failed:
            print(f"  - {f}")
        return 1
    return 0


def _demo(budget: int, seed: Optional[int]) -> int:
    env = SandboxEnv(SandboxConfig(budget_minor=budget, seed=seed))
    print(f"PAPER RUN - mode={EXECUTION_MODE}, budget={budget} {DEFAULT_CURRENCY}, seed={seed}")
    print("-" * 72)
    env.run_plan(demo_plan())
    for r in env.history:
        print(
            f"  {r.outcome.value:<12} {r.action:<18} cost={r.cost_minor:>6} "
            f"return={r.return_minor:>6} net={r.net_minor:>+7} balance={r.balance_after:>6}"
        )
    print("-" * 72)
    print(
        f"  final balance {env.balance} (net {env.net_minor:+}), "
        f"spent {env.spent_minor}, returned {env.returned_minor}"
    )
    return 0


def _montecarlo(trials: int, budget: int, seed: Optional[int]) -> int:
    print(f"MONTE CARLO - {trials} trials, budget {budget} {DEFAULT_CURRENCY}, seed {seed}")

    def progress(done: int, total: int) -> None:
        print(f"  {done}/{total}", end="\r", file=sys.stderr)

    r = monte_carlo(demo_plan(), trials=trials, budget_minor=budget, seed=seed, progress=progress)
    print(" " * 30, end="\r", file=sys.stderr)
    print(json.dumps(r.as_dict(), indent=2))
    print(
        f"\nReading this: p_ruin {r.p_ruin:.3f} +/- {r.p_ruin_stderr:.3f} "
        f"({trials} trials). The stderr is not decoration - a difference smaller "
        f"than it is noise."
    )
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Paper-run sandbox (no real funds)")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--montecarlo", action="store_true")
    ap.add_argument("--trials", type=int, default=1000)
    ap.add_argument("--budget", type=int, default=DEFAULT_BUDGET_MINOR)
    ap.add_argument("--seed", type=int, default=1337)
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.demo:
        return _demo(args.budget, args.seed)
    if args.montecarlo:
        return _montecarlo(args.trials, args.budget, args.seed)
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
