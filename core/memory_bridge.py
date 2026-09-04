#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_bridge.py - adapter onto F:/KyThuatNenAIMoi/core/agent_memory.py.

Why an adapter instead of a new memory layer
--------------------------------------------
The directive called for Qdrant behind a Docker-hosted crawler. Neither exists on
this machine, and `agent_memory.py` in the reference workspace already implements
what Qdrant would have provided: SQLite FTS5 keyword search, a vector index with
a documented embedding-backend fallback chain, and Reciprocal Rank Fusion to
merge the two rankings - with 24/24 passing self-tests. Adding Qdrant would
duplicate working code and introduce a service dependency for no measured gain.

So this module is thin on purpose. It does three things:

  1. Imports `AgentMemory` from the read-only reference workspace without
     requiring it to be installed as a package.
  2. Degrades to a NullMemory when that import fails, so the swarm runs (with
     retrieval disabled and a loud warning) rather than refusing to start.
  3. Keeps ALL writes inside F:/he_thong_da_tac_tu - the index database lives in
     `state/`, never in the reference workspace, which is read-only by policy.

On the "80% token reduction" claim
----------------------------------
`agent_memory.py` returns a measured `saving` ratio per query and its own
docstring warns against quoting 80% as a given. `build_context()` here passes
that number through unchanged. Read it; do not assume it.

CLI:
    python core/memory_bridge.py --selftest
    python core/memory_bridge.py --index F:/he_thong_da_tac_tu
    python core/memory_bridge.py --query "VRAM budget"
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Optional

__all__ = ["MemoryBridge", "NullMemory", "MemoryHit", "load_agent_memory_class"]

REPO_ROOT = Path(__file__).resolve().parent.parent
REFERENCE_ROOT = Path("F:/KyThuatNenAIMoi")
DEFAULT_INDEX_DB = REPO_ROOT / "state" / "swarm_memory.db"

# Candidate locations for the reference module. The Linux sandbox mount is
# included so this is testable off-Windows.
_AGENT_MEMORY_CANDIDATES = [
    REFERENCE_ROOT / "core" / "agent_memory.py",
    Path("/sessions/blissful-brave-lovelace/mnt/KyThuatNenAIMoi/core/agent_memory.py"),
]


@dataclass
class MemoryHit:
    """One retrieval result, normalized across backends."""

    path: str
    score: float
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"path": self.path, "score": self.score, "text": self.text}


def load_agent_memory_class(
    extra_candidates: Optional[list[Path]] = None,
) -> tuple[Optional[type], Optional[str]]:
    """Import AgentMemory by file path. Returns (class, error_message).

    Loaded by path rather than by `import` because the reference workspace is not
    on sys.path and must not be modified to make it importable.
    """
    candidates = list(extra_candidates or []) + _AGENT_MEMORY_CANDIDATES
    tried: list[str] = []
    for cand in candidates:
        tried.append(str(cand))
        try:
            if not cand.is_file():
                continue
        except OSError:
            continue
        try:
            spec = importlib.util.spec_from_file_location("_ref_agent_memory", cand)
            if spec is None or spec.loader is None:
                continue
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            cls = getattr(module, "AgentMemory", None)
            if cls is None:
                return None, f"{cand} has no AgentMemory class"
            return cls, None
        except Exception as exc:  # noqa: BLE001 - any import failure degrades gracefully
            return None, f"failed to load {cand}: {type(exc).__name__}: {exc}"
    return None, "agent_memory.py not found. Tried:\n  " + "\n  ".join(tried)


class NullMemory:
    """Stand-in when AgentMemory is unavailable.

    Returns nothing and says so. It exists so a missing reference workspace
    degrades retrieval instead of halting the swarm - but `available` is False
    and `reason` explains why, so callers can surface it.
    """

    available = False

    def __init__(self, reason: str) -> None:
        self.reason = reason

    def index_dir(self, *a: Any, **kw: Any) -> int:
        return 0

    def search(self, query: str, k: int = 5) -> list[MemoryHit]:
        return []

    def build_context(self, query: str, budget_chars: int = 4000) -> str:
        return ""


