"""R1-R4 coordinator: idempotency, window skipping, crash recovery, capital gate.

The thing under test is not "does it schedule". It is "can this system be made
to do the same two-leg entry twice". Every test here is a way of trying.
"""
from datetime import datetime, timedelta, timezone
import inspect
import json
import math
from types import SimpleNamespace

import pytest

from core.carry_runtime import CarryRuntime, arguments
from core.funding_carry import (BOOK_EQUITY_USDT, LIVE, RESEARCH_ONLY, SHADOW,
                                TWO_LEG_MIN_NOTIONAL_USDT, two_leg_capital_gate)
from core.outcome_ledger import OutcomeLedger, carry_pnl, daily_brief
from core.routine_scheduler import (DONE, EXECUTING, PENDING, RECONCILED, ROUTINES,
                                    SAIGON, Lease, Routine, RoutineError,
                                    RoutineScheduler, brief_label, brief_window_start,
                                    routine_key, slot_key)
from live.execution_gateway import Journal

HOUR = 3600.0
T0 = 1_789_000_000.0 - (1_789_000_000.0 % HOUR)       # aligned on a funding hour


def scheduler(tmp_path=None, mode='testnet', **kw):
    path = (tmp_path / 'routines.json') if tmp_path is not None else None
    return RoutineScheduler(path, mode=mode, clock=lambda: T0, **kw)


# ============================================================== idempotency key
def test_key_is_the_specified_tuple_and_is_normalised():
    assert routine_key('testnet', 'funding-scan', 'BTCUSDT', 3600) == \
           routine_key('TESTNET', 'Funding-Scan', 'btcusdt', 3600.0)
    assert len(routine_key('testnet', 'funding-scan', 'BTCUSDT', 3600)) == 64


@pytest.mark.parametrize('args', [
    ('shadow', 'funding-scan', 'BTCUSDT', 3600),
    ('testnet', 'stage-pair', 'BTCUSDT', 3600),
    ('testnet', 'funding-scan', 'ETHUSDT', 3600),
    ('testnet', 'funding-scan', 'BTCUSDT', 14400),
])
def test_every_component_of_the_key_separates_streams(args):
    assert routine_key(*args) != routine_key('testnet', 'funding-scan', 'BTCUSDT', 3600)


def test_slot_is_key_plus_window_index():
    a = slot_key('testnet', 'funding-scan', 'BTCUSDT', 3600, 10)
    assert a != slot_key('testnet', 'funding-scan', 'BTCUSDT', 3600, 11)
    assert a == slot_key('testnet', 'funding-scan', 'BTCUSDT', 3600, 10)


# ==================================================================== claiming
def test_one_claim_per_window_per_symbol():
    s = scheduler()
    first = s.claim('stage-pair', 'BTCUSDT', T0)
    assert first is not None and first.state == PENDING
    assert s.claim('stage-pair', 'BTCUSDT', T0) is None          # same window
    assert s.claim('stage-pair', 'BTCUSDT', T0 + 60) is None      # still same window
    assert s.claim('stage-pair', 'ETHUSDT', T0) is not None       # different symbol
    assert s.claim('stage-pair', 'BTCUSDT', T0 + HOUR).window_index == first.window_index + 1


def test_a_completed_slot_is_not_reopened_in_its_own_window():
    s = scheduler()
    lease = s.claim('stage-pair', 'BTCUSDT', T0)
    for step in (s.mark_executing, s.mark_reconciled, s.mark_done):
        lease = step(lease, T0)
    assert lease.state == DONE
    assert s.claim('stage-pair', 'BTCUSDT', T0 + 120) is None
    assert s.state('stage-pair', 'BTCUSDT', T0) == DONE


def test_window_closes_and_late_work_is_skipped_not_run_late():
    s = scheduler()
    assert ROUTINES['stage-pair'].window == 300.0        # AuthToken TTL is 5s
    assert s.window_open('stage-pair', T0 + 299)
    assert not s.window_open('stage-pair', T0 + 301)
    assert s.claim('stage-pair', 'BTCUSDT', T0 + 301) is None
    assert s.claim('stage-pair', 'BTCUSDT', T0 + HOUR) is not None


