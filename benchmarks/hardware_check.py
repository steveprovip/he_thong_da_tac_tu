#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hardware_check.py - does this machine actually have the headroom the plan assumes?

Why this exits non-zero when it cannot measure
----------------------------------------------
A hardware check that passes on a machine with no GPU is worse than no check: it
converts "I don't know" into "you're fine", and every downstream VRAM budget is
then built on a number nobody verified. So the exit codes are:

    0  every required check measured and passed
    1  a check measured a real problem (over budget, insufficient headroom)
    2  a required check could not be measured at all

Exit 2 is the interesting one. `--allow-unmeasurable` downgrades it to a warning
for CI on GPU-less runners, and it is off by default for the same reason
`RiskGuard.allow_unmeasurable_hardware` is off by default.

What is measured vs. what is arithmetic
---------------------------------------
Measured: RAM totals, CPU topology, disk free space, GGUF file sizes on disk,
VRAM totals and current usage via nvidia-smi, and a memory-bandwidth figure from
a large-array copy.

Arithmetic, clearly labelled as such: projected VRAM per profile. That number is
`weights_on_disk + kv_cache + cuda_overhead`, where cuda_overhead is 0.55 GB -
an ESTIMATE, per BRAIN.md §7, not a measurement. The report says so on the line
where it is used, because the difference between 12.1 GB projected and 12.1 GB
observed is the difference between a plan and a fact.

The bandwidth number is a CPU/RAM figure
----------------------------------------
This script has no CUDA kernels, so it cannot measure the 5060 Ti's 448 GB/s. It
measures host memory bandwidth, which bounds how fast weights can be staged from
RAM. It is reported under its own heading so it is not mistaken for the GPU
figure, and the BRAIN.md efficiency ratio eta=0.852 is quoted as prior measured
context rather than recomputed here.

CLI:
    python benchmarks/hardware_check.py                     # full report
    python benchmarks/hardware_check.py --json
    python benchmarks/hardware_check.py --profile Q
    python benchmarks/hardware_check.py --allow-unmeasurable
    python benchmarks/hardware_check.py --selftest
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "core"))
    from local_llm_client import ProfileRegistry, estimate_vram_gb  # type: ignore
    from risk_guard import read_ram_gb, read_vram_gb  # type: ignore
else:  # pragma: no cover
    from ..core.local_llm_client import ProfileRegistry, estimate_vram_gb
    from ..core.risk_guard import read_ram_gb, read_vram_gb

__all__ = [
    "CheckStatus",
    "CheckResult",
    "HardwareReport",
    "run_all_checks",
    "EXIT_OK",
    "EXIT_FAIL",
    "EXIT_UNMEASURABLE",
]

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = REPO_ROOT / "config" / "profiles.json"

EXIT_OK = 0
EXIT_FAIL = 1
EXIT_UNMEASURABLE = 2

# Budget anchors from the directive. Ceilings are hard; retentions are what must
# be left over for the OS, the display, and the IDE.
VRAM_CEILING_GB = 10.0
VRAM_RETAIN_GB = 2.0
RAM_CEILING_GB = 6.0
RAM_RETAIN_GB = 14.0
MIN_PHYSICAL_CORES = 6
MAX_WORKER_THREADS = 8
MIN_DISK_FREE_GB = 20.0

#: BRAIN.md §7 lists this as an estimate, not a measurement. Kept as a named
#: constant so the report can say which numbers depend on it.
CUDA_OVERHEAD_GB_ESTIMATE = 0.55

#: BRAIN.md §3: 35.0 tok/s observed against 448/10.9 theoretical on this card.
BRAIN_BANDWIDTH_EFFICIENCY = 0.852
GPU_SPEC_BANDWIDTH_GB_S = 448.0


class CheckStatus(str, Enum):
    PASS = "PASS"
    FAIL = "FAIL"
    WARN = "WARN"
    UNMEASURABLE = "UNMEASURABLE"
    SKIP = "SKIP"


@dataclass
class CheckResult:
    name: str
    status: CheckStatus
    detail: str
    #: True when a FAIL or UNMEASURABLE here should change the exit code.
    required: bool = True
    measured: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status in (CheckStatus.PASS, CheckStatus.WARN, CheckStatus.SKIP)

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "status": self.status.value,
            "detail": self.detail,
            "required": self.required,
            "measured": self.measured,
        }


