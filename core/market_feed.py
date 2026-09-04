#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
market_feed.py - read-only public market data, and nothing else.

What this module can do: ask a public ticker endpoint what something last traded
at, over plain HTTPS GET, with no credentials.

What this module cannot do, structurally rather than by policy:

* **Authenticate.** There is no API key, no secret, no HMAC signing, no nonce, no
  passphrase header - and `tests/test_safety_compliance.py` greps this file to
  keep it that way. Every endpoint below is documented as public and returns the
  same data to an anonymous caller as to a logged-in one.
* **Place, amend or cancel an order.** Only `GET` is ever issued - `_http_get`
  is the single network call in the module and it has no body parameter. There is
  no order type, no side field, no quantity field, no account id.
* **Move money.** It returns integers. Something else decides what to do with
  them, and in this system that something is a paper ledger.

Why it is fail-closed rather than best-effort
---------------------------------------------
A stale price is worse than no price. If the network is down, the venue is
rate-limiting, or the JSON changes shape, `fetch` raises and `fresh()` goes
False; `GrokMarket` will not mark a position against a tick older than
`max_age_s`. The failure mode of a trading system reading a stale feed is to
keep trading confidently on a number that stopped being true, and that is the one
outcome worth engineering against.

Untrusted input
---------------
A price from the internet is data, not truth. `_parse_price` rejects anything
non-finite, non-positive, or absurd, and `_sanity_check` rejects a tick that
moves more than `max_jump_pct` from the previous one - a 10x print is far more
likely to be a decimal error, a wrong symbol, or an incident at the venue than a
real move, and acting on it is how a system turns someone else's bug into its own
loss.

Being a polite client
---------------------
One token-bucket limiter per venue, defaulting to 20 requests/minute against
published limits that are two to three orders of magnitude higher. HTTP 429 and
503 back off and mark the venue cold. A `User-Agent` identifies the tool. There
is no retry storm: a failed fetch is one failed fetch.

Coverage is partial and says so
-------------------------------
Only BTC, ETH and SOL have a public crypto-exchange ticker. XAU-USD, EUR-USD,
SPX-IDX, NDX-IDX and OIL-WTI have no free unauthenticated endpoint and stay
synthetic; `unsupported_symbols()` lists them rather than quietly substituting
something and hoping nobody checks.

CLI:
    python core/market_feed.py --selftest
    python core/market_feed.py --venue coinbase --symbols BTC-USD,ETH-USD
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from typing import Any, Callable, Iterable, Mapping, Optional

__all__ = [
    "FeedError",
    "VenueUnavailable",
    "RateLimited",
    "SuspiciousTick",
    "Tick",
    "RateLimiter",
    "MarketFeed",
    "VENUES",
    "USER_AGENT",
]

USER_AGENT = "GrokTOPUS-paper-sim/1.0 (read-only public market data; no trading)"

#: Public, unauthenticated ticker endpoints. `path` is formatted with the venue's
#: own product id. Nothing here takes a key, and nothing here accepts a POST.
VENUES: dict[str, dict[str, Any]] = {
    "coinbase": {
        "base": "https://api.exchange.coinbase.com",
        "path": "/products/{product}/ticker",
        "price_field": "price",
        "products": {"BTC-USD": "BTC-USD", "ETH-USD": "ETH-USD", "SOL-USD": "SOL-USD"},
        "docs": "https://docs.cdp.coinbase.com/exchange/reference/exchangerestapi_getproductticker",
        "published_rate_limit": "10 req/s per IP (public endpoints)",
    },
    "binance": {
        "base": "https://api.binance.com",
        "path": "/api/v3/ticker/price?symbol={product}",
        "price_field": "price",
        "products": {"BTC-USD": "BTCUSDT", "ETH-USD": "ETHUSDT", "SOL-USD": "SOLUSDT"},
        "docs": "https://developers.binance.com/docs/binance-spot-api-docs/rest-api",
        "published_rate_limit": "1200 request-weight/min per IP",
        "availability_note": (
            "api.binance.com is blocked in some jurisdictions and for some IP "
            "ranges. A refusal here is a geography problem, not a bug - use the "
            "coinbase venue instead."
        ),
    },
}


class FeedError(RuntimeError):
    """Base class for feed faults."""


