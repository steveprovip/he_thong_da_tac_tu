#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web_ui_server.py - the loopback telemetry server behind the GrokTOPUS dashboard.

Standard library only: `http.server.ThreadingHTTPServer`. No Flask, no FastAPI,
no aiohttp. Three reasons, in order of how much they matter here:

1. **RAM.** The swarm budget is 6.0 GB for the whole process tree. A web
   framework and its dependency tree is tens of megabytes of import for what is,
   in the end, four routes that return JSON.
2. **No new failure mode.** A dashboard that cannot start because a package is
   missing is a dashboard that does not exist. This one runs on any Python 3.10+.
3. **Loopback only.** `serve()` refuses a non-loopback bind address unless the
   caller passes `allow_public=True` and means it. A paper-trading dashboard is
   not something to put on a LAN by accident.

The server is read-only. There is no route that opens a position, closes one,
changes a threshold or trips the kill switch. Everything the browser can reach
is a `GET` that returns a snapshot. That is deliberate: a dashboard that can move
money is an attack surface, and the consensus gate lives in Python, not in
JavaScript where anyone with dev-tools could edit around it.

Routes
------
    GET /                    the dashboard
    GET /style.css /app.js   its static assets
    GET /api/telemetry       one snapshot: 8 agents, consensus, market, hardware
    GET /api/campaign        the calibrated 1,981-trade reference run (cached)
    GET /api/health          liveness, for a scripted check

CLI:
    python core/web_ui_server.py                 # http://127.0.0.1:8765
    python core/web_ui_server.py --port 9000 --interval 0.5
    python core/web_ui_server.py --live          # eight brains on llama-server
    python core/web_ui_server.py --selftest