@dataclass
class HardwareReport:
    results: list[CheckResult] = field(default_factory=list)
    host: dict[str, Any] = field(default_factory=dict)
    allow_unmeasurable: bool = False

    def add(self, result: CheckResult) -> CheckResult:
        self.results.append(result)
        return result

    @property
    def failures(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is CheckStatus.FAIL and r.required]

    @property
    def unmeasurable(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is CheckStatus.UNMEASURABLE and r.required]

    @property
    def warnings(self) -> list[CheckResult]:
        return [r for r in self.results if r.status is CheckStatus.WARN]

    def exit_code(self) -> int:
        """Failures outrank unmeasurables: a known problem is worse than an unknown."""
        if self.failures:
            return EXIT_FAIL
        if self.unmeasurable and not self.allow_unmeasurable:
            return EXIT_UNMEASURABLE
        return EXIT_OK

    def as_dict(self) -> dict[str, Any]:
        return {
            "host": self.host,
            "exit_code": self.exit_code(),
            "verdict": {
                EXIT_OK: "READY",
                EXIT_FAIL: "OVER BUDGET",
                EXIT_UNMEASURABLE: "UNVERIFIED",
            }[self.exit_code()],
            "allow_unmeasurable": self.allow_unmeasurable,
            "counts": {
                s.value: sum(1 for r in self.results if r.status is s) for s in CheckStatus
            },
            "checks": [r.as_dict() for r in self.results],
        }


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------


def host_info() -> dict[str, Any]:
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "machine": platform.machine(),
        "processor": platform.processor() or "unknown",
        "logical_cpus": os.cpu_count() or 0,
        "checked_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }


def check_cpu() -> CheckResult:
    """Logical CPU count against the 6C/12T anchor.

    Only logical CPUs are portable to read; physical core count needs platform
    tools. 12 logical on a 6-core part is the expected shape, so the check tests
    logical >= 2 * MIN_PHYSICAL_CORES and reports what it actually saw.
    """
    logical = os.cpu_count() or 0
    if logical == 0:
        return CheckResult(
            name="cpu.topology",
            status=CheckStatus.UNMEASURABLE,
            detail="os.cpu_count() returned None",
        )
    expected = MIN_PHYSICAL_CORES * 2
    measured = {"logical_cpus": logical, "expected_logical": expected,
                "max_worker_threads": MAX_WORKER_THREADS}
    if logical < expected:
        return CheckResult(
            name="cpu.topology",
            status=CheckStatus.WARN,
            detail=(
                f"{logical} logical CPUs, expected {expected} for a "
                f"{MIN_PHYSICAL_CORES}C/{expected}T part. Worker threads should be "
                f"capped at min({MAX_WORKER_THREADS}, {logical})"
            ),
            required=False,
            measured=measured,
        )
    return CheckResult(
        name="cpu.topology",
        status=CheckStatus.PASS,
        detail=f"{logical} logical CPUs; worker cap {MAX_WORKER_THREADS} leaves headroom for the OS",
        measured=measured,
    )


def check_ram() -> CheckResult:
    """Total and free RAM against the 18 GB budget / 14 GB retention."""
    used, total, source = read_ram_gb()
    if total is None:
        return CheckResult(
            name="ram.headroom",
            status=CheckStatus.UNMEASURABLE,
            detail=f"could not read system memory totals (source: {source})",
            measured={"source": source},
        )
    free = total - (used or 0.0)
    measured = {
        "total_gb": round(total, 2),
        "used_gb": round(used or 0.0, 2),
        "free_gb": round(free, 2),
        "ceiling_gb": RAM_CEILING_GB,
        "retain_gb": RAM_RETAIN_GB,
        "source": source,
    }
    if total < RAM_CEILING_GB + RAM_RETAIN_GB - 4.0:
        # 32 GB machine: 18 + 14 = 32 exactly. Allow slack for reported-vs-installed.
        return CheckResult(
            name="ram.headroom",
            status=CheckStatus.FAIL,
            detail=(
                f"{total:.1f} GB installed cannot hold an {RAM_CEILING_GB:.0f} GB swarm "
                f"budget while retaining {RAM_RETAIN_GB:.0f} GB for the OS"
            ),
            measured=measured,
        )
    if free < RAM_CEILING_GB:
        return CheckResult(
            name="ram.headroom",
            status=CheckStatus.WARN,
            detail=(
                f"{free:.1f} GB free is below the {RAM_CEILING_GB:.0f} GB budget. "
                f"Close applications before a full run, or lower the budget"
            ),
            required=False,
            measured=measured,
        )
    return CheckResult(
        name="ram.headroom",
        status=CheckStatus.PASS,
        detail=f"{free:.1f} GB free of {total:.1f} GB; budget {RAM_CEILING_GB:.0f} GB fits",
        measured=measured,
    )


