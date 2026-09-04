#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_live_execution.py - the router that can send an order off this machine.

    pytest tests/test_live_execution.py -v

Nothing here touches a real venue. Every test injects a transport, because a
test suite that places orders on a testnet is a test suite that fails when
someone else's testnet is down, and one that places orders on a mainnet is not
a test suite.

The three defects these tests exist to keep fixed
-------------------------------------------------
1. **The stop-loss used to be a string.** `stop_loss_order_id` was set to
   `f"STOP_{cid}"` and no second request was ever sent, so the documented
   protection - "the stop survives your PC losing power" - did not exist.
   `test_a_real_stop_order_is_sent` asserts a second `STOP_MARKET` request
   actually goes out, and `test_stop_failure_flattens_the_entry` asserts that
   when it cannot, the entry is closed rather than left naked.

2. **A lost response used to be recorded as a fill.** `except Exception: pass`
   under the HTTP call, with `status=FILLED` assigned beforehand. A timeout -
   precisely when the venue may have accepted the order - produced a ledger that
   said FILLED. `test_a_timeout_is_unknown_not_filled` pins the distinction
   between "the venue said no" (rejected, no position) and "we do not know"
   (unknown, reconcile before doing anything else).

3. **MAINNET used the testnet URL and testnet keys.** One constant for both.
   `test_mainnet_and_testnet_share_nothing` keeps them separate.

And the gate that was missing entirely: an order used to need no consensus token
and no kill-switch check, despite both being the project's central invariants.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from pathlib import Path
from typing import Any

import pytest

from consensus_engine import (
    AgentRegistry,
    ConsensusEngine,
    Proposal,
    VoteStance,
    build_quorum,
)
from edge_report import EdgeReport, load_report, required_t_for_trials, write_report
from live_execution_router import (
    ALLOW_MAINNET_ENV,
    VENUES,
    DuplicateOrder,
    ExecutionMode,
    LimitBreached,
    LiveExecutionRouter,
    MainnetRefused,
    OrderRequest,
    OrderStatus,
    OrderUnknown,
    RouterError,
    RouterLimits,
    StopLossNotPlaced,
    UnauthorisedOrder,
    _fake_venue,
)
from risk_guard import RiskGuard, RiskGuardConfig

SPOT = 6_412_500  # $64,125.00


def order(**kw: Any) -> OrderRequest:
    base: dict[str, Any] = dict(
        symbol="BTC-USDT", side="BUY", quantity_units=1_000, qty_scale_pow10=6
    )
    base.update(kw)
    return OrderRequest(**base)


@pytest.fixture
def paper(tmp_path: Path) -> LiveExecutionRouter:
    return LiveExecutionRouter(
        ExecutionMode.SIMULATION, db_path=tmp_path / "l.db",
        edge_report_path=tmp_path / "absent.json",
    )


def _testnet(tmp_path: Path, **kw: Any) -> LiveExecutionRouter:
    kw.setdefault("transport", _fake_venue())
    kw.setdefault("env", {"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"})
    return LiveExecutionRouter(
        ExecutionMode.TESTNET, db_path=tmp_path / "l.db",
        edge_report_path=tmp_path / "absent.json", **kw
    )


# ---------------------------------------------------------------------------
# Integer money
# ---------------------------------------------------------------------------


class TestIntegerAccounting:
    def test_every_amount_is_an_int(self, paper: LiveExecutionRouter) -> None:
        r = paper.execute(order(), spot_price_cents=SPOT)
        for name in ("fill_price_cents", "stop_price_cents", "notional_cents", "fee_cents"):
            v = getattr(r, name)
            assert isinstance(v, int) and not isinstance(v, bool), f"{name} is {type(v)}"

    @pytest.mark.parametrize("bad", [0.5, "10", True, None])
    def test_float_quantity_refused(self, bad: Any) -> None:
        with pytest.raises(RouterError):
            OrderRequest(symbol="BTC-USDT", side="BUY", quantity_units=bad)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_nonpositive_quantity_refused(self, bad: int) -> None:
        with pytest.raises(RouterError):
            OrderRequest(symbol="BTC-USDT", side="BUY", quantity_units=bad)

    def test_float_spot_price_refused(self, paper: LiveExecutionRouter) -> None:
        with pytest.raises(RouterError):
            paper.execute(order(), spot_price_cents=64_125.0)  # type: ignore[arg-type]

    def test_bad_side_refused(self) -> None:
        with pytest.raises(RouterError):
            OrderRequest(symbol="BTC-USDT", side="LONG", quantity_units=1)

    def test_quantity_reaches_the_wire_as_a_decimal_string(self) -> None:
        assert str(order(quantity_units=1_500_000, qty_scale_pow10=6).quantity_decimal) == "1.500000"


