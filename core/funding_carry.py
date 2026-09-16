"""REST-only spot/perp planning plus the legacy read-only research interface.

FundingCarry/CarryConfig are retained for existing research tests. Production
carry uses SpotPerpCarryPlan and carry_votes below; it never authorizes the
legacy single-leg plan. Basis is spot minus perp, in USDT per coin.
"""
from dataclasses import dataclass
import math
from core.models import dec
from core.consensus_engine import ConsensusEngine, Vote


INTERVALS_PER_YEAR = 3 * 365  # funding prints every 8 hours


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
