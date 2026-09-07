import json
import pytest
from core.outcome_ledger import OutcomeLedger
from core.real_alpha_engine import VolumeBuckets, AlphaEngine
from core.models import Book
from core.runtime import arguments


def test_vpin_conserves_large_trade_volume_and_partial_bucket():
    b = VolumeBuckets(10, 5)
    b.add(3, False)
    b.add(1000, True)
    assert b.completed == 100
    assert b.filled == pytest.approx(3)
    assert b.total_volume == pytest.approx(b.completed*b.volume+b.filled)
    assert b.value == 1


def test_vpin_many_buckets_does_not_mean_massive_loop():
    b = VolumeBuckets(1, 50)
    b.add(10**10+.5, True)
    assert b.completed == 10**10
    assert b.filled == .5 and len(b.imbalances) == 50


def test_microprice_uses_best_level_not_deeper_walls():
    a = AlphaEngine(tick_size=.01)
    a.on_book(Book('X', ((99.99, 1.),(99.98, 1000.)), ((100.01, 1.),(100.02, 1.)), 1, 1))
    assert a.features().micro_price == pytest.approx(100)
    assert a.features().obi > .9


def test_ledger_units_source_and_dedup(tmp_path):
    path = tmp_path/'ledger.json'
    ledger = OutcomeLedger(path, 'testnet')
    assert ledger.append(-.1, 100., 10., 'X', 'one')
    assert not ledger.append(-.1, 100., 10., 'X', 'one')
    read = OutcomeLedger(path, 'testnet')
    assert read.records[0]['net_return'] == pytest.approx(-.001)
    with pytest.raises(ValueError, match='source'):
        OutcomeLedger(path, 'mainnet')
    data = json.loads(path.read_text())
    data['records'][0]['net_return'] = -.1
    path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match='units'):
        OutcomeLedger(path, 'testnet')


def test_runtime_ignores_mixed_legacy_outcomes(engine_factory, tmp_path):
    (tmp_path/'data').mkdir()
    (tmp_path/'data'/'testnet-outcomes.json').write_text('[-0.1, 0.0002]')
    ledger = OutcomeLedger(tmp_path/'data'/'testnet-settled-v2.json', 'testnet')
    ledger.append(-.1, 100, 1)
    runtime = engine_factory()
    assert list(runtime.sizer.outcomes) == [-.001]


def test_cli_cannot_mix_execution_venues_or_enable_fake_seed():
    assert arguments('paper', []).market_data == 'mainnet'
    assert arguments('testnet', []).market_data == 'testnet'
    with pytest.raises(SystemExit):
        arguments('testnet', ['--market-data', 'mainnet'])
    with pytest.raises(SystemExit):
        arguments('testnet', ['--seed-lab'])
