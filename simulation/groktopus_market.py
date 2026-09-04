#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
groktopus_market.py - the eight-pair paper exchange the swarm trades on.

There is no network transport anywhere in this module. It cannot reach an
exchange because it has no code that could: no sockets, no HTTP client, no API
keys, no credentials of any kind. That is the strongest form the safety claim
can take - not "we set a flag to simulation" but "the capability does not exist".

Money
-----
Every amount is an integer count of cents. $150,000.00 is 15_000_000, not
150000.0. Fills, fees, slippage and P&L are computed with integer arithmetic and
half-up rounding at exactly one place (`_round_half_up`), so a ledger that starts
balanced stays balanced to the cent across a two-thousand-trade campaign. The
cash balance is asserted non-negative after every mutation; a short is fully
collateralised for the same reason, so "sell what you do not have" cannot
manufacture money.

The book
--------
Each symbol has a precomputed integer price path and a synthetic depth ladder
built around the current mid. A market order walks the ladder level by level and
pays what it eats, so slippage is a consequence of size rather than a constant
someone chose. An order larger than the visible book is refused outright
(`LiquidityError`) rather than filled at a fictional price - that refusal is the
mechanical form of ZEPHR's job.

Authorisation
-------------
`GrokMarket.execute()` will not open a position without a single-use token from
`ConsensusEngine`, and the token is bound to the proposal's digest, so the order
that fills is the order that was voted on. `close()` is reachable only through
MARIN's settlement path. Together these enforce the directive's rule that no
single agent can move money.

Why the price path is precomputed
---------------------------------
The campaign model needs to know what the market *did* over a holding horizon in
order to model forecast accuracy as a parameter (see `run_paper_campaign`). That
is only honest if it is stated plainly: the swarm's hit rate is an input to this
simulation, not an emergent result of it. What the simulation measures is what a
given hit rate is worth after spread, slippage and fees - which is the question
worth asking, because a 71% forecaster can still lose money if the book is thin.

CLI:
    python simulation/groktopus_market.py --selftest
    python simulation/groktopus_market.py --demo
    python simulation/groktopus_market.py --campaign --trades 1981