def check_vram() -> CheckResult:
    """VRAM totals and current usage. UNMEASURABLE when there is no GPU to ask."""
    used, total, source = read_vram_gb()
    if total is None:
        return CheckResult(
            name="vram.headroom",
            status=CheckStatus.UNMEASURABLE,
            detail=(
                f"no VRAM reading available (source: {source}). Without this, every "
                f"projected VRAM figure below is unverified arithmetic"
            ),
            measured={"source": source},
        )
    free = total - (used or 0.0)
    measured = {
        "total_gb": round(total, 2),
        "used_gb": round(used or 0.0, 2),
        "free_gb": round(free, 2),
        "ceiling_gb": VRAM_CEILING_GB,
        "retain_gb": VRAM_RETAIN_GB,
        "source": source,
    }
    if total < VRAM_CEILING_GB + VRAM_RETAIN_GB - 0.5:
        return CheckResult(
            name="vram.headroom",
            status=CheckStatus.FAIL,
            detail=(
                f"{total:.2f} GB VRAM cannot hold a {VRAM_CEILING_GB:.1f} GB ceiling "
                f"plus a {VRAM_RETAIN_GB:.1f} GB display buffer"
            ),
            measured=measured,
        )
    if free < VRAM_CEILING_GB:
        return CheckResult(
            name="vram.headroom",
            status=CheckStatus.WARN,
            detail=(
                f"{free:.2f} GB free of {total:.2f} GB. {used:.2f} GB is already in use, "
                f"leaving less than the {VRAM_CEILING_GB:.1f} GB ceiling. Another process "
                f"may hold the card"
            ),
            required=False,
            measured=measured,
        )
    return CheckResult(
        name="vram.headroom",
        status=CheckStatus.PASS,
        detail=(
            f"{free:.2f} GB free of {total:.2f} GB via {source}; "
            f"ceiling {VRAM_CEILING_GB:.1f} GB fits with {VRAM_RETAIN_GB:.1f} GB retained"
        ),
        measured=measured,
    )


def check_disk(path: Path = REPO_ROOT) -> CheckResult:
    """Free space on the workspace volume. Everything is written here by policy."""
    try:
        usage = shutil.disk_usage(str(path))
    except OSError as exc:
        return CheckResult(
            name="disk.free",
            status=CheckStatus.UNMEASURABLE,
            detail=f"cannot stat {path}: {exc}",
            required=False,
        )
    free_gb = usage.free / (1024**3)
    measured = {
        "path": str(path),
        "free_gb": round(free_gb, 2),
        "total_gb": round(usage.total / (1024**3), 2),
        "min_free_gb": MIN_DISK_FREE_GB,
    }
    if free_gb < MIN_DISK_FREE_GB:
        return CheckResult(
            name="disk.free",
            status=CheckStatus.WARN,
            detail=(
                f"{free_gb:.1f} GB free on {path}, below the {MIN_DISK_FREE_GB:.0f} GB "
                f"working margin for state DBs, logs, and generated assets"
            ),
            required=False,
            measured=measured,
        )
    return CheckResult(
        name="disk.free",
        status=CheckStatus.PASS,
        detail=f"{free_gb:.1f} GB free on {path}",
        required=False,
        measured=measured,
    )


