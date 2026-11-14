"""NDG-1 (research/non_directional_grid_study.py): frozen plan, the quoting simulator against the plan text (hand
cases, a scalar reference, martingale / mean-reversion paths), costs, per-window statistics, verdict and gates."""
import hashlib
import json
import math
from pathlib import Path

import numpy as np
import pytest

from research import atlas_density_study as k
from research import non_directional_grid_study as g

ar = k.ar
ROOT = Path(__file__).resolve().parents[1]
MIN = 60_000
D, QLO, QHI = 0.0010, -0.0030, 0.0030                     # quote distance 10 bps, band +-30 bps
QUIET = (-0.0002, 0.0002, 0.0)


def one(bars, d=D, qlo=QLO, qhi=QHI, freeze=True, pad=True):
    """bars: [(low, high, close), ...] in log-return units from P0 -> the simulator's numbers for that one window.
    Padded to 15 bars by repeating the last close with no range (touches nothing new)."""
    bars = list(bars)
    if pad:
        c = bars[-1][2]
        bars += [(c, c, c)] * (k.H - len(bars))
    a = np.array(bars, float)[None]
    sim = g.simulate(a[:, :, 0], a[:, :, 1], a[:, :, 2], np.array([d]), np.array([qlo]), np.array([qhi]), freeze)
    return {key: v[0].item() for key, v in sim.items()}, {s: float(g.net_bps(sim, s)[0]) for s in g.SCHEDULES}


def ref_window(lo, hi, cl, d, qlo, qhi, freeze):
    """Plan section 3 for ONE window, bar by bar (scalar reference for the vectorised simulator)."""
    gross, makers, frz, end, pairs = 0.0, 0, 0, 0, 0
    bid_ok = d > 0 and (not freeze or qlo < -d)
    ask_ok = d > 0 and (not freeze or qhi > d)
    pos, c_prev, over = 0, 0.0, not (bid_ok or ask_ok)
    for low, high, close in zip(lo, hi, cl):
        if over:
            break
        out_lo, out_hi = freeze and low < qlo, freeze and high > qhi
        if pos == 1:
            if out_lo:
                gross += qlo + d; frz += 1; pos = 0; over = True
            elif ask_ok and high > d:
                gross += 2 * d; makers += 1; pairs += 1; pos = 0; over = out_hi
            elif out_hi:
                gross += qhi + d; frz += 1; pos = 0; over = True
        elif pos == -1:
            if out_hi:
                gross += d - qhi; frz += 1; pos = 0; over = True
            elif bid_ok and low < -d:
                gross += 2 * d; makers += 1; pairs += 1; pos = 0; over = out_lo
            elif out_lo:
                gross += d - qlo; frz += 1; pos = 0; over = True
        else:
            fb = bid_ok and c_prev > -d and low < -d
            fa = ask_ok and c_prev < d and high > d
            if fb and fa and out_lo and out_hi:
                gross += min(qlo + d, d - qhi); makers += 1; frz += 1; over = True
            elif fb and out_lo:
                gross += qlo + d; makers += 1; frz += 1; over = True
            elif fa and out_hi:
                gross += d - qhi; makers += 1; frz += 1; over = True
            elif fb and fa:
                gross += 2 * d; makers += 2; pairs += 1
            elif out_lo or out_hi:
                over = True                                 # band exit first: nothing is filled
            elif fb:
                pos = 1; makers += 1
            elif fa:
                pos = -1; makers += 1
        c_prev = close
    if pos == 1:
        gross += cl[-1] + d; end += 1
    elif pos == -1:
        gross += d - cl[-1]; end += 1
    return gross, makers, frz, end, pairs, bid_ok or ask_ok


