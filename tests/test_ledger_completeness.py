"""The edge estimate is only as honest as the ledger is complete.

Five of twenty-three closed round trips never reached data/testnet-settled-v2.json,
and they were not a random five: the live settler and the offline reconciler both
mis-parse the same thing -- a position built from several partially-filled entry
orders -- so the rows that went missing were exactly the messy ones. A biased
sample reads optimistic, and at n=60 that sample is what grants Kelly sizing.

These tests pin the two halves of the invariant:
  * a round trip spanning several of our own entry orders is one round trip;
  * every POSITION_CLOSED leaves either a ledger row or a journalled reason.
"""
import json
from types import SimpleNamespace

import pytest

from core.outcome_ledger import OutcomeLedger
from tools.audit_execution import fold_round_trips, gap_reasons, reconciliation_gaps


def fill(tid, order, side, qty, price, *, pnl=0.0, fee=0.0, maker=True, ms=None, asset='USDT'):
    return {'id': tid, 'orderId': order, 'side': side, 'qty': str(qty), 'price': str(price),
            'realizedPnl': str(pnl), 'commission': str(fee), 'commissionAsset': asset,
            'maker': maker, 'time': ms if ms is not None else tid * 1000}


# ------------------------------------------------------- folding round trips
def test_one_round_trip_may_span_several_of_our_own_entry_orders():
    """The SOLUSDT 18:25 shape: five 0.47 intents in 14s, two of them filling.

    manage_positions cancels a maker entry at quote_ttl and whatever already
    filled stays as inventory, so the exit covers 0.52 while no single order
    ever asked for more than 0.47.
    """
    trades = [fill(1, 100, 'BUY', 0.47, 105.20, fee=0.00494),
              fill(2, 101, 'BUY', 0.05, 105.30, fee=0.00053),
              fill(3, 999, 'SELL', 0.52, 105.40, pnl=0.0994, fee=0.02192, maker=False)]
    errors = []
    trips = fold_round_trips('SOLUSDT', trades, {100: 'gt-a', 101: 'gt-b'}, errors)
    assert errors == [], 'our own second entry order is not foreign flow'
    assert len(trips) == 1
    trip = trips[0]
    assert trip['trade_id'] == 'gt-a', 'the round trip is named by the order that opened it'
    assert trip['entry_order_ids'] == [100, 101]
    assert trip['entry_notional_usdt'] == pytest.approx(0.47*105.20 + 0.05*105.30)
    assert trip['fills'] == 3 and trip['position'] == pytest.approx(0.0)
    assert trip['entry_maker'] is True


def test_the_old_behaviour_would_have_dropped_that_round_trip():
    """Regression guard: the second entry order must not truncate the symbol.

    Before the fix this raised 'Overlapping/manual position; omitted' and broke
    out of the symbol, discarding this round trip and every later one on it.
    """
    trades = [fill(1, 100, 'BUY', 0.47, 105.20),
              fill(2, 101, 'BUY', 0.05, 105.30),
              fill(3, 999, 'SELL', 0.52, 105.40, pnl=0.0994, maker=False),
              fill(4, 102, 'BUY', 0.30, 105.00),
              fill(5, 998, 'SELL', 0.30, 105.10, pnl=0.03, maker=False)]
    errors = []
    trips = fold_round_trips('SOLUSDT', trades, {100: 'gt-a', 101: 'gt-b', 102: 'gt-c'}, errors)
    assert [t['trade_id'] for t in trips] == ['gt-a', 'gt-c']
    assert errors == []


def test_foreign_same_side_flow_still_truncates_the_symbol():
    """Inventory we did not open breaks per-trade attribution; stay conservative.

    Round trips already closed before the foreign fill are kept -- truncating is
    not a reason to discard evidence that was already sound.
    """
    trades = [fill(1, 100, 'BUY', 0.40, 105.0),
              fill(2, 900, 'SELL', 0.40, 105.1, pnl=0.04, maker=False),
              fill(3, 101, 'BUY', 0.40, 105.0),
              fill(4, 777, 'BUY', 0.40, 105.0)]
    errors = []
    trips = fold_round_trips('SOLUSDT', trades, {100: 'gt-a', 101: 'gt-b'}, errors)
    assert [t['trade_id'] for t in trips] == ['gt-a']
    assert errors and 'Foreign same-side flow' in errors[0]['error']


def test_non_usdt_commission_is_refused_not_guessed():
    errors = []
    trades = [fill(1, 100, 'BUY', 0.40, 105.0, asset='BNB')]
    assert fold_round_trips('SOLUSDT', trades, {100: 'gt-a'}, errors) == []
    assert 'FX conversion' in errors[0]['error']


