#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
memory_governor.py - what actually fits, and what does not.

This module exists because a memory target is arithmetic, not aspiration. Given
a measured GGUF size, a context length, a KV quantisation and a slot count, the
VRAM cost is determined. If the sum exceeds the ceiling, the answer is "no" and
the honest thing to do is print the shortfall, not quietly shrink the context
until something fits and call it a success.

The three envelopes (config/profiles.json -> budgets)
-----------------------------------------------------
    STANDARD   10.0 GB VRAM / 6.0 GB RAM   the shipped default
    TIGHT       9.5 GB VRAM / 4.0 GB RAM   the active target
    ULTRA       7.8 GB VRAM / 4.0 GB RAM   unreachable with the GGUF on disk

ULTRA is kept precisely because it does not fit. 8.39 GB of weights plus 0.55 GB
of CUDA context is 8.94 GB before a single KV token exists, so the 7.8 GB target
is missed by 1.14 GB at zero context. No context reduction, no KV quantisation
and no slot arithmetic closes a gap that exists before any of them apply. It
needs a smaller file - IQ1_M at 6.73 GB, or IQ1_S at 6.19 - and neither is on
this machine. `plan_vram` says so with the number attached.

The host RAM ceiling has exactly one lever that matters
-------------------------------------------------------
Not SQLite page cache. Not Python. `--no-mmap`.

With mmap enabled, llama.cpp maps the GGUF and streams it to VRAM; host resident
memory for weights stays near zero. With `--no-mmap` it reads the whole file into
host RAM first - 8.39 GB of it - which breaks a 4.0 GB ceiling by more than
double before the swarm has allocated anything. `docs/17` notes in passing that
the configuration which measured 50-55 tok/s used `--no-mmap`; under a 4.0 GB
host budget that flag is not a tuning choice, it is a contradiction, and
`assert_mmap_allowed` refuses it.

The ledger database is not where the memory goes: `state/risk_guard.db` is
~110 KB. `sqlite_pragmas` still bounds it, because an unbounded page cache per
connection is the kind of thing that is free until the day it is not, but the
docstring says plainly that this is hygiene rather than the lever.

CLI:
    python core/memory_governor.py --selftest
    python core/memory_governor.py --budget TIGHT --report
    python core/memory_governor.py --budget ULTRA --report
    python core/memory_governor.py --budget TIGHT --solve --slots 6
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

from local_llm_client import (  # noqa: E402
    DEFAULT_CONFIG,
    ProfileRegistry,
    estimate_kv_cache_gb,
)
from risk_guard import read_process_tree_ram_gb, read_ram_gb  # noqa: E402

__all__ = [
    "MemoryBudget",
    "BudgetError",
    "MmapRefused",
    "VramPlan",
    "RamVerdict",
    "RamGovernor",
    "load_budgets",
    "get_budget",
    "plan_vram",
    "max_context_per_slot",
    "max_slots",
    "feasible_plans",
    "assert_mmap_allowed",
    "FALLBACK_BUDGETS",
]

REPO_ROOT = _HERE.parent

#: Used when config/profiles.json carries no `budgets` block, so this module
#: still works against an older config rather than crashing on a missing key.
FALLBACK_BUDGETS: dict[str, tuple[float, float]] = {
    "STANDARD": (10.0, 6.0),
    "TIGHT": (9.5, 4.0),
    "ULTRA": (7.8, 4.0),
}

#: Host RAM added by `--no-mmap`: the entire weights file, resident.
NO_MMAP_HOST_COST_IS_FULL_WEIGHTS = True


class BudgetError(RuntimeError):
    """A memory envelope was misused or does not exist."""


class MmapRefused(BudgetError):
    """`--no-mmap` was requested under a budget that cannot absorb the weights."""


