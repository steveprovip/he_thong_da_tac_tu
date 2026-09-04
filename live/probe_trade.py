#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
probe_trade.py - one deliberate order through the real pipeline, to prove it works.

The problem this solves is narrow and real: the swarm can run for an hour without
firing, and when it does not fire you cannot tell whether the gate is refusing
(working) or the order path is broken (not working). Those look identical from
the outside - no trades either way - and they need completely different responses.

So this sends exactly one order, on purpose, and reports which of the seven steps
it got to. It is an integration test of the plumbing, not a trading strategy.

What it deliberately does NOT do
--------------------------------
**It does not bypass anything.** The obvious shortcut is a function that signs a
payload and posts it - and `run_binance_testnet.py` already had one. That tests
the HTTP call and nothing else: not the consensus gate, not the token, not the
kill switch, not the bracket stop, not the ledger. A probe that skips the machine
it is meant to be probing proves the one part that was never in doubt.

Instead the probe walks the whole path:

    1. build a real Proposal
    2. run a real six-analyst quorum vote through ConsensusEngine
    3. receive a real single-use AuthToken
    4. hand it to the hardened LiveExecutionRouter as VESKA
    5. router checks the kill switch and consumes the token
    6. entry order goes to the venue
    7. STOP_MARKET closePosition goes to the venue, or the entry is flattened

If step 7 fails, the probe has found a real problem worth knowing about before
the swarm finds it at 3am with a position on.

The votes are synthetic and say so
-----------------------------------
`--confidence 0.95` builds a unanimous quorum at that confidence. This is a
declared, visible override of the analysts' opinion for one order, not a
loosening of the gate: the threshold is untouched, the token is real, the stop is
real. Every record it writes carries `PROBE` in the client order id, so a probe
fill is never mistaken for a signal the swarm generated.

TESTNET by default, and MAINNET is not a flag away
---------------------------------------------------
`--mode MAINNET` goes through `LiveExecutionRouter`'s mainnet gate like anything
else, which currently refuses because the out-of-sample edge report says
`has_edge: false`. A probe is a plumbing test; it is not a reason to spend real
money, and there is no path here that treats it as one.

CLI:
    python live/probe_trade.py --selftest
    python live/probe_trade.py --dry-run
    python live/probe_trade.py --symbol BTCUSDT --qty 0.002
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional

REPO_ROOT = Path(__file__).resolve().parent.parent
for _sub in ("core", "simulation", "benchmarks", "live"):
    _p = str(REPO_ROOT / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from consensus_engine import (  # noqa: E402
    AgentRegistry,
    ConsensusDecision,
    ConsensusEngine,
    Proposal,
    VoteStance,
    build_quorum,
)
from live_execution_router import (  # noqa: E402
    ExecutionMode,
    LiveExecutionRouter,
    OrderRequest,
    OrderStatus,
    RouterError,
    RouterLimits,
)
from risk_guard import RiskGuard  # noqa: E402

__all__ = ["ProbeStep", "ProbeResult", "run_probe", "PROBE_PREFIX"]

PROBE_PREFIX = "PROBE"


@dataclass
class ProbeStep:
    name: str
    ok: bool
    detail: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {"name": self.name, "ok": self.ok, "detail": self.detail}


@dataclass
class ProbeResult:
    """Where the probe got to, and what stopped it if it stopped."""

    steps: list[ProbeStep] = field(default_factory=list)
    order: Optional[dict[str, Any]] = None
    consensus: Optional[dict[str, Any]] = None
    mode: str = ExecutionMode.SIMULATION.value

    def add(self, name: str, ok: bool, detail: str = "") -> bool:
        self.steps.append(ProbeStep(name, ok, detail))
        return ok

    @property
    def completed(self) -> bool:
        return bool(self.steps) and all(s.ok for s in self.steps)

    @property
    def failed_at(self) -> Optional[str]:
        for s in self.steps:
            if not s.ok:
                return s.name
        return None

    @property
    def protected(self) -> bool:
        return bool(self.order and self.order.get("protected"))

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "completed": self.completed,
            "failed_at": self.failed_at,
            "protected": self.protected,
            "steps": [s.as_dict() for s in self.steps],
            "consensus": self.consensus,
            "order": self.order,
        }