class VenueUnavailable(FeedError):
    """The venue could not be reached, or answered with something unusable."""


class RateLimited(FeedError):
    """The local limiter, or the venue, said to slow down."""


class SuspiciousTick(FeedError):
    """A price arrived that is more likely a data error than a market move."""


@dataclass(frozen=True)
class Tick:
    """One observed price. Integer cents, because this feeds an integer ledger."""

    symbol: str
    price_cents: int
    venue: str
    received_at: float
    raw_price: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.price_cents, int) or isinstance(self.price_cents, bool):
            raise SuspiciousTick(f"{self.symbol}: price must be an int of cents")
        if self.price_cents <= 0:
            raise SuspiciousTick(f"{self.symbol}: price must be > 0, got {self.price_cents}")

    def age_s(self, now: Optional[float] = None) -> float:
        return (time.time() if now is None else now) - self.received_at

    def is_fresh(self, max_age_s: float, now: Optional[float] = None) -> bool:
        return self.age_s(now) <= max_age_s

    def as_dict(self, now: Optional[float] = None) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "price_cents": self.price_cents,
            "price": self.price_cents / 100.0,
            "venue": self.venue,
            "received_at": self.received_at,
            "age_s": round(self.age_s(now), 3),
            "raw_price": self.raw_price,
        }


class RateLimiter:
    """A token bucket. Thread-safe, and it refuses rather than sleeping.

    Refusing is the right default for a poller: a limiter that blocks turns a
    burst into a queue of stale requests that all arrive at once, whereas a
    refusal lets the caller skip a tick and try again on the next cycle with
    fresh intent.
    """

    def __init__(self, per_minute: float = 20.0, burst: Optional[float] = None) -> None:
        if per_minute <= 0:
            raise ValueError("per_minute must be > 0")
        self.per_minute = float(per_minute)
        self.capacity = float(burst if burst is not None else max(1.0, per_minute / 4.0))
        self._tokens = self.capacity
        self._last = time.monotonic()
        self._lock = threading.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._last
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.per_minute / 60.0)

    def try_acquire(self, tokens: float = 1.0) -> bool:
        with self._lock:
            self._refill()
            if self._tokens >= tokens:
                self._tokens -= tokens
                return True
            return False

    def acquire(self, tokens: float = 1.0) -> None:
        if not self.try_acquire(tokens):
            raise RateLimited(
                f"local limiter is empty ({self.per_minute:.0f}/min); skipping this poll "
                f"rather than queueing a request that would arrive stale"
            )

    @property
    def available(self) -> float:
        with self._lock:
            self._refill()
            return round(self._tokens, 3)


def _parse_price(raw: Any, *, symbol: str) -> int:
    """Venue string -> integer cents, refusing anything that is not a price."""
    if raw is None:
        raise SuspiciousTick(f"{symbol}: venue returned no price field")
    try:
        d = Decimal(str(raw).strip())
    except (InvalidOperation, ValueError) as exc:
        raise SuspiciousTick(f"{symbol}: price {raw!r} is not a number") from exc
    if not d.is_finite():
        raise SuspiciousTick(f"{symbol}: price {raw!r} is not finite")
    if d <= 0:
        raise SuspiciousTick(f"{symbol}: price {raw!r} is not positive")
    if d > Decimal("100000000"):
        raise SuspiciousTick(f"{symbol}: price {raw!r} is implausibly large")
    cents = int((d * 100).to_integral_value(rounding="ROUND_HALF_UP"))
    if cents <= 0:
        raise SuspiciousTick(f"{symbol}: price {raw!r} rounds to zero cents")
    return cents


