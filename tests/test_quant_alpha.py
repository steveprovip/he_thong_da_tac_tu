"""Causal microstructure, adaptive alpha and adverse-selection regressions."""
from dataclasses import replace
import math
import pytest

from core.models import Book
from core.real_alpha_engine import AlphaEngine, Features, lead_lag_signal
from core.groktopus_swarm import GroktopusSwarm, adverse_selection


def book(mid=100, bq=30, aq=10, t=0, seq=1):
    return Book('BTCUSDT', ((mid-.01, bq),), ((mid+.01, aq),), t, seq)


def test_microprice_tick_units_and_cache_invalidation():
    engine = AlphaEngine(tick_size=.01)
    engine.on_book(book())
    f = engine.features()
    assert f.micro_price == pytest.approx(100.005)
    assert f.micro_delta_ticks == pytest.approx(.5)
    assert engine.features() is f
    engine.on_book(book(bq=10, aq=30, t=.2, seq=2))
    assert engine.features().micro_delta_ticks == pytest.approx(-.5)
    engine.reset_flow()
    assert engine.features().micro_delta_ticks == 0


def test_flow_acceleration_reversal_and_duplicate_are_causal():
    engine = AlphaEngine(10, 2, tick_size=.01)
    engine.on_book(book())
    for i in range(12):
        engine.on_trade(100, 1, i < 6, i*.1, i)
    f = engine.features()
    assert f.flow_acceleration < 0 and f.flow_reversal
    assert adverse_selection(f, 'BUY', .99)
    assert not engine.on_trade(100, 100, True, 1.1, 11)
    assert engine.features() is f
    engine.reset_flow()
    assert not engine.features().flow_reversal


def test_vpin_baseline_is_a_causal_rolling_median():
    """Enough bucket readings expose the symbol's own VPIN regime; too few
    readings yield None so the relative gate falls back to the absolute one."""
    engine = AlphaEngine(bucket_volume=1.0, bucket_window=40, min_buckets=3)
    engine.on_book(book())
    for i in range(40):
        engine.on_trade(100, 1, i % 2 == 0, 1001.0 + i, i + 1)
    f = engine.features()
    assert f.vpin is not None and f.vpin_baseline is not None
    assert 0 < f.vpin_baseline <= 1
    engine.reset_flow()
    assert engine.features().vpin_baseline is None


def test_fast_momentum_has_warmup_and_uses_previous_variance():
    engine = AlphaEngine(tick_size=.01)
    for i in range(30):
        engine.on_book(book(100 + .01*(i % 2), t=i*.21, seq=i+1))
    engine.on_book(book(100.2, t=6.4, seq=31))
    f = engine.features()
    assert f.fast_momentum > 1.5 and f.fast_timestamp == 6.4
    assert f.variance_per_second >= 0


def test_lead_lag_expires_rejects_future_and_attenuates_caught_up_alt():
    leader = Features(ready=True, fast_momentum=3, fast_return=.001, fast_timestamp=10)
    alt = Features(ready=True, fast_timestamp=10)
    fs = {'BTCUSDT': leader, 'SOLUSDT': alt}
    signal = lead_lag_signal('SOLUSDT', fs, 10.2)
    assert signal > 0
    assert lead_lag_signal('BTCUSDT', fs, 10.2) == 0
    assert lead_lag_signal('SOLUSDT', fs, 12.01) == 0
    assert lead_lag_signal('SOLUSDT', fs, 9.9) == 0
    fs['SOLUSDT'] = replace(alt, fast_return=.001)
    assert lead_lag_signal('SOLUSDT', fs, 10.2) == 0
    fs['SOLUSDT'] = alt
    fs['ETHUSDT'] = replace(leader, fast_momentum=-3, fast_return=-.001)
    assert lead_lag_signal('SOLUSDT', fs, 10.2) == pytest.approx(0)


def test_dynamic_atr_and_directional_terms():
    engine = AlphaEngine()
    engine.on_book(book())
    for i in range(60):
        width = 2 if i < 46 else .2
        engine.on_bar(i*60, 100+width, 100-width, 100)
    assert engine.features().atr_ratio < 1
    swarm = GroktopusSwarm()
    f = Features(vpin=.2, atr=5, ready=True, vwap_z=-2, obi=.2)
    def signals(feature, **kw):
        return {v.agent: v for v in swarm.evaluate(feature, 100, True, True, True, True, **kw)[1]}
    assert signals(replace(f, atr_ratio=2))['NORO'].confidence < signals(f)['NORO'].confidence
    assert signals(replace(f, micro_delta_ticks=1, flow_acceleration=.5))['LUMEN'].confidence > signals(f)['LUMEN'].confidence
    assert signals(f, lead_lag=2)['TIDAL'].side == 'BUY'
    decision, votes = swarm.evaluate(f, 100, True, True, True, True, lead_lag=1)
    assert {v.agent: v for v in votes}['TIDAL'].side == 'BUY'
    assert decision.regime == 'CHOP'
    assert swarm.evaluate(replace(f, vpin_jump=.2, vpin=.6), 100, True, True, True, True)[0].veto
    assert swarm.evaluate(replace(f, sweep=1, vpin_jump=.12), 100, True, True, True, True)[0].veto
    assert not swarm.evaluate(replace(f, sweep=1, vpin_jump=.02), 100, True, True, True, True)[0].veto


@pytest.mark.parametrize('field', ['obi', 'micro_delta_ticks', 'atr_ratio', 'flow_acceleration', 'vpin'])
def test_invalid_features_fail_closed(field):
    f = replace(Features(ready=True, atr=5, vpin=.2), **{field: math.nan})
    decision, votes = GroktopusSwarm().evaluate(f, 100, True, True, True, True)
    assert decision.veto and not decision.allowed and len(votes) == 8