def run_probe(
    *,
    symbol: str = "BTCUSDT",
    side: str = "BUY",
    quantity_units: int = 2_000,
    qty_scale_pow10: int = 6,
    spot_price_cents: int = 6_412_500,
    confidence: float = 0.95,
    stop_bps: float = 150.0,
    mode: ExecutionMode = ExecutionMode.SIMULATION,
    router: Optional[LiveExecutionRouter] = None,
    guard: Optional[RiskGuard] = None,
    limits: Optional[RouterLimits] = None,
    transport: Optional[Callable[..., dict[str, Any]]] = None,
    db_path: Optional[Path] = None,
    env: Optional[dict[str, str]] = None,
    dry_run: bool = False,
) -> ProbeResult:
    """Walk the full path once. Never raises; every failure becomes a failed step."""
    result = ProbeResult(mode=mode.value)
    registry = AgentRegistry()
    engine = ConsensusEngine(registry)
    stance = VoteStance.BUY if side.upper() == "BUY" else VoteStance.SELL

    proposal = Proposal(
        proposal_id=f"{PROBE_PREFIX}-{int(time.time() * 1000)}",
        symbol=symbol,
        side=stance,
        qty=max(1, quantity_units // (10 ** qty_scale_pow10) or 1),
        price_cents=spot_price_cents,
        meta={"probe": True, "note": "operator-initiated plumbing test"},
    )
    result.add("1. build proposal", True,
               f"{side} {quantity_units}e-{qty_scale_pow10} {symbol} @ {spot_price_cents/100:,.2f}")

    votes = build_quorum(registry, stance, confidence)
    result.add("2. synthetic quorum", True,
               f"six analysts at {confidence:.2f} confidence (declared override, gate untouched)")

    outcome = engine.evaluate(proposal, votes)
    result.consensus = outcome.as_dict()
    if not result.add(
        "3. consensus gate", outcome.decision is ConsensusDecision.EXECUTE, outcome.reason
    ):
        return result

    token = outcome.token.token if outcome.token else ""
    if not result.add("4. single-use AuthToken", bool(token),
                      f"score {outcome.score_pct:.2f}% cleared the bar"):
        return result

    if dry_run:
        result.add("5. router (dry run)", True, "stopped before contacting any venue")
        return result

    try:
        r = router or LiveExecutionRouter(
            mode, authoriser=engine, guard=guard, limits=limits,
            transport=transport, db_path=db_path, env=env,
        )
        if router is not None:
            r.authoriser = engine
    except RouterError as exc:
        result.add("5. router init", False, str(exc))
        return result
    result.add("5. router init", True, f"{r.mode.value} -> {r.venue.base_url if r.venue else 'paper'}")

    req = OrderRequest(
        symbol=symbol, side=side.upper(), quantity_units=quantity_units,
        qty_scale_pow10=qty_scale_pow10, stop_bps=stop_bps,
        client_order_id=f"{PROBE_PREFIX}_{int(time.time() * 1000)}",
    )
    try:
        rec = r.execute(req, spot_price_cents=spot_price_cents,
                        proposal=proposal, token=token, agent="VESKA")
    except Exception as exc:  # noqa: BLE001 - a probe reports, it does not crash
        result.add("6. entry order", False, f"{type(exc).__name__}: {exc}")
        return result

    result.order = rec.as_dict()
    result.add("6. entry order", rec.status is OrderStatus.FILLED,
               f"{rec.status.value} @ {rec.fill_price_cents/100:,.2f}, id {rec.exchange_order_id}")
    result.add("7. exchange-side stop", bool(rec.stop_order_id),
               f"stop id {rec.stop_order_id} @ {(rec.stop_price_cents or 0)/100:,.2f}"
               if rec.stop_order_id else "NO STOP ON THE VENUE")
    return result


# ---------------------------------------------------------------------------
# Self-test and CLI
# ---------------------------------------------------------------------------


def _selftest() -> int:
    import tempfile  # noqa: PLC0415

    from live_execution_router import _fake_venue  # noqa: PLC0415

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

    def td() -> Path:
        return Path(tempfile.mkdtemp())

    def t_paper_probe_completes() -> None:
        r = run_probe(db_path=td() / "p.db")
        assert r.completed, r.as_dict()
        assert r.protected

    def t_dry_run_touches_no_venue() -> None:
        seen: list[str] = []
        r = run_probe(dry_run=True, transport=_fake_venue(seen=seen), db_path=td() / "p.db")
        assert r.completed and seen == []

    def t_low_confidence_stops_at_the_gate() -> None:
        r = run_probe(confidence=0.40, db_path=td() / "p.db")
        assert not r.completed
        assert r.failed_at == "3. consensus gate", r.failed_at
        assert r.order is None, "an order was sent despite the gate refusing"

    def t_testnet_places_a_real_stop() -> None:
        seen: list[str] = []
        r = run_probe(
            mode=ExecutionMode.TESTNET, transport=_fake_venue(seen=seen), db_path=td() / "p.db",
            env={"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"},
        )
        assert r.completed, r.as_dict()
        assert any("STOP_MARKET" in u for u in seen), "no stop order reached the venue"

    def t_stop_failure_is_reported_not_hidden() -> None:
        r = run_probe(
            mode=ExecutionMode.TESTNET,
            transport=_fake_venue(stop=RouterError("margin")), db_path=td() / "p.db",
            env={"BINANCE_TESTNET_API_KEY": "k", "BINANCE_TESTNET_SECRET_KEY": "s"},
        )
        assert not r.completed
        assert r.failed_at == "6. entry order", r.failed_at

    def t_mainnet_is_refused() -> None:
        r = run_probe(
            mode=ExecutionMode.MAINNET, transport=_fake_venue(), db_path=td() / "p.db", env={},
        )
        assert not r.completed
        assert r.failed_at == "5. router init"
        assert "edge report" in r.steps[-1].detail

    def t_kill_switch_stops_the_probe() -> None:
        from risk_guard import RiskGuardConfig  # noqa: PLC0415

        d = td()
        g = RiskGuard(RiskGuardConfig(db_path=d / "g.db", kill_file=d / "K",
                                      allow_unmeasurable_hardware=True))
        try:
            g.trip_kill_switch("selftest")
            r = run_probe(guard=g, db_path=d / "p.db")
            assert not r.completed and r.failed_at == "6. entry order"
        finally:
            g.close()

    def t_orders_are_tagged_as_probes() -> None:
        r = run_probe(db_path=td() / "p.db")
        assert r.order["client_order_id"].startswith(PROBE_PREFIX)

    def t_result_is_json_safe() -> None:
        json.dumps(run_probe(db_path=td() / "p.db").as_dict())

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    print(f"probe_trade self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="One deliberate order through the real pipeline")
    ap.add_argument("--mode", default="TESTNET", choices=[m.value for m in ExecutionMode])
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--side", default="BUY", choices=["BUY", "SELL"])
    ap.add_argument("--qty", type=float, default=0.002, help="base asset quantity")
    ap.add_argument("--price", type=float, default=64125.0, help="reference spot price")
    ap.add_argument("--confidence", type=float, default=0.95)
    ap.add_argument("--stop-bps", type=float, default=150.0)
    ap.add_argument("--dry-run", action="store_true", help="stop before contacting any venue")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    result = run_probe(
        symbol=args.symbol,
        side=args.side,
        quantity_units=int(round(args.qty * 1_000_000)),
        qty_scale_pow10=6,
        spot_price_cents=int(round(args.price * 100)),
        confidence=args.confidence,
        stop_bps=args.stop_bps,
        mode=ExecutionMode(args.mode),
        dry_run=args.dry_run,
    )

    if args.json:
        print(json.dumps(result.as_dict(), indent=2))
        return 0 if result.completed else 1

    print("=" * 74)
    print(f" PROBE TRADE - {result.mode}"
          + ("  (dry run: nothing leaves this machine)" if args.dry_run else ""))
    print("=" * 74)
    for s in result.steps:
        print(f"  [{'ok' if s.ok else 'XX'}] {s.name:<26} {s.detail}")
    print("-" * 74)
    if result.completed:
        print(" The whole path works: vote -> token -> VESKA -> venue -> stop.")
        if result.order:
            o = result.order
            print(f" order {o['client_order_id']}  {o['side']} {o['quantity']} {o['symbol']}")
            print(f" filled {o['fill_price_cents']/100:,.2f}   stop "
                  f"{(o['stop_price_cents'] or 0)/100:,.2f} (id {o['stop_order_id']})")
            print(f" protected on the venue: {o['protected']}")
    else:
        print(f" Stopped at: {result.failed_at}")
        print(" Nothing further was attempted. Read the failed step above.")
    print("=" * 74)
    return 0 if result.completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
