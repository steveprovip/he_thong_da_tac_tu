"""Delta-neutral funding-carry lane, Binance USD-M <-> Hyperliquid (2026-11-07). Shadow and paper only.

Lives in research/ on purpose: tests/test_venues.py pins that no core/ or live/ trading module imports the
prototype core.venues package. Promoting this lane into trading code is an owner decision that changes that test.

Evidence it rests on: tools/funding_spread_replay.py XV HEDGE|H28|k2, pre-registered 2026-10-10. DEV 2025 picked it;
HOLDOUT 2026-01..09: n 153, +14.59 bps per 28-day round, CI95 by day +9.17..+19.92, 1.90 %/yr on notional. An
instantaneous spread is not that number. Holdout entries started at a median 5.4 % APR signal, the median
instantaneous |BN - HL| spread was 5.5 % APR, and only 8.3 % of coin-8 h windows reached 18 % (2026-01..09).

1. Funding normalization (per_hour_bps, core.funding_executor.bn_interval_hours, realtime_apr). Each rate becomes bps per hour using its own
   interval. Hyperliquid prints hourly. Binance funds every 8 h, or 4 h for the symbols listed by
   GET /fapi/v1/fundingInfo (ENA, HYPE and TAO in 2026); a symbol absent from that list uses the 8 h default. An
   interval that cannot be read is UNKNOWN and the coin is skipped, never assumed 8 h.
       gross APR = (short-leg rate - long-leg rate) per hour x 8760
       net APR   = gross APR - round trip / hold days x 365
2. Entry rule (entry_signal): the replay's rule, unchanged, so the lane trades what was tested.
       S      = trailing 72 h sum of (short-leg prints - long-leg prints); short = the venue with the higher sum
       enter iff S / 3 d x 28 d >= max(k x C, C + 8.5), with k 2, C 15 bps HEDGE round trip, 8.5 the owner's hurdle
   Prints must cover the window with no hole longer than two intervals, else UNKNOWN. Anything else is OBSERVER.
3. Execution (DualLegExecutor). The maker leg rests post-only (GTX / Alo) at its own touch. Each fill increment is
   hedged at once with an IOC limit on the other venue (touch +- hedge_slip_bps, doubled per retry up to
   hedge_slip_max_bps). A shortfall is unwound with a reduce-only IOC on the maker venue. The naked window is
   measured: leg risk is bounded, not zero. A residual that cannot be unwound is BROKEN and halts the lane.
4. Risk. LiquidationGuard reads each venue's cross-margin ratio and liquidation distance; entries need headroom, and
   de-risking or closing always acts on BOTH legs. KillSwitch is plain code checked before every order: equity
   UNKNOWN or drawdown >= 15 % from peak refuses any risk-increasing order (reduce-only unwinds stay allowed).
   core/funding_carry.two_leg_capital_gate still decides whether a pair can be funded at all.
5. Funding tracking (FundingLedger): expected payment per print vs received income per venue. A print without a
   matching receipt is UNKNOWN, never zero.
Modes: 'shadow' (intents only) and 'paper' (core.venues.paper, or any object with the same calls). 'live' is refused:
it needs an owner decision, a real Hyperliquid client and keys read from .env.local, none of which exist here.
"""
from __future__ import annotations

import asyncio
import math
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, Optional

import numpy as np

from core.funding_carry import two_leg_capital_gate
from core.funding_executor import bn_interval_hours  # noqa: F401  (re-exported)
from core.venues.base import PostOnlyRejected, VenueError

HOURS_PER_YEAR = 8760.0
HOUR_MS, DAY_MS = 3_600_000, 86_400_000
HEDGE_ROUND_TRIP_BPS = 15.0          # replay HEDGE cost: (BN maker 2 + HL taker 4.5 + 1 slip) x 2 = (HL 1.5 + BN 5 + 1) x 2
OWNER_HURDLE_BPS = 8.5               # 4 fees + 1.5 spread + 3 adverse selection (owner, 2026-11-07)
SHADOW, PAPER = 'shadow', 'paper'
MODES = (SHADOW, PAPER)
EPS = 1e-12


def _finite(*values) -> bool:
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


# ------------------------------------------------------------------ 1. funding normalization
def interval_from_prints(times_ms) -> Optional[int]:
    """Median gap of recorded prints in whole hours (>= 3 prints), else UNKNOWN."""
    t = np.asarray(times_ms, dtype=np.int64)
    if t.size < 3:
        return None
    h = int(round(float(np.median(np.diff(np.sort(t)))) / HOUR_MS))
    return h if h > 0 else None


def per_hour_bps(rate, interval_h) -> Optional[float]:
    """Fraction per interval -> bps per hour; None when the rate or the interval is UNKNOWN."""
    if not _finite(rate) or not _finite(interval_h) or interval_h <= 0:
        return None
    return float(rate) * 1e4 / float(interval_h)


@dataclass(frozen=True)
class CarryQuote:
    coin: str
    bn_rate: Optional[float]          # fraction per Binance interval (premiumIndex lastFundingRate)
    bn_interval_h: Optional[int]
    hl_rate_1h: Optional[float]       # fraction per hour (metaAndAssetCtxs funding)
    bn_mark: Optional[float]
    hl_mark: Optional[float]


def realtime_apr(q: CarryQuote, cost_bps: float = HEDGE_ROUND_TRIP_BPS, hold_days: float = 28.0) -> tuple:
    """-> (view, '') or (None, reason). The APR assumes today's spread lasts a year: telemetry, not a forecast."""
    bn_h, hl_h = per_hour_bps(q.bn_rate, q.bn_interval_h), per_hour_bps(q.hl_rate_1h, 1)
    if bn_h is None or hl_h is None:
        return None, 'UNKNOWN funding rate or interval'
    if not _finite(q.bn_mark, q.hl_mark) or q.bn_mark <= 0 or q.hl_mark <= 0:
        return None, 'UNKNOWN mark price'
    short, long_ = ('HL', 'BN') if hl_h >= bn_h else ('BN', 'HL')
    p_short, p_long = (q.hl_mark, q.bn_mark) if short == 'HL' else (q.bn_mark, q.hl_mark)
    spread = abs(hl_h - bn_h)
    gross = spread * HOURS_PER_YEAR / 100.0
    return {'coin': q.coin, 'short': short, 'long': long_, 'spread_bps_per_h': spread, 'spread_bps_8h': spread * 8,
            'gross_apr_pct': gross, 'net_apr_pct': gross - cost_bps / hold_days * 365 / 100.0,
            'basis_bps': (p_short - p_long) / (0.5 * (p_short + p_long)) * 1e4}, ''


