#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_system_stabilizer.py - Unit tests for SystemStabilizer and SafeAutoCleaner.
"""

from __future__ import annotations

import os
from pathlib import Path
import pytest

from system_stabilizer import SafeAutoCleaner, StabilizerConfig, SystemStabilizer
from risk_guard import RiskGuard, RiskGuardConfig, Decision


class TestSystemStabilizer:
    def test_disk_free_is_positive(self):
        free = SystemStabilizer.get_disk_free_gb("C:\\")
        assert free > 0.0

    def test_apply_priority_does_not_crash(self):
        stabilizer = SystemStabilizer()
        # On Windows returns True/False without exception
        res = stabilizer.apply_process_priority()
        assert isinstance(res, bool)

    def test_pace_delay(self):
        stabilizer = SystemStabilizer()
        # Should execute in tiny time without error
        stabilizer.pace(custom_delay_s=0.001)

    def test_safe_auto_cleaner(self, tmp_path):
        dummy_dir = tmp_path / "fake_pytest_temp"
        dummy_dir.mkdir()
        dummy_file = dummy_dir / "junk.dat"
        dummy_file.write_bytes(b"0" * 1024)

        assert dummy_file.exists()

        res = SafeAutoCleaner.clean_safe_artifacts([dummy_dir])
        assert res["freed_bytes"] == 1024
        assert not dummy_dir.exists()

    def test_auto_cleaner_handles_missing_paths(self, tmp_path):
        res = SafeAutoCleaner.clean_safe_artifacts([tmp_path / "non_existent_folder"])
        assert res["freed_bytes"] == 0
        assert not res["errors"]

    def test_risk_guard_disk_check_integration(self, tmp_path):
        # Configure RiskGuard with an impossibly high disk threshold to trigger safety gate
        cfg = RiskGuardConfig(
            budget_cap_minor=1000,
            db_path=tmp_path / "guard.db",
            kill_file=tmp_path / "KILL_SWITCH",
            allow_unmeasurable_hardware=True,
            min_disk_free_gb=999999.0,  # Impossible threshold
        )
        guard = RiskGuard(cfg)
        guard.credit(1000)
        verdict = guard.pre_check(100)
        assert verdict.decision == Decision.DENY
        assert "low_disk_space" in verdict.reason
        guard.close()
