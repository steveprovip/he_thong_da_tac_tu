#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
conftest.py - shared fixtures and the --budget-cap option.

Why --budget-cap is a pytest option
-----------------------------------
The directive's verification command is:

    pytest tests/test_risk_guard.py --budget-cap 10000

so the ceiling has to be settable from the command line, not hard-coded in a
test. It defaults to 10000 (the pre-funded VND ceiling) and every guard fixture
reads it, which means running the suite with `--budget-cap 500` genuinely
re-tests the invariants at a different ceiling rather than just changing a label.

Why every fixture gets its own database
---------------------------------------
RiskGuard derives balance by summing the ledger, and its kill switch persists to
a sentinel file. Sharing either between tests would let one test's spend or one
test's tripped switch leak into the next, and the failure would look like a bug
in the guard rather than in the fixture. `tmp_path` gives each test a fresh
directory, so the isolation is structural.

allow_unmeasurable_hardware=True in fixtures
--------------------------------------------
Production default is False - a guard that cannot read the GPU must DENY. Tests
set it True on purpose so they exercise budget logic on machines without a GPU.
`test_risk_guard.py` covers the fail-closed default separately, so flipping it
here does not hide it.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any, Iterator

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
for sub in ("core", "simulation", "benchmarks", "live"):
    p = str(REPO_ROOT / sub)
    if p not in sys.path:
        sys.path.insert(0, p)

DEFAULT_BUDGET_CAP = 10_000


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--budget-cap",
        action="store",
        type=int,
        default=DEFAULT_BUDGET_CAP,
        help=(
            "Pre-funded ceiling in minor currency units used by guard fixtures "
            f"(default {DEFAULT_BUDGET_CAP}). Zero overdraft is enforced at any value."
        ),
    )
    parser.addoption(
        "--run-slow",
        action="store_true",
        default=False,
        help="run tests marked slow (large Monte Carlo sweeps)",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "slow: long-running; needs --run-slow")
    config.addinivalue_line("markers", "needs_gpu: requires a readable NVIDIA GPU")
    cap = config.getoption("--budget-cap")
    if cap < 0:
        raise pytest.UsageError(f"--budget-cap must be >= 0, got {cap}")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    if config.getoption("--run-slow"):
        return
    skip = pytest.mark.skip(reason="needs --run-slow")
    for item in items:
        if "slow" in item.keywords:
            item.add_marker(skip)


def pytest_sessionstart(session: pytest.Session) -> None:
    """Lower process priority to keep OS responsive and clean pre-existing temp garbage."""
    try:
        from system_stabilizer import SafeAutoCleaner, SystemStabilizer  # noqa: PLC0415
        SystemStabilizer()
        SafeAutoCleaner.clean_safe_artifacts()
    except Exception:
        pass


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    """Automatically sweep safe temp files at session completion."""
    try:
        from system_stabilizer import SafeAutoCleaner  # noqa: PLC0415
        SafeAutoCleaner.clean_safe_artifacts()
    except Exception:
        pass


@pytest.fixture(scope="session")
def repo_root() -> Path:
    return REPO_ROOT


@pytest.fixture(scope="session")
def budget_cap(pytestconfig: pytest.Config) -> int:
    """The ceiling under test, from --budget-cap."""
    return int(pytestconfig.getoption("--budget-cap"))


@pytest.fixture
def guard_config(tmp_path: Path, budget_cap: int) -> Any:
    from risk_guard import RiskGuardConfig  # noqa: PLC0415

    return RiskGuardConfig(
        budget_cap_minor=budget_cap,
        db_path=tmp_path / "guard.db",
        kill_file=tmp_path / "KILL_SWITCH",
        allow_unmeasurable_hardware=True,
        currency="VND",
    )


@pytest.fixture
def guard(guard_config: Any) -> Iterator[Any]:
    """An unfunded guard at the configured cap. Balance starts at 0."""
    from risk_guard import RiskGuard  # noqa: PLC0415

    g = RiskGuard(guard_config)
    yield g
    g.close()


@pytest.fixture
def funded_guard(guard: Any, budget_cap: int) -> Any:
    """A guard credited to the full ceiling."""
    if budget_cap > 0:
        guard.credit(budget_cap, label="test funding")
    return guard


@pytest.fixture
def has_gpu() -> bool:
    from risk_guard import read_vram_gb  # noqa: PLC0415

    _, total, _ = read_vram_gb()
    return total is not None


@pytest.fixture
def config_path(repo_root: Path) -> Path:
    return repo_root / "config" / "profiles.json"
