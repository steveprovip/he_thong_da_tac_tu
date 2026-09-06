import pytest
from core.real_alpha_engine import AlphaEngine, VolumeBuckets, cross_sectional_momentum
from core.models import Book


def book(bq=30, aq=10, seq=1):
    return Book('BTCUSDT', ((100., bq),), ((101., aq),), float(seq), seq)


def test_obi_and_ofi():
    engine = AlphaEngine()
    engine.on_book(book())
    assert engine.features().obi == .5
    engine.on_book(book(40, 5, 2))
    assert engine.features().ofi == pytest.approx(15/45)
    assert not engine.on_book(book())


def test_bucket_split_residual_and_warmup():
    bucket = VolumeBuckets(10, 2)
    bucket.add(15, True)
    assert bucket.value is None
    bucket.add(5, False)
    assert bucket.value == .5
    assert bucket.filled == 0
    bucket.add(30, False)
    assert bucket.value == 1


def test_cvd_dedup_and_reset():
    engine = AlphaEngine(1, 2)
    engine.on_trade(100, 2, True, 1, 1)
    assert not engine.on_trade(100, 2, True, 1, 1)
    engine.on_trade(100, 1, False, 2, 2)
    assert engine.cvd == 1
    engine.reset_flow()
    assert engine.buckets.value is None and engine.cvd == 0


def test_sweep_and_flat_rsi():
    engine = AlphaEngine()
    engine.on_book(book())
    for t in range(20):
        engine.on_bar(t, 102, 99, 100)
    assert engine.features().rsi == 50
    engine.on_bar(21, 102, 98, 100)
    assert engine.features().sweep == 1


def test_cross_section_and_invalid_book():
    assert cross_sectional_momentum({'a': 1, 'b': 3}) == {'a': -1., 'b': 1.}
    with pytest.raises(ValueError):
        Book('X', ((101, 1),), ((100, 1),), 1)