def breakeven_h(spread_bps_8h, cost_bps: float = HEDGE_ROUND_TRIP_BPS) -> Optional[float]:
    """Hours the present spread must last to repay one round trip; None when it pays nothing (<= 0 or UNKNOWN)."""
    if not _finite(spread_bps_8h) or spread_bps_8h <= 0:
        return None
    return round(cost_bps / (spread_bps_8h / 8.0), 1)


# ------------------------------------------------------------------ 2. entry rule (replay parity)
@dataclass(frozen=True)
class CarryRule:
    hold_days: float = 28.0
    k: float = 2.0
    lookback_h: float = 72.0
    cost_bps: float = HEDGE_ROUND_TRIP_BPS
    hurdle_bps: float = OWNER_HURDLE_BPS

    def __post_init__(self):
        if not _finite(*asdict(self).values()) or min(asdict(self).values()) <= 0 or self.k < 1:
            raise ValueError('carry rule values must be finite and positive, k >= 1')

    @property
    def needed_bps(self) -> float:
        return max(self.k * self.cost_bps, self.cost_bps + self.hurdle_bps)


def window_sum(t: np.ndarray, r: np.ndarray, lo: int, hi: int) -> float:
    """Sum of prints r at times t in (lo, hi]; t sorted (= tools/funding_spread_replay.window_sum)."""
    return float(r[np.searchsorted(t, lo, 'right'):np.searchsorted(t, hi, 'right')].sum())


def covered(t: np.ndarray, lo: int, hi: int, step_ms: int) -> bool:
    """Prints span (lo, hi] with no hole longer than 2 x step (= tools/funding_spread_replay.covered)."""
    i, j = np.searchsorted(t, lo, 'right'), np.searchsorted(t, hi, 'right')
    if j - i < 1:
        return False
    return bool(np.diff(np.r_[lo, t[i:j], hi]).max() <= 2 * step_ms)


@dataclass(frozen=True)
class EntrySignal:
    enter: bool
    short: Optional[str]
    spread_bps: Optional[float]       # S over the lookback
    expected_bps: Optional[float]     # S / lookback days x hold days
    needed_bps: float
    reason: str


def entry_signal(bn, hl, t_ms: int, rule: CarryRule = CarryRule(), bn_step_ms: int = 8 * HOUR_MS + 1,
                 hl_step_ms: int = HOUR_MS) -> EntrySignal:
    """Decision just after a Binance print at t_ms from print arrays bn = (t_ms, bps) and hl = (t_ms, bps)."""
    need = rule.needed_bps
    if bn is None or hl is None:
        return EntrySignal(False, None, None, None, need, 'UNKNOWN: no prints for a venue')
    (bt, br), (ht, hr) = bn, hl
    lo = int(t_ms - rule.lookback_h * HOUR_MS)
    if not covered(bt, lo, t_ms, bn_step_ms) or not covered(ht, lo, t_ms, hl_step_ms):
        return EntrySignal(False, None, None, None, need, 'UNKNOWN: hole in the lookback prints')
    f_bn, f_hl = window_sum(bt, br, lo, t_ms), window_sum(ht, hr, lo, t_ms)
    short = 'BN' if f_bn >= f_hl else 'HL'
    s = abs(f_bn - f_hl)
    expected = s / (rule.lookback_h / 24.0) * rule.hold_days
    if s <= 0 or expected < need:
        return EntrySignal(False, short, s, expected, need,
                           f'OBSERVER: expected {expected:.1f} < {need:g} bps over {rule.hold_days:g} d')
    return EntrySignal(True, short, s, expected, need, f'expected {expected:.1f} >= {need:g} bps')


# ------------------------------------------------------------------ 4. kill switch and liquidation guard
class KillSwitch:
    """Plain-code gate checked before every risk-increasing order. Tripping is sticky until a human resets it."""

    def __init__(self, max_drawdown: float = 0.15):
        if not 0 < max_drawdown < 1:
            raise ValueError('max_drawdown must be in (0, 1)')
        self.max_drawdown, self.peak, self.halted = float(max_drawdown), None, None

    def check(self, equity) -> tuple:
        if self.halted:
            return False, f'HALTED: {self.halted}'
        if not _finite(equity) or equity <= 0:
            return False, 'equity UNKNOWN'
        self.peak = float(equity) if self.peak is None else max(self.peak, float(equity))
        dd = 1.0 - float(equity) / self.peak
        if dd >= self.max_drawdown - EPS:
            self.halt(f'drawdown {dd:.2%} >= {self.max_drawdown:.0%} from peak {self.peak:.2f}')
            return False, f'HALTED: {self.halted}'
        return True, f'drawdown {dd:.2%}'

    def halt(self, why: str) -> None:
        self.halted = self.halted or why


OK, BLOCK_ENTRY, DERISK, CLOSE = 'OK', 'BLOCK_ENTRY', 'DERISK', 'CLOSE'
_SEVERITY = {OK: 0, BLOCK_ENTRY: 1, DERISK: 2, CLOSE: 3}


