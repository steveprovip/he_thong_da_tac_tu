#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
position_sizer.py - Kelly, computed from what happened rather than from what was hoped.

The Kelly criterion answers one question: given a known edge and a known payoff
ratio, what fraction of capital maximises the long-run growth rate?

    f* = (p * b - q) / b        p = win probability, q = 1 - p, b = win/loss ratio

Two properties of that formula do all the work here, and both are routinely lost
in translation:

**f* is negative when the edge is negative.** With p = 0.42 and b = 1.0,
f* = (0.42 - 0.58) / 1.0 = -0.16. Kelly is not saying "bet 16% carefully". It is
saying bet the other side, and if you cannot, bet nothing. `kelly_fraction`
returns the negative number and `PositionSizer` turns it into a size of zero,
because a floor like `max(0.02, ...)` under a negative f* is a guarantee of
betting into a losing edge on every single trade.

**p must be measured.** A proposed alternative was to derive it from the
consensus score - `p = score / 100 * 0.8`, so a 78% vote becomes p = 0.62. That
substitutes the swarm's confidence in itself for evidence about its accuracy, and
those are different quantities: the measured win rate over 575 out-of-sample
trades is 42.3% while the average consensus on those same trades was above 80%.
Sizing on the second number while the first one is true is how an account
compounds downward at the fastest available rate. `PositionSizer.from_trades`
takes the realised ledger; `consensus_score` is accepted only as a *tilt* within
what the measurement already permits, never as the source of p.

Fractional Kelly
----------------
Full Kelly maximises growth and has brutal drawdowns - roughly a 1-in-3 chance of
halving the account before doubling it, and that is with p and b known exactly.
They are not known exactly here; they are estimated from a few hundred trades
with wide error bars. Thorp's answer, and the industry's, is to bet a fraction:
`lam = 0.25` by default. Quarter Kelly gives up about 44% of the growth rate for
roughly a quarter of the variance, and it is far more forgiving of an
overestimated p - which, given how p is usually estimated, is the failure that
actually happens.

CLI:
    python core/position_sizer.py --selftest
    python core/position_sizer.py --report
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

__all__ = [
    "SizerError",
    "KellyStats",
    "SizeDecision",
    "PositionSizer",
    "kelly_fraction",
]

REPO_ROOT = Path(__file__).resolve().parent.parent


class SizerError(ValueError):
    """A sizing input that cannot be used."""


def kelly_fraction(p: float, b: float) -> float:
    """f* = (p*b - q)/b. Returns the raw value, negative edges included."""
    if not 0.0 <= p <= 1.0:
        raise SizerError(f"p must lie in [0, 1], got {p}")
    if b <= 0:
        raise SizerError(f"b (win/loss ratio) must be > 0, got {b}")
    return (p * b - (1.0 - p)) / b


@dataclass(frozen=True)
class KellyStats:
    """Realised trade statistics. Every field comes from closed trades."""

    wins: int
    losses: int
    avg_win_bps: float
    avg_loss_bps: float

    def __post_init__(self) -> None:
        if self.wins < 0 or self.losses < 0:
            raise SizerError("trade counts must be >= 0")
        if self.avg_win_bps < 0 or self.avg_loss_bps < 0:
            raise SizerError("average magnitudes must be >= 0 (avg_loss_bps is unsigned)")

    @property
    def n(self) -> int:
        return self.wins + self.losses

    @property
    def win_rate(self) -> float:
        return self.wins / self.n if self.n else 0.0

    @property
    def payoff_ratio(self) -> float:
        """b. Falls back to 1.0 when there are no losses yet to measure against."""
        return self.avg_win_bps / self.avg_loss_bps if self.avg_loss_bps > 0 else 1.0

    @property
    def expectancy_bps(self) -> float:
        return self.win_rate * self.avg_win_bps - (1.0 - self.win_rate) * self.avg_loss_bps

    @property
    def kelly_f(self) -> float:
        return kelly_fraction(self.win_rate, self.payoff_ratio)

    @property
    def standard_error_bps(self) -> Optional[float]:
        """Rough SE of expectancy, so `n` can be judged rather than assumed."""
        if self.n < 2:
            return None
        spread = max(self.avg_win_bps, self.avg_loss_bps)
        return spread / math.sqrt(self.n)

    @classmethod
    def from_trades(cls, returns_bps: Sequence[float]) -> "KellyStats":
        wins = [r for r in returns_bps if r > 0]
        losses = [-r for r in returns_bps if r < 0]
        return cls(
            wins=len(wins),
            losses=len(losses),
            avg_win_bps=(sum(wins) / len(wins)) if wins else 0.0,
            avg_loss_bps=(sum(losses) / len(losses)) if losses else 0.0,
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "n": self.n,
            "wins": self.wins,
            "losses": self.losses,
            "win_rate": round(self.win_rate, 5),
            "avg_win_bps": round(self.avg_win_bps, 3),
            "avg_loss_bps": round(self.avg_loss_bps, 3),
            "payoff_ratio_b": round(self.payoff_ratio, 4),
            "expectancy_bps": round(self.expectancy_bps, 4),
            "kelly_f": round(self.kelly_f, 5),
            "standard_error_bps": (
                round(self.standard_error_bps, 3) if self.standard_error_bps else None),
        }


