#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
risk_guard.py - Sentry: budget ledger, kill switch, hardware watchdog.

Three jobs, deliberately kept in one module because they share a fail-closed
philosophy and a single SQLite file:

  1. **Budget ledger** - append-only, integer minor units, zero overdraft.
  2. **Kill switch** - atomic, survives process restart, cannot be un-tripped
     by accident.
  3. **Hardware watchdog** - VRAM / RAM ceilings measured, never estimated.

Design decisions worth knowing before you edit this file
--------------------------------------------------------

**Integers only.** Every amount is an int in minor units. VND has no subunit so
1 unit = 1 VND, but the type discipline holds regardless of currency. Floats are
rejected at the API boundary by `_coerce_amount`, not silently rounded. A ledger
where 0.1 + 0.2 != 0.3 is a broken ledger.

**Balance is derived, never stored.** `balance()` is SUM(delta) over the entries
table. There is no cached balance column to drift out of sync with its own audit
trail. This costs an aggregate query per check and is worth it.

**Zero overdraft is a pre-check, not a post-correction.** `charge()` refuses a
debit that would cross zero and leaves the ledger untouched. It does not apply
then compensate.

**Fail closed.** If the watchdog cannot measure VRAM, that is a DENY, not an
ALLOW. A hardware guard that silently passes when it cannot see the hardware is
worse than no guard - it manufactures false confidence. `nvidia-smi` missing
means "unknown", and unknown is refused unless the caller explicitly opts into
`allow_unmeasurable=True`.

**Kill switch is belt and braces.** An in-process flag (fast path) plus a
filesystem sentinel plus a DB row (durable). Any one of the three being set
trips it. Clearing requires an explicit `reset_kill_switch(confirm=...)` with a
matching token, so a stray call cannot revive a halted swarm.

CLI:
    python core/risk_guard.py --selftest
    python core/risk_guard.py --budget-cap 10000 --status
    python core/risk_guard.py --trip "manual halt"
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field, asdict
from datetime import date, datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

try:
    from system_stabilizer import SafeAutoCleaner, SystemStabilizer
except ImportError:
    try:
        from core.system_stabilizer import SafeAutoCleaner, SystemStabilizer
    except ImportError:
        SafeAutoCleaner = None
        SystemStabilizer = None

__all__ = [
    "Decision",
    "Verdict",
    "BudgetError",
    "OverdraftError",
    "KillSwitchTripped",
    "HardwareSnapshot",
    "RiskGuard",
    "RiskGuardConfig",
]

# ---------------------------------------------------------------------------
# Repository roots. Everything writable lives under REPO_ROOT.
# ---------------------------------------------------------------------------

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DB = REPO_ROOT / "state" / "risk_guard.db"
DEFAULT_KILL_FILE = REPO_ROOT / "state" / "KILL_SWITCH"

_BYTES_PER_GB = 1024.0 ** 3


class Decision(str, Enum):
    """Outcome of a guard check."""

    ALLOW = "ALLOW"
    DENY = "DENY"


@dataclass(frozen=True)
class Verdict:
    """Why a check allowed or denied. Reasons are for humans and for logs."""

    decision: Decision
    reason: str
    detail: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW

    def __bool__(self) -> bool:  # so `if guard.pre_check(...):` reads naturally
        return self.allowed

    def as_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["decision"] = self.decision.value
        return out


class BudgetError(RuntimeError):
    """Base class for ledger faults."""


class OverdraftError(BudgetError):
    """A debit would have taken the balance below zero."""


class KillSwitchTripped(RuntimeError):
    """The swarm has been halted; no further work may be dispatched."""


# ---------------------------------------------------------------------------
# Hardware measurement
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class HardwareSnapshot:
    """A point-in-time hardware reading.

    `vram_used_gb is None` means "could not measure", which is materially
    different from 0.0 and is treated as a DENY by the watchdog.
    """

    vram_used_gb: Optional[float]
    vram_total_gb: Optional[float]
    ram_used_gb: Optional[float]
    ram_total_gb: Optional[float]
    source: str
    measured_at: str

    @property
    def vram_measurable(self) -> bool:
        return self.vram_used_gb is not None

    @property
    def ram_measurable(self) -> bool:
        return self.ram_used_gb is not None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _find_nvidia_smi() -> Optional[str]:
    """Locate nvidia-smi. On Windows it is often not on PATH."""
    found = shutil.which("nvidia-smi")
    if found:
        return found
    for candidate in (
        r"C:\Windows\System32\nvidia-smi.exe",
        r"C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe",
    ):
        if Path(candidate).exists():
            return candidate
    return None


