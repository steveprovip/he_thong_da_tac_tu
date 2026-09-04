#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
live_execution_router.py - the only component that can send an order off this machine.

This file was rewritten because the previous version had three defects that
would each, on their own, lose money silently. They are worth naming, because
the fixes are the design:

1. **The exchange-side stop-loss did not exist.** `stop_loss_order_id` was set to
   the string `f"STOP_{cid}"` and the transmit path sent exactly one request -
   the entry. The documented headline feature, "if your PC loses power the stop
   still protects you", was a label with nothing behind it. Now `_place_bracket`
   sends a second, separate `STOP_MARKET closePosition=true` request, checks the
   response, and **flattens the entry immediately if the stop cannot be placed**.
   An unprotected position is not an acceptable state to return from a function.

2. **Failures were recorded as fills.** `except Exception: pass` sat under the
   HTTP call, and the record was built with `status=FILLED` before transmission
   either way. A timeout - the exact case where the exchange may have accepted
   the order - produced a local ledger saying FILLED with a fabricated order id.
   Now a transport fault produces `OrderStatus.UNKNOWN` and raises
   `OrderUnknown`; the ledger row says `needs_reconcile`, and `reconcile()`
   queries the venue by `newClientOrderId` to find out what actually happened.

3. **MAINNET pointed at the testnet URL and read testnet keys.** One base
   constant, one pair of env vars, used by every mode. Now each venue carries its
   own URL and its own environment variable names, and there is no path by which
   a mainnet call reaches a testnet endpoint or vice versa.

Two further gates that were absent entirely
-------------------------------------------
**Consensus.** The project's central rule is that only VESKA may open a position
and only against a single-use token from `ConsensusEngine`. The old router had an
`auth_token_signature` field that nothing ever read. `execute` now consumes the
token through the engine, which fails on a forged, spent, expired or mismatched
token - so an order that no vote approved cannot be sent.

**The kill switch.** `RiskGuard.killed` is checked before anything leaves the
process.

Why MAINNET is gated on a measured edge
---------------------------------------
`MAINNET` refuses to initialise without `bench/edge_report.json` showing
`has_edge: true`. This is not a moral position about trading; it is the same
argument as the stop-loss. Position sizing needs an estimate of edge and variance
- Kelly is a ratio of exactly those two - and a router that cannot state its own
out-of-sample t-statistic has no basis on which to choose a size. As of the last
measurement the report says `has_edge: false` at -8.03 bps/trade over 575
out-of-sample trades, so this gate is currently closed. `GROKTOPUS_ALLOW_MAINNET=1`
and a separate key pair are required on top of it.

Money is integers
-----------------
Prices are integer cents. Quantities are integer base units at a per-symbol
scale, converted to the wire format with `Decimal` at the boundary and nowhere
else. The previous version used `float` throughout, which the project's own hard
constraints forbid.