@dataclass(frozen=True)
class SizeDecision:
    """How much to bet, and the reason - including when the reason is "nothing"."""

    fraction: float
    notional_cents: int
    quantity_units: int
    stats: Optional[KellyStats]
    reason: str
    raw_kelly_f: float = 0.0

    @property
    def trades(self) -> bool:
        return self.quantity_units > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "fraction": round(self.fraction, 5),
            "notional_cents": self.notional_cents,
            "quantity_units": self.quantity_units,
            "raw_kelly_f": round(self.raw_kelly_f, 5),
            "trades": self.trades,
            "reason": self.reason,
            "stats": self.stats.as_dict() if self.stats else None,
        }


@dataclass
class PositionSizer:
    """Fractional Kelly with a hard floor of zero and a hard cap."""

    lam: float = 0.25                 # quarter Kelly
    max_fraction: float = 0.15
    min_trades: int = 30              # below this, p is not estimated, it is guessed
    warmup_fraction: float = 0.01     # what to risk while still measuring
    qty_scale_pow10: int = 6

    def __post_init__(self) -> None:
        if not 0.0 < self.lam <= 1.0:
            raise SizerError("lam must lie in (0, 1]")
        if not 0.0 < self.max_fraction <= 1.0:
            raise SizerError("max_fraction must lie in (0, 1]")
        if self.min_trades < 1:
            raise SizerError("min_trades must be >= 1")
        if not 0.0 <= self.warmup_fraction <= self.max_fraction:
            raise SizerError("warmup_fraction must lie in [0, max_fraction]")

    def size(
        self,
        *,
        equity_cents: int,
        price_cents: int,
        stats: Optional[KellyStats] = None,
        consensus_score: Optional[float] = None,
    ) -> SizeDecision:
        """Fractional Kelly, or zero.

        `consensus_score` is a tilt inside what the measurement already allows:
        it can scale the fraction down toward the warm-up size on a weak vote, and
        it can never scale it above what f* permits. Confidence is not evidence.
        """
        if not isinstance(equity_cents, int) or isinstance(equity_cents, bool):
            raise SizerError("equity_cents must be an int")
        if not isinstance(price_cents, int) or isinstance(price_cents, bool):
            raise SizerError("price_cents must be an int")
        if equity_cents <= 0 or price_cents <= 0:
            raise SizerError("equity_cents and price_cents must be > 0")

        if stats is None or stats.n < self.min_trades:
            n = stats.n if stats else 0
            frac = self.warmup_fraction
            reason = (
                f"only {n} closed trades against a {self.min_trades}-trade minimum; "
                f"sizing at the {frac * 100:.2f}% warm-up fraction while the win rate "
                f"is still being measured rather than assumed"
            )
            raw = stats.kelly_f if stats else 0.0
        else:
            raw = stats.kelly_f
            if raw <= 0.0:
                return SizeDecision(
                    0.0, 0, 0, stats, raw_kelly_f=raw,
                    reason=(
                        f"Kelly is {raw:+.4f} on a measured {stats.win_rate * 100:.1f}% win "
                        f"rate at b = {stats.payoff_ratio:.2f} "
                        f"({stats.expectancy_bps:+.2f} bps expectancy). A negative f* means "
                        f"the optimal bet is zero - a minimum size here would guarantee "
                        f"betting into a losing edge every trade."
                    ),
                )
            frac = min(self.max_fraction, raw * self.lam)
            reason = (
                f"f* = {raw:.4f} on {stats.n} trades, {self.lam:g}x Kelly = {frac * 100:.2f}% "
                f"of equity (capped at {self.max_fraction * 100:.0f}%)"
            )

        if consensus_score is not None:
            tilt = max(0.0, min(1.0, (consensus_score - 50.0) / 50.0))
            frac *= tilt
            reason += f"; scaled by consensus tilt {tilt:.2f} (score {consensus_score:.1f}%)"

        notional = int(equity_cents * frac)
        step = 10 ** self.qty_scale_pow10
        qty_units = int((notional * step) // price_cents)
        if qty_units <= 0:
            return SizeDecision(frac, notional, 0, stats, raw_kelly_f=raw,
                                reason=reason + "; rounds to zero at this price")
        return SizeDecision(frac, notional, qty_units, stats, raw_kelly_f=raw, reason=reason)

    @classmethod
    def from_market(cls, market: Any, **kw: Any) -> tuple["PositionSizer", KellyStats]:
        """Build a sizer and the stats from a market's closed-trade ledger."""
        rets: list[float] = []
        for t in getattr(market, "closed", []):
            committed = t.entry_price_cents * t.qty
            if committed > 0:
                rets.append(t.net_pnl_cents / committed * 10_000.0)
        return cls(**kw), KellyStats.from_trades(rets)


# --------------------------------------------------------------------------- #
# Reporting                                                                    #
# --------------------------------------------------------------------------- #

def render_report(
    *,
    equity_cents: int,
    price_cents: int,
    stats: KellyStats,
    sizer: Optional["PositionSizer"] = None,
    consensus_score: Optional[float] = None,
) -> str:
    """Human-readable sizing verdict, including the counterfactual sizes."""
    sizer = sizer or PositionSizer()
    d = sizer.size(
        equity_cents=equity_cents,
        price_cents=price_cents,
        stats=stats,
        consensus_score=consensus_score,
    )
    lines: list[str] = []
    lines.append("=" * 74)
    lines.append("FRACTIONAL KELLY POSITION SIZE")
    lines.append("=" * 74)
    lines.append(f"  equity                 ${equity_cents / 100:,.2f}")
    lines.append(f"  price                  ${price_cents / 100:,.2f}")
    lines.append("")
    lines.append("  measured statistics (closed trades only)")
    lines.append(f"    trades               {stats.n}  ({stats.wins}W / {stats.losses}L)")
    lines.append(f"    win rate p           {stats.win_rate * 100:.2f}%")
    lines.append(f"    avg win              {stats.avg_win_bps:+.2f} bps")
    lines.append(f"    avg loss             {-stats.avg_loss_bps:+.2f} bps")
    lines.append(f"    payoff ratio b       {stats.payoff_ratio:.4f}")
    lines.append(f"    expectancy           {stats.expectancy_bps:+.2f} bps/trade")
    se = stats.standard_error_bps
    if se:
        lines.append(f"    std error            {se:.2f} bps  "
                     f"(t = {stats.expectancy_bps / se:+.2f})")
    lines.append("")
    lines.append(f"  full Kelly f*          {stats.kelly_f:+.4f}")
    lines.append(f"  fractional ({sizer.lam:g}x)      "
                 f"{max(0.0, stats.kelly_f) * sizer.lam:+.4f}")
    lines.append(f"  cap                    {sizer.max_fraction:.4f}")
    if consensus_score is not None:
        lines.append(f"  consensus tilt         {consensus_score:.2f}%")
    lines.append("")
    lines.append(f"  SIZE                   {d.fraction * 100:.3f}% of equity")
    lines.append(f"                         ${d.notional_cents / 100:,.2f} notional")
    lines.append(f"                         {d.quantity_units / 10 ** sizer.qty_scale_pow10:.6f} units")
    lines.append(f"  verdict                {'TRADE' if d.trades else 'NO TRADE'}")
    lines.append("")
    for chunk in _wrap(d.reason, 68):
        lines.append(f"    {chunk}")
    lines.append("=" * 74)
    return "\n".join(lines)


def _wrap(text: str, width: int) -> list[str]:
    out: list[str] = []
    line = ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    if line:
        out.append(line)
    return out


# --------------------------------------------------------------------------- #
# Self-test                                                                    #
# --------------------------------------------------------------------------- #

def _selftest() -> int:
    checks: list[tuple[str, Callable[[], bool]]] = []

    def check(name: str) -> Callable[[Callable[[], bool]], Callable[[], bool]]:
        def deco(fn: Callable[[], bool]) -> Callable[[], bool]:
            checks.append((name, fn))
            return fn
        return deco

    # ---- the formula itself -------------------------------------------------
    @check("kelly_fraction matches the textbook value at p=0.6, b=1")
    def _k1() -> bool:
        return abs(kelly_fraction(0.6, 1.0) - 0.2) < 1e-12

    @check("kelly_fraction at p=0.5, b=2 gives 0.25")
    def _k2() -> bool:
        return abs(kelly_fraction(0.5, 2.0) - 0.25) < 1e-12

    @check("kelly_fraction is negative on a losing edge (p=0.42, b=1)")
    def _k3() -> bool:
        return abs(kelly_fraction(0.42, 1.0) - (-0.16)) < 1e-12

    @check("kelly_fraction is exactly zero at the break-even p = 1/(1+b)")
    def _k4() -> bool:
        for b in (0.5, 1.0, 2.0, 4.0):
            if abs(kelly_fraction(1.0 / (1.0 + b), b)) > 1e-12:
                return False
        return True

    @check("kelly_fraction rejects p outside [0,1] and b <= 0")
    def _k5() -> bool:
        for args in ((1.4, 1.0), (-0.1, 1.0), (0.5, 0.0), (0.5, -2.0)):
            try:
                kelly_fraction(*args)
            except SizerError:
                continue
            return False
        return True

    # ---- statistics from a ledger ------------------------------------------
    @check("KellyStats.from_trades separates wins from losses correctly")
    def _s1() -> bool:
        st = KellyStats.from_trades([100.0, -50.0, 200.0, -50.0, 0.0])
        return (st.wins == 2 and st.losses == 2 and st.n == 4
                and abs(st.avg_win_bps - 150.0) < 1e-9
                and abs(st.avg_loss_bps - 50.0) < 1e-9
                and abs(st.payoff_ratio - 3.0) < 1e-9)

    @check("expectancy agrees with the mean of the raw returns")
    def _s2() -> bool:
        rets = [120.0, -40.0, -35.0, 80.0, -60.0, 15.0]
        st = KellyStats.from_trades(rets)
        return abs(st.expectancy_bps - sum(rets) / len(rets)) < 1e-9

    @check("an empty ledger yields n=0 and does not divide by zero")
    def _s3() -> bool:
        st = KellyStats.from_trades([])
        return (st.n == 0 and st.win_rate == 0.0 and st.payoff_ratio == 1.0
                and st.standard_error_bps is None)

    # ---- the decisive property: a negative edge sizes to zero ---------------
    @check("the measured 42.3% win rate produces f* < 0 and a size of ZERO")
    def _z1() -> bool:
        # 575 out-of-sample trades, 42.3% win rate, symmetric magnitudes.
        wins, losses = 243, 332
        st = KellyStats(wins=wins, losses=losses, avg_win_bps=95.0, avg_loss_bps=95.0)
        if st.kelly_f >= 0.0:
            return False
        d = PositionSizer().size(equity_cents=500_000, price_cents=11_000_000, stats=st)
        return (d.quantity_units == 0 and d.notional_cents == 0
                and d.fraction == 0.0 and not d.trades)

    @check("no consensus score, however high, resurrects a negative edge")
    def _z2() -> bool:
        st = KellyStats(wins=243, losses=332, avg_win_bps=95.0, avg_loss_bps=95.0)
        sizer = PositionSizer()
        for score in (50.0, 76.8, 88.0, 99.9, 100.0):
            d = sizer.size(equity_cents=500_000, price_cents=11_000_000,
                           stats=st, consensus_score=score)
            if d.quantity_units != 0 or d.fraction != 0.0:
                return False
        return True

    @check("the refusal explains itself rather than returning a bare zero")
    def _z3() -> bool:
        st = KellyStats(wins=243, losses=332, avg_win_bps=95.0, avg_loss_bps=95.0)
        d = PositionSizer().size(equity_cents=500_000, price_cents=11_000_000, stats=st)
        return "negative" in d.reason.lower() and "42" in d.reason

    # ---- warm-up ------------------------------------------------------------
    @check("below min_trades the sizer uses the warm-up fraction, not Kelly")
    def _w1() -> bool:
        st = KellyStats(wins=8, losses=2, avg_win_bps=100.0, avg_loss_bps=50.0)
        sizer = PositionSizer(min_trades=30, warmup_fraction=0.01)
        d = sizer.size(equity_cents=1_000_000, price_cents=10_000_000, stats=st)
        # full Kelly here would be enormous (p=0.8, b=2 -> f*=0.70); warm-up caps it.
        return abs(d.fraction - 0.01) < 1e-12 and st.kelly_f > 0.5

    @check("stats=None is treated as zero trades, not as a free pass")
    def _w2() -> bool:
        d = PositionSizer().size(equity_cents=1_000_000, price_cents=10_000_000)
        return abs(d.fraction - 0.01) < 1e-12 and "0 closed trades" in d.reason

    # ---- a genuine edge -----------------------------------------------------
    @check("a real edge sizes at lam * f*, below the cap")
    def _p1() -> bool:
        st = KellyStats(wins=60, losses=40, avg_win_bps=100.0, avg_loss_bps=100.0)
        # p = 0.6, b = 1 -> f* = 0.20; quarter Kelly = 0.05
        d = PositionSizer(lam=0.25, max_fraction=0.15).size(
            equity_cents=1_000_000, price_cents=10_000_000, stats=st)
        return abs(d.fraction - 0.05) < 1e-9 and d.trades

    @check("the cap binds when fractional Kelly would exceed it")
    def _p2() -> bool:
        st = KellyStats(wins=90, losses=10, avg_win_bps=200.0, avg_loss_bps=100.0)
        # p = 0.9, b = 2 -> f* = 0.85; quarter = 0.2125 > cap 0.15
        d = PositionSizer(lam=0.25, max_fraction=0.15).size(
            equity_cents=1_000_000, price_cents=10_000_000, stats=st)
        return abs(d.fraction - 0.15) < 1e-9

    @check("the consensus tilt only ever scales DOWN, never up")
    def _p3() -> bool:
        st = KellyStats(wins=60, losses=40, avg_win_bps=100.0, avg_loss_bps=100.0)
        sizer = PositionSizer()
        base = sizer.size(equity_cents=1_000_000, price_cents=10_000_000, stats=st)
        for score in (50.0, 60.0, 76.8, 90.0, 100.0):
            d = sizer.size(equity_cents=1_000_000, price_cents=10_000_000,
                           stats=st, consensus_score=score)
            if d.fraction > base.fraction + 1e-12:
                return False
        return True

    @check("a bare-quorum 50% consensus sizes to zero even on a positive edge")
    def _p4() -> bool:
        st = KellyStats(wins=60, losses=40, avg_win_bps=100.0, avg_loss_bps=100.0)
        d = PositionSizer().size(equity_cents=1_000_000, price_cents=10_000_000,
                                 stats=st, consensus_score=50.0)
        return d.fraction == 0.0 and not d.trades

    # ---- integer discipline -------------------------------------------------
    @check("notional and quantity are ints, never floats")
    def _i1() -> bool:
        st = KellyStats(wins=60, losses=40, avg_win_bps=100.0, avg_loss_bps=100.0)
        d = PositionSizer().size(equity_cents=500_308, price_cents=11_037_650, stats=st)
        return (type(d.notional_cents) is int and type(d.quantity_units) is int
                and d.notional_cents >= 0 and d.quantity_units >= 0)

    @check("float equity or price is refused outright")
    def _i2() -> bool:
        st = KellyStats(wins=60, losses=40, avg_win_bps=100.0, avg_loss_bps=100.0)
        for kw in ({"equity_cents": 1_000_000.0, "price_cents": 10_000_000},
                   {"equity_cents": 1_000_000, "price_cents": 10_000_000.5},
                   {"equity_cents": True, "price_cents": 10_000_000}):
            try:
                PositionSizer().size(stats=st, **kw)  # type: ignore[arg-type]
            except SizerError:
                continue
            return False
        return True

    @check("notional never exceeds equity * max_fraction")
    def _i3() -> bool:
        st = KellyStats(wins=95, losses=5, avg_win_bps=400.0, avg_loss_bps=50.0)
        sizer = PositionSizer(max_fraction=0.15)
        d = sizer.size(equity_cents=500_308, price_cents=11_037_650, stats=st)
        return d.notional_cents <= int(500_308 * 0.15)

    @check("a size that rounds below one unit returns zero quantity, not a fraction")
    def _i4() -> bool:
        st = KellyStats(wins=60, losses=40, avg_win_bps=100.0, avg_loss_bps=100.0)
        d = PositionSizer(qty_scale_pow10=0).size(
            equity_cents=10_000, price_cents=11_000_000, stats=st)
        return d.quantity_units == 0 and not d.trades

    # ---- configuration ------------------------------------------------------
    @check("an out-of-range lam, cap, or warm-up is refused at construction")
    def _c1() -> bool:
        for kw in ({"lam": 0.0}, {"lam": 1.5}, {"max_fraction": 0.0},
                   {"max_fraction": 1.5}, {"min_trades": 0},
                   {"warmup_fraction": 0.9}, {"warmup_fraction": -0.01}):
            try:
                PositionSizer(**kw)  # type: ignore[arg-type]
            except SizerError:
                continue
            return False
        return True

    @check("KellyStats rejects negative counts and signed loss magnitudes")
    def _c2() -> bool:
        for kw in ({"wins": -1, "losses": 1}, {"wins": 1, "losses": -1}):
            try:
                KellyStats(avg_win_bps=1.0, avg_loss_bps=1.0, **kw)  # type: ignore[arg-type]
            except SizerError:
                continue
            return False
        try:
            KellyStats(wins=1, losses=1, avg_win_bps=1.0, avg_loss_bps=-1.0)
        except SizerError:
            return True
        return False

    @check("as_dict round-trips through JSON")
    def _c3() -> bool:
        st = KellyStats(wins=60, losses=40, avg_win_bps=100.0, avg_loss_bps=100.0)
        d = PositionSizer().size(equity_cents=1_000_000, price_cents=10_000_000, stats=st)
        return json.loads(json.dumps(d.as_dict()))["quantity_units"] == d.quantity_units

    # ---- growth-rate sanity: quarter Kelly beats the fixed 0.002 BTC ---------
    @check("fractional Kelly compounds while a fixed size does not adapt")
    def _g1() -> bool:
        st = KellyStats(wins=60, losses=40, avg_win_bps=100.0, avg_loss_bps=100.0)
        sizer = PositionSizer()
        small = sizer.size(equity_cents=500_000, price_cents=11_000_000, stats=st)
        large = sizer.size(equity_cents=5_000_000, price_cents=11_000_000, stats=st)
        return large.quantity_units > small.quantity_units * 9

    failed = 0
    for name, fn in checks:
        try:
            ok = bool(fn())
        except Exception as exc:  # noqa: BLE001
            ok = False
            name = f"{name}  [raised {type(exc).__name__}: {exc}]"
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            failed += 1
    print(f"\n  {len(checks) - failed}/{len(checks)} checks passed")
    return 1 if failed else 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Fractional Kelly position sizing.")
    ap.add_argument("--selftest", action="store_true", help="run the self-test suite")
    ap.add_argument("--report", action="store_true",
                    help="size a position from the supplied statistics")
    ap.add_argument("--equity-usd", type=float, default=5003.08)
    ap.add_argument("--price-usd", type=float, default=110_376.50)
    ap.add_argument("--wins", type=int, default=243)
    ap.add_argument("--losses", type=int, default=332)
    ap.add_argument("--avg-win-bps", type=float, default=95.0)
    ap.add_argument("--avg-loss-bps", type=float, default=95.0)
    ap.add_argument("--consensus", type=float, default=None)
    ap.add_argument("--lam", type=float, default=0.25)
    ap.add_argument("--max-fraction", type=float, default=0.15)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    stats = KellyStats(
        wins=args.wins, losses=args.losses,
        avg_win_bps=args.avg_win_bps, avg_loss_bps=args.avg_loss_bps,
    )
    sizer = PositionSizer(lam=args.lam, max_fraction=args.max_fraction)
    equity_cents = int(round(args.equity_usd * 100))
    price_cents = int(round(args.price_usd * 100))
    if args.json:
        d = sizer.size(equity_cents=equity_cents, price_cents=price_cents,
                       stats=stats, consensus_score=args.consensus)
        print(json.dumps(d.as_dict(), indent=2))
        return 0
    print(render_report(equity_cents=equity_cents, price_cents=price_cents,
                        stats=stats, sizer=sizer, consensus_score=args.consensus))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
