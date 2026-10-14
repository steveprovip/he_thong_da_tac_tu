"""REST-only spot/perp planning plus the legacy read-only research interface.

FundingCarry/CarryConfig are retained for existing research tests. Production
carry uses SpotPerpCarryPlan and carry_votes below; it never authorizes the
legacy single-leg plan. Basis is spot minus perp, in USDT per coin.
"""
from dataclasses import dataclass
import math
from typing import Iterable, List, Optional
from core.models import dec
from core.consensus_engine import ConsensusEngine, Vote


INTERVALS_PER_YEAR = 3 * 365  # funding prints every 8 hours

# ------------------------------------------------- HARD CAPITAL GATE (two legs)
# A spot/perp carry is TWO orders on TWO venues. Both have to clear their own
# minimum notional, both pay their own fee, and the hedge only protects you if
# both legs actually exist. On Binance that floor is 5 USDT per leg, so the
# smallest honest two-leg position is 10.0 USDT of notional.
#
# The book this system runs is E = 3.85 USDT. 3.85 < 10.0, and no amount of
# leverage changes that: leverage moves the MARGIN you post, not the notional
# the venue matches, and a levered leg is a larger loss per adverse tick, not a
# smaller one. So this book is structurally research-only on carry, exactly as
# core/capital_gate.py concludes for the single-leg case. This gate is the
# reason the runtime cannot be talked into a live pair; do not remove it.
BOOK_EQUITY_USDT = 3.85
TWO_LEG_MIN_NOTIONAL_USDT = 10.0

SHADOW = 'SHADOW'
RESEARCH_ONLY = 'RESEARCH_ONLY'
LIVE = 'LIVE'


@dataclass(frozen=True)
class CapitalGate:
    mode: str
    allowed: bool
    equity_usdt: float
    notional_usdt: float
    minimum_usdt: float
    reason: str

    @property
    def research(self):
        return self.mode in (SHADOW, RESEARCH_ONLY)


def two_leg_capital_gate(equity_usdt, notional_usdt=None, requested_mode='shadow',
                         minimum=TWO_LEG_MIN_NOTIONAL_USDT):
    """Decide SHADOW / RESEARCH_ONLY / LIVE for the next two-leg carry.

    `equity_usdt` is the whole book measured across BOTH venues (spot free cash
    plus futures wallet), never one side of it. `notional_usdt` is the size the
    runtime intends to put on per pair; passing None asks only whether the book
    could fund any compliant pair at all.
    """
    requested = str(requested_mode).lower()
    checked = [equity_usdt, minimum] + ([] if notional_usdt is None else [notional_usdt])
    if not all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
               for v in checked):
        return CapitalGate(RESEARCH_ONLY, False, float('nan'),
                           float('nan'), minimum, 'NON_FINITE_CAPITAL')
    equity = float(equity_usdt)
    notional = float('nan') if notional_usdt is None else float(notional_usdt)
    if minimum <= 0:
        raise ValueError('two-leg minimum must be positive')
    if equity < 0 or (notional_usdt is not None and notional <= 0):
        return CapitalGate(RESEARCH_ONLY, False, equity, notional, minimum, 'INVALID_CAPITAL')
    if equity < minimum:
        return CapitalGate(RESEARCH_ONLY, False, equity, notional, minimum,
                           f'EQUITY {equity:.4f} < TWO_LEG_MIN {minimum:.2f} USDT')
    if notional_usdt is not None and notional < minimum:
        return CapitalGate(RESEARCH_ONLY, False, equity, notional, minimum,
                           f'NOTIONAL {notional:.4f} < TWO_LEG_MIN {minimum:.2f} USDT')
    if notional_usdt is not None and notional > equity:
        return CapitalGate(RESEARCH_ONLY, False, equity, notional, minimum,
                           f'NOTIONAL {notional:.4f} > EQUITY {equity:.4f}')
    if requested == 'shadow':
        return CapitalGate(SHADOW, False, equity, notional, minimum, 'SHADOW_MODE')
    return CapitalGate(LIVE, True, equity, notional, minimum, 'TWO_LEG_FUNDED')