@dataclass(frozen=True)
class GuardPolicy:
    max_leverage: float = 2.0                 # 2026-10-10 f: L <= 2 (28-day holds of alts that can move +-50 %)
    entry_max_margin_ratio: float = 0.35
    derisk_margin_ratio: float = 0.60
    close_margin_ratio: float = 0.80
    entry_min_liq_dist_pct: float = 25.0
    derisk_liq_dist_pct: float = 12.0
    close_liq_dist_pct: float = 6.0
    derisk_fraction: float = 0.5
    unknown_checks_before_derisk: int = 3

    def __post_init__(self):
        if not (0 < self.entry_max_margin_ratio < self.derisk_margin_ratio < self.close_margin_ratio < 1):
            raise ValueError('need entry < derisk < close margin ratios, all in (0, 1)')
        if not (self.entry_min_liq_dist_pct > self.derisk_liq_dist_pct > self.close_liq_dist_pct > 0):
            raise ValueError('need entry > derisk > close liquidation distances')
        if not (0 < self.derisk_fraction < 1 and 0 < self.max_leverage and self.unknown_checks_before_derisk >= 1):
            raise ValueError('invalid de-risk fraction, leverage or unknown streak')


class LiquidationGuard:
    """One action for the PAIR (never one leg alone): OK, BLOCK_ENTRY, DERISK (cut both legs by derisk_fraction)
    or CLOSE (both legs)."""

    def __init__(self, policy: GuardPolicy = GuardPolicy()):
        self.p, self.unknown_streak = policy, 0

    def assess(self, legs: dict, has_position: bool, leverage: Optional[float] = None) -> tuple:
        """legs = {venue: (margin_view, position_view)}; margin_view {'margin_ratio', ...}, position_view
        {'mark', 'liq', ...}; a missing field is UNKNOWN. -> (action, reasons)."""
        p, action, why, unknown = self.p, OK, [], not legs
        if not legs:
            why.append('no margin views: UNKNOWN')
        for venue, (margin, pos) in sorted(legs.items()):
            mr = (margin or {}).get('margin_ratio')
            mark, liq = (pos or {}).get('mark'), (pos or {}).get('liq')
            dist = abs(mark - liq) / mark * 100 if _finite(mark, liq) and mark > 0 else None
            if not _finite(mr) or (has_position and dist is None):
                unknown = True
                why.append(f'{venue}: margin ratio or liquidation price UNKNOWN')
                continue
            for level, mr_cap, dist_floor in ((CLOSE, p.close_margin_ratio, p.close_liq_dist_pct),
                                              (DERISK, p.derisk_margin_ratio, p.derisk_liq_dist_pct)):
                if mr >= mr_cap or (dist is not None and dist <= dist_floor):
                    if _SEVERITY[level] > _SEVERITY[action]:
                        action = level
                    why.append(f'{venue}: {level} (margin ratio {mr:.2f}, liq distance '
                               f'{"n/a" if dist is None else f"{dist:.1f} %"})')
                    break
            else:
                if mr > p.entry_max_margin_ratio or (dist is not None and dist < p.entry_min_liq_dist_pct):
                    action = action if _SEVERITY[action] >= 1 else BLOCK_ENTRY
                    why.append(f'{venue}: no headroom for entries (margin ratio {mr:.2f})')
        if leverage is not None and (not _finite(leverage) or leverage > p.max_leverage + EPS):
            action = action if _SEVERITY[action] >= 1 else BLOCK_ENTRY
            why.append(f'leverage {leverage} > {p.max_leverage:g}')
        self.unknown_streak = self.unknown_streak + 1 if unknown else 0
        if unknown:
            if has_position and self.unknown_streak >= p.unknown_checks_before_derisk:
                action = action if _SEVERITY[action] >= 2 else DERISK
                why.append(f'UNKNOWN for {self.unknown_streak} checks with a position open')
            else:
                action = action if _SEVERITY[action] >= 1 else BLOCK_ENTRY
        return action, why


# ------------------------------------------------------------------ 5. funding ledger
class FundingLedger:
    """Expected funding per print vs received income. Payment = -qty x mark x rate (longs pay when rate > 0)."""

    def __init__(self, match_window_ms: int = 60_000, rel_tol: float = 0.05, abs_tol: float = 1e-6):
        self.window, self.rel_tol, self.abs_tol = int(match_window_ms), float(rel_tol), float(abs_tol)
        self.expected: list = []
        self.received: list = []

    def expect(self, venue: str, coin: str, t_ms: int, rate: float, qty: float, mark: float) -> float:
        amount = -float(qty) * float(mark) * float(rate)
        self.expected.append({'venue': venue, 'coin': coin, 't': int(t_ms), 'amount': amount})
        return amount

    def receive(self, venue: str, coin: str, t_ms: int, amount: float) -> None:
        self.received.append({'venue': venue, 'coin': coin, 't': int(t_ms), 'amount': float(amount)})

    def reconcile(self) -> dict:
        used, rows = set(), []
        for e in self.expected:
            hit = next((i for i, r in enumerate(self.received) if i not in used and r['venue'] == e['venue']
                        and r['coin'] == e['coin'] and abs(r['t'] - e['t']) <= self.window), None)
            if hit is None:
                rows.append(dict(e, received=None, status='MISSING'))
                continue
            used.add(hit)
            got = self.received[hit]['amount']
            ok = abs(got - e['amount']) <= max(self.abs_tol, self.rel_tol * abs(e['amount']))
            rows.append(dict(e, received=got, status='MATCHED' if ok else 'MISMATCH'))
        extra = [dict(r, status='UNEXPECTED') for i, r in enumerate(self.received) if i not in used]
        known = [r for r in rows if r['received'] is not None]
        return {'rows': rows + extra, 'expected_usdt': sum(r['amount'] for r in rows),
                'received_usdt': sum(r['received'] for r in known) + sum(r['amount'] for r in extra),
                'missing': sum(r['status'] == 'MISSING' for r in rows),
                'mismatch': sum(r['status'] == 'MISMATCH' for r in rows), 'unexpected': len(extra)}


# ------------------------------------------------------------------ 3. dual-leg execution
@dataclass(frozen=True)
class ExecPolicy:
    maker_timeout_s: float = 30.0
    poll_s: float = 0.5
    hedge_slip_bps: float = 3.0
    hedge_slip_max_bps: float = 12.0
    hedge_retries: int = 3
    unwind_slip_bps: float = 25.0


