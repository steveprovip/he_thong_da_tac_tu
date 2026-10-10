"""10-10 f funding-carry Phase 0 replay: window sums, coverage, costs, trade P&L, gate (offline, synthetic series)."""
import numpy as np

from tools import funding_spread_replay as fr

H8, H1 = 8 * 3600_000, 3600_000


def grid(step, a, b):
    return np.arange(a - a % step + step, b, step, dtype=np.int64)


def test_print_at_T_belongs_to_the_window_ending_at_T():
    t = np.array([0, H8, 2 * H8], dtype=np.int64)
    r = np.array([1.0, 2.0, 4.0])
    assert fr.window_sum(t, r, 0, H8) == 2.0            # (0, H8]: the print at 0 is NOT earned, at H8 is
    assert fr.window_sum(t, r, H8, 2 * H8) == 4.0
    assert fr.on_minute(H8 + 4) == H8 and fr.on_minute(H8 - 30_001) == H8 - 60_000


def test_coverage_flags_holes_as_unknown():
    t = np.array([H8, 2 * H8, 3 * H8], dtype=np.int64)
    assert fr.covered(t, 0, 3 * H8, H8 + 1)
    assert not fr.covered(np.array([H8, 4 * H8]), 0, 4 * H8, H8 + 1)
    assert not fr.covered(np.array([], dtype=np.int64), 0, H8, H8 + 1)


def test_round_trip_costs():
    assert fr.round_trip_cost('bn_perp', 'hl_perp', 'MAKER') == 2 * (2.0 + 1.5)
    assert fr.round_trip_cost('bn_perp', 'hl_perp', 'HEDGE') == 2 * min(2.0 + 4.5 + 1, 5.0 + 1 + 1.5)
    assert fr.round_trip_cost('bn_perp', 'bn_spot', 'HEDGE') == 2 * (2.0 + 10.0 + 1)


def series(bn_rate, hl_rate, px_bn, px_ot, days=40):
    a = fr.ms(fr.START)
    b = a + days * 86400_000
    bt, ht, kt = grid(H8, a - 1, b), grid(H1, a - 1, b), grid(4 * H1, a - 1, b)
    return {'bn': (bt, np.full(len(bt), bn_rate)), 'hl': (ht, np.full(len(ht), hl_rate)),
            'px_bn': (kt, np.array([px_bn(x) for x in kt], float)), 'px_other': (kt, np.array([px_ot(x) for x in kt], float))}


def test_xv_shorts_the_higher_funding_venue_and_books_funding_basis_and_cost():
    s = series(3.0, 0.1, lambda t: 100.0, lambda t: 100.0)   # BN 9 bps/day vs HL 2.4 bps/day, flat prices
    tr = fr.simulate(s, 'XV', 'X', 7, 1.0, 'HEDGE')
    assert tr and tr[0]['legs'] == ('bn_perp', 'hl_perp')
    x = tr[0]
    assert abs(x['funding_bps'] - (21 * 3.0 - 168 * 0.1)) < 1e-9 and x['basis_bps'] == 0.0
    assert abs(x['net_bps'] - (x['funding_bps'] - fr.round_trip_cost('bn_perp', 'hl_perp', 'HEDGE'))) < 1e-9
    assert all(b['t'] >= a['t'] + 7 * 86400 for a, b in zip(tr, tr[1:]))   # no overlapping positions


def test_basis_loss_is_charged():
    a = fr.ms(fr.START)
    s = series(3.0, 0.1, lambda t: 100.0 + (t - a) / 86400_000, lambda t: 100.0)   # short leg rallies
    x = fr.simulate(s, 'XV', 'X', 7, 1.0, 'HEDGE')[0]
    assert x['basis_bps'] < 0 and x['net_bps'] < x['funding_bps'] - x['cost_bps'] + 1e-9


def test_no_trade_below_hurdle_or_on_negative_carry():
    assert fr.simulate(series(0.2, 0.1, lambda t: 1.0, lambda t: 1.0), 'XV', 'X', 7, 1.0, 'HEDGE') == []
    assert fr.simulate(series(-3.0, 0.0, lambda t: 1.0, lambda t: 1.0), 'SP', 'X', 7, 1.0, 'HEDGE') == []


def test_unknown_price_means_no_trade():
    s = series(3.0, 0.1, lambda t: 100.0, lambda t: 100.0)
    t, o = s['px_other']
    s['px_other'] = (t[::2], o[::2])                       # half the 4 h opens missing
    tr = fr.simulate(s, 'XV', 'X', 7, 1.0, 'HEDGE')
    assert all(fr.price_at(*s['px_other'], int(x['t'] * 1000)) is not None for x in tr)


def test_gate_is_n_and_day_ci():
    assert fr.eligible({'n': 100, 'ci95_day': [0.1, 2]})
    assert not fr.eligible({'n': 99, 'ci95_day': [0.1, 2]})
    assert not fr.eligible({'n': 500, 'ci95_day': [0.0, 2]})
    assert not fr.eligible({'n': 0})
