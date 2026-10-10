"""10-10 e point-in-time universe screen for the intraday 15m lane (research input, not wired into the engine)."""
from pathlib import Path

from core.universe import DAY_MS, Bar, Listing, bars_from_klines, listings_from_exchange_info, screen

ROOT = Path(__file__).resolve().parents[1]
T0 = 1_700_000_000_000 // DAY_MS * DAY_MS
AS_OF = T0 + 20 * DAY_MS


def lst(sym, **kw):
    a = dict(quote='USDT', contract='PERPETUAL', status='TRADING', onboard_ms=T0 - 400 * DAY_MS, underlying='COIN')
    a.update(kw)
    return Listing(sym, **a)


def bars(qv, rng_bps=400.0, days=20, start=T0):
    return [Bar(start + i * DAY_MS, 100 + rng_bps / 200, 100 - rng_bps / 200, 100.0, qv) for i in range(days)]


def test_ranks_by_median_volume_and_caps_top_n():
    ls = [lst('A'), lst('B'), lst('C')]
    d = {'A': bars(60e6), 'B': bars(90e6), 'C': bars(70e6)}
    picked, why = screen(ls, d, AS_OF, top_n=2)
    assert [p['symbol'] for p in picked] == ['B', 'C']
    assert why['below_top_n'] == 1
    assert abs(picked[0]['median_range_bps'] - 400) < 1e-6


def test_one_day_spike_does_not_lift_a_thin_symbol():
    b = bars(10e6)
    b[-1] = Bar(b[-1].open_ms, b[-1].high, b[-1].low, b[-1].close, 5e9)
    picked, why = screen([lst('PUMP')], {'PUMP': b}, AS_OF)
    assert picked == [] and why['volume'] == 1


def test_range_cap_drops_pump_and_dead_names():
    ls = [lst('WILD'), lst('DEAD'), lst('OK')]
    d = {'WILD': bars(80e6, 5000), 'DEAD': bars(80e6, 50), 'OK': bars(80e6, 600)}
    picked, why = screen(ls, d, AS_OF)
    assert [p['symbol'] for p in picked] == ['OK'] and why['range'] == 2


def test_no_look_ahead_bar_closing_after_as_of_is_ignored():
    b = bars(10e6, days=14) + [Bar(T0 + 14 * DAY_MS, 101, 99, 100, 9e9)]
    # as_of inside day 14: that bar has not closed, so only the 14 thin days count
    picked, why = screen([lst('X')], {'X': b}, T0 + 14 * DAY_MS + 1000)
    assert picked == [] and why['volume'] == 1


def test_unknown_or_ineligible_is_excluded_never_zero_filled():
    ls = [lst('NOAGE', onboard_ms=None), lst('YOUNG', onboard_ms=AS_OF - 10 * DAY_MS),
          lst('IDX', underlying='INDEX'), lst('UNK', underlying=None), lst('USDC', quote='USDC'),
          lst('HALT', status='SETTLING'), lst('SHORT')]
    d = {s.symbol: bars(1e9) for s in ls}
    d['SHORT'] = bars(1e9, days=5)
    picked, why = screen(ls, d, AS_OF)
    assert picked == []
    assert why == {'young_or_unknown_age': 2, 'not_coin': 2, 'not_usdt_perp_trading': 2, 'short_history': 1,
                   'below_top_n': 0}


def test_binance_payload_parsers():
    info = {'symbols': [{'symbol': 'SOLUSDT', 'quoteAsset': 'USDT', 'contractType': 'PERPETUAL',
                         'status': 'TRADING', 'onboardDate': 1, 'underlyingType': 'COIN'}]}
    assert listings_from_exchange_info(info) == [Listing('SOLUSDT', 'USDT', 'PERPETUAL', 'TRADING', 1, 'COIN')]
    k = [[T0, '1', '2.5', '0.5', '2', '10', T0 + DAY_MS - 1, '123.4', 5, '0', '0', '0']]
    assert bars_from_klines(k) == [Bar(T0, 2.5, 0.5, 2.0, 123.4)]


def test_not_wired_into_live_paths():
    for d in ('core', 'live'):
        for f in (ROOT / d).rglob('*.py'):
            if f.name != 'universe.py':
                assert 'core.universe' not in f.read_text(encoding='utf-8', errors='ignore'), f