@dataclass
class PairResult:
    state: str                        # REFUSED, REJECTED, NO_FILL, HEDGED, UNWOUND, BROKEN
    reason: str = ''
    maker_filled: float = 0.0
    hedge_filled: float = 0.0
    unwound: float = 0.0
    naked_qty_max: float = 0.0
    naked_s_max: float = 0.0
    maker_avg: Optional[float] = None
    hedge_avg: Optional[float] = None
    orders: list = field(default_factory=list)


def _opposite(side: str) -> str:
    return 'SELL' if side == 'BUY' else 'BUY'


class DualLegExecutor:
    """Maker leg on `maker` (post-only), hedge leg on `hedge` (IOC limit). Venues need best_bid_ask,
    place_post_only, place_ioc_limit, order_status, cancel_order (core.venues.paper.PaperPerpVenue has them)."""

    TERMINAL = ('FILLED', 'CANCELED', 'PARTIALLY_FILLED_CANCELED', 'EXPIRED', 'PARTIALLY_FILLED_EXPIRED')

    def __init__(self, maker, hedge, kill: KillSwitch, equity_fn: Callable, policy: ExecPolicy = ExecPolicy(),
                 clock: Callable[[], float] = time.monotonic, sleep: Callable = asyncio.sleep):
        self.maker, self.hedge, self.kill, self.equity_fn = maker, hedge, kill, equity_fn
        self.p, self.clock, self.sleep = policy, clock, sleep

    async def _pre_order(self, reduce_only: bool) -> tuple:
        """The kill switch, before every order. Reduce-only orders only shrink exposure, so they always pass."""
        if reduce_only:
            return True, 'reduce-only'
        return self.kill.check(await self.equity_fn())

    async def _ioc(self, venue, symbol: str, side: str, qty: float, slip_bps: float, reduce_only: bool,
                   res: PairResult) -> tuple:
        ok, why = await self._pre_order(reduce_only)
        if not ok:
            res.orders.append({'venue': venue.name, 'kind': 'IOC', 'refused': why})
            return 0.0, None
        bid, ask = await venue.best_bid_ask(symbol)
        limit = ask * (1 + slip_bps / 1e4) if side == 'BUY' else bid * (1 - slip_bps / 1e4)
        try:
            r = await venue.place_ioc_limit(symbol, side, qty, limit, reduce_only=reduce_only)
        except VenueError as exc:
            res.orders.append({'venue': venue.name, 'kind': 'IOC', 'side': side, 'qty': qty, 'error': str(exc)})
            return 0.0, None
        res.orders.append({'venue': venue.name, 'kind': 'IOC', 'side': side, 'qty': qty, 'limit': limit,
                           'filled': r['filled_qty'], 'reduce_only': reduce_only})
        return float(r['filled_qty']), r.get('avg_px')

    async def _hedge(self, symbol: str, side: str, qty: float, reduce_only: bool, res: PairResult) -> float:
        left, notional = qty, 0.0
        cap = self.p.unwind_slip_bps if reduce_only else self.p.hedge_slip_max_bps   # a closing leg must get out
        for i in range(self.p.hedge_retries + 1):
            got, px = await self._ioc(self.hedge, symbol, side, left, min(self.p.hedge_slip_bps * 2 ** i, cap),
                                      reduce_only, res)
            if got > 0:
                left -= got
                notional += got * px
            if left <= EPS:
                break
        done = qty - max(left, 0.0)
        if done > 0:
            prev = res.hedge_filled
            res.hedge_avg = ((res.hedge_avg or 0.0) * prev + notional) / (prev + done)
            res.hedge_filled = prev + done
        return done

    async def run(self, maker_symbol: str, hedge_symbol: str, maker_side: str, qty: float,
                  reduce_only: bool = False) -> PairResult:
        """Open (reduce_only False) or close (True) one pair. The hedge side is the opposite of the maker side."""
        res = PairResult('NO_FILL')
        ok, why = await self._pre_order(reduce_only)
        if not ok:
            return PairResult('REFUSED', why)
        bid, ask = await self.maker.best_bid_ask(maker_symbol)
        px = bid if maker_side == 'BUY' else ask              # join our own side of the touch: never crosses
        try:
            r = await self.maker.place_post_only(maker_symbol, maker_side, qty, px, reduce_only=reduce_only)
        except PostOnlyRejected as exc:
            return PairResult('REJECTED', str(exc))
        oid, hedge_side, t0 = r['order_id'], _opposite(maker_side), self.clock()
        res.orders.append({'venue': self.maker.name, 'kind': 'POST_ONLY', 'side': maker_side, 'qty': qty, 'px': px})
        naked_since, cancelled = None, False
        while True:
            st = await self.maker.order_status(maker_symbol, oid)
            new = float(st['filled_qty']) - res.maker_filled
            if new > EPS:
                res.maker_filled += new
                res.maker_avg = st.get('avg_px')
                naked_since = naked_since if naked_since is not None else self.clock()
                res.naked_qty_max = max(res.naked_qty_max, res.maker_filled - res.hedge_filled - res.unwound)
                short = new - await self._hedge(hedge_symbol, hedge_side, new, reduce_only, res)
                if short > EPS and not reduce_only:           # opening: take the unhedged part back off
                    got, _ = await self._ioc(self.maker, maker_symbol, hedge_side, short, self.p.unwind_slip_bps,
                                             True, res)
                    res.unwound += got
                if res.maker_filled - res.hedge_filled - res.unwound <= EPS:
                    res.naked_s_max = max(res.naked_s_max, self.clock() - naked_since)
                    naked_since = None
            if st['status'] in self.TERMINAL:
                break
            if not cancelled and self.clock() - t0 >= self.p.maker_timeout_s:
                await self.maker.cancel_order(maker_symbol, oid)
                cancelled = True
                continue                                      # read once more: fills may race the cancel
            await self.sleep(self.p.poll_s)
        residual = res.maker_filled - res.hedge_filled - res.unwound
        if naked_since is not None:
            res.naked_s_max = max(res.naked_s_max, self.clock() - naked_since)
        if res.maker_filled <= EPS:
            res.state, res.reason = 'NO_FILL', 'maker order did not fill before the timeout'
        elif residual > EPS:
            res.state, res.reason = 'BROKEN', f'{residual:g} unhedged after retries and unwind'
            self.kill.halt(f'BROKEN_HEDGE {maker_symbol}/{hedge_symbol} residual {residual:g}')
        elif res.unwound > EPS:
            res.state, res.reason = 'UNWOUND', f'{res.unwound:g} of the maker fill was unwound (hedge short)'
        else:
            res.state, res.reason = 'HEDGED', 'both legs filled'
        return res