def fine_paths(n, sd15, seed, sub=60, kappa=0.0, vol=None):
    """n windows x 15 bars (low, high, close) built from `sub` Gaussian steps per bar; kappa > 0 pulls back to P0."""
    rng = np.random.default_rng(seed)
    step = sd15 / math.sqrt(k.H * sub) * (np.ones(n) if vol is None else vol)
    e = rng.normal(0.0, 1.0, (n, k.H * sub)) * step[:, None]
    if kappa:
        x = np.empty_like(e)
        cur = np.zeros(n)
        for j in range(e.shape[1]):
            cur = cur * (1.0 - kappa) + e[:, j]
            x[:, j] = cur
    else:
        x = np.cumsum(e, axis=1)
    x = x.reshape(n, k.H, sub)
    prev = np.concatenate([np.zeros((n, 1)), x[:, :-1, -1]], axis=1)      # a bar's range includes its open
    return np.minimum(x.min(axis=2), prev), np.maximum(x.max(axis=2), prev), x[:, :, -1]


def synth_bars(n=9000, seed=0, t0='2025-03-01', symbol='XUSDT', sigma=3e-4):
    """Gap-free 1 m bars with slow volatility cycles (so the gate has quiet and loud windows)."""
    rng = np.random.default_rng(seed)
    vol = sigma * (1.0 + 0.8 * np.sin(np.arange(n) / 400.0))
    r = rng.normal(0.0, 1.0, n) * vol
    r[0] = 0.0
    close = 100.0 * np.exp(np.cumsum(r))
    wig = np.abs(rng.normal(0.0, 1.0, (2, n))) * vol * 0.5
    openp = np.concatenate([[close[0]], close[:-1]])
    high, low = np.maximum(openp, close) * np.exp(wig[0]), np.minimum(openp, close) * np.exp(-wig[1])
    t = k.ms(t0) + np.arange(n) * MIN
    return ar.make_bars(symbol, t, high, low, close, np.full(n, 10.0), np.full(n, 5.0))


# ======================================================================================================== freeze
def test_the_plan_is_the_file_that_was_frozen_and_the_code_runs_its_numbers():
    plan = (ROOT / g.PLAN).read_bytes().replace(b'\r\n', b'\n')
    rows = [json.loads(x) for x in (ROOT / 'docs/research_upgrade/trial_log.jsonl').read_text(encoding='utf-8').splitlines()
            if x.strip()]
    frozen = [r for r in rows if r.get('kind') == 'ndg1-prereg']
    assert len(frozen) == 1 and frozen[0]['freeze']['sha256_lf'] == hashlib.sha256(plan).hexdigest()
    cfg = frozen[0]['cfg']
    assert (list(g.K_GRID), list(g.FREEZES), [g.GATE_LO, g.GATE_HI]) == (cfg['k'], cfg['freeze'], cfg['gate_pct'])
    assert (k.H, g.SIGMA_DIV, g.MIN_FILL_WINDOWS, g.N_TRIALS) == (cfg['window_bars'], cfg['sigma_div'],
                                                                 cfg['min_fill_windows'], frozen[0]['n_trials'])
    assert {'maker': g.MAKER, 'taker': g.TAKER, 'slip': g.SLIP} == cfg['cost_bps'] and list(k.SYMBOLS) == cfg['symbols']
    assert g.N_TRIALS == len(g.K_GRID) * len(g.FREEZES) * 2
    from core.early_stop import MAKER_FEE_BPS, SPREAD_SLIPPAGE_BPS, TAKER_FEE_BPS
    assert (g.MAKER, g.TAKER, g.SLIP) == (MAKER_FEE_BPS, TAKER_FEE_BPS, SPREAD_SLIPPAGE_BPS)
    assert g.SCHEDULES[g.PRIMARY] == (2.0, 6.5)
    assert k.PARTS['TRAIN'][0] == cfg['train'][0] and k.PARTS['HOLDOUT'][0] == cfg['holdout'][0]
    assert k.PARTS['FRESH'][0] == cfg['fresh'][0]


