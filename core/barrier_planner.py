#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
barrier_planner.py - where to put the exits, and whether they can pay for themselves.

The Triple Barrier Method (Lopez de Prado, *Advances in Financial Machine
Learning*, ch. 3) is the right shape for an exit rule: a profit barrier, a loss
barrier, and a vertical barrier in time so a position cannot be held forever
waiting for either. This module implements it, and it implements the thing that
has to come first - the arithmetic that says whether a given barrier placement
can make money at all.

The arithmetic nobody runs
--------------------------
Let `tp` and `sl` be the barrier distances in basis points and `c` the round-trip
cost in basis points (both fees, the spread, and slippage). A win returns
`tp - c`; a loss returns `-(sl + c)`. So expected value per trade is

    EV = p * (tp - c) - (1 - p) * (sl + c)
       = p * (tp + sl) - (sl + c)

and the win rate needed merely to break even is

    p* = (sl + c) / (tp + sl)

This is the whole model, and it is unforgiving in one specific way: **`c` appears
in the numerator and not in the denominator.** Shrinking the barriers shrinks
`tp` and `sl` together, leaving `c` fixed, so `p*` rises toward 1. There is a
barrier scale below which no win rate is sufficient, and it is not far away.

Worked, with the numbers from this account
-------------------------------------------
Binance USD-M Futures taker is 0.04% a side, so `c` is about 8-9 bps once the
spread is included. BTC's 1-minute ATR runs 0.04-0.09%, call it 6 bps.

    proposed:  k_tp = 1.5, k_sl = 1.0  ->  tp = 9 bps, sl = 6 bps
               p* = (6 + 9) / (9 + 6) = 1.00

**A hundred percent.** Every trade must win, forever, to break even. The
round-trip cost is 1.5x the entire average one-minute range, and a strategy
cannot scalp inside its own transaction cost. Tightening the barriers toward ATR
does not make the system trade profitably more often; it makes each trade
mathematically unable to profit.

By contrast the fixed barriers the system ships with:

    current:   tp = 120 bps (1.2%), sl = 100 bps (1.0%)
               p* = (100 + 9) / (120 + 100) = 0.495

Just under a coin flip. The complaint that these are "too far away" is correct
about the *latency* - 1.2% takes hours at 6 bps of per-minute range - but the
fix is not to move them inside the cost band. It is to hold for the horizon the
barriers imply, or to trade an instrument whose range is large relative to the
fee, or to earn the maker rebate instead of paying the taker fee.

`minimum_viable_k_tp` computes the smallest profit barrier that leaves a coin
flip break-even, and `sweep` tabulates the trade-off so it can be seen rather
than argued about.

What this module does NOT claim
-------------------------------
It says nothing about whether any given `p` is achievable. That is what
`benchmarks/edge_report.py` measures, and as of the last run the measured win
rate is 42.3% with a negative mean return. A barrier placement that requires
p* = 0.50 is not a plan to make money; it is the precondition for one.

CLI:
    python core/barrier_planner.py --selftest
    python core/barrier_planner.py --atr-pct 0.06 --report
    python core/barrier_planner.py --atr-pct 0.06 --sweep
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, Iterable, Optional, Sequence

__all__ = [
    "BarrierError",
    "BarrierTouch",
    "CostModel",
    "BarrierConfig",
    "BarrierEconomics",
    "TripleBarrier",
    "breakeven_win_rate",
    "minimum_viable_k_tp",
    "sweep",
    "BINANCE_USDM_TAKER",
    "BINANCE_USDM_MAKER",
]

#: Binance USD-M Futures standard tier, as basis points per side.
BINANCE_USDM_TAKER = 4.0
BINANCE_USDM_MAKER = 2.0


class BarrierError(ValueError):
    """A barrier configuration that cannot be evaluated."""


class BarrierTouch(str, Enum):
    """Which barrier a position hit."""

    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    TIME = "TIME"
    BREAK_EVEN = "BREAK_EVEN"
    NONE = "NONE"