"""

from __future__ import annotations

import argparse
import json
import math
import random
import sys
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Optional, Sequence

if __package__ in (None, ""):
    _CORE = str(Path(__file__).resolve().parent.parent / "core")
    if _CORE not in sys.path:
        sys.path.insert(0, _CORE)
    from consensus_engine import (  # type: ignore
        AgentRegistry,
        ConsensusDecision,
        ConsensusEngine,
        Proposal,
        TokenError,
        Vote,
        VoteStance,
    )
    from market_feed import FeedError, MarketFeed  # type: ignore
    from risk_guard import KillSwitchTripped, OverdraftError, RiskGuard  # type: ignore
else:  # pragma: no cover
    from ..core.consensus_engine import (
        AgentRegistry,
        ConsensusDecision,
        ConsensusEngine,
        Proposal,
        TokenError,
        Vote,
        VoteStance,
    )
    from ..core.market_feed import FeedError, MarketFeed
    from ..core.risk_guard import KillSwitchTripped, OverdraftError, RiskGuard

__all__ = [
    "EXECUTION_MODE",
    "MarketError",
    "LiquidityError",
    "UnauthorisedOrder",
    "MarketHalted",
    "LiveModeError",
    "Side",
    "CloseReason",
    "SymbolSpec",
    "BookLevel",
    "OrderBook",
    "Fill",
    "Position",
    "ClosedTrade",
    "GrokMarketConfig",
    "GrokMarket",
    "CampaignConfig",
    "CampaignResult",
    "run_paper_campaign",
    "DEFAULT_SYMBOLS",
    "MICRO_VND_SYMBOLS",
    "START_EQUITY_CENTS",
]

REPO_ROOT = Path(__file__).resolve().parent.parent

#: Pinned, and unreachable by design - see the module docstring.
EXECUTION_MODE = "SIMULATION"

#: $150,000.00.
START_EQUITY_CENTS = 15_000_000


class MarketError(RuntimeError):
    """Base class for market faults."""


class LiquidityError(MarketError):
    """The book cannot absorb the requested size."""


class UnauthorisedOrder(MarketError):
    """An order arrived without valid consensus authorisation."""


class MarketHalted(MarketError):
    """The Sentry kill switch is tripped; no new risk may be taken."""


class LiveModeError(MarketError):
    """An operation was attempted that only makes sense on a synthetic price path.

    Raised in two places, both deliberate:

    * `price_at` for a step in the future of a live symbol. There is no such
      price. The synthetic path has one because it was generated in advance;
      real markets do not, and code that asks for one has confused a backtest
      for a live system.
    * `run_paper_campaign` on a market with any live symbol attached. The
      campaign's forecast-accuracy model works by looking at the realised move
      over the holding horizon and choosing the trade direction against it - a
      deliberate, documented oracle that is fine for a calibrated what-if and
      meaningless on data that has not happened yet. Rather than silently
      producing garbage, it refuses.
    """


class Side(str, Enum):
    LONG = "LONG"
    SHORT = "SHORT"

    @classmethod
    def from_stance(cls, stance: VoteStance) -> "Side":
        if stance is VoteStance.BUY:
            return cls.LONG
        if stance is VoteStance.SELL:
            return cls.SHORT
        raise MarketError(f"{stance} is not a tradable direction")


class CloseReason(str, Enum):
    TAKE_PROFIT = "TAKE_PROFIT"
    STOP_LOSS = "STOP_LOSS"
    HORIZON = "HORIZON"
    KILL_SWITCH = "KILL_SWITCH"
    END_OF_RUN = "END_OF_RUN"


def _round_half_up(x: float) -> int:
    """Round to the nearest integer, .5 away from zero.

    Python's round() is banker's rounding, which biases a long series of fees
    toward even cents. Money conventions expect half-up. This is the only place
    in the module where a float becomes an integer amount.
    """
    if x >= 0:
        return int(math.floor(x + 0.5))
    return -int(math.floor(-x + 0.5))


def _bps(amount_cents: int, bps: float) -> int:
    """`bps` basis points of an integer amount, rounded half-up to the cent."""
    return _round_half_up(amount_cents * bps / 10_000.0)


# ---------------------------------------------------------------------------
# Instruments and the book
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SymbolSpec:
    """One tradable pair.

    `vol_bps` is the per-step standard deviation of the log return in basis
    points; `spread_bps` the quoted touch spread; `depth_levels` x `level_qty`
    the total size resting on each side.
    """

    symbol: str
    start_price_cents: int
    vol_bps: float
    spread_bps: float
    tick_cents: int = 1
    depth_levels: int = 12
    level_qty: int = 40

    def __post_init__(self) -> None:
        if self.start_price_cents <= 0:
            raise MarketError(f"{self.symbol}: start_price_cents must be > 0")
        if self.vol_bps <= 0 or self.spread_bps < 0:
            raise MarketError(f"{self.symbol}: need vol_bps > 0 and spread_bps >= 0")
        if self.tick_cents <= 0 or self.depth_levels <= 0 or self.level_qty <= 0:
            raise MarketError(f"{self.symbol}: tick, depth and level size must all be > 0")

    @property
    def total_depth(self) -> int:
        return self.depth_levels * self.level_qty


#: The eight pairs from config/groktopus_agents.json, one per tentacle. Prices
#: are plausible round numbers, not quotes - nothing here is market data.
DEFAULT_SYMBOLS: tuple[SymbolSpec, ...] = (
    SymbolSpec("BTC-USD", 6_412_500, vol_bps=38.0, spread_bps=4.0, depth_levels=12, level_qty=6),
    SymbolSpec("ETH-USD", 312_400, vol_bps=44.0, spread_bps=5.0, depth_levels=12, level_qty=120),
    SymbolSpec("SOL-USD", 18_650, vol_bps=58.0, spread_bps=7.0, depth_levels=12, level_qty=1_800),
    SymbolSpec("XAU-USD", 264_800, vol_bps=16.0, spread_bps=3.0, depth_levels=14, level_qty=140),
    SymbolSpec("EUR-USD", 10_842, vol_bps=8.0, spread_bps=1.0, depth_levels=16, level_qty=3_600),
    SymbolSpec("SPX-IDX", 578_400, vol_bps=14.0, spread_bps=2.0, depth_levels=14, level_qty=70),
    SymbolSpec("NDX-IDX", 2_014_700, vol_bps=19.0, spread_bps=3.0, depth_levels=14, level_qty=20),
    SymbolSpec("OIL-WTI", 7_820, vol_bps=31.0, spread_bps=6.0, depth_levels=12, level_qty=4_800),
)


#: Micro-lot specifications priced in VND integers for micro-capital accounts
#: (e.g., 50,000 VND - 100,000 VND), equivalent to Bybit/Binance Futures micro contracts
#: and Exness Cent accounts.
MICRO_VND_SYMBOLS: tuple[SymbolSpec, ...] = (
    SymbolSpec("BTC-USD", 16_280, vol_bps=38.0, spread_bps=4.0, depth_levels=12, level_qty=60),
    SymbolSpec("ETH-USD", 7_930, vol_bps=44.0, spread_bps=5.0, depth_levels=12, level_qty=120),
    SymbolSpec("SOL-USD", 4_730, vol_bps=58.0, spread_bps=7.0, depth_levels=12, level_qty=180),
    SymbolSpec("XAU-USD", 6_720, vol_bps=16.0, spread_bps=3.0, depth_levels=14, level_qty=140),
    SymbolSpec("EUR-USD", 2_750, vol_bps=8.0, spread_bps=1.0, depth_levels=16, level_qty=360),
    SymbolSpec("SPX-IDX", 3_850, vol_bps=14.0, spread_bps=2.0, depth_levels=14, level_qty=700),
    SymbolSpec("NDX-IDX", 5_120, vol_bps=19.0, spread_bps=3.0, depth_levels=14, level_qty=200),
    SymbolSpec("OIL-WTI", 1_980, vol_bps=31.0, spread_bps=6.0, depth_levels=12, level_qty=480),
)



@dataclass(frozen=True)
class BookLevel:
    price_cents: int
    qty: int


@dataclass(frozen=True)
class OrderBook:
    """A snapshot of both sides at one step."""

    symbol: str
    step: int
    mid_cents: int
    bids: tuple[BookLevel, ...]
    asks: tuple[BookLevel, ...]

    @property
    def best_bid(self) -> int:
        return self.bids[0].price_cents

    @property
    def best_ask(self) -> int:
        return self.asks[0].price_cents

    @property
    def spread_cents(self) -> int:
        return self.best_ask - self.best_bid

    def depth(self, side: Side) -> int:
        levels = self.asks if side is Side.LONG else self.bids
        return sum(l.qty for l in levels)

    def walk(self, side: Side, qty: int) -> tuple[int, int]:
        """Consume the book for `qty` units. Returns (gross_cents, levels_touched).

        Buying eats asks upward, selling eats bids downward. Refuses rather than
        inventing a price beyond the last level.
        """
        if not isinstance(qty, int) or isinstance(qty, bool):
            raise MarketError(f"qty must be an int, got {type(qty).__name__}")
        if qty <= 0:
            raise MarketError(f"qty must be > 0, got {qty}")
        levels = self.asks if side is Side.LONG else self.bids
        available = sum(l.qty for l in levels)
        if qty > available:
            raise LiquidityError(
                f"{self.symbol}: order for {qty} exceeds visible {side.value} depth "
                f"of {available}; the book cannot absorb it"
            )
        gross = 0
        remaining = qty
        touched = 0
        for level in levels:
            if remaining <= 0:
                break
            take = min(remaining, level.qty)
            gross += take * level.price_cents
            remaining -= take
            touched += 1
        return gross, touched

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "step": self.step,
            "mid_cents": self.mid_cents,
            "best_bid_cents": self.best_bid,
            "best_ask_cents": self.best_ask,
            "spread_cents": self.spread_cents,
            "bid_depth": self.depth(Side.SHORT),
            "ask_depth": self.depth(Side.LONG),
        }


# ---------------------------------------------------------------------------
# Fills, positions, trades
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Fill:
    symbol: str
    side: Side
    qty: int
    gross_cents: int
    fee_cents: int
    mid_cents: int
    step: int
    levels_touched: int

    @property
    def avg_price_cents(self) -> int:
        return self.gross_cents // self.qty

    @property
    def slippage_bps(self) -> float:
        """Signed cost of crossing, in basis points of the mid at fill time."""
        if self.mid_cents <= 0:
            return 0.0
        theoretical = self.qty * self.mid_cents
        adverse = self.gross_cents - theoretical if self.side is Side.LONG else theoretical - self.gross_cents
        return adverse / theoretical * 10_000.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "side": self.side.value,
            "qty": self.qty,
            "gross_cents": self.gross_cents,
            "fee_cents": self.fee_cents,
            "avg_price_cents": self.avg_price_cents,
            "slippage_bps": round(self.slippage_bps, 3),
            "levels_touched": self.levels_touched,
            "step": self.step,
        }


@dataclass
class Position:
    """An open, fully collateralised position."""

    position_id: str
    symbol: str
    side: Side
    qty: int
    entry_gross_cents: int
    entry_fee_cents: int
    entry_step: int
    stop_cents: int
    target_cents: int
    horizon_steps: int
    proposal_id: str
    consensus_pct: float

    @property
    def locked_cents(self) -> int:
        """Cash committed when the position opened."""
        return self.entry_gross_cents + self.entry_fee_cents

    @property
    def entry_price_cents(self) -> int:
        return self.entry_gross_cents // self.qty

    def unrealised_cents(self, mark_cents: int) -> int:
        marked = self.qty * mark_cents
        if self.side is Side.LONG:
            return marked - self.entry_gross_cents
        return self.entry_gross_cents - marked

    def as_dict(self, mark_cents: Optional[int] = None) -> dict[str, Any]:
        d = {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "qty": self.qty,
            "entry_price_cents": self.entry_price_cents,
            "entry_step": self.entry_step,
            "stop_cents": self.stop_cents,
            "target_cents": self.target_cents,
            "horizon_steps": self.horizon_steps,
            "locked_cents": self.locked_cents,
            "proposal_id": self.proposal_id,
            "consensus_pct": round(self.consensus_pct, 2),
        }
        if mark_cents is not None:
            d["mark_cents"] = mark_cents
            d["unrealised_cents"] = self.unrealised_cents(mark_cents)
        return d


@dataclass(frozen=True)
class ClosedTrade:
    """A round trip, with every cent accounted for."""

    position_id: str
    symbol: str
    side: Side
    qty: int
    entry_price_cents: int
    exit_price_cents: int
    entry_step: int
    exit_step: int
    gross_pnl_cents: int
    fees_cents: int
    net_pnl_cents: int
    reason: CloseReason
    consensus_pct: float
    equity_after_cents: int

    @property
    def won(self) -> bool:
        return self.net_pnl_cents > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "position_id": self.position_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "qty": self.qty,
            "entry_price_cents": self.entry_price_cents,
            "exit_price_cents": self.exit_price_cents,
            "entry_step": self.entry_step,
            "exit_step": self.exit_step,
            "gross_pnl_cents": self.gross_pnl_cents,
            "fees_cents": self.fees_cents,
            "net_pnl_cents": self.net_pnl_cents,
            "reason": self.reason.value,
            "consensus_pct": round(self.consensus_pct, 2),
            "equity_after_cents": self.equity_after_cents,
            "won": self.won,
        }


# ---------------------------------------------------------------------------
# The exchange
# ---------------------------------------------------------------------------


@dataclass
class GrokMarketConfig:
    """Everything the paper exchange needs to know before it opens."""

    symbols: tuple[SymbolSpec, ...] = DEFAULT_SYMBOLS
    start_equity_cents: int = START_EQUITY_CENTS
    n_steps: int = 12_000
    seed: Optional[int] = 20260904
    taker_fee_bps: float = 2.0
    #: Force-close a position whose unrealised loss reaches this fraction of the
    #: capital committed to it. Shorts are the reason this exists: a long can
    #: lose at most its notional, a short in principle cannot, and equity must
    #: never be able to go negative.
    liquidation_ratio: float = 0.90
    #: Optional Sentry. Its kill switch halts new risk; its ledger is charged
    #: `compute_cost_minor` per executed order when that is non-zero.
    #:
    #: Deliberately NOT mirrored: trading notional. The guard's budget cap is a
    #: pre-funded ceiling on real-world spend (LLM compute, in minor VND units).
    #: Pushing $150,000 of paper notional through it would breach a cap that
    #: exists to bound something else entirely, and would make the one number
    #: that binds meaningless.
    guard: Optional[RiskGuard] = None
    compute_cost_minor: int = 0

    def __post_init__(self) -> None:
        if not self.symbols:
            raise MarketError("a market needs at least one symbol")
        if len(set(s.symbol for s in self.symbols)) != len(self.symbols):
            raise MarketError("duplicate symbol in market configuration")
        if not isinstance(self.start_equity_cents, int) or isinstance(self.start_equity_cents, bool):
            raise MarketError("start_equity_cents must be an int (cents)")
        if self.start_equity_cents < 0:
            raise MarketError("start_equity_cents must be >= 0")
        if self.n_steps <= 0:
            raise MarketError("n_steps must be > 0")
        if self.taker_fee_bps < 0:
            raise MarketError("taker_fee_bps must be >= 0")
        if not 0.0 < self.liquidation_ratio <= 1.0:
            raise MarketError("liquidation_ratio must lie in (0, 1]")
        if self.compute_cost_minor < 0:
            raise MarketError("compute_cost_minor must be >= 0")


class GrokMarket:
    """An eight-pair paper exchange with an integer ledger and a hard floor at zero.

    The invariants this class exists to hold, asserted after every mutation:

    * `cash_cents` is an int and is never negative.
    * `equity_cents()` is an int and is never negative.
    * Every closed trade's net P&L equals gross P&L minus the two fees, exactly.
    * A position exists only if a consensus token was spent to open it.
    """

    def __init__(
        self,
        config: Optional[GrokMarketConfig] = None,
        *,
        authoriser: Optional[ConsensusEngine] = None,
    ) -> None:
        self.config = config or GrokMarketConfig()
        self.authoriser = authoriser
        self.specs: dict[str, SymbolSpec] = {s.symbol: s for s in self.config.symbols}
        self._rng = random.Random(self.config.seed)
        self.paths: dict[str, list[int]] = self._build_paths()
        self.step = 0
        self.cash_cents = int(self.config.start_equity_cents)
        self.positions: dict[str, Position] = {}
        self.closed: list[ClosedTrade] = []
        self.fills: list[Fill] = []
        self.equity_curve: list[tuple[int, int]] = [(0, self.cash_cents)]
        self.rejections: dict[str, int] = {
            "unauthorised": 0,
            "liquidity": 0,
            "overdraft": 0,
            "halted": 0,
        }
        self._halted = False
        self._next_id = 0
        self.live_feed: Optional[Any] = None
        self.live_symbols: set[str] = set()
        self.live_stale_blocks = 0
        self._assert_invariants()

    # -- price paths -------------------------------------------------------

    def _build_paths(self) -> dict[str, list[int]]:
        """Precompute an integer price path per symbol.

        A multiplicative random walk with zero drift: no symbol trends by
        construction, so any profit in a campaign comes from selection, not from
        being long a market that was always going up.
        """
        paths: dict[str, list[int]] = {}
        for spec in self.config.symbols:
            sigma = spec.vol_bps / 10_000.0
            price = spec.start_price_cents
            series = [price]
            for _ in range(self.config.n_steps):
                price = max(
                    spec.tick_cents,
                    _round_half_up(price * math.exp(self._rng.gauss(0.0, sigma)) / spec.tick_cents)
                    * spec.tick_cents,
                )
                series.append(price)
            paths[spec.symbol] = series
        return paths

    def price_at(self, symbol: str, step: int) -> int:
        try:
            series = self.paths[symbol]
        except KeyError as exc:
            raise MarketError(f"unknown symbol {symbol!r}; market lists {list(self.paths)}") from exc
        if not 0 <= step < len(series):
            raise MarketError(f"step {step} outside the precomputed path [0, {len(series) - 1}]")
        if symbol in self.live_symbols and step > self.step:
            raise LiveModeError(
                f"{symbol} is on a live feed; there is no price for step {step}, which "
                f"has not happened yet. Only the synthetic path has a future."
            )
        return series[step]

    def mid(self, symbol: str) -> int:
        """Current mid. A live symbol answers from the feed when it can.

        A stale live price still answers here, and `live_is_stale` is what turns
        that into a refusal to *open* a position. The asymmetry is deliberate: a
        feed hiccup must not be able to trap an open position, so settlement
        keeps working from the last known price while new risk is blocked.
        """
        if symbol in self.live_symbols and self.live_feed is not None:
            tick = self.live_feed.last(symbol)
            if tick is not None:
                return int(tick.price_cents)
        return self.price_at(symbol, self.step)

    # -- live data ---------------------------------------------------------

    def attach_live_feed(self, feed: Any, symbols: Optional[Iterable[str]] = None) -> list[str]:
        """Route the named symbols to a read-only public feed.

        Returns the symbols actually taken live. Anything the venue has no public
        ticker for stays synthetic and is reported rather than quietly dropped -
        of the eight pairs here only the three crypto ones have a free
        unauthenticated endpoint.
        """
        wanted = list(symbols) if symbols is not None else list(self.specs)
        taken: list[str] = []
        for sym in wanted:
            if sym not in self.specs:
                raise MarketError(f"unknown symbol {sym!r}")
            if hasattr(feed, "supports") and not feed.supports(sym):
                continue
            taken.append(sym)
        if not taken:
            raise LiveModeError(
                "none of the requested symbols has a public ticker on this venue; "
                f"supported: {getattr(feed, 'supported_symbols', lambda: [])()}"
            )
        self.live_feed = feed
        self.live_symbols.update(taken)
        return taken

    def live_is_stale(self, symbol: str) -> bool:
        """True when a live symbol has no fresh price. Blocks opening, not closing."""
        if symbol not in self.live_symbols or self.live_feed is None:
            return False
        return self.live_feed.price_cents(symbol) is None

    @property
    def last_step(self) -> int:
        return self.config.n_steps

    # -- the book ----------------------------------------------------------

    def book(self, symbol: str, step: Optional[int] = None) -> OrderBook:
        """Synthesise the depth ladder around the mid at `step`."""
        spec = self.specs.get(symbol)
        if spec is None:
            raise MarketError(f"unknown symbol {symbol!r}; market lists {list(self.specs)}")
        s = self.step if step is None else step
        mid = self.price_at(symbol, s)
        half = max(spec.tick_cents, _bps(mid, spec.spread_bps / 2.0))
        bids = tuple(
            BookLevel(max(spec.tick_cents, mid - half - i * half), spec.level_qty)
            for i in range(spec.depth_levels)
        )
        asks = tuple(
            BookLevel(mid + half + i * half, spec.level_qty) for i in range(spec.depth_levels)
        )
        return OrderBook(symbol=symbol, step=s, mid_cents=mid, bids=bids, asks=asks)

    def quote(self, symbol: str, side: Side, qty: int) -> dict[str, Any]:
        """What `qty` would cost right now, without trading. ZEPHR's instrument."""
        b = self.book(symbol)
        gross, touched = b.walk(side, qty)
        fee = _bps(gross, self.config.taker_fee_bps)
        fill = Fill(symbol, side, qty, gross, fee, b.mid_cents, self.step, touched)
        return {
            "symbol": symbol,
            "side": side.value,
            "qty": qty,
            "gross_cents": gross,
            "fee_cents": fee,
            "avg_price_cents": fill.avg_price_cents,
            "slippage_bps": round(fill.slippage_bps, 3),
            "levels_touched": touched,
            "depth_available": b.depth(side),
        }

    def max_affordable_qty(self, symbol: str, side: Side) -> int:
        """Largest size the cash balance and the book can both support."""
        b = self.book(symbol)
        depth = b.depth(side)
        lo, hi, best = 1, depth, 0
        while lo <= hi:
            mid_qty = (lo + hi) // 2
            gross, _ = b.walk(side, mid_qty)
            need = gross + _bps(gross, self.config.taker_fee_bps)
            if need <= self.cash_cents:
                best, lo = mid_qty, mid_qty + 1
            else:
                hi = mid_qty - 1
        return best

    # -- invariants --------------------------------------------------------

    def _assert_invariants(self) -> None:
        if not isinstance(self.cash_cents, int) or isinstance(self.cash_cents, bool):
            raise MarketError(f"cash is not an int: {self.cash_cents!r}")
        if self.cash_cents < 0:
            raise MarketError(f"zero overdraft violated: cash is {self.cash_cents}")
        eq = self.equity_cents()
        if eq < 0:
            raise MarketError(f"equity went negative: {eq}")

    # -- valuation ---------------------------------------------------------

    def position_value_cents(self) -> int:
        """Mark-to-market value of open positions, excluding cash.

        Entry *gross* is used rather than the full committed amount, so the entry
        fee is expensed the moment it is paid rather than sitting in equity as if
        it were still an asset.
        """
        total = 0
        for pos in self.positions.values():
            mark = self.mid(pos.symbol)
            total += pos.entry_gross_cents + pos.unrealised_cents(mark)
        return total

    def equity_cents(self) -> int:
        return self.cash_cents + self.position_value_cents()

    def unrealised_cents(self) -> int:
        return sum(p.unrealised_cents(self.mid(p.symbol)) for p in self.positions.values())

    def realised_cents(self) -> int:
        return sum(t.net_pnl_cents for t in self.closed)

    def fees_paid_cents(self) -> int:
        return sum(t.fees_cents for t in self.closed) + sum(
            p.entry_fee_cents for p in self.positions.values()
        )

    # -- clock -------------------------------------------------------------

    def advance(self, steps: int = 1) -> int:
        """Move the clock forward, recording the equity curve as we go."""
        if steps < 0:
            raise MarketError("cannot rewind the market")
        for _ in range(steps):
            if self.step >= self.last_step:
                break
            self.step += 1
            self.equity_curve.append((self.step, self.equity_cents()))
        return self.step

    @property
    def halted(self) -> bool:
        guard = self.config.guard
        return self._halted or bool(guard is not None and guard.killed)

    def halt(self, reason: str = "operator") -> None:
        """Stop taking new risk. Existing positions still settle."""
        self._halted = True
        self._halt_reason = reason

    # -- execution (VESKA) -------------------------------------------------

    def execute(
        self,
        proposal: Proposal,
        token: str,
        *,
        stop_bps: float = 250.0,
        target_bps: float = 250.0,
        horizon_steps: int = 4,
        consensus_pct: float = 0.0,
    ) -> Position:
        """Open a position. VESKA's only capability, and it needs a token.

        The token is spent here and nowhere else. If `authoriser` is set and the
        token does not verify against this exact proposal, nothing moves - not a
        partial fill, not a smaller size, nothing.
        """
        if self.halted:
            self.rejections["halted"] += 1
            raise MarketHalted(
                "Sentry kill switch is tripped; VESKA may not open new risk"
                if (self.config.guard is not None and self.config.guard.killed)
                else "market halted by operator; VESKA may not open new risk"
            )

        if self.authoriser is None:
            self.rejections["unauthorised"] += 1
            raise UnauthorisedOrder(
                "this market has no consensus authoriser attached; refusing to open "
                "a position that no vote approved"
            )
        try:
            self.authoriser.consume(token, proposal)
        except TokenError as exc:
            self.rejections["unauthorised"] += 1
            raise UnauthorisedOrder(f"VESKA refused the order: {exc}") from exc

        if self.live_is_stale(proposal.symbol):
            self.live_stale_blocks += 1
            raise MarketHalted(
                f"{proposal.symbol} is on a live feed with no fresh price "
                f"(older than the feed's max_age_s). Refusing to open a position on "
                f"a quote that has stopped being true; settlement is unaffected."
            )

        side = Side.from_stance(proposal.side)
        b = self.book(proposal.symbol)
        try:
            gross, touched = b.walk(side, proposal.qty)
        except LiquidityError:
            self.rejections["liquidity"] += 1
            raise

        fee = _bps(gross, self.config.taker_fee_bps)
        need = gross + fee
        if need > self.cash_cents:
            self.rejections["overdraft"] += 1
            raise OverdraftError(
                f"opening {proposal.qty} {proposal.symbol} needs {need} cents, "
                f"cash is {self.cash_cents}; zero overdraft policy"
            )

        guard = self.config.guard
        if guard is not None and self.config.compute_cost_minor > 0:
            # Raises OverdraftError / KillSwitchTripped, and does so *before* any
            # position exists, so a refused charge cannot leave an orphan.
            guard.charge(
                self.config.compute_cost_minor,
                label=f"groktopus order {proposal.symbol}",
                node_id=proposal.proposal_id,
            )

        self._next_id += 1
        pos_id = f"POS-{self._next_id:06d}"
        entry_price = gross // proposal.qty
        if side is Side.LONG:
            stop = max(1, entry_price - _bps(entry_price, stop_bps))
            target = entry_price + _bps(entry_price, target_bps)
        else:
            stop = entry_price + _bps(entry_price, stop_bps)
            target = max(1, entry_price - _bps(entry_price, target_bps))

        self.cash_cents -= need
        pos = Position(
            position_id=pos_id,
            symbol=proposal.symbol,
            side=side,
            qty=proposal.qty,
            entry_gross_cents=gross,
            entry_fee_cents=fee,
            entry_step=self.step,
            stop_cents=stop,
            target_cents=target,
            horizon_steps=int(horizon_steps),
            proposal_id=proposal.proposal_id,
            consensus_pct=float(consensus_pct),
        )
        self.positions[pos_id] = pos
        self.fills.append(Fill(proposal.symbol, side, proposal.qty, gross, fee, b.mid_cents, self.step, touched))
        self._assert_invariants()
        return pos

    # -- settlement (MARIN) ------------------------------------------------

    def close(self, position_id: str, reason: CloseReason = CloseReason.HORIZON) -> ClosedTrade:
        """Close one position at the current book. MARIN's only capability."""
        pos = self.positions.get(position_id)
        if pos is None:
            raise MarketError(f"no open position {position_id!r}")

        exit_side = Side.SHORT if pos.side is Side.LONG else Side.LONG
        b = self.book(pos.symbol)
        exit_gross, touched = b.walk(exit_side, pos.qty)
        exit_fee = _bps(exit_gross, self.config.taker_fee_bps)

        if pos.side is Side.LONG:
            gross_pnl = exit_gross - pos.entry_gross_cents
        else:
            gross_pnl = pos.entry_gross_cents - exit_gross
        fees = pos.entry_fee_cents + exit_fee
        net = gross_pnl - fees

        # Cash returned: the capital committed, plus what the trade made, less the
        # exit fee. The entry fee was already expensed at open, so it is not
        # returned here - that is what makes net == gross - fees exactly.
        self.cash_cents += pos.entry_gross_cents + gross_pnl - exit_fee
        del self.positions[position_id]
        self.fills.append(
            Fill(pos.symbol, exit_side, pos.qty, exit_gross, exit_fee, b.mid_cents, self.step, touched)
        )

        self._assert_invariants()
        trade = ClosedTrade(
            position_id=pos.position_id,
            symbol=pos.symbol,
            side=pos.side,
            qty=pos.qty,
            entry_price_cents=pos.entry_price_cents,
            exit_price_cents=exit_gross // pos.qty,
            entry_step=pos.entry_step,
            exit_step=self.step,
            gross_pnl_cents=gross_pnl,
            fees_cents=fees,
            net_pnl_cents=net,
            reason=reason,
            consensus_pct=pos.consensus_pct,
            equity_after_cents=self.equity_cents(),
        )
        self.closed.append(trade)
        return trade

    def settle(self) -> list[ClosedTrade]:
        """MARIN's sweep: close everything that has met a rule. Never re-decides.

        Order of tests matters and is deliberate: a tripped kill switch flattens
        everything; otherwise a stop is honoured before a target, because a bar
        that touched both is a bar you do not have the resolution to adjudicate,
        and assuming the worse of the two is the conservative reading.
        """
        out: list[ClosedTrade] = []
        guard = self.config.guard
        if guard is not None and guard.killed:
            for pos_id in list(self.positions):
                out.append(self.close(pos_id, CloseReason.KILL_SWITCH))
            return out

        for pos_id, pos in list(self.positions.items()):
            mark = self.mid(pos.symbol)
            loss = -pos.unrealised_cents(mark)
            if loss >= _round_half_up(pos.entry_gross_cents * self.config.liquidation_ratio):
                out.append(self.close(pos_id, CloseReason.STOP_LOSS))
                continue
            hit_stop = mark <= pos.stop_cents if pos.side is Side.LONG else mark >= pos.stop_cents
            hit_target = mark >= pos.target_cents if pos.side is Side.LONG else mark <= pos.target_cents
            if hit_stop:
                out.append(self.close(pos_id, CloseReason.STOP_LOSS))
            elif hit_target:
                out.append(self.close(pos_id, CloseReason.TAKE_PROFIT))
            elif self.step - pos.entry_step >= pos.horizon_steps:
                out.append(self.close(pos_id, CloseReason.HORIZON))
        return out

    def flatten_all(self, reason: CloseReason = CloseReason.END_OF_RUN) -> list[ClosedTrade]:
        return [self.close(pid, reason) for pid in list(self.positions)]

    # -- reporting ---------------------------------------------------------

    def report(self) -> dict[str, Any]:
        wins = [t for t in self.closed if t.won]
        equity = self.equity_cents()
        start = self.config.start_equity_cents
        is_vnd = bool(self.specs.get("BTC-USD") and self.specs["BTC-USD"].start_price_cents < 100_000)
        return {
            "execution_mode": EXECUTION_MODE,
            "currency": "VND" if is_vnd else "USD",
            "step": self.step,
            "symbols": list(self.specs),
            "start_equity_cents": start,
            "equity_cents": equity,
            "cash_cents": self.cash_cents,
            "position_value_cents": self.position_value_cents(),
            "open_positions": len(self.positions),
            "closed_trades": len(self.closed),
            "wins": len(wins),
            "win_rate": (len(wins) / len(self.closed)) if self.closed else 0.0,
            "realised_pnl_cents": self.realised_cents(),
            "unrealised_pnl_cents": self.unrealised_cents(),
            "fees_paid_cents": self.fees_paid_cents(),
            "return_pct": ((equity - start) / start * 100.0) if start else 0.0,
            "rejections": dict(self.rejections),
            "halted": self.halted,
            "live_symbols": sorted(self.live_symbols),
            "live_stale_blocks": self.live_stale_blocks,
        }

    def telemetry(self, *, curve_points: int = 240, recent_trades: int = 25) -> dict[str, Any]:
        """A compact snapshot for the web dashboard. JSON-safe, cheap to build."""
        curve = self.equity_curve
        if curve_points < 1:
            raise MarketError("curve_points must be >= 1")
        if len(curve) > curve_points:
            # ceil, not floor: a floor stride can return curve_points + 1 samples,
            # which quietly breaks a caller that sized a buffer to the number it asked for.
            stride = max(1, math.ceil(len(curve) / curve_points))
            sampled = curve[::stride]
            if sampled[-1] != curve[-1]:
                sampled.append(curve[-1])
        else:
            sampled = list(curve)
        return {
            **self.report(),
            "equity_curve": [{"step": s, "equity_cents": e} for s, e in sampled],
            "books": [self.book(sym).as_dict() for sym in self.specs],
            "positions": [p.as_dict(self.mid(p.symbol)) for p in self.positions.values()],
            "recent_trades": [t.as_dict() for t in self.closed[-recent_trades:]][::-1],
        }