def test_an_open_position_is_excluded_and_reported():
    errors = []
    trades = [fill(1, 100, 'BUY', 0.40, 105.0)]
    assert fold_round_trips('SOLUSDT', trades, {100: 'gt-a'}, errors) == []
    assert 'Incomplete/open round trip' in errors[0]['error']


def test_fills_before_our_first_entry_are_ignored_not_adopted():
    """Do not silently take ownership of exposure the session did not open."""
    errors = []
    trades = [fill(1, 555, 'BUY', 0.40, 105.0),
              fill(2, 100, 'BUY', 0.40, 105.0),
              fill(3, 900, 'SELL', 0.40, 105.1, pnl=0.04, maker=False)]
    trips = fold_round_trips('SOLUSDT', trades, {100: 'gt-a'}, errors)
    assert [t['trade_id'] for t in trips] == ['gt-a']
    assert trips[0]['entry_notional_usdt'] == pytest.approx(0.40*105.0)


# --------------------------------------------------- POSITION_CLOSED invariant
def events(*rows):
    return [(float(i), kind, payload) for i, (kind, payload) in enumerate(rows)]


def test_a_close_with_a_ledger_row_is_not_a_gap():
    ev = events(('POSITION_CLOSED', {'symbol': 'SOLUSDT', 'client_id': 'gt-a'}))
    assert reconciliation_gaps(ev, {'gt-a'}) == []


def test_a_close_with_a_journalled_reason_is_explained_not_silent():
    ev = events(('SETTLE_WITHHELD', {'symbol': 'SOLUSDT', 'client_id': 'gt-a',
                                     'reason': 'missing entry notional'}),
                ('POSITION_CLOSED', {'symbol': 'SOLUSDT', 'client_id': 'gt-a'}))
    assert reconciliation_gaps(ev, set()) == [
        {'symbol': 'SOLUSDT', 'client_id': 'gt-a', 'reason': 'missing entry notional'}]


def test_a_close_with_neither_is_flagged_unexplained():
    """This is the shape of the five that went missing."""
    ev = events(('POSITION_CLOSED', {'symbol': 'SOLUSDT', 'client_id': 'gt-a'}),
                ('POSITION_CLOSED', {'symbol': 'DOGEUSDT', 'client_id': 'gt-b'}),
                ('POSITION_CLOSED', {'symbol': 'LINKUSDT', 'client_id': 'gt-c'}))
    gaps = reconciliation_gaps(ev, {'gt-c'})
    assert [g['client_id'] for g in gaps] == ['gt-a', 'gt-b']
    assert all(g['reason'] == 'UNEXPLAINED' for g in gaps)


async def test_runtime_journals_the_reason_a_settlement_was_withheld(engine_factory):
    written = []
    engine = engine_factory()
    engine.gateway = SimpleNamespace(journal=SimpleNamespace(
        write=lambda kind, payload: written.append((kind, payload))))
    engine.record_outcome(1.0, notional=None, symbol='SOLUSDT', trade_id='gt-a')
    assert written == [('SETTLE_WITHHELD', {'symbol': 'SOLUSDT', 'client_id': 'gt-a',
                                            'reason': 'missing entry notional'})]
    assert len(engine.ledger.records) == 0, 'a withheld outcome must not reach the ledger'


async def test_withholding_survives_having_no_gateway(engine_factory):
    """Paper mode has no journal; the guard must not turn an accounting gap into a crash."""
    engine = engine_factory()
    engine.gateway = None
    engine.record_outcome(1.0, notional=0.0, symbol='SOLUSDT', trade_id='gt-a')
    assert len(engine.ledger.records) == 0


# ------------------------------------------------------------------ provenance
def test_backfilled_rows_are_marked_but_still_count(tmp_path):
    """Provenance, not a filter.

    Excluding recovered rows from Kelly would re-create the exact bias the
    backfill exists to remove, so origin is recorded and nothing reads it as a
    reason to skip the row.
    """
    ledger = OutcomeLedger(tmp_path/'led.json', 'testnet')
    assert ledger.append(-0.3, 296.0, 1788703412.0, 'ADAUSDT', 'gt-live')
    assert ledger.append(-0.68, 296.0, 1788711181.0, 'SOLUSDT', 'gt-late', origin='backfill')
    assert [r['origin'] for r in ledger.records] == ['live', 'backfill']
    reread = OutcomeLedger(tmp_path/'led.json', 'testnet')
    assert len(reread.records) == 2, 'both rows load; neither is filtered out'