@dataclass(frozen=True)
class CostModel:
    """Everything a round trip costs, in basis points.

    Separated from the barrier config because it belongs to the venue, not to the
    strategy: changing `k_tp` is a decision, changing the taker fee is a fact
    about where you are trading.
    """

    entry_fee_bps: float = BINANCE_USDM_TAKER
    exit_fee_bps: float = BINANCE_USDM_TAKER
    spread_bps: float = 1.0
    slippage_bps: float = 0.5

    def __post_init__(self) -> None:
        for name in ("entry_fee_bps", "exit_fee_bps", "spread_bps", "slippage_bps"):
            v = getattr(self, name)
            if not math.isfinite(v) or v < 0:
                raise BarrierError(f"{name} must be finite and >= 0, got {v}")

    @property
    def round_trip_bps(self) -> float:
        """What a position pays just to open and close, before any price move."""
        return self.entry_fee_bps + self.exit_fee_bps + self.spread_bps + self.slippage_bps

    @classmethod
    def maker_rebate(cls, rebate_bps: float = BINANCE_USDM_MAKER) -> "CostModel":
        """Both legs as resting limit orders. The single biggest lever on `c`.

        Halving the fee halves the cost term, which moves the minimum viable
        barrier by more than any amount of signal work. It costs queue risk: a
        limit order that does not fill is a trade that did not happen, and on the
        exit leg it is a stop that did not protect.
        """
        return cls(entry_fee_bps=rebate_bps, exit_fee_bps=rebate_bps,
                   spread_bps=0.0, slippage_bps=0.25)


@dataclass(frozen=True)
class BarrierConfig:
    """Triple Barrier placement, in multiples of ATR.

    `max_holding_bars` is the vertical barrier: the position closes at that point
    whatever the price, which is what stops a losing idea from becoming a
    long-term investment.
    """

    k_tp: float = 1.5
    k_sl: float = 1.0
    max_holding_bars: int = 15
    #: Move the stop to entry-plus-costs once this fraction of the way to TP is
    #: covered. Zero disables it.
    break_even_at: float = 0.40
    #: Minimum profit, in bps, required before the time barrier closes a winner.
    time_exit_min_profit_bps: float = 5.0

    def __post_init__(self) -> None:
        if self.k_tp <= 0 or self.k_sl <= 0:
            raise BarrierError("k_tp and k_sl must be > 0")
        if self.max_holding_bars < 1:
            raise BarrierError("max_holding_bars must be >= 1")
        if not 0.0 <= self.break_even_at <= 1.0:
            raise BarrierError("break_even_at must lie in [0, 1]")

    def tp_bps(self, atr_pct: float) -> float:
        return self.k_tp * atr_pct * 100.0

    def sl_bps(self, atr_pct: float) -> float:
        return self.k_sl * atr_pct * 100.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "k_tp": self.k_tp,
            "k_sl": self.k_sl,
            "max_holding_bars": self.max_holding_bars,
            "break_even_at": self.break_even_at,
            "time_exit_min_profit_bps": self.time_exit_min_profit_bps,
        }


# ---------------------------------------------------------------------------
# The economics
# ---------------------------------------------------------------------------


def breakeven_win_rate(tp_bps: float, sl_bps: float, cost_bps: float) -> float:
    """p* = (sl + c) / (tp + sl). Returns > 1.0 when no win rate suffices.

    Deliberately NOT clamped to 1.0. A p* of 1.43 is a different situation from
    a p* of 1.00 and the caller deserves to see which one it is in.
    """
    if tp_bps <= 0 or sl_bps <= 0:
        raise BarrierError("tp_bps and sl_bps must be > 0")
    if cost_bps < 0:
        raise BarrierError("cost_bps must be >= 0")
    return (sl_bps + cost_bps) / (tp_bps + sl_bps)