def measure_host_bandwidth_gb_s(mib: int = 128, repeats: int = 3) -> Optional[float]:
    """Rough host memory bandwidth from a bytearray copy.

    Not a GPU measurement and not a rigorous STREAM benchmark - a single-threaded
    copy on a shared machine. Useful as an order-of-magnitude sanity check
    (single-channel DDR4 lands near 10 GB/s, dual-channel near 20+), nothing more.
    """
    try:
        size = mib * 1024 * 1024
        src = bytearray(os.urandom(min(size, 8 * 1024 * 1024)))
        src = src * max(1, size // len(src))
        best = 0.0
        for _ in range(repeats):
            start = time.perf_counter()
            dst = bytes(src)  # one full read + one full write
            elapsed = time.perf_counter() - start
            del dst
            if elapsed > 0:
                # Two passes over the buffer: read source, write destination.
                gb_s = (2 * len(src) / (1024**3)) / elapsed
                best = max(best, gb_s)
        return best or None
    except (MemoryError, OSError):
        return None


def check_bandwidth() -> CheckResult:
    """Host RAM bandwidth. Advisory only - the GPU figure needs CUDA."""
    gb_s = measure_host_bandwidth_gb_s()
    if gb_s is None:
        return CheckResult(
            name="bandwidth.host_ram",
            status=CheckStatus.UNMEASURABLE,
            detail="host memory copy benchmark could not allocate",
            required=False,
        )
    return CheckResult(
        name="bandwidth.host_ram",
        status=CheckStatus.PASS,
        detail=(
            f"host RAM copy ~{gb_s:.1f} GB/s (single-threaded, advisory). "
            f"This is NOT the GPU's {GPU_SPEC_BANDWIDTH_GB_S:.0f} GB/s - measuring that "
            f"needs CUDA. BRAIN.md §3 already measured GPU efficiency eta={BRAIN_BANDWIDTH_EFFICIENCY}"
        ),
        required=False,
        measured={
            "host_copy_gb_s": round(gb_s, 2),
            "gpu_spec_gb_s": GPU_SPEC_BANDWIDTH_GB_S,
            "gpu_efficiency_from_brain_md": BRAIN_BANDWIDTH_EFFICIENCY,
            "note": "host figure only; GPU bandwidth not measured here",
        },
    )


def check_nvidia_smi() -> CheckResult:
    """Is the tooling present at all? Separated from the VRAM reading itself so a
    missing driver is distinguishable from a driver that reports odd numbers."""
    exe = shutil.which("nvidia-smi")
    if exe is None:
        return CheckResult(
            name="gpu.nvidia_smi",
            status=CheckStatus.UNMEASURABLE,
            detail="nvidia-smi not on PATH; no NVIDIA driver tooling to query",
            measured={},
        )
    try:
        out = subprocess.run(
            [exe, "--query-gpu=name,driver_version,memory.total", "--format=csv,noheader"],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return CheckResult(
            name="gpu.nvidia_smi",
            status=CheckStatus.UNMEASURABLE,
            detail=f"nvidia-smi present but failed: {type(exc).__name__}: {exc}",
        )
    if out.returncode != 0:
        return CheckResult(
            name="gpu.nvidia_smi",
            status=CheckStatus.UNMEASURABLE,
            detail=f"nvidia-smi exited {out.returncode}: {(out.stderr or '').strip()[:200]}",
        )
    lines = [ln.strip() for ln in out.stdout.splitlines() if ln.strip()]
    return CheckResult(
        name="gpu.nvidia_smi",
        status=CheckStatus.PASS,
        detail="; ".join(lines) or "nvidia-smi responded with no GPU rows",
        measured={"gpus": lines, "count": len(lines)},
    )


def check_profile_footprints(
    config_path: Path = DEFAULT_CONFIG, only: Optional[str] = None
) -> list[CheckResult]:
    """Project VRAM per profile from the GGUF actually on disk.

    Weights are measured (file size). KV cache and CUDA overhead are computed,
    and the overhead term is an estimate - so every result here is labelled
    PROJECTED, never observed.
    """
    results: list[CheckResult] = []
    try:
        registry = ProfileRegistry(config_path)
    except Exception as exc:  # noqa: BLE001
        results.append(
            CheckResult(
                name="profiles.registry",
                status=CheckStatus.UNMEASURABLE,
                detail=f"cannot load {config_path}: {type(exc).__name__}: {exc}",
            )
        )
        return results

    keys = [only] if only else list(registry.specs)
    for key in keys:
        spec = registry.specs.get(key)
        if spec is None:
            results.append(
                CheckResult(
                    name=f"profile.{key}",
                    status=CheckStatus.FAIL,
                    detail=f"no such profile {key!r}; known: {sorted(registry.specs)}",
                )
            )
            continue
        try:
            resolved = registry.require(key)
        except Exception as exc:  # noqa: BLE001 - typically ModelNotFoundError
            results.append(
                CheckResult(
                    name=f"profile.{key}",
                    status=CheckStatus.UNMEASURABLE,
                    detail=f"weights not on disk: {str(exc).splitlines()[0]}",
                    required=False,
                    measured={"model_file": spec.model_file},
                )
            )
            continue

        est = estimate_vram_gb(
            resolved.size_gb,
            spec.context,
            spec.kv_type,
            overhead_gb=CUDA_OVERHEAD_GB_ESTIMATE,
        )
        measured = {
            "model_path": str(resolved.path),
            "weights_gb_measured": round(resolved.size_gb, 3),
            "weights_gb_claimed": round(spec.disk_gb, 3) if spec.disk_gb else None,
            "context": spec.context,
            "kv_type": spec.kv_type,
            "kv_cache_gb_computed": round(est.kv_cache_gb, 3),
            "cuda_overhead_gb_ESTIMATE": CUDA_OVERHEAD_GB_ESTIMATE,
            "total_gb_PROJECTED": round(est.total_gb, 3),
            "ceiling_gb": VRAM_CEILING_GB,
            "confidence": spec.confidence,
        }
        headroom = VRAM_CEILING_GB - est.total_gb
        if est.total_gb > VRAM_CEILING_GB:
            status = CheckStatus.FAIL
            detail = (
                f"PROJECTED {est.total_gb:.2f} GB exceeds the {VRAM_CEILING_GB:.1f} GB "
                f"ceiling by {-headroom:.2f} GB "
                f"({resolved.size_gb:.2f} weights + {est.kv_cache_gb:.2f} KV @ctx "
                f"{spec.context} {spec.kv_type} + {CUDA_OVERHEAD_GB_ESTIMATE} overhead ESTIMATE). "
                f"Reduce context or drop a quant tier"
            )
        elif headroom < 0.75:
            status = CheckStatus.WARN
            detail = (
                f"PROJECTED {est.total_gb:.2f} GB leaves only {headroom:.2f} GB under the "
                f"ceiling - inside the tolerance band, so a bad overhead estimate could "
                f"push it over"
            )
        else:
            status = CheckStatus.PASS
            detail = (
                f"PROJECTED {est.total_gb:.2f} GB ({resolved.size_gb:.2f} weights measured "
                f"+ {est.kv_cache_gb:.2f} KV computed + {CUDA_OVERHEAD_GB_ESTIMATE} overhead "
                f"ESTIMATE), {headroom:.2f} GB under the ceiling. Confidence: {spec.confidence}"
            )
        results.append(
            CheckResult(
                name=f"profile.{key}",
                status=status,
                detail=detail,
                required=(status is CheckStatus.FAIL),
                measured=measured,
            )
        )
    return results


def check_single_resident(
    config_path: Path = DEFAULT_CONFIG,
) -> CheckResult:
    """Confirm no two profiles could be resident at once.

    Not a hardware reading - a structural check that the config cannot express a
    configuration the card cannot hold.
    """
    try:
        registry = ProfileRegistry(config_path)
        keys = list(registry.specs)
    except Exception as exc:  # noqa: BLE001
        return CheckResult(
            name="vram.single_resident",
            status=CheckStatus.UNMEASURABLE,
            detail=f"cannot load profiles: {exc}",
            required=False,
        )
    if len(keys) < 2:
        return CheckResult(
            name="vram.single_resident",
            status=CheckStatus.SKIP,
            detail="fewer than two profiles defined; nothing to contend",
            required=False,
        )
    try:
        registry.ensure_single_resident(keys[:2])
        return CheckResult(
            name="vram.single_resident",
            status=CheckStatus.FAIL,
            detail=(
                f"registry allowed {keys[0]!r} and {keys[1]!r} to be resident together; "
                f"16 GB cannot hold two profiles and the guard should have refused"
            ),
            measured={"attempted": keys[:2]},
        )
    except Exception as exc:  # noqa: BLE001 - VramBudgetError is the correct outcome
        return CheckResult(
            name="vram.single_resident",
            status=CheckStatus.PASS,
            detail=f"registry refuses concurrent residency, as it must: {str(exc)[:120]}",
            measured={"attempted": keys[:2]},
        )


def run_all_checks(
    *,
    config_path: Path = DEFAULT_CONFIG,
    only_profile: Optional[str] = None,
    allow_unmeasurable: bool = False,
    skip_bandwidth: bool = False,
) -> HardwareReport:
    report = HardwareReport(host=host_info(), allow_unmeasurable=allow_unmeasurable)
    report.add(check_cpu())
    report.add(check_ram())
    report.add(check_nvidia_smi())
    report.add(check_vram())
    report.add(check_disk())
    if not skip_bandwidth:
        report.add(check_bandwidth())
    for r in check_profile_footprints(config_path, only_profile):
        report.add(r)
    report.add(check_single_resident(config_path))
    return report


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

_GLYPH = {
    CheckStatus.PASS: "ok  ",
    CheckStatus.FAIL: "FAIL",
    CheckStatus.WARN: "warn",
    CheckStatus.UNMEASURABLE: "??? ",
    CheckStatus.SKIP: "skip",
}


def render(report: HardwareReport) -> str:
    lines: list[str] = []
    h = report.host
    lines.append("=" * 78)
    lines.append("HARDWARE CHECK - F:/he_thong_da_tac_tu")
    lines.append("=" * 78)
    lines.append(f"  host    {h.get('platform', '?')}")
    lines.append(f"  python  {h.get('python', '?')}  cpus {h.get('logical_cpus', '?')}")
    lines.append(f"  at      {h.get('checked_at', '?')}")
    lines.append("")
    for r in report.results:
        lines.append(f"  [{_GLYPH[r.status]}] {r.name}")
        for chunk in _wrap(r.detail, 70):
            lines.append(f"         {chunk}")
    lines.append("")
    lines.append("-" * 78)
    code = report.exit_code()
    verdict = {EXIT_OK: "READY", EXIT_FAIL: "OVER BUDGET", EXIT_UNMEASURABLE: "UNVERIFIED"}[code]
    lines.append(f"  verdict: {verdict}  (exit {code})")
    if report.failures:
        lines.append(f"  failed: {', '.join(r.name for r in report.failures)}")
    if report.unmeasurable:
        names = ", ".join(r.name for r in report.unmeasurable)
        if report.allow_unmeasurable:
            lines.append(f"  unmeasurable (downgraded by --allow-unmeasurable): {names}")
        else:
            lines.append(f"  unmeasurable: {names}")
            lines.append(
                "  Exit 2 means these could not be measured, not that they passed. "
                "Run on the host with the GPU driver present, or pass "
                "--allow-unmeasurable if you accept unverified budgets."
            )
    if report.warnings:
        lines.append(f"  warnings: {', '.join(r.name for r in report.warnings)}")
    lines.append("-" * 78)
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    words = text.split()
    out: list[str] = []
    cur = ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > width:
            out.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        out.append(cur)
    return out or [""]


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
            print(f"  [ok] {name}")
        except AssertionError as exc:
            failed.append(f"{name}: {exc}")
            print(f"  [FAIL] {name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}: {type(exc).__name__}: {exc}")
            print(f"  [ERR ] {name}: {type(exc).__name__}: {exc}")

    print("hardware_check.py selftest")
    print("-" * 60)

    def mk(status: CheckStatus, required: bool = True, name: str = "x") -> CheckResult:
        return CheckResult(name=name, status=status, detail="", required=required)

    # --- exit code semantics: the whole point of the module ---

    def t_all_pass_exits_zero() -> None:
        rep = HardwareReport(results=[mk(CheckStatus.PASS), mk(CheckStatus.PASS)])
        assert rep.exit_code() == EXIT_OK, rep.exit_code()

    def t_unmeasurable_exits_two() -> None:
        rep = HardwareReport(results=[mk(CheckStatus.PASS), mk(CheckStatus.UNMEASURABLE)])
        assert rep.exit_code() == EXIT_UNMEASURABLE, (
            "an unmeasurable required check must NOT be reported as a pass"
        )

    def t_fail_exits_one() -> None:
        rep = HardwareReport(results=[mk(CheckStatus.FAIL)])
        assert rep.exit_code() == EXIT_FAIL, rep.exit_code()

    def t_fail_outranks_unmeasurable() -> None:
        rep = HardwareReport(results=[mk(CheckStatus.FAIL), mk(CheckStatus.UNMEASURABLE)])
        assert rep.exit_code() == EXIT_FAIL, "a known failure is the more actionable signal"

    def t_allow_unmeasurable_downgrades() -> None:
        rep = HardwareReport(results=[mk(CheckStatus.UNMEASURABLE)], allow_unmeasurable=True)
        assert rep.exit_code() == EXIT_OK

    def t_allow_unmeasurable_does_not_mask_failure() -> None:
        rep = HardwareReport(results=[mk(CheckStatus.FAIL)], allow_unmeasurable=True)
        assert rep.exit_code() == EXIT_FAIL, "--allow-unmeasurable must not hide a real failure"

    def t_optional_unmeasurable_ignored() -> None:
        rep = HardwareReport(results=[mk(CheckStatus.UNMEASURABLE, required=False)])
        assert rep.exit_code() == EXIT_OK, "advisory checks must not gate the exit code"

    def t_optional_failure_ignored() -> None:
        rep = HardwareReport(results=[mk(CheckStatus.FAIL, required=False)])
        assert rep.exit_code() == EXIT_OK

    def t_warn_does_not_fail() -> None:
        rep = HardwareReport(results=[mk(CheckStatus.WARN)])
        assert rep.exit_code() == EXIT_OK
        assert len(rep.warnings) == 1

    def t_skip_does_not_fail() -> None:
        rep = HardwareReport(results=[mk(CheckStatus.SKIP)])
        assert rep.exit_code() == EXIT_OK

    def t_empty_report_is_ok() -> None:
        assert HardwareReport().exit_code() == EXIT_OK

    def t_exit_codes_are_distinct() -> None:
        assert len({EXIT_OK, EXIT_FAIL, EXIT_UNMEASURABLE}) == 3

    # --- individual checks return well-formed results ---

    def t_cpu_check_shape() -> None:
        r = check_cpu()
        assert isinstance(r, CheckResult)
        assert r.status in set(CheckStatus)
        assert r.detail, "every result must explain itself"

    def t_ram_check_shape() -> None:
        r = check_ram()
        assert r.name == "ram.headroom"
        if r.status is not CheckStatus.UNMEASURABLE:
            assert r.measured.get("total_gb", 0) > 0, r.measured

    def t_vram_check_never_silently_passes() -> None:
        r = check_vram()
        # On a GPU-less box this MUST be UNMEASURABLE, not PASS.
        if r.status is CheckStatus.PASS:
            assert r.measured.get("total_gb", 0) > 0, "a PASS must carry a measurement"
        else:
            assert r.status in (CheckStatus.FAIL, CheckStatus.WARN, CheckStatus.UNMEASURABLE)

    def t_vram_unmeasurable_says_why() -> None:
        r = check_vram()
        if r.status is CheckStatus.UNMEASURABLE:
            assert "unverified" in r.detail.lower() or "no VRAM" in r.detail, r.detail

    def t_nvidia_smi_check_shape() -> None:
        r = check_nvidia_smi()
        assert r.name == "gpu.nvidia_smi"
        assert r.status in (CheckStatus.PASS, CheckStatus.UNMEASURABLE)

    def t_disk_check_is_advisory() -> None:
        r = check_disk()
        assert r.required is False, "a full disk should not read as a hardware failure"

    def t_disk_check_measures() -> None:
        r = check_disk(Path("/"))
        if r.status is not CheckStatus.UNMEASURABLE:
            assert r.measured.get("total_gb", 0) > 0

    def t_bandwidth_is_advisory_and_labelled() -> None:
        r = check_bandwidth()
        assert r.required is False
        if r.status is CheckStatus.PASS:
            assert "NOT the GPU" in r.detail, "must not be mistaken for GPU bandwidth"
            assert r.measured.get("note")

    def t_bandwidth_measures_something() -> None:
        gb_s = measure_host_bandwidth_gb_s(mib=16, repeats=1)
        if gb_s is not None:
            assert gb_s > 0.1, f"implausible bandwidth {gb_s}"
            assert gb_s < 10_000, f"implausible bandwidth {gb_s}"

    # --- profile footprint arithmetic ---

    def t_profile_check_labels_projection() -> None:
        results = check_profile_footprints(DEFAULT_CONFIG)
        assert results, "expected at least one profile result"
        for r in results:
            if r.status in (CheckStatus.PASS, CheckStatus.FAIL, CheckStatus.WARN):
                assert "PROJECTED" in r.detail, f"{r.name}: {r.detail}"
                assert "ESTIMATE" in r.detail, f"{r.name} must flag the overhead estimate"

    def t_profile_missing_weights_is_unmeasurable() -> None:
        results = check_profile_footprints(DEFAULT_CONFIG)
        for r in results:
            if r.status is CheckStatus.UNMEASURABLE:
                assert "not on disk" in r.detail, r.detail
                assert r.required is False, "absent weights should not fail the whole check"

    def t_unknown_profile_fails() -> None:
        results = check_profile_footprints(DEFAULT_CONFIG, only="NOPE")
        assert len(results) == 1
        assert results[0].status is CheckStatus.FAIL, results[0].as_dict()

    def t_bad_config_is_unmeasurable() -> None:
        results = check_profile_footprints(Path("/definitely/not/here.json"))
        assert len(results) == 1
        assert results[0].status is CheckStatus.UNMEASURABLE, results[0].as_dict()

    def t_overhead_estimate_is_named() -> None:
        # The constant must stay discoverable so the report can label its own math.
        assert CUDA_OVERHEAD_GB_ESTIMATE == 0.55
        results = check_profile_footprints(DEFAULT_CONFIG)
        for r in results:
            if "cuda_overhead_gb_ESTIMATE" in r.measured:
                assert r.measured["cuda_overhead_gb_ESTIMATE"] == 0.55

    def t_projection_is_sum_of_parts() -> None:
        results = check_profile_footprints(DEFAULT_CONFIG)
        for r in results:
            m = r.measured
            if "total_gb_PROJECTED" not in m:
                continue
            parts = m["weights_gb_measured"] + m["kv_cache_gb_computed"] + 0.55
            assert abs(parts - m["total_gb_PROJECTED"]) < 0.01, (r.name, m)

    def t_single_resident_enforced() -> None:
        r = check_single_resident(DEFAULT_CONFIG)
        assert r.status in (CheckStatus.PASS, CheckStatus.SKIP, CheckStatus.UNMEASURABLE), (
            f"the registry must refuse two resident profiles; got {r.status}: {r.detail}"
        )

    # --- full run and rendering ---

    def t_full_run_produces_report() -> None:
        rep = run_all_checks(skip_bandwidth=True)
        assert rep.results, "expected checks to run"
        assert rep.host.get("platform")
        assert rep.exit_code() in (EXIT_OK, EXIT_FAIL, EXIT_UNMEASURABLE)

    def t_report_json_serializable() -> None:
        rep = run_all_checks(skip_bandwidth=True)
        blob = json.dumps(rep.as_dict())
        assert "checks" in blob
        parsed = json.loads(blob)
        assert parsed["verdict"] in ("READY", "OVER BUDGET", "UNVERIFIED")

    def t_render_mentions_verdict() -> None:
        rep = run_all_checks(skip_bandwidth=True)
        text = render(rep)
        assert "verdict:" in text
        for r in rep.results:
            assert r.name in text, f"{r.name} missing from the rendered report"

    def t_render_explains_exit_two() -> None:
        rep = HardwareReport(host=host_info(), results=[mk(CheckStatus.UNMEASURABLE, name="vram.headroom")])
        text = render(rep)
        assert "not that they passed" in text, "exit 2 must be explained, not just shown"

    def t_counts_add_up() -> None:
        rep = run_all_checks(skip_bandwidth=True)
        counts = rep.as_dict()["counts"]
        assert sum(counts.values()) == len(rep.results), counts

    def t_wrap_respects_width() -> None:
        long = "word " * 60
        for line in _wrap(long, 40):
            assert len(line) <= 40, line

    def t_wrap_handles_empty() -> None:
        assert _wrap("", 40) == [""]

    def t_cli_json_runs() -> None:
        import contextlib
        import io

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--json", "--allow-unmeasurable", "--skip-bandwidth"])
        assert code in (EXIT_OK, EXIT_FAIL), code
        parsed = json.loads(buf.getvalue())
        assert "checks" in parsed and parsed["allow_unmeasurable"] is True

    def t_no_gpu_never_exits_zero() -> None:
        """Without a GPU reading the check must never report READY.

        It may exit 1 instead of 2 if some other required check measured a real
        failure - on a small CI box the RAM check does exactly that, and a
        measured failure is the more actionable signal. What must never happen is
        exit 0, which would claim the VRAM budget was verified.
        """
        report = run_all_checks(skip_bandwidth=True)
        _, total, _ = read_vram_gb()
        if total is None:
            assert report.unmeasurable, "a missing GPU must register as unmeasurable"
            assert any(r.name == "vram.headroom" for r in report.unmeasurable)
            assert report.exit_code() != EXIT_OK, (
                "no VRAM reading, yet the report claims READY - this is the exact "
                "failure mode the module exists to prevent"
            )

    def t_unmeasurable_vram_alone_exits_two() -> None:
        # Isolate the VRAM path from whatever else this host happens to fail.
        rep = HardwareReport(
            results=[mk(CheckStatus.PASS, name="ram.headroom"),
                     mk(CheckStatus.UNMEASURABLE, name="vram.headroom")]
        )
        assert rep.exit_code() == EXIT_UNMEASURABLE, rep.exit_code()

    for name, fn in [
        ("all-pass exits 0", t_all_pass_exits_zero),
        ("unmeasurable exits 2, not 0", t_unmeasurable_exits_two),
        ("failure exits 1", t_fail_exits_one),
        ("failure outranks unmeasurable", t_fail_outranks_unmeasurable),
        ("--allow-unmeasurable downgrades to 0", t_allow_unmeasurable_downgrades),
        ("--allow-unmeasurable does not mask failures", t_allow_unmeasurable_does_not_mask_failure),
        ("optional unmeasurable ignored", t_optional_unmeasurable_ignored),
        ("optional failure ignored", t_optional_failure_ignored),
        ("warn does not fail the run", t_warn_does_not_fail),
        ("skip does not fail the run", t_skip_does_not_fail),
        ("empty report exits 0", t_empty_report_is_ok),
        ("exit codes are distinct", t_exit_codes_are_distinct),
        ("cpu check shape", t_cpu_check_shape),
        ("ram check shape", t_ram_check_shape),
        ("vram never silently passes", t_vram_check_never_silently_passes),
        ("vram unmeasurable explains itself", t_vram_unmeasurable_says_why),
        ("nvidia-smi check shape", t_nvidia_smi_check_shape),
        ("disk check is advisory", t_disk_check_is_advisory),
        ("disk check measures free space", t_disk_check_measures),
        ("bandwidth advisory and labelled", t_bandwidth_is_advisory_and_labelled),
        ("bandwidth measures something plausible", t_bandwidth_measures_something),
        ("profile results labelled PROJECTED", t_profile_check_labels_projection),
        ("missing weights are unmeasurable", t_profile_missing_weights_is_unmeasurable),
        ("unknown profile fails", t_unknown_profile_fails),
        ("bad config is unmeasurable", t_bad_config_is_unmeasurable),
        ("overhead estimate is named", t_overhead_estimate_is_named),
        ("projection is the sum of its parts", t_projection_is_sum_of_parts),
        ("single residency enforced", t_single_resident_enforced),
        ("full run produces a report", t_full_run_produces_report),
        ("report is JSON serializable", t_report_json_serializable),
        ("render mentions every check", t_render_mentions_verdict),
        ("render explains exit 2", t_render_explains_exit_two),
        ("status counts add up", t_counts_add_up),
        ("wrap respects width", t_wrap_respects_width),
        ("wrap handles empty text", t_wrap_handles_empty),
        ("CLI --json runs", t_cli_json_runs),
        ("no GPU never exits 0", t_no_gpu_never_exits_zero),
        ("unmeasurable VRAM alone exits 2", t_unmeasurable_vram_alone_exits_two),
    ]:
        check(name, fn)

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
    ap = argparse.ArgumentParser(
        description="Verify this machine against the swarm's hardware budget",
        epilog="exit 0 = ready, 1 = a measured problem, 2 = could not measure",
    )
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--json", action="store_true", help="machine-readable output")
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--profile", default=None, help="check one profile only")
    ap.add_argument(
        "--allow-unmeasurable",
        action="store_true",
        help="downgrade unmeasurable checks to warnings (exit 0 instead of 2)",
    )
    ap.add_argument("--skip-bandwidth", action="store_true", help="skip the memory copy benchmark")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    report = run_all_checks(
        config_path=Path(args.config),
        only_profile=args.profile,
        allow_unmeasurable=args.allow_unmeasurable,
        skip_bandwidth=args.skip_bandwidth,
    )
    if args.json:
        print(json.dumps(report.as_dict(), indent=2, ensure_ascii=False))
    else:
        print(render(report))
    return report.exit_code()


if __name__ == "__main__":
    sys.exit(main())
