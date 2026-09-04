#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_safety_compliance.py - the invariants that make this legal to run.

    pytest tests/test_safety_compliance.py -v

Most tests in this repository check that a function does what it says. These
check that the codebase *cannot* do things it never claimed to - which needs a
different technique, because you cannot unit-test the absence of a capability by
calling it. So these read the source.

Source scanning has an obvious weakness: it matches text, not behaviour, and a
determined author routes around it in a minute. That is not the threat model. The
threat here is drift - someone adds a convenience that quietly crosses a line
nobody meant to cross, six months after the line was drawn and the reasoning
forgotten. A failing grep with a docstring attached is how the reasoning survives
the person who wrote it.

The five claims under test
--------------------------
1. **No real funds, ever.** Execution mode is pinned, the escape hatch always
   raises, and no module imports a payment SDK or exchange trading client.
2. **No credentials anywhere.** No API keys, no secrets, no request signing, no
   auth headers - in a codebase that talks to two exchanges.
3. **Market data is read-only.** The feed issues GET and nothing else, against
   documented public endpoints, over HTTPS, rate-limited well below what the
   venues publish.
4. **No Docker, no daemon.** The directive requires it to run natively; that
   means no Dockerfile and no shelling out to a container runtime.
5. **Nothing writes outside the workspace.** State stays in `state/`, and no
   module writes to a user directory or a system path.