def test_the_gate_uses_the_final_atlas_ensemble_as_constants():
    src = ROOT / 'data' / 'atlas_density_study.json'
    assert [a.name for a in g.ATLAS_FINAL] == list(g.ATLAS_WEIGHTS) and len(g.ATLAS_FINAL) == 8
    assert all(a == a.clipped() for a in g.ATLAS_FINAL)
    if not src.exists():
        pytest.skip('data/atlas_density_study.json is git-ignored and absent')
    d = json.loads(src.read_text(encoding='utf-8'))
    assert [[a.name, a.family, a.lookback, a.block, a.signal, a.coef, a.vol_mult] for a in g.ATLAS_FINAL] == \
        [[p[c] for c in ('name', 'family', 'lookback', 'block', 'signal', 'coef', 'vol_mult')] for p in d['population_final']]
    assert g.ATLAS_WEIGHTS == d['weights']
    assert g.REF_Z == pytest.approx((d['ref_z'][0], d['ref_z'][-1]), abs=1e-6)


# ======================================================================================================== simulator
def test_a_window_that_touches_no_quote_is_active_and_counts_zero():
    s, net = one([QUIET])
    assert s['active'] and (s['gross'], s['makers'], s['pairs'], s['t_frz'], s['t_end']) == (0.0, 0, 0, 0, 0)
    assert net[g.PRIMARY] == 0.0


def test_bid_fill_then_ask_fill_is_one_pair_worth_two_d_minus_two_maker_fees():
    s, net = one([(-0.0012, 0.0002, -0.0008), (-0.0009, 0.0011, 0.0005)])
    assert (s['makers'], s['pairs'], s['t_frz'], s['t_end']) == (2, 1, 0, 0) and s['gross'] == pytest.approx(2 * D)
    assert net[g.PRIMARY] == pytest.approx(20.0 - 4.0)
    s, _ = one([(-0.0001, 0.0012, 0.0008), (-0.0011, 0.0009, -0.0005)])                      # the short side
    assert (s['makers'], s['pairs']) == (2, 1) and s['gross'] == pytest.approx(2 * D)


def test_both_quotes_in_one_bar_from_flat_is_one_pair_and_quotes_are_posted_again():
    s, _ = one([(-0.0012, 0.0012, 0.0)])
    assert (s['makers'], s['pairs']) == (2, 1) and s['gross'] == pytest.approx(2 * D)
    s, _ = one([(-0.0012, 0.0012, 0.0), (-0.0012, 0.0012, 0.0)])
    assert (s['makers'], s['pairs']) == (4, 2) and s['gross'] == pytest.approx(4 * D)


def test_inventory_left_at_the_window_end_is_closed_as_a_taker_and_no_second_unit_is_added():
    s, net = one([(-0.0012, 0.0, -0.0010), (-0.0020, -0.0009, -0.0015), (-0.0018, -0.0011, -0.0015)])
    assert (s['makers'], s['pairs'], s['t_frz'], s['t_end']) == (1, 0, 0, 1)
    assert s['gross'] == pytest.approx(-0.0015 + D)
    assert net[g.PRIMARY] == pytest.approx(-5.0 - 2.0 - 6.5)
    assert net['all-maker exits (bound)'] == pytest.approx(-5.0 - 4.0) and net['gross'] == pytest.approx(-5.0)


def test_a_bar_that_leaves_the_band_closes_inventory_at_the_edge_and_ends_the_window():
    s, net = one([(-0.0012, 0.0, -0.0010), (-0.0035, -0.0005, -0.0032), (-0.0033, 0.0015, 0.0)])
    assert (s['makers'], s['pairs'], s['t_frz'], s['t_end']) == (1, 0, 1, 0)
    assert s['gross'] == pytest.approx(QLO + D) and net[g.PRIMARY] == pytest.approx(-20.0 - 2.0 - 6.5)
    s, _ = one([(-0.0012, 0.0, -0.0010), (-0.0035, -0.0005, -0.0032), (-0.0033, 0.0015, 0.0)], freeze=False)
    assert (s['makers'], s['pairs'], s['t_frz']) == (2, 1, 0) and s['gross'] == pytest.approx(2 * D)   # NONE: no band