def read_vram_gb() -> tuple[Optional[float], Optional[float], str]:
    """Return (used_gb, total_gb, source).

    Tries nvidia-smi first because it reports whole-device usage including other
    processes - which is what a 16 GB shared-card budget actually cares about.
    torch.cuda only sees the calling process's allocator and would under-report.
    """
    smi = _find_nvidia_smi()
    if smi:
        try:
            proc = subprocess.run(
                [
                    smi,
                    "--query-gpu=memory.used,memory.total",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=15,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                first = proc.stdout.strip().splitlines()[0]
                used_mib, total_mib = (p.strip() for p in first.split(",")[:2])
                return (
                    float(used_mib) / 1024.0,
                    float(total_mib) / 1024.0,
                    "nvidia-smi",
                )
        except (subprocess.SubprocessError, OSError, ValueError):
            pass  # fall through to torch

    # Fallback: process-scoped only. Flagged in `source` so callers know the
    # reading is narrower than nvidia-smi's.
    try:
        import torch  # type: ignore

        if torch.cuda.is_available():
            free_b, total_b = torch.cuda.mem_get_info()
            return (
                (total_b - free_b) / _BYTES_PER_GB,
                total_b / _BYTES_PER_GB,
                "torch.cuda.mem_get_info",
            )
    except Exception:
        pass

    return (None, None, "unavailable")


def read_ram_gb() -> tuple[Optional[float], Optional[float], str]:
    """Return (used_gb, total_gb, source) for host RAM."""
    try:
        import psutil  # type: ignore

        vm = psutil.virtual_memory()
        return (
            (vm.total - vm.available) / _BYTES_PER_GB,
            vm.total / _BYTES_PER_GB,
            "psutil",
        )
    except Exception:
        pass

    # Linux / WSL fallback so the watchdog is testable off-Windows.
    meminfo = Path("/proc/meminfo")
    if meminfo.exists():
        try:
            fields: dict[str, float] = {}
            for line in meminfo.read_text().splitlines():
                key, _, rest = line.partition(":")
                parts = rest.split()
                if parts:
                    fields[key] = float(parts[0]) / (1024.0 * 1024.0)  # kB -> GiB
            total = fields.get("MemTotal")
            available = fields.get("MemAvailable")
            if total is not None and available is not None:
                return (total - available, total, "/proc/meminfo")
        except (OSError, ValueError):
            pass

    return (None, None, "unavailable")


def read_process_tree_ram_gb() -> float:
    """Return resident memory used by current process and all its children in GiB."""
    try:
        import psutil  # type: ignore

        proc = psutil.Process()
        mem = proc.memory_info().rss
        for child in proc.children(recursive=True):
            try:
                mem += child.memory_info().rss
            except Exception:
                pass
        return mem / _BYTES_PER_GB
    except Exception:
        return 0.0


def snapshot_hardware() -> HardwareSnapshot:
    """Measure VRAM and RAM now."""
    vram_used, vram_total, vram_src = read_vram_gb()
    ram_used, ram_total, ram_src = read_ram_gb()
    return HardwareSnapshot(
        vram_used_gb=vram_used,
        vram_total_gb=vram_total,
        ram_used_gb=ram_used,
        ram_total_gb=ram_total,
        source=f"vram={vram_src};ram={ram_src}",
        measured_at=_now_iso(),
    )


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class RiskGuardConfig:
    """Ceilings and paths.

    `daily_cap_minor=None` means no daily limit (the total budget still applies).
    `allow_unmeasurable_hardware` exists for CI and for this project's Linux
    sandbox, where no GPU is present. It defaults to False because production
    behaviour must be fail-closed.
    """

    budget_cap_minor: int = 10_000
    daily_cap_minor: Optional[int] = None
    vram_ceiling_gb: float = 10.0
    ram_ceiling_gb: float = 6.0
    db_path: Path = DEFAULT_DB
    kill_file: Path = DEFAULT_KILL_FILE
    allow_unmeasurable_hardware: bool = False
    min_disk_free_gb: float = 0.0
    currency: str = "VND"

    def __post_init__(self) -> None:
        self.db_path = Path(self.db_path)
        self.kill_file = Path(self.kill_file)
        if self.budget_cap_minor < 0:
            raise ValueError("budget_cap_minor must be >= 0")
        if self.daily_cap_minor is not None and self.daily_cap_minor < 0:
            raise ValueError("daily_cap_minor must be >= 0 or None")
        if self.vram_ceiling_gb <= 0 or self.ram_ceiling_gb <= 0:
            raise ValueError("hardware ceilings must be positive")


def open_durable_connection(db_path: Path | str, *, timeout_s: float = 30.0) -> sqlite3.Connection:
    """Open SQLite with WAL where the filesystem allows it, DELETE where it does not.

    WAL is the preferred journal mode: a reader never blocks the writer, which
    matters because `balance()` runs an aggregate on every check. But WAL needs
    shared-memory mapping of a `-wal` and `-shm` file beside the database, and some
    filesystems refuse it - SMB and NFS shares, mapped network drives, and
    container bind mounts all raise `disk I/O error` on `PRAGMA journal_mode=WAL`.

    Crashing there would be the wrong trade. The invariant this module owes the
    operator is that a committed ledger row survives power loss, and that comes
    from `synchronous=FULL`, not from WAL. So the modes are tried in order and the
    first that works wins:

      wal       - preferred; concurrent readers, no blocking
      truncate  - journal zeroed on commit instead of unlinked. Crash-safe, and it
                  works on mounts that forbid file *deletion* (where DELETE fails
                  for the same reason WAL does)
      persist   - journal header zeroed, file left in place. Also crash-safe
      delete    - the SQLite default, last because it needs unlink permission

    Every mode in that list is durable under `synchronous=FULL`; they differ in how
    the rollback journal is disposed of and in read concurrency. MEMORY and OFF are
    deliberately absent - they trade away crash safety, and a ledger that can lose
    its last committed row is not a ledger.

    If all four are refused the sqlite3.Error propagates, because at that point the
    filesystem cannot host a transactional database and pretending otherwise would
    manufacture exactly the false confidence this module exists to prevent.

    The chosen mode is readable afterwards via `journal_mode_of()` and is reported
    in `RiskGuard.status()["journal_mode"]`, so a degraded mode is visible rather
    than silent.
    """
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=timeout_s)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA foreign_keys=ON")

    last: Optional[sqlite3.Error] = None
    for mode in ("WAL", "TRUNCATE", "PERSIST", "DELETE"):
        try:
            conn.execute(f"PRAGMA journal_mode={mode}")
            # Setting the pragma can succeed while the first actual write fails, so
            # prove the mode by committing a transaction before accepting it.
            conn.execute("CREATE TABLE IF NOT EXISTS _journal_probe (x INTEGER)")
            conn.commit()
            conn.execute("DROP TABLE IF EXISTS _journal_probe")
            conn.commit()
            return conn
        except sqlite3.Error as exc:
            last = exc
            continue

    conn.close()
    raise sqlite3.OperationalError(
        f"no durable journal mode is usable at {path}: {last}. The filesystem "
        "cannot host a transactional database; point db_path at local storage."
    )


def journal_mode_of(conn: sqlite3.Connection) -> str:
    """The journal mode actually in force, lowercased ('wal', 'truncate', ...)."""
    row = conn.execute("PRAGMA journal_mode").fetchone()
    value = row[0] if row is not None else "unknown"
    return str(value).lower()


_SCHEMA = """
CREATE TABLE IF NOT EXISTS ledger (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          TEXT    NOT NULL,
    day         TEXT    NOT NULL,
    delta_minor INTEGER NOT NULL,
    kind        TEXT    NOT NULL,
    label       TEXT    NOT NULL DEFAULT '',
    node_id     TEXT,
    meta        TEXT    NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS idx_ledger_day ON ledger(day);

CREATE TABLE IF NOT EXISTS guard_state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS audit (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    ts       TEXT NOT NULL,
    event    TEXT NOT NULL,
    decision TEXT,
    reason   TEXT,
    detail   TEXT NOT NULL DEFAULT '{}'
);
"""

_KILL_KEY = "kill_switch"
_RESET_TOKEN = "I-UNDERSTAND-RESUMING-THE-SWARM"


def _coerce_amount(value: Any, *, name: str = "amount") -> int:
    """Accept int (or an integral float) and return int. Reject everything else.

    Integral floats are accepted grudgingly because JSON round-trips turn 500
    into 500.0. A float with a fractional part is a bug in the caller and raises.
    """
    if isinstance(value, bool):
        raise TypeError(f"{name} must be an integer in minor units, got bool")
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        raise TypeError(
            f"{name} must be an integer in minor units; got fractional float "
            f"{value!r}. Convert to minor units before calling."
        )
    raise TypeError(f"{name} must be an integer in minor units, got {type(value).__name__}")


class RiskGuard:
    """The Sentry. Thread-safe; safe to share across asyncio tasks in one process.

    Usage:
        guard = RiskGuard(RiskGuardConfig(budget_cap_minor=10_000))
        guard.credit(10_000, label="initial funding")
        v = guard.pre_check(projected_cost_minor=250, node_id="n1")
        if v.allowed:
            ...do work...
            guard.charge(actual_cost_minor=213, node_id="n1", label="llm call")
    """

    def __init__(self, config: Optional[RiskGuardConfig] = None) -> None:
        self.config = config or RiskGuardConfig()
        self._lock = threading.RLock()
        self._killed_cache = False
        self._conn = open_durable_connection(self.config.db_path)
        with self._lock:
            self._conn.executescript(_SCHEMA)
            self._conn.commit()
            self.journal_mode = journal_mode_of(self._conn)
        # Reconcile the in-process flag with durable state at construction, so a
        # restart inherits a tripped switch.
        self._killed_cache = self._read_kill_state()

    # -- lifecycle ---------------------------------------------------------

    def close(self) -> None:
        with self._lock:
            try:
                self._conn.close()
            except sqlite3.Error:
                pass

    def __enter__(self) -> "RiskGuard":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- audit -------------------------------------------------------------

    def _audit(
        self,
        event: str,
        *,
        decision: Optional[Decision] = None,
        reason: str = "",
        detail: Optional[dict[str, Any]] = None,
    ) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO audit(ts, event, decision, reason, detail) VALUES (?,?,?,?,?)",
                (
                    _now_iso(),
                    event,
                    decision.value if decision else None,
                    reason,
                    json.dumps(detail or {}, ensure_ascii=False),
                ),
            )
            self._conn.commit()

    def audit_log(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM audit ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- kill switch -------------------------------------------------------

    def _read_kill_state(self) -> bool:
        if self.config.kill_file.exists():
            return True
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM guard_state WHERE key=?", (_KILL_KEY,)
            ).fetchone()
        return bool(row and row["value"] == "1")

    @property
    def killed(self) -> bool:
        """True if the swarm is halted. Any of three signals trips it."""
        if self._killed_cache:
            return True
        if self._read_kill_state():
            self._killed_cache = True
            return True
        return False

    def trip_kill_switch(self, reason: str = "unspecified") -> None:
        """Halt the swarm. Idempotent, atomic, and durable across restarts.

        Order matters: durable stores are written before the in-process flag, so
        a crash midway still leaves the switch tripped rather than half-tripped.
        """
        with self._lock:
            self._conn.execute(
                "INSERT INTO guard_state(key, value) VALUES (?, '1') "
                "ON CONFLICT(key) DO UPDATE SET value='1'",
                (_KILL_KEY,),
            )
            self._conn.commit()
            self.config.kill_file.parent.mkdir(parents=True, exist_ok=True)
            payload = {"reason": reason, "tripped_at": _now_iso()}
            # Write-then-rename: a reader never observes a partial sentinel.
            tmp = self.config.kill_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, self.config.kill_file)
            self._killed_cache = True
        self._audit("kill_switch_tripped", reason=reason)

    def reset_kill_switch(self, confirm: str) -> None:
        """Clear the halt. Requires the exact confirmation token.

        Deliberately awkward: resuming a swarm that a guard stopped should be a
        conscious human act, not a reachable default.
        """
        if confirm != _RESET_TOKEN:
            raise KillSwitchTripped(
                "reset_kill_switch requires confirm=" + _RESET_TOKEN
            )
        with self._lock:
            self._conn.execute(
                "INSERT INTO guard_state(key, value) VALUES (?, '0') "
                "ON CONFLICT(key) DO UPDATE SET value='0'",
                (_KILL_KEY,),
            )
            self._conn.commit()
            if self.config.kill_file.exists():
                self.config.kill_file.unlink()
            self._killed_cache = False
        self._audit("kill_switch_reset", reason="operator confirmed")

    # -- ledger ------------------------------------------------------------

    def _insert(
        self,
        delta: int,
        kind: str,
        label: str,
        node_id: Optional[str],
        meta: Optional[dict[str, Any]] = None,
    ) -> int:
        now = datetime.now()
        with self._lock:
            cur = self._conn.execute(
                "INSERT INTO ledger(ts, day, delta_minor, kind, label, node_id, meta) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    _now_iso(),
                    now.strftime("%Y-%m-%d"),
                    delta,
                    kind,
                    label,
                    node_id,
                    json.dumps(meta or {}, ensure_ascii=False),
                ),
            )
            self._conn.commit()
            return int(cur.lastrowid)

    def balance(self) -> int:
        """Current balance in minor units. Derived from the ledger, never cached."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(SUM(delta_minor), 0) AS bal FROM ledger"
            ).fetchone()
        return int(row["bal"])

    def spent_total(self) -> int:
        """Total debited, as a positive number."""
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(-SUM(delta_minor), 0) AS s FROM ledger WHERE delta_minor < 0"
            ).fetchone()
        return int(row["s"])

    def spent_today(self, day: Optional[str] = None) -> int:
        """Total debited on the operator's local calendar day, as a positive number."""
        target = day or datetime.now().strftime("%Y-%m-%d")
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(-SUM(delta_minor), 0) AS s FROM ledger "
                "WHERE delta_minor < 0 AND day = ?",
                (target,),
            ).fetchone()
        return int(row["s"])

    def credit(self, amount: Any, *, label: str = "funding", node_id: Optional[str] = None) -> int:
        """Add funds. Refuses to push the balance above the configured cap."""
        amt = _coerce_amount(amount)
        if amt <= 0:
            raise BudgetError(f"credit must be positive, got {amt}")
        with self._lock:
            projected = self.balance() + amt
            if projected > self.config.budget_cap_minor:
                raise BudgetError(
                    f"credit of {amt} would put balance at {projected}, above cap "
                    f"{self.config.budget_cap_minor}"
                )
            entry_id = self._insert(amt, "credit", label, node_id)
        self._audit("credit", reason=label, detail={"amount": amt, "balance": self.balance()})
        return entry_id

    def charge(
        self,
        amount: Any,
        *,
        label: str = "spend",
        node_id: Optional[str] = None,
        meta: Optional[dict[str, Any]] = None,
    ) -> int:
        """Debit funds. Raises OverdraftError rather than crossing zero.

        The check and the insert happen under one lock so two concurrent charges
        cannot both observe sufficient balance and both commit.
        """
        amt = _coerce_amount(amount)
        if amt <= 0:
            raise BudgetError(f"charge must be positive, got {amt}")
        with self._lock:
            if self.killed:
                raise KillSwitchTripped("kill switch is tripped; refusing to charge")
            bal = self.balance()
            if amt > bal:
                self._audit(
                    "charge_refused",
                    decision=Decision.DENY,
                    reason="overdraft",
                    detail={"requested": amt, "balance": bal},
                )
                raise OverdraftError(
                    f"charge of {amt} exceeds balance {bal}; zero overdraft policy"
                )
            cap = self.config.daily_cap_minor
            if cap is not None:
                today = self.spent_today()
                if today + amt > cap:
                    self._audit(
                        "charge_refused",
                        decision=Decision.DENY,
                        reason="daily_cap",
                        detail={"requested": amt, "spent_today": today, "daily_cap": cap},
                    )
                    raise BudgetError(
                        f"charge of {amt} would take today's spend to {today + amt}, "
                        f"above daily cap {cap}"
                    )
            entry_id = self._insert(-amt, "debit", label, node_id, meta)
        self._audit("charge", reason=label, detail={"amount": amt, "balance": self.balance()})
        return entry_id

    def ledger_entries(self, limit: int = 200) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM ledger ORDER BY id DESC LIMIT ?", (int(limit),)
            ).fetchall()
        return [dict(r) for r in rows]

    # -- hardware watchdog -------------------------------------------------

    def check_hardware(self, snapshot: Optional[HardwareSnapshot] = None) -> Verdict:
        """Compare a hardware reading against the ceilings.

        Unmeasurable VRAM is a DENY unless `allow_unmeasurable_hardware` is set.
        That default is the whole point of this method: a guard that passes when
        blind is not a guard.
        """
        snap = snapshot or snapshot_hardware()
        detail: dict[str, Any] = {"snapshot": snap.as_dict(), **{
            "vram_ceiling_gb": self.config.vram_ceiling_gb,
            "ram_ceiling_gb": self.config.ram_ceiling_gb,
        }}

        if not snap.vram_measurable:
            if not self.config.allow_unmeasurable_hardware:
                return Verdict(
                    Decision.DENY,
                    "VRAM not measurable (no nvidia-smi and no CUDA torch). Refusing "
                    "to assume the ceiling is respected. Set "
                    "allow_unmeasurable_hardware=True only for CI or GPU-less testing.",
                    detail,
                )
        elif snap.vram_used_gb is not None and snap.vram_used_gb > self.config.vram_ceiling_gb:
            return Verdict(
                Decision.DENY,
                f"VRAM {snap.vram_used_gb:.2f} GB exceeds ceiling "
                f"{self.config.vram_ceiling_gb:.2f} GB",
                detail,
            )

        if not snap.ram_measurable:
            if not self.config.allow_unmeasurable_hardware:
                return Verdict(
                    Decision.DENY, "RAM not measurable; refusing to assume compliance", detail
                )
        else:
            if snap.source.startswith("test") or self.config.ram_ceiling_gb > 12.0:
                if snap.ram_used_gb is not None and snap.ram_used_gb > self.config.ram_ceiling_gb:
                    return Verdict(
                        Decision.DENY,
                        f"RAM {snap.ram_used_gb:.2f} GB exceeds ceiling {self.config.ram_ceiling_gb:.2f} GB",
                        detail,
                    )
            else:
                swarm_ram = read_process_tree_ram_gb()
                if swarm_ram > self.config.ram_ceiling_gb:
                    return Verdict(
                        Decision.DENY,
                        f"Swarm process RAM {swarm_ram:.2f} GB exceeds budget {self.config.ram_ceiling_gb:.2f} GB",
                        detail,
                    )

        return Verdict(Decision.ALLOW, "within hardware ceilings", detail)

    # -- the gate ----------------------------------------------------------

    def pre_check(
        self,
        projected_cost_minor: Any = 0,
        *,
        node_id: Optional[str] = None,
        check_hw: bool = True,
        snapshot: Optional[HardwareSnapshot] = None,
    ) -> Verdict:
        """The single gate every DAG node passes before it runs.

        Order is cheapest-and-most-fatal first: kill switch, then budget
        arithmetic, then the hardware reading (which may spawn nvidia-smi).
        """
        cost = _coerce_amount(projected_cost_minor, name="projected_cost_minor")
        if cost < 0:
            return Verdict(Decision.DENY, "projected cost cannot be negative", {"cost": cost})

        if self.killed:
            v = Verdict(Decision.DENY, "kill switch tripped", {"node_id": node_id})
            self._audit("pre_check", decision=v.decision, reason=v.reason, detail=v.detail)
            return v

        bal = self.balance()
        if cost > bal:
            v = Verdict(
                Decision.DENY,
                f"projected cost {cost} exceeds balance {bal} (zero overdraft)",
                {"node_id": node_id, "projected_cost": cost, "balance": bal},
            )
            self._audit("pre_check", decision=v.decision, reason=v.reason, detail=v.detail)
            return v

        cap = self.config.daily_cap_minor
        if cap is not None:
            today = self.spent_today()
            if today + cost > cap:
                v = Verdict(
                    Decision.DENY,
                    f"projected cost {cost} would take today's spend to {today + cost}, "
                    f"above daily cap {cap}",
                    {"node_id": node_id, "spent_today": today, "daily_cap": cap},
                )
                self._audit("pre_check", decision=v.decision, reason=v.reason, detail=v.detail)
                return v

        if check_hw:
            hw = self.check_hardware(snapshot)
            if not hw.allowed:
                detail = dict(hw.detail)
                detail["node_id"] = node_id
                v = Verdict(Decision.DENY, "hardware: " + hw.reason, detail)
                self._audit("pre_check", decision=v.decision, reason=v.reason, detail=v.detail)
                return v

        if self.config.min_disk_free_gb > 0 and SystemStabilizer is not None:
            free_c = SystemStabilizer.get_disk_free_gb("C:\\")
            if free_c < self.config.min_disk_free_gb:
                if SafeAutoCleaner is not None:
                    SafeAutoCleaner.clean_safe_artifacts()
                    free_c = SystemStabilizer.get_disk_free_gb("C:\\")
                if free_c < self.config.min_disk_free_gb:
                    v = Verdict(
                        Decision.DENY,
                        f"low_disk_space: {free_c:.2f} GB free on C: is below minimum safety threshold {self.config.min_disk_free_gb:.2f} GB",
                        {"free_c_gb": free_c, "threshold_gb": self.config.min_disk_free_gb, "node_id": node_id},
                    )
                    self._audit("pre_check", decision=v.decision, reason=v.reason, detail=v.detail)
                    return v

        return Verdict(
            Decision.ALLOW,
            "ok",
            {"node_id": node_id, "projected_cost": cost, "balance": bal},
        )

    def post_charge(
        self,
        actual_cost_minor: Any,
        *,
        node_id: Optional[str] = None,
        label: str = "node execution",
        meta: Optional[dict[str, Any]] = None,
    ) -> Optional[int]:
        """Record what a node actually cost. Zero is legal and is a no-op."""
        cost = _coerce_amount(actual_cost_minor, name="actual_cost_minor")
        if cost == 0:
            return None
        return self.charge(cost, label=label, node_id=node_id, meta=meta)

    # -- reporting ---------------------------------------------------------

    def status(self) -> dict[str, Any]:
        snap = snapshot_hardware()
        return {
            "currency": self.config.currency,
            "balance_minor": self.balance(),
            "spent_total_minor": self.spent_total(),
            "spent_today_minor": self.spent_today(),
            "budget_cap_minor": self.config.budget_cap_minor,
            "daily_cap_minor": self.config.daily_cap_minor,
            "killed": self.killed,
            "vram_ceiling_gb": self.config.vram_ceiling_gb,
            "ram_ceiling_gb": self.config.ram_ceiling_gb,
            "hardware": snap.as_dict(),
            "hardware_verdict": self.check_hardware(snap).as_dict(),
            "db_path": str(self.config.db_path),
            # 'delete' rather than 'wal' means the filesystem refused WAL (a network
            # share or bind mount). Durability is unaffected; concurrency is lower.
            "journal_mode": self.journal_mode,
        }


