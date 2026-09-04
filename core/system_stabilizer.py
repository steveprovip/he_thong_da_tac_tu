#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
system_stabilizer.py - Performance pacing, OS responsiveness, and safe auto-cleaner.

Solves two core operational failure modes in continuous agent execution:
  1. **Machine Lag / OS Stutter**: Intensive Python workloads (pytest, token parsing,
     DAG scheduling) monopolize CPU cores and disk I/O, freezing the Windows GUI.
     Stabilizer lowers process priority to BELOW_NORMAL_PRIORITY_CLASS so user actions
     (cursor, browser, editor) always take precedence.
  2. **Disk Full Exhaustion**: Research and testing produce temporary test databases,
     mock model artifacts, and crash dumps. SafeAutoCleaner sweeps only verified-safe
     temporary directories automatically without touching any source code or state.
"""

from __future__ import annotations

import ctypes
import logging
import os
import shutil
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("system_stabilizer")

# Windows Process Priority Constants
BELOW_NORMAL_PRIORITY_CLASS = 0x00004000
IDLE_PRIORITY_CLASS = 0x00000040
NORMAL_PRIORITY_CLASS = 0x00000020

# Safe cleanable paths on Windows for development/research workflows
DEFAULT_SAFE_CLEAN_TARGETS = [
    Path(r"C:\Users\Admin\AppData\Local\Temp\pytest-of-Admin"),
    Path(r"C:\Users\Admin\AppData\Local\CrashDumps"),
]


@dataclass
class StabilizerConfig:
    """Settings for process pacing and disk space protection."""
    lower_process_priority: bool = True
    priority_level: int = BELOW_NORMAL_PRIORITY_CLASS
    min_disk_free_gb: float = 5.0
    auto_clean_threshold_gb: float = 8.0
    safe_clean_targets: list[Path] = field(default_factory=lambda: list(DEFAULT_SAFE_CLEAN_TARGETS))
    pace_delay_s: float = 0.005  # Short yield delay to prevent 100% thread pegging


class SystemStabilizer:
    """Manages process priority, yields CPU slices, and guards disk space."""

    def __init__(self, config: Optional[StabilizerConfig] = None) -> None:
        self.config = config or StabilizerConfig()
        self._priority_adjusted = False
        if self.config.lower_process_priority:
            self.apply_process_priority()

    def apply_process_priority(self) -> bool:
        """Set current process to BELOW_NORMAL priority on Windows."""
        if os.name != "nt":
            return False
        try:
            handle = ctypes.windll.kernel32.GetCurrentProcess()
            ok = ctypes.windll.kernel32.SetPriorityClass(handle, self.config.priority_level)
            self._priority_adjusted = bool(ok)
            return self._priority_adjusted
        except Exception as exc:
            logger.debug("Failed to set process priority: %s", exc)
            return False

    @staticmethod
    def get_disk_free_gb(path: str | Path = "C:\\") -> float:
        """Return free space in gigabytes on the volume containing `path`."""
        try:
            usage = shutil.disk_usage(str(path))
            return usage.free / (1024.0 ** 3)
        except Exception:
            return 0.0

    def pace(self, custom_delay_s: Optional[float] = None) -> None:
        """Cooperative yield to keep the OS UI silky smooth."""
        delay = custom_delay_s if custom_delay_s is not None else self.config.pace_delay_s
        if delay > 0:
            time.sleep(delay)

    def check_and_auto_clean(self, target_drive: str = "C:\\") -> dict[str, Any]:
        """Verify disk free space. If below auto-clean threshold, safely purge temp items."""
        free_gb = self.get_disk_free_gb(target_drive)
        result: dict[str, Any] = {
            "initial_free_gb": round(free_gb, 2),
            "cleaned": False,
            "freed_bytes": 0,
            "final_free_gb": round(free_gb, 2),
            "status": "OK",
        }

        if free_gb < self.config.auto_clean_threshold_gb:
            logger.warning(
                "Disk space on %s is low (%.2f GB < threshold %.2f GB). Running safe auto-clean...",
                target_drive, free_gb, self.config.auto_clean_threshold_gb
            )
            clean_res = SafeAutoCleaner.clean_safe_artifacts(self.config.safe_clean_targets)
            after_free = self.get_disk_free_gb(target_drive)
            result["cleaned"] = True
            result["freed_bytes"] = clean_res["freed_bytes"]
            result["details"] = clean_res
            result["final_free_gb"] = round(after_free, 2)

            if after_free < self.config.min_disk_free_gb:
                result["status"] = "CRITICAL_LOW_SPACE"
            else:
                result["status"] = "RECOVERED"

        return result


class SafeAutoCleaner:
    """Purges only verified temporary files (pytest-of-Admin, CrashDumps, orphan journals)."""

    @classmethod
    def clean_safe_artifacts(
        cls, targets: Optional[list[Path]] = None, dry_run: bool = False
    ) -> dict[str, Any]:
        """Delete only strictly safe temporary paths and return audit metrics."""
        paths_to_clean = targets or DEFAULT_SAFE_CLEAN_TARGETS
        total_freed = 0
        cleaned_items: list[str] = []
        errors: list[str] = []

        for p in paths_to_clean:
            path_obj = Path(p)
            if not path_obj.exists():
                continue

            try:
                # Measure directory/file size before deletion
                size = cls._calc_size(path_obj)
                if not dry_run:
                    if path_obj.is_dir():
                        shutil.rmtree(path_obj, ignore_errors=True)
                    else:
                        path_obj.unlink(missing_ok=True)
                total_freed += size
                cleaned_items.append(f"{path_obj} ({round(size / (1024**2), 2)} MB)")
            except Exception as exc:
                errors.append(f"Failed to delete {path_obj}: {exc}")

        return {
            "dry_run": dry_run,
            "freed_bytes": total_freed,
            "freed_mb": round(total_freed / (1024.0 ** 2), 2),
            "freed_gb": round(total_freed / (1024.0 ** 3), 2),
            "cleaned_items": cleaned_items,
            "errors": errors,
        }

    @staticmethod
    def _calc_size(path: Path) -> int:
        if not path.exists():
            return 0
        if path.is_file():
            try:
                return path.stat().st_size
            except OSError:
                return 0
        total = 0
        for root, _, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    pass
        return total


if __name__ == "__main__":
    stabilizer = SystemStabilizer()
    print("=" * 60)
    print("SYSTEM STABILIZER & SAFE AUTO-CLEANER REPORT")
    print("=" * 60)
    print(f"Windows priority below-normal applied: {stabilizer._priority_adjusted}")
    c_free = stabilizer.get_disk_free_gb("C:\\")
    f_free = stabilizer.get_disk_free_gb("F:\\")
    print(f"Disk C: Free = {c_free:.2f} GB")
    print(f"Disk F: Free = {f_free:.2f} GB")
    print("\nRunning safe auto-cleaner check...")
    report = stabilizer.check_and_auto_clean("C:\\")
    print(f"Status: {report['status']}")
    print(f"Initial: {report['initial_free_gb']} GB -> Final: {report['final_free_gb']} GB")
    print("=" * 60)