def test_same_bar_ambiguity_is_resolved_against_the_strategy():
    s, _ = one([(-0.0035, 0.0012, 0.0)])                     # flat: both quotes touched and the band broke below
    assert (s['makers'], s['pairs'], s['t_frz']) == (1, 0, 1) and s['gross'] == pytest.approx(QLO + D)
    s, _ = one([(-0.0012, 0.0035, 0.0)])                     # ... and above
    assert (s['makers'], s['pairs'], s['t_frz']) == (1, 0, 1) and s['gross'] == pytest.approx(D - QHI)
    s, _ = one([(-0.0012, 0.0, -0.0010), (-0.0035, 0.0012, 0.0)])     # long: band exit before the favourable fill
    assert (s['makers'], s['pairs'], s['t_frz']) == (1, 0, 1) and s['gross'] == pytest.approx(QLO + D)
    s, _ = one([(-0.0035, 0.0040, 0.0)], qhi=0.0038)         # both edges: the worse of the two orders
    assert (s['makers'], s['t_frz']) == (1, 1) and s['gross'] == pytest.approx(min(QLO + D, D - 0.0038))
    s, _ = one([(-0.0035, 0.0012, 0.0)], freeze=False)
    assert (s['makers'], s['pairs']) == (2, 1)


def test_a_marketable_post_only_quote_is_not_posted_until_the_close_is_back_on_its_passive_side():
    s, _ = one([(-0.0012, 0.0016, 0.0015),                   # pair; closes ABOVE the ask
                (0.0011, 0.0020, 0.0016),                    # ask would be marketable: not posted, no fill
                (0.0005, 0.0017, 0.0008),                    # previous close still above: not posted
                (0.0006, 0.0013, 0.0009)])                   # previous close below the ask: posted and filled
    assert (s['makers'], s['pairs'], s['t_end']) == (3, 1, 1)
    assert s['gross'] == pytest.approx(2 * D + (D - 0.0009))


def test_a_quote_beyond_the_band_edge_is_not_posted_and_its_inventory_leaves_as_a_taker_at_the_edge():
    bars = [(-0.0012, 0.0, -0.0005), (-0.0006, 0.0012, 0.0007)]
    s, net = one(bars, qhi=0.0008)                           # the ask (+10 bps) is outside the band (+8 bps)
    assert (s['makers'], s['pairs'], s['t_frz']) == (1, 0, 1) and s['gross'] == pytest.approx(0.0008 + D)
    assert net[g.PRIMARY] == pytest.approx(18.0 - 2.0 - 6.5)
    s, _ = one([(0.0, 0.0012, 0.0005), (-0.0012, 0.0006, -0.0007)], qlo=-0.0008)             # the mirror image
    assert (s['makers'], s['pairs'], s['t_frz']) == (1, 0, 1) and s['gross'] == pytest.approx(D + 0.0008)
    s, _ = one([QUIET], qlo=-0.0008, qhi=0.0008)             # neither quote fits inside the band: not active
    assert not s['active'] and s['makers'] == 0
    s, _ = one([(-0.0012, 0.0012, 0.0)], d=0.0)              # no predicted volatility: no quote
    assert not s['active'] and s['makers'] == 0


def test_a_band_exit_with_no_inventory_cancels_the_quotes_for_the_rest_of_the_window():
    s, _ = one([(-0.0012, 0.0016, 0.0015),                   # pair; closes above the ask
                (0.0012, 0.0031, 0.0029),                    # flat, ask not posted, band broke above: window over
                (-0.0015, 0.0015, 0.0)])                     # would be a pair
    assert (s['makers'], s['pairs'], s['t_frz'], s['t_end']) == (2, 1, 0, 0) and s['gross'] == pytest.approx(2 * D)
    s, _ = one([(-0.0012, 0.0016, 0.0015),
                (-0.0012, 0.0031, 0.0),                      # only the bid can fill, far edge broke: exit first
                (-0.0015, 0.0015, 0.0)])
    assert (s['makers'], s['pairs'], s['t_frz'], s['t_end']) == (2, 1, 0, 0)