# ---------------------------------------------------------------------------
# The stop that has to be real
# ---------------------------------------------------------------------------


class TestProtectiveStop:
    def test_long_stop_below_short_stop_above(self, paper: LiveExecutionRouter) -> None:
        long = paper.execute(order(), spot_price_cents=SPOT)
        short = paper.execute(order(side="SELL"), spot_price_cents=SPOT)
        assert long.stop_price_cents < long.fill_price_cents
        assert short.stop_price_cents > short.fill_price_cents

    def test_stop_distance_matches_the_requested_bps(self, paper: LiveExecutionRouter) -> None:
        r = paper.execute(order(stop_bps=150.0), spot_price_cents=1_000_000)
        assert r.stop_price_cents == 1_000_000 - 15_000

    def test_a_real_stop_order_is_sent(self, tmp_path: Path) -> None:
        seen: list[str] = []
        r = _testnet(tmp_path, transport=_fake_venue(seen=seen))
        rec = r.execute(order(), spot_price_cents=SPOT)
        stops = [u for u in seen if "STOP_MARKET" in u]
        assert stops, "no STOP_MARKET request was sent"
        assert "closePosition" in stops[0]
        assert "side=SELL" in stops[0], "the stop must be on the opposite side"
        assert rec.stop_order_id == "777"
        assert rec.protected

    def test_stop_failure_flattens_the_entry(self, tmp_path: Path) -> None:
        seen: list[str] = []
        r = _testnet(tmp_path, transport=_fake_venue(stop=RouterError("margin"), seen=seen))
        with pytest.raises(StopLossNotPlaced, match="not a state this router returns"):
            r.execute(order(), spot_price_cents=SPOT)
        assert any("FLAT_" in u for u in seen), "the naked entry was not flattened"

    def test_a_failed_flatten_is_reported_loudly(self, tmp_path: Path) -> None:
        r = _testnet(tmp_path, transport=_fake_venue(
            stop=RouterError("margin"), flat=RouterError("also down")))
        with pytest.raises(StopLossNotPlaced) as exc:
            r.execute(order(), spot_price_cents=SPOT)
        assert "check the venue by hand" in str(exc.value)

    def test_stop_beyond_the_cap_refused(self, paper: LiveExecutionRouter) -> None:
        with pytest.raises(LimitBreached):
            paper.execute(order(stop_bps=9_999.0), spot_price_cents=SPOT)

    def test_paper_records_the_same_shape_as_live(self, paper: LiveExecutionRouter) -> None:
        # A paper record shaped differently from a live one hides bugs until the
        # day they cost money.
        r = paper.execute(order(), spot_price_cents=SPOT)
        assert r.protected and r.stop_order_id and r.stop_price_cents


# ---------------------------------------------------------------------------
# Never lie about a fill
# ---------------------------------------------------------------------------


class TestFillHonesty:
    def test_a_timeout_is_unknown_not_filled(self, tmp_path: Path) -> None:
        r = _testnet(tmp_path, transport=_fake_venue(entry=OrderUnknown("timeout")))
        with pytest.raises(OrderUnknown, match="reconcile"):
            r.execute(order(), spot_price_cents=SPOT)
        rows = r.unreconciled()
        assert len(rows) == 1 and rows[0]["status"] == OrderStatus.UNKNOWN.value

    def test_a_venue_rejection_is_not_unknown(self, tmp_path: Path) -> None:
        r = _testnet(tmp_path, transport=_fake_venue(entry={"status": "REJECTED"}))
        with pytest.raises(RouterError):
            r.execute(order(), spot_price_cents=SPOT)
        assert r.unreconciled() == [], "a clean rejection was flagged for reconcile"
        assert r.stats["rejected"] == 1

    def test_the_fill_price_comes_from_the_venue(self, tmp_path: Path) -> None:
        r = _testnet(tmp_path, transport=_fake_venue(
            entry={"orderId": 1, "status": "FILLED", "avgPrice": "64200.50"}))
        rec = r.execute(order(), spot_price_cents=SPOT)
        assert rec.fill_price_cents == 6_420_050
        assert rec.stop_price_cents == 6_420_050 - int(round(6_420_050 * 150 / 10_000))

    def test_reconcile_reports_a_naked_position(self, tmp_path: Path) -> None:
        r = _testnet(tmp_path, transport=_fake_venue(entry=OrderUnknown("timeout")))
        with pytest.raises(OrderUnknown):
            r.execute(order(client_order_id="CID-1"), spot_price_cents=SPOT)
        r._transport = _fake_venue(entry={"orderId": 5, "status": "FILLED"})
        rec = r.reconcile("CID-1")
        assert rec.status is OrderStatus.FILLED
        assert "NO stop" in rec.detail
        assert not rec.needs_reconcile

    def test_reconcile_reports_a_nonexistent_order(self, tmp_path: Path) -> None:
        r = _testnet(tmp_path, transport=_fake_venue(entry=OrderUnknown("timeout")))
        with pytest.raises(OrderUnknown):
            r.execute(order(client_order_id="CID-2"), spot_price_cents=SPOT)
        r._transport = _fake_venue(entry=RouterError("HTTP 400: Unknown order sent"))
        rec = r.reconcile("CID-2")
        assert rec.status is OrderStatus.REJECTED and not rec.needs_reconcile

    def test_reconcile_needs_a_ledger_row(self, paper: LiveExecutionRouter) -> None:
        with pytest.raises(RouterError, match="no ledger row"):
            paper.reconcile("never-sent")