def test_alternate_funding_periods_shift_the_windows():
    s = scheduler()
    assert s.claim('funding-scan', '*', T0, period=28800.0) is not None
    # 1h and 8h are different streams, so the 1h slot is still free.
    assert s.claim('funding-scan', '*', T0, period=3600.0) is not None
    with pytest.raises(RoutineError):
        s.claim('funding-scan', '*', T0, period=900.0)
    with pytest.raises(RoutineError):
        s.claim('no-such-routine', '*', T0)


# =============================================================== state machine
def test_transitions_are_linear_and_one_way():
    s = scheduler()
    lease = s.claim('funding-scan', '*', T0)
    with pytest.raises(RoutineError):
        s.mark_reconciled(lease, T0)          # cannot skip EXECUTING
    with pytest.raises(RoutineError):
        s.mark_done(lease, T0)
    lease = s.mark_executing(lease, T0 + 1)
    with pytest.raises(RoutineError):
        s.advance(lease, PENDING, T0 + 2)     # no way back: that is a double entry
    with pytest.raises(RoutineError):
        s.mark_executing(lease, T0 + 2)
    lease = s.mark_reconciled(lease, T0 + 3)
    lease = s.mark_done(lease, T0 + 4)
    with pytest.raises(RoutineError):
        s.mark_done(lease, T0 + 5)
    assert [h[0] for h in lease.history] == [PENDING, EXECUTING, RECONCILED, DONE]


def test_transition_on_an_unknown_slot_is_refused():
    s = scheduler()
    orphan = Lease('deadbeef', 'k', 'testnet', 'stage-pair', 'BTCUSDT', HOUR, 1, T0, T0 + 300)
    with pytest.raises(RoutineError):
        s.mark_executing(orphan, T0)


# ============================================================== crash recovery
def test_restart_cannot_reclaim_a_slot_left_executing(tmp_path):
    before = scheduler(tmp_path)
    lease = before.mark_executing(before.claim('stage-pair', 'BTCUSDT', T0), T0)
    del before                                             # process dies here

    after = scheduler(tmp_path)
    assert after.claim('stage-pair', 'BTCUSDT', T0) is None
    stranded = after.recover()
    assert [(l.routine, l.symbol, l.state) for l in stranded] == \
           [('stage-pair', 'BTCUSDT', EXECUTING)]
    assert stranded[0].slot == lease.slot
    # Recovery hands the slot back for reconciliation; it does not complete it.
    assert after.state('stage-pair', 'BTCUSDT', T0) == EXECUTING
    resumed = after.mark_done(after.mark_reconciled(stranded[0], T0 + 5), T0 + 6)
    assert resumed.state == DONE and after.recover() == []


def test_state_survives_restart_for_every_reached_state(tmp_path):
    s = scheduler(tmp_path)
    s.claim('funding-scan', '*', T0)
    s.mark_executing(s.claim('envelope-size', 'BTCUSDT', T0), T0)
    done = s.claim('stage-pair', 'ETHUSDT', T0)
    s.mark_done(s.mark_reconciled(s.mark_executing(done, T0), T0), T0)

    again = scheduler(tmp_path)
    assert again.state('funding-scan', '*', T0) == PENDING
    assert again.state('envelope-size', 'BTCUSDT', T0) == EXECUTING
    assert again.state('stage-pair', 'ETHUSDT', T0) == DONE
    assert len(again.unfinished()) == 2


def test_state_file_is_mode_scoped_and_version_checked(tmp_path):
    scheduler(tmp_path, mode='testnet').claim('stage-pair', 'BTCUSDT', T0)
    with pytest.raises(RoutineError):
        scheduler(tmp_path, mode='mainnet')                # never cross modes
    path = tmp_path / 'routines.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['schema'] = 99
    path.write_text(json.dumps(data), encoding='utf-8')
    with pytest.raises(RoutineError):
        scheduler(tmp_path, mode='testnet')


def test_pruning_never_evicts_an_unfinished_slot(tmp_path):
    s = scheduler(tmp_path, max_slots=3)
    stuck = s.mark_executing(s.claim('stage-pair', 'BTCUSDT', T0), T0)
    for n in range(1, 8):
        now = T0 + n * HOUR
        lease = s.claim('stage-pair', 'BTCUSDT', now)
        s.mark_done(s.mark_reconciled(s.mark_executing(lease, now), now), now)
    assert stuck.slot in s.slots and s.slots[stuck.slot]['state'] == EXECUTING
    assert scheduler(tmp_path, max_slots=3).recover()[0].slot == stuck.slot