def minimum_viable_k_tp(
    *, atr_pct: float, k_sl: float, cost_bps: float, target_win_rate: float = 0.50
) -> float:
    """Smallest profit barrier at which `target_win_rate` breaks even.

    Rearranging p* = (sl + c)/(tp + sl) for tp:

        tp = (sl + c) / p  -  sl

    At p = 0.5 this is tp = sl + 2c: the profit barrier has to clear the stop
    *plus twice the round trip*. With c = 9 bps and a 6 bps stop that is 24 bps,
    or four times ATR - not the 1.5x that looks natural when reading a chart.
    """
    if not 0.0 < target_win_rate <= 1.0:
        raise BarrierError("target_win_rate must lie in (0, 1]")
    if atr_pct <= 0:
        raise BarrierError("atr_pct must be > 0")
    sl_bps = k_sl * atr_pct * 100.0
    tp_bps = (sl_bps + cost_bps) / target_win_rate - sl_bps
    return tp_bps / (atr_pct * 100.0)


@dataclass(frozen=True)
class BarrierEconomics:
    """One placement, costed, with the verdict attached."""

    atr_pct: float
    tp_bps: float
    sl_bps: float
    cost_bps: float
    breakeven_p: float
    measured_p: Optional[float] = None

    @property
    def viable(self) -> bool:
        """Is there any win rate below 100% that breaks even?"""
        return self.breakeven_p < 1.0

    @property
    def cost_to_atr_ratio(self) -> float:
        """Round-trip cost as a multiple of one bar's average range.

        Above 1.0 means a round trip costs more than the market typically moves
        in a bar, which is the clearest single statement of why scalping that
        timeframe does not work at these fees.
        """
        return self.cost_bps / max(1e-9, self.atr_pct * 100.0)

    def expected_value_bps(self, p: float) -> float:
        return p * (self.tp_bps + self.sl_bps) - (self.sl_bps + self.cost_bps)

    @property
    def verdict(self) -> str:
        if not self.viable:
            return (
                f"NOT VIABLE: break-even needs a {self.breakeven_p * 100:.1f}% win rate. "
                f"The round trip costs {self.cost_to_atr_ratio:.2f}x the average bar range, "
                f"so the profit barrier sits inside the cost band."
            )
        if self.measured_p is None:
            return (
                f"VIABLE IF you can win {self.breakeven_p * 100:.1f}% of the time. "
                f"That is a precondition, not a forecast - measure it."
            )
        if self.measured_p >= self.breakeven_p:
            return (
                f"CLEARS: measured {self.measured_p * 100:.1f}% win rate against a "
                f"{self.breakeven_p * 100:.1f}% break-even, "
                f"{self.expected_value_bps(self.measured_p):+.2f} bps/trade"
            )
        return (
            f"SHORT BY {(self.breakeven_p - self.measured_p) * 100:.1f} POINTS: measured "
            f"{self.measured_p * 100:.1f}% against a {self.breakeven_p * 100:.1f}% "
            f"break-even, {self.expected_value_bps(self.measured_p):+.2f} bps/trade"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "atr_pct": round(self.atr_pct, 5),
            "tp_bps": round(self.tp_bps, 3),
            "sl_bps": round(self.sl_bps, 3),
            "cost_bps": round(self.cost_bps, 3),
            "cost_to_atr_ratio": round(self.cost_to_atr_ratio, 3),
            "breakeven_win_rate": round(self.breakeven_p, 5),
            "measured_win_rate": self.measured_p,
            "viable": self.viable,
            "expected_value_bps_at_measured": (
                round(self.expected_value_bps(self.measured_p), 3)
                if self.measured_p is not None else None
            ),
            "verdict": self.verdict,
        }


def evaluate(
    config: BarrierConfig,
    *,
    atr_pct: float,
    costs: Optional[CostModel] = None,
    measured_win_rate: Optional[float] = None,
) -> BarrierEconomics:
    c = (costs or CostModel()).round_trip_bps
    return BarrierEconomics(
        atr_pct=atr_pct,
        tp_bps=config.tp_bps(atr_pct),
        sl_bps=config.sl_bps(atr_pct),
        cost_bps=c,
        breakeven_p=breakeven_win_rate(config.tp_bps(atr_pct), config.sl_bps(atr_pct), c),
        measured_p=measured_win_rate,
    )