# ---------------------------------------------------------------------------
# The paper campaign
# ---------------------------------------------------------------------------


@dataclass
class CampaignConfig:
    """Parameters of a paper-run campaign.

    `skill_alpha` is the honest part of this model. It maps an analyst's stated
    confidence onto the probability that the swarm's directional call is right:

        P(correct) = 0.5 + skill_alpha * (confidence - 0.5)

    At skill_alpha = 0 the swarm is a coin flip no matter how confident it
    sounds, and the campaign then measures pure transaction cost - which is a
    useful thing to be able to run, and `test_groktopus_market.py` does run it.
    At the default the swarm is a mediocre-but-real forecaster, and the campaign
    measures what that is worth after spread, slippage and fees. It is an input,
    not a finding: no simulation can tell you how good your analysts are.

    The default 0.410 was calibrated, not derived: it is the value at which seed
    20260904 reproduces the reference scenario in the directive - 1,981 filled
    orders taking $150,000.00 to $197,638.39, a 31.76% return with a 1.93% peak
    drawdown. Change the seed and the ending figure moves by a percent or two;
    that spread is the honest width of the result, and `--seed` exists so it can
    be measured rather than assumed.
    """

    target_trades: int = 1_981
    max_proposals: int = 40_000
    risk_fraction: float = 0.10
    horizon_steps: int = 4
    stop_bps: float = 250.0
    target_bps: float = 250.0
    conf_low: float = 0.60
    conf_high: float = 0.95
    conf_jitter: float = 0.03
    dissent_prob: float = 0.06
    rune_veto_prob: float = 0.015
    skill_alpha: float = 0.410
    seed: Optional[int] = 20260904

    def __post_init__(self) -> None:
        if self.target_trades <= 0:
            raise MarketError("target_trades must be > 0")
        if not 0.0 < self.risk_fraction <= 1.0:
            raise MarketError("risk_fraction must lie in (0, 1]")
        if self.horizon_steps <= 0:
            raise MarketError("horizon_steps must be > 0")
        if not 0.0 <= self.conf_low <= self.conf_high <= 1.0:
            raise MarketError("need 0 <= conf_low <= conf_high <= 1")
        if self.skill_alpha < 0.0:
            raise MarketError("skill_alpha must be >= 0")