# ================================================= R4 wall-clock anchoring
def test_daily_brief_window_is_0700_asia_saigon():
    assert ROUTINES['reconcile-brief'].period == 86400.0
    assert ROUTINES['reconcile-brief'].anchor_local_seconds == 7 * 3600
    # Asia/Saigon is a fixed UTC+7 with no DST, so 07:00 local is 00:00 UTC.
    assert ROUTINES['reconcile-brief'].anchor_offset() == 0.0
    start = brief_window_start(T0)
    local = datetime.fromtimestamp(start, SAIGON)
    assert (local.hour, local.minute, local.second) == (7, 0, 0)
    assert brief_label(T0) == local.strftime('%Y-%m-%d')
    assert start <= T0 < start + 86400


def test_only_one_brief_per_business_day():
    s = scheduler()
    at_seven = brief_window_start(T0) + 5
    lease = s.claim('reconcile-brief', '*', at_seven)
    assert lease is not None
    s.mark_done(s.mark_reconciled(s.mark_executing(lease, at_seven), at_seven), at_seven)
    assert s.claim('reconcile-brief', '*', at_seven + 1800) is None
    assert s.claim('reconcile-brief', '*', at_seven + 86400) is not None
    # Outside the one-hour grace the brief is skipped, not published late.
    assert s.claim('reconcile-brief', '*', at_seven + 86400 * 2 + 4000) is None


def test_routine_definition_rejects_impossible_windows():
    with pytest.raises(RoutineError):
        Routine('x', period=60, window=0)
    with pytest.raises(RoutineError):
        Routine('x', period=60, window=61)
    with pytest.raises(RoutineError):
        Routine('x', period=0, window=1)


def test_snapshot_reports_every_routine():
    s = scheduler()
    s.mark_executing(s.claim('stage-pair', 'BTCUSDT', T0), T0)
    snap = s.snapshot(T0)
    assert set(snap['routines']) == {'funding-scan', 'envelope-size', 'stage-pair',
                                     'reconcile-brief'}
    assert snap['states'][EXECUTING] == 1 and snap['recovering'] == 1
    assert snap['routines']['stage-pair']['agents'] == ['ZEPHR', 'VESKA']


# ======================================================== hard capital gate
def test_the_385_book_can_never_stage_a_two_leg_carry():
    assert BOOK_EQUITY_USDT == 3.85 and TWO_LEG_MIN_NOTIONAL_USDT == 10.0
    for mode in ('shadow', 'testnet', 'mainnet'):
        gate = two_leg_capital_gate(BOOK_EQUITY_USDT, None, mode)
        assert gate.mode == RESEARCH_ONLY and not gate.allowed and gate.research


def test_leverage_cannot_buy_its_way_past_the_two_leg_minimum():
    """Leverage moves the margin posted, not the notional the venue matches."""
    assert two_leg_capital_gate(50.0, 9.99, 'testnet').mode == RESEARCH_ONLY
    assert two_leg_capital_gate(50.0, 10.0, 'testnet').mode == LIVE
    assert two_leg_capital_gate(50.0, 60.0, 'testnet').mode == RESEARCH_ONLY  # > equity
    assert two_leg_capital_gate(50.0, 20.0, 'shadow').mode == SHADOW
    assert two_leg_capital_gate(float('nan'), 20.0, 'testnet').mode == RESEARCH_ONLY
    assert two_leg_capital_gate(-1.0, 20.0, 'testnet').mode == RESEARCH_ONLY


def runtime(tmp_path, mode='testnet'):
    journal = Journal(tmp_path / 'pairs.sqlite')
    venue = SimpleNamespace(settings=SimpleNamespace(mode=mode, armed=True))
    args = arguments(['--mode', mode, '--routine-state', str(tmp_path / 'routines.json'),
                      '--journal', str(tmp_path / 'pairs.sqlite')])
    return CarryRuntime(args, venue, venue, journal), journal


def test_runtime_forces_research_only_under_ten_usdt(tmp_path):
    r, journal = runtime(tmp_path)
    try:
        assert r.execution_enabled()                     # nothing measured yet
        gate = r.enforce_capital_gate(BOOK_EQUITY_USDT, 100.0)
        assert gate.mode == RESEARCH_ONLY and r.research_only
        assert not r.execution_enabled()
        kinds = [k for k, in journal.db.execute('SELECT kind FROM events')]
        assert 'CAPITAL_GATE_RESEARCH_ONLY' in kinds
    finally:
        journal.close()


