#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_web_ui_server.py - the dashboard's server and its static assets.

    pytest tests/test_web_ui_server.py -v

Three things are worth testing here and one thing is not.

Worth testing:
  1. **The API is read-only.** There is no route that opens or closes a position,
     and POST is refused. A dashboard that can move money is an attack surface,
     and the consensus gate lives in Python precisely so it cannot be edited
     around from dev-tools.
  2. **The static handler cannot be walked out of.** `..` in a path, an encoded
     traversal, a config file outside the web root - all refused, and only a
     short allow-list of extensions is served at all.
  3. **Loopback by default.** A paper-trading dashboard with no authentication
     must not land on a LAN because someone typed 0.0.0.0 out of habit.

Not worth testing: whether the octopus looks good. That is a judgement, not an
assertion. What the asset tests do check is the mechanical part - that the page
references the files it needs, that the thresholds it prints match the engine's
constants, and that nothing loads from a CDN, because a dashboard for an offline
rig must render with the network unplugged.
"""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Iterator

import pytest

from consensus_engine import EXECUTE_THRESHOLD_PCT, HOLD_THRESHOLD_PCT
from groktopus_swarm import build_swarm
from web_ui_server import STATIC_DIR, TelemetryHub, make_handler, serve


@pytest.fixture(scope="module")
def hub() -> Iterator[TelemetryHub]:
    h = TelemetryHub(build_swarm(seed=606, n_steps=900), interval_s=0.0, autostart=False)
    yield h
    h.stop()


@pytest.fixture(scope="module")
def base_url(hub: TelemetryHub) -> Iterator[str]:
    httpd = serve(hub, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()
    httpd.server_close()


def get(url: str) -> tuple[int, bytes, dict[str, str]]:
    with urllib.request.urlopen(url, timeout=15) as r:
        return r.status, r.read(), dict(r.headers)


# ---------------------------------------------------------------------------
# The hub
# ---------------------------------------------------------------------------


class TestTelemetryHub:
    def test_snapshot_available_before_the_worker_starts(self, hub: TelemetryHub) -> None:
        assert len(hub.snapshot()["agents"]) == 8

    def test_snapshot_is_json_safe(self, hub: TelemetryHub) -> None:
        json.dumps(hub.snapshot())

    def test_publish_refreshes_the_snapshot(self) -> None:
        h = TelemetryHub(build_swarm(seed=607, n_steps=900), interval_s=0.0, autostart=False)
        before = h.snapshot()["market"]["step"]
        h.swarm.market.advance(5)
        h.publish()
        assert h.snapshot()["market"]["step"] == before + 5
        h.stop()

    def test_worker_runs_then_stops(self) -> None:
        import time  # noqa: PLC0415

        h = TelemetryHub(build_swarm(seed=608, n_steps=900), interval_s=0.0, cycles=80)
        deadline = time.time() + 30
        while h.swarm.cycles_run == 0 and h.running and time.time() < deadline:
            time.sleep(0.02)
        h.stop(timeout=30)
        assert h.swarm.cycles_run > 0
        assert h.error is None
        assert h.running is False

    def test_a_dead_worker_is_visible_not_silent(self) -> None:
        # A failing brain is already handled inside the swarm, so break the loop
        # one level up instead: the point is that the hub reports the failure
        # rather than dying quietly behind a snapshot that looks fine.
        # The worker body is driven directly rather than through start()/stop(),
        # which would race: stop() sets the event, and a loop that sees it before
        # its first iteration exits cleanly without ever reaching the fault.
        h = TelemetryHub(build_swarm(seed=609, n_steps=900), interval_s=0.0, autostart=False)
        h.swarm.cycle = None  # type: ignore[assignment]
        h._loop()
        assert h.error is not None
        assert "TypeError" in h.error
        assert h.snapshot()["hub"]["error"] == h.error

    @pytest.mark.slow
    def test_campaign_is_cached_and_reproduces_the_reference_run(self) -> None:
        h = TelemetryHub(build_swarm(seed=610, n_steps=400), interval_s=0.0, autostart=False)
        a = h.campaign()
        assert h.campaign() is a
        assert a["executed"] == 1_981
        assert a["end_equity_cents"] == 19_763_839
        assert "INPUT" in a["disclosure"]
        h.stop()


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


class TestRoutes:
    def test_index(self, base_url: str) -> None:
        status, body, headers = get(base_url + "/")
        assert status == 200
        assert b"GrokTOPUS" in body
        assert headers["Content-Type"].startswith("text/html")
        assert headers["X-Content-Type-Options"] == "nosniff"

    def test_health(self, base_url: str) -> None:
        status, body, _ = get(base_url + "/api/health")
        assert status == 200 and json.loads(body)["ok"] is True

    def test_telemetry(self, base_url: str) -> None:
        status, body, headers = get(base_url + "/api/telemetry")
        data = json.loads(body)
        assert status == 200
        assert headers["Cache-Control"] == "no-store"
        assert len(data["agents"]) == 8
        assert data["consensus"]["execute_threshold_pct"] == EXECUTE_THRESHOLD_PCT
        assert data["max_concurrent_inferences"] == 1

    @pytest.mark.parametrize("path,ctype", [("/style.css", "text/css"), ("/app.js", "javascript")])
    def test_static_assets(self, base_url: str, path: str, ctype: str) -> None:
        status, body, headers = get(base_url + path)
        assert status == 200 and body
        assert ctype in headers["Content-Type"]

    def test_unknown_api_endpoint_is_404(self, base_url: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(base_url + "/api/does-not-exist")
        assert exc.value.code == 404

    def test_missing_static_file_is_404(self, base_url: str) -> None:
        with pytest.raises(urllib.error.HTTPError) as exc:
            get(base_url + "/nope.js")
        assert exc.value.code == 404

    def test_post_is_refused(self, base_url: str) -> None:
        req = urllib.request.Request(base_url + "/api/telemetry", data=b"{}", method="POST")
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(req, timeout=10)
        assert exc.value.code == 405
        assert b"read-only" in exc.value.read()

    def test_no_write_capability_is_reachable_from_the_http_layer(self) -> None:
        import web_ui_server  # noqa: PLC0415

        src = Path(web_ui_server.__file__).read_text(encoding="utf-8")
        for forbidden in ("trip_kill_switch", ".execute(", ".close(", ".credit(", ".charge("):
            assert forbidden not in src, f"{forbidden} is reachable from the HTTP layer"


# ---------------------------------------------------------------------------
# Safety
# ---------------------------------------------------------------------------


class TestSafety:
    @pytest.mark.parametrize(
        "path",
        [
            "/../core/risk_guard.py",
            "/../../etc/passwd",
            "/%2e%2e/core/risk_guard.py",
            "/..%2fcore%2frisk_guard.py",
            "/config/profiles.json",
            "/state/risk_guard.db",
        ],
    )
    def test_traversal_and_out_of_root_refused(self, base_url: str, path: str) -> None:
        try:
            status, _, _ = get(base_url + path)
        except urllib.error.HTTPError as exc:
            assert exc.code in (403, 404), exc.code
            return
        assert status != 200, f"{path} was served"

    @pytest.mark.parametrize("name", ["notes.py", "secrets.env", "dump.db"])
    def test_only_web_extensions_are_served(
        self, hub: TelemetryHub, tmp_path: Path, name: str
    ) -> None:
        (tmp_path / name).write_text("secret", encoding="utf-8")
        httpd = serve(hub, port=0, static_dir=tmp_path)
        threading.Thread(target=httpd.serve_forever, daemon=True).start()
        try:
            with pytest.raises(urllib.error.HTTPError) as exc:
                get(f"http://127.0.0.1:{httpd.server_address[1]}/{name}")
            assert exc.value.code == 403
        finally:
            httpd.shutdown()
            httpd.server_close()

    def test_make_handler_is_bound_to_one_root(self, hub: TelemetryHub, tmp_path: Path) -> None:
        assert make_handler(hub, tmp_path) is not make_handler(hub, STATIC_DIR)

    def test_public_bind_refused_by_default(self, hub: TelemetryHub) -> None:
        with pytest.raises(ValueError, match="refusing to bind"):
            serve(hub, host="0.0.0.0", port=0)

    def test_public_bind_allowed_when_asked_explicitly(self, hub: TelemetryHub) -> None:
        httpd = serve(hub, host="0.0.0.0", port=0, allow_public=True)
        try:
            assert httpd.server_address[1] > 0
        finally:
            httpd.server_close()

    @pytest.mark.parametrize("host", ["127.0.0.1", "localhost"])
    def test_loopback_hosts_accepted(self, hub: TelemetryHub, host: str) -> None:
        httpd = serve(hub, host=host, port=0)
        try:
            assert httpd.server_address[1] > 0
        finally:
            httpd.server_close()


# ---------------------------------------------------------------------------
# Static assets
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def files() -> dict[str, str]:
    return {
        n: (STATIC_DIR / n).read_text(encoding="utf-8")
        for n in ("index.html", "style.css", "app.js")
    }


class TestStaticAssets:
    def test_all_three_files_exist(self) -> None:
        for n in ("index.html", "style.css", "app.js"):
            assert (STATIC_DIR / n).is_file(), f"web_ui/{n} is missing"

    def test_index_references_its_assets(self, files: dict[str, str]) -> None:
        assert "/style.css" in files["index.html"]
        assert "/app.js" in files["index.html"]

    def test_no_external_resource_is_loaded(self, files: dict[str, str]) -> None:
        # An offline rig must render with the network unplugged, so nothing may
        # come from a CDN - not a font, not a chart library, not an icon set.
        for name, text in files.items():
            for m in re.finditer(r"""(?:src|href)\s*=\s*["']([^"']+)["']""", text):
                url = m.group(1)
                assert not url.startswith(("http://", "https://", "//")), f"{name} loads {url}"

    def test_thresholds_shown_match_the_engine(self, files: dict[str, str]) -> None:
        html = files["index.html"]
        assert str(EXECUTE_THRESHOLD_PCT) in html
        assert str(HOLD_THRESHOLD_PCT) in html

    def test_all_eight_agents_have_a_tentacle(self, files: dict[str, str]) -> None:
        js = files["app.js"]
        for name in ("TIDAL", "NORO", "LUMEN", "ZEPHR", "OKAPI", "RUNE", "VESKA", "MARIN"):
            assert re.search(rf"\b{name}\s*:\s*-?\d", js), f"{name} has no tentacle angle"

    def test_the_page_only_reads(self, files: dict[str, str]) -> None:
        js = files["app.js"]
        assert 'method: "POST"' not in js and "method: 'POST'" not in js
        for m in re.finditer(r"""fetch\(\s*["']([^"']+)["']""", js):
            assert m.group(1).startswith("/api/"), m.group(1)

    def test_no_browser_storage_is_used(self, files: dict[str, str]) -> None:
        for name, text in files.items():
            for api in ("localStorage", "sessionStorage", "indexedDB"):
                assert api not in text, f"{name} uses {api}"
