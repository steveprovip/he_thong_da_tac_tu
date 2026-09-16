"""Carry-lab: counterfactual carry-cycle recorder — read-only, never trades.

Q5 handoff schema. One row per funding cycle per symbol:
  forecast (immutable at decision): symbol, side, rate_forecast_bps,
    next_funding_ts, horizon_s, notional_usdt, hedge_qty, decision_ts,
    available_at, snapshot_id, source
  settlement (filled later): funding_realized_usdt, basis_pnl_usdt,
    fee_usdt, borrow_usdt, net_usdt, capital_locked_usdt, status
    (FORECAST | SETTLED | INCOMPLETE | UNMATCHED)

Rules: no fake fills; forecast/simulation/settlement kept separate;
unallocatable funding income is never attributed to a strategy.
"""
import json
import math
import time


STATUSES = ('FORECAST', 'SETTLED', 'INCOMPLETE', 'UNMATCHED')


def forecast_row(symbol, side, rate_forecast_bps, next_funding_ts, horizon_s,
                 notional_usdt, hedge_qty=0.0, decision_ts=None,
                 available_at=None, snapshot_id='', source='premium_index'):
    """Build an immutable forecast row. Never raises on bad input — returns (row, reason)."""
    now = decision_ts if decision_ts is not None else time.time()
    row = {'schema': 1, 'kind': 'carry_forecast', 'symbol': symbol, 'side': side,
           'rate_forecast_bps': rate_forecast_bps, 'next_funding_ts': next_funding_ts,
           'horizon_s': horizon_s, 'notional_usdt': notional_usdt,
           'hedge_qty': hedge_qty, 'decision_ts': now,
           'available_at': available_at if available_at is not None else now,
           'snapshot_id': snapshot_id, 'source': source, 'status': 'FORECAST',
           'recorded': time.time()}
    try:
        if not row['symbol'] or row['side'] not in ('BUY', 'SELL'):
            return None, 'BAD_SIDE_OR_SYMBOL'
        for k in ('rate_forecast_bps', 'next_funding_ts', 'horizon_s',
                  'notional_usdt', 'hedge_qty', 'decision_ts', 'available_at'):
            v = float(row[k])
            if not math.isfinite(v):
                return None, 'NON_FINITE_FIELD'
        if not (row['next_funding_ts'] > row['decision_ts'] and row['horizon_s'] > 0
                and row['notional_usdt'] > 0 and row['available_at'] <= row['decision_ts']):
            return None, 'BAD_CARRY_TIMING'
    except (TypeError, ValueError):
        return None, 'NON_NUMERIC_FIELD'
    return row, 'OK'


def settle_row(forecast, funding_realized_usdt, basis_pnl_usdt, fee_usdt,
               borrow_usdt=0.0, capital_locked_usdt=0.0, matched=True):
    """Attach settlement to a forecast copy. Unmatched income -> UNMATCHED, never attributed."""
    if not isinstance(forecast, dict) or forecast.get('kind') != 'carry_forecast':
        return None, 'BAD_FORECAST'
    if not matched:
        out = dict(forecast, status='UNMATCHED')
        return out, 'UNMATCHED'
    try:
        vals = [float(x) for x in (funding_realized_usdt, basis_pnl_usdt,
                                   fee_usdt, borrow_usdt, capital_locked_usdt)]
    except (TypeError, ValueError):
        return None, 'NON_NUMERIC_SETTLEMENT'
    if not all(math.isfinite(v) for v in vals):
        return None, 'NON_FINITE_SETTLEMENT'
    if fee_usdt is None or float(fee_usdt) < 0:
        return None, 'MISSING_FEE_LEG'
    funding, basis, fee, borrow, locked = vals
    out = dict(forecast,
               funding_realized_usdt=funding, basis_pnl_usdt=basis,
               fee_usdt=fee, borrow_usdt=borrow,
               capital_locked_usdt=locked,
               net_usdt=funding + basis - fee - borrow,
               status='SETTLED', settled_at=time.time())
    return out, 'SETTLED'


def encode(row):
    """One JSONL line for the carry-lab file."""
    return json.dumps(row, sort_keys=True, allow_nan=False)
