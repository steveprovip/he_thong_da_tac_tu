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
from collections import defaultdict
from datetime import datetime, timezone
from core.models import dec


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


def wallet_totals(account):
    return {r['asset']: dec(r['free']) + dec(r['locked']) for r in account['balances']}


def wallet_reconciliation(before, after, spot_trades, base_by_symbol, tolerance='0.00000001'):
    """Conserve each asset, including BNB. External transfers stay unmatched."""
    flows = defaultdict(lambda: dec(0))
    seen = set()
    for trade in spot_trades:
        key = (trade['symbol'], trade['id'])
        if key in seen:
            continue
        seen.add(key)
        base = base_by_symbol[trade['symbol']]
        sign = 1 if trade['isBuyer'] else -1
        flows[base] += sign * dec(trade['qty'])
        flows['USDT'] -= sign * dec(trade['quoteQty'])
        flows[trade['commissionAsset']] -= dec(trade['commission'])
    assets = set(before) | set(after) | set(flows)
    residual = {asset: dec(after.get(asset, 0)) - dec(before.get(asset, 0)) - flows[asset] for asset in assets}
    mismatches = {a: str(v) for a, v in residual.items() if abs(v) > dec(tolerance)}
    return {'status': 'UNMATCHED' if mismatches else 'RECONCILED', 'residuals': mismatches}


