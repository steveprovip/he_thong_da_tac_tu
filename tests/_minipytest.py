#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_minipytest.py - a small stand-in for pytest, used only when pytest is absent.

Why this exists
---------------
The test suite is written for real pytest and the directive's verification command
is `pytest tests/test_risk_guard.py --budget-cap 10000`. But a machine (or a CI
container, or this authoring sandbox) may not have pytest installed and may have
no package index reachable. Shipping a suite nobody could run until they fixed
their environment would mean shipping unverified code.

So this module implements the subset of pytest's API the suite actually uses -
fixtures with scopes and dependency injection, `raises`, `parametrize`, marks,
`skip`, and the `pytest_addoption` / `pytest_configure` /
`pytest_collection_modifyitems` conftest hooks - and registers itself as the
`pytest` module *only if the real one cannot be imported*.

What this is not
----------------
Not a pytest replacement. No plugins, no assertion rewriting (so a bare
`assert x == y` failure reports the values Python gives, not pytest's diff), no
`-k`, no xdist, no fixture finalization ordering guarantees beyond LIFO. If real
pytest is installed it is always preferred, and the suite is written so that
running under real pytest is the normal case.

Exit codes match pytest: 0 all passed, 1 failures.

Usage:
    python tests/run_tests.py                       # auto-detects real pytest
    python tests/run_tests.py --budget-cap 500
"""

from __future__ import annotations

import inspect
import sys
import tempfile
import traceback
import types
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

__all__ = ["install", "run", "REAL_PYTEST"]

try:  # Real pytest always wins.
    import pytest as _real_pytest  # type: ignore

    REAL_PYTEST = True
except ImportError:
    _real_pytest = None  # type: ignore
    REAL_PYTEST = False


# ---------------------------------------------------------------------------
# Exceptions and marker plumbing
# ---------------------------------------------------------------------------


class Skipped(Exception):
    """Raised by pytest.skip()."""

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason)
        self.reason = reason


class Failed(AssertionError):
    """Raised by pytest.fail()."""


class UsageError(Exception):
    """Bad command-line usage, as pytest.UsageError."""


class _RaisesContext:
    """Context manager form of pytest.raises, including `match`."""

    def __init__(self, expected: Any, match: Optional[str] = None) -> None:
        self.expected = expected if isinstance(expected, tuple) else (expected,)
        self.match = match
        self.value: Optional[BaseException] = None

    def __enter__(self) -> "_RaisesContext":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if exc_type is None:
            names = ", ".join(e.__name__ for e in self.expected)
            raise Failed(f"DID NOT RAISE {names}")
        if not issubclass(exc_type, self.expected):
            return False  # propagate: the wrong exception is a real failure
        if self.match is not None:
            import re

            if not re.search(self.match, str(exc)):
                raise Failed(f"pattern {self.match!r} not found in {str(exc)!r}")
        self.value = exc
        return True


def _raises(expected: Any, *args: Any, match: Optional[str] = None, **kw: Any) -> Any:
    if args:  # pytest.raises(Exc, callable, *a) form
        func, rest = args[0], args[1:]
        ctx = _RaisesContext(expected, match)
        with ctx:
            func(*rest, **kw)
        return ctx
    return _RaisesContext(expected, match)


def _skip(reason: str = "") -> None:
    raise Skipped(reason)


def _fail(reason: str = "") -> None:
    raise Failed(reason)


@dataclass
class _Mark:
    name: str
    args: tuple = ()
    kwargs: dict = field(default_factory=dict)


class _MarkDecorator:
    def __init__(self, name: str) -> None:
        self.name = name

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Bare decorator use: @pytest.mark.slow
        if len(args) == 1 and not kwargs and callable(args[0]) and not isinstance(args[0], str):
            func = args[0]
            marks = list(getattr(func, "_minipytest_marks", []))
            marks.append(_Mark(self.name))
            func._minipytest_marks = marks  # type: ignore[attr-defined]
            return func

        def decorate(func: Callable) -> Callable:
            marks = list(getattr(func, "_minipytest_marks", []))
            marks.append(_Mark(self.name, args, kwargs))
            func._minipytest_marks = marks  # type: ignore[attr-defined]
            return func

        return decorate


class _MarkNamespace:
    def __getattr__(self, name: str) -> _MarkDecorator:
        return _MarkDecorator(name)


def _fixture(*args: Any, **kwargs: Any) -> Any:
    """pytest.fixture, supporting bare use and (scope=..., name=...)."""

    def decorate(func: Callable) -> Callable:
        func._minipytest_fixture = {  # type: ignore[attr-defined]
            "scope": kwargs.get("scope", "function"),
            "name": kwargs.get("name", func.__name__),
        }
        return func

    if len(args) == 1 and callable(args[0]) and not kwargs:
        return decorate(args[0])
    return decorate


# ---------------------------------------------------------------------------
# Config, Parser, Item - the surfaces conftest.py touches
# ---------------------------------------------------------------------------


class Parser:
    """Minimal argparse-alike matching pytest's addoption surface."""

    def __init__(self) -> None:
        self.options: dict[str, dict[str, Any]] = {}

    def addoption(self, name: str, **kwargs: Any) -> None:
        self.options[name] = kwargs

    def parse(self, argv: list[str]) -> tuple[dict[str, Any], list[str]]:
        values: dict[str, Any] = {}
        for name, spec in self.options.items():
            values[name] = spec.get("default")
        rest: list[str] = []
        i = 0
        while i < len(argv):
            tok = argv[i]
            key = tok.split("=", 1)[0]
            if key in self.options:
                spec = self.options[key]
                if spec.get("action") == "store_true":
                    values[key] = True
                    i += 1
                    continue
                if "=" in tok:
                    raw = tok.split("=", 1)[1]
                    i += 1
                else:
                    if i + 1 >= len(argv):
                        raise UsageError(f"{key} expects a value")
                    raw = argv[i + 1]
                    i += 2
                caster = spec.get("type") or str
                try:
                    values[key] = caster(raw)
                except (TypeError, ValueError) as exc:
                    raise UsageError(f"{key}: {exc}") from exc
                continue
            rest.append(tok)
            i += 1
        return values, rest


@dataclass
class _InvocationParams:
    args: tuple = ()


class Config:
    def __init__(self, values: dict[str, Any], argv: list[str]) -> None:
        self._values = values
        self._ini: list[str] = []
        self.invocation_params = _InvocationParams(args=tuple(argv))

    def getoption(self, name: str, default: Any = None) -> Any:
        if name in self._values:
            return self._values[name]
        # Accept both --budget-cap and budget_cap spellings.
        alt = "--" + name.replace("_", "-")
        if alt in self._values:
            return self._values[alt]
        return default

    def addinivalue_line(self, key: str, line: str) -> None:
        self._ini.append(f"{key}: {line}")


@dataclass
class Item:
    """One collected test."""

    name: str
    nodeid: str
    func: Callable
    cls: Optional[type]
    module: types.ModuleType
    params: dict[str, Any] = field(default_factory=dict)
    marks: list[_Mark] = field(default_factory=list)
    extra_markers: list[str] = field(default_factory=list)

    @property
    def keywords(self) -> set[str]:
        return {m.name for m in self.marks} | set(self.extra_markers) | {self.name}

    def add_marker(self, marker: Any) -> None:
        name = getattr(marker, "name", None) or str(marker)
        if isinstance(marker, _MarkDecorator):
            name = marker.name
        self.extra_markers.append(name)
        if getattr(marker, "_skip_reason", None):
            self.extra_markers.append("skip")


class _SkipMarker:
    """What pytest.mark.skip(reason=...) yields, as conftest uses it."""

    name = "skip"

    def __init__(self, reason: str = "") -> None:
        self._skip_reason = reason
        self.reason = reason


# ---------------------------------------------------------------------------
# The fake pytest module
# ---------------------------------------------------------------------------


def _build_module() -> types.ModuleType:
    mod = types.ModuleType("pytest")
    mod.raises = _raises  # type: ignore[attr-defined]
    mod.skip = _skip  # type: ignore[attr-defined]
    mod.fail = _fail  # type: ignore[attr-defined]
    mod.fixture = _fixture  # type: ignore[attr-defined]
    mod.Parser = Parser  # type: ignore[attr-defined]
    mod.Config = Config  # type: ignore[attr-defined]
    mod.Item = Item  # type: ignore[attr-defined]
    mod.UsageError = UsageError  # type: ignore[attr-defined]
    mod.Skipped = Skipped  # type: ignore[attr-defined]
    mod.__minipytest__ = True  # type: ignore[attr-defined]

    class _Mk(_MarkNamespace):
        def skip(self, *a: Any, **kw: Any) -> Any:  # type: ignore[override]
            reason = kw.get("reason", a[0] if a else "")
            return _SkipMarker(reason)

        def parametrize(self, argnames: str, argvalues: Any, **kw: Any) -> Callable:
            def decorate(func: Callable) -> Callable:
                cases = list(getattr(func, "_minipytest_params", []))
                cases.append((argnames, list(argvalues)))
                func._minipytest_params = cases  # type: ignore[attr-defined]
                return func

            return decorate

    mod.mark = _Mk()  # type: ignore[attr-defined]
    return mod


def install() -> bool:
    """Register the stand-in as `pytest` unless the real one is importable.

    Returns True if the stand-in was installed.
    """
    if REAL_PYTEST:
        return False
    if "pytest" in sys.modules and getattr(sys.modules["pytest"], "__minipytest__", False):
        return True
    sys.modules["pytest"] = _build_module()
    return True


# ---------------------------------------------------------------------------
# Fixture resolution
# ---------------------------------------------------------------------------


class _FixtureRegistry:
    def __init__(self) -> None:
        self.defs: dict[str, tuple[Callable, str]] = {}
        self._session_cache: dict[str, Any] = {}
        self._session_finalizers: list[Callable[[], None]] = []

    def register_module(self, module: types.ModuleType) -> None:
        for name, obj in vars(module).items():
            spec = getattr(obj, "_minipytest_fixture", None)
            if spec:
                self.defs[spec["name"]] = (obj, spec["scope"])

    def resolve(
        self,
        name: str,
        *,
        function_cache: dict[str, Any],
        finalizers: list[Callable[[], None]],
        builtins: dict[str, Callable[[], Any]],
        stack: Optional[tuple[str, ...]] = None,
    ) -> Any:
        stack = stack or ()
        if name in stack:
            raise RuntimeError(f"fixture cycle: {' -> '.join(stack + (name,))}")
        if name in function_cache:
            return function_cache[name]
        if name in self._session_cache:
            return self._session_cache[name]
        if name in builtins:
            value = builtins[name]()
            function_cache[name] = value
            return value
        if name not in self.defs:
            raise RuntimeError(f"fixture {name!r} not found")

        func, scope = self.defs[name]
        kwargs = {}
        for param in inspect.signature(func).parameters:
            kwargs[param] = self.resolve(
                param,
                function_cache=function_cache,
                finalizers=finalizers,
                builtins=builtins,
                stack=stack + (name,),
            )

        if inspect.isgeneratorfunction(func):
            gen = func(**kwargs)
            value = next(gen)

            def finalize(g: Iterator = gen) -> None:
                try:
                    next(g)
                except StopIteration:
                    pass

            if scope == "session":
                self._session_finalizers.append(finalize)
            else:
                finalizers.append(finalize)
        else:
            value = func(**kwargs)

        if scope == "session":
            self._session_cache[name] = value
        else:
            function_cache[name] = value
        return value

    def teardown_session(self) -> None:
        for fin in reversed(self._session_finalizers):
            try:
                fin()
            except Exception:  # noqa: BLE001, S110 - teardown must not mask results
                pass
        self._session_finalizers.clear()
        self._session_cache.clear()


# ---------------------------------------------------------------------------
# Collection and execution
# ---------------------------------------------------------------------------


def _collect_module(module: types.ModuleType, registry: _FixtureRegistry) -> list[Item]:
    items: list[Item] = []
    rel = getattr(module, "__minipytest_relpath__", module.__name__)

    def expand(func: Callable, cls: Optional[type], name: str) -> list[Item]:
        param_sets = list(getattr(func, "_minipytest_params", []))
        marks = list(getattr(func, "_minipytest_marks", []))
        combos: list[dict[str, Any]] = [{}]
        for argnames, argvalues in param_sets:
            names = [n.strip() for n in argnames.split(",")]
            expanded: list[dict[str, Any]] = []
            for base in combos:
                for value in argvalues:
                    vals = value if len(names) > 1 else (value,)
                    merged = dict(base)
                    merged.update(dict(zip(names, vals)))
                    expanded.append(merged)
            combos = expanded
        out: list[Item] = []
        for params in combos:
            suffix = ""
            if params:
                suffix = "[" + "-".join(repr(v) for v in params.values()) + "]"
            prefix = f"{rel}::{cls.__name__}::" if cls else f"{rel}::"
            out.append(
                Item(
                    name=name + suffix,
                    nodeid=prefix + name + suffix,
                    func=func,
                    cls=cls,
                    module=module,
                    params=params,
                    marks=marks,
                )
            )
        return out

    for name, obj in sorted(vars(module).items()):
        if name.startswith("test_") and inspect.isfunction(obj):
            items.extend(expand(obj, None, name))
        elif name.startswith("Test") and inspect.isclass(obj):
            for meth_name, meth in sorted(vars(obj).items()):
                if meth_name.startswith("test_") and inspect.isfunction(meth):
                    items.extend(expand(meth, obj, meth_name))
    registry.register_module(module)
    return items


def _builtin_fixtures(config: Config, tmp_root: Path, counter: dict[str, int]) -> dict[str, Callable[[], Any]]:
    def tmp_path() -> Path:
        counter["n"] += 1
        p = tmp_root / f"t{counter['n']:04d}"
        p.mkdir(parents=True, exist_ok=True)
        return p

    return {
        "tmp_path": tmp_path,
        "pytestconfig": lambda: config,
        "request": lambda: types.SimpleNamespace(config=config),
    }


@dataclass
class _Outcome:
    item: Item
    status: str  # passed | failed | skipped | error
    detail: str = ""


def run(argv: Optional[list[str]] = None) -> int:
    """Collect and run the suite. Returns a pytest-compatible exit code."""
    argv = list(argv if argv is not None else sys.argv[1:])
    install()

    tests_dir = Path(__file__).resolve().parent
    repo_root = tests_dir.parent
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))

    quiet = "-q" in argv or "--quiet" in argv
    verbose = "-v" in argv
    argv = [a for a in argv if a not in ("-q", "--quiet", "-v")]

    # conftest first: it owns the options and the fixtures.
    import importlib.util

    def load(path: Path, modname: str) -> types.ModuleType:
        spec = importlib.util.spec_from_file_location(modname, path)
        if spec is None or spec.loader is None:
            raise ImportError(f"cannot load {path}")
        module = importlib.util.module_from_spec(spec)
        sys.modules[modname] = module
        spec.loader.exec_module(module)
        module.__minipytest_relpath__ = str(path.relative_to(repo_root)).replace("\\", "/")  # type: ignore[attr-defined]
        return module

    conftest = load(tests_dir / "conftest.py", "conftest")

    parser = Parser()
    if hasattr(conftest, "pytest_addoption"):
        conftest.pytest_addoption(parser)

    try:
        values, positional = parser.parse(argv)
    except UsageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4

    config = Config(values, argv)
    try:
        if hasattr(conftest, "pytest_configure"):
            conftest.pytest_configure(config)
    except UsageError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 4

    # Which files?
    targets: list[Path] = []
    for token in positional:
        p = Path(token)
        if not p.is_absolute():
            p = repo_root / token
        if p.is_dir():
            targets.extend(sorted(p.glob("test_*.py")))
        elif p.exists():
            targets.append(p)
        else:
            print(f"ERROR: no such test path: {token}", file=sys.stderr)
            return 4
    if not targets:
        targets = sorted(tests_dir.glob("test_*.py"))
    if not targets:
        print("no tests collected")
        return 5

    registry = _FixtureRegistry()
    registry.register_module(conftest)

    items: list[Item] = []
    for path in targets:
        module = load(path, path.stem)
        items.extend(_collect_module(module, registry))

    if hasattr(conftest, "pytest_collection_modifyitems"):
        conftest.pytest_collection_modifyitems(config, items)

    tmp_root = Path(tempfile.mkdtemp(prefix="minipytest_"))
    counter = {"n": 0}
    builtins = _builtin_fixtures(config, tmp_root, counter)

    results: list[_Outcome] = []
    if not quiet:
        print(f"minipytest (pytest not installed) - {len(items)} tests from {len(targets)} files")
        print("-" * 72)

    for item in items:
        if "skip" in item.keywords and item.extra_markers:
            results.append(_Outcome(item, "skipped", "deselected by conftest"))
            if not quiet:
                print("s", end="", flush=True)
            continue

        function_cache: dict[str, Any] = dict(item.params)
        finalizers: list[Callable[[], None]] = []
        try:
            kwargs: dict[str, Any] = {}
            sig = inspect.signature(item.func)
            for pname in sig.parameters:
                if pname == "self":
                    continue
                if pname in item.params:
                    kwargs[pname] = item.params[pname]
                    continue
                kwargs[pname] = registry.resolve(
                    pname,
                    function_cache=function_cache,
                    finalizers=finalizers,
                    builtins=builtins,
                )
            target = item.func
            if item.cls is not None:
                instance = item.cls()
                target = item.func.__get__(instance, item.cls)
            target(**kwargs)
            results.append(_Outcome(item, "passed"))
            if not quiet:
                print("." if not verbose else f"  [ok] {item.nodeid}\n", end="", flush=True)
        except Skipped as exc:
            results.append(_Outcome(item, "skipped", exc.reason))
            if not quiet:
                print("s" if not verbose else f"  [skip] {item.nodeid}: {exc.reason}\n", end="", flush=True)
        except AssertionError as exc:
            results.append(_Outcome(item, "failed", f"{exc}\n{traceback.format_exc()}"))
            if not quiet:
                print("F" if not verbose else f"  [FAIL] {item.nodeid}\n", end="", flush=True)
        except Exception as exc:  # noqa: BLE001
            results.append(
                _Outcome(item, "error", f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}")
            )
            if not quiet:
                print("E" if not verbose else f"  [ERR] {item.nodeid}\n", end="", flush=True)
        finally:
            for fin in reversed(finalizers):
                try:
                    fin()
                except Exception:  # noqa: BLE001, S110
                    pass

    registry.teardown_session()

    passed = sum(1 for r in results if r.status == "passed")
    skipped = sum(1 for r in results if r.status == "skipped")
    bad = [r for r in results if r.status in ("failed", "error")]

    print()
    print("-" * 72)
    for r in bad:
        print(f"\n{'=' * 72}\n{r.status.upper()}: {r.nodeid if hasattr(r, 'nodeid') else r.item.nodeid}\n{'-' * 72}")
        print(r.detail.rstrip())
    print("-" * 72)
    cap = config.getoption("--budget-cap")
    print(f"{passed} passed, {len(bad)} failed, {skipped} skipped   (--budget-cap {cap})")
    import shutil

    shutil.rmtree(tmp_root, ignore_errors=True)
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(run())