@dataclass(frozen=True)
class MemoryBudget:
    """One named memory envelope."""

    name: str
    vram_ceiling_gb: float
    ram_ceiling_gb: float
    cuda_overhead_gb: float = 0.55
    note: str = ""

    def __post_init__(self) -> None:
        if self.vram_ceiling_gb <= 0 or self.ram_ceiling_gb <= 0:
            raise BudgetError(f"{self.name}: ceilings must be positive")
        if self.cuda_overhead_gb < 0:
            raise BudgetError(f"{self.name}: cuda_overhead_gb must be >= 0")

    @property
    def weights_and_kv_budget_gb(self) -> float:
        """What is left for weights plus KV once the CUDA context is paid for."""
        return self.vram_ceiling_gb - self.cuda_overhead_gb

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "vram_ceiling_gb": self.vram_ceiling_gb,
            "ram_ceiling_gb": self.ram_ceiling_gb,
            "cuda_overhead_gb": self.cuda_overhead_gb,
            "weights_and_kv_budget_gb": round(self.weights_and_kv_budget_gb, 4),
            "note": self.note,
        }


def load_budgets(config_path: Path | str = DEFAULT_CONFIG) -> dict[str, MemoryBudget]:
    """Read the named envelopes from config, falling back to the built-in three."""
    overhead = 0.55
    raw: dict[str, Any] = {}
    path = Path(config_path)
    if path.exists():
        try:
            cfg = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise BudgetError(f"{path} is not valid JSON: {exc}") from exc
        overhead = float((cfg.get("budget") or {}).get("cuda_overhead_gb", 0.55))
        raw = {k: v for k, v in (cfg.get("budgets") or {}).items() if not k.startswith("_")}

    out: dict[str, MemoryBudget] = {}
    for name, (vram, ram) in FALLBACK_BUDGETS.items():
        out[name] = MemoryBudget(name, vram, ram, overhead, "built-in fallback")
    for name, d in raw.items():
        if not isinstance(d, dict):
            continue
        out[name] = MemoryBudget(
            name=name,
            vram_ceiling_gb=float(d.get("vram_ceiling_gb", 10.0)),
            ram_ceiling_gb=float(d.get("ram_ceiling_gb", 6.0)),
            cuda_overhead_gb=float(d.get("cuda_overhead_gb", overhead)),
            note=str(d.get("note", "")),
        )
    return out


def get_budget(
    name: str = "TIGHT", config_path: Path | str = DEFAULT_CONFIG
) -> MemoryBudget:
    budgets = load_budgets(config_path)
    key = str(name).upper()
    if key not in budgets:
        raise BudgetError(f"unknown budget {name!r}; known: {sorted(budgets)}")
    return budgets[key]


# ---------------------------------------------------------------------------
# VRAM
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class VramPlan:
    """One candidate configuration, costed to the last component.

    Everything is in GiB. `fits` is the only field a caller should branch on;
    `shortfall_gb` exists so a refusal can say *by how much*, which is the
    difference between a useful "no" and an unhelpful one.
    """

    budget: MemoryBudget
    label: str
    weights_gb: float
    context_per_slot: int
    kv_type: str
    slots: int
    kv_total_gb: float
    draft_weights_gb: float
    draft_kv_gb: float
    overhead_gb: float
    notes: tuple[str, ...] = ()

    @property
    def total_gb(self) -> float:
        return round(
            self.weights_gb
            + self.kv_total_gb
            + self.draft_weights_gb
            + self.draft_kv_gb
            + self.overhead_gb,
            4,
        )

    @property
    def fits(self) -> bool:
        return self.total_gb <= self.budget.vram_ceiling_gb + 1e-9

    @property
    def headroom_gb(self) -> float:
        return round(self.budget.vram_ceiling_gb - self.total_gb, 4)

    @property
    def shortfall_gb(self) -> float:
        """How much over the ceiling this plan is. Zero when it fits."""
        return round(max(0.0, self.total_gb - self.budget.vram_ceiling_gb), 4)

    @property
    def total_context(self) -> int:
        return self.context_per_slot * self.slots

    def verdict_line(self) -> str:
        if self.fits:
            return (
                f"FITS   {self.label}: {self.total_gb:.2f} / "
                f"{self.budget.vram_ceiling_gb:.2f} GB, {self.headroom_gb:.2f} GB spare"
            )
        return (
            f"OVER   {self.label}: {self.total_gb:.2f} / "
            f"{self.budget.vram_ceiling_gb:.2f} GB, short by {self.shortfall_gb:.2f} GB"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "budget": self.budget.name,
            "label": self.label,
            "weights_gb": round(self.weights_gb, 4),
            "context_per_slot": self.context_per_slot,
            "total_context": self.total_context,
            "kv_type": self.kv_type,
            "slots": self.slots,
            "kv_total_gb": round(self.kv_total_gb, 4),
            "draft_weights_gb": round(self.draft_weights_gb, 4),
            "draft_kv_gb": round(self.draft_kv_gb, 4),
            "overhead_gb": round(self.overhead_gb, 4),
            "total_gb": self.total_gb,
            "ceiling_gb": self.budget.vram_ceiling_gb,
            "fits": self.fits,
            "headroom_gb": self.headroom_gb,
            "shortfall_gb": self.shortfall_gb,
            "notes": list(self.notes),
        }


