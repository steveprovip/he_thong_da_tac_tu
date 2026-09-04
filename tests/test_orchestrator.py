#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_orchestrator.py - the scheduler's contract.

    pytest tests/test_orchestrator.py --budget-cap 10000

What is being defended
----------------------
1. A cyclic graph is rejected *before* any node runs. Discovering a cycle halfway
   through means work already happened that cannot be undone.
2. Dependencies are honoured. A node never starts before every parent is DONE.
3. Failure does not silently drop descendants on the floor - they become BLOCKED,
   a distinct state you can query, not a gap in the graph.
4. State survives a crash. Every transition commits, and a node left RUNNING by a
   dead process comes back READY, because a RUNNING node nobody owns would
   deadlock the resumed DAG.
5. The Sentry gates every node. Cost is pre-checked before work and charged after,
   and a refused charge halts rather than letting the ledger drift.

The suite drives the orchestrator through fake handlers rather than a live model.
That is deliberate: the scheduling contract is what these tests exist to prove,
and a real llama.cpp server would make the outcomes nondeterministic while
testing nothing extra about ordering.
"""

from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from orchestrator import (
    DAG,
    CycleError,
    HandlerResult,
    NodeState,
    Orchestrator,
    OrchestratorConfig,
    TaskNode,
)
from risk_guard import RiskGuard, RiskGuardConfig


# ---------------------------------------------------------------------------
# Fixtures local to this module
# ---------------------------------------------------------------------------


@pytest.fixture
def orch_config(tmp_path: Path) -> OrchestratorConfig:
    return OrchestratorConfig(db_path=tmp_path / "engine.db", node_timeout_s=5.0,
                              retry_backoff_s=0.0)


@pytest.fixture
def make_orch(tmp_path: Path, budget_cap: int) -> Any:
    """Factory for orchestrators wired to a funded, GPU-blind-tolerant guard.

    Every orchestrator needs a guard, and the default one is fail-closed on
    unmeasurable VRAM - correctly, since a guard that passes when it cannot see the
    GPU is not a guard. On a GPU-less runner that default blocks every node, which
    would make this file test the hardware gate instead of the scheduler. So the
    factory opts into `allow_unmeasurable_hardware`, and
    `TestSentryIntegration::test_default_guard_fails_closed_when_blind` covers the
    strict default explicitly rather than letting it go unexercised.

    Each guard gets its own database under tmp_path so no test writes to the repo's
    `state/` directory or inherits another test's ledger.
    """
    made: list[Any] = []
    counter = {"n": 0}

    def factory(**kw: Any) -> Orchestrator:
        counter["n"] += 1
        i = counter["n"]
        cfg = kw.pop("config", None) or OrchestratorConfig(
            db_path=tmp_path / f"engine{i}.db", node_timeout_s=5.0, retry_backoff_s=0.0
        )
        guard = kw.pop("guard", None)
        if guard is None:
            guard = RiskGuard(
                RiskGuardConfig(
                    budget_cap_minor=budget_cap,
                    db_path=tmp_path / f"guard{i}.db",
                    kill_file=tmp_path / f"KILL{i}",
                    allow_unmeasurable_hardware=True,
                )
            )
            if budget_cap > 0:
                guard.credit(budget_cap)
        o = Orchestrator(config=cfg, guard=guard, **kw)
        made.append((o, guard))
        return o

    yield factory
    for o, g in made:
        o.close()
        g.close()


@pytest.fixture
def orch(make_orch: Any) -> Any:
    return make_orch()


@pytest.fixture
def guarded_orch(make_orch: Any) -> Any:
    return make_orch()


def linear(n: int, cost: int = 0) -> DAG:
    dag = DAG()
    prev: list[str] = []
    for i in range(n):
        dag.add(TaskNode(id=f"n{i}", kind="probe", depends_on=list(prev),
                         projected_cost=cost))
        prev = [f"n{i}"]
    return dag


def run(o: Orchestrator, dag: DAG, goal: str = "test") -> DAG:
    return asyncio.run(o.run(dag, goal=goal))


# ---------------------------------------------------------------------------
# Graph validation happens first
# ---------------------------------------------------------------------------


class TestGraphValidation:
    def test_linear_order_is_stable(self):
        assert linear(4).topological_order() == ["n0", "n1", "n2", "n3"]

    def test_cycle_rejected(self):
        dag = DAG([
            TaskNode(id="a", kind="probe", depends_on=["b"]),
            TaskNode(id="b", kind="probe", depends_on=["a"]),
        ])
        with pytest.raises(CycleError):
            dag.validate()

    def test_self_loop_rejected(self):
        dag = DAG([TaskNode(id="a", kind="probe", depends_on=["a"])])
        with pytest.raises(CycleError):
            dag.validate()

    def test_unknown_dependency_rejected(self):
        dag = DAG([TaskNode(id="a", kind="probe", depends_on=["ghost"])])
        with pytest.raises((KeyError, ValueError)):
            dag.validate()

    def test_duplicate_id_rejected(self):
        dag = DAG()
        dag.add(TaskNode(id="a", kind="probe"))
        with pytest.raises(ValueError):
            dag.add(TaskNode(id="a", kind="probe"))

    def test_diamond_topology_valid(self):
        dag = DAG([
            TaskNode(id="root", kind="probe"),
            TaskNode(id="l", kind="probe", depends_on=["root"]),
            TaskNode(id="r", kind="probe", depends_on=["root"]),
            TaskNode(id="join", kind="probe", depends_on=["l", "r"]),
        ])
        order = dag.topological_order()
        assert order[0] == "root"
        assert order[-1] == "join"
        assert order.index("l") < order.index("join")
        assert order.index("r") < order.index("join")

    def test_cycle_rejected_before_any_work(self, orch):
        """The point of validating up front: no handler runs on a bad graph."""
        seen: list[str] = []

        async def spy(o: Orchestrator, node: TaskNode) -> HandlerResult:
            seen.append(node.id)
            return HandlerResult(output="ok")

        orch.register("probe", spy)
        dag = DAG([
            TaskNode(id="a", kind="probe"),
            TaskNode(id="b", kind="probe", depends_on=["c"]),
            TaskNode(id="c", kind="probe", depends_on=["b"]),
        ])
        with pytest.raises(CycleError):
            run(orch, dag)
        assert seen == [], f"no node should have run, but {seen} did"

    def test_empty_dag_is_complete(self):
        assert DAG().complete is True


# ---------------------------------------------------------------------------
# Execution and ordering
# ---------------------------------------------------------------------------


class TestExecution:
    def test_all_nodes_reach_done(self, orch):
        async def ok(o: Orchestrator, node: TaskNode) -> HandlerResult:
            return HandlerResult(output=f"did {node.id}")

        orch.register("probe", ok)
        dag = run(orch, linear(5))
        assert all(n.state is NodeState.DONE for n in dag.nodes.values())
        assert dag.complete

    def test_dependencies_are_respected(self, orch):
        order: list[str] = []

        async def record(o: Orchestrator, node: TaskNode) -> HandlerResult:
            await asyncio.sleep(0)
            order.append(node.id)
            return HandlerResult(output="ok")

        orch.register("probe", record)
        dag = DAG([
            TaskNode(id="root", kind="probe"),
            TaskNode(id="l", kind="probe", depends_on=["root"]),
            TaskNode(id="r", kind="probe", depends_on=["root"]),
            TaskNode(id="join", kind="probe", depends_on=["l", "r"]),
        ])
        run(orch, dag)
        assert order[0] == "root"
        assert order[-1] == "join"

    def test_result_is_stored_on_the_node(self, orch):
        async def ok(o: Orchestrator, node: TaskNode) -> HandlerResult:
            return HandlerResult(output="the answer")

        orch.register("probe", ok)
        dag = run(orch, linear(1))
        assert dag.nodes["n0"].result == "the answer"

    def test_missing_handler_fails_the_node(self, orch):
        dag = DAG([TaskNode(id="n0", kind="nonexistent-kind", max_attempts=1)])
        result = run(orch, dag)
        assert result.nodes["n0"].state is NodeState.FAILED
        assert result.nodes["n0"].error

    def test_handler_returning_wrong_type_fails(self, orch):
        async def bad(o: Orchestrator, node: TaskNode) -> Any:
            return "not a HandlerResult"

        orch.register("probe", bad)
        dag = run(orch, linear(1, cost=0))
        assert dag.nodes["n0"].state is NodeState.FAILED

    def test_timeout_is_a_failure_not_a_hang(self, make_orch, orch_config):
        cfg = OrchestratorConfig(db_path=orch_config.db_path, node_timeout_s=0.05,
                                 retry_backoff_s=0.0)
        o = make_orch(config=cfg)

        async def slow(_o: Orchestrator, node: TaskNode) -> HandlerResult:
            await asyncio.sleep(5.0)
            return HandlerResult(output="never")

        o.register("probe", slow)
        dag = DAG([TaskNode(id="n0", kind="probe", max_attempts=1)])
        result = asyncio.run(o.run(dag, goal="timeout"))
        assert result.nodes["n0"].state is NodeState.FAILED
        assert "timed out" in (result.nodes["n0"].error or "").lower()

    def test_noop_handler_is_registered(self, orch):
        dag = run(orch, DAG([TaskNode(id="n0", kind="noop")]))
        assert dag.nodes["n0"].state is NodeState.DONE


# ---------------------------------------------------------------------------
# Failure propagation
# ---------------------------------------------------------------------------


class TestFailurePropagation:
    def test_descendants_become_blocked_not_missing(self, orch):
        async def boom(o: Orchestrator, node: TaskNode) -> HandlerResult:
            raise RuntimeError("handler exploded")

        orch.register("probe", boom)
        dag = DAG([
            TaskNode(id="a", kind="probe", max_attempts=1),
            TaskNode(id="b", kind="probe", depends_on=["a"], max_attempts=1),
            TaskNode(id="c", kind="probe", depends_on=["b"], max_attempts=1),
        ])
        result = run(orch, dag)
        assert result.nodes["a"].state is NodeState.FAILED
        assert result.nodes["b"].state is NodeState.BLOCKED
        assert result.nodes["c"].state is NodeState.BLOCKED

    def test_independent_branch_still_finishes(self, orch):
        async def selective(o: Orchestrator, node: TaskNode) -> HandlerResult:
            if node.id == "bad":
                raise RuntimeError("only this one fails")
            return HandlerResult(output="ok")

        orch.register("probe", selective)
        dag = DAG([
            TaskNode(id="bad", kind="probe", max_attempts=1),
            TaskNode(id="good", kind="probe"),
        ])
        result = run(orch, dag)
        assert result.nodes["bad"].state is NodeState.FAILED
        assert result.nodes["good"].state is NodeState.DONE

    def test_retry_then_succeed(self, orch):
        attempts = {"n": 0}

        async def flaky(o: Orchestrator, node: TaskNode) -> HandlerResult:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise RuntimeError("transient")
            return HandlerResult(output="eventually")

        orch.register("probe", flaky)
        dag = DAG([TaskNode(id="n0", kind="probe", max_attempts=3)])
        result = run(orch, dag)
        assert result.nodes["n0"].state is NodeState.DONE
        assert attempts["n"] == 3

    def test_retry_budget_is_finite(self, orch):
        attempts = {"n": 0}

        async def always_bad(o: Orchestrator, node: TaskNode) -> HandlerResult:
            attempts["n"] += 1
            raise RuntimeError("permanent")

        orch.register("probe", always_bad)
        dag = DAG([TaskNode(id="n0", kind="probe", max_attempts=2)])
        result = run(orch, dag)
        assert result.nodes["n0"].state is NodeState.FAILED
        assert attempts["n"] == 2, "max_attempts must cap the retries"

    def test_blocked_by_failure_is_queryable(self, orch):
        async def boom(o: Orchestrator, node: TaskNode) -> HandlerResult:
            raise RuntimeError("x")

        orch.register("probe", boom)
        dag = DAG([
            TaskNode(id="a", kind="probe", max_attempts=1),
            TaskNode(id="b", kind="probe", depends_on=["a"], max_attempts=1),
        ])
        result = run(orch, dag)
        assert [n.id for n in result.blocked_by_failure()] == []  # already marked
        assert result.summary()["BLOCKED"] == 1


# ---------------------------------------------------------------------------
# Crash resumability
# ---------------------------------------------------------------------------


class TestPersistence:
    def test_state_survives_reload(self, make_orch, orch_config):
        o = make_orch(config=orch_config)

        async def ok(_o: Orchestrator, node: TaskNode) -> HandlerResult:
            return HandlerResult(output="ok")

        o.register("probe", ok)
        dag = DAG([TaskNode(id="n0", kind="probe"), TaskNode(id="n1", kind="probe")])
        run_id = o.create_run("persisted")
        asyncio.run(o.run(dag, run_id=run_id))
        o.close()

        o2 = make_orch(config=orch_config)
        reloaded = o2.load_dag(run_id)
        assert set(reloaded.nodes) == {"n0", "n1"}
        assert all(n.state is NodeState.DONE for n in reloaded.nodes.values())

    def test_running_resets_to_ready(self, make_orch, orch_config):
        """A RUNNING node whose process died must not stay RUNNING.

        Nothing will ever finish it, so a resumed DAG that respects RUNNING would
        wait forever on a node with no owner.
        """
        o = make_orch(config=orch_config)
        run_id = o.create_run("crashed")
        node = TaskNode(id="orphan", kind="probe", state=NodeState.RUNNING)
        o.run_id = run_id
        o._persist_node(node)  # noqa: SLF001 - simulating a crash mid-run
        o.close()

        o2 = make_orch(config=orch_config)
        reloaded = o2.load_dag(run_id)
        assert reloaded.nodes["orphan"].state is NodeState.READY

    def test_resume_finishes_a_partial_dag(self, make_orch, orch_config):
        done: list[str] = []

        async def ok(_o: Orchestrator, node: TaskNode) -> HandlerResult:
            done.append(node.id)
            return HandlerResult(output="ok")

        o = make_orch(config=orch_config)
        o.register("probe", ok)
        run_id = o.create_run("partial")
        o.run_id = run_id
        for n in (
            TaskNode(id="a", kind="probe", state=NodeState.DONE, result="prior"),
            TaskNode(id="b", kind="probe", depends_on=["a"]),
        ):
            o._persist_node(n)  # noqa: SLF001
        o.close()

        o2 = make_orch(config=orch_config)
        o2.register("probe", ok)
        reloaded = asyncio.run(o2.run(o2.load_dag(run_id), run_id=run_id))
        assert reloaded.complete
        assert done == ["b"], "a completed node must not be re-run"
        assert reloaded.nodes["a"].result == "prior"

    def test_transitions_are_recorded(self, orch):
        async def ok(o: Orchestrator, node: TaskNode) -> HandlerResult:
            return HandlerResult(output="ok")

        orch.register("probe", ok)
        run_id = orch.create_run("audit")
        asyncio.run(orch.run(linear(2), run_id=run_id))
        rows = orch.transitions(run_id)
        assert rows, "expected transition rows"
        states = {r["to_state"] for r in rows}
        assert "DONE" in states or NodeState.DONE.value in states

    def test_unknown_run_id_raises(self, orch):
        with pytest.raises((KeyError, ValueError)):
            orch.load_dag("no-such-run")

    def test_run_is_listed(self, orch):
        async def ok(o: Orchestrator, node: TaskNode) -> HandlerResult:
            return HandlerResult(output="ok")

        orch.register("probe", ok)
        asyncio.run(orch.run(linear(1), goal="listed"))
        goals = [r["goal"] for r in orch.runs()]
        assert "listed" in goals

    def test_db_lands_in_repo_by_default(self, repo_root):
        cfg = OrchestratorConfig()
        p = Path(cfg.db_path)
        assert "KyThuatNenAIMoi" not in str(p)
        assert p.is_relative_to(repo_root)

    def test_schema_has_three_tables(self, orch):
        conn = sqlite3.connect(str(orch.config.db_path))
        try:
            names = {r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )}
        finally:
            conn.close()
        assert {"runs", "nodes", "transitions"} <= names


# ---------------------------------------------------------------------------
# The Sentry gates every node
# ---------------------------------------------------------------------------


class TestSentryIntegration:
    def test_unaffordable_node_is_blocked(self, guarded_orch, budget_cap):
        ran: list[str] = []

        async def spy(o: Orchestrator, node: TaskNode) -> HandlerResult:
            ran.append(node.id)
            return HandlerResult(output="ok")

        guarded_orch.register("probe", spy)
        dag = DAG([TaskNode(id="pricey", kind="probe",
                            projected_cost=budget_cap + 1, max_attempts=1)])
        result = run(guarded_orch, dag)
        assert result.nodes["pricey"].state is NodeState.BLOCKED
        assert ran == [], "an unaffordable node must not execute"

    def test_costs_are_charged_after_work(self, guarded_orch, budget_cap):
        if budget_cap < 300:
            pytest.skip("cap too small")

        async def costly(o: Orchestrator, node: TaskNode) -> HandlerResult:
            return HandlerResult(output="ok", cost=100)

        guarded_orch.register("probe", costly)
        run(guarded_orch, linear(3, cost=100))
        assert guarded_orch.guard.spent_total() == 300
        assert guarded_orch.guard.balance() == budget_cap - 300

    def test_balance_never_negative_through_a_dag(self, guarded_orch, budget_cap):
        if budget_cap < 10:
            pytest.skip("cap too small")
        per = max(1, budget_cap // 4)

        async def costly(o: Orchestrator, node: TaskNode) -> HandlerResult:
            assert o.guard.balance() >= 0
            return HandlerResult(output="ok", cost=per)

        guarded_orch.register("probe", costly)
        run(guarded_orch, linear(12, cost=per))
        assert guarded_orch.guard.balance() >= 0

    def test_kill_switch_halts_the_run(self, guarded_orch, budget_cap):
        async def trip_then_work(o: Orchestrator, node: TaskNode) -> HandlerResult:
            if node.id == "n0":
                o.guard.trip_kill_switch("orchestrator test")
            return HandlerResult(output="ok")

        guarded_orch.register("probe", trip_then_work)
        result = run(guarded_orch, linear(4))
        assert guarded_orch.halted
        assert result.nodes["n0"].state is NodeState.DONE
        later = [result.nodes[f"n{i}"].state for i in (2, 3)]
        assert all(s is not NodeState.DONE for s in later), later

    def test_halt_reason_is_recorded(self, guarded_orch):
        guarded_orch.halt("manual stop")
        assert guarded_orch.halted
        assert "manual stop" in guarded_orch.halt_reason

    def test_a_guard_is_always_attached(self, orch):
        """There is no ungated mode. Omitting a guard builds the strict default."""
        assert orch.guard is not None

    def test_default_guard_fails_closed_when_blind(self, tmp_path, orch_config):
        """The production default blocks work it cannot verify the hardware for.

        Every other test in this file opts into `allow_unmeasurable_hardware` so it
        can exercise scheduling on a GPU-less runner. This one does not, and asserts
        the consequence: on a host where VRAM is unreadable, nodes are BLOCKED
        rather than run on an unverified machine.
        """
        from risk_guard import snapshot_hardware  # noqa: PLC0415

        strict = RiskGuard(
            RiskGuardConfig(
                budget_cap_minor=10_000,
                db_path=tmp_path / "strict.db",
                kill_file=tmp_path / "STRICT_KILL",
                # allow_unmeasurable_hardware deliberately left at its default
            )
        )
        assert strict.config.allow_unmeasurable_hardware is False
        strict.credit(10_000)
        blind = not snapshot_hardware().vram_measurable
        try:
            o = Orchestrator(config=orch_config, guard=strict)

            async def ok(_o: Orchestrator, node: TaskNode) -> HandlerResult:
                return HandlerResult(output="ok", cost=10)

            o.register("probe", ok)
            result = asyncio.run(o.run(DAG([TaskNode(id="n0", kind="probe",
                                                     projected_cost=10,
                                                     max_attempts=1)])))
            state = result.nodes["n0"].state
            if blind:
                assert state is NodeState.BLOCKED, (
                    "VRAM is unreadable here, so the strict guard must refuse"
                )
                assert "hardware" in (result.nodes["n0"].error or "").lower()
            else:
                assert state is NodeState.DONE, (
                    "VRAM is readable and within ceiling, so work should proceed"
                )
            o.close()
        finally:
            strict.close()


# ---------------------------------------------------------------------------
# Concurrency
# ---------------------------------------------------------------------------


class TestConcurrency:
    def test_default_is_one(self):
        assert OrchestratorConfig().max_concurrency == 1

    def test_zero_concurrency_rejected(self, tmp_path):
        with pytest.raises(ValueError):
            OrchestratorConfig(db_path=tmp_path / "x.db", max_concurrency=0)

    def test_one_serializes_independent_nodes(self, make_orch, orch_config):
        """With one model resident, two nodes must not be in flight at once."""
        peak = {"now": 0, "max": 0}

        async def track(_o: Orchestrator, node: TaskNode) -> HandlerResult:
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
            await asyncio.sleep(0.01)
            peak["now"] -= 1
            return HandlerResult(output="ok")

        cfg = OrchestratorConfig(db_path=orch_config.db_path, max_concurrency=1,
                                 retry_backoff_s=0.0)
        o = make_orch(config=cfg)
        o.register("probe", track)
        asyncio.run(o.run(DAG([TaskNode(id=f"n{i}", kind="probe") for i in range(6)])))
        assert peak["max"] == 1, f"concurrency 1 must never overlap, saw {peak['max']}"

    def test_higher_concurrency_overlaps(self, make_orch, orch_config):
        peak = {"now": 0, "max": 0}

        async def track(_o: Orchestrator, node: TaskNode) -> HandlerResult:
            peak["now"] += 1
            peak["max"] = max(peak["max"], peak["now"])
            await asyncio.sleep(0.05)
            peak["now"] -= 1
            return HandlerResult(output="ok")

        cfg = OrchestratorConfig(db_path=orch_config.db_path, max_concurrency=3,
                                 retry_backoff_s=0.0)
        o = make_orch(config=cfg)
        o.register("probe", track)
        asyncio.run(o.run(DAG([TaskNode(id=f"n{i}", kind="probe") for i in range(6)])))
        assert peak["max"] > 1
        assert peak["max"] <= 3, "the semaphore must still cap it"


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------


class _StubLLM:
    """Stands in for LocalLLMClient.

    The signatures mirror the real client - persona first, prompt second - so a
    change to that contract breaks these tests rather than passing while the live
    orchestrator would fail.
    """

    def __init__(self, payload: Any = None, text: str = "stub text") -> None:
        self.payload = payload
        self.text = text
        self.calls: list[dict[str, Any]] = []

    def complete_json(self, persona: Any, user_prompt: str, **kw: Any) -> Any:
        self.calls.append({"persona": persona, "prompt": user_prompt, **kw})
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def complete(self, persona: Any, user_prompt: str, **kw: Any) -> dict[str, Any]:
        self.calls.append({"persona": persona, "prompt": user_prompt, **kw})
        return {"text": self.text, "usage": {"total_tokens": 7}}


class TestPlanning:
    def test_no_llm_yields_a_single_node(self, orch):
        dag = asyncio.run(orch.plan("do the thing"))
        dag.validate()
        assert len(dag.nodes) == 1

    def test_model_plan_is_used(self, make_orch):
        payload = {"nodes": [
            {"id": "research", "kind": "research", "prompt": "find sources"},
            {"id": "write", "kind": "copy", "prompt": "draft", "depends_on": ["research"]},
        ]}
        o = make_orch(llm=_StubLLM(payload))
        dag = asyncio.run(o.plan("write a post"))
        dag.validate()
        assert set(dag.nodes) == {"research", "write"}
        assert dag.topological_order() == ["research", "write"]

    def test_dangling_dependency_is_dropped_not_fatal(self, make_orch):
        payload = {"nodes": [
            {"id": "a", "kind": "noop"},
            {"id": "b", "kind": "noop", "depends_on": ["a", "ghost"]},
        ]}
        o = make_orch(llm=_StubLLM(payload))
        dag = asyncio.run(o.plan("goal"))
        dag.validate()
        assert "ghost" not in dag.nodes["b"].depends_on
        assert "a" in dag.nodes["b"].depends_on, "a real dependency must survive"

    def test_cyclic_model_plan_is_flattened(self, make_orch):
        payload = {"nodes": [
            {"id": "a", "kind": "noop", "depends_on": ["b"]},
            {"id": "b", "kind": "noop", "depends_on": ["a"]},
        ]}
        o = make_orch(llm=_StubLLM(payload))
        dag = asyncio.run(o.plan("goal"))
        dag.validate()  # must not raise: a bad plan is repaired, not executed

    def test_bad_json_falls_back(self, make_orch):
        o = make_orch(llm=_StubLLM(ValueError("no json")))
        dag = asyncio.run(o.plan("goal"))
        dag.validate()
        assert len(dag.nodes) >= 1
        assert any("planning_error" in n.meta for n in dag.nodes.values()), (
            "the reason for the fallback must be recorded, not swallowed"
        )

    def test_planned_dag_is_runnable(self, make_orch):
        payload = {"nodes": [
            {"id": "a", "kind": "noop"},
            {"id": "b", "kind": "noop", "depends_on": ["a"]},
        ]}
        o = make_orch(llm=_StubLLM(payload))
        dag = asyncio.run(o.plan("goal"))
        result = asyncio.run(o.run(dag, goal="planned"))
        assert result.complete

    def test_orchestrator_persona_does_the_planning(self, make_orch):
        stub = _StubLLM({"nodes": [{"id": "a", "kind": "noop"}]})
        o = make_orch(llm=stub)
        asyncio.run(o.plan("goal"))
        assert stub.calls
        assert "orchestrator" in str(stub.calls[0]["persona"]).lower()


# ---------------------------------------------------------------------------
# Built-in handlers
# ---------------------------------------------------------------------------


class TestBuiltinHandlers:
    def test_llm_kinds_need_a_client(self, orch):
        dag = DAG([TaskNode(id="n0", kind="research", prompt="hi", max_attempts=1)])
        result = run(orch, dag)
        assert result.nodes["n0"].state is NodeState.FAILED
        assert "llm" in (result.nodes["n0"].error or "").lower()

    def test_llm_handler_returns_model_text(self, make_orch):
        stub = _StubLLM(text="model said this")
        o = make_orch(llm=stub)
        dag = DAG([TaskNode(id="n0", kind="copy", prompt="write something")])
        result = asyncio.run(o.run(dag))
        assert result.nodes["n0"].state is NodeState.DONE
        assert result.nodes["n0"].result == "model said this"
        assert stub.calls

    def test_node_persona_reaches_the_client(self, make_orch):
        stub = _StubLLM(text="ok")
        o = make_orch(llm=stub)
        asyncio.run(o.run(DAG([TaskNode(id="n0", kind="copy", prompt="x",
                                        persona="copywriter")])))
        assert "copywriter" in str(stub.calls[0]["persona"]).lower()

    def test_memory_context_is_passed_when_it_works(self, make_orch):
        """The happy path first, so the failure test below cannot pass vacuously.

        The handler swallows any exception from retrieval, including an
        AttributeError from a stub with the wrong method name. Proving the correct
        method is called here is what gives the tolerance test its meaning.
        """
        class GoodMemory:
            def context_blocks(self, query: str, budget_chars: int = 4000) -> list[str]:
                return ["RETRIEVED CONTEXT"]

        stub = _StubLLM(text="ok")
        o = make_orch(llm=stub, memory=GoodMemory())
        asyncio.run(o.run(DAG([TaskNode(id="n0", kind="research", prompt="q")])))
        assert "RETRIEVED CONTEXT" in str(stub.calls[0])

    def test_memory_failure_does_not_block_a_node(self, make_orch):
        calls = {"n": 0}

        class BrokenMemory:
            def context_blocks(self, *a: Any, **kw: Any) -> list[str]:
                calls["n"] += 1
                raise RuntimeError("index corrupt")

        o = make_orch(llm=_StubLLM(text="ok"), memory=BrokenMemory())
        dag = DAG([TaskNode(id="n0", kind="research", prompt="q")])
        result = asyncio.run(o.run(dag))
        assert calls["n"] == 1, "the broken method must actually have been reached"
        assert result.nodes["n0"].state is NodeState.DONE, (
            "retrieval is an enhancement; losing it must not fail the task"
        )
        assert result.nodes["n0"].meta.get("context_blocks") == 0

    def test_fetch_kind_needs_a_fetcher(self, orch):
        dag = DAG([TaskNode(id="n0", kind="fetch", prompt="https://example.invalid",
                            max_attempts=1)])
        result = run(orch, dag)
        assert result.nodes["n0"].state is NodeState.FAILED

    def test_status_reports_the_essentials(self, orch):
        st = orch.status()
        assert "registered_kinds" in st
        assert "noop" in st["registered_kinds"]
        assert st["halted"] is False
        # A degraded journal mode must be visible, not silent.
        assert st["guard"]["journal_mode"] in ("wal", "delete")