# ------------------------------------------------------------------ orchestration (decisions only)
class CarryLane:
    """Turns prints, positions and risk views into one intent per coin: OPEN, CLOSE, DERISK, HOLD or OBSERVE."""

    def __init__(self, mode: str = SHADOW, rule: CarryRule = CarryRule(), guard: Optional[LiquidationGuard] = None,
                 kill: Optional[KillSwitch] = None):
        if mode not in MODES:
            raise ValueError("carry lane runs 'shadow' or 'paper' only: live needs an owner decision, a real "
                             'Hyperliquid client and keys read from .env.local')
        self.mode, self.rule = mode, rule
        self.guard, self.kill = guard or LiquidationGuard(), kill or KillSwitch()

    def decide(self, coin: str, bn, hl, t_ms: int, equity_usdt, notional_per_leg: float, legs: Optional[dict] = None,
               held: Optional[dict] = None, leverage: float = 1.0) -> dict:
        """held = {'opened_ms', 'short'} of the open pair, or None."""
        base = {'coin': coin, 'mode': self.mode, 't': int(t_ms)}
        if self.mode == SHADOW and legs is None:          # a virtual pair has no venue margin to read
            action, why = OK, ['shadow: no venue margin']
        else:
            action, why = self.guard.assess(legs or {}, held is not None, leverage)
        if held is not None:
            if action in (CLOSE, DERISK):
                return dict(base, action=action, reason='; '.join(why), short=held['short'])
            if t_ms >= held['opened_ms'] + self.rule.hold_days * DAY_MS:
                return dict(base, action='CLOSE', reason=f'hold {self.rule.hold_days:g} d done', short=held['short'])
            return dict(base, action='HOLD', reason='inside the hold', short=held['short'])
        sig = entry_signal(bn, hl, t_ms, self.rule)
        if not sig.enter:
            return dict(base, action='OBSERVE', reason=sig.reason, signal=asdict(sig))
        ok, kill_why = self.kill.check(equity_usdt)
        # fundability only (both legs >= venue minimums, notional <= equity); it never authorizes a live order here
        gate = two_leg_capital_gate(equity_usdt if _finite(equity_usdt) else float('nan'), notional_per_leg * 2,
                                    requested_mode='live')
        blocked = ([] if ok else [f'kill switch: {kill_why}']) + ([] if gate.allowed else [f'capital: {gate.reason}'])
        blocked += [] if action == OK else [f'guard {action}: ' + '; '.join(why)]
        if blocked:
            return dict(base, action='OBSERVE', reason='; '.join(blocked), signal=asdict(sig))
        return dict(base, action='OPEN', reason=sig.reason, short=sig.short, long='HL' if sig.short == 'BN' else 'BN',
                    notional_per_leg=notional_per_leg, signal=asdict(sig))