class MemoryBridge:
    """Semantic retrieval for the swarm, backed by the reference AgentMemory."""

    def __init__(
        self,
        db_path: Path | str = DEFAULT_INDEX_DB,
        *,
        extra_candidates: Optional[list[Path]] = None,
        backend: Optional[Any] = None,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.load_error: Optional[str] = None

        if backend is not None:
            # Injected for tests.
            self._backend = backend
            self.available = True
            return

        cls, err = load_agent_memory_class(extra_candidates)
        if cls is None:
            self.load_error = err
            self._backend = NullMemory(err or "unknown")
            self.available = False
            return
        try:
            self._backend = cls(str(self.db_path))
            self.available = True
        except Exception as exc:  # noqa: BLE001
            self.load_error = f"AgentMemory({self.db_path}) failed: {exc}"
            self._backend = NullMemory(self.load_error)
            self.available = False

    # -- introspection -----------------------------------------------------

    def backend_info(self) -> dict[str, Any]:
        """Report which retrieval backend is live.

        agent_memory.py exposes `backend_info()` specifically so callers do not
        confuse a real embedding model with the hashing-n-gram fallback. Pass it
        through rather than paraphrasing.
        """
        info: dict[str, Any] = {
            "available": self.available,
            "db_path": str(self.db_path),
            "load_error": self.load_error,
        }
        fn = getattr(self._backend, "backend_info", None)
        if callable(fn):
            try:
                info["backend"] = fn()
            except Exception as exc:  # noqa: BLE001
                info["backend"] = f"backend_info() raised: {exc}"
        return info

    # -- operations --------------------------------------------------------

    def index_dir(
        self, root: Path | str, patterns: Optional[list[str]] = None
    ) -> int:
        """Index a directory tree. Returns the number of documents indexed."""
        pats = patterns or ["*.py", "*.md", "*.json", "*.txt"]
        fn = getattr(self._backend, "index_dir", None)
        if not callable(fn):
            return 0
        try:
            result = fn(str(root), patterns=pats)
            return int(result) if isinstance(result, int) else 0
        except TypeError:
            # Older signature without the keyword.
            try:
                result = fn(str(root), pats)
                return int(result) if isinstance(result, int) else 0
            except Exception:
                return 0
        except Exception:
            return 0

    def search(self, query: str, k: int = 5) -> list[MemoryHit]:
        """Hybrid search. Returns normalized hits; never raises."""
        if not query or not query.strip():
            return []
        fn = getattr(self._backend, "search", None)
        if not callable(fn):
            return []
        try:
            raw = fn(query, k=k)
        except TypeError:
            try:
                raw = fn(query, k)
            except Exception:
                return []
        except Exception:
            return []

        hits: list[MemoryHit] = []
        for item in raw or []:
            if isinstance(item, MemoryHit):
                hits.append(item)
            elif isinstance(item, dict):
                hits.append(
                    MemoryHit(
                        path=str(item.get("path", item.get("id", "?"))),
                        score=float(item.get("score", 0.0) or 0.0),
                        text=str(item.get("text", item.get("chunk", "")) or ""),
                    )
                )
        return hits

    def build_context(self, query: str, budget_chars: int = 4000) -> str:
        """Assemble a character-budgeted context block for a prompt.

        Budgeting matters more than it looks: every retrieved character passes
        through prefill, and prefill spends the same wall-clock budget that
        Best-of-N sampling wants (BRAIN.md QD-8).
        """
        if not query or not query.strip():
            return ""
        fn = getattr(self._backend, "build_context", None)
        if callable(fn):
            try:
                out = fn(query, budget_chars=budget_chars)
                if isinstance(out, str):
                    return out
            except TypeError:
                try:
                    out = fn(query, budget_chars)
                    if isinstance(out, str):
                        return out
                except Exception:
                    pass
            except Exception:
                pass

        # Compose from hits if the backend lacks build_context.
        blocks: list[str] = []
        used = 0
        for hit in self.search(query, k=8):
            piece = f"# {hit.path}\n{hit.text}".strip()
            if not piece:
                continue
            if used + len(piece) > budget_chars:
                break
            blocks.append(piece)
            used += len(piece)
        return "\n\n---\n\n".join(blocks)

    def context_blocks(self, query: str, budget_chars: int = 4000) -> list[str]:
        """build_context() split into a list, for LocalLLMClient.complete()."""
        ctx = self.build_context(query, budget_chars)
        return [ctx] if ctx else []


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------


def _selftest() -> int:
    import tempfile
    import shutil

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

    tmp = Path(tempfile.mkdtemp(prefix="membridge_selftest_"))

    class FakeBackend:
        """Minimal stand-in with the AgentMemory surface."""

        def __init__(self) -> None:
            self.indexed: list[str] = []

        def backend_info(self) -> dict[str, str]:
            return {"vector": "hashing-ngram", "note": "not real semantics"}

        def index_dir(self, root: str, patterns: Optional[list[str]] = None) -> int:
            self.indexed.append(root)
            return 7

        def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
            return [
                {"path": "a.py", "score": 0.9, "text": "alpha body"},
                {"path": "b.md", "score": 0.4, "text": "beta body"},
            ][:k]

        def build_context(self, query: str, budget_chars: int = 4000) -> str:
            return "CTX:" + query[:20]

    class BareBackend:
        """Has search but no build_context, to exercise the composition path."""

        def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
            return [{"path": f"f{i}.py", "score": 1.0 / (i + 1), "text": "x" * 100} for i in range(8)]

    print("memory_bridge.py selftest")
    print("-" * 60)

    def t_null_memory_is_inert() -> None:
        n = NullMemory("no reference workspace")
        assert n.available is False
        assert n.search("anything") == []
        assert n.build_context("anything") == ""
        assert n.index_dir("/tmp") == 0

    def t_missing_reference_degrades() -> None:
        # Point at a path that cannot exist; must not raise.
        b = MemoryBridge(tmp / "m1.db", extra_candidates=[tmp / "nope" / "agent_memory.py"])
        info = b.backend_info()
        assert isinstance(info["available"], bool)
        if not b.available:
            assert b.load_error, "must explain why retrieval is unavailable"
            assert b.search("q") == [], "degraded search returns empty, not an error"

    def t_injected_backend_used() -> None:
        b = MemoryBridge(tmp / "m2.db", backend=FakeBackend())
        assert b.available is True

    def t_search_normalizes_dicts() -> None:
        b = MemoryBridge(tmp / "m3.db", backend=FakeBackend())
        hits = b.search("alpha")
        assert len(hits) == 2, hits
        assert isinstance(hits[0], MemoryHit)
        assert hits[0].path == "a.py" and hits[0].score == 0.9

    def t_search_respects_k() -> None:
        b = MemoryBridge(tmp / "m4.db", backend=FakeBackend())
        assert len(b.search("alpha", k=1)) == 1

    def t_empty_query_returns_nothing() -> None:
        b = MemoryBridge(tmp / "m5.db", backend=FakeBackend())
        assert b.search("   ") == []
        assert b.build_context("") == ""

    def t_build_context_delegates() -> None:
        b = MemoryBridge(tmp / "m6.db", backend=FakeBackend())
        assert b.build_context("hello world").startswith("CTX:")

    def t_build_context_composes_when_absent() -> None:
        b = MemoryBridge(tmp / "m7.db", backend=BareBackend())
        ctx = b.build_context("q", budget_chars=250)
        assert ctx, "must compose context from hits"
        assert len(ctx) <= 400, f"must respect budget, got {len(ctx)}"

    def t_budget_is_honoured() -> None:
        b = MemoryBridge(tmp / "m8.db", backend=BareBackend())
        small = b.build_context("q", budget_chars=120)
        large = b.build_context("q", budget_chars=600)
        assert len(small) < len(large), (len(small), len(large))

    def t_context_blocks_shape() -> None:
        b = MemoryBridge(tmp / "m9.db", backend=FakeBackend())
        blocks = b.context_blocks("q")
        assert isinstance(blocks, list) and len(blocks) == 1

    def t_context_blocks_empty_when_no_context() -> None:
        b = MemoryBridge(tmp / "m10.db", backend=NullMemory("none"))
        assert b.context_blocks("q") == []

    def t_backend_info_passthrough() -> None:
        b = MemoryBridge(tmp / "m11.db", backend=FakeBackend())
        info = b.backend_info()
        assert "backend" in info
        assert info["backend"]["vector"] == "hashing-ngram", info

    def t_index_dir_returns_count() -> None:
        fake = FakeBackend()
        b = MemoryBridge(tmp / "m12.db", backend=fake)
        assert b.index_dir(tmp) == 7
        assert fake.indexed, "backend must receive the root"

    def t_db_stays_in_repo() -> None:
        # The index must never be written into the read-only reference workspace.
        assert DEFAULT_INDEX_DB.is_relative_to(REPO_ROOT), DEFAULT_INDEX_DB
        assert "KyThuatNenAIMoi" not in str(DEFAULT_INDEX_DB)

    def t_backend_exception_is_swallowed() -> None:
        class Exploding:
            def search(self, query: str, k: int = 5) -> list[dict[str, Any]]:
                raise RuntimeError("backend on fire")

        b = MemoryBridge(tmp / "m13.db", backend=Exploding())
        assert b.search("q") == [], "a broken backend must not take down the swarm"

    def t_real_agent_memory_if_present() -> None:
        # Opportunistic: if the reference workspace is reachable, exercise it.
        cls, err = load_agent_memory_class()
        if cls is None:
            print(f"        (skipped - reference not reachable: {str(err)[:60]})")
            return
        b = MemoryBridge(tmp / "real.db")
        assert b.available, b.load_error
        n = b.index_dir(REPO_ROOT, patterns=["*.md"])
        assert isinstance(n, int)
        hits = b.search("VRAM budget", k=3)
        assert isinstance(hits, list)

    for name, fn in [
        ("NullMemory is inert", t_null_memory_is_inert),
        ("missing reference degrades gracefully", t_missing_reference_degrades),
        ("injected backend is used", t_injected_backend_used),
        ("search normalizes dict results", t_search_normalizes_dicts),
        ("search respects k", t_search_respects_k),
        ("empty query returns nothing", t_empty_query_returns_nothing),
        ("build_context delegates to backend", t_build_context_delegates),
        ("build_context composes when absent", t_build_context_composes_when_absent),
        ("character budget is honoured", t_budget_is_honoured),
        ("context_blocks returns a list", t_context_blocks_shape),
        ("context_blocks empty without context", t_context_blocks_empty_when_no_context),
        ("backend_info passed through", t_backend_info_passthrough),
        ("index_dir returns a count", t_index_dir_returns_count),
        ("index DB stays inside repo", t_db_stays_in_repo),
        ("broken backend does not propagate", t_backend_exception_is_swallowed),
        ("real AgentMemory when reachable", t_real_agent_memory_if_present),
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


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Semantic memory bridge")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--db", default=str(DEFAULT_INDEX_DB))
    ap.add_argument("--index", metavar="DIR", default=None, help="index a directory tree")
    ap.add_argument("--query", default=None, help="search the index")
    ap.add_argument("--k", type=int, default=5)
    ap.add_argument("--info", action="store_true", help="show which backend is live")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    bridge = MemoryBridge(args.db)
    if not bridge.available:
        print(f"WARNING: retrieval unavailable - {bridge.load_error}", file=sys.stderr)

    if args.info:
        import json as _json

        print(_json.dumps(bridge.backend_info(), indent=2, ensure_ascii=False))
        return 0
    if args.index:
        n = bridge.index_dir(args.index)
        print(f"indexed {n} documents from {args.index} into {bridge.db_path}")
        return 0
    if args.query:
        hits = bridge.search(args.query, k=args.k)
        if not hits:
            print("no hits")
            return 0
        for h in hits:
            print(f"{h.score:8.4f}  {h.path}")
        return 0

    import json as _json

    print(_json.dumps(bridge.backend_info(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
