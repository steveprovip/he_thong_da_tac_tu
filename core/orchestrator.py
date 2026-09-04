#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
orchestrator.py - asyncio DAG scheduler with SQLite WAL state persistence.

The shape of this scheduler follows from one hardware fact
---------------------------------------------------------
Only one model fits in 16 GB of VRAM at a time, and llama.cpp requires
`--parallel 1` when MTP speculative decoding is on (BRAIN.md QD-3). So there is
exactly one inference slot. The scheduler is asyncio-based not to run model calls
in parallel - it cannot - but because:

  * a single semaphore of size 1 makes the serialization explicit and auditable
    rather than accidental,
  * non-model work (fetching, file writing, indexing) genuinely does overlap
    with a running completion,
  * cancellation and timeouts are tractable.

`max_concurrency` defaults to 1 for exactly this reason. Raising it above 1 is
only meaningful if your node handlers are I/O-bound and never touch the model.

Durability
----------
Every state transition is a committed write before the next step begins, so a
`kill -9` mid-DAG leaves a resumable graph rather than an ambiguous one. On
restart, nodes found in RUNNING are reset to READY - the process that owned them
is gone, so they never completed. WAL mode is on for crash safety and for
concurrent readers (a dashboard can tail the DB while the swarm runs).

The Sentry gate is not optional
-------------------------------
Every node passes `RiskGuard.pre_check` before it runs and is charged after.
A DENY marks the node BLOCKED and, when the kill switch is the cause, halts the
whole run. There is no code path that dispatches a node without the gate.

CLI:
    python core/orchestrator.py --selftest
    python core/orchestrator.py --demo
    python core/orchestrator.py --status --db state/engine.db
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sqlite3
import sys
import threading
import time
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Awaitable, Callable, Iterable, Optional

# Import siblings whether this is run as a module or as a script.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from local_llm_client import LocalLLMClient, Persona  # type: ignore
    from risk_guard import (  # type: ignore
        Decision,
        RiskGuard,
        RiskGuardConfig,
        Verdict,
        journal_mode_of,
        open_durable_connection,
    )
else:  # pragma: no cover - exercised only when imported as a package
    from .local_llm_client import LocalLLMClient, Persona
    from .risk_guard import (
        Decision,
        RiskGuard,
        RiskGuardConfig,
        Verdict,
        journal_mode_of,
        open_durable_connection,
    )

__all__ = [
    "NodeState",
    "TaskNode",
    "DAG",
    "Orchestrator",
    "OrchestratorConfig",
    "CycleError",
    "HandlerResult",
]

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "state" / "engine.db"


class NodeState(str, Enum):
    PENDING = "PENDING"
    READY = "READY"
    RUNNING = "RUNNING"
    DONE = "DONE"
    FAILED = "FAILED"
    BLOCKED = "BLOCKED"
    CANCELLED = "CANCELLED"

    @property
    def terminal(self) -> bool:
        return self in (NodeState.DONE, NodeState.FAILED, NodeState.BLOCKED, NodeState.CANCELLED)


class CycleError(ValueError):
    """The submitted graph is not acyclic."""


@dataclass
class TaskNode:
    """One atomic unit of work."""

    id: str
    kind: str
    prompt: str = ""
    persona: str = Persona.RESEARCHER.value
    depends_on: list[str] = field(default_factory=list)
    state: NodeState = NodeState.PENDING
    result: Optional[str] = None
    error: Optional[str] = None
    attempts: int = 0
    max_attempts: int = 3
    projected_cost: int = 0
    actual_cost: int = 0
    started_at: Optional[str] = None
    finished_at: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["state"] = self.state.value
        return d


@dataclass
class HandlerResult:
    """What a node handler returns."""

    output: str
    cost: int = 0
    meta: dict[str, Any] = field(default_factory=dict)


NodeHandler = Callable[["Orchestrator", TaskNode], Awaitable[HandlerResult]]


class DAG:
    """A task graph. Validates acyclicity at construction."""

    def __init__(self, nodes: Optional[Iterable[TaskNode]] = None) -> None:
        self.nodes: dict[str, TaskNode] = {}
        for n in nodes or []:
            self.add(n)

    def add(self, node: TaskNode) -> TaskNode:
        if node.id in self.nodes:
            raise ValueError(f"duplicate node id {node.id!r}")
        self.nodes[node.id] = node
        return node

    def validate(self) -> None:
        """Reject unknown dependencies, self-loops, and cycles."""
        for n in self.nodes.values():
            for dep in n.depends_on:
                if dep == n.id:
                    raise CycleError(f"node {n.id!r} depends on itself")
                if dep not in self.nodes:
                    raise ValueError(f"node {n.id!r} depends on unknown node {dep!r}")
        # Kahn's algorithm; leftover nodes mean a cycle.
        indegree = {nid: len(n.depends_on) for nid, n in self.nodes.items()}
        queue = [nid for nid, d in indegree.items() if d == 0]
        seen = 0
        while queue:
            cur = queue.pop()
            seen += 1
            for other in self.nodes.values():
                if cur in other.depends_on:
                    indegree[other.id] -= 1
                    if indegree[other.id] == 0:
                        queue.append(other.id)
        if seen != len(self.nodes):
            stuck = sorted(nid for nid, d in indegree.items() if d > 0)
            raise CycleError(f"graph contains a cycle among: {', '.join(stuck)}")

    def topological_order(self) -> list[str]:
        self.validate()
        indegree = {nid: len(n.depends_on) for nid, n in self.nodes.items()}
        # Sorted for determinism, so two runs of the same DAG order identically.
        queue = sorted(nid for nid, d in indegree.items() if d == 0)
        out: list[str] = []
        while queue:
            cur = queue.pop(0)
            out.append(cur)
            newly: list[str] = []
            for other in self.nodes.values():
                if cur in other.depends_on:
                    indegree[other.id] -= 1
                    if indegree[other.id] == 0:
                        newly.append(other.id)
            queue.extend(sorted(newly))
            queue.sort()
        return out

    def ready_nodes(self) -> list[TaskNode]:
        """Nodes whose dependencies are all DONE."""
        out: list[TaskNode] = []
        for n in self.nodes.values():
            if n.state not in (NodeState.PENDING, NodeState.READY):
                continue
            if all(self.nodes[d].state is NodeState.DONE for d in n.depends_on):
                out.append(n)
        return sorted(out, key=lambda x: x.id)

    def blocked_by_failure(self) -> list[TaskNode]:
        """Nodes that can never run because an ancestor is terminal-but-not-DONE."""
        out: list[TaskNode] = []
        for n in self.nodes.values():
            if n.state.terminal:
                continue
            for d in n.depends_on:
                ds = self.nodes[d].state
                if ds.terminal and ds is not NodeState.DONE:
                    out.append(n)
                    break
        return out

    @property
    def complete(self) -> bool:
        return all(n.state.terminal for n in self.nodes.values())

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for n in self.nodes.values():
            counts[n.state.value] = counts.get(n.state.value, 0) + 1
        return counts


_SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id     TEXT PRIMARY KEY,
    goal       TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'RUNNING'
);

CREATE TABLE IF NOT EXISTS nodes (
    run_id         TEXT NOT NULL,
    id             TEXT NOT NULL,
    kind           TEXT NOT NULL,
    prompt         TEXT NOT NULL DEFAULT '',
    persona        TEXT NOT NULL DEFAULT '',
    depends_on     TEXT NOT NULL DEFAULT '[]',
    state          TEXT NOT NULL,
    result         TEXT,
    error          TEXT,
    attempts       INTEGER NOT NULL DEFAULT 0,
    max_attempts   INTEGER NOT NULL DEFAULT 3,
    projected_cost INTEGER NOT NULL DEFAULT 0,
    actual_cost    INTEGER NOT NULL DEFAULT 0,
    started_at     TEXT,
    finished_at    TEXT,
    meta           TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (run_id, id)
);
CREATE INDEX IF NOT EXISTS idx_nodes_state ON nodes(run_id, state);

CREATE TABLE IF NOT EXISTS transitions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    ts         TEXT NOT NULL,
    run_id     TEXT NOT NULL,
    node_id    TEXT NOT NULL,
    from_state TEXT,
    to_state   TEXT NOT NULL,
    note       TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_trans_run ON transitions(run_id);