# ---------------------------------------------------------------------------
# Authorisation
# ---------------------------------------------------------------------------


class TestAuthorisation:
    def test_kill_switch_blocks_the_router(self, tmp_path: Path) -> None:
        g = RiskGuard(RiskGuardConfig(
            db_path=tmp_path / "g.db", kill_file=tmp_path / "K",
            allow_unmeasurable_hardware=True))
        try:
            g.trip_kill_switch("test")
            r = LiveExecutionRouter(ExecutionMode.SIMULATION, db_path=tmp_path / "l.db", guard=g)
            with pytest.raises(UnauthorisedOrder, match="kill switch"):
                r.execute(order(), spot_price_cents=SPOT)
        finally:
            g.close()

    def test_no_token_no_order(self, tmp_path: Path) -> None:
        r = LiveExecutionRouter(
            ExecutionMode.SIMULATION, db_path=tmp_path / "l.db",
            authoriser=ConsensusEngine(AgentRegistry()),
        )
        with pytest.raises(UnauthorisedOrder, match="single-use token"):
            r.execute(order(), spot_price_cents=SPOT)

    def test_a_valid_token_is_accepted_once(self, tmp_path: Path) -> None:
        reg = AgentRegistry()
        engine = ConsensusEngine(reg)
        r = LiveExecutionRouter(
            ExecutionMode.SIMULATION, db_path=tmp_path / "l.db", authoriser=engine)
        p = Proposal("p-1", "BTC-USDT", VoteStance.BUY, 1, SPOT)
        res = engine.evaluate(p, build_quorum(reg, VoteStance.BUY, 0.95))
        rec = r.execute(order(), spot_price_cents=SPOT, proposal=p, token=res.token.token)
        assert rec.status is OrderStatus.FILLED
        with pytest.raises(UnauthorisedOrder):
            r.execute(order(), spot_price_cents=SPOT, proposal=p, token=res.token.token)

    def test_a_forged_token_is_refused(self, tmp_path: Path) -> None:
        reg = AgentRegistry()
        r = LiveExecutionRouter(
            ExecutionMode.SIMULATION, db_path=tmp_path / "l.db",
            authoriser=ConsensusEngine(reg))
        p = Proposal("p-2", "BTC-USDT", VoteStance.BUY, 1, SPOT)
        with pytest.raises(UnauthorisedOrder):
            r.execute(order(), spot_price_cents=SPOT, proposal=p, token="deadbeef.cafe")

    def test_missing_credentials_refused(self, tmp_path: Path) -> None:
        r = _testnet(tmp_path, env={})
        with pytest.raises(UnauthorisedOrder, match="BINANCE_TESTNET_API_KEY"):
            r.execute(order(), spot_price_cents=SPOT)


# ---------------------------------------------------------------------------
# Limits and idempotency
# ---------------------------------------------------------------------------


