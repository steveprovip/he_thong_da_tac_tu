"""Smart Money Concepts: known patterns, no lookahead, engine plumbing, swarm default bit-identity."""
import time
from dataclasses import replace

import numpy as np
import pytest

from core.groktopus_swarm import GroktopusSwarm
from core.models import Book
from core.real_alpha_engine import AlphaEngine, Features
from core.smc import FIELDS, SMCState, smc_features

# (high, low, close); open = previous close
PATTERN = [(10, 9, 9.5), (11, 10, 10.5), (13, 11, 12), (12, 10.5, 11), (11.5, 10, 10.2),   # swing high 13 @2
           (13.5, 11, 12.8),                 # 5: wick above 13, close back below -> buy-side sweep
           (14, 12, 13.8),                   # 6: close > 13 -> BOS up; swing low 10 @4 confirmed; OB = bar 4
           (13, 10.8, 11), (11, 9, 9.5)]     # 8: close < 10 against the up structure -> CHoCH down


def _run(bars, **kw):
    h, lo, c = (np.array(x, dtype=float) for x in zip(*bars))
    return smc_features(h, lo, c, **kw)


def _random_bars(n, seed=0):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0, 1e-3, n)))
    o = np.r_[c[0], c[:-1]]
    h = np.maximum(o, c) * (1 + rng.uniform(0, 1e-3, n))
    lo = np.minimum(o, c) * (1 - rng.uniform(0, 1e-3, n))
    return h, lo, c


def test_fair_value_gap_opens_on_a_three_bar_gap_and_closes_when_traded_through():
    out = _run([(10, 9, 9.5), (12, 10.2, 11.8), (14, 11, 13.5), (13.6, 9.8, 10.0)])
    assert out['n_fvg'].tolist() == [0, 0, 1, 0]
    assert out['fvg_dist'][2] < 0 and out['fvg_dist'][3] == 0           # gap [10, 11] sat below the close


def test_sweep_bos_order_block_and_choch_on_a_known_sequence():
    out = _run(PATTERN)
    assert out['trend'][:6].tolist() == [0] * 6
    assert out['sweep'][5] == -1 and out['bos'][5] == 0
    assert (out['bos'][6], out['choch'][6], out['trend'][6]) == (1, 0, 1)
    assert out['ob_dist'][6] < 0                                          # demand block (bar 4) below price
    assert (out['bos'][8], out['choch'][8], out['trend'][8]) == (0, -1, -1)
    assert out['ob_dist'][8] > 0                                          # demand voided, supply (bar 6) above


def test_no_lookahead_prefix_invariance():
    h, lo, c = _random_bars(3000)
    full = smc_features(h, lo, c)
    for cut in (7, 500, 1234, 2999):
        part = smc_features(h[:cut], lo[:cut], c[:cut])
        for f in FIELDS:
            assert np.array_equal(part[f], full[f][:cut]), (f, cut)


def test_engine_on_bar_feeds_the_same_state_into_features():
    h, lo, c = _random_bars(400, seed=3)
    ref = smc_features(h, lo, c)
    a = AlphaEngine(tick_size=.01)
    for i in range(400):
        assert a.on_bar(1000.0 + 60 * i, h[i], lo[i], c[i])
    assert not a.on_bar(1000.0, h[0], lo[0], c[0])                        # stale bar: SMC not advanced
    a.on_book(Book('X', ((c[-1] - .01, 1.),), ((c[-1] + .01, 1.),), 1, 1))
    f = a.features()
    got = (f.smc_trend, f.smc_bos, f.smc_choch, f.smc_sweep, f.smc_fvg_dist, f.smc_ob_dist)
    assert got == tuple(ref[k][-1].item() for k in ('trend', 'bos', 'choch', 'sweep', 'fvg_dist', 'ob_dist'))


def test_swarm_is_bit_identical_with_default_weights_and_uses_smc_only_when_weighted():
    f = Features(obi=.9, ofi=1, flow=.9, vpin=.5, vwap_z=-8, momentum=4, rsi=50, atr=5, spread_bps=1, ready=True)
    g = replace(f, smc_trend=-1, smc_fvg_dist=-4.0, smc_bos=-1, smc_choch=-1, smc_sweep=-1, smc_ob_dist=3.0)
    assert GroktopusSwarm().evaluate(f, 100, True, True, True, True) == \
        GroktopusSwarm().evaluate(g, 100, True, True, True, True)
    weighted = GroktopusSwarm(smc_noro=1.0, smc_tidal=1.0)
    _, base_votes = weighted.evaluate(f, 100, True, True, True, True)
    _, smc_votes = weighted.evaluate(g, 100, True, True, True, True)
    conf = lambda votes: {v.agent: v.confidence for v in votes}
    assert conf(smc_votes)['TIDAL'] < conf(base_votes)['TIDAL']           # bearish structure pulls TIDAL down
    assert conf(smc_votes)['NORO'] < conf(base_votes)['NORO']             # gap below pulls NORO down
    with pytest.raises(ValueError):
        GroktopusSwarm(smc_tidal=float('nan'))


def test_invalid_parameters_and_replay_speed():
    with pytest.raises(ValueError):
        SMCState(swing=0)
    h, lo, c = _random_bars(50_000, seed=5)
    t = time.perf_counter()
    out = smc_features(h, lo, c)
    assert time.perf_counter() - t < 5.0
    assert set(np.unique(out['trend'])) <= {-1, 0, 1} and np.abs(out['fvg_dist']).max() <= 5.0
