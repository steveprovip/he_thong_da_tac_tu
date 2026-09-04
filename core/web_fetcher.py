#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
web_fetcher.py - HTTP retrieval + text extraction behind a swappable interface.

Why this is stdlib and not Firecrawl
------------------------------------
The directive specified a self-hosted "GroktoCrawl" Firecrawl replacement in
Docker on port 3002 with Qdrant behind it. Nothing of the sort exists on this
machine - no directory, no config, and port 3002 refuses connections. Rather
than build against an absent service, retrieval goes through the `WebFetcher`
protocol below. `StdlibFetcher` implements it with urllib and a regex-based
extractor; `FirecrawlFetcher` implements the same protocol against a real
Firecrawl API if one is ever stood up. Swapping is a constructor change, and no
agent code moves.

Safety properties, which are the actual reason this file is careful
------------------------------------------------------------------
Agents choose URLs. That makes fetching an untrusted-input problem, so:

  * **Scheme allowlist.** http and https only. No file://, no ftp://, no
    data: - an agent must not be able to read the local disk through the
    "web" tool.
  * **SSRF blocking.** Loopback, RFC1918, link-local, and cloud metadata
    addresses (169.254.169.254) are refused. Hostnames are resolved and every
    returned address is checked, so `evil.test` pointing at 127.0.0.1 is caught.
  * **Redirect re-validation.** Each hop is re-checked, because a public URL
    that 302s to 169.254.169.254 would otherwise bypass the check entirely.
  * **Response size cap.** Reads stop at max_bytes. A 4 GB response must not
    become a 4 GB string on a machine with an 18 GB RAM ceiling.
  * **robots.txt respected** by default, with an explicit opt-out.
  * **Fetched text is data, never instructions.** Extracted content carries a
    provenance header and LocalLLMClient places it in an evidence block. Prompt
    injection from a scraped page is a real risk and the framing is deliberate.

CLI:
    python core/web_fetcher.py --selftest
    python core/web_fetcher.py --url https://example.com