def pair_cashflow(pair_id, spot_trades, futures_trades, incomes, *, base_asset,
                  spot_bid, perp_ask, fee_prices, expected_settlements=(), exit_fee_bps=14.0,
                  complete=True):
    """Attributed net liquidation value and cash PnL, no assumed fills.

    fee_prices[(asset, minute)] must be a historical REST price for third-asset
    commissions. Missing pricing, fills or settlement pages produce INCOMPLETE.
    Basis sign is implicit in long spot / short futures cash flows.
    """
    spot_cash, spot_qty, future_realized, fees = (dec(0) for _ in range(4))
    funding = dec(0)
    short_qty, entry_value = dec(0), dec(0)
    issues, seen = [], set()
    trades = sorted([dict(t, venue='spot') for t in spot_trades] +
                    [dict(t, venue='futures') for t in futures_trades], key=lambda t: (int(t['time']), str(t['id'])))
    for t in trades:
        key = (t['venue'], t['symbol'], t['id'])
        if key in seen:
            continue
        seen.add(key)
        q, price, commission = dec(t['qty']), dec(t['price']), dec(t['commission'])
        if min(q, price) <= 0 or commission < 0:
            raise ValueError('Invalid trade quantity/price/commission')
        buyer = t['isBuyer'] if t['venue'] == 'spot' else t['side'] == 'BUY'
        if t['venue'] == 'spot':
            spot_cash += (-1 if buyer else 1) * dec(t['quoteQty'])
            spot_qty += (1 if buyer else -1) * q
        elif not buyer:
            short_qty += q
            entry_value += q * price
        else:
            if q > short_qty:
                issues.append('FUTURES_CLOSE_EXCEEDS_OWNED_SHORT')
            if short_qty > 0:
                entry_value -= entry_value / short_qty * q
            short_qty -= q
            future_realized += dec(t['realizedPnl'])
        asset = t['commissionAsset']
        if asset == 'USDT':
            conversion = dec(1)
        elif asset == base_asset:
            conversion = price
        else:
            conversion = fee_prices.get((asset, int(t['time']) // 60000))
        if conversion is None:
            issues.append('MISSING_COMMISSION_FX:' + asset)
        else:
            fees += commission * dec(conversion)
        if t['venue'] == 'spot' and asset == base_asset:
            spot_qty -= commission
            # Fee-valued units are removed from inventory; add cost basis back
            # before subtracting the explicit fee once, avoiding double charge.
            spot_cash += commission * price
    seen_income = set()
    actual_times = []
    for income in incomes:
        key = (income['incomeType'], str(income['tranId']))
        if key in seen_income:
            continue
        seen_income.add(key)
        if income['incomeType'] != 'FUNDING_FEE' or income['asset'] != 'USDT':
            issues.append('UNSUPPORTED_INCOME')
            continue
        stamp = int(income['time'])
        exposure = sum((dec(t['qty']) * (1 if t['side'] == 'SELL' else -1)
                        for t in futures_trades if int(t['time']) < stamp), dec(0))
        if exposure <= 0:
            issues.append('UNATTRIBUTED_FUNDING')
            continue
        funding += dec(income['income'])
        actual_times.append(stamp)
    for settlement in expected_settlements:
        stamp = int(settlement['fundingTime'])
        exposure = sum((dec(t['qty']) * (1 if t['side'] == 'SELL' else -1)
                        for t in futures_trades if int(t['time']) < stamp), dec(0))
        if exposure > 0 and dec(settlement['fundingRate']) != 0 and not any(abs(t - stamp) <= 10000 for t in actual_times):
            issues.append('MISSING_FUNDING_SETTLEMENT')
    if not complete:
        issues.append('TRUNCATED_HISTORY')
    if short_qty < 0 or spot_qty < 0 or spot_qty != short_qty:
        issues.append('DELTA_MISMATCH')
    spot_value = spot_qty * dec(spot_bid)
    unrealized_future = entry_value - short_qty * dec(perp_ask)
    liquidation_cost = max(dec(0), short_qty) * dec(perp_ask) * dec(exit_fee_bps) / 10000
    basis_pnl = spot_cash + spot_value + future_realized + unrealized_future
    net = funding + basis_pnl - fees - liquidation_cost
    return {'pair_id': pair_id, 'status': 'INCOMPLETE' if issues else 'RECONCILED',
            'issues': sorted(set(issues)), 'funding_usdt': str(funding), 'basis_pnl_usdt': str(basis_pnl),
            'fee_usdt': str(fees), 'exit_cost_reserve_usdt': str(liquidation_cost), 'net_usdt': str(net),
            'spot_qty': str(spot_qty), 'short_qty': str(short_qty),
            'closed': spot_qty == 0 and short_qty == 0, 'trade_count': len(seen)}


def weekly_report(rows):
    """Realized whole-cycle PnL grouped by closing week; open MTM is separate."""
    weeks = defaultdict(lambda: {'cycles': 0, 'funding': dec(0), 'basis': dec(0), 'fees': dec(0), 'net': dec(0)})
    lines = ['# Carry weekly reconciliation', '',
             'Realized complete cycles, grouped by UTC closing week. Open positions are liquidation MTM.', '',
             '| Week UTC | Cycles | Funding USDT | Basis PnL USDT | Fees USDT | Net USDT |',
             '|---|---:|---:|---:|---:|---:|']
    open_rows, incomplete = [], []
    for row in rows:
        if row['status'] != 'RECONCILED':
            incomplete.append(row['pair_id'])
        elif not row['closed']:
            open_rows.append(row)
        else:
            week = datetime.fromtimestamp(row['closed_at'], timezone.utc).strftime('%G-W%V')
            w = weeks[week]
            w['cycles'] += 1
            for field, key in [('funding', 'funding_usdt'), ('basis', 'basis_pnl_usdt'),
                               ('fees', 'fee_usdt'), ('net', 'net_usdt')]:
                w[field] += dec(row[key])
    for week, w in sorted(weeks.items()):
        lines.append(f"| {week} | {w['cycles']} | {w['funding']} | {w['basis']} | {w['fees']} | {w['net']} |")
    if not weeks:
        lines.append('| No reconciled closed cycles | 0 | — | — | — | — |')
    lines.extend(['', '## Open liquidation MTM', ''])
    lines.extend(f"- {r['pair_id']}: {r['net_usdt']} USDT after estimated exit costs" for r in open_rows)
    lines.extend(['', 'Incomplete/unmatched cycles: ' + (', '.join(incomplete) or 'none'), ''])
    return '\n'.join(lines)


class PairReconciler:
    """Exchange order IDs anchor all attribution; REST only, bounded pagination."""
    def __init__(self, futures, spot, journal):
        self.futures, self.spot, self.journal = futures, spot, journal
        self.fx_cache = {}

    async def fee_price(self, asset, stamp):
        if asset == 'USDT':
            return dec(1)
        minute = int(stamp) // 60000
        key = (asset, minute)
        if key not in self.fx_cache:
            rows = await self.spot.request('GET', '/api/v3/klines',
                {'symbol': asset + 'USDT', 'interval': '1m', 'startTime': minute * 60000, 'limit': 1}, weight=2)
            if not rows or int(rows[0][0]) != minute * 60000:
                raise RuntimeError('Missing commission valuation candle')
            self.fx_cache[key] = dec(rows[0][1])
        return self.fx_cache[key]

    async def income_window(self, symbol, start_ms, end_ms):
        rows = []
        for start in range(start_ms, end_ms + 1, 7 * 86400000):
            end = min(end_ms, start + 7 * 86400000 - 1)
            for page in range(1, 21):
                result = await self.futures.request('GET', '/fapi/v1/income',
                    {'symbol': symbol, 'incomeType': 'FUNDING_FEE', 'startTime': start,
                     'endTime': end, 'limit': 1000, 'page': page}, signed=True, weight=30)
                rows.extend(result)
                if len(result) < 1000:
                    break
            else:
                return rows, False
        return rows, True

    async def pair_report(self, pair, spot_bid, perp_ask, end_ms=None):
        end_ms = int(time.time() * 1000) if end_ms is None else end_ms
        events = [(kind, json.loads(payload)) for kind, payload in self.journal.db.execute(
            'SELECT kind,payload FROM events WHERE kind IN (?,?,?) ORDER BY id',
            ('PAIR_ORDER_INTENT', 'PAIR_ORDER_REJECTED', 'PAIR_STATE'))]
        rejected = {data['client_id'] for kind, data in events if kind == 'PAIR_ORDER_REJECTED'}
        operations = [data for kind, data in events if kind == 'PAIR_ORDER_INTENT'
                      and data['pair_id'] == pair.pair_id and data['client_id'] not in rejected]
        spot_trades, futures_trades = [], []
        complete = True
        start_ms = int(pair.opened * 1000)
        future_history, full = await self.futures.history_since(pair.intent.perp_symbol, start_ms)
        future_history = [t for t in future_history if int(t['time']) <= end_ms]
        complete &= full
        for op in operations:
            client = self.spot if op['role'].startswith('SPOT') else self.futures
            row = await client.order(op['symbol'], op['client_id'])
            if dec(row.get('executedQty', 0)) == 0:
                continue
            if op['role'].startswith('SPOT'):
                fills = await self.spot.my_trades(op['symbol'], row['orderId'])
                fills = [t for t in fills if int(t['time']) <= end_ms]
                complete &= len(fills) < 1000
                spot_trades.extend(dict(t, symbol=op['symbol']) for t in fills)
            else:
                fills = [t for t in future_history if str(t['orderId']) == str(row['orderId'])]
                futures_trades.extend(dict(t, symbol=op['symbol']) for t in fills)
            complete &= sum((dec(t['qty']) for t in fills), dec(0)) == dec(row['executedQty'])
        # Exchange-side stop execution belongs to this pair too. Algo IDs are
        # retained in state history even after successful cancellation/closure.
        stop_ids = {data['stop_id'] for kind, data in events if kind == 'PAIR_STATE'
                    and data['pair_id'] == pair.pair_id and data.get('stop_id')}
        known_ids = {str(t['id']) for t in futures_trades}
        for stop_id in stop_ids:
            algo = await self.futures.algo(stop_id)
            actual_id = algo.get('actualOrderId')
            if actual_id and str(actual_id) != '0':
                for t in future_history:
                    if str(t['orderId']) == str(actual_id) and str(t['id']) not in known_ids:
                        futures_trades.append(dict(t, symbol=pair.intent.perp_symbol))
                        known_ids.add(str(t['id']))
        # Unknown manual/ADL executions must be visible, never silently ignored.
        if any(str(t['id']) not in known_ids for t in future_history):
            complete = False
        incomes, full = await self.income_window(pair.intent.perp_symbol, start_ms, end_ms)
        complete &= full
        settled = await self.futures.request('GET', '/fapi/v1/fundingRate',
            {'symbol': pair.intent.perp_symbol, 'startTime': start_ms, 'endTime': end_ms, 'limit': 1000})
        complete &= len(settled) < 1000
        fx = {}
        for trade in spot_trades + futures_trades:
            asset = trade['commissionAsset']
            if asset not in ('USDT', pair.intent.base_asset):
                fx[(asset, int(trade['time']) // 60000)] = await self.fee_price(asset, trade['time'])
        report = pair_cashflow(pair.pair_id, spot_trades, futures_trades, incomes,
            base_asset=pair.intent.base_asset, spot_bid=spot_bid, perp_ask=perp_ask,
            fee_prices=fx, expected_settlements=[s for s in settled if int(s['fundingTime']) < end_ms - 60000],
            exit_fee_bps=float(pair.intent.spot_fee_rate) * 10000 + float(pair.intent.futures_taker_fee_bps), complete=complete)
        report['closed_at'] = pair.closed_at or end_ms / 1000
        report['reported_at'] = time.time()
        report['fee_fx_method'] = 'USDT exact; third-asset commissions valued at REST 1m open'
        if (dec(report['spot_qty']) != dec(pair.spot_owned) or dec(report['short_qty']) != pair.short_quantity):
            report['status'] = 'INCOMPLETE'
            report['issues'].append('VENUE_TRADES_DISAGREE_WITH_PAIR_STATE')
        return report, spot_trades