def sweep(
    *,
    atr_pct: float,
    k_sl: float = 1.0,
    k_tp_values: Iterable[float] = (1.0, 1.5, 2.0, 3.0, 4.0, 6.0, 10.0, 20.0),
    costs: Optional[CostModel] = None,
    measured_win_rate: Optional[float] = None,
) -> list[BarrierEconomics]:
    """Tabulate break-even across profit barriers, so the trade-off is visible."""
    return [
        evaluate(BarrierConfig(k_tp=k, k_sl=k_sl), atr_pct=atr_pct, costs=costs,
                 measured_win_rate=measured_win_rate)
        for k in k_tp_values
    ]


# ---------------------------------------------------------------------------
# The barrier itself
# ---------------------------------------------------------------------------


@dataclass
class TripleBarrier:
    """A live position's three exits, plus the break-even lock.

    Prices are integer cents throughout, matching the rest of the ledger. The
    barriers are fixed at entry from the ATR observed *then*: recomputing them as
    ATR moves would let a position widen its own stop in a selloff, which is the
    single most common way a stop stops being a stop.
    """

    side: str                       # "LONG" | "SHORT"
    entry_cents: int
    tp_cents: int
    sl_cents: int
    entry_bar: int
    max_holding_bars: int
    break_even_cents: Optional[int] = None
    break_even_trigger_cents: Optional[int] = None
    break_even_armed: bool = False
    time_exit_min_profit_bps: float = 5.0

    def __post_init__(self) -> None:
        if self.side not in ("LONG", "SHORT"):
            raise BarrierError(f"side must be LONG or SHORT, got {self.side!r}")
        for name in ("entry_cents", "tp_cents", "sl_cents"):
            v = getattr(self, name)
            if not isinstance(v, int) or isinstance(v, bool) or v <= 0:
                raise BarrierError(f"{name} must be a positive int of cents, got {v!r}")
        if self.side == "LONG" and not (self.sl_cents < self.entry_cents < self.tp_cents):
            raise BarrierError("a LONG needs sl < entry < tp")
        if self.side == "SHORT" and not (self.tp_cents < self.entry_cents < self.sl_cents):
            raise BarrierError("a SHORT needs tp < entry < sl")

    @classmethod
    def from_atr(
        cls,
        *,
        side: str,
        entry_cents: int,
        atr_cents: int,
        entry_bar: int,
        config: Optional[BarrierConfig] = None,
        costs: Optional[CostModel] = None,
    ) -> "TripleBarrier":
        cfg = config or BarrierConfig()
        cost_bps = (costs or CostModel()).round_trip_bps
        tp_move = max(1, int(round(cfg.k_tp * atr_cents)))
        sl_move = max(1, int(round(cfg.k_sl * atr_cents)))
        # Break-even is entry plus the cost of getting out, not entry: a stop at
        # exactly entry still loses the round trip, which is the mistake the
        # phrase "risk-free trade" is usually hiding.
        cost_move = max(1, int(round(entry_cents * cost_bps / 10_000.0)))
        trigger_move = int(round(tp_move * cfg.break_even_at)) if cfg.break_even_at else 0

        if side == "LONG":
            tp, sl = entry_cents + tp_move, max(1, entry_cents - sl_move)
            be = entry_cents + cost_move
            trig = entry_cents + trigger_move if trigger_move else None
        else:
            tp, sl = max(1, entry_cents - tp_move), entry_cents + sl_move
            be = max(1, entry_cents - cost_move)
            trig = max(1, entry_cents - trigger_move) if trigger_move else None

        return cls(
            side=side, entry_cents=entry_cents, tp_cents=tp, sl_cents=sl,
            entry_bar=entry_bar, max_holding_bars=cfg.max_holding_bars,
            break_even_cents=be, break_even_trigger_cents=trig,
            time_exit_min_profit_bps=cfg.time_exit_min_profit_bps,
        )

    # -- state -------------------------------------------------------------

    def pnl_bps(self, mark_cents: int) -> float:
        if self.side == "LONG":
            return (mark_cents - self.entry_cents) / self.entry_cents * 10_000.0
        return (self.entry_cents - mark_cents) / self.entry_cents * 10_000.0

    def progress_to_tp(self, mark_cents: int) -> float:
        span = abs(self.tp_cents - self.entry_cents)
        if span <= 0:
            return 0.0
        return max(0.0, min(1.0, abs(mark_cents - self.entry_cents) / span
                            if self.pnl_bps(mark_cents) > 0 else 0.0))

    def arm_break_even(self, mark_cents: int) -> bool:
        """Ratchet the stop to entry+cost once the trigger is passed. One-way.

        Never widens: `arm_break_even` only ever moves the stop toward the entry,
        and only once. A stop that can move outward under any condition is not a
        stop, it is a suggestion with a timer.
        """
        if self.break_even_armed or self.break_even_trigger_cents is None:
            return False
        hit = (mark_cents >= self.break_even_trigger_cents if self.side == "LONG"
               else mark_cents <= self.break_even_trigger_cents)
        if not hit or self.break_even_cents is None:
            return False
        improved = (self.break_even_cents > self.sl_cents if self.side == "LONG"
                    else self.break_even_cents < self.sl_cents)
        if improved:
            self.sl_cents = self.break_even_cents
        self.break_even_armed = True
        return improved

    def check(self, mark_cents: int, bar: int) -> BarrierTouch:
        """Which barrier, if any, this mark has touched.

        Stop before target, deliberately: a bar that spans both is a bar whose
        internal order is unknown, and assuming the worse of the two is the only
        assumption that does not flatter the backtest.
        """
        self.arm_break_even(mark_cents)
        if self.side == "LONG":
            if mark_cents <= self.sl_cents:
                return BarrierTouch.BREAK_EVEN if self.break_even_armed else BarrierTouch.STOP_LOSS
            if mark_cents >= self.tp_cents:
                return BarrierTouch.TAKE_PROFIT
        else:
            if mark_cents >= self.sl_cents:
                return BarrierTouch.BREAK_EVEN if self.break_even_armed else BarrierTouch.STOP_LOSS
            if mark_cents <= self.tp_cents:
                return BarrierTouch.TAKE_PROFIT
        if bar - self.entry_bar >= self.max_holding_bars:
            return BarrierTouch.TIME
        return BarrierTouch.NONE

    def as_dict(self, mark_cents: Optional[int] = None) -> dict[str, Any]:
        d = {
            "side": self.side,
            "entry_cents": self.entry_cents,
            "tp_cents": self.tp_cents,
            "sl_cents": self.sl_cents,
            "entry_bar": self.entry_bar,
            "max_holding_bars": self.max_holding_bars,
            "break_even_cents": self.break_even_cents,
            "break_even_armed": self.break_even_armed,
        }
        if mark_cents is not None:
            d["mark_cents"] = mark_cents
            d["pnl_bps"] = round(self.pnl_bps(mark_cents), 3)
            d["progress_to_tp"] = round(self.progress_to_tp(mark_cents), 4)
        return d


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------