@dataclass(frozen=True)
class CarryConfig:
    # MEASURED baseline 6.0 bps (2 maker entry + 4 taker exit, reconciled vs
    # /fapi/v1/userTrades, see core/signal_lab.py). Hedge legs need their own
    # full fee added — never assume total carry cost = 6.
    roundtrip_fee_bps: float = 6.0    # was 7.0 (2+5); corrected Q5 handoff
    edge_multiple: float = 1.8        # same fee hurdle the swarm gate uses
    min_rate_bps: float = 5.0         # ignore noise below this per interval
    max_spread_bps: float = 15.0      # entry book must be tight enough to exit
    stop_atr_multiple: float = 2.0
    min_stop_ticks: int = 8
    tp_multiple: float = 4.0          # nominal TP; the deadline does the real exit
    settle_buffer: float = 120.0      # hold this long past the funding print
    min_horizon: float = 300.0
    max_horizon: float = 3 * 3600.0
    scan_interval: float = 60.0


@dataclass(frozen=True)
class FundingRow:
    symbol: str
    rate: float          # last funding rate, per 8h interval (e.g. 0.0001 = 1 bps)
    mark: float
    index: float
    next_funding: float  # unix seconds of the next funding print


@dataclass(frozen=True)
class CarryPlan:
    symbol: str
    side: str            # SELL collects positive funding; BUY collects negative
    rate_bps: float
    cost_bps: float
    edge_bps: float
    stop: float
    take_profit: float
    horizon: float
    reason: str


def parse_premium(rows, now):
    """Parse /fapi/v1/premiumIndex rows into FundingRow, dropping junk."""
    parsed = {}
    if not isinstance(rows, list):
        return parsed
    for row in rows:
        if not isinstance(row, dict):
            continue
        symbol = row.get('symbol')
        try:
            rate = float(row.get('lastFundingRate', 'nan'))
            mark = float(row.get('markPrice', 'nan'))
            index = float(row.get('indexPrice', 'nan'))
            next_funding = float(row.get('nextFundingTime', 0))/1000
        except (TypeError, ValueError):
            continue
        if not symbol or not all(math.isfinite(v) for v in (rate, mark, index, next_funding)):
            continue
        if mark <= 0 or index <= 0 or abs(rate) > .05 or next_funding <= 0:
            continue
        parsed[symbol] = FundingRow(symbol, rate, mark, index, next_funding)
    return parsed


class FundingCarry:
    def __init__(self, config=None):
        self.config = config or CarryConfig()
        self.rows = {}

    def update(self, rows, now):
        self.rows = parse_premium(rows, now)

    def snapshot(self, limit=5):
        """Telemetry view: strongest funding extremes first, annualized."""
        ranked = sorted(self.rows.values(),
                        key=lambda r: -abs(r.rate*INTERVALS_PER_YEAR*1e4))
        return [{'symbol': r.symbol, 'rate_bps': r.rate*1e4,
                 'annualized_bps': r.rate*INTERVALS_PER_YEAR*1e4,
                 'side': 'SELL' if r.rate > 0 else 'BUY',
                 'next_funding': r.next_funding} for r in ranked[:limit]]

    def plan(self, symbol, price, spread_bps, atr, tick, now):
        """Fee-hurdle carry plan for one symbol, or (None, reason)."""
        cfg = self.config
        row = self.rows.get(symbol)
        if row is None:
            return None, 'NO_FUNDING_DATA'
        if row.next_funding <= now:
            return None, 'FUNDING_STALE'
        rate_bps = row.rate*1e4
        if abs(rate_bps) < cfg.min_rate_bps:
            return None, 'CARRY_TOO_SMALL'
        if not (math.isfinite(spread_bps) and 0 <= spread_bps <= cfg.max_spread_bps):
            return None, 'SPREAD_TOO_WIDE'
        cost_bps = cfg.roundtrip_fee_bps + spread_bps/2
        edge_bps = abs(rate_bps)-cost_bps
        if edge_bps < cfg.edge_multiple*cost_bps:
            return None, 'EDGE_BELOW_HURDLE'
        side = 'SELL' if row.rate > 0 else 'BUY'
        if not (math.isfinite(price) and price > 0 and math.isfinite(atr) and atr >= 0
                and math.isfinite(tick) and tick > 0):
            return None, 'INVALID_MARKET_DATA'
        distance = max(cfg.stop_atr_multiple*atr, cfg.min_stop_ticks*tick)
        if not math.isfinite(distance) or distance <= 0:
            return None, 'INVALID_MARKET_DATA'
        horizon = min(cfg.max_horizon, max(cfg.min_horizon,
                                           row.next_funding-now+cfg.settle_buffer))
        if side == 'SELL':
            stop, take_profit = price+distance, price-cfg.tp_multiple*distance
        else:
            stop, take_profit = price-distance, price+cfg.tp_multiple*distance
        reason = f'FUNDING CARRY {rate_bps:+.1f}bps/8h'
        return CarryPlan(symbol, side, rate_bps, cost_bps, edge_bps,
                         stop, take_profit, horizon, reason), 'AUTHORIZED'