def test_the_vectorised_simulator_equals_the_scalar_reference_on_random_paths():
    rng = np.random.default_rng(7)
    n = 4000
    lo, hi, cl = fine_paths(n, 0.0020, 11, sub=6, vol=rng.uniform(0.3, 2.5, n))
    d = rng.choice([0.0, 0.0004, 0.0008, 0.0016], n, p=[0.02, 0.38, 0.3, 0.3])
    qlo, qhi = -rng.uniform(0.0003, 0.0040, n), rng.uniform(0.0003, 0.0040, n)
    seen = set()
    for freeze in (True, False):
        sim = g.simulate(lo, hi, cl, d, qlo, qhi, freeze)
        for i in range(n):
            gross, makers, frz, end, pairs, active = ref_window(lo[i], hi[i], cl[i], d[i], qlo[i], qhi[i], freeze)
            assert sim['gross'][i] == pytest.approx(gross, abs=1e-12)
            assert (sim['makers'][i], sim['t_frz'][i], sim['t_end'][i], sim['pairs'][i], sim['active'][i]) == \
                (makers, frz, end, pairs, active)
            seen.add((freeze, makers > 0, frz > 0, end > 0, pairs > 1, bool(active)))
        assert np.array_equal(sim['makers'], 2 * sim['pairs'] + sim['t_frz'] + sim['t_end'])   # every unit is closed
        assert not sim['makers'][~sim['active']].any()
    assert len(seen) >= 12                                    # the sample really visits the branches


def test_a_martingale_gives_no_gross_and_pays_the_fees_while_reversion_pays_the_quoter():
    sd15 = 0.0012
    lo, hi, cl = fine_paths(20_000, sd15, 3)
    d, band = np.full(20_000, 0.5 * sd15), np.full(20_000, 1.645 * sd15)
    for freeze in (True, False):
        sim = g.simulate(lo, hi, cl, d, -band, band, freeze)
        assert abs(sim['gross'].mean()) < 0.05 * sd15         # a pair is worth 1.0 sd15: optional stopping
        assert g.net_bps(sim).mean() < -2.0                   # what is left is the fee bill
    lo, hi, cl = fine_paths(20_000, sd15, 4, kappa=0.02)
    sim = g.simulate(lo, hi, cl, np.full(20_000, 0.25 * sd15), -band, band, False)
    assert sim['gross'].mean() > 5 * sim['gross'].std() / math.sqrt(20_000)


# ======================================================================================================== statistics
def test_cell_statistics_are_per_active_window_with_unfilled_windows_counted_as_zero():
    sim = {'gross': np.array([0.0020, 0.0, -0.0005, 0.0, 0.0030]), 'makers': np.array([2, 0, 1, 0, 2]),
           't_frz': np.array([0, 0, 0, 0, 0]), 't_end': np.array([0, 0, 1, 0, 0]), 'pairs': np.array([1, 0, 0, 0, 1]),
           'active': np.array([True, True, True, False, True]), 'd': np.full(5, 0.0010)}
    day = np.array([1, 1, 2, 2, 3])
    mask = np.array([True, True, True, True, False])
    c = g.cell_stats(sim, mask, day, n_days=2)
    net = np.array([20.0 - 4.0, 0.0, -5.0 - 2.0 - 6.5])
    assert (c['windows'], c['regime_windows'], c['fill_windows']) == (3, 4, 2)
    assert c['mean_net_bps'] == pytest.approx(net.mean()) and c['fill_share'] == pytest.approx(2 / 3)
    assert c['se'] == pytest.approx(k.mean_se(net, day[:3])[1]) and c['lcb95'] == pytest.approx(net.mean() - 1.645 * c['se'])
    assert c['mean_net_bps_filled'] == pytest.approx((16.0 - 13.5) / 2)
    assert (c['pairs'], c['maker_fills'], c['freeze_exits'], c['end_exits']) == (1, 3, 0, 1)
    assert c['mean_net_bps_pair_only'] == pytest.approx(16.0) and c['mean_net_bps_forced'] == pytest.approx(-13.5)
    assert c['net_bps_per_day'] == pytest.approx(net.sum() / 2) and c['gross_bps'] == pytest.approx(15.0 / 3)
    assert c['usdt_per_day_at_micro_lot'] == pytest.approx(net.sum() / 2 * 1e-4 * k.MICRO_NOTIONAL)
    assert c['schedules']['gross'] == pytest.approx(5.0) and c['schedules'][g.PRIMARY] == pytest.approx(net.mean())
    assert c['schedules']['hypothetical rebate -0.5 / 5'] == pytest.approx((20.0 + 1.0 + 0.0 - 5.0 + 0.5 - 6.5) / 3)
    empty = g.cell_stats(sim, np.zeros(5, bool), day, 2)
    assert empty['windows'] == 0 and empty['mean_net_bps'] is None


