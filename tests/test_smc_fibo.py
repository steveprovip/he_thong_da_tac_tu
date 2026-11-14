"""2026-11-13 SMC Fibo-discount liquidity-sweep detector (core/smc_fibo.py) and the conservative 1 m simulator of
research/smc_fibo_backtest.py. Hand-built bars; the economic verdict is in data/smc_fibo_backtest.json (holdout FAIL)."""
import numpy as np
import pytest

from core.smc_fibo import Bar, SMCFibo
from research import smc_fibo_backtest as bt

H4 = [(10, 9, 9.5), (11, 10, 10.5), (12, 11, 11.5), (11, 10, 10.5), (10.5, 9.5, 10), (13, 12, 12.6)]   # BOS up
H1 = [(100.25, 99.75, 100.0)] * 16 + [(99.9, 99.4, 99.6), (99.7, 99.0, 99.5), (100.2, 99.3, 100.0),
                                      (101.0, 99.9, 100.9), (102.0, 100.8, 101.9), (103.0, 101.8, 102.6),
                                      (102.7, 102.0, 102.2), (102.4, 101.6, 101.8)]       # L 99.0 -> H 103.0
HOUR, M5 = 3_600_000, 300_000


def pullback():
    """50 bars drifting 101.8 -> ~100.6 (strictly falling: no fractal), alternating +-100 delta, then a 5 m swing
    low at 100.30 confirmed by two higher lows."""
    out = []
    for i in range(50):
        c = 101.8 - i * 1.2 / 50
        out.append((c + 0.08, c - 0.08, c, 1000.0, 550.0 if i % 2 else 450.0))
    out += [(100.66, 100.50, 100.55, 1000.0, 500.0), (100.50, 100.30, 100.40, 1000.0, 500.0),
            (100.60, 100.45, 100.55, 1000.0, 500.0), (100.70, 100.55, 100.62, 1000.0, 500.0)]
    return out


SWEEP = (100.45, 100.00, 100.40, 1000.0, 100.0)    # low under 100.30, close back above, delta -800, CLV 0.89


def feed(det, m5_rows, mirror=False, h4=True):
    ax4, ax = 30.0, 200.0
    mk = (lambda t, h, l, c, v=0.0, tb=0.0, a=ax: Bar(t, a - c, a - l, a - h, a - c, v, v - tb)) if mirror else \
         (lambda t, h, l, c, v=0.0, tb=0.0, a=ax: Bar(t, c, h, l, c, v, tb))
    if h4:
        for i, (h, l, c) in enumerate(H4):
            det.on_h4(mk((i + 1) * 4 * HOUR - 100 * HOUR, h, l, c, a=ax4))
    for i, (h, l, c) in enumerate(H1):
        det.on_h1(mk((i + 1) * HOUR, h, l, c))
    t0 = len(H1) * HOUR
    out = []
    for i, (h, l, c, v, tb) in enumerate(m5_rows):
        s = det.on_5m(mk(t0 + (i + 1) * M5, h, l, c, v, tb))
        if s is not None:
            out.append((i, s))
    return out


def test_long_setup_on_the_sweep_bar_with_the_frozen_geometry():
    got = feed(SMCFibo(r_multiple=2.0), pullback() + [SWEEP])
    assert len(got) == 1 and got[0][0] == 54                             # the sweep bar, nothing before it
    s = got[0][1]
    assert s.side == 'BUY' and s.entry == pytest.approx(100.40)
    assert s.leg_lo == 99.0 and s.leg_hi == 103.0 and s.swept == pytest.approx(100.30)
    assert s.retrace == pytest.approx(0.75)                              # (103 - 100) / 4: inside 0.705 .. 1.0
    assert s.sl < 100.0 and s.tp == pytest.approx(s.entry + 2.0 * (s.entry - s.sl))
    assert s.delta_z <= -1.0 and s.clv >= 0.5 and 0.10 <= s.risk_pct <= 1.5


def test_short_setup_is_the_exact_mirror():
    got = feed(SMCFibo(r_multiple=2.0), pullback() + [SWEEP], mirror=True)
    assert len(got) == 1
    s = got[0][1]
    assert s.side == 'SELL' and s.entry == pytest.approx(99.60) and s.sl > 100.0
    assert s.tp == pytest.approx(s.entry - 2.0 * (s.sl - s.entry)) and s.delta_z >= 1.0 and s.clv <= 0.5


@pytest.mark.parametrize('sweep, why', [
    ((100.45, 100.00, 100.40, 1000.0, 900.0), 'buyers, not absorbed sellers'),
    ((100.45, 100.00, 100.25, 1000.0, 100.0), 'close does not reclaim the swept low'),
    ((100.45, 100.25, 100.40, 1000.0, 100.0), 'low above the 0.705 retracement'),
    ((100.45, 100.00, 100.05, 1000.0, 100.0), 'closes in the lower half'),
])
def test_each_condition_is_required(sweep, why):
    assert feed(SMCFibo(), pullback() + [sweep]) == [], why


def test_no_trend_no_setup_and_risk_filter():
    assert feed(SMCFibo(), pullback() + [SWEEP], h4=False) == []         # H4 structure unknown
    assert feed(SMCFibo(risk_max_pct=0.2), pullback() + [SWEEP]) == []   # stop too wide for the filter


def test_an_unconfirmed_swing_low_is_not_liquidity_yet():
    rows = pullback()
    del rows[-1]                                                         # the second higher low is missing
    assert feed(SMCFibo(), rows + [SWEEP]) == []