"""

from __future__ import annotations

import argparse
import asyncio
import json
import mimetypes
import os
import socket
import sys
import threading
import time
import webbrowser
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))
_SIM = str(_HERE.parent / "simulation")
if _SIM not in sys.path:
    sys.path.insert(0, _SIM)

from groktopus_swarm import GrokSwarm, SwarmConfig, build_swarm  # noqa: E402
from groktopus_market import (  # noqa: E402
    CampaignConfig,
    build_default_stack,
    run_paper_campaign,
)
from risk_guard import RiskGuard  # noqa: E402

__all__ = ["TelemetryHub", "make_handler", "serve", "DEFAULT_PORT", "STATIC_DIR"]

REPO_ROOT = _HERE.parent
STATIC_DIR = REPO_ROOT / "web_ui"
DEFAULT_PORT = 8765
_LOOPBACK = {"127.0.0.1", "::1", "localhost"}

_ALLOWED_STATIC = {".html", ".css", ".js", ".svg", ".ico", ".json", ".png", ".webmanifest"}


class TelemetryHub:
    """Runs a swarm on a background thread and publishes its latest snapshot.

    The HTTP handler never touches the swarm directly - it reads `snapshot()`,
    which returns the last dict the worker published under a lock. A slow browser
    therefore cannot stall the swarm, and a mid-cycle read cannot observe a
    half-updated market.
    """

    def __init__(
        self,
        swarm: Optional[GrokSwarm] = None,
        *,
        interval_s: float = 0.35,
        cycles: int = 100_000,
        autostart: bool = True,
    ) -> None:
        self.swarm = swarm or build_swarm()
        self.interval_s = max(0.0, float(interval_s))
        self.cycles = int(cycles)
        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = self.swarm.telemetry()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.started_at = time.time()
        self.error: Optional[str] = None
        self._campaign_cache: Optional[dict[str, Any]] = None
        if autostart:
            self.start()

    # -- worker ------------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._loop, name="groktopus-swarm", daemon=True)
        self._thread.start()

    def _loop(self) -> None:
        async def drive() -> None:
            for _ in range(self.cycles):
                if self._stop.is_set():
                    break
                m = self.swarm.market
                if m.step + self.swarm.config.horizon_steps + 1 >= m.last_step:
                    break
                await self.swarm.cycle()
                self.publish()
                if self.interval_s:
                    await asyncio.sleep(self.interval_s)

        try:
            asyncio.run(drive())
        except Exception as exc:  # noqa: BLE001 - a dead worker must be visible, not silent
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.publish()

    def publish(self) -> None:
        snap = self.swarm.telemetry()
        snap["hub"] = {
            "running": self.running,
            "interval_s": self.interval_s,
            "error": self.error,
        }
        with self._lock:
            self._snapshot = snap

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        t = self._thread
        if t is not None:
            t.join(timeout=timeout)
            self._thread = None

    @property
    def running(self) -> bool:
        t = self._thread
        return bool(t is not None and t.is_alive())

    # -- readers -----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return self._snapshot

    def campaign(self) -> dict[str, Any]:
        """The calibrated reference run. Computed once, then cached.

        It is a few seconds of work and it never changes for a fixed seed, so
        recomputing it per request would be a way to let a browser refresh loop
        pin a core of the i5 for no reason.
        """
        if self._campaign_cache is None:
            market, engine = build_default_stack(seed=20260904)
            res = run_paper_campaign(market, engine, CampaignConfig(seed=20260904))
            self._campaign_cache = {
                **res.as_dict(),
                "equity_curve": market.telemetry(curve_points=300)["equity_curve"],
                "disclosure": (
                    "Forecast accuracy is an INPUT to this simulation, not a result of "
                    "it. CampaignConfig.skill_alpha sets how often the swarm's "
                    "directional call is right; the campaign then measures what that "
                    "accuracy is worth after spread, slippage and fees. The shipped "
                    "default reproduces the reference scenario on seed 20260904."
                ),
            }
        return self._campaign_cache


def _json_bytes(payload: Any) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def make_handler(
    hub: TelemetryHub, static_dir: Path = STATIC_DIR
) -> type[BaseHTTPRequestHandler]:
    """Build a request handler bound to one hub and one static directory."""
    root = Path(static_dir).resolve()

    class Handler(BaseHTTPRequestHandler):
        server_version = "GrokTOPUS/1.0"
        protocol_version = "HTTP/1.1"

        # -- plumbing ------------------------------------------------------

        def log_message(self, fmt: str, *args: Any) -> None:
            """Silent by default. The console is the swarm's, not the socket's."""
            if os.environ.get("GROKTOPUS_HTTP_LOG") == "1":
                super().log_message(fmt, *args)

        def _send(self, status: HTTPStatus, body: bytes, content_type: str,
                  *, cache: str = "no-store") -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", cache)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def _json(self, payload: Any, status: HTTPStatus = HTTPStatus.OK) -> None:
            self._send(status, _json_bytes(payload), "application/json; charset=utf-8")

        def _error(self, status: HTTPStatus, message: str) -> None:
            self._json({"error": message, "status": int(status)}, status)

        # -- routes --------------------------------------------------------

        def do_HEAD(self) -> None:  # noqa: N802
            self.do_GET()

        def do_GET(self) -> None:  # noqa: N802
            path = urlparse(self.path).path
            if path in ("/", "/index.html"):
                return self._static("index.html")
            if path == "/api/health":
                return self._json(
                    {
                        "ok": True,
                        "running": hub.running,
                        "uptime_s": round(time.time() - hub.started_at, 2),
                        "cycles_run": hub.swarm.cycles_run,
                        "error": hub.error,
                    }
                )
            if path == "/api/telemetry":
                return self._json(hub.snapshot())
            if path == "/api/campaign":
                return self._json(hub.campaign())
            if path.startswith("/api/"):
                return self._error(HTTPStatus.NOT_FOUND, f"no such endpoint: {path}")
            return self._static(path.lstrip("/"))

        def do_POST(self) -> None:  # noqa: N802
            # There is no write route, on purpose. Money moves through the
            # consensus gate in Python or it does not move.
            self._error(
                HTTPStatus.METHOD_NOT_ALLOWED,
                "this dashboard is read-only; nothing here can place or close an order",
            )

        # -- static --------------------------------------------------------

        def _static(self, relative: str) -> None:
            if not relative:
                relative = "index.html"
            candidate = (root / relative).resolve()
            try:
                candidate.relative_to(root)
            except ValueError:
                return self._error(HTTPStatus.FORBIDDEN, "path escapes the static root")
            if candidate.suffix.lower() not in _ALLOWED_STATIC:
                return self._error(HTTPStatus.FORBIDDEN, f"refusing to serve {candidate.suffix}")
            if not candidate.is_file():
                return self._error(HTTPStatus.NOT_FOUND, f"not found: {relative}")
            ctype = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
            if ctype.startswith(("text/", "application/javascript", "application/json")):
                ctype += "; charset=utf-8"
            self._send(HTTPStatus.OK, candidate.read_bytes(), ctype, cache="no-cache")

    return Handler


def serve(
    hub: Optional[TelemetryHub] = None,
    *,
    host: str = "127.0.0.1",
    port: int = DEFAULT_PORT,
    static_dir: Path = STATIC_DIR,
    allow_public: bool = False,
) -> ThreadingHTTPServer:
    """Create (but do not block on) the HTTP server. Loopback unless told otherwise."""
    if host not in _LOOPBACK and not allow_public:
        raise ValueError(
            f"refusing to bind {host}: this dashboard shows a paper account and has no "
            f"authentication. Pass allow_public=True if you genuinely want it on a network."
        )
    hub = hub or TelemetryHub()
    httpd = ThreadingHTTPServer((host, port), make_handler(hub, static_dir))
    httpd.daemon_threads = True
    httpd.hub = hub  # type: ignore[attr-defined]
    return httpd


def _selftest() -> int:
    import urllib.request  # noqa: PLC0415

    passed = 0
    failed: list[str] = []

    def check(name: str, fn: Callable[[], Any]) -> None:
        nonlocal passed
        try:
            fn()
            passed += 1
        except AssertionError as exc:
            failed.append(f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}: unexpected {type(exc).__name__}: {exc}")

    hub = TelemetryHub(build_swarm(seed=1, n_steps=900), interval_s=0.0, autostart=False)
    httpd = serve(hub, port=0)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def get(path: str) -> tuple[int, bytes]:
        with urllib.request.urlopen(base + path, timeout=10) as r:
            return r.status, r.read()

    def t_index_served() -> None:
        status, body = get("/")
        assert status == 200 and b"GrokTOPUS" in body

    def t_health() -> None:
        status, body = get("/api/health")
        assert status == 200 and json.loads(body)["ok"] is True

    def t_telemetry_shape() -> None:
        status, body = get("/api/telemetry")
        data = json.loads(body)
        assert status == 200 and len(data["agents"]) == 8

    def t_unknown_api_is_404() -> None:
        try:
            get("/api/nope")
        except urllib.error.HTTPError as exc:
            assert exc.code == 404
            return
        raise AssertionError("unknown endpoint did not 404")

    def t_traversal_refused() -> None:
        try:
            get("/../core/risk_guard.py")
        except urllib.error.HTTPError as exc:
            assert exc.code in (403, 404), exc.code
            return
        raise AssertionError("path traversal was served")

    def t_public_bind_refused() -> None:
        try:
            serve(hub, host="0.0.0.0", port=0)
        except ValueError:
            return
        raise AssertionError("a public bind was allowed without allow_public")

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    httpd.shutdown()
    httpd.server_close()
    hub.stop()
    print(f"web_ui_server self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GrokTOPUS web dashboard")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT)
    ap.add_argument("--interval", type=float, default=0.35, help="seconds between swarm cycles")
    ap.add_argument("--seed", type=int, default=20260904)
    ap.add_argument("--live", action="store_true", help="use llama-server for the eight brains")
    ap.add_argument("--no-browser", action="store_true", help="do not open a browser window")
    ap.add_argument("--allow-public", action="store_true", help="permit a non-loopback bind")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    if not STATIC_DIR.is_dir():
        print(f"static directory missing: {STATIC_DIR}", file=sys.stderr)
        return 2

    swarm = build_swarm(seed=args.seed, live=args.live, n_steps=200_000)
    hub = TelemetryHub(swarm, interval_s=args.interval)
    try:
        httpd = serve(hub, host=args.host, port=args.port, allow_public=args.allow_public)
    except OSError as exc:
        print(f"cannot bind {args.host}:{args.port}: {exc}", file=sys.stderr)
        hub.stop()
        return 2

    url = f"http://{args.host}:{httpd.server_address[1]}"
    print("=" * 72)
    print("  GROKTOPUS DASHBOARD")
    print(f"  {url}")
    print(f"  brain: {getattr(swarm.brain, '__name__', type(swarm.brain).__name__)}"
          f"   cycle interval: {args.interval:g}s   read-only: yes")
    print("  Ctrl+C to stop")
    print("=" * 72)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping...")
    finally:
        httpd.shutdown()
        httpd.server_close()
        hub.stop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