def test_the_verdict_needs_every_pre_registered_check():
    ho = {'windows': 5000, 'fill_windows': 3000, 'lcb95': 0.5, 'sr_window': 0.2, 'skew': 0.0, 'kurt': 3.0}
    fr = {'windows': 500, 'mean_net_bps': 1.0}
    assert g.verdict(ho, fr)['verdict'] == 'EDGE'
    for change in ({'fill_windows': 299}, {'lcb95': -0.01}, {'sr_window': 0.01}):
        v = g.verdict({**ho, **change}, fr)
        assert v['verdict'] == 'NO EDGE' and sum(not x for x in v['checks'].values()) == 1
    assert g.verdict(ho, {'windows': 99, 'mean_net_bps': 1.0})['verdict'] == 'NO EDGE'
    assert g.verdict(ho, {'windows': 500, 'mean_net_bps': -0.1})['verdict'] == 'NO EDGE'
    assert g.verdict(ho, {'windows': 0, 'mean_net_bps': None})['verdict'] == 'NO EDGE'
    assert g.verdict({'windows': 0, 'fill_windows': 0, 'mean_net_bps': None}, fr)['verdict'] == 'NO EDGE'


def test_t1_is_the_train_best_cell_of_the_atlas_gate_only():
    cells = {'ATLAS|k0.25|BAND': {'mean_net_bps': -3.0}, 'ATLAS|k1|NONE': {'mean_net_bps': -1.0},
             'ATLAS|k0.5|BAND': {'mean_net_bps': None}, 'EWMA|k1|NONE': {'mean_net_bps': +9.0}}
    assert g.choose_t1(cells) == 'ATLAS|k1|NONE'