class TestLimits:
    def test_duplicate_client_order_id_refused(self, paper: LiveExecutionRouter) -> None:
        paper.execute(order(client_order_id="X-1"), spot_price_cents=SPOT)
        with pytest.raises(DuplicateOrder, match="already in the ledger"):
            paper.execute(order(client_order_id="X-1"), spot_price_cents=SPOT)

    def test_notional_cap(self, tmp_path: Path) -> None:
        r = LiveExecutionRouter(
            ExecutionMode.SIMULATION, db_path=tmp_path / "l.db",
            limits=RouterLimits(max_order_notional_cents=100))
        with pytest.raises(LimitBreached, match="notional"):
            r.execute(order(), spot_price_cents=SPOT)

    def test_open_position_cap(self, tmp_path: Path) -> None:
        r = LiveExecutionRouter(
            ExecutionMode.SIMULATION, db_path=tmp_path / "l.db",
            limits=RouterLimits(max_open_positions=2))
        r.execute(order(), spot_price_cents=SPOT)
        r.execute(order(), spot_price_cents=SPOT)
        with pytest.raises(LimitBreached, match="positions already open"):
            r.execute(order(), spot_price_cents=SPOT)

    def test_session_loss_stop(self, tmp_path: Path) -> None:
        r = LiveExecutionRouter(
            ExecutionMode.SIMULATION, db_path=tmp_path / "l.db",
            limits=RouterLimits(max_session_loss_cents=500))
        r.record_realised_cents(-500)
        with pytest.raises(LimitBreached, match="stop-for-the-day"):
            r.execute(order(), spot_price_cents=SPOT)

    @pytest.mark.parametrize("bad", [{"max_order_notional_cents": 0},
                                     {"max_open_positions": -1},
                                     {"max_session_loss_cents": 1.5}])
    def test_nonsense_limits_refused(self, bad: dict) -> None:
        with pytest.raises(RouterError):
            RouterLimits(**bad)

    def test_client_order_id_names_the_agent(self, paper: LiveExecutionRouter) -> None:
        cid = paper.new_client_order_id("VESKA")
        assert cid.startswith("GROK_VESKA_") and len(cid.split("_")) == 4


# ---------------------------------------------------------------------------
# The mainnet gate
# ---------------------------------------------------------------------------


def _edge_file(tmp_path: Path, has_edge: bool) -> Path:
    p = tmp_path / "edge.json"
    p.write_text(json.dumps({
        "schema": "groktopus.edge_report/1",
        "has_edge": has_edge,
        "verdict": "EDGE FOUND" if has_edge else "NO EDGE: -8.02 bps",
    }), encoding="utf-8")
    return p


FULL_ENV = {
    ALLOW_MAINNET_ENV: "1",
    "BINANCE_MAINNET_API_KEY": "k",
    "BINANCE_MAINNET_SECRET_KEY": "s",
}


class TestMainnetGate:
    def test_refused_with_nothing_configured(self, tmp_path: Path) -> None:
        with pytest.raises(MainnetRefused) as exc:
            LiveExecutionRouter(ExecutionMode.MAINNET, db_path=tmp_path / "l.db", env={})
        msg = str(exc.value)
        assert ALLOW_MAINNET_ENV in msg
        assert "edge report" in msg

    def test_refused_without_the_opt_in_flag(self, tmp_path: Path) -> None:
        env = {k: v for k, v in FULL_ENV.items() if k != ALLOW_MAINNET_ENV}
        with pytest.raises(MainnetRefused, match=ALLOW_MAINNET_ENV):
            LiveExecutionRouter(
                ExecutionMode.MAINNET, db_path=tmp_path / "l.db",
                edge_report_path=_edge_file(tmp_path, True), env=env)

    def test_refused_when_the_edge_report_says_no(self, tmp_path: Path) -> None:
        with pytest.raises(MainnetRefused, match="has_edge=false"):
            LiveExecutionRouter(
                ExecutionMode.MAINNET, db_path=tmp_path / "l.db",
                edge_report_path=_edge_file(tmp_path, False), env=FULL_ENV)

    def test_refused_when_there_is_no_edge_report(self, tmp_path: Path) -> None:
        with pytest.raises(MainnetRefused, match="no out-of-sample edge report"):
            LiveExecutionRouter(
                ExecutionMode.MAINNET, db_path=tmp_path / "l.db",
                edge_report_path=tmp_path / "absent.json", env=FULL_ENV)

    def test_refused_when_the_kill_switch_is_tripped(self, tmp_path: Path) -> None:
        g = RiskGuard(RiskGuardConfig(
            db_path=tmp_path / "g.db", kill_file=tmp_path / "K",
            allow_unmeasurable_hardware=True))
        try:
            g.trip_kill_switch("test")
            with pytest.raises(MainnetRefused, match="kill switch"):
                LiveExecutionRouter(
                    ExecutionMode.MAINNET, db_path=tmp_path / "l.db",
                    edge_report_path=_edge_file(tmp_path, True), env=FULL_ENV, guard=g)
        finally:
            g.close()

    def test_opens_when_every_gate_is_satisfied(self, tmp_path: Path) -> None:
        r = LiveExecutionRouter(
            ExecutionMode.MAINNET, db_path=tmp_path / "l.db",
            edge_report_path=_edge_file(tmp_path, True), env=FULL_ENV,
            transport=_fake_venue())
        assert r.venue.base_url == "https://fapi.binance.com"

    def test_mainnet_and_testnet_share_nothing(self) -> None:
        m, t = VENUES[ExecutionMode.MAINNET], VENUES[ExecutionMode.TESTNET]
        assert m.base_url != t.base_url
        assert m.key_env != t.key_env
        assert m.secret_env != t.secret_env
        assert "testnet" not in m.base_url

    def test_the_live_repo_report_currently_blocks_mainnet(self) -> None:
        # Documents the state of the shipped measurement rather than asserting a
        # value: if a real edge is ever found, this flips and that is the point.
        report = load_report()
        if report is not None:
            assert isinstance(report["has_edge"], bool)
            if not report["has_edge"]:
                assert "NO EDGE" in report["verdict"] or "NOT SIGNIFICANT" in report["verdict"]

    def test_preflight_touches_no_network(self, tmp_path: Path) -> None:
        seen: list[str] = []
        r = _testnet(tmp_path, transport=_fake_venue(seen=seen))
        info = r.preflight()
        assert seen == []
        assert info["mode"] == "TESTNET"
        json.dumps(info)