"""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


@dataclass
class OrchestratorConfig:
    db_path: Path = DEFAULT_DB
    max_concurrency: int = 1
    node_timeout_s: float = 300.0
    retry_backoff_s: float = 0.5
    halt_on_kill_switch: bool = True

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path)
        if self.max_concurrency < 1:
            raise ValueError("max_concurrency must be >= 1")


class Orchestrator:
    """Runs a DAG, persisting every transition, gated by the Sentry.

    Usage:
        orch = Orchestrator(guard=guard, llm=client)
        orch.register("research", my_async_handler)
        dag = DAG([TaskNode(id="a", kind="research", prompt="...")])
        await orch.run(dag, goal="find things")
    """

    def __init__(
        self,
        *,
        config: Optional[OrchestratorConfig] = None,
        guard: Optional[RiskGuard] = None,
        llm: Optional[LocalLLMClient] = None,
        memory: Optional[Any] = None,
        fetcher: Optional[Any] = None,
    ) -> None:
        self.config = config or OrchestratorConfig()
        self.guard = guard or RiskGuard(RiskGuardConfig())
        self.llm = llm
        self.memory = memory
        self.fetcher = fetcher
        self.handlers: dict[str, NodeHandler] = {}
        self.run_id: Optional[str] = None
        self._lock = threading.RLock()
        self._halted = False
        self._halt_reason = ""

        # Shares risk_guard's connection opener so both databases degrade the same
        # way on a filesystem that refuses WAL. See open_durable_connection.
        self._conn = open_durable_connection(self.config.db_path)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self.journal_mode = journal_mode_of(self._conn)

        self._register_builtin_handlers()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> "Orchestrator":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- handler registry --------------------------------------------------

    def register(self, kind: str, handler: NodeHandler) -> None:
        """Bind a node kind to an async handler."""
        if not callable(handler):
            raise TypeError("handler must be callable")
        self.handlers[kind] = handler

    def _register_builtin_handlers(self) -> None:
        """Default handlers for the personas that need no bespoke logic."""

        async def llm_handler(orch: "Orchestrator", node: TaskNode) -> HandlerResult:
            if orch.llm is None:
                raise RuntimeError(
                    f"node {node.id!r} of kind {node.kind!r} needs an LLM client, "
                    f"but none was provided to the Orchestrator"
                )
            persona = Persona(node.persona)
            blocks: list[str] = []
            if orch.memory is not None:
                try:
                    blocks = orch.memory.context_blocks(node.prompt, budget_chars=3000)
                except Exception:
                    blocks = []  # retrieval is an optimization, not a requirement
            # The model call is sync; keep the event loop free for other nodes.
            out = await asyncio.to_thread(
                orch.llm.complete, persona, node.prompt, context_blocks=blocks or None
            )
            return HandlerResult(
                output=out.get("text", ""),
                cost=0,
                meta={
                    "prompt_tokens": out.get("prompt_tokens", 0),
                    "completion_tokens": out.get("completion_tokens", 0),
                    "context_blocks": len(blocks),
                },
            )

        async def fetch_handler(orch: "Orchestrator", node: TaskNode) -> HandlerResult:
            if orch.fetcher is None:
                raise RuntimeError(f"node {node.id!r} needs a fetcher; none provided")
            url = node.meta.get("url") or node.prompt.strip()
            if not url:
                raise ValueError(f"node {node.id!r} of kind 'fetch' has no url")
            res = await asyncio.to_thread(orch.fetcher.fetch, url)
            if not res.ok:
                raise RuntimeError(f"fetch failed: {res.error}")
            return HandlerResult(
                output=res.as_context_block(),
                cost=0,
                meta={"final_url": res.final_url, "bytes": res.bytes_read},
            )

        async def noop_handler(orch: "Orchestrator", node: TaskNode) -> HandlerResult:
            return HandlerResult(output=node.prompt, cost=node.projected_cost)

        for kind in ("research", "design", "copy", "plan", "llm"):
            self.handlers[kind] = llm_handler
        self.handlers["fetch"] = fetch_handler
        self.handlers["noop"] = noop_handler

    # -- persistence -------------------------------------------------------

    def _persist_node(self, node: TaskNode) -> None:
        assert self.run_id is not None
        with self._lock:
            self._conn.execute(
                "INSERT INTO nodes(run_id, id, kind, prompt, persona, depends_on, state, "
                "result, error, attempts, max_attempts, projected_cost, actual_cost, "
                "started_at, finished_at, meta) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(run_id, id) DO UPDATE SET "
                "state=excluded.state, result=excluded.result, error=excluded.error, "
                "attempts=excluded.attempts, actual_cost=excluded.actual_cost, "
                "started_at=excluded.started_at, finished_at=excluded.finished_at, "
                "meta=excluded.meta",
                (
                    self.run_id,
                    node.id,
                    node.kind,
                    node.prompt,
                    node.persona,
                    json.dumps(node.depends_on),
                    node.state.value,
                    node.result,
                    node.error,
                    node.attempts,
                    node.max_attempts,
                    node.projected_cost,
                    node.actual_cost,
                    node.started_at,
                    node.finished_at,
                    json.dumps(node.meta, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def _transition(self, node: TaskNode, to: NodeState, note: str = "") -> None:
        """Move a node to a new state and commit before returning.

        The commit is the point: a crash after this line leaves a graph that can
        be resumed, not one whose state is a guess.
        """
        assert self.run_id is not None
        frm = node.state
        node.state = to
        if to is NodeState.RUNNING and node.started_at is None:
            node.started_at = _now_iso()
        if to.terminal:
            node.finished_at = _now_iso()
        with self._lock:
            self._conn.execute(
                "INSERT INTO transitions(ts, run_id, node_id, from_state, to_state, note) "
                "VALUES (?,?,?,?,?,?)",
                (_now_iso(), self.run_id, node.id, frm.value, to.value, note),
            )
            self._conn.commit()
        self._persist_node(node)

    def _touch_run(self, status: Optional[str] = None) -> None:
        if self.run_id is None:
            return
        with self._lock:
            if status:
                self._conn.execute(
                    "UPDATE runs SET updated_at=?, status=? WHERE run_id=?",
                    (_now_iso(), status, self.run_id),
                )
            else:
                self._conn.execute(
                    "UPDATE runs SET updated_at=? WHERE run_id=?", (_now_iso(), self.run_id)
                )
            self._conn.commit()

    def create_run(self, goal: str = "") -> str:
        run_id = uuid.uuid4().hex[:16]
        with self._lock:
            self._conn.execute(
                "INSERT INTO runs(run_id, goal, created_at, updated_at, status) "
                "VALUES (?,?,?,?,'RUNNING')",
                (run_id, goal, _now_iso(), _now_iso()),
            )
            self._conn.commit()
        self.run_id = run_id
        return run_id

    def load_dag(self, run_id: str) -> DAG:
        """Rebuild a DAG from persisted state.

        Nodes left in RUNNING are reset to READY: the process that owned them is
        gone, so they did not finish, and leaving them RUNNING would deadlock the
        resumed graph.
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM nodes WHERE run_id=? ORDER BY id", (run_id,)
            ).fetchall()
        if not rows:
            raise KeyError(f"no such run: {run_id}")
        dag = DAG()
        for r in rows:
            state = NodeState(r["state"])
            if state is NodeState.RUNNING:
                state = NodeState.READY
            dag.add(
                TaskNode(
                    id=r["id"],
                    kind=r["kind"],
                    prompt=r["prompt"],
                    persona=r["persona"],
                    depends_on=json.loads(r["depends_on"]),
                    state=state,
                    result=r["result"],
                    error=r["error"],
                    attempts=r["attempts"],
                    max_attempts=r["max_attempts"],
                    projected_cost=r["projected_cost"],
                    actual_cost=r["actual_cost"],
                    started_at=r["started_at"],
                    finished_at=r["finished_at"],
                    meta=json.loads(r["meta"]),
                )
            )
        self.run_id = run_id
        return dag

    def transitions(self, run_id: Optional[str] = None, limit: int = 500) -> list[dict[str, Any]]:
        rid = run_id or self.run_id
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM transitions WHERE run_id=? ORDER BY id LIMIT ?",
                (rid, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def runs(self, limit: int = 20) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- halting -----------------------------------------------------------

    @property
    def halted(self) -> bool:
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

    def halt(self, reason: str) -> None:
        """Stop dispatching. In-flight nodes are allowed to finish or time out."""
        self._halted = True
        self._halt_reason = reason

    # -- execution ---------------------------------------------------------

    async def _execute_node(self, dag: DAG, node: TaskNode) -> None:
        """Gate, run, charge, record. The only path by which a node executes."""
        verdict: Verdict = await asyncio.to_thread(
            self.guard.pre_check, node.projected_cost, node_id=node.id
        )
        if not verdict.allowed:
            self._transition(node, NodeState.BLOCKED, f"sentry: {verdict.reason}")
            node.error = f"blocked by sentry: {verdict.reason}"
            self._persist_node(node)
            if self.config.halt_on_kill_switch and "kill switch" in verdict.reason.lower():
                self.halt(verdict.reason)
            return

        handler = self.handlers.get(node.kind)
        if handler is None:
            self._transition(node, NodeState.FAILED, "no handler")
            node.error = (
                f"no handler registered for kind {node.kind!r}; "
                f"known kinds: {sorted(self.handlers)}"
            )
            self._persist_node(node)
            return

        node.attempts += 1
        self._transition(node, NodeState.RUNNING, f"attempt {node.attempts}")

        try:
            result = await asyncio.wait_for(
                handler(self, node), timeout=self.config.node_timeout_s
            )
        except asyncio.TimeoutError:
            await self._handle_failure(
                dag, node, f"timed out after {self.config.node_timeout_s:.0f}s"
            )
            return
        except asyncio.CancelledError:
            self._transition(node, NodeState.CANCELLED, "cancelled")
            raise
        except Exception as exc:  # noqa: BLE001 - a handler fault is node-scoped
            await self._handle_failure(dag, node, f"{type(exc).__name__}: {exc}")
            return

        if not isinstance(result, HandlerResult):
            await self._handle_failure(
                dag,
                node,
                f"handler returned {type(result).__name__}, expected HandlerResult",
            )
            return

        # Charge what it actually cost. A refusal here is a real failure: the
        # work happened but cannot be paid for, and pretending otherwise would
        # let the ledger drift from reality.
        try:
            if result.cost:
                await asyncio.to_thread(
                    self.guard.post_charge, result.cost, node_id=node.id, label=node.kind
                )
        except Exception as exc:  # noqa: BLE001
            node.error = f"post-charge refused: {exc}"
            self._transition(node, NodeState.BLOCKED, node.error)
            self._persist_node(node)
            self.halt(f"ledger refused a completed node's cost: {exc}")
            return

        node.result = result.output
        node.actual_cost = result.cost
        node.error = None
        node.meta.update(result.meta or {})
        self._transition(node, NodeState.DONE, "ok")

    async def _handle_failure(self, dag: DAG, node: TaskNode, reason: str) -> None:
        """Retry with backoff, or mark FAILED once attempts are exhausted."""
        node.error = reason
        if node.attempts < node.max_attempts:
            self._transition(
                node, NodeState.READY, f"retry {node.attempts}/{node.max_attempts}: {reason}"
            )
            await asyncio.sleep(self.config.retry_backoff_s * node.attempts)
        else:
            self._transition(node, NodeState.FAILED, reason)
        self._persist_node(node)

    async def run(self, dag: DAG, goal: str = "", run_id: Optional[str] = None) -> DAG:
        """Execute a DAG to completion. Returns the same DAG, mutated.

        Raises CycleError before doing any work if the graph is invalid - better
        to reject at submission than to discover it halfway through.
        """
        dag.validate()
        if run_id is None:
            if self.run_id is None:
                self.create_run(goal)
        else:
            self.run_id = run_id

        for node in dag.nodes.values():
            self._persist_node(node)

        sem = asyncio.Semaphore(self.config.max_concurrency)
        in_flight: dict[str, asyncio.Task] = {}

        async def guarded(node: TaskNode) -> None:
            async with sem:
                await self._execute_node(dag, node)

        while not dag.complete:
            if self._halted:
                for n in dag.nodes.values():
                    if not n.state.terminal and n.id not in in_flight:
                        self._transition(n, NodeState.CANCELLED, f"halted: {self._halt_reason}")
                break

            # Cascade: a node whose ancestor failed can never run.
            for n in dag.blocked_by_failure():
                if n.id not in in_flight:
                    self._transition(n, NodeState.BLOCKED, "upstream dependency did not complete")

            ready = [n for n in dag.ready_nodes() if n.id not in in_flight]
            for n in ready:
                if n.state is NodeState.PENDING:
                    self._transition(n, NodeState.READY, "dependencies satisfied")
                in_flight[n.id] = asyncio.create_task(guarded(n))

            if not in_flight:
                # Nothing running and nothing ready: any non-terminal node is
                # unreachable. Mark and stop rather than spin.
                stuck = [n for n in dag.nodes.values() if not n.state.terminal]
                if stuck:
                    for n in stuck:
                        self._transition(n, NodeState.BLOCKED, "unreachable: no runnable path")
                break

            done, _ = await asyncio.wait(
                in_flight.values(), return_when=asyncio.FIRST_COMPLETED
            )
            finished_ids = [nid for nid, t in in_flight.items() if t in done]
            for nid in finished_ids:
                task = in_flight.pop(nid)
                exc = task.exception() if not task.cancelled() else None
                if exc is not None:
                    # A fault escaped _execute_node; record it rather than
                    # letting the whole run die silently.
                    n = dag.nodes[nid]
                    n.error = f"scheduler-level fault: {type(exc).__name__}: {exc}"
                    if not n.state.terminal:
                        self._transition(n, NodeState.FAILED, n.error)

        for t in in_flight.values():
            t.cancel()
        if in_flight:
            await asyncio.gather(*in_flight.values(), return_exceptions=True)

        counts = dag.summary()
        status = "COMPLETE" if counts.get(NodeState.DONE.value, 0) == len(dag.nodes) else "PARTIAL"
        if self._halted:
            status = "HALTED"
        self._touch_run(status)
        return dag

    # -- planning ----------------------------------------------------------

    async def plan(self, goal: str) -> DAG:
        """Ask the Orchestrator persona to decompose a goal into a DAG.

        Returns a single-node fallback DAG if no LLM is attached or the model's
        JSON is unusable - a degraded plan beats a crash, and the reason lands in
        the node's meta.
        """
        fallback = DAG(
            [
                TaskNode(
                    id="root",
                    kind="research",
                    prompt=goal,
                    persona=Persona.RESEARCHER.value,
                    meta={"planning": "fallback single node"},
                )
            ]
        )
        if self.llm is None:
            return fallback

        instruction = (
            f"Goal: {goal}\n\n"
            "Decompose this into 2-6 atomic tasks. Reply with JSON only, shaped as:\n"
            '{"nodes": [{"id": "n1", "kind": "research|design|copy|fetch", '
            '"persona": "researcher|designer|copywriter", "prompt": "...", '
            '"depends_on": []}]}\n'
            "Only one model is resident in VRAM, so tasks run serially. Prefer a "
            "short dependency chain over wide fan-out."
        )
        try:
            data = await asyncio.to_thread(
                self.llm.complete_json, Persona.ORCHESTRATOR, instruction
            )
        except Exception as exc:  # noqa: BLE001
            fallback.nodes["root"].meta["planning_error"] = str(exc)
            return fallback

        raw_nodes = (data or {}).get("nodes") if isinstance(data, dict) else None
        if not isinstance(raw_nodes, list) or not raw_nodes:
            fallback.nodes["root"].meta["planning_error"] = "model returned no nodes"
            return fallback

        dag = DAG()
        for i, item in enumerate(raw_nodes):
            if not isinstance(item, dict):
                continue
            nid = str(item.get("id") or f"n{i+1}")
            kind = str(item.get("kind") or "research")
            persona_raw = str(item.get("persona") or "researcher")
            try:
                persona = Persona(persona_raw).value
            except ValueError:
                persona = Persona.RESEARCHER.value
            deps = [str(d) for d in (item.get("depends_on") or [])]
            try:
                dag.add(
                    TaskNode(
                        id=nid,
                        kind=kind,
                        prompt=str(item.get("prompt") or goal),
                        persona=persona,
                        depends_on=deps,
                    )
                )
            except ValueError:
                continue  # duplicate id from the model; skip it

        if not dag.nodes:
            fallback.nodes["root"].meta["planning_error"] = "no usable nodes parsed"
            return fallback

        # Drop dangling dependencies rather than rejecting the whole plan.
        known = set(dag.nodes)
        for n in dag.nodes.values():
            n.depends_on = [d for d in n.depends_on if d in known and d != n.id]
        try:
            dag.validate()
        except CycleError:
            # A cyclic plan is unusable; flatten it into a chain in id order.
            order = sorted(dag.nodes)
            for idx, nid in enumerate(order):
                dag.nodes[nid].depends_on = [order[idx - 1]] if idx else []
        return dag

    # -- reporting ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "halted": self._halted,
            "halt_reason": self._halt_reason,
            "db_path": str(self.config.db_path),
            "max_concurrency": self.config.max_concurrency,
            "registered_kinds": sorted(self.handlers),
            "guard": self.guard.status(),
        }


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

    tmp = Path(tempfile.mkdtemp(prefix="orch_selftest_"))
    counter = {"n": 0}

    def fresh_orch(**kw: Any) -> Orchestrator:
        counter["n"] += 1
        i = counter["n"]
        guard = RiskGuard(
            RiskGuardConfig(
                db_path=tmp / f"guard{i}.db",
                kill_file=tmp / f"kill{i}",
                allow_unmeasurable_hardware=True,
                budget_cap_minor=10_000,
            )
        )
        guard.credit(10_000)
        cfg = OrchestratorConfig(db_path=tmp / f"engine{i}.db", retry_backoff_s=0.0, **kw)
        return Orchestrator(config=cfg, guard=guard)

    async def ok_handler(orch: Orchestrator, node: TaskNode) -> HandlerResult:
        return HandlerResult(output=f"did {node.id}", cost=node.projected_cost)

    print("orchestrator.py selftest")
    print("-" * 60)

    # --- DAG structure ---

    def t_topo_order_linear() -> None:
        dag = DAG(
            [
                TaskNode(id="c", kind="noop", depends_on=["b"]),
                TaskNode(id="b", kind="noop", depends_on=["a"]),
                TaskNode(id="a", kind="noop"),
            ]
        )
        assert dag.topological_order() == ["a", "b", "c"], dag.topological_order()

    def t_cycle_rejected() -> None:
        dag = DAG(
            [
                TaskNode(id="a", kind="noop", depends_on=["b"]),
                TaskNode(id="b", kind="noop", depends_on=["a"]),
            ]
        )
        try:
            dag.validate()
            raise AssertionError("expected CycleError")
        except CycleError:
            pass

    def t_self_loop_rejected() -> None:
        dag = DAG([TaskNode(id="a", kind="noop", depends_on=["a"])])
        try:
            dag.validate()
            raise AssertionError("expected CycleError")
        except CycleError:
            pass

    def t_unknown_dependency_rejected() -> None:
        dag = DAG([TaskNode(id="a", kind="noop", depends_on=["ghost"])])
        try:
            dag.validate()
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def t_duplicate_id_rejected() -> None:
        dag = DAG([TaskNode(id="a", kind="noop")])
        try:
            dag.add(TaskNode(id="a", kind="noop"))
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def t_ready_respects_dependencies() -> None:
        dag = DAG(
            [
                TaskNode(id="a", kind="noop"),
                TaskNode(id="b", kind="noop", depends_on=["a"]),
            ]
        )
        assert [n.id for n in dag.ready_nodes()] == ["a"]
        dag.nodes["a"].state = NodeState.DONE
        assert [n.id for n in dag.ready_nodes()] == ["b"]

    def t_diamond_order_valid() -> None:
        dag = DAG(
            [
                TaskNode(id="a", kind="noop"),
                TaskNode(id="b", kind="noop", depends_on=["a"]),
                TaskNode(id="c", kind="noop", depends_on=["a"]),
                TaskNode(id="d", kind="noop", depends_on=["b", "c"]),
            ]
        )
        order = dag.topological_order()
        assert order.index("a") < order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    # --- execution ---

    def t_runs_linear_dag() -> None:
        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", ok_handler)
            dag = DAG(
                [
                    TaskNode(id="a", kind="t"),
                    TaskNode(id="b", kind="t", depends_on=["a"]),
                ]
            )
            await orch.run(dag, goal="test")
            assert dag.nodes["a"].state is NodeState.DONE, dag.nodes["a"].as_dict()
            assert dag.nodes["b"].state is NodeState.DONE
            assert dag.nodes["b"].result == "did b"
            orch.close()

        asyncio.run(go())

    def t_execution_respects_order() -> None:
        seen: list[str] = []

        async def recording(orch: Orchestrator, node: TaskNode) -> HandlerResult:
            seen.append(node.id)
            return HandlerResult(output="x")

        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", recording)
            dag = DAG(
                [
                    TaskNode(id="first", kind="t"),
                    TaskNode(id="second", kind="t", depends_on=["first"]),
                    TaskNode(id="third", kind="t", depends_on=["second"]),
                ]
            )
            await orch.run(dag)
            assert seen == ["first", "second", "third"], seen
            orch.close()

        asyncio.run(go())

    def t_failure_cascades_to_blocked() -> None:
        async def boom(orch: Orchestrator, node: TaskNode) -> HandlerResult:
            raise RuntimeError("intentional")

        async def go() -> None:
            orch = fresh_orch()
            orch.register("bad", boom)
            orch.register("good", ok_handler)
            dag = DAG(
                [
                    TaskNode(id="a", kind="bad", max_attempts=1),
                    TaskNode(id="b", kind="good", depends_on=["a"]),
                ]
            )
            await orch.run(dag)
            assert dag.nodes["a"].state is NodeState.FAILED, dag.nodes["a"].state
            assert dag.nodes["b"].state is NodeState.BLOCKED, dag.nodes["b"].state
            orch.close()

        asyncio.run(go())

    def t_retry_then_succeed() -> None:
        tries = {"n": 0}

        async def flaky(orch: Orchestrator, node: TaskNode) -> HandlerResult:
            tries["n"] += 1
            if tries["n"] < 3:
                raise RuntimeError("not yet")
            return HandlerResult(output="finally")

        async def go() -> None:
            orch = fresh_orch()
            orch.register("flaky", flaky)
            dag = DAG([TaskNode(id="a", kind="flaky", max_attempts=3)])
            await orch.run(dag)
            assert dag.nodes["a"].state is NodeState.DONE, dag.nodes["a"].as_dict()
            assert dag.nodes["a"].attempts == 3, dag.nodes["a"].attempts
            orch.close()

        asyncio.run(go())

    def t_retry_exhausted_fails() -> None:
        async def always_bad(orch: Orchestrator, node: TaskNode) -> HandlerResult:
            raise RuntimeError("always")

        async def go() -> None:
            orch = fresh_orch()
            orch.register("bad", always_bad)
            dag = DAG([TaskNode(id="a", kind="bad", max_attempts=2)])
            await orch.run(dag)
            assert dag.nodes["a"].state is NodeState.FAILED
            assert dag.nodes["a"].attempts == 2, dag.nodes["a"].attempts
            orch.close()

        asyncio.run(go())

    def t_missing_handler_fails_node() -> None:
        async def go() -> None:
            orch = fresh_orch()
            dag = DAG([TaskNode(id="a", kind="nonexistent-kind")])
            await orch.run(dag)
            assert dag.nodes["a"].state is NodeState.FAILED
            assert "no handler" in (dag.nodes["a"].error or "").lower()
            orch.close()

        asyncio.run(go())

    def t_timeout_is_a_failure() -> None:
        async def slow(orch: Orchestrator, node: TaskNode) -> HandlerResult:
            await asyncio.sleep(5)
            return HandlerResult(output="never")

        async def go() -> None:
            orch = fresh_orch(node_timeout_s=0.05)
            orch.register("slow", slow)
            dag = DAG([TaskNode(id="a", kind="slow", max_attempts=1)])
            await orch.run(dag)
            assert dag.nodes["a"].state is NodeState.FAILED
            assert "timed out" in (dag.nodes["a"].error or "")
            orch.close()

        asyncio.run(go())

    def t_bad_return_type_fails() -> None:
        async def wrong(orch: Orchestrator, node: TaskNode) -> Any:
            return "a bare string, not HandlerResult"

        async def go() -> None:
            orch = fresh_orch()
            orch.register("wrong", wrong)
            dag = DAG([TaskNode(id="a", kind="wrong", max_attempts=1)])
            await orch.run(dag)
            assert dag.nodes["a"].state is NodeState.FAILED
            assert "HandlerResult" in (dag.nodes["a"].error or "")
            orch.close()

        asyncio.run(go())

    # --- sentry integration ---

    def t_sentry_blocks_unaffordable_node() -> None:
        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", ok_handler)
            dag = DAG([TaskNode(id="a", kind="t", projected_cost=999_999)])
            await orch.run(dag)
            assert dag.nodes["a"].state is NodeState.BLOCKED, dag.nodes["a"].state
            assert "sentry" in (dag.nodes["a"].error or "").lower()
            orch.close()

        asyncio.run(go())

    def t_kill_switch_halts_run() -> None:
        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", ok_handler)
            orch.guard.trip_kill_switch("test halt")
            dag = DAG(
                [
                    TaskNode(id="a", kind="t"),
                    TaskNode(id="b", kind="t", depends_on=["a"]),
                ]
            )
            await orch.run(dag)
            assert orch.halted, "run must halt on a tripped kill switch"
            assert dag.nodes["a"].state is NodeState.BLOCKED
            assert dag.nodes["b"].state in (NodeState.CANCELLED, NodeState.BLOCKED)
            orch.close()

        asyncio.run(go())

    def t_costs_are_charged() -> None:
        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", ok_handler)
            start = orch.guard.balance()
            dag = DAG(
                [
                    TaskNode(id="a", kind="t", projected_cost=100),
                    TaskNode(id="b", kind="t", projected_cost=250, depends_on=["a"]),
                ]
            )
            await orch.run(dag)
            assert orch.guard.balance() == start - 350, orch.guard.balance()
            orch.close()

        asyncio.run(go())

    def t_balance_never_negative_through_dag() -> None:
        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", ok_handler)
            nodes = [TaskNode(id=f"n{i}", kind="t", projected_cost=3_000) for i in range(8)]
            await orch.run(DAG(nodes))
            assert orch.guard.balance() >= 0, orch.guard.balance()
            orch.close()

        asyncio.run(go())

    # --- persistence ---

    def t_transitions_recorded() -> None:
        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", ok_handler)
            await orch.run(DAG([TaskNode(id="a", kind="t")]))
            trans = orch.transitions()
            states = [t["to_state"] for t in trans]
            assert "RUNNING" in states and "DONE" in states, states
            orch.close()

        asyncio.run(go())

    def t_state_survives_reload() -> None:
        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", ok_handler)
            dag = DAG(
                [
                    TaskNode(id="a", kind="t"),
                    TaskNode(id="b", kind="t", depends_on=["a"]),
                ]
            )
            await orch.run(dag, goal="persist me")
            rid = orch.run_id
            assert rid
            db = orch.config.db_path
            orch.close()

            orch2 = Orchestrator(
                config=OrchestratorConfig(db_path=db),
                guard=RiskGuard(
                    RiskGuardConfig(
                        db_path=tmp / "g_reload.db",
                        kill_file=tmp / "k_reload",
                        allow_unmeasurable_hardware=True,
                    )
                ),
            )
            reloaded = orch2.load_dag(rid)
            assert set(reloaded.nodes) == {"a", "b"}
            assert reloaded.nodes["a"].state is NodeState.DONE
            assert reloaded.nodes["a"].result == "did a", reloaded.nodes["a"].result
            assert reloaded.nodes["b"].depends_on == ["a"]
            orch2.close()

        asyncio.run(go())

    def t_running_resets_to_ready_on_reload() -> None:
        # Simulates a hard kill: a node stuck in RUNNING must not deadlock resume.
        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", ok_handler)
            orch.create_run("interrupted")
            node = TaskNode(id="a", kind="t", state=NodeState.RUNNING)
            orch._persist_node(node)
            rid = orch.run_id
            assert rid
            db = orch.config.db_path
            orch.close()

            orch2 = Orchestrator(
                config=OrchestratorConfig(db_path=db),
                guard=RiskGuard(
                    RiskGuardConfig(
                        db_path=tmp / "g_res.db",
                        kill_file=tmp / "k_res",
                        allow_unmeasurable_hardware=True,
                    )
                ),
            )
            reloaded = orch2.load_dag(rid)
            assert reloaded.nodes["a"].state is NodeState.READY, reloaded.nodes["a"].state
            orch2.close()

        asyncio.run(go())

    def t_resume_finishes_partial_dag() -> None:
        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", ok_handler)
            orch.create_run("resume")
            rid = orch.run_id
            db = orch.config.db_path
            done = TaskNode(id="a", kind="t", state=NodeState.DONE, result="did a")
            todo = TaskNode(id="b", kind="t", depends_on=["a"])
            orch._persist_node(done)
            orch._persist_node(todo)
            orch.close()

            guard = RiskGuard(
                RiskGuardConfig(
                    db_path=tmp / "g_fin.db",
                    kill_file=tmp / "k_fin",
                    allow_unmeasurable_hardware=True,
                )
            )
            guard.credit(5_000)
            orch2 = Orchestrator(config=OrchestratorConfig(db_path=db), guard=guard)
            orch2.register("t", ok_handler)
            dag = orch2.load_dag(rid)  # type: ignore[arg-type]
            await orch2.run(dag, run_id=rid)
            assert dag.nodes["b"].state is NodeState.DONE, dag.nodes["b"].as_dict()
            orch2.close()

        asyncio.run(go())

    def t_unknown_run_raises() -> None:
        orch = fresh_orch()
        try:
            orch.load_dag("no-such-run")
            raise AssertionError("expected KeyError")
        except KeyError:
            pass
        finally:
            orch.close()

    def t_run_recorded_with_status() -> None:
        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", ok_handler)
            await orch.run(DAG([TaskNode(id="a", kind="t")]), goal="statuscheck")
            runs = orch.runs()
            assert runs and runs[0]["status"] == "COMPLETE", runs
            orch.close()

        asyncio.run(go())

    # --- planning ---

    def t_plan_without_llm_is_single_node() -> None:
        async def go() -> None:
            orch = fresh_orch()
            dag = await orch.plan("do a thing")
            assert len(dag.nodes) == 1
            assert "root" in dag.nodes
            orch.close()

        asyncio.run(go())

    def t_plan_parses_model_json() -> None:
        class FakeLLM:
            def complete_json(self, persona: Any, prompt: str, **kw: Any) -> Any:
                return {
                    "nodes": [
                        {"id": "r1", "kind": "research", "persona": "researcher", "prompt": "a"},
                        {
                            "id": "d1",
                            "kind": "design",
                            "persona": "designer",
                            "prompt": "b",
                            "depends_on": ["r1"],
                        },
                    ]
                }

        async def go() -> None:
            orch = fresh_orch()
            orch.llm = FakeLLM()  # type: ignore[assignment]
            dag = await orch.plan("goal")
            assert set(dag.nodes) == {"r1", "d1"}, sorted(dag.nodes)
            assert dag.nodes["d1"].depends_on == ["r1"]
            dag.validate()
            orch.close()

        asyncio.run(go())

    def t_plan_drops_dangling_dependency() -> None:
        class FakeLLM:
            def complete_json(self, persona: Any, prompt: str, **kw: Any) -> Any:
                return {
                    "nodes": [
                        {"id": "a", "kind": "research", "prompt": "x", "depends_on": ["ghost"]}
                    ]
                }

        async def go() -> None:
            orch = fresh_orch()
            orch.llm = FakeLLM()  # type: ignore[assignment]
            dag = await orch.plan("goal")
            assert dag.nodes["a"].depends_on == [], dag.nodes["a"].depends_on
            dag.validate()
            orch.close()

        asyncio.run(go())

    def t_plan_flattens_cyclic_plan() -> None:
        class FakeLLM:
            def complete_json(self, persona: Any, prompt: str, **kw: Any) -> Any:
                return {
                    "nodes": [
                        {"id": "a", "kind": "research", "prompt": "x", "depends_on": ["b"]},
                        {"id": "b", "kind": "research", "prompt": "y", "depends_on": ["a"]},
                    ]
                }

        async def go() -> None:
            orch = fresh_orch()
            orch.llm = FakeLLM()  # type: ignore[assignment]
            dag = await orch.plan("goal")
            dag.validate()  # must not raise
            orch.close()

        asyncio.run(go())

    def t_plan_bad_json_falls_back() -> None:
        class FakeLLM:
            def complete_json(self, persona: Any, prompt: str, **kw: Any) -> Any:
                raise RuntimeError("model returned garbage")

        async def go() -> None:
            orch = fresh_orch()
            orch.llm = FakeLLM()  # type: ignore[assignment]
            dag = await orch.plan("goal")
            assert "root" in dag.nodes
            assert "planning_error" in dag.nodes["root"].meta
            orch.close()

        asyncio.run(go())

    # --- concurrency ---

    def t_default_concurrency_is_one() -> None:
        # The single VRAM slot means serial by default. Guard against drift.
        assert OrchestratorConfig().max_concurrency == 1

    def t_semaphore_serializes_nodes() -> None:
        active = {"now": 0, "peak": 0}

        async def tracker(orch: Orchestrator, node: TaskNode) -> HandlerResult:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
            await asyncio.sleep(0.01)
            active["now"] -= 1
            return HandlerResult(output="x")

        async def go() -> None:
            orch = fresh_orch(max_concurrency=1)
            orch.register("t", tracker)
            # Four independent nodes; with concurrency 1 peak must stay at 1.
            await orch.run(DAG([TaskNode(id=f"n{i}", kind="t") for i in range(4)]))
            assert active["peak"] == 1, f"peak concurrency {active['peak']}, expected 1"
            orch.close()

        asyncio.run(go())

    def t_higher_concurrency_overlaps() -> None:
        active = {"now": 0, "peak": 0}

        async def tracker(orch: Orchestrator, node: TaskNode) -> HandlerResult:
            active["now"] += 1
            active["peak"] = max(active["peak"], active["now"])
            await asyncio.sleep(0.05)
            active["now"] -= 1
            return HandlerResult(output="x")

        async def go() -> None:
            orch = fresh_orch(max_concurrency=3)
            orch.register("t", tracker)
            await orch.run(DAG([TaskNode(id=f"n{i}", kind="t") for i in range(4)]))
            assert active["peak"] > 1, "concurrency 3 should overlap I/O-bound nodes"
            orch.close()

        asyncio.run(go())

    def t_invalid_concurrency_rejected() -> None:
        try:
            OrchestratorConfig(max_concurrency=0)
            raise AssertionError("expected ValueError")
        except ValueError:
            pass

    def t_cyclic_dag_rejected_before_work() -> None:
        async def go() -> None:
            orch = fresh_orch()
            orch.register("t", ok_handler)
            dag = DAG(
                [
                    TaskNode(id="a", kind="t", depends_on=["b"]),
                    TaskNode(id="b", kind="t", depends_on=["a"]),
                ]
            )
            try:
                await orch.run(dag)
                raise AssertionError("expected CycleError")
            except CycleError:
                pass
            orch.close()

        asyncio.run(go())

    def t_status_shape() -> None:
        orch = fresh_orch()
        st = orch.status()
        for k in ("run_id", "halted", "registered_kinds", "guard"):
            assert k in st, f"missing {k}"
        assert "noop" in st["registered_kinds"]
        orch.close()

    def t_builtin_llm_handler_needs_client() -> None:
        async def go() -> None:
            orch = fresh_orch()
            dag = DAG([TaskNode(id="a", kind="research", prompt="q", max_attempts=1)])
            await orch.run(dag)
            assert dag.nodes["a"].state is NodeState.FAILED
            assert "LLM client" in (dag.nodes["a"].error or "")
            orch.close()

        asyncio.run(go())

    def t_builtin_llm_handler_uses_client() -> None:
        class FakeLLM:
            def complete(self, persona: Any, prompt: str, **kw: Any) -> dict[str, Any]:
                return {
                    "text": f"answered: {prompt}",
                    "prompt_tokens": 5,
                    "completion_tokens": 9,
                }

        async def go() -> None:
            orch = fresh_orch()
            orch.llm = FakeLLM()  # type: ignore[assignment]
            dag = DAG([TaskNode(id="a", kind="research", prompt="why?")])
            await orch.run(dag)
            assert dag.nodes["a"].state is NodeState.DONE, dag.nodes["a"].as_dict()
            assert "answered: why?" in (dag.nodes["a"].result or "")
            assert dag.nodes["a"].meta.get("completion_tokens") == 9
            orch.close()

        asyncio.run(go())

    def t_memory_failure_does_not_block_node() -> None:
        class FakeLLM:
            def complete(self, persona: Any, prompt: str, **kw: Any) -> dict[str, Any]:
                return {"text": "ok"}

        class BrokenMemory:
            def context_blocks(self, q: str, budget_chars: int = 0) -> list[str]:
                raise RuntimeError("index corrupt")

        async def go() -> None:
            orch = fresh_orch()
            orch.llm = FakeLLM()  # type: ignore[assignment]
            orch.memory = BrokenMemory()
            dag = DAG([TaskNode(id="a", kind="research", prompt="q")])
            await orch.run(dag)
            assert dag.nodes["a"].state is NodeState.DONE, "retrieval is optional"
            orch.close()

        asyncio.run(go())

    for name, fn in [
        ("topological order is correct", t_topo_order_linear),
        ("cycle rejected", t_cycle_rejected),
        ("self-loop rejected", t_self_loop_rejected),
        ("unknown dependency rejected", t_unknown_dependency_rejected),
        ("duplicate node id rejected", t_duplicate_id_rejected),
        ("ready set respects dependencies", t_ready_respects_dependencies),
        ("diamond DAG orders correctly", t_diamond_order_valid),
        ("runs a linear DAG", t_runs_linear_dag),
        ("execution follows dependency order", t_execution_respects_order),
        ("failure cascades to BLOCKED", t_failure_cascades_to_blocked),
        ("retry then succeed", t_retry_then_succeed),
        ("exhausted retries FAIL", t_retry_exhausted_fails),
        ("missing handler fails node", t_missing_handler_fails_node),
        ("timeout counts as failure", t_timeout_is_a_failure),
        ("bad handler return type fails", t_bad_return_type_fails),
        ("sentry blocks unaffordable node", t_sentry_blocks_unaffordable_node),
        ("kill switch halts the run", t_kill_switch_halts_run),
        ("node costs are charged", t_costs_are_charged),
        ("balance never negative through DAG", t_balance_never_negative_through_dag),
        ("transitions are recorded", t_transitions_recorded),
        ("state survives reload", t_state_survives_reload),
        ("RUNNING resets to READY on reload", t_running_resets_to_ready_on_reload),
        ("resume finishes a partial DAG", t_resume_finishes_partial_dag),
        ("unknown run id raises", t_unknown_run_raises),
        ("run recorded with status", t_run_recorded_with_status),
        ("plan without LLM is single node", t_plan_without_llm_is_single_node),
        ("plan parses model JSON", t_plan_parses_model_json),
        ("plan drops dangling dependency", t_plan_drops_dangling_dependency),
        ("plan flattens a cyclic plan", t_plan_flattens_cyclic_plan),
        ("plan falls back on bad JSON", t_plan_bad_json_falls_back),
        ("default concurrency is 1", t_default_concurrency_is_one),
        ("semaphore serializes nodes", t_semaphore_serializes_nodes),
        ("higher concurrency overlaps", t_higher_concurrency_overlaps),
        ("invalid concurrency rejected", t_invalid_concurrency_rejected),
        ("cyclic DAG rejected before work", t_cyclic_dag_rejected_before_work),
        ("status has expected keys", t_status_shape),
        ("builtin llm handler needs a client", t_builtin_llm_handler_needs_client),
        ("builtin llm handler uses client", t_builtin_llm_handler_uses_client),
        ("broken memory does not block node", t_memory_failure_does_not_block_node),
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


async def _demo() -> int:
    """Run a small DAG with no model attached, to show the machinery working."""
    import tempfile

    tmp = Path(tempfile.mkdtemp(prefix="orch_demo_"))
    guard = RiskGuard(
        RiskGuardConfig(
            db_path=tmp / "guard.db",
            kill_file=tmp / "kill",
            budget_cap_minor=10_000,
            allow_unmeasurable_hardware=True,
        )
    )
    guard.credit(10_000, label="demo funding")
    orch = Orchestrator(
        config=OrchestratorConfig(db_path=tmp / "engine.db"), guard=guard
    )

    async def work(o: Orchestrator, node: TaskNode) -> HandlerResult:
        await asyncio.sleep(0.05)
        return HandlerResult(output=f"[{node.id}] {node.prompt}", cost=node.projected_cost)

    orch.register("demo", work)
    dag = DAG(
        [
            TaskNode(id="gather", kind="demo", prompt="collect sources", projected_cost=100),
            TaskNode(
                id="draft", kind="demo", prompt="write copy", depends_on=["gather"], projected_cost=250
            ),
            TaskNode(
                id="design", kind="demo", prompt="build layout", depends_on=["gather"], projected_cost=180
            ),
            TaskNode(
                id="review",
                kind="demo",
                prompt="final check",
                depends_on=["draft", "design"],
                projected_cost=75,
            ),
        ]
    )
    print(f"run id: {orch.create_run('demo goal')}")
    print(f"order : {' -> '.join(dag.topological_order())}\n")
    await orch.run(dag)

    for nid in dag.topological_order():
        n = dag.nodes[nid]
        print(f"  {n.state.value:<9} {n.id:<8} cost={n.actual_cost:<5} {n.result or n.error or ''}")
    print(f"\nsummary: {dag.summary()}")
    print(f"balance: {guard.balance()} of {guard.config.budget_cap_minor} (spent {guard.spent_total()})")
    orch.close()
    guard.close()
    import shutil as _sh

    _sh.rmtree(tmp, ignore_errors=True)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Multi-agent DAG orchestrator")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--demo", action="store_true", help="run a small DAG with stub handlers")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--list-runs", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.demo:
        return asyncio.run(_demo())
    if args.list_runs:
        orch = Orchestrator(config=OrchestratorConfig(db_path=Path(args.db)))
        try:
            for r in orch.runs():
                print(f"{r['run_id']}  {r['status']:<9} {r['created_at']}  {r['goal'][:50]}")
            return 0
        finally:
            orch.close()
    if args.status:
        orch = Orchestrator(config=OrchestratorConfig(db_path=Path(args.db)))
        try:
            print(json.dumps(orch.status(), indent=2, ensure_ascii=False))
            return 0
        finally:
            orch.close()
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