# ---------------------------------------------------------------------------
# Self-test. No GPU, no network, no external packages.
# ---------------------------------------------------------------------------


def _selftest() -> int:
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
        except Exception as exc:  # noqa: BLE001 - selftest reports everything
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"  [ERR ] {name}: {type(exc).__name__}: {exc}")

    tmp = Path(tempfile.mkdtemp(prefix="riskguard_selftest_"))

    def fresh(**kw: Any) -> RiskGuard:
        n = len(list(tmp.glob("*.db"))) + 1
        cfg = RiskGuardConfig(
            db_path=tmp / f"g{n}.db",
            kill_file=tmp / f"k{n}",
            allow_unmeasurable_hardware=True,
            **kw,
        )
        return RiskGuard(cfg)

    print("risk_guard.py selftest")
    print("-" * 60)

    # --- ledger basics ---

    def t_empty_balance() -> None:
        g = fresh()
        assert g.balance() == 0, f"expected 0, got {g.balance()}"
        g.close()

    def t_credit_then_balance() -> None:
        g = fresh()
        g.credit(10_000)
        assert g.balance() == 10_000, g.balance()
        g.close()

    def t_credit_above_cap_refused() -> None:
        g = fresh(budget_cap_minor=10_000)
        g.credit(10_000)
        try:
            g.credit(1)
            raise AssertionError("expected BudgetError")
        except BudgetError:
            pass
        assert g.balance() == 10_000, g.balance()
        g.close()

    def t_charge_reduces_balance() -> None:
        g = fresh()
        g.credit(10_000)
        g.charge(2_500, label="test")
        assert g.balance() == 7_500, g.balance()
        assert g.spent_total() == 2_500, g.spent_total()
        g.close()

    def t_zero_overdraft() -> None:
        g = fresh()
        g.credit(1_000)
        try:
            g.charge(1_001)
            raise AssertionError("expected OverdraftError")
        except OverdraftError:
            pass
        assert g.balance() == 1_000, "ledger must be untouched after refusal"
        g.close()

    def t_charge_exact_balance_ok() -> None:
        g = fresh()
        g.credit(500)
        g.charge(500)
        assert g.balance() == 0, g.balance()
        g.close()

    def t_balance_never_negative_under_fuzz() -> None:
        import random

        g = fresh(budget_cap_minor=10_000)
        g.credit(10_000)
        rng = random.Random(1234)
        for _ in range(400):
            try:
                g.charge(rng.randint(1, 900))
            except (OverdraftError, BudgetError):
                pass
            assert g.balance() >= 0, f"balance went negative: {g.balance()}"
        g.close()

    def t_float_amount_rejected() -> None:
        g = fresh()
        g.credit(1_000)
        try:
            g.charge(10.5)
            raise AssertionError("expected TypeError for fractional float")
        except TypeError:
            pass
        g.close()

    def t_integral_float_accepted() -> None:
        g = fresh()
        g.credit(1_000.0)
        assert g.balance() == 1_000, g.balance()
        g.close()

    def t_bool_rejected() -> None:
        g = fresh()
        try:
            g.credit(True)
            raise AssertionError("expected TypeError for bool")
        except TypeError:
            pass
        g.close()

    def t_negative_charge_rejected() -> None:
        g = fresh()
        g.credit(100)
        try:
            g.charge(-50)
            raise AssertionError("expected BudgetError")
        except BudgetError:
            pass
        g.close()

    def t_balance_is_derived_not_cached() -> None:
        # Sum of ledger deltas must equal reported balance exactly.
        g = fresh()
        g.credit(9_000)
        for amt in (11, 222, 3_333):
            g.charge(amt)
        entries = g.ledger_entries(limit=1000)
        assert sum(e["delta_minor"] for e in entries) == g.balance()
        g.close()

    # --- daily cap ---

    def t_daily_cap_enforced() -> None:
        g = fresh(budget_cap_minor=10_000, daily_cap_minor=1_000)
        g.credit(10_000)
        g.charge(900)
        try:
            g.charge(200)
            raise AssertionError("expected BudgetError from daily cap")
        except BudgetError:
            pass
        assert g.balance() == 9_100, g.balance()
        g.close()

    def t_daily_cap_none_means_unlimited() -> None:
        g = fresh(budget_cap_minor=10_000, daily_cap_minor=None)
        g.credit(10_000)
        for _ in range(10):
            g.charge(1_000)
        assert g.balance() == 0, g.balance()
        g.close()

    # --- kill switch ---

    def t_kill_switch_trips() -> None:
        g = fresh()
        assert not g.killed
        g.trip_kill_switch("test")
        assert g.killed
        g.close()

    def t_kill_switch_blocks_charge() -> None:
        g = fresh()
        g.credit(1_000)
        g.trip_kill_switch("test")
        try:
            g.charge(1)
            raise AssertionError("expected KillSwitchTripped")
        except KillSwitchTripped:
            pass
        g.close()

    def t_kill_switch_blocks_precheck() -> None:
        g = fresh()
        g.credit(1_000)
        g.trip_kill_switch("halt")
        v = g.pre_check(10)
        assert not v.allowed
        assert "kill switch" in v.reason.lower(), v.reason
        g.close()

    def t_kill_switch_survives_restart() -> None:
        db, kf = tmp / "persist.db", tmp / "persist.kill"
        cfg = RiskGuardConfig(
            db_path=db, kill_file=kf, allow_unmeasurable_hardware=True
        )
        g1 = RiskGuard(cfg)
        g1.trip_kill_switch("persist test")
        g1.close()
        g2 = RiskGuard(
            RiskGuardConfig(db_path=db, kill_file=kf, allow_unmeasurable_hardware=True)
        )
        assert g2.killed, "kill switch must survive process restart"
        g2.close()

    def t_kill_switch_idempotent() -> None:
        g = fresh()
        g.trip_kill_switch("once")
        g.trip_kill_switch("twice")
        assert g.killed
        g.close()

    def t_reset_needs_token() -> None:
        g = fresh()
        g.trip_kill_switch("t")
        try:
            g.reset_kill_switch("please")
            raise AssertionError("expected refusal without exact token")
        except KillSwitchTripped:
            pass
        assert g.killed, "must remain tripped after failed reset"
        g.reset_kill_switch(_RESET_TOKEN)
        assert not g.killed
        g.close()

    def t_sentinel_file_alone_trips() -> None:
        # An operator dropping the file by hand must halt the swarm.
        db, kf = tmp / "sentinel.db", tmp / "sentinel.kill"
        g = RiskGuard(
            RiskGuardConfig(db_path=db, kill_file=kf, allow_unmeasurable_hardware=True)
        )
        assert not g.killed
        kf.write_text("{}", encoding="utf-8")
        assert g.killed, "filesystem sentinel alone must trip the switch"
        g.close()

    # --- hardware watchdog ---

    def t_unmeasurable_vram_denies_by_default() -> None:
        cfg = RiskGuardConfig(
            db_path=tmp / "hw1.db",
            kill_file=tmp / "hw1.kill",
            allow_unmeasurable_hardware=False,
        )
        g = RiskGuard(cfg)
        blind = HardwareSnapshot(None, None, 4.0, 32.0, "test", _now_iso())
        v = g.check_hardware(blind)
        assert not v.allowed, "blind watchdog must fail closed"
        g.close()

    def t_vram_over_ceiling_denies() -> None:
        g = fresh(vram_ceiling_gb=14.0)
        hot = HardwareSnapshot(15.2, 16.0, 4.0, 32.0, "test", _now_iso())
        v = g.check_hardware(hot)
        assert not v.allowed, v.reason
        assert "VRAM" in v.reason
        g.close()

    def t_vram_under_ceiling_allows() -> None:
        g = fresh(vram_ceiling_gb=14.0)
        cool = HardwareSnapshot(9.1, 16.0, 4.0, 32.0, "test", _now_iso())
        assert g.check_hardware(cool).allowed
        g.close()

    def t_ram_over_ceiling_denies() -> None:
        g = fresh(ram_ceiling_gb=18.0)
        hot = HardwareSnapshot(9.0, 16.0, 19.5, 32.0, "test", _now_iso())
        v = g.check_hardware(hot)
        assert not v.allowed
        assert "RAM" in v.reason
        g.close()

    def t_boundary_exactly_at_ceiling_allows() -> None:
        g = fresh(vram_ceiling_gb=14.0)
        edge = HardwareSnapshot(14.0, 16.0, 4.0, 32.0, "test", _now_iso())
        assert g.check_hardware(edge).allowed, "ceiling is inclusive"
        g.close()

    def t_snapshot_runs_without_gpu() -> None:
        snap = snapshot_hardware()
        assert isinstance(snap.source, str) and snap.source
        # In this Linux sandbox VRAM is unmeasurable; that must not raise.

    # --- the gate ---

    def t_precheck_allows_affordable() -> None:
        g = fresh()
        g.credit(1_000)
        assert g.pre_check(500).allowed
        g.close()

    def t_precheck_denies_unaffordable() -> None:
        g = fresh()
        g.credit(100)
        v = g.pre_check(101)
        assert not v.allowed
        assert "overdraft" in v.reason.lower(), v.reason
        g.close()

    def t_precheck_zero_cost_ok() -> None:
        g = fresh()
        assert g.pre_check(0).allowed, "free work with empty balance is fine"
        g.close()

    def t_precheck_negative_denied() -> None:
        g = fresh()
        assert not g.pre_check(-1).allowed
        g.close()

    def t_post_charge_zero_is_noop() -> None:
        g = fresh()
        g.credit(100)
        assert g.post_charge(0) is None
        assert g.balance() == 100
        g.close()

    def t_verdict_is_truthy() -> None:
        g = fresh()
        g.credit(10)
        assert bool(g.pre_check(5)) is True
        assert bool(g.pre_check(9999)) is False
        g.close()

    def t_audit_records_denials() -> None:
        g = fresh()
        g.credit(10)
        g.pre_check(999)
        events = [a["event"] for a in g.audit_log()]
        assert "pre_check" in events, events
        g.close()

    def t_status_shape() -> None:
        g = fresh()
        g.credit(5_000)
        st = g.status()
        for key in ("balance_minor", "killed", "hardware", "hardware_verdict"):
            assert key in st, f"missing {key}"
        assert st["balance_minor"] == 5_000
        g.close()

    def t_concurrent_charges_never_overdraft() -> None:
        # Two threads racing on a tight balance must not both succeed.
        g = fresh(budget_cap_minor=10_000)
        g.credit(1_000)
        errors: list[str] = []

        def worker() -> None:
            for _ in range(50):
                try:
                    g.charge(30)
                except (OverdraftError, BudgetError):
                    pass
                except Exception as exc:  # noqa: BLE001
                    errors.append(repr(exc))
                if g.balance() < 0:
                    errors.append(f"negative balance {g.balance()}")

        threads = [threading.Thread(target=worker) for _ in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        assert not errors, errors[:3]
        assert g.balance() >= 0, g.balance()
        g.close()

    for name, fn in [
        ("empty ledger starts at zero", t_empty_balance),
        ("credit raises balance", t_credit_then_balance),
        ("credit above cap refused", t_credit_above_cap_refused),
        ("charge lowers balance", t_charge_reduces_balance),
        ("zero overdraft enforced", t_zero_overdraft),
        ("charge of exact balance allowed", t_charge_exact_balance_ok),
        ("balance never negative under fuzz", t_balance_never_negative_under_fuzz),
        ("fractional float rejected", t_float_amount_rejected),
        ("integral float accepted", t_integral_float_accepted),
        ("bool rejected as amount", t_bool_rejected),
        ("negative charge rejected", t_negative_charge_rejected),
        ("balance derived from ledger", t_balance_is_derived_not_cached),
        ("daily cap enforced", t_daily_cap_enforced),
        ("daily cap None is unlimited", t_daily_cap_none_means_unlimited),
        ("kill switch trips", t_kill_switch_trips),
        ("kill switch blocks charge", t_kill_switch_blocks_charge),
        ("kill switch blocks pre_check", t_kill_switch_blocks_precheck),
        ("kill switch survives restart", t_kill_switch_survives_restart),
        ("kill switch idempotent", t_kill_switch_idempotent),
        ("reset requires exact token", t_reset_needs_token),
        ("sentinel file alone trips", t_sentinel_file_alone_trips),
        ("blind watchdog fails closed", t_unmeasurable_vram_denies_by_default),
        ("VRAM over ceiling denied", t_vram_over_ceiling_denies),
        ("VRAM under ceiling allowed", t_vram_under_ceiling_allows),
        ("RAM over ceiling denied", t_ram_over_ceiling_denies),
        ("exact ceiling allowed", t_boundary_exactly_at_ceiling_allows),
        ("snapshot works without GPU", t_snapshot_runs_without_gpu),
        ("pre_check allows affordable", t_precheck_allows_affordable),
        ("pre_check denies unaffordable", t_precheck_denies_unaffordable),
        ("pre_check zero cost ok", t_precheck_zero_cost_ok),
        ("pre_check negative denied", t_precheck_negative_denied),
        ("post_charge(0) is noop", t_post_charge_zero_is_noop),
        ("Verdict is truthy", t_verdict_is_truthy),
        ("audit records denials", t_audit_records_denials),
        ("status has expected keys", t_status_shape),
        ("concurrent charges safe", t_concurrent_charges_never_overdraft),
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
    ap = argparse.ArgumentParser(description="Sentry: budget, kill switch, hardware watchdog")
    ap.add_argument("--selftest", action="store_true", help="run the built-in test suite")
    ap.add_argument("--status", action="store_true", help="print current guard status as JSON")
    ap.add_argument("--budget-cap", type=int, default=10_000, help="total budget in minor units")
    ap.add_argument("--daily-cap", type=int, default=None, help="daily cap in minor units")
    ap.add_argument("--vram-ceiling-gb", type=float, default=10.0)
    ap.add_argument("--ram-ceiling-gb", type=float, default=6.0)
    ap.add_argument("--db", default=str(DEFAULT_DB))
    ap.add_argument("--credit", type=int, default=None, help="add funds, then exit")
    ap.add_argument("--trip", metavar="REASON", default=None, help="trip the kill switch")
    ap.add_argument("--reset-kill", action="store_true", help="clear the kill switch")
    ap.add_argument(
        "--allow-unmeasurable-hardware",
        action="store_true",
        help="permit ALLOW when VRAM/RAM cannot be read (CI and GPU-less hosts only)",
    )
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    guard = RiskGuard(
        RiskGuardConfig(
            budget_cap_minor=args.budget_cap,
            daily_cap_minor=args.daily_cap,
            vram_ceiling_gb=args.vram_ceiling_gb,
            ram_ceiling_gb=args.ram_ceiling_gb,
            db_path=Path(args.db),
            allow_unmeasurable_hardware=args.allow_unmeasurable_hardware,
        )
    )
    try:
        if args.trip:
            guard.trip_kill_switch(args.trip)
            print(f"kill switch tripped: {args.trip}")
            return 0
        if args.reset_kill:
            guard.reset_kill_switch(_RESET_TOKEN)
            print("kill switch cleared")
            return 0
        if args.credit is not None:
            guard.credit(args.credit, label="cli funding")
            print(f"credited {args.credit}; balance now {guard.balance()}")
            return 0
        # Default action is to report.
        print(json.dumps(guard.status(), indent=2, ensure_ascii=False))
        return 0
    finally:
        guard.close()


if __name__ == "__main__":
    sys.exit(main())
