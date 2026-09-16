"""Funding-carry engine: harvest extreme funding with deadline-bound protection.

Research basis: He, Manela, Ross & von Wächter, "Fundamentals of Perpetual
Futures" (arXiv:2212.06888). Perpetual prices deviate from no-arbitrage
value; the funding payment compensates the side that is short the gap,
deviations are large and co-move across currencies, and the implied carry
strategy earns high Sharpe ratios out of sample.

HONEST STATUS (Q5 handoff 09/09): single-leg FUNDED DIRECTIONAL only — a
futures-only account has no spot leg, so textbook delta-neutral carry is
impossible here. Do NOT call this hedged. A carry position is an explicitly
funded directional position whose expected edge IS the funding payment:

  edge_bps(8h) = |funding_rate_bps| - (roundtrip_fee_bps + half_spread_exit)
  trade only if edge_bps >= edge_multiple * cost_bps   (same hurdle as ZEPHR)

The position is wrapped in the identical safety envelope as every swarm
intent: GTX passive entry, on-exchange STOP_MARKET protection, a deadline
exit shortly after the funding print (so the payment is actually collected),
PositionSizer sizing (bootstrap cap + fractional Kelly), DrawdownBrake and
the VESKA one-time HMAC token. Carry authorization is fee-hurdle based, not
vote based: the swarm has no opinion on funding, so a synthetic full-score
decision is issued locally and the ordinary token/execution gates still apply.
"""
from dataclasses import dataclass
import math


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