"""

from __future__ import annotations

import argparse
import html as html_mod
import ipaddress
import re
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import urllib.robotparser
from dataclasses import dataclass, field, asdict
from typing import Any, Callable, Optional, Protocol, runtime_checkable

__all__ = [
    "FetchResult",
    "FetchError",
    "BlockedURLError",
    "WebFetcher",
    "StdlibFetcher",
    "FirecrawlFetcher",
    "extract_text",
    "is_url_allowed",
]

_DEFAULT_UA = "he-thong-da-tac-tu/1.0 (local research agent; +https://localhost)"
_ALLOWED_SCHEMES = ("http", "https")
_MAX_BYTES_DEFAULT = 2 * 1024 * 1024  # 2 MiB of HTML is already a lot of text
_CLOUD_METADATA = {"169.254.169.254", "metadata.google.internal", "100.100.100.200"}


class FetchError(RuntimeError):
    """A fetch failed for a network or protocol reason."""


class BlockedURLError(FetchError):
    """A URL was refused by policy before any request was made."""


@dataclass
class FetchResult:
    """One retrieved document."""

    url: str
    final_url: str
    status: int
    title: str
    text: str
    content_type: str = ""
    bytes_read: int = 0
    truncated: bool = False
    elapsed_s: float = 0.0
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and 200 <= self.status < 300

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)

    def as_context_block(self, max_chars: int = 4000) -> str:
        """Render for a prompt, with provenance attached.

        The source URL rides along so the Researcher persona can cite it and so a
        reader can tell retrieved text from model output.
        """
        body = self.text[:max_chars]
        if len(self.text) > max_chars:
            body += f"\n[...truncated at {max_chars} chars]"
        return f"SOURCE: {self.final_url}\nTITLE: {self.title}\n\n{body}"


# ---------------------------------------------------------------------------
# URL policy
# ---------------------------------------------------------------------------


def _ip_is_blocked(ip: str) -> bool:
    """True if an address must not be reachable by an agent-chosen URL."""
    try:
        addr = ipaddress.ip_address(ip)
    except ValueError:
        return True  # unparseable is not proven safe
    return bool(
        addr.is_private
        or addr.is_loopback
        or addr.is_link_local
        or addr.is_reserved
        or addr.is_multicast
        or addr.is_unspecified
        or str(addr) in _CLOUD_METADATA
    )


def is_url_allowed(
    url: str,
    *,
    resolve: bool = True,
    resolver: Optional[Callable[[str], list[str]]] = None,
) -> tuple[bool, str]:
    """Check a URL against the scheme allowlist and SSRF rules.

    Returns (allowed, reason). `resolver` is injectable so the rules can be
    tested without DNS.
    """
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError as exc:
        return False, f"unparseable URL: {exc}"

    if parts.scheme.lower() not in _ALLOWED_SCHEMES:
        return False, (
            f"scheme {parts.scheme!r} not allowed; only {', '.join(_ALLOWED_SCHEMES)}. "
            f"This blocks file:// and data: reads through the web tool."
        )
    host = parts.hostname
    if not host:
        return False, "URL has no host"

    if host.lower() in _CLOUD_METADATA:
        return False, f"host {host!r} is a cloud metadata endpoint"

    # A literal IP needs no DNS.
    try:
        ipaddress.ip_address(host)
        if _ip_is_blocked(host):
            return False, f"address {host} is private, loopback, or link-local"
        return True, "ok"
    except ValueError:
        pass

    if not resolve:
        return True, "ok (not resolved)"

    try:
        if resolver is not None:
            addrs = resolver(host)
        else:
            addrs = [ai[4][0] for ai in socket.getaddrinfo(host, None)]
    except Exception as exc:  # noqa: BLE001
        return False, f"DNS resolution failed for {host!r}: {exc}"

    if not addrs:
        return False, f"no addresses for {host!r}"
    for a in addrs:
        if _ip_is_blocked(a):
            return False, (
                f"host {host!r} resolves to {a}, which is private, loopback, or "
                f"link-local (SSRF guard)"
            )
    return True, "ok"


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Re-runs the URL policy on every redirect hop.

    Without this, a permitted public URL could 302 into the private network and
    the SSRF check would have been decorative.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # type: ignore[no-untyped-def]
        allowed, reason = is_url_allowed(newurl)
        if not allowed:
            raise BlockedURLError(f"redirect to {newurl} refused: {reason}")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# ---------------------------------------------------------------------------
# HTML -> text
# ---------------------------------------------------------------------------

_RE_SCRIPT_STYLE = re.compile(
    r"<(script|style|noscript|template|svg)\b[^>]*>.*?</\1\s*>", re.I | re.S
)
_RE_COMMENT = re.compile(r"<!--.*?-->", re.S)
_RE_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)
_RE_BLOCK = re.compile(
    r"</?(p|div|br|li|tr|h[1-6]|section|article|header|footer|blockquote|pre)\b[^>]*>", re.I
)
_RE_TAG = re.compile(r"<[^>]+>")
_RE_WS_RUN = re.compile(r"[ \t\x0b\f\r]+")
_RE_BLANKS = re.compile(r"\n{3,}")


def extract_text(html: str) -> tuple[str, str]:
    """Return (title, text) from an HTML document.

    A regex extractor, deliberately: it has no dependency, cannot execute
    anything, and is adequate for feeding a language model. If extraction quality
    becomes the bottleneck, swap in trafilatura behind this same function - the
    signature is the contract.
    """
    if not html:
        return "", ""

    title = ""
    m = _RE_TITLE.search(html)
    if m:
        title = html_mod.unescape(_RE_TAG.sub("", m.group(1))).strip()

    body = _RE_COMMENT.sub(" ", html)
    body = _RE_SCRIPT_STYLE.sub(" ", body)
    body = _RE_BLOCK.sub("\n", body)
    body = _RE_TAG.sub(" ", body)
    body = html_mod.unescape(body)
    body = _RE_WS_RUN.sub(" ", body)
    body = "\n".join(line.strip() for line in body.split("\n"))
    body = _RE_BLANKS.sub("\n\n", body).strip()
    return title, body


# ---------------------------------------------------------------------------
# The interface
# ---------------------------------------------------------------------------


@runtime_checkable
class WebFetcher(Protocol):
    """What the Researcher persona depends on. Keep this surface small."""

    def fetch(self, url: str) -> FetchResult: ...

    def fetch_many(self, urls: list[str]) -> list[FetchResult]: ...


class StdlibFetcher:
    """urllib + regex extraction. No third-party packages, no Docker."""

    def __init__(
        self,
        *,
        user_agent: str = _DEFAULT_UA,
        timeout_s: float = 20.0,
        max_bytes: int = _MAX_BYTES_DEFAULT,
        respect_robots: bool = True,
        delay_s: float = 0.5,
        opener: Optional[Any] = None,
        resolver: Optional[Callable[[str], list[str]]] = None,
    ) -> None:
        self.user_agent = user_agent
        self.timeout_s = timeout_s
        self.max_bytes = max_bytes
        self.respect_robots = respect_robots
        self.delay_s = delay_s
        # Injectable so the URL policy is testable without live DNS. In
        # production this stays None and socket.getaddrinfo is used.
        self.resolver = resolver
        self._robots: dict[str, Optional[urllib.robotparser.RobotFileParser]] = {}
        self._last_fetch: dict[str, float] = {}
        self._opener = opener or urllib.request.build_opener(_ValidatingRedirectHandler())

    # -- robots ------------------------------------------------------------

    def _robots_for(self, url: str) -> Optional[urllib.robotparser.RobotFileParser]:
        parts = urllib.parse.urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        if origin in self._robots:
            return self._robots[origin]
        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(f"{origin}/robots.txt")
        try:
            rp.read()
        except Exception:
            # No robots.txt, or unreachable: treat as unrestricted, which is the
            # conventional reading of a missing file.
            self._robots[origin] = None
            return None
        self._robots[origin] = rp
        return rp

    def _robots_allows(self, url: str) -> tuple[bool, str]:
        if not self.respect_robots:
            return True, "robots check disabled"
        rp = self._robots_for(url)
        if rp is None:
            return True, "no robots.txt"
        try:
            if rp.can_fetch(self.user_agent, url):
                return True, "robots allows"
            return False, "robots.txt disallows this path for our user-agent"
        except Exception:
            return True, "robots.txt unparseable; treating as unrestricted"

    def _throttle(self, url: str) -> None:
        """One request per `delay_s` per host. Politeness, not rate limiting."""
        host = urllib.parse.urlsplit(url).netloc
        last = self._last_fetch.get(host)
        now = time.monotonic()
        if last is not None:
            wait = self.delay_s - (now - last)
            if wait > 0:
                time.sleep(wait)
        self._last_fetch[host] = time.monotonic()

    # -- fetch -------------------------------------------------------------

    def fetch(self, url: str) -> FetchResult:
        """Retrieve one URL. Returns a FetchResult with `error` set on failure.

        Policy violations raise BlockedURLError; transport failures are captured
        in the result so one bad URL does not abort a batch.
        """
        started = time.monotonic()
        allowed, reason = is_url_allowed(url, resolver=self.resolver)
        if not allowed:
            raise BlockedURLError(f"refused {url}: {reason}")

        ok_robots, robots_reason = self._robots_allows(url)
        if not ok_robots:
            return FetchResult(
                url=url,
                final_url=url,
                status=0,
                title="",
                text="",
                error=f"blocked by robots.txt: {robots_reason}",
                elapsed_s=round(time.monotonic() - started, 3),
            )

        self._throttle(url)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml,text/plain;q=0.9,*/*;q=0.5",
                "Accept-Language": "en,vi;q=0.8",
            },
            method="GET",
        )
        try:
            with self._opener.open(req, timeout=self.timeout_s) as resp:
                content_type = resp.headers.get("Content-Type", "") or ""
                raw = resp.read(self.max_bytes + 1)
                truncated = len(raw) > self.max_bytes
                if truncated:
                    raw = raw[: self.max_bytes]
                charset = "utf-8"
                if "charset=" in content_type.lower():
                    charset = content_type.lower().split("charset=", 1)[1].split(";")[0].strip()
                try:
                    body = raw.decode(charset, errors="replace")
                except LookupError:
                    body = raw.decode("utf-8", errors="replace")
                final_url = resp.geturl()
                status = getattr(resp, "status", 200) or 200
        except BlockedURLError:
            raise
        except urllib.error.HTTPError as exc:
            return FetchResult(
                url=url,
                final_url=url,
                status=exc.code,
                title="",
                text="",
                error=f"HTTP {exc.code}: {exc.reason}",
                elapsed_s=round(time.monotonic() - started, 3),
            )
        except Exception as exc:  # noqa: BLE001 - one bad URL must not kill a batch
            return FetchResult(
                url=url,
                final_url=url,
                status=0,
                title="",
                text="",
                error=f"{type(exc).__name__}: {exc}",
                elapsed_s=round(time.monotonic() - started, 3),
            )

        if "html" in content_type.lower() or body.lstrip()[:1] == "<":
            title, text = extract_text(body)
        else:
            title, text = "", body

        return FetchResult(
            url=url,
            final_url=final_url,
            status=status,
            title=title,
            text=text,
            content_type=content_type,
            bytes_read=len(raw),
            truncated=truncated,
            elapsed_s=round(time.monotonic() - started, 3),
        )

    def fetch_many(self, urls: list[str]) -> list[FetchResult]:
        """Fetch a list, collecting failures rather than raising."""
        out: list[FetchResult] = []
        for u in urls:
            try:
                out.append(self.fetch(u))
            except BlockedURLError as exc:
                out.append(
                    FetchResult(
                        url=u, final_url=u, status=0, title="", text="", error=str(exc)
                    )
                )
        return out


class FirecrawlFetcher:
    """Same interface, against a real Firecrawl-compatible API.

    Unused today: no such service exists on this machine and port 3002 is closed.
    It is here so that standing one up later is a constructor swap in the
    Researcher agent, not a rewrite. `available()` tells you whether to bother.
    """

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:3002",
        *,
        api_key: Optional[str] = None,
        timeout_s: float = 60.0,
        transport: Optional[Callable[[str, dict[str, Any], float], dict[str, Any]]] = None,
        resolver: Optional[Callable[[str], list[str]]] = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_s = timeout_s
        self._transport = transport or self._http_post
        self.resolver = resolver

    def available(self) -> bool:
        """True if something answers. Never raises."""
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=3) as resp:
                return 200 <= resp.status < 300
        except Exception:
            return False

    def _http_post(self, path: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        import json as _json

        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=_json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return _json.loads(resp.read().decode("utf-8"))

    def fetch(self, url: str) -> FetchResult:
        allowed, reason = is_url_allowed(url, resolver=self.resolver)
        if not allowed:
            raise BlockedURLError(f"refused {url}: {reason}")
        started = time.monotonic()
        try:
            raw = self._transport(
                "/v2/scrape", {"url": url, "formats": ["markdown"]}, self.timeout_s
            )
        except Exception as exc:  # noqa: BLE001
            return FetchResult(
                url=url,
                final_url=url,
                status=0,
                title="",
                text="",
                error=f"firecrawl unreachable: {type(exc).__name__}: {exc}",
                elapsed_s=round(time.monotonic() - started, 3),
            )
        data = raw.get("data", raw) or {}
        meta = data.get("metadata", {}) or {}
        text = data.get("markdown") or data.get("content") or ""
        return FetchResult(
            url=url,
            final_url=meta.get("sourceURL", url),
            status=int(meta.get("statusCode", 200) or 200),
            title=meta.get("title", "") or "",
            text=text,
            content_type="text/markdown",
            bytes_read=len(text.encode("utf-8")),
            elapsed_s=round(time.monotonic() - started, 3),
        )

    def fetch_many(self, urls: list[str]) -> list[FetchResult]:
        out: list[FetchResult] = []
        for u in urls:
            try:
                out.append(self.fetch(u))
            except BlockedURLError as exc:
                out.append(
                    FetchResult(url=u, final_url=u, status=0, title="", text="", error=str(exc))
                )
        return out


# ---------------------------------------------------------------------------
# Self-test. No network access required.
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

    print("web_fetcher.py selftest")
    print("-" * 60)

    public = lambda host: ["93.184.216.34"]  # noqa: E731 - test resolver

    # --- URL policy ---

    def t_https_public_allowed() -> None:
        ok, why = is_url_allowed("https://example.com/page", resolver=public)
        assert ok, why

    def t_file_scheme_blocked() -> None:
        ok, why = is_url_allowed("file:///C:/Windows/win.ini")
        assert not ok and "scheme" in why, why

    def t_data_scheme_blocked() -> None:
        ok, _ = is_url_allowed("data:text/html,<h1>x</h1>")
        assert not ok

    def t_ftp_blocked() -> None:
        ok, _ = is_url_allowed("ftp://example.com/f")
        assert not ok

    def t_loopback_literal_blocked() -> None:
        ok, why = is_url_allowed("http://127.0.0.1:8080/admin")
        assert not ok and "loopback" in why.lower(), why

    def t_localhost_name_blocked() -> None:
        ok, _ = is_url_allowed("http://localhost:3002/v2/scrape", resolver=lambda h: ["127.0.0.1"])
        assert not ok

    def t_rfc1918_blocked() -> None:
        for ip in ("10.0.0.5", "192.168.1.1", "172.16.0.9"):
            ok, _ = is_url_allowed(f"http://{ip}/")
            assert not ok, ip

    def t_link_local_blocked() -> None:
        ok, _ = is_url_allowed("http://169.254.169.254/latest/meta-data/")
        assert not ok, "cloud metadata endpoint must be refused"

    def t_dns_rebind_to_private_blocked() -> None:
        # Public-looking name that resolves inward.
        ok, why = is_url_allowed("http://evil.test/", resolver=lambda h: ["127.0.0.1"])
        assert not ok and "ssrf" in why.lower(), why

    def t_mixed_resolution_blocked() -> None:
        # If ANY address is private, refuse - not just the first.
        ok, _ = is_url_allowed(
            "http://mixed.test/", resolver=lambda h: ["93.184.216.34", "10.1.2.3"]
        )
        assert not ok

    def t_no_host_blocked() -> None:
        ok, _ = is_url_allowed("http://")
        assert not ok

    def t_dns_failure_blocked() -> None:
        def boom(host: str) -> list[str]:
            raise socket.gaierror("nope")

        ok, why = is_url_allowed("http://nx.test/", resolver=boom)
        assert not ok and "DNS" in why, why

    def t_ipv6_loopback_blocked() -> None:
        ok, _ = is_url_allowed("http://[::1]/")
        assert not ok

    def t_skip_resolve_still_checks_scheme() -> None:
        ok, _ = is_url_allowed("file:///etc/passwd", resolve=False)
        assert not ok

    # --- extraction ---

    def t_extract_title_and_text() -> None:
        title, text = extract_text(
            "<html><head><title>Hello</title></head><body><p>One</p><p>Two</p></body></html>"
        )
        assert title == "Hello", title
        assert "One" in text and "Two" in text, text

    def t_script_and_style_stripped() -> None:
        _, text = extract_text(
            "<body><script>var evil=1;</script><style>.a{color:red}</style><p>Keep</p></body>"
        )
        assert "evil" not in text and "color" not in text, text
        assert "Keep" in text

    def t_entities_unescaped() -> None:
        _, text = extract_text("<p>a &amp; b &lt;tag&gt; &quot;q&quot;</p>")
        assert "a & b" in text and "<tag>" in text, text

    def t_comments_removed() -> None:
        _, text = extract_text("<body><!-- secret --><p>visible</p></body>")
        assert "secret" not in text and "visible" in text

    def t_block_tags_become_newlines() -> None:
        _, text = extract_text("<body><p>a</p><p>b</p></body>")
        assert "\n" in text, repr(text)

    def t_empty_html_safe() -> None:
        assert extract_text("") == ("", "")

    def t_no_title_ok() -> None:
        title, text = extract_text("<body><p>x</p></body>")
        assert title == "" and "x" in text

    # --- fetcher behaviour with a fake opener ---

    class FakeResponse:
        def __init__(self, body: bytes, ctype: str = "text/html; charset=utf-8", url: str = "https://example.com", status: int = 200) -> None:
            self._body = body
            self.headers = {"Content-Type": ctype}
            self._url = url
            self.status = status
            self._pos = 0

        def read(self, n: int = -1) -> bytes:
            if n is None or n < 0:
                out, self._pos = self._body[self._pos:], len(self._body)
                return out
            out = self._body[self._pos : self._pos + n]
            self._pos += len(out)
            return out

        def geturl(self) -> str:
            return self._url

        def __enter__(self) -> "FakeResponse":
            return self

        def __exit__(self, *exc: object) -> None:
            return None

    class FakeOpener:
        def __init__(self, response: Any) -> None:
            self.response = response
            self.calls: list[str] = []

        def open(self, req: Any, timeout: float = 0) -> Any:
            self.calls.append(req.full_url if hasattr(req, "full_url") else str(req))
            if isinstance(self.response, Exception):
                raise self.response
            return self.response

    def _fetcher(response: Any, **kw: Any) -> StdlibFetcher:
        # `resolver=public` because this sandbox has no DNS; without it every
        # fetch test would fail on name resolution rather than on its subject.
        return StdlibFetcher(
            opener=FakeOpener(response),
            respect_robots=False,
            delay_s=0.0,
            resolver=public,
            **kw,
        )

    def t_fetch_returns_text() -> None:
        f = _fetcher(FakeResponse(b"<title>T</title><body><p>Body here</p></body>"))
        r = f.fetch("https://example.com/")
        assert r.ok, r.error
        assert r.title == "T", r.title
        assert "Body here" in r.text

    def t_fetch_blocks_private_before_request() -> None:
        opener = FakeOpener(FakeResponse(b"should never be read"))
        f = StdlibFetcher(
            opener=opener, respect_robots=False, delay_s=0.0, resolver=public
        )
        try:
            f.fetch("http://127.0.0.1/")
            raise AssertionError("expected BlockedURLError")
        except BlockedURLError:
            pass
        assert not opener.calls, "must refuse before opening a connection"

    def t_size_cap_truncates() -> None:
        big = b"<body>" + b"x" * 10_000 + b"</body>"
        f = _fetcher(FakeResponse(big), max_bytes=1000)
        r = f.fetch("https://example.com/")
        assert r.truncated, "must flag truncation"
        assert r.bytes_read <= 1000, r.bytes_read

    def t_http_error_captured_not_raised() -> None:
        err = urllib.error.HTTPError("https://example.com", 404, "Not Found", {}, None)  # type: ignore[arg-type]
        f = _fetcher(err)
        r = f.fetch("https://example.com/")
        assert not r.ok
        assert r.status == 404, r.status
        assert r.error and "404" in r.error

    def t_transport_error_captured() -> None:
        f = _fetcher(urllib.error.URLError("connection refused"))
        r = f.fetch("https://example.com/")
        assert not r.ok and r.error

    def t_fetch_many_collects_failures() -> None:
        f = _fetcher(FakeResponse(b"<body>ok</body>"))
        # Second URL is a loopback literal: refused by policy, no DNS involved.
        results = f.fetch_many(["https://example.com/a", "http://127.0.0.1/b"])
        assert len(results) == 2, results
        assert results[0].ok
        assert not results[1].ok and results[1].error, "blocked URL becomes a failed result"

    def t_non_html_passed_through() -> None:
        f = _fetcher(FakeResponse(b"plain data", ctype="text/plain"))
        r = f.fetch("https://example.com/f.txt")
        assert r.text == "plain data", r.text

    def t_context_block_has_provenance() -> None:
        f = _fetcher(FakeResponse(b"<title>T</title><body>content</body>"))
        block = f.fetch("https://example.com/").as_context_block()
        assert "SOURCE: " in block and "example.com" in block

    def t_context_block_respects_max() -> None:
        f = _fetcher(FakeResponse(b"<body>" + b"y" * 5000 + b"</body>"))
        block = f.fetch("https://example.com/").as_context_block(max_chars=100)
        assert "truncated" in block
        assert len(block) < 400, len(block)

    def t_protocol_conformance() -> None:
        assert isinstance(StdlibFetcher(), WebFetcher)
        assert isinstance(FirecrawlFetcher(), WebFetcher)

    def t_firecrawl_unavailable_is_graceful() -> None:
        fc = FirecrawlFetcher("http://127.0.0.1:3002")
        assert fc.available() is False, "nothing listens on 3002; must report False"

    def t_firecrawl_parses_shape() -> None:
        def fake(path: str, payload: dict[str, Any], t: float) -> dict[str, Any]:
            return {
                "data": {
                    "markdown": "# Heading\n\nbody",
                    "metadata": {
                        "title": "Doc",
                        "sourceURL": "https://example.com/x",
                        "statusCode": 200,
                    },
                }
            }

        fc = FirecrawlFetcher(transport=fake, resolver=public)
        r = fc.fetch("https://example.com/x")
        assert r.ok and r.title == "Doc", r.as_dict()
        assert "Heading" in r.text

    def t_firecrawl_also_blocks_private() -> None:
        fc = FirecrawlFetcher(transport=lambda *a: {}, resolver=public)
        try:
            fc.fetch("http://10.0.0.1/")
            raise AssertionError("expected BlockedURLError")
        except BlockedURLError:
            pass

    for name, fn in [
        ("public https allowed", t_https_public_allowed),
        ("file:// blocked", t_file_scheme_blocked),
        ("data: blocked", t_data_scheme_blocked),
        ("ftp:// blocked", t_ftp_blocked),
        ("loopback literal blocked", t_loopback_literal_blocked),
        ("localhost name blocked", t_localhost_name_blocked),
        ("RFC1918 ranges blocked", t_rfc1918_blocked),
        ("cloud metadata blocked", t_link_local_blocked),
        ("DNS rebind to private blocked", t_dns_rebind_to_private_blocked),
        ("any private address blocks", t_mixed_resolution_blocked),
        ("URL without host blocked", t_no_host_blocked),
        ("DNS failure blocked", t_dns_failure_blocked),
        ("IPv6 loopback blocked", t_ipv6_loopback_blocked),
        ("scheme checked without resolving", t_skip_resolve_still_checks_scheme),
        ("title and text extracted", t_extract_title_and_text),
        ("script and style stripped", t_script_and_style_stripped),
        ("HTML entities unescaped", t_entities_unescaped),
        ("comments removed", t_comments_removed),
        ("block tags become newlines", t_block_tags_become_newlines),
        ("empty HTML safe", t_empty_html_safe),
        ("missing title tolerated", t_no_title_ok),
        ("fetch returns extracted text", t_fetch_returns_text),
        ("private URL refused pre-connect", t_fetch_blocks_private_before_request),
        ("response size cap enforced", t_size_cap_truncates),
        ("HTTP error captured not raised", t_http_error_captured_not_raised),
        ("transport error captured", t_transport_error_captured),
        ("fetch_many collects failures", t_fetch_many_collects_failures),
        ("non-HTML passed through", t_non_html_passed_through),
        ("context block carries source URL", t_context_block_has_provenance),
        ("context block respects max chars", t_context_block_respects_max),
        ("both fetchers satisfy protocol", t_protocol_conformance),
        ("firecrawl absence is graceful", t_firecrawl_unavailable_is_graceful),
        ("firecrawl response parsed", t_firecrawl_parses_shape),
        ("firecrawl enforces SSRF guard", t_firecrawl_also_blocks_private),
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
    ap = argparse.ArgumentParser(description="Web fetcher with SSRF guards")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--url", default=None, help="fetch one URL and print extracted text")
    ap.add_argument("--max-chars", type=int, default=2000)
    ap.add_argument("--no-robots", action="store_true", help="ignore robots.txt")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.url:
        f = StdlibFetcher(respect_robots=not args.no_robots)
        try:
            r = f.fetch(args.url)
        except BlockedURLError as exc:
            print(f"BLOCKED: {exc}", file=sys.stderr)
            return 2
        if not r.ok:
            print(f"FAILED: {r.error}", file=sys.stderr)
            return 1
        print(r.as_context_block(max_chars=args.max_chars))
        return 0
    ap.print_help()
    return 0


if __name__ == "__main__":
    sys.exit(main())