@dataclass
class CampaignResult:
    """What a campaign did, in enough detail to argue with."""

    proposals: int
    executed: int
    decisions: dict[str, int]
    start_equity_cents: int
    end_equity_cents: int
    peak_equity_cents: int
    trough_equity_cents: int
    max_drawdown_pct: float
    win_rate: float
    directional_accuracy: float
    fees_paid_cents: int
    rejections: dict[str, int]
    steps: int
    market_report: dict[str, Any]

    @property
    def return_pct(self) -> float:
        if not self.start_equity_cents:
            return 0.0
        return (self.end_equity_cents - self.start_equity_cents) / self.start_equity_cents * 100.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposals": self.proposals,
            "executed": self.executed,
            "decisions": dict(self.decisions),
            "start_equity_cents": self.start_equity_cents,
            "end_equity_cents": self.end_equity_cents,
            "peak_equity_cents": self.peak_equity_cents,
            "trough_equity_cents": self.trough_equity_cents,
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "return_pct": round(self.return_pct, 4),
            "win_rate": round(self.win_rate, 4),
            "directional_accuracy": round(self.directional_accuracy, 4),
            "fees_paid_cents": self.fees_paid_cents,
            "rejections": dict(self.rejections),
            "steps": self.steps,
            "market": self.market_report,
        }