def plan_vram(
    *,
    weights_gb: float,
    context_per_slot: int,
    kv_type: str = "q4_0",
    slots: int = 1,
    kib_per_token_f16: float = 64.0,
    draft_weights_gb: float = 0.0,
    draft_kib_per_token_f16: Optional[float] = None,
    draft_kv_type: Optional[str] = None,
    budget: Optional[MemoryBudget] = None,
    label: str = "plan",
    notes: Iterable[str] = (),
) -> VramPlan:
    """Cost one configuration against one envelope.

    Every KV slot is charged separately, because llama-server's `--parallel N`
    allocates N independent caches of `--ctx-size / N` each - the total is what
    the card pays for, and a plan that costs one slot and then runs six is a plan
    that runs out of memory in production rather than in a spreadsheet.
    """
    if weights_gb < 0 or draft_weights_gb < 0:
        raise BudgetError("weights must be >= 0")
    if context_per_slot <= 0:
        raise BudgetError("context_per_slot must be > 0")
    if slots <= 0:
        raise BudgetError("slots must be > 0")

    b = budget or get_budget("TIGHT")
    kv_one = estimate_kv_cache_gb(context_per_slot, kv_type, kib_per_token_f16)
    kv_total = kv_one * slots

    draft_kv = 0.0
    if draft_weights_gb > 0:
        draft_kv = estimate_kv_cache_gb(
            context_per_slot,
            draft_kv_type or kv_type,
            draft_kib_per_token_f16 if draft_kib_per_token_f16 is not None else 12.0,
        ) * slots

    return VramPlan(
        budget=b,
        label=label,
        weights_gb=float(weights_gb),
        context_per_slot=int(context_per_slot),
        kv_type=kv_type,
        slots=int(slots),
        kv_total_gb=kv_total,
        draft_weights_gb=float(draft_weights_gb),
        draft_kv_gb=draft_kv,
        overhead_gb=b.cuda_overhead_gb,
        notes=tuple(notes),
    )


