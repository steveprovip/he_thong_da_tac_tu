"""Read-only exchange reconciliation against this project's journaled entry IDs."""
import asyncio
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import sqlite3
import sys
import time
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import ROOT, Settings
from live.binance_futures_client import BinanceFuturesClient
from core.outcome_ledger import OutcomeLedger

ENTRY_PREFIX = 'gt-'          # execution_gateway.py:59; gs-/gx-/gf- are stop/exit/flatten
ALL_ORDERS_PAGE = 1000        # /fapi/v1/allOrders hard page cap


def fold_round_trips(symbol, trades, matched, errors):
    """Group one symbol's venue fills into closed round trips. Pure; no network.

    A round trip can span SEVERAL of our own entry orders. manage_positions
    cancels a maker entry at quote_ttl and whatever already filled stays as
    inventory, so five 0.47 intents fired inside 14 seconds can leave a single
    0.52 position (journal, SOLUSDT 18:25:23-37). Treating the second order's
    fill as foreign flow and aborting the symbol dropped 5 of 23 closed round
    trips -- and it dropped exactly the messy partial-fill ones, so what
    survived was a biased sample rather than a smaller random one.

    Foreign same-side flow still truncates the symbol: once inventory we did
    not open is in the position, per-trade attribution is no longer sound.
    """
    results, current, seen = [], None, set()
    for trade in sorted(trades, key=lambda t: (t['time'], t['id'])):
        if trade['id'] in seen:
            continue
        seen.add(trade['id'])
        entry_id = matched.get(trade['orderId'])
        qty = float(trade['qty']) * (1 if trade['side'] == 'BUY' else -1)
        if current is None:
            if not entry_id:
                continue
            current = {'symbol': symbol, 'trade_id': entry_id, 'entry_order_id': trade['orderId'],
                       'entry_order_ids': [trade['orderId']], 'entry_notional_usdt': 0.,
                       'gross_pnl_usdt': 0., 'fees_usdt': 0., 'position': 0.,
                       'opened_at': trade['time']/1000, 'fills': 0, 'entry_maker': True}
        if trade.get('commissionAsset') != 'USDT':
            errors.append({'symbol': symbol, 'error': 'Non-USDT fee needs FX conversion'})
            return results
        own_entry = trade['orderId'] == current['entry_order_id'] or (
            entry_id and current['position'] * qty > 0)
        if own_entry:
            current['entry_notional_usdt'] += abs(qty)*float(trade['price'])
            current['entry_maker'] &= bool(trade['maker'])
            if trade['orderId'] not in current['entry_order_ids']:
                current['entry_order_ids'].append(trade['orderId'])
        elif current['position'] * qty > 0:
            errors.append({'symbol': symbol, 'error': 'Foreign same-side flow; symbol truncated here'})
            return results
        current['position'] += qty
        current['gross_pnl_usdt'] += float(trade['realizedPnl'])
        current['fees_usdt'] += float(trade['commission'])
        current['fills'] += 1
        if abs(current['position']) < 1e-9:
            current['closed_at'] = trade['time']/1000
            results.append(current)
            current = None
    if current:
        errors.append({'symbol': symbol, 'error': 'Incomplete/open round trip; excluded'})
    return results


def gap_reasons(results, matched):
    """Why the reconciler could not settle a close, when it could not.

    'UNEXPLAINED' is never an acceptable end state for a closed round trip: it
    means the edge estimate has a hole nobody can size. These two reasons name
    the hole instead.
    """
    folded = {r['trade_id'] for r in results}
    attributed = {client_id for orders in matched.values() for client_id in orders.values()}
    reasons = {}
    for client_id in attributed - folded:
        reasons[client_id] = 'FOLD_TRUNCATED: foreign flow truncated the symbol before this round trip'
    return reasons, attributed


def reconciliation_gaps(events, ledger_ids, reasons=None):
    """Closed round trips with neither a ledger row nor a journalled reason.

    The invariant this enforces: every POSITION_CLOSED either produced a
    settled outcome or left a SETTLE_WITHHELD saying why it did not. Anything
    listed here is a silent hole in the edge estimate -- which is how five
    settled round trips stayed invisible while the mean read -8.16 bps.
    """
    folded_reasons, attributed = reasons if reasons else ({}, None)
    withheld = {p.get('client_id'): p.get('reason', '') for _, kind, p in events
                if kind == 'SETTLE_WITHHELD'}

    def why(client_id):
        if client_id in withheld:
            return withheld[client_id]
        if client_id in folded_reasons:
            return folded_reasons[client_id]
        if attributed is not None and client_id not in attributed:
            return 'ATTRIBUTION_IMPOSSIBLE: no venue order carries this client id'
        return 'UNEXPLAINED'

    return [{'symbol': p.get('symbol'), 'client_id': p.get('client_id'),
             'reason': why(p.get('client_id'))}
            for _, kind, p in events
            if kind == 'POSITION_CLOSED' and p.get('client_id') not in ledger_ids]