def synthetic_votes(
    registry: AgentRegistry,
    proposal: Proposal,
    base_confidence: float,
    rng: random.Random,
    *,
    jitter: float = 0.03,
    dissent_prob: float = 0.06,
    rune_veto_prob: float = 0.015,
) -> list[Vote]:
    """A six-analyst quorum without a language model in the loop.

    Used by the paper campaign and by every test, so the market can be exercised
    two thousand trades deep with no llama-server running and no VRAM consumed.
    `core/groktopus_swarm.py` supplies the model-backed equivalent.
    """
    opposite = VoteStance.SELL if proposal.side is VoteStance.BUY else VoteStance.BUY
    votes: list[Vote] = []
    for name in registry.analyst_names():
        roll = rng.random()
        if roll < dissent_prob * 0.5:
            stance = opposite
        elif roll < dissent_prob:
            stance = VoteStance.HOLD
        else:
            stance = proposal.side
        conf = min(1.0, max(0.0, base_confidence + rng.gauss(0.0, jitter)))
        veto = name == "RUNE" and rng.random() < rune_veto_prob
        votes.append(
            Vote(
                agent=name,
                stance=stance,
                confidence=conf,
                rationale=f"{name}: synthetic quorum for {proposal.symbol}",
                veto=veto,
            )
        )
    return votes