def render_report(
    *,
    atr_pct: float = 0.06,
    k_sl: float = 1.0,
    costs: Optional[CostModel] = None,
    measured_win_rate: Optional[float] = None,
    current_tp_pct: float = 1.2,
    current_sl_pct: float = 1.0,
) -> str:
    c = costs or CostModel()
    rt = c.round_trip_bps
    lines: list[str] = []
    add = lines.append
    add("=" * 78)
    add(" BARRIER ECONOMICS - can these exits pay for themselves?")
    add("=" * 78)
    add(f" ATR (1 bar)          : {atr_pct:.4f}%  = {atr_pct * 100:.2f} bps")
    add(f" round-trip cost      : {rt:.2f} bps"
        f"   (fees {c.entry_fee_bps + c.exit_fee_bps:.1f} + spread {c.spread_bps:.1f}"
        f" + slippage {c.slippage_bps:.1f})")
    add(f" cost / ATR           : {rt / max(1e-9, atr_pct * 100):.2f}x")
    if rt > atr_pct * 100:
        add("   -> a round trip costs MORE than the market typically moves in a bar.")
    add("-" * 78)

    add(" p* is the win rate needed just to break even:  p* = (sl + c) / (tp + sl)")
    add("")
    add(f"   {'k_tp':>6}  {'tp bps':>8}  {'sl bps':>8}  {'p* needed':>10}   verdict")
    for e in sweep(atr_pct=atr_pct, k_sl=k_sl, costs=c, measured_win_rate=measured_win_rate):
        k = e.tp_bps / max(1e-9, atr_pct * 100.0)
        mark = "impossible" if not e.viable else f"{e.breakeven_p * 100:.1f}%"
        add(f"   {k:>6.1f}  {e.tp_bps:>8.1f}  {e.sl_bps:>8.1f}  {mark:>10}"
            + ("   <- proposed" if abs(k - 1.5) < 0.01 else ""))
    add("-" * 78)

    for target in (0.50, 0.55, 0.60):
        k = minimum_viable_k_tp(atr_pct=atr_pct, k_sl=k_sl, cost_bps=rt, target_win_rate=target)
        add(f" to break even at a {target * 100:.0f}% win rate you need k_tp >= {k:.2f}"
            f"  (tp {k * atr_pct * 100:.1f} bps)")
    add("-" * 78)

    cur_tp, cur_sl = current_tp_pct * 100.0, current_sl_pct * 100.0
    p_cur = breakeven_win_rate(cur_tp, cur_sl, rt)
    add(f" the barriers currently shipped: tp {current_tp_pct:.2f}% / sl {current_sl_pct:.2f}%")
    add(f"   p* = {p_cur * 100:.1f}%   -> {'viable' if p_cur < 1 else 'not viable'}")
    add(f"   but at {atr_pct * 100:.1f} bps of range per bar, {cur_tp:.0f} bps is roughly "
        f"{cur_tp / max(1e-9, atr_pct * 100):.0f} bars of pure drift away.")
    add("   The complaint that they are slow is right. Moving them inside the cost")
    add("   band is not the fix; holding for the horizon they imply is.")
    add("-" * 78)

    cheap = CostModel.maker_rebate()
    add(f" if both legs were maker fills ({cheap.round_trip_bps:.2f} bps round trip):")
    for target in (0.50, 0.55):
        k = minimum_viable_k_tp(atr_pct=atr_pct, k_sl=k_sl,
                                cost_bps=cheap.round_trip_bps, target_win_rate=target)
        add(f"   k_tp >= {k:.2f} for a {target * 100:.0f}% win rate"
            f"  (vs {minimum_viable_k_tp(atr_pct=atr_pct, k_sl=k_sl, cost_bps=rt, target_win_rate=target):.2f} as a taker)")
    add("   Fee tier moves the minimum barrier further than any signal work does.")
    if measured_win_rate is not None:
        add("-" * 78)
        add(f" measured win rate    : {measured_win_rate * 100:.2f}% "
            f"(benchmarks/edge_report.py, out of sample)")
        best = minimum_viable_k_tp(atr_pct=atr_pct, k_sl=k_sl, cost_bps=rt,
                                   target_win_rate=max(0.01, measured_win_rate))
        add(f" k_tp needed for THAT win rate to break even: {best:.2f}"
            f"  (tp {best * atr_pct * 100:.1f} bps)")
    add("=" * 78)
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Self-test and CLI
# ---------------------------------------------------------------------------


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

    def t_breakeven_formula() -> None:
        # tp 120, sl 100, c 9  ->  109/220
        assert abs(breakeven_win_rate(120, 100, 9) - 0.49545) < 1e-4

    def t_the_proposed_scale_needs_certainty() -> None:
        # atr 6 bps, k_tp 1.5, k_sl 1.0, c 9  ->  tp 9, sl 6  ->  15/15 = 1.0
        e = evaluate(BarrierConfig(k_tp=1.5, k_sl=1.0), atr_pct=0.06,
                     costs=CostModel(spread_bps=1.0, slippage_bps=0.0))
        assert abs(e.breakeven_p - 1.0) < 1e-9, e.breakeven_p
        assert not e.viable

    def t_shrinking_barriers_raises_the_bar() -> None:
        wide = evaluate(BarrierConfig(k_tp=6.0), atr_pct=0.06)
        tight = evaluate(BarrierConfig(k_tp=1.5), atr_pct=0.06)
        assert tight.breakeven_p > wide.breakeven_p

    def t_zero_cost_makes_symmetric_barriers_a_coin_flip() -> None:
        assert abs(breakeven_win_rate(10, 10, 0) - 0.5) < 1e-12

    def t_minimum_viable_inverts_the_formula() -> None:
        k = minimum_viable_k_tp(atr_pct=0.06, k_sl=1.0, cost_bps=9.0, target_win_rate=0.5)
        e = evaluate(BarrierConfig(k_tp=k, k_sl=1.0), atr_pct=0.06,
                     costs=CostModel(entry_fee_bps=4.5, exit_fee_bps=4.5,
                                     spread_bps=0.0, slippage_bps=0.0))
        assert abs(e.breakeven_p - 0.5) < 1e-6, e.breakeven_p

    def t_at_a_coin_flip_tp_is_sl_plus_twice_cost() -> None:
        k = minimum_viable_k_tp(atr_pct=0.06, k_sl=1.0, cost_bps=9.0, target_win_rate=0.5)
        assert abs(k * 6.0 - (6.0 + 18.0)) < 1e-6, k * 6.0

    def t_maker_fills_lower_the_bar() -> None:
        taker = minimum_viable_k_tp(atr_pct=0.06, k_sl=1.0,
                                    cost_bps=CostModel().round_trip_bps)
        maker = minimum_viable_k_tp(atr_pct=0.06, k_sl=1.0,
                                    cost_bps=CostModel.maker_rebate().round_trip_bps)
        assert maker < taker

    def t_cost_to_atr_ratio() -> None:
        e = evaluate(BarrierConfig(), atr_pct=0.06,
                     costs=CostModel(spread_bps=1.0, slippage_bps=0.0))
        assert abs(e.cost_to_atr_ratio - 1.5) < 1e-9

    def t_ev_is_zero_at_breakeven() -> None:
        e = evaluate(BarrierConfig(k_tp=20.0), atr_pct=0.06)
        assert abs(e.expected_value_bps(e.breakeven_p)) < 1e-9

    def t_bad_configs_refused() -> None:
        for kw in ({"k_tp": 0}, {"k_sl": -1}, {"max_holding_bars": 0}, {"break_even_at": 2.0}):
            try:
                BarrierConfig(**kw)
            except BarrierError:
                continue
            raise AssertionError(f"{kw} was accepted")

    def t_long_barrier_ordering() -> None:
        b = TripleBarrier.from_atr(side="LONG", entry_cents=6_412_500, atr_cents=4_000, entry_bar=0)
        assert b.sl_cents < b.entry_cents < b.tp_cents

    def t_short_barrier_ordering() -> None:
        b = TripleBarrier.from_atr(side="SHORT", entry_cents=6_412_500, atr_cents=4_000, entry_bar=0)
        assert b.tp_cents < b.entry_cents < b.sl_cents

    def t_inverted_barriers_refused() -> None:
        try:
            TripleBarrier("LONG", 100, tp_cents=50, sl_cents=150, entry_bar=0, max_holding_bars=5)
        except BarrierError:
            return
        raise AssertionError("an inverted LONG barrier was accepted")

    def t_take_profit_fires() -> None:
        b = TripleBarrier.from_atr(side="LONG", entry_cents=100_000, atr_cents=1_000, entry_bar=0)
        assert b.check(b.tp_cents, 1) is BarrierTouch.TAKE_PROFIT

    def t_stop_fires_before_target() -> None:
        b = TripleBarrier.from_atr(side="LONG", entry_cents=100_000, atr_cents=1_000, entry_bar=0)
        assert b.check(b.sl_cents, 1) is BarrierTouch.STOP_LOSS

    def t_time_barrier_closes_a_drifting_position() -> None:
        b = TripleBarrier.from_atr(side="LONG", entry_cents=100_000, atr_cents=1_000, entry_bar=0,
                                   config=BarrierConfig(max_holding_bars=15))
        assert b.check(100_010, 14) is BarrierTouch.NONE
        assert b.check(100_010, 15) is BarrierTouch.TIME

    def t_break_even_arms_and_ratchets() -> None:
        b = TripleBarrier.from_atr(side="LONG", entry_cents=100_000, atr_cents=1_000, entry_bar=0,
                                   config=BarrierConfig(k_tp=4.0, break_even_at=0.4))
        before = b.sl_cents
        assert b.arm_break_even(b.break_even_trigger_cents)
        assert b.sl_cents > before
        assert b.break_even_armed

    def t_break_even_never_widens_the_stop() -> None:
        b = TripleBarrier.from_atr(side="LONG", entry_cents=100_000, atr_cents=1_000, entry_bar=0,
                                   config=BarrierConfig(k_tp=4.0))
        b.arm_break_even(b.break_even_trigger_cents)
        held = b.sl_cents
        b.break_even_armed = False
        b.arm_break_even(b.break_even_trigger_cents)
        assert b.sl_cents >= held

    def t_break_even_sits_above_entry_not_at_it() -> None:
        b = TripleBarrier.from_atr(side="LONG", entry_cents=6_412_500, atr_cents=4_000, entry_bar=0)
        assert b.break_even_cents > b.entry_cents, "a stop at entry still loses the round trip"

    def t_report_renders() -> None:
        text = render_report(atr_pct=0.06, measured_win_rate=0.4226)
        assert "impossible" in text and "cost / ATR" in text

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    print(f"barrier_planner self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Triple Barrier placement and its economics")
    ap.add_argument("--atr-pct", type=float, default=0.06,
                    help="ATR of one bar as a percentage of price (BTC 1m is 0.04-0.09)")
    ap.add_argument("--k-sl", type=float, default=1.0)
    ap.add_argument("--taker-bps", type=float, default=BINANCE_USDM_TAKER)
    ap.add_argument("--spread-bps", type=float, default=1.0)
    ap.add_argument("--slippage-bps", type=float, default=0.5)
    ap.add_argument("--win-rate", type=float, default=None,
                    help="measured win rate, e.g. 0.4226 from bench/edge_report.json")
    ap.add_argument("--maker", action="store_true", help="cost both legs as maker fills")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    costs = (CostModel.maker_rebate() if args.maker else
             CostModel(entry_fee_bps=args.taker_bps, exit_fee_bps=args.taker_bps,
                       spread_bps=args.spread_bps, slippage_bps=args.slippage_bps))

    wr = args.win_rate
    if wr is None:
        try:
            from pathlib import Path  # noqa: PLC0415
            blob = json.loads(
                (Path(__file__).resolve().parent.parent / "bench" / "edge_report.json")
                .read_text(encoding="utf-8"))
            wr = float(blob.get("win_rate")) or None
        except Exception:  # noqa: BLE001 - the report is optional
            wr = None

    if args.sweep or args.json:
        rows = [e.as_dict() for e in sweep(atr_pct=args.atr_pct, k_sl=args.k_sl,
                                           costs=costs, measured_win_rate=wr)]
        if args.json:
            print(json.dumps({"costs": costs.round_trip_bps, "rows": rows}, indent=2))
            return 0
        for r in rows:
            print(f"  tp {r['tp_bps']:>7.1f} bps   sl {r['sl_bps']:>6.1f} bps   "
                  f"p* {r['breakeven_win_rate'] * 100:>6.1f}%   {r['verdict']}")
        return 0

    print(render_report(atr_pct=args.atr_pct, k_sl=args.k_sl, costs=costs,
                        measured_win_rate=wr))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