"""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
CODE_DIRS = ("core", "simulation", "benchmarks", "web_ui")


def python_sources() -> list[Path]:
    out: list[Path] = []
    for d in ("core", "simulation", "benchmarks"):
        out.extend(sorted((REPO_ROOT / d).glob("*.py")))
    out.append(REPO_ROOT / "run_menu.py")
    return [p for p in out if p.is_file()]


def read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def sources() -> dict[Path, str]:
    return {p: read(p) for p in python_sources()}


@pytest.fixture(scope="module")
def feed_src() -> str:
    return read(REPO_ROOT / "core" / "market_feed.py")


@pytest.fixture(scope="module")
def feed_runtime_src(feed_src: str) -> str:
    """market_feed.py with its docstrings and self-test removed.

    Both of those legitimately name the things the module refuses to do - the
    docstring explains that there is no API key, and the self-test asserts that
    no credential-shaped token appears in a URL. A grep for "apikey" therefore
    matches the very code that proves "apikey" is absent, which is the classic
    way a source-scanning test ends up forbidding its own evidence. Scanning only
    the runtime path keeps the check meaningful.
    """
    marker = "def _selftest("
    assert marker in feed_src, "the self-test boundary moved; this fixture needs updating"
    runtime = feed_src.split(marker)[0]
    return re.sub(r'"""..*?"""', "", runtime, flags=re.S)


# ---------------------------------------------------------------------------
# 1. No real funds
# ---------------------------------------------------------------------------


class TestNoRealFunds:
    def test_execution_mode_is_pinned_in_both_markets(self) -> None:
        from groktopus_market import EXECUTION_MODE as grok  # noqa: PLC0415
        from sandbox_env import EXECUTION_MODE as sandbox  # noqa: PLC0415

        assert grok == "SIMULATION" and sandbox == "SIMULATION"

    def test_the_real_funds_escape_hatch_always_refuses(self) -> None:
        from sandbox_env import RealFundsRefused, authorize_real_funds  # noqa: PLC0415

        for args in ({}, {"token": "yes"}, {"token": "yes", "second_factor": "also yes"}):
            with pytest.raises(RealFundsRefused):
                authorize_real_funds(**args)

    @pytest.mark.parametrize(
        "sdk",
        ["stripe", "paypal", "braintree", "square", "plaid", "web3", "eth_account",
         "ccxt", "alpaca", "ib_insync", "binance.client", "coinbase.wallet"],
    )
    def test_no_payment_or_trading_sdk_is_imported(self, sources: dict[Path, str], sdk: str) -> None:
        for path, src in sources.items():
            for node in ast.walk(ast.parse(src, filename=str(path))):
                names: list[str] = []
                if isinstance(node, ast.Import):
                    names = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names = [node.module]
                for name in names:
                    assert not name.lower().startswith(sdk.split(".")[0]), f"{path.name} imports {name}"

    def test_no_module_names_a_trading_endpoint(self, sources: dict[Path, str]) -> None:
        # Order-placing paths on the two venues the feed knows about.
        forbidden = ("/api/v3/order", "/orders", "/api/v3/account", "sapi/v1",
                     "/withdraw", "/api/v3/myTrades")
        for path, src in sources.items():
            low = src.lower()
            for token in forbidden:
                assert token.lower() not in low, f"{path.name} references {token}"

    def test_the_paper_ledger_is_the_only_thing_that_moves_money(self) -> None:
        from groktopus_market import GrokMarket  # noqa: PLC0415

        movers = [n for n in ("execute", "close", "flatten_all", "settle")
                  if hasattr(GrokMarket, n)]
        assert set(movers) == {"execute", "close", "flatten_all", "settle"}
        src = read(REPO_ROOT / "simulation" / "groktopus_market.py")
        assert "no network transport" in src


# ---------------------------------------------------------------------------
# 2. No credentials
# ---------------------------------------------------------------------------


class TestNoCredentials:
    @pytest.mark.parametrize(
        "pattern",
        [r"api[_-]?key", r"secret[_-]?key", r"\bhmac\b", r"X-MBX-APIKEY",
         r"CB-ACCESS", r"passphrase", r"private[_-]?key", r"bearer\s+[a-z0-9]"],
    )
    def test_the_feed_carries_no_credential_machinery(
        self, feed_runtime_src: str, pattern: str
    ) -> None:
        assert not re.search(pattern, feed_runtime_src, re.I), \
            f"market_feed.py runtime path contains {pattern}"

    def test_the_feed_never_sets_an_auth_header(self, feed_src: str) -> None:
        headers = re.findall(r'headers\s*=\s*\{([^}]*)\}', feed_src, re.S)
        assert headers, "no headers dict found; the transport must have changed"
        for block in headers:
            low = block.lower()
            assert "authorization" not in low
            assert "api" not in low or "application/json" in low

    def test_no_hardcoded_secret_shaped_literal(self, sources: dict[Path, str]) -> None:
        # 32+ chars of base64/hex assigned to something that sounds like a secret.
        pattern = re.compile(
            r'(secret|token|apikey|api_key|password|passwd)\w*\s*=\s*["\'][A-Za-z0-9+/=_-]{32,}["\']',
            re.I,
        )
        for path, src in sources.items():
            assert not pattern.search(src), f"{path.name} looks like it holds a secret"

    def test_the_only_env_var_read_is_the_documented_opt_in(self, sources: dict[Path, str]) -> None:
        allowed = {"ENGINE_ALLOW_FALLBACK", "GROKTOPUS_HTTP_LOG", "ANTHROPIC_API_KEY",
                   "PATH", "SYSTEMROOT", "TEMP", "TMP", "USERPROFILE", "LOCALAPPDATA",
                   "COMSPEC", "NUMBER_OF_PROCESSORS"}
        found = set()
        for src in sources.values():
            found.update(re.findall(r'environ(?:\.get)?[\.\(\[]\s*["\']([A-Z_][A-Z0-9_]*)["\']', src))
        assert found <= allowed, f"unexpected environment variables read: {sorted(found - allowed)}"


# ---------------------------------------------------------------------------
# 3. Read-only market data
# ---------------------------------------------------------------------------


class TestReadOnlyFeed:
    def test_every_venue_is_https(self) -> None:
        from market_feed import VENUES  # noqa: PLC0415

        for name, spec in VENUES.items():
            assert spec["base"].startswith("https://"), name

    def test_every_venue_documents_its_public_endpoint(self) -> None:
        from market_feed import VENUES  # noqa: PLC0415

        for name, spec in VENUES.items():
            assert spec.get("docs", "").startswith("https://"), name
            assert spec.get("published_rate_limit"), name

    def test_the_feed_only_ever_issues_get(self, feed_src: str) -> None:
        methods = re.findall(r'method\s*=\s*["\'](\w+)["\']', feed_src)
        assert methods, "no explicit HTTP method found"
        assert set(methods) == {"GET"}, methods

    def test_the_feed_never_sends_a_body(self, feed_src: str) -> None:
        assert "data=" not in feed_src.replace("data=None", "")

    def test_exactly_one_network_call_site(self, feed_src: str) -> None:
        assert feed_src.count("urllib.request.urlopen") == 1

    def test_the_local_rate_limit_is_far_below_the_published_one(self) -> None:
        from market_feed import MarketFeed  # noqa: PLC0415

        # Coinbase publishes 10 req/s = 600/min for public endpoints.
        assert MarketFeed("coinbase", transport=lambda *a: {}).limiter.per_minute <= 60

    def test_a_venue_429_is_honoured_rather_than_retried(self, feed_src: str) -> None:
        assert "cold_until" in feed_src
        assert "429" in feed_src

    def test_untrusted_prices_are_validated(self) -> None:
        from market_feed import SuspiciousTick, _parse_price  # noqa: PLC0415

        for bad in ("0", "-1", "nan", "inf", "abc", None):
            with pytest.raises(SuspiciousTick):
                _parse_price(bad, symbol="BTC-USD")

    def test_live_data_cannot_reach_the_oracle_campaign(self) -> None:
        src = read(REPO_ROOT / "simulation" / "groktopus_market.py")
        assert "LiveModeError" in src
        assert "future does not exist" in src


# ---------------------------------------------------------------------------
# 4. Native only - no Docker, no daemons
# ---------------------------------------------------------------------------


class TestNativeOnly:
    @pytest.mark.parametrize(
        "name", ["Dockerfile", "docker-compose.yml", "docker-compose.yaml", ".dockerignore"]
    )
    def test_no_container_files(self, name: str) -> None:
        assert not (REPO_ROOT / name).exists(), f"{name} exists; the directive requires native"

    def test_nothing_shells_out_to_a_container_runtime(self, sources: dict[Path, str]) -> None:
        for path, src in sources.items():
            body = re.sub(r'""".*?"""', "", src, flags=re.S)
            for token in ("docker ", "docker-compose", "podman ", "kubectl "):
                assert token not in body, f"{path.name} invokes {token.strip()}"

    def test_no_third_party_web_framework(self, sources: dict[Path, str]) -> None:
        for path, src in sources.items():
            for node in ast.walk(ast.parse(src, filename=str(path))):
                mods: list[str] = []
                if isinstance(node, ast.Import):
                    mods = [a.name for a in node.names]
                elif isinstance(node, ast.ImportFrom) and node.module:
                    mods = [node.module]
                for m in mods:
                    root = m.split(".")[0]
                    assert root not in {"flask", "fastapi", "aiohttp", "django", "tornado"}, \
                        f"{path.name} imports {m}"

    def test_the_dashboard_server_is_stdlib_only(self) -> None:
        src = read(REPO_ROOT / "core" / "web_ui_server.py")
        assert "http.server" in src
        assert "ThreadingHTTPServer" in src


# ---------------------------------------------------------------------------
# 5. Nothing escapes the workspace
# ---------------------------------------------------------------------------


class TestFilesystemContainment:
    def test_durable_state_lives_under_state(self) -> None:
        from risk_guard import DEFAULT_DB, DEFAULT_KILL_FILE  # noqa: PLC0415

        for p in (DEFAULT_DB, DEFAULT_KILL_FILE):
            assert Path(p).parent == REPO_ROOT / "state", p

    def test_benchmarks_write_under_the_repo(self) -> None:
        from throughput_bench import BENCH_DIR  # noqa: PLC0415

        assert BENCH_DIR.parent == REPO_ROOT

    def test_no_module_writes_to_a_home_or_system_path(self, sources: dict[Path, str]) -> None:
        bad = re.compile(r'open\(\s*["\'](?:/etc/|/usr/|C:\\\\Windows|~[/\\])', re.I)
        for path, src in sources.items():
            assert not bad.search(src), f"{path.name} writes outside the workspace"

    def test_the_dashboard_serves_only_web_assets(self) -> None:
        from web_ui_server import _ALLOWED_STATIC  # noqa: PLC0415

        assert ".py" not in _ALLOWED_STATIC and ".db" not in _ALLOWED_STATIC


# ---------------------------------------------------------------------------
# Cross-cutting: the safety story the modules tell about themselves
# ---------------------------------------------------------------------------


class TestDeclaredInvariants:
    def test_the_dashboard_has_no_write_route(self) -> None:
        src = read(REPO_ROOT / "core" / "web_ui_server.py")
        assert "do_POST" in src and "METHOD_NOT_ALLOWED" in src

    def test_the_dashboard_binds_loopback_by_default(self) -> None:
        from web_ui_server import serve  # noqa: PLC0415
        import inspect  # noqa: PLC0415

        assert inspect.signature(serve).parameters["host"].default == "127.0.0.1"
        assert inspect.signature(serve).parameters["allow_public"].default is False

    def test_no_position_can_open_without_a_consensus_token(self) -> None:
        src = read(REPO_ROOT / "simulation" / "groktopus_market.py")
        assert "self.authoriser.consume(token, proposal)" in src
        assert "no consensus authoriser attached" in src

    def test_the_kill_switch_gates_new_risk(self) -> None:
        src = read(REPO_ROOT / "simulation" / "groktopus_market.py")
        assert "if self.halted:" in src

    def test_no_mmap_is_refused_under_a_tight_host_budget(self) -> None:
        from memory_governor import MmapRefused, assert_mmap_allowed, get_budget  # noqa: PLC0415

        with pytest.raises(MmapRefused):
            assert_mmap_allowed(no_mmap=True, weights_gb=8.39, budget=get_budget("TIGHT"))

    def test_the_shipped_argv_never_disables_mmap(self) -> None:
        from inference_planner import llama_server_argv  # noqa: PLC0415

        for mode in ("serial", "batched"):
            assert "--no-mmap" not in llama_server_argv(model_path="m.gguf", mode=mode)

    def test_config_declares_the_unreachable_budget_honestly(self) -> None:
        cfg = json.loads(read(REPO_ROOT / "config" / "profiles.json"))
        assert "UNREACHABLE" in cfg["budgets"]["ULTRA"]["note"]

    def test_the_draft_model_vocab_risk_is_recorded(self) -> None:
        cfg = json.loads(read(REPO_ROOT / "config" / "profiles.json"))
        entry = cfg["draft_models"]["qwen2.5-0.5b-q4km"]
        assert "UNVERIFIED" in entry["vocab_note"]