def test_the_variance_ratio_is_one_on_a_random_walk_and_below_one_under_reversion():
    rng = np.random.default_rng(5)
    n = 30_000
    day = np.repeat(np.arange(n // 96 + 1), 96)[:n]
    w = rng.uniform(1.0, 2.0, n)
    thr = {'X': {'w': (1.2, 1.8), 'ewma': (0.0, 0.0)}}
    r = rng.normal(0.0, 3e-4, (n, k.H))
    rw = g.variance_ratio({'X': {'cl': np.cumsum(r, axis=1), 'day': day, 'w': w}}, thr)
    assert set(rw) == {'LOW', 'MID', 'HIGH', 'ALL'} and abs(rw['ALL']['vr15'] - 1.0) < 4 * rw['ALL']['se']
    assert rw['LOW']['share'] == pytest.approx(0.2, abs=0.02) and rw['HIGH']['share'] == pytest.approx(0.2, abs=0.02)
    assert rw['ALL']['sd_1m_bps'] == pytest.approx(3.0, rel=0.02)
    ar1 = r.copy()
    for j in range(1, k.H):
        ar1[:, j] = -0.3 * ar1[:, j - 1] + r[:, j]
    rev = g.variance_ratio({'X': {'cl': np.cumsum(ar1, axis=1), 'day': day, 'w': w}}, thr)
    assert rev['ALL']['vr15'] < 1.0 - 10 * rev['ALL']['se']


# ======================================================================================================== gates
@pytest.fixture(scope='module')
def panel():
    return k.Panel({'XUSDT': synth_bars(seed=1), 'YUSDT': synth_bars(seed=2, symbol='YUSDT')}, 'TRAIN', k.uniforms(k.SEED))


@pytest.fixture(scope='module')
def inputs(panel):
    return g.gate_inputs(panel)


def test_gate_inputs_are_the_ensemble_band_and_the_paths_of_the_next_fifteen_bars(panel, inputs):
    for s, v in inputs.items():
        p, b = panel.pts[s], panel.bars[s][0]
        n = p.idx.size
        assert n > 300 and v['lo'].shape == (n, k.H) and np.all(v['w'] > 0) and np.all(v['qlo'] < v['qhi'])
        assert np.allclose(v['w'], v['qhi'] - v['qlo']) and np.all(v['hi'] >= v['cl']) and np.all(v['lo'] <= v['cl'])
        i = p.idx[17]
        assert np.allclose(v['cl'][17], b.logc[i + 1:i + 1 + k.H] - b.logc[i])
        assert np.allclose(v['lo'][17], np.log(b.low[i + 1:i + 1 + k.H]) - b.logc[i])
        assert np.allclose(v['hi'][17], np.log(b.high[i + 1:i + 1 + k.H]) - b.logc[i])
        assert v['cl'][:, -1] == pytest.approx(p.y)            # the window ends where the ATLAS target ends
        assert np.allclose(v['ewma'], k.ewma_scale(panel.bars[s], p))


def test_the_gate_reads_nothing_after_the_decision_close():
    base = synth_bars(seed=1)
    b = base[0]
    cut = 6000
    f = np.where(np.arange(b.close.size) > cut, 1.5, 1.0)     # every price after bar `cut` is moved
    alt = ar.make_bars('XUSDT', b.t_ms, b.high * f, b.low * f, b.close * f, np.full(b.close.size, 10.0),
                       np.full(b.close.size, 5.0))
    u = k.uniforms(k.SEED)
    v0 = g.gate_inputs(k.Panel({'XUSDT': base}, 'TRAIN', u))['XUSDT']
    p1 = k.Panel({'XUSDT': alt}, 'TRAIN', u)
    v1 = g.gate_inputs(p1)['XUSDT']
    before, after = p1.pts['XUSDT'].idx <= cut, p1.pts['XUSDT'].idx > cut + k.H
    assert before.sum() > 100 and after.sum() > 100
    for key in ('qlo', 'qhi', 'w', 'ewma'):
        assert np.array_equal(v0[key][before], v1[key][before])
    assert not np.allclose(v0['hi'][before], v1['hi'][before])    # the outcome paths did move (those ending past it)


def test_low_and_high_are_the_train_quintiles_per_symbol_and_the_ewma_gate_uses_its_own_scale(inputs):
    thr = g.thresholds(inputs)
    for s, v in inputs.items():
        assert thr[s]['w'] == pytest.approx((np.percentile(v['w'], 20), np.percentile(v['w'], 80)))
        assert thr[s]['ewma'] == pytest.approx((np.percentile(v['ewma'], 20), np.percentile(v['ewma'], 80)))
        sigma, qlo, qhi = g.quote_inputs(v, 'ATLAS')
        assert np.allclose(sigma, v['w'] / 3.29) and qlo is v['qlo'] and qhi is v['qhi']
        sigma, qlo, qhi = g.quote_inputs(v, 'EWMA')
        assert np.allclose(sigma, v['ewma']) and np.allclose(qlo, v['ewma'] * g.REF_Z[0]) and np.allclose(qhi, v['ewma'] * g.REF_Z[1])
    for source in ('ATLAS', 'EWMA'):
        sim, day, masks, sym = g.pooled(inputs, thr, source, 0.5, True)
        n = sum(v['w'].size for v in inputs.values())
        assert sim['gross'].size == day.size == sym.size == n and masks['ALL'].all()
        assert np.array_equal(masks['LOW'] | masks['MID'] | masks['HIGH'], masks['ALL'])
        assert not (masks['LOW'] & masks['HIGH']).any() and not (masks['MID'] & (masks['LOW'] | masks['HIGH'])).any()
        assert masks['LOW'].mean() == pytest.approx(0.2, abs=0.01) and masks['HIGH'].mean() == pytest.approx(0.2, abs=0.01)
        x = np.concatenate([v['w'] if source == 'ATLAS' else v['ewma'] for v in inputs.values()])
        t_lo = np.concatenate([np.full(v['w'].size, thr[s]['w' if source == 'ATLAS' else 'ewma'][0]) for s, v in inputs.items()])
        assert np.array_equal(masks['LOW'], x < t_lo)
        sigma = np.concatenate([g.quote_inputs(v, source)[0] for v in inputs.values()])
        assert np.allclose(sim['d'], 0.5 * sigma)
    reg = g.variance_ratio(inputs, thr)
    assert reg['HIGH']['abs_ret_bps'] > reg['LOW']['abs_ret_bps']          # the gate ranks realised volatility


def test_evaluate_reports_every_cell_ablation_and_symbol(inputs):
    thr = g.thresholds(inputs)
    rep = g.evaluate(inputs, thr, n_days=4)
    assert sorted(rep['cells']) == sorted(f'{src}|k{kk:g}|{fz}' for src in ('ATLAS', 'EWMA') for kk in g.K_GRID
                                          for fz in g.FREEZES) and len(rep['cells']) == g.N_TRIALS
    assert set(rep['ablation']) == set(rep['per_symbol']) == {c for c in rep['cells'] if c.startswith('ATLAS|')}
    for name, cell in rep['ablation'].items():
        assert set(cell) == {'ALL', 'MID', 'HIGH'} and set(rep['per_symbol'][name]) == set(inputs)
        assert sum(c['windows'] for c in rep['per_symbol'][name].values()) == rep['cells'][name]['windows']
    c = rep['cells']['ATLAS|k0.5|BAND']
    assert c['windows'] > 100 and set(c['schedules']) == set(g.SCHEDULES)
    assert c['schedules']['gross'] == pytest.approx(c['gross_bps'])
    assert c['schedules']['gross'] - c['schedules'][g.PRIMARY] == pytest.approx(
        (2.0 * c['maker_fills'] + 6.5 * (c['freeze_exits'] + c['end_exits'])) / c['windows'])
    wide, tight = rep['cells']['ATLAS|k1|NONE'], rep['cells']['ATLAS|k0.25|NONE']
    assert wide['fill_share'] < tight['fill_share'] and wide['quote_distance_bps'] == pytest.approx(4 * tight['quote_distance_bps'])


def test_the_full_run_chooses_on_train_and_reads_holdout_and_fresh_once():
    runs = []
    for n, t0 in enumerate(('2025-06-01', '2026-03-01', '2026-10-20')):          # one short run per partition
        runs += synth_bars(n=5000, seed=20 + n, t0=t0)
    lines = []
    out = g.run(symbols=('XUSDT',), bars={'XUSDT': runs}, log=lambda *a: lines.append(a))
    assert [x[0] for x in lines] == list(k.PARTS) and set(out['partitions']) == set(k.PARTS)
    t1 = out['T1']
    assert t1['cell'] == g.choose_t1(out['partitions']['TRAIN']['cells']) and t1['cell'].startswith('ATLAS|')
    assert all(t1[p] == out['partitions'][p]['cells'][t1['cell']] for p in k.PARTS)
    assert t1['verdict'] == 'NO EDGE' and t1['checks']['fill_windows>=300'] is False     # ~28 LOW windows: no verdict
    assert set(out['thresholds']) == {'XUSDT'} and out['partitions']['HOLDOUT']['windows']['XUSDT'] > 100
    json.dumps(out, default=float)