async def audit():
    path = ROOT/'data'/'testnet-journal.sqlite'
    with sqlite3.connect(path.as_uri()+'?mode=ro', uri=True) as db:
        events = [(stamp, kind, json.loads(payload)) for stamp, kind, payload in db.execute('SELECT timestamp,kind,payload FROM events ORDER BY id')]
    intents = {p['client_id']: p for _, k, p in events if k == 'ENTRY_INTENT'}
    # Kept as a diagnostic only: the gap between this and venue_entry_orders is
    # how much of the fill history the journal missed. It no longer gates anything.
    journal_filled = {p['client_id'] for _, k, p in events
                      if k == 'ORDER_STATE' and p.get('filled', 0) > 0}
    matched = defaultdict(dict)
    errors = []
    async with BinanceFuturesClient(Settings.load('testnet')) as client:
        await client.sync_time()
        # Attribution comes from the venue, never from the journal's view of what
        # filled. ORDER_STATE is written from user-stream events and reconcile, so
        # an entry that fills and is cancelled in the same breath can be journalled
        # as (0.0, NEW) and never updated -- 25 of 328 entries show a fill in the
        # journal while the venue closed more positions than that. Two such orders
        # (SOLUSDT gt-857104626f5d, DOGEUSDT gt-2945b65c106f) made their own fills
        # look like foreign flow, which truncated SOLUSDT and cost three further
        # round trips. allOrders maps every orderId we ever sent, in one call per
        # symbol, so a missing journal write can no longer hide a settled trade.
        for symbol in sorted({p['symbol'] for p in intents.values()}):
            try:
                orders = await client.request('GET', '/fapi/v1/allOrders',
                                              {'symbol': symbol, 'limit': ALL_ORDERS_PAGE},
                                              signed=True, weight=5)
            except Exception as error:
                errors.append({'symbol': symbol, 'error': f'Order lookup failed: {error}'})
                continue
            if len(orders) >= ALL_ORDERS_PAGE:
                errors.append({'symbol': symbol,
                               'error': 'allOrders page cap reached; attribution may be truncated'})
            for order in orders:
                client_id = order.get('clientOrderId', '')
                if client_id.startswith(ENTRY_PREFIX):
                    matched[symbol][order['orderId']] = client_id
        account = await client.account()
        trades_by_symbol = {}
        for symbol in matched:
            trades_by_symbol[symbol] = await client.request('GET', '/fapi/v1/userTrades',
                {'symbol': symbol, 'limit': 1000}, signed=True, weight=5)
        results = []
        for symbol, trades in trades_by_symbol.items():
            for record in fold_round_trips(symbol, trades, matched[symbol], errors):
                funding = await client.request('GET', '/fapi/v1/income', {'symbol': symbol,
                    'incomeType': 'FUNDING_FEE', 'startTime': int(record['opened_at']*1000),
                    'endTime': int(record['closed_at']*1000), 'limit': 1000}, signed=True, weight=30)
                record['funding_usdt'] = sum(float(i['income']) for i in funding if i['asset'] == 'USDT')
                record['net_pnl_usdt'] = record['gross_pnl_usdt']-record['fees_usdt']+record['funding_usdt']
                record['net_return'] = record['net_pnl_usdt']/record['entry_notional_usdt']
                results.append(record)
    results.sort(key=lambda r: r['closed_at'])
    report = {'timestamp': time.time(), 'source': 'testnet', 'wallet': float(account['totalWalletBalance']),
              'journal_entries': len(intents), 'journal_filled_entries': len(journal_filled),
              'round_trips': len(results), 'wins': sum(r['net_pnl_usdt'] > 0 for r in results),
              'gross_pnl_usdt': sum(r['gross_pnl_usdt'] for r in results),
              'fees_usdt': sum(r['fees_usdt'] for r in results),
              'net_pnl_usdt': sum(r['net_pnl_usdt'] for r in results), 'errors': errors, 'trades': results}
    ledger = OutcomeLedger(ROOT/'data'/'testnet-settled-v2.json', 'testnet')

    def mean_edge_bps(records):
        return round(1e4*math.fsum(r['net_return'] for r in records)/len(records), 3) if records else None

    # Report the estimate on both sides of the backfill. A recovered round trip
    # that moves the mean is the measure of how much the gap was flattering it.
    report['edge_bps_before'] = mean_edge_bps(ledger.records)
    # Anything this reconciler adds is by definition a row the live settler did
    # not record, so it is marked as such. Venue numbers, recovered provenance.
    backfilled = sum(ledger.append(row['net_pnl_usdt'], row['entry_notional_usdt'], row['closed_at'],
                                   row['symbol'], row['trade_id'], origin='backfill') for row in results)
    report['edge_bps_after'] = mean_edge_bps(ledger.records)
    report['settled_outcomes'] = len(ledger.records)
    report['backfilled_outcomes'] = backfilled
    report['venue_entry_orders'] = sum(len(m) for m in matched.values())
    report['ledger_gaps'] = reconciliation_gaps(events, {r.get('trade_id') for r in ledger.records},
                                                gap_reasons(results, matched))
    (ROOT/'data'/'execution-audit.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'trades'}, indent=2))


if __name__ == '__main__':
    asyncio.run(audit())
