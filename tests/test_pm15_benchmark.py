"""research/pm15_benchmark.py: break-even ask after the Polymarket crypto taker fee, window alignment, calibration and
the real-signal summary. Synthetic data; the measured numbers are in data/pm15_benchmark.json."""
import json

import numpy as np
import pytest

from research import pm15_benchmark as pb
from research.polymarket_15m_fast_arb import fee


@pytest.mark.parametrize('p', [0.1063, 0.3, 0.5, 0.75, 0.95])
def test_breakeven_ask_zeroes_the_ev_after_fee(p):
    a = pb.breakeven_ask(p)
    assert a < p and p - a - fee(a, 0.07) == pytest.approx(0.0, abs=1e-9)


def test_ten_x_ticket_needs_more_than_ten_percent():
    """A 0.10 share pays 1.00 (10x) only with probability p: EV > 0 needs p > 0.10 + 0.07 x 0.10 x 0.90 = 0.1063."""
    assert pb.breakeven_ask(0.1063) == pytest.approx(0.10, abs=1e-4)
    assert pb.breakeven_ask(0.10) < 0.10
    assert pb.breakeven_ask(0.0) == 0.0 and pb.breakeven_ask(1.0) == 1.0


def test_window_points_align_to_quarter_hours_and_use_past_volatility():
    n = 15 * 40
    t = (np.arange(n) * 60_000 + 1_700_006_400_000).astype(np.int64)    # 2023-11-15 00:00 UTC: on a 900 s boundary
    rng = np.random.default_rng(0)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 0.001, n)))
    o = np.r_[c[0], c[:-1]]
    pts = pb.window_points({'t': t, 'o': o, 'c': c})
    starts = np.unique(pts['start'])
    assert ((starts - t[0]) % 900_000 == 0).all() and starts.min() >= t[121]   # 120 returns of history first
    assert set(pts['e'].tolist()) == {5, 10, 13}
    for s, y in zip(pts['start'], pts['y']):
        i = int((s - t[0]) // 60_000)
        assert y == (c[i + 14] > o[i])                                    # outcome = 15th close vs 1st open


def test_calibration_rewards_a_calibrated_forecast():
    rng = np.random.default_rng(1)
    p = rng.uniform(0, 1, 20_000)
    y = rng.uniform(0, 1, 20_000) < p
    cal = pb.calibration(p, y)
    assert cal['brier'] < cal['brier_base'] and cal['skill'] > 0.25
    assert all(abs(r['p_mean'] - r['freq']) < 0.05 for r in cal['reliability'])


def test_real_signal_summary(tmp_path):
    f = tmp_path / 's.jsonl'
    f.write_text('\n'.join(json.dumps(r) for r in [
        {'won': True, 'pnl_per_share': 0.5, 'price': 0.45, 'edge': 0.1, 'trigger': 'IMPULSE'},
        {'won': False, 'pnl_per_share': -0.4, 'price': 0.38, 'edge': 0.06},
        {'won': None, 'pnl_per_share': None, 'price': 0.5, 'edge': 0.1}]), encoding='utf-8')
    out = pb.real_signals(f)
    assert out['all']['n'] == 2 and out['all']['wins'] == 1 and out['impulse']['n'] == 1 and out['model']['n'] == 1
    assert out['all']['pnl_per_share_sum'] == pytest.approx(0.1)