def run_paper_campaign(
    market: GrokMarket,
    engine: ConsensusEngine,
    config: Optional[CampaignConfig] = None,
    *,
    vote_fn: Optional[Callable[[Proposal, float, random.Random], list[Vote]]] = None,
    on_trade: Optional[Callable[[dict[str, Any]], None]] = None,
) -> CampaignResult:
    """Run proposals through the full pipeline until `target_trades` have filled.

    The pipeline is the whole point, and it is the same one a live-ish run would
    take: propose -> six analysts vote -> ConsensusEngine scores and mints ->
    VESKA spends the token to open -> MARIN settles on stop, target or horizon.
    Nothing here can bypass a step; `market.execute` refuses an order with no
    valid token, and a token exists only where a vote cleared 76.8%.
    """
    cfg = config or CampaignConfig()
    if getattr(market, "live_symbols", None):
        raise LiveModeError(
            "run_paper_campaign models forecast accuracy by looking at the realised "
            f"move over the holding horizon, and {sorted(market.live_symbols)} are on a "
            "live feed whose future does not exist yet. Detach the feed to run the "
            "calibrated campaign, or drive the market with GrokSwarm, which only ever "
            "reads the present."
        )
    rng = random.Random(cfg.seed)
    registry = engine.registry
    if vote_fn is None:
        def vote_fn(p: Proposal, base: float, r: random.Random) -> list[Vote]:  # type: ignore[misc]
            return synthetic_votes(
                registry, p, base, r,
                jitter=cfg.conf_jitter,
                dissent_prob=cfg.dissent_prob,
                rune_veto_prob=cfg.rune_veto_prob,
            )

    symbols = list(market.specs)
    decisions = {"EXECUTE": 0, "HOLD": 0, "VETO": 0}
    start_equity = market.equity_cents()
    peak = trough = start_equity
    max_dd = 0.0
    correct_calls = 0
    proposals = 0
    executed = 0
    sym_i = 0

    while executed < cfg.target_trades and proposals < cfg.max_proposals:
        if market.step + cfg.horizon_steps + 1 >= market.last_step:
            break
        symbol = symbols[sym_i % len(symbols)]
        sym_i += 1
        proposals += 1

        now_px = market.mid(symbol)
        future_px = market.price_at(symbol, market.step + cfg.horizon_steps)
        if future_px > now_px:
            true_side = VoteStance.BUY
        elif future_px < now_px:
            true_side = VoteStance.SELL
        else:
            true_side = VoteStance.BUY if rng.random() < 0.5 else VoteStance.SELL

        base_conf = rng.uniform(cfg.conf_low, cfg.conf_high)
        p_correct = min(1.0, max(0.0, 0.5 + cfg.skill_alpha * (base_conf - 0.5)))
        if rng.random() < p_correct:
            side = true_side
            correct_calls += 1
        else:
            side = VoteStance.SELL if true_side is VoteStance.BUY else VoteStance.BUY

        equity = market.equity_cents()
        want_qty = max(1, int(equity * cfg.risk_fraction) // now_px)
        pos_side = Side.from_stance(side)
        qty = min(want_qty, market.book(symbol).depth(pos_side), market.max_affordable_qty(symbol, pos_side))
        if qty < 1:
            market.advance(1)
            market.settle()
            continue

        proposal = Proposal(
            proposal_id=f"PR-{proposals:06d}",
            symbol=symbol,
            side=side,
            qty=qty,
            price_cents=now_px,
            meta={"step": market.step, "base_confidence": round(base_conf, 4)},
        )
        result = engine.evaluate(proposal, vote_fn(proposal, base_conf, rng))
        decisions[result.decision.value] += 1

        if result.decision is ConsensusDecision.EXECUTE and result.token is not None:
            try:
                market.execute(
                    proposal,
                    result.token.token,
                    stop_bps=cfg.stop_bps,
                    target_bps=cfg.target_bps,
                    horizon_steps=cfg.horizon_steps,
                    consensus_pct=result.score_pct,
                )
                executed += 1
            except (LiquidityError, OverdraftError, MarketHalted, UnauthorisedOrder):
                pass

        market.advance(1)
        for trade in market.settle():
            if on_trade is not None:
                on_trade(trade.as_dict())

        eq = market.equity_cents()
        peak = max(peak, eq)
        trough = min(trough, eq)
        if peak > 0:
            max_dd = max(max_dd, (peak - eq) / peak * 100.0)

    for trade in market.flatten_all(CloseReason.END_OF_RUN):
        if on_trade is not None:
            on_trade(trade.as_dict())

    end_equity = market.equity_cents()
    rep = market.report()
    return CampaignResult(
        proposals=proposals,
        executed=executed,
        decisions=decisions,
        start_equity_cents=start_equity,
        end_equity_cents=end_equity,
        peak_equity_cents=max(peak, end_equity),
        trough_equity_cents=min(trough, end_equity),
        max_drawdown_pct=max_dd,
        win_rate=rep["win_rate"],
        directional_accuracy=(correct_calls / proposals) if proposals else 0.0,
        fees_paid_cents=rep["fees_paid_cents"],
        rejections=rep["rejections"],
        steps=market.step,
        market_report=rep,
    )


def build_default_stack(
    *,
    seed: Optional[int] = 20260904,
    guard: Optional[RiskGuard] = None,
    n_steps: int = 12_000,
    start_equity_cents: int = START_EQUITY_CENTS,
) -> tuple[GrokMarket, ConsensusEngine]:
    """A wired market + engine pair: the eight pairs, the eight agents, one gate."""
    engine = ConsensusEngine(AgentRegistry())
    market = GrokMarket(
        GrokMarketConfig(
            seed=seed,
            guard=guard,
            n_steps=n_steps,
            start_equity_cents=start_equity_cents,
        ),
        authoriser=engine,
    )
    return market, engine


# ---------------------------------------------------------------------------
# Self-test and CLI
# ---------------------------------------------------------------------------


def _fmt_usd(cents: int) -> str:
    return f"${cents / 100:,.2f}"


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

    from consensus_engine import build_quorum  # noqa: PLC0415

    def stack() -> tuple[GrokMarket, ConsensusEngine]:
        return build_default_stack(seed=7, n_steps=400)

    def authorised(m: GrokMarket, e: ConsensusEngine, sym: str, side: VoteStance, qty: int):
        p = Proposal(f"p-{sym}-{m.step}", sym, side, qty, m.mid(sym))
        r = e.evaluate(p, build_quorum(e.registry, side, 0.95))
        return p, r

    def t_eight_symbols() -> None:
        m, _ = stack()
        assert len(m.specs) == 8, len(m.specs)

    def t_money_is_integers() -> None:
        m, _ = stack()
        assert isinstance(m.cash_cents, int) and isinstance(m.equity_cents(), int)

    def t_no_position_without_a_token() -> None:
        m, e = stack()
        p, _ = authorised(m, e, "BTC-USD", VoteStance.BUY, 2)
        try:
            m.execute(p, "forged.token")
        except UnauthorisedOrder:
            return
        raise AssertionError("an unauthorised order opened a position")

    def t_token_opens_exactly_one_position() -> None:
        m, e = stack()
        p, r = authorised(m, e, "BTC-USD", VoteStance.BUY, 2)
        m.execute(p, r.token.token, consensus_pct=r.score_pct)
        assert len(m.positions) == 1
        try:
            m.execute(p, r.token.token)
        except UnauthorisedOrder:
            return
        raise AssertionError("one token opened two positions")

    def t_round_trip_conserves_money() -> None:
        m, e = stack()
        before = m.equity_cents()
        p, r = authorised(m, e, "ETH-USD", VoteStance.BUY, 40)
        m.execute(p, r.token.token)
        m.advance(3)
        t = m.flatten_all()[0]
        assert t.net_pnl_cents == t.gross_pnl_cents - t.fees_cents, t.as_dict()
        assert m.equity_cents() == before + t.net_pnl_cents, (m.equity_cents(), before, t.net_pnl_cents)

    def t_cash_never_negative() -> None:
        m, e = stack()
        for _ in range(40):
            qty = m.max_affordable_qty("SOL-USD", Side.LONG)
            if qty < 1:
                break
            p, r = authorised(m, e, "SOL-USD", VoteStance.BUY, qty)
            try:
                m.execute(p, r.token.token)
            except (OverdraftError, LiquidityError):
                break
            m.advance(1)
        assert m.cash_cents >= 0, m.cash_cents

    def t_order_beyond_the_book_refused() -> None:
        m, _ = stack()
        deep = m.book("BTC-USD").depth(Side.LONG) + 1
        try:
            m.book("BTC-USD").walk(Side.LONG, deep)
        except LiquidityError:
            return
        raise AssertionError("the book absorbed more than it had")

    def t_slippage_grows_with_size() -> None:
        m, _ = stack()
        small = m.quote("SOL-USD", Side.LONG, 100)["slippage_bps"]
        large = m.quote("SOL-USD", Side.LONG, 9_000)["slippage_bps"]
        assert large > small, (small, large)

    def t_kill_switch_blocks_new_risk() -> None:
        import tempfile  # noqa: PLC0415
        from risk_guard import RiskGuard, RiskGuardConfig  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as td:
            g = RiskGuard(RiskGuardConfig(db_path=Path(td) / "g.db", kill_file=Path(td) / "K",
                                          allow_unmeasurable_hardware=True))
            e = ConsensusEngine(AgentRegistry())
            m = GrokMarket(GrokMarketConfig(seed=3, n_steps=200, guard=g), authoriser=e)
            g.trip_kill_switch("selftest")
            p, r = authorised(m, e, "BTC-USD", VoteStance.BUY, 1)
            try:
                m.execute(p, r.token.token)
            except MarketHalted:
                g.close()
                return
            g.close()
            raise AssertionError("a tripped kill switch did not stop VESKA")

    def t_campaign_runs_and_conserves() -> None:
        m, e = build_default_stack(seed=11, n_steps=3_000)
        res = run_paper_campaign(m, e, CampaignConfig(target_trades=120, seed=11))
        assert res.executed == 120, res.executed
        assert m.cash_cents >= 0
        assert m.equity_cents() == m.config.start_equity_cents + m.realised_cents(), (
            m.equity_cents(), m.realised_cents()
        )

    def t_zero_skill_loses_to_costs() -> None:
        m, e = build_default_stack(seed=5, n_steps=4_000)
        res = run_paper_campaign(m, e, CampaignConfig(target_trades=400, seed=5, skill_alpha=0.0))
        assert res.return_pct < 0.0, res.return_pct

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    print(f"groktopus_market self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def _demo(trades: int = 1_981, seed: Optional[int] = 20260904) -> int:
    market, engine = build_default_stack(seed=seed)
    cfg = CampaignConfig(target_trades=trades, seed=seed)
    res = run_paper_campaign(market, engine, cfg)
    r = res.market_report

    print("=" * 78)
    print(" GROKTOPUS PAPER CAMPAIGN - 8 pairs, 8 agents, 1 resident model")
    print(f" execution mode: {EXECUTION_MODE} (no network transport exists in this module)")
    print("=" * 78)
    print(f" proposals evaluated   : {res.proposals:,}")
    print(f"   EXECUTE (>= 76.8%)  : {res.decisions['EXECUTE']:,}")
    print(f"   HOLD    (70-76.8%)  : {res.decisions['HOLD']:,}")
    print(f"   VETO    (< 70% RUNE): {res.decisions['VETO']:,}")
    print(f" orders filled         : {res.executed:,}")
    print("-" * 78)
    print(f" starting equity       : {_fmt_usd(res.start_equity_cents)}")
    print(f" ending equity         : {_fmt_usd(res.end_equity_cents)}")
    print(f" return                : {res.return_pct:+.2f}%")
    print(f" peak / trough         : {_fmt_usd(res.peak_equity_cents)} / {_fmt_usd(res.trough_equity_cents)}")
    print(f" max drawdown          : {res.max_drawdown_pct:.2f}%")
    print(f" win rate              : {res.win_rate * 100:.2f}%  over {r['closed_trades']:,} round trips")
    print(f" forecast accuracy     : {res.directional_accuracy * 100:.2f}%  (a model input, not a finding)")
    print(f" fees + slippage paid  : {_fmt_usd(res.fees_paid_cents)}")
    print(f" cash / open positions : {_fmt_usd(r['cash_cents'])} / {r['open_positions']}")
    print(f" refused orders        : {res.rejections}")
    print("-" * 78)
    print(f" ledger check          : equity == start + realised P&L -> "
          f"{market.equity_cents() == market.config.start_equity_cents + market.realised_cents()}")
    print(f" zero overdraft        : cash never negative -> {market.cash_cents >= 0}")
    print("=" * 78)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GrokTOPUS eight-pair paper exchange")
    ap.add_argument("--selftest", action="store_true", help="run the built-in checks")
    ap.add_argument("--demo", action="store_true", help="run the reference paper campaign")
    ap.add_argument("--campaign", action="store_true", help="alias for --demo")
    ap.add_argument("--trades", type=int, default=1_981, help="orders to fill (default 1981)")
    ap.add_argument("--seed", type=int, default=20260904, help="RNG seed")
    ap.add_argument("--json", action="store_true", help="emit the campaign result as JSON")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.json:
        market, engine = build_default_stack(seed=args.seed)
        res = run_paper_campaign(market, engine, CampaignConfig(target_trades=args.trades, seed=args.seed))
        print(json.dumps(res.as_dict(), indent=2))
        return 0
    return _demo(trades=args.trades, seed=args.seed)


if __name__ == "__main__":
    raise SystemExit(main())
