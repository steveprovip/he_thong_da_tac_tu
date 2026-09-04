#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
run_tests.py - run the suite with or without pytest installed.

The directive's verification command is:

    pytest tests/test_risk_guard.py --budget-cap 10000

and on a host with pytest that is exactly what you should run. This script exists
for the other case: a machine where pytest is not installed and no package index
is reachable. It prefers real pytest whenever it can import it, and falls back to
the in-repo stand-in (`tests/_minipytest.py`) otherwise, so the assertions are
always actually executed rather than merely shipped.

    python tests/run_tests.py                        # whole suite
    python tests/run_tests.py --budget-cap 500       # re-test at another ceiling
    python tests/run_tests.py tests/test_risk_guard.py --budget-cap 10000
    python tests/run_tests.py --run-slow             # include the big sweeps
    python tests/run_tests.py --force-mini           # exercise the fallback path

Exit codes follow pytest: 0 all passed, 1 failures, 4 usage error, 5 nothing
collected.
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = TESTS_DIR.parent

for p in (str(REPO_ROOT), str(TESTS_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)


def main(argv: list[str]) -> int:
    force_mini = "--force-mini" in argv
    argv = [a for a in argv if a != "--force-mini"]

    if not force_mini:
        try:
            import pytest  # noqa: PLC0415

            if not getattr(pytest, "__minipytest__", False):
                args = argv or [str(TESTS_DIR)]
                print(f"running under pytest {pytest.__version__}")
                return int(pytest.main(args))
        except ImportError:
            pass

    import _minipytest  # noqa: PLC0415

    if not force_mini:
        print(
            "pytest is not installed; running the in-repo stand-in.\n"
            "On a host with pytest, prefer: pytest tests/ --budget-cap 10000",
            file=sys.stderr,
        )
    return _minipytest.run(argv)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