# ---------------------------------------------------------------------------
# Signing
# ---------------------------------------------------------------------------


class TestSigning:
    def test_signature_covers_the_exact_query(self, tmp_path: Path) -> None:
        r = _testnet(tmp_path)
        q = r._signed({"symbol": "BTCUSDT", "side": "BUY"})
        body, sig = q.rsplit("&signature=", 1)
        assert sig == hmac.new(b"s", body.encode(), hashlib.sha256).hexdigest()

    def test_recv_window_and_timestamp_are_signed(self, tmp_path: Path) -> None:
        body = _testnet(tmp_path)._signed({"symbol": "BTCUSDT"}).rsplit("&signature=", 1)[0]
        assert "recvWindow=" in body and "timestamp=" in body

    def test_the_secret_never_appears_in_the_url(self, tmp_path: Path) -> None:
        assert "s" not in _testnet(tmp_path)._signed({"symbol": "BTCUSDT"}).split("signature=")[0].replace(
            "symbol=BTCUSDT", "").replace("recvWindow", "").replace("timestamp", "")

    def test_time_sync_uses_the_venue_clock(self, tmp_path: Path) -> None:
        r = _testnet(tmp_path, transport=_fake_venue())
        r._sync_time()
        assert abs(r._time_offset_ms) < 60_000


# ---------------------------------------------------------------------------
# The edge report the gate reads
# ---------------------------------------------------------------------------


class TestEdgeReportGate:
    def base(self, **kw: Any) -> EdgeReport:
        d: dict[str, Any] = dict(
            trades=500, mean_return=0.001, stdev_return=0.01, t_stat=2.5,
            p_value=0.01, win_rate=0.55, profit_factor=1.3,
            max_drawdown_pct=5.0, total_return_pct=4.0)
        d.update(kw)
        return EdgeReport(**d)

    def test_too_few_trades_is_inconclusive(self) -> None:
        assert not self.base(trades=50).has_edge

    def test_negative_mean_is_no_edge(self) -> None:
        assert not self.base(mean_return=-0.0008).has_edge

    def test_weak_significance_fails(self) -> None:
        assert not self.base(t_stat=1.5).has_edge

    def test_declaring_more_trials_raises_the_bar(self) -> None:
        assert self.base(t_stat=2.5, trials=1).has_edge
        assert not self.base(t_stat=2.5, trials=25).has_edge

    def test_twenty_trials_need_roughly_three_sigma(self) -> None:
        assert 2.9 < required_t_for_trials(20) < 3.2

    def test_a_corrupt_report_reads_as_no_evidence(self, tmp_path: Path) -> None:
        p = tmp_path / "edge.json"
        p.write_text("{ truncated", encoding="utf-8")
        assert load_report(p) is None

    def test_a_foreign_schema_reads_as_no_evidence(self, tmp_path: Path) -> None:
        p = tmp_path / "edge.json"
        p.write_text('{"schema":"other/1","has_edge":true}', encoding="utf-8")
        assert load_report(p) is None

    def test_round_trip(self, tmp_path: Path) -> None:
        p = write_report(self.base(t_stat=4.0), tmp_path / "edge.json")
        assert load_report(p)["has_edge"] is True