def test_duplicate_trade_ids_are_refused_so_backfill_cannot_double_count(tmp_path):
    ledger = OutcomeLedger(tmp_path/'led.json', 'testnet')
    assert ledger.append(-0.3, 296.0, 1788703412.0, 'ADAUSDT', 'gt-a') is True
    assert ledger.append(-0.3, 296.0, 1788703412.0, 'ADAUSDT', 'gt-a', origin='backfill') is False
    assert len(ledger.records) == 1


def test_rows_written_before_provenance_existed_still_load(tmp_path):
    """The 20 rows already on disk have no origin field; they must remain valid."""
    path = tmp_path/'led.json'
    path.write_text(json.dumps({'schema': 2, 'source': 'testnet', 'records': [
        {'net_pnl_usdt': -0.1, 'entry_notional_usdt': 50.0, 'net_return': -0.002,
         'closed_at': 1788664105.0, 'symbol': 'ADAUSDT', 'trade_id': 'gt-old'}]}), encoding='utf-8')
    assert len(OutcomeLedger(path, 'testnet').records) == 1


def test_an_unknown_origin_is_refused(tmp_path):
    with pytest.raises(ValueError):
        OutcomeLedger(tmp_path/'led.json', 'testnet').append(
            -0.3, 296.0, 1788703412.0, 'ADAUSDT', 'gt-a', origin='estimated')


# ------------------------------------------------ naming the hole, not hiding it
def test_an_unattributable_close_is_named_not_left_unexplained():
    """SOLUSDT gt-857104626f5d and DOGEUSDT gt-2945b65c: the venue closed these
    positions but the journal only ever wrote (0.0, 'NEW'), so the reconciler
    built its attribution from a fill history that never mentioned them."""
    ev = events(('POSITION_CLOSED', {'symbol': 'DOGEUSDT', 'client_id': 'gt-ghost'}))
    reasons = gap_reasons(results=[], matched={'DOGEUSDT': {100: 'gt-known'}})
    assert reconciliation_gaps(ev, set(), reasons) == [
        {'symbol': 'DOGEUSDT', 'client_id': 'gt-ghost',
         'reason': 'ATTRIBUTION_IMPOSSIBLE: no venue order carries this client id'}]


def test_a_close_lost_to_truncation_says_so():
    """The three SOLUSDT round trips that were collateral damage: attributable,
    but the symbol had already been truncated by the unattributable one."""
    ev = events(('POSITION_CLOSED', {'symbol': 'SOLUSDT', 'client_id': 'gt-late'}))
    reasons = gap_reasons(results=[{'trade_id': 'gt-early'}],
                          matched={'SOLUSDT': {100: 'gt-early', 101: 'gt-late'}})
    gap = reconciliation_gaps(ev, set(), reasons)[0]
    assert gap['reason'].startswith('FOLD_TRUNCATED')


def test_a_journalled_reason_outranks_an_inferred_one():
    """The runtime knows why it withheld; the reconciler is only guessing."""
    ev = events(('SETTLE_WITHHELD', {'symbol': 'SOLUSDT', 'client_id': 'gt-a',
                                     'reason': 'incomplete history or non-USDT fee'}),
                ('POSITION_CLOSED', {'symbol': 'SOLUSDT', 'client_id': 'gt-a'}))
    reasons = gap_reasons(results=[], matched={})
    assert reconciliation_gaps(ev, set(), reasons)[0]['reason'] == 'incomplete history or non-USDT fee'


def test_a_settled_close_is_never_reported_as_a_gap():
    ev = events(('POSITION_CLOSED', {'symbol': 'SOLUSDT', 'client_id': 'gt-a'}))
    reasons = gap_reasons(results=[{'trade_id': 'gt-a'}], matched={'SOLUSDT': {100: 'gt-a'}})
    assert reconciliation_gaps(ev, {'gt-a'}, reasons) == []


def test_gap_reasons_reports_what_the_venue_knows():
    reasons, attributed = gap_reasons(results=[{'trade_id': 'gt-a'}],
                                      matched={'SOLUSDT': {1: 'gt-a', 2: 'gt-b'},
                                               'DOGEUSDT': {3: 'gt-c'}})
    assert attributed == {'gt-a', 'gt-b', 'gt-c'}
    assert set(reasons) == {'gt-b', 'gt-c'}, 'everything attributed but unfolded is named'


def test_gaps_still_resolve_without_reconciler_context():
    """reconciliation_gaps must stay usable from a bare journal + ledger."""
    ev = events(('POSITION_CLOSED', {'symbol': 'SOLUSDT', 'client_id': 'gt-a'}))
    assert reconciliation_gaps(ev, set())[0]['reason'] == 'UNEXPLAINED'
