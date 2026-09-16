"""Q5/Q4 handoff tests: v2 contract lineage, carry-lab, snapshot lineage, fee 6bps."""
from phan_tich.slow_brain_contract import validate_verdict
from phan_tich.carry_lab import forecast_row, settle_row
from core.funding_carry import CarryConfig


def base_v2(**over):
    v = dict(schema_version=2, symbol='BTCUSDT', regime='TREND', veto=False,
             conviction=0.7, as_of_ts=1000.0, generated_at=1001.0,
             expires_at=4601.0, data_cutoff=999.0, model_version='ta-0.4.0',
             available_at=1000.5, published_at=1001.0, reason='TREND-OK',
             timeframe='1h/20', max_source_age_s=7200.0)
    v.update(over)
    return v


def test_v1_still_passes_without_v2_fields():
    v = dict(schema_version=1, symbol='BTCUSDT', regime='TREND', veto=False,
             conviction=0.7, as_of_ts=1000.0, generated_at=1001.0,
             expires_at=4601.0, data_cutoff=999.0, model_version='ta-0.4.0')
    assert validate_verdict(v, 2000.0) == (True, 'SLOW_BRAIN_PASS')


def test_v2_valid_passes():
    assert validate_verdict(base_v2(), 2000.0) == (True, 'SLOW_BRAIN_PASS')


def test_v2_missing_fields_block():
    v = base_v2()
    del v['reason']
    assert validate_verdict(v, 2000.0)[1] == 'MISSING_V2_FIELDS'


def test_v2_lineage_order_violation_blocks():
    assert validate_verdict(base_v2(available_at=999.0), 2000.0)[1] == 'LINEAGE_ORDER_VIOLATION'
    assert validate_verdict(base_v2(published_at=3000.0), 2000.0)[1] == 'LINEAGE_ORDER_VIOLATION'


def test_v2_rerun_must_not_refresh_source_age():
    # v2 as_of=1000, max_age 7200: intent at 9000 is past expires(4601) anyway.
    assert validate_verdict(base_v2(), 9000.0)[1] == 'STALE_OR_FUTURE_VERDICT'
    # Fresh long-lived verdict: source age bound is (now - as_of) > max_age.
    v = base_v2(as_of_ts=1000.0, generated_at=1001.0, expires_at=20000.0,
                available_at=1000.5, published_at=1001.0)
    assert validate_verdict(v, 1000.0 + 7199.0)[0] is True
    assert validate_verdict(v, 1000.0 + 7201.0)[1] == 'SOURCE_TOO_OLD'


def test_v2_empty_reason_or_timeframe_blocks():
    assert validate_verdict(base_v2(reason=''), 2000.0)[1] == 'MISSING_REASON'
    assert validate_verdict(base_v2(timeframe=''), 2000.0)[1] == 'MISSING_TIMEFRAME'


def test_carry_fee_baseline_is_measured_6bps():
    assert CarryConfig().roundtrip_fee_bps == 6.0


def test_carry_forecast_and_settle_net():
    row, why = forecast_row('BTCUSDT', 'SELL', 50.0, 2000.0, 3600.0, 100.0,
                            decision_ts=1500.0, snapshot_id='s1')
    assert why == 'OK' and row['status'] == 'FORECAST'
    out, why = settle_row(row, 5.0, -1.0, 0.6, borrow_usdt=0.1,
                          capital_locked_usdt=100.0)
    assert why == 'SETTLED' and out['net_usdt'] == 5.0 - 1.0 - 0.6 - 0.1
    assert out['status'] == 'SETTLED'


def test_carry_unmatched_never_attributed():
    row, _ = forecast_row('BTCUSDT', 'SELL', 50.0, 2000.0, 3600.0, 100.0,
                          decision_ts=1500.0)
    out, why = settle_row(row, 5.0, 0.0, 0.6, matched=False)
    assert why == 'UNMATCHED' and out['status'] == 'UNMATCHED'
    assert 'net_usdt' not in out


def test_carry_bad_timing_blocked():
    assert forecast_row('BTCUSDT', 'SELL', 50.0, 1000.0, 3600.0, 100.0,
                        decision_ts=1500.0)[1] == 'BAD_CARRY_TIMING'


def test_submit_lineage_is_shadow_only():
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from live.execution_gateway import ExecutionGateway, Journal
    from core.consensus_engine import TokenAuthority, ConsensusEngine, Vote
    from core.models import Intent
    import tempfile, os
    path = os.path.join(tempfile.gettempdir(), 'test-lineage.sqlite')
    try:
        os.remove(path)
    except OSError:
        pass
    journal = Journal(path)
    policy = ConsensusEngine()
    auth = TokenAuthority(policy=policy)
    client = SimpleNamespace(settings=SimpleNamespace(armed=True, mode='testnet'),
                             place_entry=AsyncMock(return_value={'executedQty': '0', 'status': 'NEW'}))
    gw = ExecutionGateway(client, auth, journal)
    intent = Intent('BTCUSDT', 'BUY', '0.001', '100.0', '99.0', '101.0', 300.0, 'testnet')
    votes = [Vote(a, 'BUY', 0.9, 't') for a in
             ('NORO', 'LUMEN', 'TIDAL', 'ZEPHR', 'RUNE', 'OKAPI', 'MARIN', 'VESKA')]
    decision = policy.evaluate(votes)
    assert decision.allowed
    token = auth.issue(intent, decision)
    asyncio.get_event_loop().run_until_complete(
        gw.submit(intent, token, snapshot_id='snap-1',
                  slow_brain={'baseline': 'ALLOW', 'would_block': True, 'reason': 'CHOP'}))
    rows = journal.db.execute("SELECT payload FROM events WHERE kind='ENTRY_INTENT'").fetchall()
    import json
    payload = json.loads(rows[-1][0])
    assert payload['snapshot_id'] == 'snap-1' and payload['would_block'] is True
    assert payload['reason'] == 'CHOP' and payload['baseline'] == 'ALLOW'
    journal.close()
    os.remove(path)
