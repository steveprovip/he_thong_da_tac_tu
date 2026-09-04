#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_risk_guard.py - the Sentry's invariants.

Run the directive's verification command:

    pytest tests/test_risk_guard.py --budget-cap 10000

Every test that involves a ceiling reads it from the `budget_cap` fixture rather
than a literal, so the same suite proves the same properties at any cap. Run it
with `--budget-cap 500` and it still passes; run it with `--budget-cap 0` and the
zero-overdraft tests get sharper, because every charge is unaffordable.

The four properties worth caring about
--------------------------------------
1. Balance never goes negative. Not "usually", not "unless concurrent" - never.
2. Balance is derived from the ledger, never cached, so it cannot drift.
3. Amounts are integers. A fractional float in a ledger is rejected at the door.
4. Unmeasurable hardware DENIES by default. Fail-closed, not fail-quiet.
"""

from __future__ import annotations

import random
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from risk_guard import (
    BudgetError,
    Decision,
    HardwareSnapshot,
    KillSwitchTripped,
    OverdraftError,
    RiskGuard,
    RiskGuardConfig,
    read_ram_gb,
    read_vram_gb,
    snapshot_hardware,
)


# ---------------------------------------------------------------------------
# The cap comes from the command line
# ---------------------------------------------------------------------------


class TestBudgetCapOption:
    def test_cap_is_honoured(self, guard, budget_cap):
        assert guard.config.budget_cap_minor == budget_cap

    def test_default_cap_is_ten_thousand(self, pytestconfig):
        # The directive's stated pre-funded ceiling. Overridable, but this is
        # what an unadorned `pytest tests/test_risk_guard.py` tests.
        if not any("--budget-cap" in str(a) for a in pytestconfig.invocation_params.args):
            assert pytestconfig.getoption("--budget-cap") == 10_000

    def test_funding_to_cap_succeeds(self, funded_guard, budget_cap):
        assert funded_guard.balance() == budget_cap

    def test_cannot_fund_above_cap(self, funded_guard, budget_cap):
        if budget_cap == 0:
            pytest.skip("no room to overfund at a zero cap")
        with pytest.raises(BudgetError, match="cap"):
            funded_guard.credit(1)


# ---------------------------------------------------------------------------
# Zero overdraft
# ---------------------------------------------------------------------------


class TestZeroOverdraft:
    def test_charge_beyond_balance_refused(self, funded_guard, budget_cap):
        with pytest.raises(OverdraftError):
            funded_guard.charge(budget_cap + 1)
        assert funded_guard.balance() == budget_cap, "a refused charge must not move money"

    def test_charge_exactly_balance_allowed(self, funded_guard, budget_cap):
        if budget_cap == 0:
            pytest.skip("nothing to spend")
        funded_guard.charge(budget_cap)
        assert funded_guard.balance() == 0

    def test_charge_on_empty_guard_refused(self, guard):
        with pytest.raises(OverdraftError):
            guard.charge(1)

    def test_balance_never_negative_under_random_load(self, funded_guard, budget_cap):
        rng = random.Random(20260903)
        for _ in range(400):
            amount = rng.randint(1, max(1, budget_cap // 3))
            try:
                funded_guard.charge(amount)
            except (OverdraftError, BudgetError):
                pass
            assert funded_guard.balance() >= 0, funded_guard.balance()

    def test_spent_never_exceeds_cap(self, funded_guard, budget_cap):
        for _ in range(200):
            try:
                funded_guard.charge(97)
            except (OverdraftError, BudgetError):
                break
        assert funded_guard.spent_total() <= budget_cap, funded_guard.spent_total()

    def test_concurrent_charges_cannot_overdraw(self, funded_guard, budget_cap):
        """Two threads must not both see sufficient balance and both commit.

        The check-then-insert in charge() happens under one lock; this is the
        test that would catch it being split.
        """
        if budget_cap < 10:
            pytest.skip("cap too small to contend")
        amount = max(1, budget_cap // 8)
        errors: list[Exception] = []

        def spend() -> None:
            try:
                funded_guard.charge(amount)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        with ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _: spend(), range(32)))

        assert funded_guard.balance() >= 0, funded_guard.balance()
        assert funded_guard.spent_total() <= budget_cap
        # Some attempts must have been refused; that is the correct outcome.
        assert errors, "32 charges of an eighth of the cap should not all succeed"


# ---------------------------------------------------------------------------
# Integer discipline
# ---------------------------------------------------------------------------


class TestAmountCoercion:
    @pytest.mark.parametrize("bad", [1.5, 0.1, -2.5, 99.999])
    def test_fractional_float_rejected(self, funded_guard, bad):
        with pytest.raises((TypeError, ValueError, BudgetError)):
            funded_guard.charge(bad)

    def test_integral_float_accepted(self, funded_guard, budget_cap):
        if budget_cap < 10:
            pytest.skip("cap too small")
        funded_guard.charge(10.0)
        assert funded_guard.balance() == budget_cap - 10
        assert isinstance(funded_guard.balance(), int)

    @pytest.mark.parametrize("bad", [True, False])
    def test_bool_rejected(self, funded_guard, bad):
        # bool is an int subclass; accepting it would silently charge 1 or 0.
        with pytest.raises((TypeError, ValueError, BudgetError)):
            funded_guard.charge(bad)

    @pytest.mark.parametrize("bad", ["100", None, [1], {"a": 1}])
    def test_non_numeric_rejected(self, funded_guard, bad):
        with pytest.raises((TypeError, ValueError, BudgetError)):
            funded_guard.charge(bad)

    @pytest.mark.parametrize("bad", [0, -1, -100])
    def test_nonpositive_charge_rejected(self, funded_guard, bad):
        with pytest.raises(BudgetError):
            funded_guard.charge(bad)

    def test_every_reported_amount_is_int(self, funded_guard, budget_cap):
        if budget_cap >= 100:
            funded_guard.charge(100)
        for value in (funded_guard.balance(), funded_guard.spent_total(), funded_guard.spent_today()):
            assert isinstance(value, int), type(value)


# ---------------------------------------------------------------------------
# Balance is derived, not cached
# ---------------------------------------------------------------------------


class TestLedgerIsTheTruth:
    def test_balance_matches_sum_of_deltas(self, funded_guard, budget_cap):
        if budget_cap >= 300:
            funded_guard.charge(120)
            funded_guard.charge(75)
        entries = funded_guard.ledger_entries()
        assert entries, "expected ledger rows"
        total = sum(int(e["delta_minor"]) for e in entries)
        assert funded_guard.balance() == total, (funded_guard.balance(), total)

    def test_balance_survives_reopen(self, guard_config, budget_cap):
        g1 = RiskGuard(guard_config)
        if budget_cap > 0:
            g1.credit(budget_cap)
        if budget_cap >= 400:
            g1.charge(400)
        expected = g1.balance()
        g1.close()

        g2 = RiskGuard(guard_config)
        assert g2.balance() == expected, "balance must be recomputed from the ledger"
        g2.close()

    def test_tampering_with_ledger_changes_balance(self, funded_guard, budget_cap):
        """Deleting a row changes the reported balance.

        This is the point of deriving rather than caching: the ledger is the only
        state, so there is no second copy to disagree with it.
        """
        if budget_cap < 500:
            pytest.skip("cap too small")
        funded_guard.charge(500)
        before = funded_guard.balance()
        db = Path(funded_guard.config.db_path)
        conn = sqlite3.connect(str(db))
        conn.execute("DELETE FROM ledger WHERE delta_minor = -500")
        conn.commit()
        conn.close()
        assert funded_guard.balance() != before
        assert funded_guard.balance() == before + 500

    def test_ledger_records_labels(self, funded_guard, budget_cap):
        if budget_cap < 50:
            pytest.skip("cap too small")
        funded_guard.charge(50, label="unit-test-spend", node_id="n42")
        entries = funded_guard.ledger_entries()
        blob = " ".join(str(e) for e in entries)
        assert "unit-test-spend" in blob
        assert "n42" in blob


# ---------------------------------------------------------------------------
# Kill switch
# ---------------------------------------------------------------------------


class TestKillSwitch:
    def test_starts_untripped(self, guard):
        assert guard.killed is False

    def test_trip_blocks_charges(self, funded_guard):
        funded_guard.trip_kill_switch("test")
        with pytest.raises(KillSwitchTripped):
            funded_guard.charge(1)

    def test_trip_blocks_precheck(self, funded_guard):
        funded_guard.trip_kill_switch("test")
        verdict = funded_guard.pre_check(1, node_id="n1")
        assert verdict.decision is Decision.DENY
        assert "kill" in verdict.reason.lower()

    def test_sentinel_file_written(self, funded_guard):
        funded_guard.trip_kill_switch("test")
        assert Path(funded_guard.config.kill_file).exists()

    def test_survives_restart(self, guard_config, budget_cap):
        g1 = RiskGuard(guard_config)
        g1.trip_kill_switch("persisted")
        g1.close()
        g2 = RiskGuard(guard_config)
        assert g2.killed is True, "a tripped switch must not be forgotten on restart"
        g2.close()

    def test_external_sentinel_trips_guard(self, guard_config):
        # A human touching the file must be able to stop the swarm without the API.
        Path(guard_config.kill_file).write_text("stopped by hand", encoding="utf-8")
        g = RiskGuard(guard_config)
        assert g.killed is True
        g.close()

    def test_reset_requires_exact_token(self, funded_guard):
        funded_guard.trip_kill_switch("test")
        for wrong in ("yes", "", "I-UNDERSTAND", None):
            with pytest.raises((ValueError, TypeError, RuntimeError)):
                funded_guard.reset_kill_switch(wrong)
        assert funded_guard.killed is True, "a failed reset must leave the switch tripped"

    def test_reset_with_token_clears(self, funded_guard):
        funded_guard.trip_kill_switch("test")
        funded_guard.reset_kill_switch("I-UNDERSTAND-RESUMING-THE-SWARM")
        assert funded_guard.killed is False
        assert not Path(funded_guard.config.kill_file).exists()

    def test_trip_is_idempotent(self, funded_guard):
        funded_guard.trip_kill_switch("first")
        funded_guard.trip_kill_switch("second")
        assert funded_guard.killed is True


# ---------------------------------------------------------------------------
# Hardware gating: fail closed
# ---------------------------------------------------------------------------


class TestHardwareGate:
    def test_unmeasurable_denies_by_default(self, tmp_path, budget_cap):
        """The production default. A guard that cannot see the GPU must DENY.

        This is the one test that must not use the permissive fixture - it exists
        precisely to prove the default is fail-closed.
        """
        g = RiskGuard(
            RiskGuardConfig(
                budget_cap_minor=budget_cap,
                db_path=tmp_path / "strict.db",
                kill_file=tmp_path / "strict_kill",
                # allow_unmeasurable_hardware deliberately left at its default
            )
        )
        assert g.config.allow_unmeasurable_hardware is False, (
            "the default must be fail-closed; flipping it silently would make every "
            "GPU-less host look healthy"
        )
        blind = HardwareSnapshot(
            vram_used_gb=None,
            vram_total_gb=None,
            ram_used_gb=1.0,
            ram_total_gb=32.0,
            source="test-blind",
            measured_at=0.0,
        )
        verdict = g.check_hardware(blind)
        assert verdict.decision is Decision.DENY, verdict
        g.close()

    def test_unmeasurable_allowed_when_opted_in(self, guard):
        blind = HardwareSnapshot(
            vram_used_gb=None,
            vram_total_gb=None,
            ram_used_gb=1.0,
            ram_total_gb=32.0,
            source="test-blind",
            measured_at=0.0,
        )
        assert guard.check_hardware(blind).decision is Decision.ALLOW

    def test_vram_over_ceiling_denies(self, guard):
        hot = HardwareSnapshot(
            vram_used_gb=15.5,
            vram_total_gb=16.0,
            ram_used_gb=4.0,
            ram_total_gb=32.0,
            source="test-hot",
            measured_at=0.0,
        )
        verdict = guard.check_hardware(hot)
        assert verdict.decision is Decision.DENY
        assert "vram" in verdict.reason.lower()

    def test_ram_over_ceiling_denies(self, guard):
        hot = HardwareSnapshot(
            vram_used_gb=2.0,
            vram_total_gb=16.0,
            ram_used_gb=30.0,
            ram_total_gb=32.0,
            source="test-hot",
            measured_at=0.0,
        )
        assert guard.check_hardware(hot).decision is Decision.DENY

    def test_healthy_snapshot_allows(self, guard):
        cool = HardwareSnapshot(
            vram_used_gb=9.0,
            vram_total_gb=16.0,
            ram_used_gb=4.0,
            ram_total_gb=32.0,
            source="test-cool",
            measured_at=0.0,
        )
        assert guard.check_hardware(cool).decision is Decision.ALLOW

    def test_snapshot_reports_measurability(self):
        snap = snapshot_hardware()
        assert isinstance(snap.vram_measurable, bool)
        assert snap.source

    def test_readers_return_triples(self):
        # Signature stability: hardware_check.py unpacks three values.
        assert len(read_vram_gb()) == 3
        assert len(read_ram_gb()) == 3


# ---------------------------------------------------------------------------
# pre_check / post_charge, the orchestrator's gate
# ---------------------------------------------------------------------------


class TestPreCheckPostCharge:
    def test_affordable_precheck_allows(self, funded_guard, budget_cap):
        if budget_cap < 10:
            pytest.skip("cap too small")
        verdict = funded_guard.pre_check(10, node_id="n1", check_hw=False)
        assert verdict.allowed, verdict
        assert bool(verdict) is True

    def test_unaffordable_precheck_denies(self, funded_guard, budget_cap):
        verdict = funded_guard.pre_check(budget_cap + 1, node_id="n1", check_hw=False)
        assert not verdict.allowed
        assert verdict.reason

    def test_precheck_does_not_move_money(self, funded_guard, budget_cap):
        before = funded_guard.balance()
        funded_guard.pre_check(min(50, budget_cap), node_id="n1", check_hw=False)
        assert funded_guard.balance() == before, "pre_check must be a query, not a debit"

    def test_post_charge_debits(self, funded_guard, budget_cap):
        if budget_cap < 250:
            pytest.skip("cap too small")
        funded_guard.post_charge(250, node_id="n1", label="node-work")
        assert funded_guard.balance() == budget_cap - 250

    def test_post_charge_zero_is_free(self, funded_guard):
        before = funded_guard.balance()
        funded_guard.post_charge(0, node_id="n1", label="free")
        assert funded_guard.balance() == before

    def test_post_charge_beyond_balance_raises(self, funded_guard, budget_cap):
        with pytest.raises((OverdraftError, BudgetError)):
            funded_guard.post_charge(budget_cap + 1, node_id="n1", label="too much")

    def test_status_reports_the_essentials(self, funded_guard, budget_cap):
        st = funded_guard.status()
        assert st["balance_minor"] == budget_cap
        assert st["budget_cap_minor"] == budget_cap
        assert st["killed"] is False
        assert "currency" in st

    def test_daily_cap_enforced(self, tmp_path, budget_cap):
        if budget_cap < 200:
            pytest.skip("cap too small to split into a daily limit")
        daily = budget_cap // 4
        g = RiskGuard(
            RiskGuardConfig(
                budget_cap_minor=budget_cap,
                daily_cap_minor=daily,
                db_path=tmp_path / "daily.db",
                kill_file=tmp_path / "daily_kill",
                allow_unmeasurable_hardware=True,
            )
        )
        g.credit(budget_cap)
        g.charge(daily)
        with pytest.raises(BudgetError):
            g.charge(1)
        assert g.spent_today() == daily
        g.close()


# ---------------------------------------------------------------------------
# Storage policy
# ---------------------------------------------------------------------------


class TestStoragePolicy:
    def test_db_written_where_told(self, funded_guard, tmp_path):
        assert Path(funded_guard.config.db_path).exists()
        assert Path(funded_guard.config.db_path).is_relative_to(tmp_path)

    def test_default_paths_stay_in_repo(self, repo_root):
        """No state may land in the read-only reference workspace or off F:."""
        cfg = RiskGuardConfig()
        for p in (Path(cfg.db_path), Path(cfg.kill_file)):
            assert "KyThuatNenAIMoi" not in str(p), p
            assert p.is_relative_to(repo_root), (p, repo_root)

    def test_audit_trail_exists(self, funded_guard, budget_cap):
        if budget_cap < 25:
            pytest.skip("cap too small")
        funded_guard.charge(25, label="audited")
        conn = sqlite3.connect(str(funded_guard.config.db_path))
        try:
            rows = conn.execute("SELECT COUNT(*) FROM audit").fetchone()[0]
        finally:
            conn.close()
        assert rows > 0, "spending must leave an audit record"