class MarketFeed:
    """A polite, read-only, fail-closed ticker client."""

    def __init__(
        self,
        venue: str = "coinbase",
        *,
        transport: Optional[Callable[[str, float], dict[str, Any]]] = None,
        per_minute: float = 20.0,
        max_age_s: float = 30.0,
        max_jump_pct: float = 20.0,
        timeout_s: float = 8.0,
    ) -> None:
        key = str(venue).lower()
        if key not in VENUES:
            raise FeedError(f"unknown venue {venue!r}; known: {sorted(VENUES)}")
        self.venue = key
        self.spec = VENUES[key]
        self.limiter = RateLimiter(per_minute)
        self.max_age_s = float(max_age_s)
        self.max_jump_pct = float(max_jump_pct)
        self.timeout_s = float(timeout_s)
        self._transport = transport or self._http_get
        self._last: dict[str, Tick] = {}
        self._lock = threading.Lock()
        self.stats: dict[str, int] = {
            "ok": 0, "rate_limited": 0, "unavailable": 0, "suspicious": 0
        }
        self.cold_until: float = 0.0

    # -- transport ---------------------------------------------------------

    def _http_get(self, url: str, timeout_s: float) -> dict[str, Any]:
        """The only network call in this module. GET, no body, no credentials."""
        req = urllib.request.Request(
            url, method="GET", headers={"User-Agent": USER_AGENT, "Accept": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                if resp.status != 200:
                    raise VenueUnavailable(f"{url} returned HTTP {resp.status}")
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if exc.code in (429, 418):
                raise RateLimited(f"{self.venue} returned HTTP {exc.code}; backing off") from exc
            raise VenueUnavailable(f"{url} returned HTTP {exc.code}") from exc
        except urllib.error.URLError as exc:
            raise VenueUnavailable(f"cannot reach {url}: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise VenueUnavailable(f"{url} did not return JSON: {exc}") from exc

    # -- coverage ----------------------------------------------------------

    def supports(self, symbol: str) -> bool:
        return symbol in self.spec["products"]

    def supported_symbols(self) -> list[str]:
        return sorted(self.spec["products"])

    @staticmethod
    def unsupported_symbols(symbols: Iterable[str], venue: str = "coinbase") -> list[str]:
        products = VENUES[str(venue).lower()]["products"]
        return sorted(s for s in symbols if s not in products)

    def url_for(self, symbol: str) -> str:
        if not self.supports(symbol):
            raise FeedError(
                f"{self.venue} has no public ticker for {symbol}; supported: "
                f"{self.supported_symbols()}"
            )
        product = urllib.parse.quote(str(self.spec["products"][symbol]), safe="-_")
        return self.spec["base"] + self.spec["path"].format(product=product)

    # -- validation --------------------------------------------------------

    def _sanity_check(self, symbol: str, cents: int) -> None:
        prev = self._last.get(symbol)
        if prev is None:
            return
        jump = abs(cents - prev.price_cents) / prev.price_cents * 100.0
        if jump > self.max_jump_pct:
            raise SuspiciousTick(
                f"{symbol}: {prev.price_cents / 100:.2f} -> {cents / 100:.2f} is a "
                f"{jump:.1f}% move against a {self.max_jump_pct:.1f}% guard. Refusing the "
                f"tick: a print this size is more often a venue error than a market."
            )

    # -- fetching ----------------------------------------------------------

    def fetch(self, symbol: str, *, now: Optional[float] = None) -> Tick:
        """One price. Raises rather than returning something stale or wrong."""
        if time.time() < self.cold_until:
            self.stats["rate_limited"] += 1
            raise RateLimited(
                f"{self.venue} is in backoff for another "
                f"{self.cold_until - time.time():.1f}s"
            )
        url = self.url_for(symbol)
        try:
            self.limiter.acquire()
        except RateLimited:
            self.stats["rate_limited"] += 1
            raise
        try:
            payload = self._transport(url, self.timeout_s)
        except RateLimited:
            self.stats["rate_limited"] += 1
            self.cold_until = time.time() + 60.0
            raise
        except VenueUnavailable:
            self.stats["unavailable"] += 1
            raise
        except Exception as exc:  # noqa: BLE001 - any transport fault is unavailability
            self.stats["unavailable"] += 1
            raise VenueUnavailable(f"{self.venue}: {type(exc).__name__}: {exc}") from exc

        if not isinstance(payload, Mapping):
            self.stats["unavailable"] += 1
            raise VenueUnavailable(f"{self.venue} returned {type(payload).__name__}, not an object")
        try:
            cents = _parse_price(payload.get(self.spec["price_field"]), symbol=symbol)
            self._sanity_check(symbol, cents)
        except SuspiciousTick:
            self.stats["suspicious"] += 1
            raise

        tick = Tick(
            symbol=symbol,
            price_cents=cents,
            venue=self.venue,
            received_at=time.time() if now is None else now,
            raw_price=str(payload.get(self.spec["price_field"])),
        )
        with self._lock:
            self._last[symbol] = tick
        self.stats["ok"] += 1
        return tick

    def poll(self, symbols: Iterable[str]) -> dict[str, Tick]:
        """Fetch what it can. Failures are omitted, never faked."""
        out: dict[str, Tick] = {}
        for sym in symbols:
            if not self.supports(sym):
                continue
            try:
                out[sym] = self.fetch(sym)
            except FeedError:
                continue
        return out

    # -- readers -----------------------------------------------------------

    def last(self, symbol: str) -> Optional[Tick]:
        with self._lock:
            return self._last.get(symbol)

    def fresh(self, symbol: str, *, now: Optional[float] = None) -> bool:
        t = self.last(symbol)
        return bool(t and t.is_fresh(self.max_age_s, now))

    def price_cents(self, symbol: str, *, now: Optional[float] = None) -> Optional[int]:
        """The last price, or None if it is too old to act on. Fail closed."""
        t = self.last(symbol)
        if t is None or not t.is_fresh(self.max_age_s, now):
            return None
        return t.price_cents

    def status(self) -> dict[str, Any]:
        now = time.time()
        return {
            "venue": self.venue,
            "read_only": True,
            "authenticated": False,
            "docs": self.spec.get("docs"),
            "published_rate_limit": self.spec.get("published_rate_limit"),
            "local_rate_limit_per_min": self.limiter.per_minute,
            "tokens_available": self.limiter.available,
            "max_age_s": self.max_age_s,
            "max_jump_pct": self.max_jump_pct,
            "cold_for_s": round(max(0.0, self.cold_until - now), 2),
            "supported_symbols": self.supported_symbols(),
            "stats": dict(self.stats),
            "last": {s: t.as_dict(now) for s, t in self._last.items()},
        }


# ---------------------------------------------------------------------------
# Self-test and CLI
# ---------------------------------------------------------------------------


def _fake_transport(prices: dict[str, Any]) -> Callable[[str, float], dict[str, Any]]:
    """A stand-in venue keyed by product id, so tests never touch the network."""

    def transport(url: str, timeout: float) -> dict[str, Any]:
        for product, price in prices.items():
            if product in url:
                if isinstance(price, Exception):
                    raise price
                return {"price": price}
        raise VenueUnavailable(f"fake venue has no product for {url}")

    return transport


def _selftest() -> int:
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

    def feed(**kw: Any) -> MarketFeed:
        kw.setdefault("transport", _fake_transport({"BTC-USD": "64125.00"}))
        return MarketFeed("coinbase", **kw)

    def t_price_is_integer_cents() -> None:
        t = feed().fetch("BTC-USD")
        assert t.price_cents == 6_412_500 and isinstance(t.price_cents, int)

    def t_half_up_rounding() -> None:
        f = MarketFeed("coinbase", transport=_fake_transport({"BTC-USD": "1.005"}))
        assert f.fetch("BTC-USD").price_cents == 101

    def t_unsupported_symbol_refused() -> None:
        try:
            feed().fetch("XAU-USD")
        except FeedError:
            return
        raise AssertionError("an unsupported symbol was fetched")

    def t_unsupported_symbols_are_listed() -> None:
        missing = MarketFeed.unsupported_symbols(
            ["BTC-USD", "XAU-USD", "EUR-USD", "SPX-IDX", "NDX-IDX", "OIL-WTI", "ETH-USD"])
        assert missing == ["EUR-USD", "NDX-IDX", "OIL-WTI", "SPX-IDX", "XAU-USD"], missing

    def t_bad_prices_refused() -> None:
        for bad in ("0", "-5", "abc", "nan", "", None, "1e40"):
            f = MarketFeed("coinbase", transport=_fake_transport({"BTC-USD": bad}))
            try:
                f.fetch("BTC-USD")
            except SuspiciousTick:
                continue
            raise AssertionError(f"price {bad!r} was accepted")

    def t_absurd_jump_refused() -> None:
        f = feed()
        f.fetch("BTC-USD")
        f._transport = _fake_transport({"BTC-USD": "641250.00"})
        try:
            f.fetch("BTC-USD")
        except SuspiciousTick:
            assert f.last("BTC-USD").price_cents == 6_412_500, "the bad tick was stored"
            return
        raise AssertionError("a 10x print was accepted")

    def t_small_move_accepted() -> None:
        f = feed()
        f.fetch("BTC-USD")
        f._transport = _fake_transport({"BTC-USD": "64500.00"})
        assert f.fetch("BTC-USD").price_cents == 6_450_000

    def t_stale_price_is_none() -> None:
        f = MarketFeed("coinbase", transport=_fake_transport({"BTC-USD": "64125.00"}), max_age_s=10)
        f.fetch("BTC-USD")
        assert f.price_cents("BTC-USD") == 6_412_500
        assert f.price_cents("BTC-USD", now=time.time() + 60) is None
        assert not f.fresh("BTC-USD", now=time.time() + 60)

    def t_rate_limiter_refuses_a_burst() -> None:
        f = MarketFeed("coinbase", transport=_fake_transport({"BTC-USD": "64125.00"}),
                       per_minute=1.0)
        hits = 0
        for _ in range(20):
            try:
                f.fetch("BTC-USD")
                hits += 1
            except RateLimited:
                pass
        assert hits < 20, hits
        assert f.stats["rate_limited"] > 0

    def t_venue_429_triggers_backoff() -> None:
        f = MarketFeed("coinbase", transport=_fake_transport({"BTC-USD": RateLimited("429")}))
        try:
            f.fetch("BTC-USD")
        except RateLimited:
            pass
        assert f.cold_until > time.time()

    def t_unavailable_venue_raises() -> None:
        f = MarketFeed("coinbase", transport=_fake_transport({"BTC-USD": VenueUnavailable("down")}))
        try:
            f.fetch("BTC-USD")
        except VenueUnavailable:
            return
        raise AssertionError("an unreachable venue produced a tick")

    def t_poll_omits_failures_never_fakes_them() -> None:
        f = MarketFeed("coinbase", transport=_fake_transport({"BTC-USD": "64125.00"}))
        out = f.poll(["BTC-USD", "ETH-USD", "XAU-USD"])
        assert set(out) == {"BTC-USD"}, sorted(out)

    def t_url_has_no_credentials() -> None:
        url = feed().url_for("BTC-USD")
        assert url.startswith("https://")
        for token in ("key", "secret", "signature", "token", "apikey"):
            assert token not in url.lower(), url

    def t_unknown_venue_refused() -> None:
        try:
            MarketFeed("definitely-not-a-venue")
        except FeedError:
            return
        raise AssertionError("an unknown venue was accepted")

    def t_status_declares_read_only() -> None:
        s = feed().status()
        assert s["read_only"] is True and s["authenticated"] is False
        json.dumps(s)

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    print(f"market_feed self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GrokTOPUS read-only public market feed")
    ap.add_argument("--venue", default="coinbase", choices=sorted(VENUES))
    ap.add_argument("--symbols", default="BTC-USD,ETH-USD,SOL-USD")
    ap.add_argument("--per-minute", type=float, default=20.0)
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    symbols = [s.strip() for s in args.symbols.split(",") if s.strip()]
    feed = MarketFeed(args.venue, per_minute=args.per_minute)
    missing = MarketFeed.unsupported_symbols(symbols, args.venue)
    ticks = feed.poll(symbols)

    if args.json:
        print(json.dumps({"status": feed.status(),
                          "ticks": {k: v.as_dict() for k, v in ticks.items()},
                          "unsupported": missing}, indent=2))
        return 0 if ticks else 1

    print("=" * 70)
    print(f" PUBLIC MARKET FEED - {args.venue}  (read-only, unauthenticated)")
    print(f" {feed.spec.get('docs')}")
    print("=" * 70)
    for sym in symbols:
        t = ticks.get(sym)
        if t:
            print(f"  {sym:<10} {t.price_cents / 100:>14,.2f}   age {t.age_s():.2f}s")
        elif sym in missing:
            print(f"  {sym:<10} {'-':>14}   no public ticker on this venue")
        else:
            print(f"  {sym:<10} {'-':>14}   fetch failed (see stats)")
    print("-" * 70)
    print(f"  stats: {feed.stats}   limiter: {feed.limiter.available:.1f} tokens left")
    if missing:
        print(f"  synthetic only: {', '.join(missing)}")
    print("=" * 70)
    return 0 if ticks else 1


if __name__ == "__main__":
    raise SystemExit(main())