CLI:
    python live/live_execution_router.py --selftest
    python live/live_execution_router.py --preflight --mode TESTNET
    python live/live_execution_router.py --preflight --mode MAINNET
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
for _sub in ("core", "simulation", "benchmarks"):
    _p = str(REPO_ROOT / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from consensus_engine import ConsensusEngine, Proposal, TokenError  # noqa: E402
from edge_report import load_report  # noqa: E402
from risk_guard import RiskGuard  # noqa: E402

__all__ = [
    "ExecutionMode",
    "OrderStatus",
    "RouterError",
    "MainnetRefused",
    "UnauthorisedOrder",
    "StopLossNotPlaced",
    "OrderUnknown",
    "DuplicateOrder",
    "LimitBreached",
    "VenueSpec",
    "RouterLimits",
    "OrderRequest",
    "OrderRecord",
    "LiveExecutionRouter",
    "VENUES",
    "ALLOW_MAINNET_ENV",
]

ALLOW_MAINNET_ENV = "GROKTOPUS_ALLOW_MAINNET"


class ExecutionMode(str, Enum):
    SIMULATION = "SIMULATION"
    TESTNET = "TESTNET"
    MAINNET = "MAINNET"


class OrderStatus(str, Enum):
    NEW = "NEW"
    SUBMITTED = "SUBMITTED"
    FILLED = "FILLED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
    #: The order left this process and its fate is not known. Never treated as a
    #: fill, never treated as a miss - it is the state that requires reconcile().
    UNKNOWN = "UNKNOWN"


class RouterError(RuntimeError):
    """Base class for routing faults."""


class MainnetRefused(RouterError):
    """MAINNET was requested without everything it requires."""


class UnauthorisedOrder(RouterError):
    """No valid consensus token, or the kill switch is tripped."""


class StopLossNotPlaced(RouterError):
    """The protective stop could not be placed; the entry was flattened."""


class OrderUnknown(RouterError):
    """The venue's response was not received. The position may or may not exist."""


class DuplicateOrder(RouterError):
    """This client order id has been used before."""


class LimitBreached(RouterError):
    """A router-level size or loss limit would be exceeded."""


@dataclass(frozen=True)
class VenueSpec:
    """One venue, with its own URL and its own credentials.

    Separate `key_env` per mode is the whole point: there is no expression in
    this module that can hand a testnet key to a mainnet endpoint, because they
    are never read from the same place.
    """

    name: str
    base_url: str
    key_env: str
    secret_env: str
    order_path: str = "/fapi/v1/order"
    time_path: str = "/fapi/v1/time"
    recv_window_ms: int = 5_000
    docs: str = "https://developers.binance.com/docs/derivatives/usds-margined-futures"


VENUES: dict[ExecutionMode, VenueSpec] = {
    ExecutionMode.TESTNET: VenueSpec(
        name="binance-futures-testnet",
        base_url="https://testnet.binancefuture.com",
        key_env="BINANCE_TESTNET_API_KEY",
        secret_env="BINANCE_TESTNET_SECRET_KEY",
    ),
    ExecutionMode.MAINNET: VenueSpec(
        name="binance-futures-mainnet",
        base_url="https://fapi.binance.com",
        key_env="BINANCE_MAINNET_API_KEY",
        secret_env="BINANCE_MAINNET_SECRET_KEY",
    ),
}


@dataclass
class RouterLimits:
    """Hard bounds the router enforces regardless of what the swarm asks for.

    These are not risk management - `RiskGuard` and RUNE are - they are the
    backstop for the case where risk management is the thing that is broken.
    """

    max_order_notional_cents: int = 5_000_00       # $5,000 per order
    max_session_loss_cents: int = 100_00           # $100 per session
    max_open_positions: int = 3
    mandatory_stop_bps: float = 150.0              # 1.5%
    max_stop_bps: float = 1_000.0

    def __post_init__(self) -> None:
        for name in ("max_order_notional_cents", "max_session_loss_cents", "max_open_positions"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise RouterError(f"{name} must be a positive int, got {v!r}")
        if not 0.0 < self.mandatory_stop_bps <= self.max_stop_bps:
            raise RouterError("need 0 < mandatory_stop_bps <= max_stop_bps")


@dataclass(frozen=True)
class OrderRequest:
    """What VESKA is asking for. Integers only."""

    symbol: str
    side: str                       # "BUY" | "SELL"
    quantity_units: int             # integer base units at `qty_scale_pow10`
    qty_scale_pow10: int = 6        # 10^-6 of the base asset
    price_cents: Optional[int] = None
    stop_bps: float = 150.0
    client_order_id: Optional[str] = None

    def __post_init__(self) -> None:
        if self.side not in ("BUY", "SELL"):
            raise RouterError(f"side must be BUY or SELL, got {self.side!r}")
        if not isinstance(self.quantity_units, int) or isinstance(self.quantity_units, bool):
            raise RouterError("quantity_units must be an int of base units")
        if self.quantity_units <= 0:
            raise RouterError("quantity_units must be > 0")
        if self.price_cents is not None:
            if not isinstance(self.price_cents, int) or isinstance(self.price_cents, bool):
                raise RouterError("price_cents must be an int of cents")
            if self.price_cents <= 0:
                raise RouterError("price_cents must be > 0")
        if self.qty_scale_pow10 < 0 or self.qty_scale_pow10 > 12:
            raise RouterError("qty_scale_pow10 must lie in [0, 12]")

    @property
    def quantity_decimal(self) -> Decimal:
        """Wire quantity. The single place an integer becomes a decimal string."""
        return Decimal(self.quantity_units).scaleb(-self.qty_scale_pow10)

    @property
    def opposite_side(self) -> str:
        return "SELL" if self.side == "BUY" else "BUY"


@dataclass
class OrderRecord:
    """What happened, including when that is not known."""

    client_order_id: str
    symbol: str
    side: str
    quantity_units: int
    qty_scale_pow10: int
    fill_price_cents: int
    status: OrderStatus
    mode: str
    exchange_order_id: Optional[str] = None
    stop_order_id: Optional[str] = None
    stop_price_cents: Optional[int] = None
    fee_cents: int = 0
    needs_reconcile: bool = False
    created_at: float = field(default_factory=time.time)
    detail: str = ""

    @property
    def notional_cents(self) -> int:
        return int(
            (Decimal(self.quantity_units).scaleb(-self.qty_scale_pow10)
             * Decimal(self.fill_price_cents)).to_integral_value(rounding="ROUND_HALF_UP")
        )

    @property
    def protected(self) -> bool:
        """A filled position with a live stop on the venue."""
        return self.status is OrderStatus.FILLED and bool(self.stop_order_id)

    def as_dict(self) -> dict[str, Any]:
        return {
            "client_order_id": self.client_order_id,
            "symbol": self.symbol,
            "side": self.side,
            "quantity_units": self.quantity_units,
            "qty_scale_pow10": self.qty_scale_pow10,
            "quantity": str(Decimal(self.quantity_units).scaleb(-self.qty_scale_pow10)),
            "fill_price_cents": self.fill_price_cents,
            "notional_cents": self.notional_cents,
            "status": self.status.value,
            "mode": self.mode,
            "exchange_order_id": self.exchange_order_id,
            "stop_order_id": self.stop_order_id,
            "stop_price_cents": self.stop_price_cents,
            "protected": self.protected,
            "fee_cents": self.fee_cents,
            "needs_reconcile": self.needs_reconcile,
            "created_at": self.created_at,
            "detail": self.detail,
        }


class LiveExecutionRouter:
    """Routes VESKA's approved orders to a paper book, a testnet, or a real venue."""

    def __init__(
        self,
        mode: ExecutionMode = ExecutionMode.SIMULATION,
        *,
        authoriser: Optional[ConsensusEngine] = None,
        guard: Optional[RiskGuard] = None,
        limits: Optional[RouterLimits] = None,
        transport: Optional[Callable[[str, str, dict[str, str], float], dict[str, Any]]] = None,
        db_path: Optional[Path] = None,
        edge_report_path: Optional[Path] = None,
        env: Optional[dict[str, str]] = None,
        timeout_s: float = 10.0,
    ) -> None:
        self.mode = ExecutionMode(mode)
        self.authoriser = authoriser
        self.guard = guard
        self.limits = limits or RouterLimits()
        self.timeout_s = float(timeout_s)
        self._env = dict(os.environ if env is None else env)
        self._edge_report_path = edge_report_path
        self.venue: Optional[VenueSpec] = VENUES.get(self.mode)
        self.db_path = Path(db_path or (REPO_ROOT / "state" / "live_trade_ledger.db"))
        self._transport = transport or self._http_request
        self._time_offset_ms = 0
        self.session_realised_cents = 0
        self.stats: dict[str, int] = {
            "submitted": 0, "filled": 0, "rejected": 0, "unknown": 0,
            "flattened_no_stop": 0, "refused": 0,
        }

        blockers = self.mainnet_blockers() if self.mode is ExecutionMode.MAINNET else []
        if blockers:
            self.stats["refused"] += 1
            raise MainnetRefused(
                "MAINNET refused. Unmet requirements:\n  - " + "\n  - ".join(blockers)
            )
        self._init_db()

    # -- credentials -------------------------------------------------------

    @property
    def api_key(self) -> str:
        return self._env.get(self.venue.key_env, "") if self.venue else ""

    @property
    def secret_key(self) -> str:
        return self._env.get(self.venue.secret_env, "") if self.venue else ""

    @property
    def has_credentials(self) -> bool:
        return bool(self.api_key and self.secret_key)

    # -- the mainnet gate --------------------------------------------------

    def mainnet_blockers(self) -> list[str]:
        """Everything standing between this configuration and a real order.

        Returned as a list rather than a bool so the operator is told all of it
        at once. Nothing here is advisory - `__init__` raises on a non-empty list.
        """
        out: list[str] = []
        venue = VENUES[ExecutionMode.MAINNET]

        if self._env.get(ALLOW_MAINNET_ENV) != "1":
            out.append(f"{ALLOW_MAINNET_ENV}=1 is not set in the environment")
        if not self._env.get(venue.key_env):
            out.append(f"{venue.key_env} is not set (testnet keys are never reused here)")
        if not self._env.get(venue.secret_env):
            out.append(f"{venue.secret_env} is not set")

        report = load_report(self._edge_report_path)
        if report is None:
            out.append(
                "no out-of-sample edge report found - run "
                "`python benchmarks/edge_report.py --folds 6 --cycles 900 --write`"
            )
        elif not report.get("has_edge"):
            out.append(
                f"the edge report says has_edge=false: {report.get('verdict', 'no verdict')}. "
                f"Position sizing needs an estimate of edge and variance; without one "
                f"there is no basis for choosing a size."
            )

        if self.guard is not None and self.guard.killed:
            out.append("the Sentry kill switch is tripped")
        return out

    def preflight(self) -> dict[str, Any]:
        """What would happen if an order were submitted right now. Sends nothing."""
        report = load_report(self._edge_report_path)
        return {
            "mode": self.mode.value,
            "venue": self.venue.name if self.venue else "paper (in-process)",
            "base_url": self.venue.base_url if self.venue else None,
            "credentials_present": self.has_credentials,
            "key_env": self.venue.key_env if self.venue else None,
            "kill_switch_tripped": bool(self.guard is not None and self.guard.killed),
            "consensus_required": self.authoriser is not None,
            "edge_report_found": report is not None,
            "edge_report_has_edge": bool(report and report.get("has_edge")),
            "edge_report_verdict": (report or {}).get("verdict"),
            "mainnet_blockers": (
                self.mainnet_blockers() if self.mode is ExecutionMode.MAINNET
                else self._hypothetical_mainnet_blockers()
            ),
            "limits": {
                "max_order_notional_cents": self.limits.max_order_notional_cents,
                "max_session_loss_cents": self.limits.max_session_loss_cents,
                "max_open_positions": self.limits.max_open_positions,
                "mandatory_stop_bps": self.limits.mandatory_stop_bps,
            },
            "session_realised_cents": self.session_realised_cents,
            "stats": dict(self.stats),
        }

    def _hypothetical_mainnet_blockers(self) -> list[str]:
        saved, self.mode = self.mode, ExecutionMode.MAINNET
        try:
            return self.mainnet_blockers()
        finally:
            self.mode = saved

    # -- ledger ------------------------------------------------------------

    def _init_db(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS live_orders (
                    client_order_id  TEXT PRIMARY KEY,
                    exchange_order_id TEXT,
                    mode             TEXT NOT NULL,
                    symbol           TEXT NOT NULL,
                    side             TEXT NOT NULL,
                    quantity_units   INTEGER NOT NULL,
                    qty_scale_pow10  INTEGER NOT NULL,
                    fill_price_cents INTEGER NOT NULL,
                    status           TEXT NOT NULL,
                    stop_order_id    TEXT,
                    stop_price_cents INTEGER,
                    fee_cents        INTEGER NOT NULL DEFAULT 0,
                    needs_reconcile  INTEGER NOT NULL DEFAULT 0,
                    detail           TEXT,
                    created_at       REAL NOT NULL
                );
                """
            )
            conn.commit()

    def _seen(self, client_order_id: str) -> bool:
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT 1 FROM live_orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
        return row is not None

    def _persist(self, rec: OrderRecord) -> None:
        """Write the record. INSERT OR REPLACE only for a row this call created.

        Idempotency is enforced by `_seen` before submission, not by letting a
        second write silently overwrite the first - an order id that appears
        twice is a bug worth surfacing, not a row to update.
        """
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO live_orders (
                    client_order_id, exchange_order_id, mode, symbol, side,
                    quantity_units, qty_scale_pow10, fill_price_cents, status,
                    stop_order_id, stop_price_cents, fee_cents, needs_reconcile,
                    detail, created_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?);
                """,
                (
                    rec.client_order_id, rec.exchange_order_id, rec.mode, rec.symbol,
                    rec.side, rec.quantity_units, rec.qty_scale_pow10,
                    rec.fill_price_cents, rec.status.value, rec.stop_order_id,
                    rec.stop_price_cents, rec.fee_cents, int(rec.needs_reconcile),
                    rec.detail, rec.created_at,
                ),
            )
            conn.commit()

    def open_orders(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM live_orders WHERE status IN ('FILLED','SUBMITTED','UNKNOWN')"
            ).fetchall()
        return [dict(r) for r in rows]

    def unreconciled(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute("SELECT * FROM live_orders WHERE needs_reconcile = 1").fetchall()
        return [dict(r) for r in rows]

    # -- ids ---------------------------------------------------------------

    @staticmethod
    def new_client_order_id(agent: str = "VESKA") -> str:
        """`GROK_<agent>_<ms>_<8 hex>`. Unique, and it names who asked."""
        return f"GROK_{agent}_{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"

    # -- transport ---------------------------------------------------------

    def _http_request(
        self, method: str, url: str, headers: dict[str, str], timeout_s: float
    ) -> dict[str, Any]:
        req = urllib.request.Request(url, method=method)
        for k, v in headers.items():
            req.add_header(k, v)
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:400]
            # An HTTP error IS a response: the venue answered and said no.
            # That is a rejection, not an unknown, and the two must not be
            # conflated - a rejection means no position exists.
            raise RouterError(f"venue rejected: HTTP {exc.code}: {body}") from exc
        except (urllib.error.URLError, TimeoutError, OSError, json.JSONDecodeError) as exc:
            # No answer. The order may or may not be live at the venue.
            raise OrderUnknown(f"no response from venue: {type(exc).__name__}: {exc}") from exc

    def _sync_time(self) -> int:
        """Offset between this machine's clock and the venue's, in milliseconds.

        Binance rejects a request whose timestamp is outside `recvWindow`, and
        Windows clock drift of a few seconds is ordinary. Syncing once per router
        and reusing the offset turns an intermittent, confusing rejection into a
        non-event.
        """
        if not self.venue:
            return 0
        try:
            data = self._transport(
                "GET", f"{self.venue.base_url}{self.venue.time_path}", {}, self.timeout_s
            )
            server_ms = int(data.get("serverTime", 0))
            if server_ms:
                self._time_offset_ms = server_ms - int(time.time() * 1000)
        except (RouterError, OrderUnknown, ValueError, TypeError):
            self._time_offset_ms = 0
        return self._time_offset_ms

    def _signed(self, params: dict[str, Any]) -> str:
        """HMAC-SHA256 over the exact query string that will be sent."""
        if not self.venue:
            raise RouterError("no venue in SIMULATION mode")
        payload = dict(params)
        payload["timestamp"] = int(time.time() * 1000) + self._time_offset_ms
        payload["recvWindow"] = self.venue.recv_window_ms
        query = urllib.parse.urlencode(payload)
        sig = hmac.new(
            self.secret_key.encode("utf-8"), query.encode("utf-8"), hashlib.sha256
        ).hexdigest()
        return f"{query}&signature={sig}"

    def _post_order(self, params: dict[str, Any]) -> dict[str, Any]:
        if not self.venue:
            raise RouterError("no venue in SIMULATION mode")
        if not self.has_credentials:
            raise UnauthorisedOrder(
                f"{self.mode.value} needs {self.venue.key_env} and {self.venue.secret_env}"
            )
        url = f"{self.venue.base_url}{self.venue.order_path}?{self._signed(params)}"
        return self._transport("POST", url, {"X-MBX-APIKEY": self.api_key}, self.timeout_s)

    # -- the gate ----------------------------------------------------------

    def _authorise(self, proposal: Optional[Proposal], token: Optional[str]) -> None:
        """Kill switch, then consensus. Both refuse; neither warns."""
        if self.guard is not None and self.guard.killed:
            self.stats["refused"] += 1
            raise UnauthorisedOrder("the Sentry kill switch is tripped; no order may be sent")
        if self.authoriser is None:
            return
        if proposal is None or not token:
            self.stats["refused"] += 1
            raise UnauthorisedOrder(
                "this router has a consensus authoriser attached, so an order needs the "
                "proposal it was voted on and the single-use token that vote minted"
            )
        try:
            self.authoriser.consume(token, proposal)
        except TokenError as exc:
            self.stats["refused"] += 1
            raise UnauthorisedOrder(f"VESKA refused the order: {exc}") from exc

    def _check_limits(self, req: OrderRequest, fill_price_cents: int) -> None:
        notional = int(
            (req.quantity_decimal * Decimal(fill_price_cents)).to_integral_value(
                rounding="ROUND_HALF_UP")
        )
        if notional > self.limits.max_order_notional_cents:
            self.stats["refused"] += 1
            raise LimitBreached(
                f"order notional {notional} cents exceeds the router cap of "
                f"{self.limits.max_order_notional_cents} cents"
            )
        if -self.session_realised_cents >= self.limits.max_session_loss_cents:
            self.stats["refused"] += 1
            raise LimitBreached(
                f"session loss {-self.session_realised_cents} cents has reached the "
                f"{self.limits.max_session_loss_cents} cent stop-for-the-day"
            )
        live = [o for o in self.open_orders() if o["status"] == "FILLED"]
        if len(live) >= self.limits.max_open_positions:
            self.stats["refused"] += 1
            raise LimitBreached(
                f"{len(live)} positions already open, cap is {self.limits.max_open_positions}"
            )
        if not 0.0 < req.stop_bps <= self.limits.max_stop_bps:
            raise LimitBreached(
                f"stop_bps must lie in (0, {self.limits.max_stop_bps}], got {req.stop_bps}"
            )

    @staticmethod
    def _stop_price_cents(side: str, fill_cents: int, stop_bps: float) -> int:
        move = int(round(fill_cents * stop_bps / 10_000.0))
        return max(1, fill_cents - move) if side == "BUY" else fill_cents + move

    # -- execution ---------------------------------------------------------

    def execute(
        self,
        req: OrderRequest,
        *,
        spot_price_cents: int,
        proposal: Optional[Proposal] = None,
        token: Optional[str] = None,
        agent: str = "VESKA",
    ) -> OrderRecord:
        """Open a protected position, or leave nothing behind.

        The three outcomes are FILLED-and-protected, a raised exception with no
        position, or `OrderUnknown` with a row flagged for reconciliation. There
        is deliberately no fourth outcome in which a position exists and the
        caller is told everything is fine.
        """
        if not isinstance(spot_price_cents, int) or isinstance(spot_price_cents, bool):
            raise RouterError("spot_price_cents must be an int of cents")
        if spot_price_cents <= 0:
            raise RouterError("spot_price_cents must be > 0")

        self._authorise(proposal, token)

        cid = req.client_order_id or self.new_client_order_id(agent)
        if self._seen(cid):
            raise DuplicateOrder(
                f"client order id {cid} is already in the ledger; refusing to resend. "
                f"Use reconcile() to find out what happened to it."
            )

        fill = req.price_cents or spot_price_cents
        self._check_limits(req, fill)
        stop_cents = self._stop_price_cents(req.side, fill, req.stop_bps)

        rec = OrderRecord(
            client_order_id=cid,
            symbol=req.symbol,
            side=req.side,
            quantity_units=req.quantity_units,
            qty_scale_pow10=req.qty_scale_pow10,
            fill_price_cents=fill,
            status=OrderStatus.NEW,
            mode=self.mode.value,
            stop_price_cents=stop_cents,
        )

        if self.mode is ExecutionMode.SIMULATION:
            # The paper path still books a stop id, because a record that is
            # shaped differently from the live one is a record that hides bugs
            # until the day they cost money.
            rec.status = OrderStatus.FILLED
            rec.exchange_order_id = f"SIM-{cid}"
            rec.stop_order_id = f"SIM-STOP-{cid}"
            rec.fee_cents = int(round(rec.notional_cents * 4 / 10_000))
            rec.detail = "paper fill; nothing left this process"
            self.stats["submitted"] += 1
            self.stats["filled"] += 1
            self._persist(rec)
            return rec

        self._sync_time()
        wire_symbol = req.symbol.replace("-", "").replace("/", "").replace("USD", "USDT") \
            if req.symbol.endswith("-USD") else req.symbol.replace("-", "").replace("/", "")
        qty = format(req.quantity_decimal.normalize(), "f")

        # --- entry -------------------------------------------------------
        rec.status = OrderStatus.SUBMITTED
        self.stats["submitted"] += 1
        self._persist(rec)
        try:
            resp = self._post_order({
                "symbol": wire_symbol,
                "side": req.side,
                "type": "MARKET",
                "quantity": qty,
                "newClientOrderId": cid,
            })
        except OrderUnknown as exc:
            rec.status = OrderStatus.UNKNOWN
            rec.needs_reconcile = True
            rec.detail = str(exc)
            self.stats["unknown"] += 1
            self._persist(rec)
            raise OrderUnknown(
                f"{cid}: the entry order left this process and no reply came back. "
                f"A position may exist and it has NO stop. Run reconcile({cid!r}) "
                f"before doing anything else."
            ) from exc
        except RouterError as exc:
            rec.status = OrderStatus.REJECTED
            rec.detail = str(exc)
            self.stats["rejected"] += 1
            self._persist(rec)
            raise

        rec.exchange_order_id = str(resp.get("orderId") or resp.get("clientOrderId") or cid)
        venue_status = str(resp.get("status", "")).upper()
        if venue_status in ("REJECTED", "EXPIRED", "CANCELED", "CANCELLED"):
            rec.status = OrderStatus.REJECTED
            rec.detail = f"venue status {venue_status}"
            self.stats["rejected"] += 1
            self._persist(rec)
            raise RouterError(f"{cid}: venue returned status {venue_status}")

        avg = resp.get("avgPrice") or resp.get("price")
        if avg:
            try:
                got = int((Decimal(str(avg)) * 100).to_integral_value(rounding="ROUND_HALF_UP"))
                if got > 0:
                    rec.fill_price_cents = got
                    rec.stop_price_cents = self._stop_price_cents(req.side, got, req.stop_bps)
            except (ValueError, ArithmeticError):
                pass
        rec.status = OrderStatus.FILLED
        self.stats["filled"] += 1
        self._persist(rec)

        # --- the protective stop, as a real second order ------------------
        try:
            self._place_bracket(rec, req, wire_symbol)
        except (RouterError, OrderUnknown) as exc:
            flat = self._flatten(rec, req, wire_symbol)
            self.stats["flattened_no_stop"] += 1
            rec.detail = f"stop placement failed ({exc}); entry flattened: {flat}"
            rec.status = OrderStatus.CANCELLED if "flattened" in flat else OrderStatus.UNKNOWN
            rec.needs_reconcile = rec.status is OrderStatus.UNKNOWN
            self._persist(rec)
            raise StopLossNotPlaced(
                f"{cid}: could not place the protective stop ({exc}). "
                f"{flat} An unprotected position is not a state this router returns."
            ) from exc

        self._persist(rec)
        return rec

    def _place_bracket(self, rec: OrderRecord, req: OrderRequest, wire_symbol: str) -> None:
        """STOP_MARKET, closePosition, opposite side. This is the safety feature.

        `closePosition=true` means the venue closes whatever is open when the
        stop triggers, so it stays correct even if the position was partially
        filled or later adjusted. It survives this machine losing power, which
        is the only reason a stop belongs at the venue rather than in a loop here.
        """
        stop_cid = f"STOP_{rec.client_order_id}"[:36]
        resp = self._post_order({
            "symbol": wire_symbol,
            "side": req.opposite_side,
            "type": "STOP_MARKET",
            "stopPrice": format(Decimal(rec.stop_price_cents or 0) / 100, "f"),
            "closePosition": "true",
            "workingType": "MARK_PRICE",
            "newClientOrderId": stop_cid,
        })
        status = str(resp.get("status", "")).upper()
        if status in ("REJECTED", "EXPIRED"):
            raise RouterError(f"venue rejected the stop: status {status}")
        oid = resp.get("orderId") or resp.get("clientOrderId")
        if not oid:
            raise RouterError(f"venue accepted the stop but returned no order id: {resp}")
        rec.stop_order_id = str(oid)

    def _flatten(self, rec: OrderRecord, req: OrderRequest, wire_symbol: str) -> str:
        """Close the entry immediately. Returns a human description of the result."""
        try:
            self._post_order({
                "symbol": wire_symbol,
                "side": req.opposite_side,
                "type": "MARKET",
                "quantity": format(req.quantity_decimal.normalize(), "f"),
                "reduceOnly": "true",
                "newClientOrderId": f"FLAT_{rec.client_order_id}"[:36],
            })
            return "The entry was flattened."
        except (RouterError, OrderUnknown) as exc:
            return (
                f"The flatten attempt ALSO failed ({exc}). A position may be open "
                f"with no stop - check the venue by hand now."
            )

    # -- reconciliation ----------------------------------------------------

    def reconcile(self, client_order_id: str) -> OrderRecord:
        """Ask the venue what actually happened to an order we lost track of."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM live_orders WHERE client_order_id = ?", (client_order_id,)
            ).fetchone()
        if row is None:
            raise RouterError(f"no ledger row for {client_order_id!r}")
        rec = OrderRecord(
            client_order_id=row["client_order_id"], symbol=row["symbol"], side=row["side"],
            quantity_units=row["quantity_units"], qty_scale_pow10=row["qty_scale_pow10"],
            fill_price_cents=row["fill_price_cents"], status=OrderStatus(row["status"]),
            mode=row["mode"], exchange_order_id=row["exchange_order_id"],
            stop_order_id=row["stop_order_id"], stop_price_cents=row["stop_price_cents"],
            fee_cents=row["fee_cents"], needs_reconcile=bool(row["needs_reconcile"]),
            created_at=row["created_at"], detail=row["detail"] or "",
        )
        if self.mode is ExecutionMode.SIMULATION or not self.venue:
            rec.needs_reconcile = False
            rec.detail = "simulation: nothing to reconcile"
            self._persist(rec)
            return rec

        self._sync_time()
        wire_symbol = rec.symbol.replace("-", "").replace("/", "")
        url = f"{self.venue.base_url}{self.venue.order_path}?" + self._signed(
            {"symbol": wire_symbol, "origClientOrderId": rec.client_order_id}
        )
        try:
            resp = self._transport("GET", url, {"X-MBX-APIKEY": self.api_key}, self.timeout_s)
        except RouterError as exc:
            # The venue answered "unknown order" - it never existed there.
            rec.status = OrderStatus.REJECTED
            rec.needs_reconcile = False
            rec.detail = f"venue has no such order: {exc}"
            self._persist(rec)
            return rec
        except OrderUnknown:
            rec.detail = "reconcile could not reach the venue; still unknown"
            self._persist(rec)
            return rec

        status = str(resp.get("status", "")).upper()
        rec.exchange_order_id = str(resp.get("orderId") or rec.exchange_order_id or "")
        if status in ("FILLED", "PARTIALLY_FILLED"):
            rec.status = OrderStatus.FILLED
            rec.needs_reconcile = False
            rec.detail = (
                f"reconciled: venue reports {status}. This position has NO stop attached - "
                f"place one or flatten it now."
            )
        elif status in ("CANCELED", "CANCELLED", "EXPIRED", "REJECTED"):
            rec.status = OrderStatus.REJECTED
            rec.needs_reconcile = False
            rec.detail = f"reconciled: venue reports {status}; no position exists"
        else:
            rec.status = OrderStatus.SUBMITTED
            rec.detail = f"reconciled: venue reports {status}"
        self._persist(rec)
        return rec

    def record_realised_cents(self, delta_cents: int) -> int:
        """Book a closed trade's result against the session loss limit."""
        if not isinstance(delta_cents, int) or isinstance(delta_cents, bool):
            raise RouterError("delta_cents must be an int")
        self.session_realised_cents += delta_cents
        return self.session_realised_cents

    def status(self) -> dict[str, Any]:
        return {
            **self.preflight(),
            "open_positions": len([o for o in self.open_orders() if o["status"] == "FILLED"]),
            "unreconciled": len(self.unreconciled()),
            "db_path": str(self.db_path),
        }


# ---------------------------------------------------------------------------
# Self-test and CLI
# ---------------------------------------------------------------------------


def _fake_venue(
    *, entry: Optional[dict[str, Any]] = None, stop: Optional[Any] = None,
    flat: Optional[Any] = None, seen: Optional[list[str]] = None,
) -> Callable[[str, str, dict[str, str], float], dict[str, Any]]:
    """A stand-in exchange. `stop`/`flat` may be an Exception to raise."""

    def transport(method: str, url: str, headers: dict[str, str], timeout: float) -> dict[str, Any]:
        if seen is not None:
            seen.append(url)
        if "/time" in url:
            return {"serverTime": int(time.time() * 1000)}
        if "STOP_MARKET" in url:
            if isinstance(stop, Exception):
                raise stop
            return stop or {"orderId": 777, "status": "NEW"}
        if "FLAT_" in url:
            if isinstance(flat, Exception):
                raise flat
            return flat or {"orderId": 888, "status": "FILLED"}
        if isinstance(entry, Exception):
            raise entry
        return entry or {"orderId": 999, "status": "FILLED", "avgPrice": "64125.00"}

    return transport


def _selftest() -> int:
    import tempfile  # noqa: PLC0415

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

    def req(**kw: Any) -> OrderRequest:
        base = dict(symbol="BTC-USDT", side="BUY", quantity_units=1_000, qty_scale_pow10=6)
        base.update(kw)
        return OrderRequest(**base)  # type: ignore[arg-type]

    def router(mode: ExecutionMode = ExecutionMode.SIMULATION, **kw: Any) -> LiveExecutionRouter:
        td = tempfile.mkdtemp()
        kw.setdefault("db_path", Path(td) / "live.db")
        kw.setdefault("edge_report_path", Path(td) / "nope.json")
        return LiveExecutionRouter(mode, **kw)

    def t_money_is_integers() -> None:
        r = router().execute(req(), spot_price_cents=6_412_500)
        for v in (r.fill_price_cents, r.stop_price_cents, r.notional_cents, r.fee_cents):
            assert isinstance(v, int) and not isinstance(v, bool), v

    def t_float_quantity_refused() -> None:
        try:
            OrderRequest(symbol="BTC-USDT", side="BUY", quantity_units=0.5)  # type: ignore[arg-type]
        except RouterError:
            return
        raise AssertionError("a float quantity was accepted")

    def t_long_stop_is_below_entry() -> None:
        r = router().execute(req(), spot_price_cents=6_412_500)
        assert r.stop_price_cents < r.fill_price_cents

    def t_short_stop_is_above_entry() -> None:
        r = router().execute(req(side="SELL"), spot_price_cents=6_412_500)
        assert r.stop_price_cents > r.fill_price_cents

    def t_mainnet_refused_with_nothing_set() -> None:
        try:
            router(ExecutionMode.MAINNET, env={})
        except MainnetRefused as exc:
            assert "GROKTOPUS_ALLOW_MAINNET" in str(exc)
            assert "edge report" in str(exc)
            return
        raise AssertionError("MAINNET started with no gates satisfied")

    def t_mainnet_refused_without_an_edge() -> None:
        td = Path(tempfile.mkdtemp())
        (td / "edge.json").write_text(json.dumps({
            "schema": "groktopus.edge_report/1", "has_edge": False,
            "verdict": "NO EDGE: -8.02 bps",
        }), encoding="utf-8")
        try:
            LiveExecutionRouter(
                ExecutionMode.MAINNET, db_path=td / "l.db", edge_report_path=td / "edge.json",
                env={ALLOW_MAINNET_ENV: "1", "BINANCE_MAINNET_API_KEY": "k",
                     "BINANCE_MAINNET_SECRET_KEY": "s"},
            )
        except MainnetRefused as exc:
            assert "has_edge=false" in str(exc)
            return
        raise AssertionError("MAINNET started with has_edge=false")

    def t_mainnet_opens_when_every_gate_is_met() -> None:
        td = Path(tempfile.mkdtemp())
        (td / "edge.json").write_text(json.dumps({
            "schema": "groktopus.edge_report/1", "has_edge": True, "verdict": "EDGE FOUND",
        }), encoding="utf-8")
        r = LiveExecutionRouter(
            ExecutionMode.MAINNET, db_path=td / "l.db", edge_report_path=td / "edge.json",
            env={ALLOW_MAINNET_ENV: "1", "BINANCE_MAINNET_API_KEY": "k",
                 "BINANCE_MAINNET_SECRET_KEY": "s"},
            transport=_fake_venue(),
        )
        assert r.venue.base_url == "https://fapi.binance.com"

    def t_mainnet_never_reads_testnet_keys() -> None:
        assert VENUES[ExecutionMode.MAINNET].key_env != VENUES[ExecutionMode.TESTNET].key_env
        assert VENUES[ExecutionMode.MAINNET].base_url != VENUES[ExecutionMode.TESTNET].base_url

    def t_kill_switch_blocks_everything() -> None:
        td = Path(tempfile.mkdtemp())
        from risk_guard import RiskGuardConfig  # noqa: PLC0415

        g = RiskGuard(RiskGuardConfig(db_path=td / "g.db", kill_file=td / "K",
                                      allow_unmeasurable_hardware=True))
        g.trip_kill_switch("selftest")
        r = LiveExecutionRouter(ExecutionMode.SIMULATION, db_path=td / "l.db", guard=g)
        try:
            r.execute(req(), spot_price_cents=6_412_500)
        except UnauthorisedOrder:
            g.close()
            return
        g.close()
        raise AssertionError("a tripped kill switch did not stop the router")

    def t_consensus_required_when_attached() -> None:
        from consensus_engine import AgentRegistry  # noqa: PLC0415

        r = router(authoriser=ConsensusEngine(AgentRegistry()))
        try:
            r.execute(req(), spot_price_cents=6_412_500)
        except UnauthorisedOrder:
            return
        raise AssertionError("an order was sent with no consensus token")

    def t_duplicate_client_id_refused() -> None:
        r = router()
        cid = r.new_client_order_id()
        r.execute(req(client_order_id=cid), spot_price_cents=6_412_500)
        try:
            r.execute(req(client_order_id=cid), spot_price_cents=6_412_500)
        except DuplicateOrder:
            return
        raise AssertionError("the same client order id was sent twice")

    def t_notional_cap_enforced() -> None:
        r = router(limits=RouterLimits(max_order_notional_cents=1_00))
        try:
            r.execute(req(), spot_price_cents=6_412_500)
        except LimitBreached:
            return
        raise AssertionError("the notional cap was not enforced")

    def t_testnet_places_a_real_stop() -> None:
        seen: list[str] = []
        r = router(ExecutionMode.TESTNET, transport=_fake_venue(seen=seen),
                   env={"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"})
        rec = r.execute(req(), spot_price_cents=6_412_500)
        assert rec.protected, rec.as_dict()
        assert rec.stop_order_id == "777"
        assert any("STOP_MARKET" in u for u in seen), "no stop order was ever sent"
        assert any("closePosition" in u for u in seen)

    def t_stop_failure_flattens_and_raises() -> None:
        seen: list[str] = []
        r = router(ExecutionMode.TESTNET,
                   transport=_fake_venue(stop=RouterError("margin"), seen=seen),
                   env={"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"})
        try:
            r.execute(req(), spot_price_cents=6_412_500)
        except StopLossNotPlaced as exc:
            assert "flattened" in str(exc).lower()
            assert any("FLAT_" in u for u in seen), "the entry was not flattened"
            return
        raise AssertionError("an unprotected position was returned as success")

    def t_timeout_is_unknown_not_filled() -> None:
        r = router(ExecutionMode.TESTNET,
                   transport=_fake_venue(entry=OrderUnknown("timeout")),
                   env={"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"})
        try:
            r.execute(req(), spot_price_cents=6_412_500)
        except OrderUnknown:
            rows = r.unreconciled()
            assert rows and rows[0]["status"] == "UNKNOWN", rows
            return
        raise AssertionError("a lost response was recorded as a fill")

    def t_venue_rejection_is_not_unknown() -> None:
        r = router(ExecutionMode.TESTNET,
                   transport=_fake_venue(entry={"status": "REJECTED"}),
                   env={"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"})
        try:
            r.execute(req(), spot_price_cents=6_412_500)
        except RouterError:
            assert r.unreconciled() == [], "a clean rejection was marked for reconcile"
            return
        raise AssertionError("a venue rejection was treated as a fill")

    def t_missing_credentials_refused() -> None:
        r = router(ExecutionMode.TESTNET, transport=_fake_venue(), env={})
        try:
            r.execute(req(), spot_price_cents=6_412_500)
        except UnauthorisedOrder:
            return
        raise AssertionError("an order was attempted with no API key")

    def t_signature_covers_the_query() -> None:
        r = router(ExecutionMode.TESTNET, transport=_fake_venue(),
                   env={"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"})
        q = r._signed({"symbol": "BTCUSDT"})
        body, sig = q.rsplit("&signature=", 1)
        expect = hmac.new(b"s", body.encode(), hashlib.sha256).hexdigest()
        assert sig == expect
        assert "recvWindow" in body and "timestamp" in body

    def t_preflight_sends_nothing() -> None:
        seen: list[str] = []
        r = router(ExecutionMode.TESTNET, transport=_fake_venue(seen=seen),
                   env={"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"})
        p = r.preflight()
        assert seen == [], "preflight touched the network"
        assert p["mode"] == "TESTNET"
        json.dumps(p)

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    print(f"live_execution_router self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GrokTOPUS live execution router")
    ap.add_argument("--mode", default="SIMULATION", choices=[m.value for m in ExecutionMode])
    ap.add_argument("--preflight", action="store_true", help="report what would happen; sends nothing")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    mode = ExecutionMode(args.mode)
    try:
        router = LiveExecutionRouter(mode)
    except MainnetRefused as exc:
        print(str(exc), file=sys.stderr)
        return 2

    info = router.preflight()
    if args.json:
        print(json.dumps(info, indent=2))
        return 0

    print("=" * 76)
    print(f" EXECUTION ROUTER PREFLIGHT - {info['mode']}")
    print("=" * 76)
    print(f" venue                 : {info['venue']}")
    print(f" base url              : {info['base_url']}")
    print(f" credentials present   : {info['credentials_present']}  ({info['key_env']})")
    print(f" consensus required    : {info['consensus_required']}")
    print(f" kill switch tripped   : {info['kill_switch_tripped']}")
    print(f" edge report found     : {info['edge_report_found']}")
    print(f" edge report has_edge  : {info['edge_report_has_edge']}")
    print(f"   verdict             : {info['edge_report_verdict']}")
    print("-" * 76)
    lim = info["limits"]
    print(f" max order notional    : {lim['max_order_notional_cents'] / 100:,.2f}")
    print(f" session loss stop     : {lim['max_session_loss_cents'] / 100:,.2f}")
    print(f" max open positions    : {lim['max_open_positions']}")
    print(f" mandatory stop        : {lim['mandatory_stop_bps']:.0f} bps, placed AT the venue")
    print("-" * 76)
    blockers = info["mainnet_blockers"]
    if blockers:
        print(" MAINNET is blocked by:")
        for b in blockers:
            print(f"   - {b}")
    else:
        print(" MAINNET: every gate is satisfied.")
    print("=" * 76)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