@dataclass(frozen=True)
class CarrySnapshot:
    symbol: str
    spot_bid: float
    spot_ask: float
    perp_bid: float
    perp_ask: float
    rate: float
    interval_hours: int
    next_funding: float
    oi_usdt: float
    observed_at: float
    funding_history: tuple
    spot_fee_bps: float = 10.0
    margin_ratio: float = 0.0
    bnb_ok: bool = False
    capacity_ok: bool = False
    accounts_verified: bool = False
    futures_fee_bps: float = 6.0
    futures_taker_fee_bps: float = 4.0

    @property
    def basis_fraction(self):
        spot = (self.spot_bid + self.spot_ask) / 2
        perp = (self.perp_bid + self.perp_ask) / 2
        return (spot - perp) / spot


@dataclass(frozen=True)
class SpotPerpCarryPlan:
    symbol: str
    apr: float
    horizon_days: float
    gross_bps: float
    cost_bps: float
    net_bps: float
    basis_fraction: float
    projected_rate: float


def plan_spot_perp(snapshot, now, horizon_days=7.0, slippage_bps=2.0):
    s = snapshot
    values = (s.spot_bid, s.spot_ask, s.perp_bid, s.perp_ask, s.rate, s.next_funding,
              s.oi_usdt, s.observed_at, s.spot_fee_bps, s.futures_fee_bps, horizon_days, slippage_bps)
    if not all(math.isfinite(x) for x in values):
        return None, 'NON_FINITE_DATA'
    if (min(s.spot_bid, s.perp_bid) <= 0 or s.spot_ask < s.spot_bid or s.perp_ask < s.perp_bid
            or slippage_bps < 0 or s.spot_fee_bps < 0 or s.futures_fee_bps < 0):
        return None, 'INVALID_QUOTES_OR_COST'
    if not 0 <= now - s.observed_at < 60 or s.next_funding <= now:
        return None, 'STALE_DATA'
    if s.interval_hours not in (1, 4, 8):
        return None, 'UNKNOWN_FUNDING_INTERVAL'
    if not 7 <= horizon_days <= 30:
        return None, 'HORIZON_OUTSIDE_7_30_DAYS'
    if abs(s.basis_fraction) > .015:
        return None, 'RUNE_BASIS'
    spread = (s.spot_ask - s.spot_bid) / ((s.spot_bid + s.spot_ask) / 2) * 10000
    if spread >= 3:
        return None, 'SPOT_SPREAD'
    if s.oi_usdt <= 20_000_000:
        return None, 'INSUFFICIENT_OI'
    if len(s.funding_history) < 21 or not all(math.isfinite(r) for r in s.funding_history):
        return None, 'INSUFFICIENT_FUNDING_HISTORY'
    rate = min(s.rate, sum(s.funding_history) / len(s.funding_history))
    apr = rate * 24 / s.interval_hours * 365
    if apr <= .15:
        return None, 'APR_BELOW_15_PERCENT'
    settlements = max(0, 1 + int((now + horizon_days * 86400 - s.next_funding) // (s.interval_hours * 3600)))
    gross = rate * settlements * 10000
    perp_spread = (s.perp_ask - s.perp_bid) / ((s.perp_bid + s.perp_ask) / 2) * 10000
    cost = max(6, s.futures_fee_bps) + 2 * s.spot_fee_bps + spread + perp_spread / 2 + slippage_bps
    net = gross - cost
    if net <= 2 * cost:
        return None, 'ZEPHR_NET_HURDLE'
    return SpotPerpCarryPlan(s.symbol, apr, horizon_days, gross, cost, net, s.basis_fraction, rate), 'ELIGIBLE'


def carry_votes(snapshot, plan, *, gateway_ready, exits_ready, now):
    """Eight auditable suitability checks; no invented independent alpha scores."""
    s = snapshot
    basis_ok = min(s.spot_bid, s.perp_bid) > 0 and abs(s.basis_fraction) <= .015
    history_ok = (len(s.funding_history) >= 21 and
                  all(math.isfinite(r) for r in s.funding_history) and
                  sum(r > 0 for r in s.funding_history) / len(s.funding_history) >= .8)
    rune_ok = (basis_ok and s.accounts_verified and s.bnb_ok and
               math.isfinite(s.margin_ratio) and 0 <= s.margin_ratio <= .65)
    checks = [
        ('NORO', basis_ok and plan is not None, 'Hedged basis and projected carry'),
        ('LUMEN', history_ok and 0 <= now - s.observed_at < 60, 'Settled funding persistence and freshness'),
        ('TIDAL', math.isfinite(s.oi_usdt) and s.oi_usdt > 20_000_000, 'Open-interest capacity'),
        ('ZEPHR', plan is not None and plan.net_bps > 2 * plan.cost_bps, 'Both-leg net/cost > 2'),
        ('RUNE', rune_ok, 'Basis <=1.5%, BNB reserve, verified isolated margin <=65%'),
        ('OKAPI', s.accounts_verified and s.capacity_ok, 'Spot cash plus isolated futures margin'),
        ('MARIN', exits_ready, 'Both-leg exit and exchange protection ready'),
        ('VESKA', gateway_ready, 'Both clients and private streams ready'),
    ]
    votes = [Vote(name, 'SELL' if ok else 'HOLD', float(bool(ok)), reason,
                  veto=not ok and name in ('ZEPHR', 'RUNE', 'OKAPI', 'MARIN', 'VESKA'))
             for name, ok, reason in checks]
    policy = ConsensusEngine(threshold=.768, veto_threshold=.70, trend_floor=.768)
    return policy.evaluate(votes, regime='NORMAL'), votes


def isolated_margin_ratio(position):
    """Maintenance / (isolated wallet + unrealized PnL), never account-wide equity."""
    try:
        wallet = dec(position['isolatedWallet'])
        pnl = dec(position.get('unRealizedProfit', position.get('unrealizedProfit')))
        maintenance = dec(position['maintMargin'])
        if wallet + pnl <= 0 or maintenance < 0:
            return math.inf
        return float(maintenance / (wallet + pnl))
    except (KeyError, TypeError, ValueError):
        return math.inf


@dataclass(frozen=True)
class DynamicXVQuote:
    symbol: str
    binance_rate_8h_bps: float       # Đã quy đổi về chuẩn bps/8h
    hyperliquid_rate_1h_bps: float   # Rate 1h của Hyperliquid tính bằng bps
    ema_spread_fast_bps: float       # EMA 6h của |8*HL_1h - BN_8h|
    ema_spread_slow_bps: float       # EMA 72h của |8*HL_1h - BN_8h|
    persistence_rho: float           # Hệ số tự tương quan AR(1) thuộc [0.5, 0.95]
    basis_entry_bps: float           # (P_short_bid - P_long_ask) / P_mid * 1e4
    min_depth_usd: float             # Thanh khoản L1-L3 khả dụng trên chân mỏng hơn
    round_trip_fee_bps: float = 10.0


@dataclass(frozen=True)
class DynamicXVPlan:
    symbol: str
    direction: str                   # "LONG_BN_SHORT_HL" | "SHORT_BN_LONG_HL"
    instant_spread_8h_bps: float
    expected_cum_yield_bps: float
    effective_cost_bps: float
    breakeven_cycles_8h: float
    optimal_hold_cycles_8h: int
    projected_net_apr_pct: float
    score: float


def evaluate_dynamic_xv_candidate(
    q: DynamicXVQuote,
    eval_horizon_cycles: int = 12,
    capital_utilization: float = 0.70,
    min_liquidity_usd: float = 25_000.0,
) -> Optional[DynamicXVPlan]:
    """
    Đánh giá ứng viên Cross-Venue Funding (Binance <-> Hyperliquid)
    tích hợp dự báo phân rã AR(1) và khấu hao chi phí theo Basis Entry.
    """
    if q.min_depth_usd < min_liquidity_usd:
        return None

    hl_8h_equiv_bps = 8.0 * float(q.hyperliquid_rate_1h_bps)
    bn_8h_bps = float(q.binance_rate_8h_bps)
    raw_diff = hl_8h_equiv_bps - bn_8h_bps
    instant_spread_bps = abs(raw_diff)

    if instant_spread_bps < 1.2:
        return None

    direction = "LONG_BN_SHORT_HL" if raw_diff > 0 else "SHORT_BN_LONG_HL"
    rho = max(0.50, min(0.95, float(q.persistence_rho)))
    h = max(int(eval_horizon_cycles), 3)

    # Tổng lợi tức kỳ vọng trong H chu kỳ 8h theo quá trình hồi quy trung bình AR(1)
    fast_s = max(float(q.ema_spread_fast_bps), instant_spread_bps * 0.8)
    slow_s = max(float(q.ema_spread_slow_bps), 0.8)
    decay_sum = rho * (1.0 - (rho ** h)) / max(1.0 - rho, 1e-6)
    expected_cum_yield_bps = h * slow_s + (fast_s - slow_s) * decay_sum

    # Khấu trừ lợi thế Basis khi vào lệnh (giới hạn tối đa giảm 60% phí để thận trọng)
    basis_credit_bps = max(-4.0, min(float(q.basis_entry_bps), 0.60 * float(q.round_trip_fee_bps)))
    effective_cost_bps = max(2.5, float(q.round_trip_fee_bps) - basis_credit_bps)

    net_cum_yield_bps = expected_cum_yield_bps - effective_cost_bps
    if net_cum_yield_bps <= 0.0:
        return None

    avg_spread_per_cycle = expected_cum_yield_bps / h
    breakeven_cycles = effective_cost_bps / max(avg_spread_per_cycle, 1e-6)
    if breakeven_cycles > 6.0:  # Không vào nếu mất quá 48 giờ chỉ để hòa vốn phí
        return None

    amortized_net_per_8h_bps = net_cum_yield_bps / h
    projected_net_apr_pct = (amortized_net_per_8h_bps * 1e-4) * (365.0 * 3.0) * float(capital_utilization) * 100.0

    # Điểm xếp hạng ưu tiên lợi suất ròng cao và thời gian hòa vốn ngắn
    score = amortized_net_per_8h_bps / math.sqrt(max(breakeven_cycles, 0.5))

    return DynamicXVPlan(
        symbol=q.symbol,
        direction=direction,
        instant_spread_8h_bps=instant_spread_bps,
        expected_cum_yield_bps=expected_cum_yield_bps,
        effective_cost_bps=effective_cost_bps,
        breakeven_cycles_8h=breakeven_cycles,
        optimal_hold_cycles_8h=h,
        projected_net_apr_pct=projected_net_apr_pct,
        score=score,
    )


def rank_dynamic_xv_opportunities(
    quotes: Iterable[DynamicXVQuote],
    eval_horizon_cycles: int = 12,
    capital_utilization: float = 0.70,
) -> List[DynamicXVPlan]:
    plans = [
        p for q in quotes
        if (p := evaluate_dynamic_xv_candidate(q, eval_horizon_cycles, capital_utilization)) is not None
    ]
    plans.sort(key=lambda x: x.score, reverse=True)
    return plans


def should_rotate_xv_position(
    current_plan: DynamicXVPlan,
    candidate_plan: DynamicXVPlan,
    exit_cost_current_bps: float = 5.0,
    hysteresis_buffer_bps: float = 4.0,
) -> bool:
    """
    Luật Dừng Tối ưu có Trễ (Hysteresis Stopping Rule):
    Chỉ đảo từ vị thế đang giữ sang vị thế mới nếu phần chênh lệch tích lũy
    lớn hơn toàn bộ phí thoát lệnh cũ + phí vào lệnh mới + đệm an toàn 4 bps.
    """
    if current_plan.symbol == candidate_plan.symbol and current_plan.direction == candidate_plan.direction:
        return False

    # Với vị thế đang giữ, phí vào lệnh đã là sunk cost, chỉ còn phí thoát
    remaining_value_current = current_plan.expected_cum_yield_bps - float(exit_cost_current_bps)
    net_value_candidate = candidate_plan.expected_cum_yield_bps - candidate_plan.effective_cost_bps

    return (net_value_candidate - remaining_value_current) > float(hysteresis_buffer_bps)