def max_context_per_slot(
    *,
    weights_gb: float,
    kv_type: str = "q4_0",
    slots: int = 1,
    kib_per_token_f16: float = 64.0,
    draft_weights_gb: float = 0.0,
    draft_kib_per_token_f16: Optional[float] = None,
    budget: Optional[MemoryBudget] = None,
    granularity: int = 256,
) -> int:
    """Largest per-slot context that fits, rounded down to `granularity`.

    Returns 0 when nothing fits - which is the answer for ULTRA against the file
    on disk, and the caller is expected to report that rather than round it up
    to a minimum.
    """
    b = budget or get_budget("TIGHT")
    fixed = weights_gb + draft_weights_gb + b.cuda_overhead_gb
    spare = b.vram_ceiling_gb - fixed
    if spare <= 0:
        return 0
    per_token_gb = (
        estimate_kv_cache_gb(1, kv_type, kib_per_token_f16) * slots
        + (
            estimate_kv_cache_gb(
                1, kv_type, draft_kib_per_token_f16 if draft_kib_per_token_f16 is not None else 12.0
            )
            * slots
            if draft_weights_gb > 0
            else 0.0
        )
    )
    if per_token_gb <= 0:
        raise BudgetError("KV cost per token computed as zero; check kib_per_token_f16")
    tokens = int(spare / per_token_gb)
    return (tokens // granularity) * granularity


def max_slots(
    *,
    weights_gb: float,
    context_per_slot: int,
    kv_type: str = "q4_0",
    kib_per_token_f16: float = 64.0,
    draft_weights_gb: float = 0.0,
    draft_kib_per_token_f16: Optional[float] = None,
    budget: Optional[MemoryBudget] = None,
    cap: int = 64,
) -> int:
    """How many KV slots of `context_per_slot` fit. Zero means the weights alone are over."""
    b = budget or get_budget("TIGHT")
    best = 0
    for n in range(1, int(cap) + 1):
        plan = plan_vram(
            weights_gb=weights_gb,
            context_per_slot=context_per_slot,
            kv_type=kv_type,
            slots=n,
            kib_per_token_f16=kib_per_token_f16,
            draft_weights_gb=draft_weights_gb,
            draft_kib_per_token_f16=draft_kib_per_token_f16,
            budget=b,
        )
        if plan.fits:
            best = n
        else:
            break
    return best


def feasible_plans(
    *,
    weights_gb: float,
    budget: Optional[MemoryBudget] = None,
    contexts: Iterable[int] = (2048, 3072, 4096, 6144, 8192, 16384, 32768),
    slot_counts: Iterable[int] = (1, 2, 4, 6, 8),
    kv_types: Iterable[str] = ("q4_0", "q8_0"),
    kib_per_token_f16: float = 64.0,
    draft_weights_gb: float = 0.0,
    draft_kib_per_token_f16: Optional[float] = None,
) -> list[VramPlan]:
    """Enumerate the grid and return only what fits, richest configuration first."""
    b = budget or get_budget("TIGHT")
    out: list[VramPlan] = []
    for kv in kv_types:
        for slots in slot_counts:
            for ctx in contexts:
                plan = plan_vram(
                    weights_gb=weights_gb,
                    context_per_slot=ctx,
                    kv_type=kv,
                    slots=slots,
                    kib_per_token_f16=kib_per_token_f16,
                    draft_weights_gb=draft_weights_gb,
                    draft_kib_per_token_f16=draft_kib_per_token_f16,
                    budget=b,
                    label=f"{kv} x{slots} slots @ {ctx}",
                )
                if plan.fits:
                    out.append(plan)
    out.sort(key=lambda p: (p.slots * p.context_per_slot, p.slots), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Host RAM
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class RamVerdict:
    """A host RAM reading against an envelope."""

    measured_gb: Optional[float]
    ceiling_gb: float
    source: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def measurable(self) -> bool:
        return self.measured_gb is not None

    @property
    def ok(self) -> bool:
        """Unmeasurable is not OK. A guard that passes when blind is not a guard."""
        return self.measured_gb is not None and self.measured_gb <= self.ceiling_gb

    @property
    def headroom_gb(self) -> Optional[float]:
        if self.measured_gb is None:
            return None
        return round(self.ceiling_gb - self.measured_gb, 4)

    def as_dict(self) -> dict[str, Any]:
        return {
            "measured_gb": self.measured_gb,
            "ceiling_gb": self.ceiling_gb,
            "measurable": self.measurable,
            "ok": self.ok,
            "headroom_gb": self.headroom_gb,
            "source": self.source,
            "detail": dict(self.detail),
        }


def assert_mmap_allowed(
    *, no_mmap: bool, weights_gb: float, budget: MemoryBudget
) -> None:
    """Refuse `--no-mmap` when the weights cannot fit the host ceiling.

    This is the single highest-value check in the module. `--no-mmap` makes
    llama.cpp read the entire GGUF into host memory before uploading it, so the
    host cost of the weights goes from roughly zero to the full file size. At
    8.39 GB of weights against a 4.0 GB host ceiling it is not a tuning
    regression, it is an immediate breach of more than double - and it is easy
    to inherit by accident, because `docs/17` records that the configuration
    which measured 50-55 tok/s used exactly that flag.
    """
    if not no_mmap:
        return
    if weights_gb <= budget.ram_ceiling_gb:
        return
    raise MmapRefused(
        f"--no-mmap would make llama.cpp hold all {weights_gb:.2f} GB of weights in "
        f"host RAM, against a {budget.ram_ceiling_gb:.2f} GB ceiling on budget "
        f"{budget.name} - a breach of {weights_gb - budget.ram_ceiling_gb:.2f} GB "
        f"before the swarm allocates anything. Drop the flag, or move to a budget "
        f"whose host ceiling can hold the file."
    )


class RamGovernor:
    """Measures and bounds host RAM for the swarm process tree."""

    def __init__(self, budget: Optional[MemoryBudget] = None) -> None:
        self.budget = budget or get_budget("TIGHT")

    # -- measurement -------------------------------------------------------

    def measure(self) -> RamVerdict:
        """Process-tree RSS, which is what the ceiling is actually about.

        System-wide RAM is the wrong number: this machine has 32 GB and other
        programs are entitled to it. What the budget bounds is what *this* swarm
        holds, so the reading is the resident set of this process and its
        children.
        """
        try:
            tree = read_process_tree_ram_gb()
        except Exception as exc:  # noqa: BLE001 - unmeasurable is a verdict, not a crash
            return RamVerdict(None, self.budget.ram_ceiling_gb, f"unavailable: {exc}")
        sys_used, sys_total, sys_source = read_ram_gb()
        if tree <= 0:
            return RamVerdict(
                None,
                self.budget.ram_ceiling_gb,
                "process tree unreadable (psutil missing?)",
                {"system_used_gb": sys_used, "system_total_gb": sys_total},
            )
        return RamVerdict(
            round(tree, 4),
            self.budget.ram_ceiling_gb,
            "process-tree RSS",
            {
                "system_used_gb": sys_used,
                "system_total_gb": sys_total,
                "system_source": sys_source,
            },
        )

    def enforce(self, *, raise_on_breach: bool = True) -> RamVerdict:
        v = self.measure()
        if raise_on_breach and v.measurable and not v.ok:
            raise BudgetError(
                f"swarm process tree holds {v.measured_gb:.2f} GB against a "
                f"{v.ceiling_gb:.2f} GB ceiling on budget {self.budget.name}"
            )
        return v

    # -- SQLite ------------------------------------------------------------

    def sqlite_pragmas(self) -> dict[str, Any]:
        """Bounded-memory PRAGMAs for the ledger connections.

        Hygiene, not the lever. `state/risk_guard.db` is on the order of a
        hundred kilobytes and the swarm opens a handful of connections; this is
        not where four gigabytes goes. It is here because an unbounded page cache
        per connection is free right up until the day a ledger grows and it is
        not, and because a negative `cache_size` is the only spelling SQLite
        reads as "kibibytes" rather than "pages", which is the kind of detail
        that is worth writing down once.
        """
        # Roughly 2% of the envelope, capped, expressed as negative KiB.
        kib = int(min(65_536, max(2_048, self.budget.ram_ceiling_gb * 1024 * 1024 * 0.02)))
        return {
            "cache_size": -kib,
            "mmap_size": 64 * 1024 * 1024,
            "temp_store": "FILE",
            "journal_mode": "WAL",
            "synchronous": "NORMAL",
        }

    def tune_sqlite(self, conn: sqlite3.Connection) -> dict[str, Any]:
        """Apply the PRAGMAs and report what the connection actually accepted.

        Reported rather than assumed: a filesystem that refuses WAL leaves the
        journal in DELETE mode, and a caller that logs the requested value
        instead of the observed one is logging a wish.
        """
        applied: dict[str, Any] = {}
        for key, value in self.sqlite_pragmas().items():
            try:
                conn.execute(f"PRAGMA {key}={value}")
            except sqlite3.Error as exc:
                applied[key] = f"refused: {exc}"
                continue
            try:
                row = conn.execute(f"PRAGMA {key}").fetchone()
                applied[key] = row[0] if row else None
            except sqlite3.Error:
                applied[key] = "unreadable"
        return applied

    # -- reporting ---------------------------------------------------------

    def report(self) -> dict[str, Any]:
        return {
            "budget": self.budget.as_dict(),
            "ram": self.measure().as_dict(),
            "sqlite_pragmas": self.sqlite_pragmas(),
            "mmap_required": True,
            "mmap_note": (
                "--no-mmap copies the entire weights file into host RAM. Under a "
                f"{self.budget.ram_ceiling_gb:.1f} GB ceiling it is refused."
            ),
        }


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def measured_weights_gb(profile_key: str = "S", config_path: Path | str = DEFAULT_CONFIG) -> tuple[float, str]:
    """The weights size to plan against, preferring a stat over a claim.

    Returns `(gb, provenance)`. If the GGUF is on this filesystem its measured
    size wins; otherwise the config's `disk_gb_reported_by_user` is used and the
    provenance says so, because planning against a number nobody measured and
    not saying which is how a budget quietly becomes fiction.
    """
    reg = ProfileRegistry(config_path)
    resolved = reg.resolve(profile_key)
    if resolved.found and resolved.disk_gb_measured:
        return float(resolved.disk_gb_measured), f"measured on disk: {resolved.path}"
    spec = resolved.spec
    reported = getattr(spec, "disk_gb_reported_by_user", None)
    raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    entry = (raw.get("profiles") or {}).get(profile_key, {})
    reported = entry.get("disk_gb_reported_by_user") or entry.get("disk_gb_claimed")
    if reported:
        return float(reported), "config disk_gb_reported_by_user (NOT measured here)"
    return float(spec.disk_gb_claimed or 0.0), "config disk_gb_claimed (NOT measured here)"


def render_report(
    budget_name: str = "TIGHT",
    *,
    profile_key: str = "S",
    slots: int = 6,
    config_path: Path | str = DEFAULT_CONFIG,
) -> str:
    """A plain-text budget report. Every number carries where it came from."""
    budgets = load_budgets(config_path)
    b = budgets[budget_name.upper()]
    weights, provenance = measured_weights_gb(profile_key, config_path)
    raw = json.loads(Path(config_path).read_text(encoding="utf-8"))
    prof = (raw.get("profiles") or {}).get(profile_key, {})
    kib = float(prof.get("kv_kib_per_token_f16") or 64.0)
    kv_type = str(prof.get("kv_type") or "q4_0")

    lines: list[str] = []
    add = lines.append
    add("=" * 78)
    add(f" MEMORY GOVERNOR - budget {b.name}  ({b.vram_ceiling_gb:.2f} GB VRAM / "
        f"{b.ram_ceiling_gb:.2f} GB host RAM)")
    add("=" * 78)
    if b.note:
        add(f" {b.note}")
        add("-" * 78)
    add(f" profile              : {profile_key}  ({prof.get('model_file', '?')})")
    add(f" weights              : {weights:.2f} GB   [{provenance}]")
    add(f" KV                   : {kib:.1f} KiB/token @ f16, quantised {kv_type}")
    add(f" CUDA context         : {b.cuda_overhead_gb:.2f} GB   [ESTIMATED - "
        f"calibrate against nvidia-smi]")
    add(f" left for KV          : {b.vram_ceiling_gb - weights - b.cuda_overhead_gb:.2f} GB")
    add("-" * 78)

    add(" Candidate configurations")
    for label, n, ctx in (
        ("serial deliberation".ljust(21), 1, max_context_per_slot(
            weights_gb=weights, kv_type=kv_type, slots=1,
            kib_per_token_f16=kib, budget=b)),
        (f"batched x{slots} slots".ljust(21), slots, max_context_per_slot(
            weights_gb=weights, kv_type=kv_type, slots=slots,
            kib_per_token_f16=kib, budget=b)),
    ):
        if ctx <= 0:
            add(f"   {label}: no context fits - the weights alone are over the ceiling")
            continue
        plan = plan_vram(
            weights_gb=weights, context_per_slot=ctx, kv_type=kv_type, slots=n,
            kib_per_token_f16=kib, budget=b, label=f"{n} slot(s) @ {ctx}",
        )
        add(f"   {label}: max ctx {ctx:>6,}/slot  total {plan.total_context:>7,} tok  "
            f"{plan.total_gb:.2f} GB  headroom {plan.headroom_gb:.2f} GB")

    draft = (raw.get("draft_models") or {}).get("qwen2.5-0.5b-q4km") or {}
    if draft:
        dw = float(draft.get("weights_gb_claimed") or 0.0)
        dkib = float(draft.get("kv_kib_per_token_f16") or 12.0)
        ctx_d = max_context_per_slot(
            weights_gb=weights, kv_type=kv_type, slots=slots, kib_per_token_f16=kib,
            draft_weights_gb=dw, draft_kib_per_token_f16=dkib, budget=b,
        )
        ctx_d1 = max_context_per_slot(
            weights_gb=weights, kv_type=kv_type, slots=1, kib_per_token_f16=kib,
            draft_weights_gb=dw, draft_kib_per_token_f16=dkib, budget=b,
        )
        add(f"   + draft model      : {draft.get('label', '?')} ({dw:.2f} GB weights)")
        add(f"     serial + draft   : max ctx {ctx_d1:>6,}/slot")
        add(f"     batched + draft  : max ctx {ctx_d:>6,}/slot"
            + ("   <- both at once is very tight" if 0 < ctx_d < 2048 else ""))

    add("-" * 78)
    add(" Every envelope, same weights")
    for name in sorted(budgets):
        bb = budgets[name]
        floor = plan_vram(
            weights_gb=weights, context_per_slot=256, kv_type=kv_type, slots=1,
            kib_per_token_f16=kib, budget=bb, label="minimum",
        )
        if floor.fits:
            best = max_context_per_slot(
                weights_gb=weights, kv_type=kv_type, slots=1,
                kib_per_token_f16=kib, budget=bb)
            add(f"   {name:<9} {bb.vram_ceiling_gb:>5.2f} GB  FITS, up to {best:,} tok of context")
        else:
            add(f"   {name:<9} {bb.vram_ceiling_gb:>5.2f} GB  UNREACHABLE - over by "
                f"{floor.shortfall_gb:.2f} GB even at 256 tokens of context")

    gov = RamGovernor(b)
    v = gov.measure()
    add("-" * 78)
    add(" Host RAM")
    if v.measurable:
        add(f"   swarm process tree : {v.measured_gb:.2f} / {v.ceiling_gb:.2f} GB  "
            f"({'OK' if v.ok else 'OVER'})")
    else:
        add(f"   swarm process tree : not measurable ({v.source})")
    add(f"   --no-mmap          : REFUSED under this budget "
        f"(would add {weights:.2f} GB of host residency)")
    add(f"   sqlite pragmas     : {gov.sqlite_pragmas()}")
    add("=" * 78)
    return "\n".join(lines)


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

    tight = get_budget("TIGHT")
    ultra = get_budget("ULTRA")
    W = 8.39  # the file as reported on this machine

    def t_three_budgets_exist() -> None:
        b = load_budgets()
        assert {"STANDARD", "TIGHT", "ULTRA"} <= set(b), sorted(b)

    def t_tight_is_95_and_40() -> None:
        assert tight.vram_ceiling_gb == 9.5 and tight.ram_ceiling_gb == 4.0

    def t_ultra_is_unreachable_for_this_file() -> None:
        p = plan_vram(weights_gb=W, context_per_slot=256, slots=1, budget=ultra)
        assert not p.fits, p.total_gb
        assert p.shortfall_gb > 1.0, p.shortfall_gb

    def t_tight_fits_serial() -> None:
        p = plan_vram(weights_gb=W, context_per_slot=8192, slots=1, budget=tight)
        assert p.fits, p.verdict_line()

    def t_tight_fits_six_slots() -> None:
        p = plan_vram(weights_gb=W, context_per_slot=4096, slots=6, budget=tight)
        assert p.fits, p.verdict_line()

    def t_kv_scales_with_slots() -> None:
        one = plan_vram(weights_gb=W, context_per_slot=4096, slots=1, budget=tight)
        six = plan_vram(weights_gb=W, context_per_slot=4096, slots=6, budget=tight)
        assert abs(six.kv_total_gb - 6 * one.kv_total_gb) < 1e-9

    def t_max_context_zero_when_weights_over() -> None:
        assert max_context_per_slot(weights_gb=W, slots=1, budget=ultra) == 0

    def t_max_context_is_actually_feasible() -> None:
        ctx = max_context_per_slot(weights_gb=W, slots=6, budget=tight)
        assert ctx > 0
        assert plan_vram(weights_gb=W, context_per_slot=ctx, slots=6, budget=tight).fits
        over = plan_vram(weights_gb=W, context_per_slot=ctx + 4096, slots=6, budget=tight)
        assert not over.fits, "the solver left a whole extra 4k of headroom"

    def t_max_slots_is_actually_feasible() -> None:
        n = max_slots(weights_gb=W, context_per_slot=4096, budget=tight)
        assert n >= 1
        assert plan_vram(weights_gb=W, context_per_slot=4096, slots=n, budget=tight).fits
        assert not plan_vram(weights_gb=W, context_per_slot=4096, slots=n + 1, budget=tight).fits

    def t_draft_costs_weights_and_kv() -> None:
        bare = plan_vram(weights_gb=W, context_per_slot=4096, slots=6, budget=tight)
        with_draft = plan_vram(
            weights_gb=W, context_per_slot=4096, slots=6, budget=tight,
            draft_weights_gb=0.35, draft_kib_per_token_f16=12.0,
        )
        assert with_draft.total_gb > bare.total_gb + 0.35

    def t_no_mmap_refused_under_tight() -> None:
        try:
            assert_mmap_allowed(no_mmap=True, weights_gb=W, budget=tight)
        except MmapRefused:
            return
        raise AssertionError("--no-mmap was allowed under a 4 GB host ceiling")

    def t_mmap_allowed_when_weights_fit_host() -> None:
        assert_mmap_allowed(no_mmap=True, weights_gb=1.0, budget=tight)
        assert_mmap_allowed(no_mmap=False, weights_gb=99.0, budget=tight)

    def t_feasible_plans_all_fit() -> None:
        plans = feasible_plans(weights_gb=W, budget=tight)
        assert plans
        assert all(p.fits for p in plans)

    def t_unknown_budget_raises() -> None:
        try:
            get_budget("ENORMOUS")
        except BudgetError:
            return
        raise AssertionError("an unknown budget resolved")

    def t_ram_verdict_shape() -> None:
        v = RamGovernor(tight).measure()
        assert v.ceiling_gb == 4.0
        json.dumps(v.as_dict())

    def t_sqlite_tuning_applies() -> None:
        conn = sqlite3.connect(":memory:")
        applied = RamGovernor(tight).tune_sqlite(conn)
        assert "cache_size" in applied
        conn.close()

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    print(f"memory_governor self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GrokTOPUS memory governor")
    ap.add_argument("--budget", default="TIGHT", help="STANDARD | TIGHT | ULTRA")
    ap.add_argument("--profile", default="S", help="profile key from config/profiles.json")
    ap.add_argument("--slots", type=int, default=6, help="KV slots for the batched plan")
    ap.add_argument("--report", action="store_true", help="print the budget report")
    ap.add_argument("--solve", action="store_true", help="list every configuration that fits")
    ap.add_argument("--json", action="store_true", help="emit JSON instead of text")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    budget = get_budget(args.budget)
    weights, provenance = measured_weights_gb(args.profile)

    if args.solve:
        plans = feasible_plans(weights_gb=weights, budget=budget)
        if args.json:
            print(json.dumps([p.as_dict() for p in plans], indent=2))
            return 0
        print(f"{len(plans)} configurations fit {budget.name} "
              f"({budget.vram_ceiling_gb:.2f} GB) with {weights:.2f} GB of weights")
        for p in plans[:25]:
            print("  " + p.verdict_line())
        return 0 if plans else 1

    if args.json:
        print(json.dumps({
            "budget": budget.as_dict(),
            "weights_gb": weights,
            "weights_provenance": provenance,
            "ram": RamGovernor(budget).report(),
        }, indent=2))
        return 0

    print(render_report(args.budget, profile_key=args.profile, slots=args.slots))
    floor = plan_vram(weights_gb=weights, context_per_slot=256, slots=1, budget=budget)
    return 0 if floor.fits else 1


if __name__ == "__main__":
    raise SystemExit(main())