def test_shadow_mode_never_executes_however_rich_the_book(tmp_path):
    r, journal = runtime(tmp_path, mode='shadow')
    try:
        assert r.enforce_capital_gate(10_000.0, 100.0).mode == SHADOW
        assert not r.execution_enabled() and not r.research_only
    finally:
        journal.close()


def test_scheduler_is_wired_into_the_runtime(tmp_path):
    r, journal = runtime(tmp_path)
    try:
        assert r.scheduler.mode == 'testnet'
        assert r.routine_period('stage-pair') == 3600.0
        assert r.routine_period('reconcile-brief') is None
        lease = r.routine_slot('stage-pair', 'BTCUSDT', T0)
        assert lease is not None and r.routine_slot('stage-pair', 'BTCUSDT', T0) is None
        r.scheduler.mark_executing(lease, T0)
        assert [l.slot for l in r.recover_routines()] == [lease.slot]
        assert r.gateway.halted                          # a half-open pair halts staging
    finally:
        journal.close()


def test_routine_interval_never_touches_the_30s_protection_watchdog():
    """RUNE/MARIN protection is not a throughput knob (invariant)."""
    source = inspect.getsource(CarryRuntime.watchdog)
    assert 'self.stop.wait(), 30' in source
    assert 'routine' not in source and 'scheduler' not in source
    assert 'routine' not in inspect.getsource(CarryRuntime.watchdog_once)


# ============================================================ R4 brief maths
def test_brief_formula_is_spot_plus_perp_plus_funding_minus_fees():
    assert carry_pnl(1.0, -0.4, 0.9, 0.25) == pytest.approx(1.25)
    with pytest.raises(ValueError):
        carry_pnl(1.0, 0.0, float('inf'), 0.0)


def leg(closed_at, symbol='BTCUSDT', spot=1.0, perp=-0.4, funding=0.9, fees=0.25):
    return {'symbol': symbol, 'delta_spot_usdt': spot, 'delta_perp_usdt': perp,
            'funding_usdt': funding, 'fees_usdt': fees, 'closed_at': closed_at}


def test_brief_counts_only_the_business_day_it_covers():
    end = brief_window_start(T0)
    brief = daily_brief([leg(end - 3600), leg(end - 86_000, 'ETHUSDT'),
                         leg(end - 90_000, 'OLDUSDT'), leg(end + 60, 'FUTUSDT')], T0)
    assert brief['legs'] == 2 and set(brief['by_symbol']) == {'BTCUSDT', 'ETHUSDT'}
    assert brief['window_end'] - brief['window_start'] == 86400.0
    assert brief['net_pnl_usdt'] == pytest.approx(2.5)
    assert brief['funding_usdt'] == pytest.approx(1.8) and brief['fees_usdt'] == pytest.approx(0.5)
    assert brief['label'] == brief_label(T0) and brief['status'] == 'RECONCILED'


def test_brief_refuses_a_leg_with_a_missing_component():
    end = brief_window_start(T0)
    broken = leg(end - 60)
    broken.pop('funding_usdt')
    with pytest.raises(ValueError):
        daily_brief([broken], T0)
    with pytest.raises(ValueError):
        daily_brief([dict(leg(end - 60), closed_at=float('nan'))], T0)


def test_brief_cross_checks_against_the_settled_ledger(tmp_path):
    end = brief_window_start(T0)
    legs = [leg(end - 3600)]
    ledger = OutcomeLedger(tmp_path / 'settled.json', 'testnet')
    ledger.append(1.25, 100.0, end - 3600, 'BTCUSDT', 'gt-1')
    ledger.append(9.99, 100.0, end - 200_000, 'BTCUSDT', 'gt-old')
    window = ledger.window(end - 86400, end)
    assert [r['trade_id'] for r in window] == ['gt-1']
    assert daily_brief(legs, T0, settled=window)['status'] == 'RECONCILED'
    mismatched = daily_brief(legs, T0, settled=[{'net_pnl_usdt': 0.0}])
    assert mismatched['status'] == 'UNMATCHED' and 'reason' in mismatched