# ------------------------------------------------------------------ simulated book for the demo engine loop
class CarrySim:
    """Simulated dual-leg carry book run by core.engine.Runner.funding_loop (owner request, 2026-11-07). Every order is
    simulated on core.venues.paper accounts; nothing reaches Binance or Hyperliquid.

    Entry (owner rule, the RFC thresholds, NOT the replay-validated H28|k2 rule): instantaneous spread >= 2.5 bps/8 h,
    gross APR >= 20 % (2.5 bps/8 h is already 27.4 %), basis skew >= +1.5 bps, at most `slots` pairs. The replay says
    such instantaneous spreads decay: holdout entries started near a 5.4 % APR signal and realized 1.90 %/yr net.
    Exit: the spread flips or falls to <= 0. Maker leg on Binance (post-only at its touch, assumed filled: the replay's
    HEDGE assumption); limit IOC hedge on Hyperliquid at its touch + 3 bps, widened to 12. Each pair has its own pair of
    isolated paper accounts at leverage 1. Funding accrues per refresh at the current per-hour rates (an estimate
    between prints). Kill switch: 15 % drawdown of the simulated book halts new pairs; the guard can close a pair.

    11-10 settlement='discrete' (the demo engine loop): funding is paid only at the venue's settlement instants that passed
    while the pair was open - Hyperliquid every hour on the hour, Binance at 00:00 / 08:00 / 16:00 UTC - at the rate last
    seen BEFORE the instant (the quote after it already estimates the next period). A pair opened and closed inside one
    Binance window earns no Binance funding, which the continuous estimate credited. A Binance symbol on a 4 h interval is
    settled as its 8 h equivalent on the 8 h grid. A refresh with UNKNOWN quotes settles nothing; the instants it skipped
    are settled at the next known refresh from the last known rates. min_net_apr_pct adds a net-APR entry floor.

    11-10 (g) basis_freeze_bps (15): the -3 bps basis stop closes a pair on a slow drift; a drag of 15 bps or more in one
    refresh is a venue dislocation, so the book also opens no NEW pair for one rotation interval (view key basis_freeze).
    Telemetry: fees_total_usdt, net_apr_realized_pct (the book after all simulated costs) and breakeven_h per coin."""

    HL_NAME = {'1000PEPE': 'kPEPE'}
    ROTATION_PRIORITY = {'1000PEPE': 0, 'WLD': 1}
    SETTLE_S = {'bn': 8 * 3600.0, 'hl': 3600.0}

    def __init__(self, capital_usdt: float, slots: int = 3, utilization: float = 0.7, min_spread_8h: float = 2.5,
                 min_apr_pct: float = 20.0, min_basis_bps: float = 1.5, half_spread_bps: float = 0.5,
                 clock: Callable[[], float] = time.time, rotation_interval_s: float = 3600.0,
                 basis_stop_bps: float = -3.0, min_net_apr_pct: float = 0.0, settlement: str = 'continuous',
                 basis_freeze_bps: float = 15.0):
        if (not (_finite(capital_usdt, rotation_interval_s, basis_stop_bps, min_net_apr_pct, basis_freeze_bps)
                 and capital_usdt > 0 and slots >= 1 and 0 < utilization <= 1 and rotation_interval_s > 0
                 and basis_stop_bps < 0 and basis_freeze_bps > 0)):
            raise ValueError('need positive capital, slots >= 1, utilization in (0, 1]')
        if settlement not in ('continuous', 'discrete'):
            raise ValueError('settlement must be continuous or discrete')
        self.min_net_apr, self.settlement = float(min_net_apr_pct), settlement
        self.cash = self.start = float(capital_usdt)
        self.slots, self.utilization = int(slots), float(utilization)
        self.min_spread, self.min_apr, self.min_basis = float(min_spread_8h), float(min_apr_pct), float(min_basis_bps)
        self.half = float(half_spread_bps) / 1e4
        self.clock = clock
        self.rotation_interval_s, self.basis_stop_bps = float(rotation_interval_s), float(basis_stop_bps)
        self.kill, self.guard = KillSwitch(0.15), LiquidationGuard()
        self.pairs: dict = {}
        self.closed: list = []
        self.events: list = []
        self.last_t: Optional[float] = None
        self.last_rotation_t: Optional[float] = None
        self.rotation_targets: list = []
        self.basis_eject_until: dict = {}
        self.first_t: Optional[float] = None
        self.funding_closed = 0.0                            # simulated funding already banked by closed pairs
        self.fees_closed = 0.0                               # simulated fees already paid by closed pairs
        self.basis_freeze_bps = float(basis_freeze_bps)      # 11-10 (g): a held pair's basis moved this far against it
        self.basis_freeze: Optional[dict] = None             # ... since entry -> no NEW pair for one rotation interval

    @property
    def notional_per_leg(self) -> float:
        return self.start * self.utilization / (2.0 * self.slots)

    def equity(self) -> float:
        return self.cash + sum(p['bn'].equity() + p['hl'].equity() for p in self.pairs.values())

    def _book(self, venue, sym: str, mark: float) -> None:
        venue.set_book(sym, mark * (1 - self.half), mark * (1 + self.half))

    async def _open(self, coin: str, v: dict, q) -> None:
        from core.venues.paper import PaperPerpVenue
        n = self.notional_per_leg
        bn = PaperPerpVenue('BN', 2.0, 5.0, n, auto_fill=True)
        hl = PaperPerpVenue('HL', 1.5, 4.5, n)
        bs, hs = f'{coin}USDT', self.HL_NAME.get(coin, coin)
        self._book(bn, bs, q.bn_mark)
        self._book(hl, hs, q.hl_mark)

        async def equity():
            return self.equity() + bn.equity() + hl.equity()           # the new pair's cash is still in self.cash
        ex = DualLegExecutor(bn, hl, self.kill, equity, ExecPolicy(maker_timeout_s=0.0, poll_s=0.0), self.clock)
        maker_side = 'SELL' if v['short'] == 'BN' else 'BUY'
        self.cash -= 2 * n
        res = await ex.run(bs, hs, maker_side, n / q.bn_mark)
        if res.state != 'HEDGED':
            self.cash += bn.equity() + hl.equity()
            self.events.append(f'CARRY SIM {coin}: entry {res.state} ({res.reason}) - no pair')
            return
        self.pairs[coin] = {'bn': bn, 'hl': hl, 'bs': bs, 'hs': hs, 'ex': ex, 'short': v['short'], 'long': v['long'],
                            'qty': res.maker_filled, 'maker_side': maker_side, 'opened': self.clock(),
                            'spread_8h_entry': v['spread_bps_8h'], 'spread_8h': v['spread_bps_8h'],
                            'gross_apr_pct': v['gross_apr_pct'], 'net_apr_pct': v['net_apr_pct'],
                            'entry_bn_mark': q.bn_mark, 'entry_hl_mark': q.hl_mark, 'basis_drag_bps': 0.0,
                            'rates': (q.bn_rate_8h, q.hl_rate_1h), 'settle_t': self.clock(), 'settled': {'bn': 0, 'hl': 0}}
        self.events.append(f'CARRY SIM OPEN {coin}: short {v["short"]} / long {v["long"]} {v["spread_bps_8h"]:+.2f} '
                           f'bps/8h (net APR {v["net_apr_pct"]:.1f} %) · {n:,.0f} USDT per leg · BN post-only + HL IOC '
                           f'(simulated)')

    async def _close(self, coin: str, why: str) -> None:
        p = self.pairs[coin]
        res = await p['ex'].run(p['bs'], p['hs'], _opposite(p['maker_side']), p['qty'], reduce_only=True)
        if res.state != 'HEDGED':
            self.events.append(f'CARRY SIM {coin}: close {res.state} ({res.reason}) - pair kept')
            return
        n = self.notional_per_leg
        got = p['bn'].equity() + p['hl'].equity()
        self.cash += got
        self.pairs.pop(coin)
        fund = p['bn'].funding + p['hl'].funding
        self.funding_closed += fund
        self.fees_closed += p['bn'].fees + p['hl'].fees
        self.closed.append({'coin': coin, 'why': why, 'net_usdt': round(got - 2 * n, 4), 'funding_usdt': round(fund, 4),
                            'days': round((self.clock() - p['opened']) / 86400, 3)})
        self.events.append(f'CARRY SIM CLOSE {coin}: {why} · net {got - 2 * n:+.4f} USDT (simulated)')

    def _accrue(self, coin: str, q, dt_h: float) -> None:
        p = self.pairs[coin]
        if self.settlement == 'discrete':
            return self._settle(p, q)
        if dt_h <= 0 or q.bn_rate_8h is None or q.hl_rate_1h is None:
            return                                           # UNKNOWN rates: nothing accrues, nothing is guessed
        t = int(self.clock() * 1000)
        p['bn'].settle_funding(p['bs'], q.bn_rate_8h / 8.0 * dt_h, t)
        p['hl'].settle_funding(p['hs'], q.hl_rate_1h * dt_h, t)

    def _settle(self, p: dict, q) -> None:
        """Discrete funding: one payment per settlement instant in (p['settle_t'], now], at the rates stored before it."""
        now, rates = self.clock(), p['rates']
        for venue, sym, rate in (('bn', p['bs'], rates[0]), ('hl', p['hs'], rates[1])):
            step = self.SETTLE_S[venue]
            n = int(now // step) - int(p['settle_t'] // step)
            if n > 0 and rate is not None:
                for _ in range(n):
                    p[venue].settle_funding(sym, rate, int(now * 1000))     # bn: fraction per 8 h, hl: per 1 h
                p['settled'][venue] += n
        p['settle_t'] = now
        if q.bn_rate_8h is not None and q.hl_rate_1h is not None:
            p['rates'] = (q.bn_rate_8h, q.hl_rate_1h)

    def _entry_ok(self, v: dict) -> bool:
        return (v['spread_bps_8h'] >= self.min_spread and v['gross_apr_pct'] >= self.min_apr
                and v['net_apr_pct'] > 0 and v['net_apr_pct'] >= self.min_net_apr
                and v['basis_bps'] >= self.min_basis)

    async def step(self, quotes) -> dict:
        """One refresh: accrue, mark, guard and exit the held pairs, then open new ones. -> telemetry view."""
        now = self.clock()
        dt_h = 0.0 if self.last_t is None else max(now - self.last_t, 0.0) / 3600.0
        self.first_t = now if self.first_t is None else self.first_t
        self.last_t, self.events = now, []
        rotation_due = (self.last_rotation_t is None or
                        now - self.last_rotation_t >= self.rotation_interval_s - EPS)
        views = {}
        for q in quotes:
            v, why = realtime_apr(CarryQuote(q.coin, q.bn_rate_8h, 8, q.hl_rate_1h, q.bn_mark, q.hl_mark))
            views[q.coin] = (q, v, why)
        for coin in list(self.pairs):
            q, v, _ = views.get(coin, (None, None, 'UNKNOWN'))
            if q is None or v is None:
                continue                                     # UNKNOWN quote: hold, never read as a flip
            p = self.pairs[coin]
            self._accrue(coin, q, dt_h)
            self._book(p['bn'], p['bs'], q.bn_mark)
            self._book(p['hl'], p['hs'], q.hl_mark)
            p['spread_8h'] = v['spread_bps_8h'] if v['short'] == p['short'] else -v['spread_bps_8h']
            p['gross_apr_pct'] = p['spread_8h'] * 3 * 365 / 100
            p['net_apr_pct'] = p['gross_apr_pct'] - HEDGE_ROUND_TRIP_BPS / 28.0 * 365 / 100.0
            short_entry, long_entry = ((p['entry_bn_mark'], p['entry_hl_mark']) if p['short'] == 'BN'
                                       else (p['entry_hl_mark'], p['entry_bn_mark']))
            short_now, long_now = ((q.bn_mark, q.hl_mark) if p['short'] == 'BN' else (q.hl_mark, q.bn_mark))
            p['basis_drag_bps'] = ((long_now / long_entry) - (short_now / short_entry)) * 1e4
            if p['basis_drag_bps'] <= -self.basis_freeze_bps + EPS:      # far past the -3 bps stop in one refresh: a dislocation
                self.basis_freeze = {'coin': coin, 'drag_bps': round(p['basis_drag_bps'], 2), 't': now,
                                     'until': now + self.rotation_interval_s}
                self.events.append(f'CARRY SIM BASIS DIVERGENCE {coin}: {p["basis_drag_bps"]:+.2f} bps against the pair since '
                                   f'entry (limit -{self.basis_freeze_bps:g}) -> no new pair for '
                                   f'{self.rotation_interval_s / 60:.0f} min')
            legs ={'BN': (await p['bn'].margin_view(), await p['bn'].position_view(p['bs'])),
                    'HL': (await p['hl'].margin_view(), await p['hl'].position_view(p['hs']))}
            action, why = self.guard.assess(legs, True, 1.0)
            if action in (CLOSE, DERISK):
                await self._close(coin, f'guard {action}: {"; ".join(why)}')
            elif p['basis_drag_bps'] <= self.basis_stop_bps + EPS:
                next_scan = now + self.rotation_interval_s if rotation_due else \
                    (self.last_rotation_t or now) + self.rotation_interval_s
                self.basis_eject_until[coin] = next_scan
                await self._close(coin, f'EJECT-BASIS drag {p["basis_drag_bps"]:+.2f} bps <= '
                                       f'{self.basis_stop_bps:+.2f} bps')
            elif p['spread_8h'] <= 0:
                await self._close(coin, f'spread {p["spread_8h"]:+.2f} bps/8h (flipped or gone)')
        ok, kill_why = self.kill.check(self.equity())
        self.basis_eject_until = {c: until for c, until in self.basis_eject_until.items() if until > now}
        ranked = sorted((x for x in views.values() if x[1] is not None and self._entry_ok(x[1])
                         and self.basis_eject_until.get(x[0].coin, -math.inf) <= now),
                        key=lambda x: (-x[1]['net_apr_pct'], self.ROTATION_PRIORITY.get(x[0].coin, 99), x[0].coin))
        if rotation_due:
            self.last_rotation_t = now
            self.rotation_targets = [q.coin for q, _, _ in ranked[:self.slots]]
            self.events.append('CARRY SIM ROTATION SCAN: ' +
                               (', '.join(self.rotation_targets) if self.rotation_targets else 'no eligible pairs') +
                               ' · highest Net APR; 1000PEPE/WLD preferred on ties')
            target = set(self.rotation_targets)
            for coin in list(self.pairs):
                q, v, _ = views.get(coin, (None, None, 'UNKNOWN'))
                if coin not in target and v is not None:
                    await self._close(coin, f'ROTATE net APR rank outside top {self.slots}')
        if self.basis_freeze and self.basis_freeze['until'] <= now:
            self.basis_freeze = None
        for q, v, _ in ranked:
            if (ok and not self.basis_freeze and (not rotation_due or q.coin in self.rotation_targets)
                    and q.coin not in self.pairs and len(self.pairs) < self.slots
                    and self.cash >= 2 * self.notional_per_leg - 1e-9):
                await self._open(q.coin, v, q)
        if not rotation_due:
            held = list(self.pairs)
            self.rotation_targets = [c for c in self.rotation_targets if c in self.pairs]
            self.rotation_targets.extend(c for c in held if c not in self.rotation_targets)
        return self.view(views, ok, kill_why)

    def view(self, views: dict, ok: bool = True, kill_why: str = '') -> dict:
        eq, n = self.equity(), self.notional_per_leg
        rows = []
        for coin, (q, v, why) in views.items():
            if v is None:
                rows.append({'coin': coin, 'status': 'UNKNOWN', 'why': why})
                continue
            eligible = self._entry_ok(v)
            rows.append({'coin': coin, 'short': v['short'], 'long': v['long'], 'breakeven_h': breakeven_h(v['spread_bps_8h']),
                         'spread_8h': round(v['spread_bps_8h'], 3), 'apr_pct': round(v['net_apr_pct'], 2),
                         'net_apr_pct': round(v['net_apr_pct'], 2), 'gross_apr_pct': round(v['gross_apr_pct'], 2),
                         'basis_bps': round(v['basis_bps'], 2), 'selected': coin in self.rotation_targets,
                         'status': ('HELD' if coin in self.pairs else 'TARGET' if eligible and coin in self.rotation_targets
                                    else 'ENTRY' if eligible else 'BELOW')})
        rows.sort(key=lambda r: -r.get('net_apr_pct', -1e9))
        fund_open = sum(p['bn'].funding + p['hl'].funding for p in self.pairs.values())
        fund_total = fund_open + self.funding_closed
        fees_total = self.fees_closed + sum(p['bn'].fees + p['hl'].fees for p in self.pairs.values())
        days =0.0 if self.first_t is None else max(self.clock() - self.first_t, 0.0) / 86400.0
        pos = [{'coin': c, 'short': p['short'], 'long': p['long'], 'notional_per_leg': round(n, 2),
                'opened': p['opened'], 'spread_8h_entry': round(p['spread_8h_entry'], 3),
                'spread_8h': round(p['spread_8h'], 3), 'apr_pct': round(p['net_apr_pct'], 2),
                'net_apr_pct': round(p['net_apr_pct'], 2), 'gross_apr_pct': round(p['gross_apr_pct'], 2),
                'basis_drag_bps': round(p['basis_drag_bps'], 2), 'breakeven_h': breakeven_h(p['spread_8h']),
                'funding_usdt':round(p['bn'].funding + p['hl'].funding, 4), 'settlements': dict(p['settled']),
                'fees_usdt': round(p['bn'].fees + p['hl'].fees, 4),
                'net_usdt': round(p['bn'].equity() + p['hl'].equity() - 2 * n, 4)} for c, p in self.pairs.items()]
        return {'t': self.clock(), 'mode': 'SIMULATED · paper venues · no real order',
                'settlement': self.settlement,
                'next_settlement': ({k: (self.clock() // v + 1) * v for k, v in self.SETTLE_S.items()}
                                    if self.settlement == 'discrete' else None),
                'rule': {'min_spread_8h': self.min_spread, 'min_apr_pct': self.min_apr, 'min_basis_bps': self.min_basis,
                         'min_net_apr_pct': self.min_net_apr,
                         'slots': self.slots, 'validated': False, 'rotation_interval_s': self.rotation_interval_s,
                         'basis_stop_bps': self.basis_stop_bps, 'basis_freeze_bps': self.basis_freeze_bps,
                         'round_trip_bps': HEDGE_ROUND_TRIP_BPS,
                         'tie_priority': ['1000PEPE', 'WLD'],
                         'replay_reference': 'XV H28|k2 holdout +14.59 bps / 28 d = 1.90 %/yr net'},
                'rotation': {'interval_s': self.rotation_interval_s, 'last_scan': self.last_rotation_t,
                             'next_scan': None if self.last_rotation_t is None else
                             self.last_rotation_t + self.rotation_interval_s,
                             'targets': list(self.rotation_targets), 'basis_stop_bps': self.basis_stop_bps},
                'equity': round(eq, 4), 'start': round(self.start, 4), 'pnl': round(eq - self.start, 4),
                # accumulated SIMULATED funding (no real order, no real settlement): estimates at the current rate between
                # prints; the APR below is that yield on the book's start capital, None until an hour of run time (UNKNOWN)
                'funding_total_usdt': round(fund_total, 4), 'funding_open_usdt': round(fund_open, 4),
                'funding_closed_usdt': round(self.funding_closed, 4), 'run_days': round(days, 4),
                'funding_apr_pct': round(fund_total / self.start / days * 365 * 100, 2) if days >= 1 / 24 else None,
                # 11-10 (g): what the book made after every simulated fee, hedge slip and basis move, annualized on the start
                # capital. The per-coin "net APR" is today's spread held 28 days; a pair that rotates sooner pays the same
                # round trip (breakeven_h = hours of the present spread that repay it)
                'fees_total_usdt': round(fees_total, 4),
                'net_apr_realized_pct': round((eq - self.start) / self.start / days * 365 * 100, 2) if days >= 1 / 24 else None,
                'basis_freeze': dict(self.basis_freeze) if self.basis_freeze else None,
                'drawdown': round(1 - eq / self.kill.peak, 6) if self.kill.peak else None,
                'kill': None if ok else kill_why, 'positions': pos, 'top': rows[:8],
                'closed': self.closed[-5:], 'new_events': list(self.events)}