def test_a_close_below_the_leg_origin_kills_the_leg():
    rows = pullback()
    rows.insert(10, (101.5, 98.8, 98.9, 1000.0, 500.0))                 # closes under L = 99.0
    assert feed(SMCFibo(), rows + [SWEEP]) == []


def test_invalid_parameters_are_refused():
    with pytest.raises(ValueError):
        SMCFibo(zone_lo=1.2)
    with pytest.raises(ValueError):
        SMCFibo(risk_min_pct=1.0, risk_max_pct=0.5)


# ============================================================================= conservative 1 m simulator
def m1(bars):
    a = np.asarray(bars, dtype=float)
    return {'t': (np.arange(len(a)) * 60_000).astype(np.int64), 'o': a[:, 0], 'h': a[:, 1], 'l': a[:, 2], 'c': a[:, 3]}


def test_long_fill_then_take_profit():
    m = m1([(100.0, 100.1, 99.98, 100.0), (100.0, 100.5, 99.9, 100.4), (100.4, 101.02, 100.6, 100.9)])
    r = bt.simulate(m, 0, 1, 100.0, 99.5, 101.0, 0.01, be=False)
    assert r['filled'] and r['why'] == 'TP' and r['gross_bps'] == pytest.approx(100.0)
    assert r['net_bps'] == pytest.approx(96.0)                           # maker in 2 + maker TP 2


def test_gapped_stop_exits_at_the_open_with_taker_fee_and_slip():
    m = m1([(100.0, 100.1, 99.98, 100.0), (99.4, 99.45, 99.3, 99.35)])
    r = bt.simulate(m, 0, 1, 100.0, 99.5, 101.0, 0.01, be=False)
    assert r['why'] == 'SL' and r['exit'] == pytest.approx(99.4 * (1 - 1e-4))
    assert r['net_bps'] == pytest.approx((99.4 * (1 - 1e-4) / 100 - 1) * 1e4 - 7.0)


def test_same_bar_stop_and_target_counts_as_the_stop_and_no_fill_without_trade_through():
    both = m1([(100.0, 100.1, 99.98, 100.0), (100.0, 101.5, 99.4, 100.0)])
    assert bt.simulate(both, 0, 1, 100.0, 99.5, 101.0, 0.01, be=False)['why'] == 'SL'
    touch_only = m1([(100.0, 100.1, 99.995, 100.0)] * 20)                # touches 100.00 but never 99.99
    assert bt.simulate(touch_only, 0, 1, 100.0, 99.5, 101.0, 0.01, be=False) == {'filled': False, 'why': 'NO_FILL'}
    tp_first = m1([(100.0, 101.2, 100.05, 101.0), (101.0, 101.1, 99.9, 100.0)])
    assert bt.simulate(tp_first, 0, 1, 100.0, 99.5, 101.0, 0.01, be=False)['why'] == 'TP_FIRST'


def test_fee_shield_break_even_nets_about_zero():
    m = m1([(100.0, 100.1, 99.98, 100.0), (100.0, 100.6, 99.9, 100.5), (100.5, 100.55, 100.05, 100.1)])
    r = bt.simulate(m, 0, 1, 100.0, 99.5, 101.0, 0.01, be=True)
    assert r['why'] == 'BE' and r['net_bps'] == pytest.approx(0.0, abs=0.02)
    assert bt.simulate(m, 0, 1, 100.0, 99.5, 101.0, 0.01, be=False)['why'] in ('TIME', 'SL')


def test_short_is_mirrored():
    m = m1([(100.0, 100.02, 99.9, 100.0), (100.0, 100.1, 98.98, 99.0)])
    r = bt.simulate(m, 0, -1, 100.0, 100.5, 99.0, 0.01, be=False)
    assert r['why'] == 'TP' and r['gross_bps'] == pytest.approx(100.0) and r['net_bps'] == pytest.approx(96.0)


def test_micro_portfolio_session_stop_and_min_notional():
    day = 86_400_000
    loss = lambda t: {'t_fill': t, 't_exit': t + 1, 'net_bps': -40.0, 'risk_bps': 30.0, 'net_R': -1.33}
    rows = [loss(day + i * 10) for i in range(4)]
    out = bt.micro_portfolio(rows, day, 2 * day)
    assert out['trades'] == 2 and out['skipped'] == {'session_stop': 2}
    wide = [{'t_fill': day, 't_exit': day + 1, 'net_bps': 10.0, 'risk_bps': 200.0, 'net_R': 0.05}]
    assert bt.micro_portfolio(wide, day, 2 * day)['skipped'] == {'min_notional': 1}   # 0.05 U / 2.08 % = 2.4 U
    assert bt.micro_portfolio(rows, day, 2 * day, kelly=True)['trades'] == 0             # no evidence, no Kelly size


def test_statistics_helpers():
    s = bt.stats([10.0, -5.0, 3.0], [10.0, 10.0, 10.0])
    assert s['n'] == 3 and s['mean_net_bps'] == pytest.approx(8 / 3, abs=1e-3) and s['win_rate'] == pytest.approx(2 / 3, abs=1e-4)
    assert s['profit_factor'] == pytest.approx(13 / 5) and s['mdd_R'] == pytest.approx(0.5)
    w = bt.welch_lcb([1.0, 2.0, 3.0], [0.0, 0.0, 0.0])
    assert w['diff'] == pytest.approx(2.0) and w['lcb95'] < 2.0
