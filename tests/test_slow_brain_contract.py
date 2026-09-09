"""Slow-brain contract tests: future/stale/malformed verdicts must block."""
from phan_tich.slow_brain_contract import validate_verdict


def base(**over):
    v = dict(schema_version=1, symbol='BTCUSDT', regime='TREND', veto=False,
             conviction=0.7, as_of_ts=1000.0, generated_at=1001.0,
             expires_at=4601.0, data_cutoff=999.0, model_version='ta-0.4.0')
    v.update(over)
    return v


def test_valid_trend_verdict_passes():
    ok, reason = validate_verdict(base(), 2000.0)
    assert (ok, reason) == (True, 'SLOW_BRAIN_PASS')


def test_veto_and_chop_toxic_block():
    assert validate_verdict(base(veto=True), 2000.0)[0] is False
    assert validate_verdict(base(regime='CHOP'), 2000.0)[1] == 'SLOW_BRAIN_BLOCK'
    assert validate_verdict(base(regime='TOXIC'), 2000.0)[1] == 'SLOW_BRAIN_BLOCK'


def test_future_stale_malformed_block():
    assert validate_verdict(base(generated_at=3000.0), 2000.0)[1] == 'STALE_OR_FUTURE_VERDICT'
    assert validate_verdict(base(expires_at=1500.0), 2000.0)[1] == 'STALE_OR_FUTURE_VERDICT'
    assert validate_verdict(base(data_cutoff=1005.0), 2000.0)[1] == 'STALE_OR_FUTURE_VERDICT'
    assert validate_verdict({'symbol': 'BTCUSDT'}, 2000.0)[1] == 'MISSING_FIELDS'
    assert validate_verdict('not-a-dict', 2000.0)[1] == 'MALFORMED_VERDICT'
    assert validate_verdict(base(regime='MOON'), 2000.0)[1] == 'UNKNOWN_REGIME'
