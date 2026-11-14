"""ATLAS-K1 (research/atlas_density_study.py): frozen plan, causality, bootstrap arithmetic, rules, fills, statistics,
loop discipline, carry reduction, and the depth -> touch converter (tools/rec_to_l2_touch.py)."""
import gzip
import hashlib
import json
import math
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

import autoresearch_agent_swarm as ar
from research import atlas_density_study as k
from tools import rec_to_l2_touch as conv

ROOT = Path(__file__).resolve().parents[1]
MIN = 60_000


def _bars(n=9000, sigma=3e-4, seed=0, drift=0.0, symbol='XUSDT', t0=None):
    """Gap-free synthetic 1 m bars; bar 0 opens at a UTC midnight inside TRAIN."""
    rng = np.random.default_rng(seed)
    r = rng.normal(drift, sigma, n)
    r[0] = 0.0
    close = 100.0 * np.exp(np.cumsum(r))
    t = (k.ms('2025-03-01') if t0 is None else t0) + np.arange(n) * MIN
    vol = np.full(n, 10.0)
    tb = np.clip(5.0 + 4.0 * np.tanh(r / sigma), 0.0, 10.0)                     # taker buys lean with the bar
    return ar.make_bars(symbol, t, close * 1.0004, close * 0.9996, close, vol, tb)


# ======================================================================================================== freeze
def test_the_plan_is_the_file_that_was_frozen_and_the_code_runs_its_numbers():
    plan = (ROOT / k.PLAN).read_bytes().replace(b'\r\n', b'\n')
    rows = [json.loads(x) for x in (ROOT / 'docs/research_upgrade/trial_log.jsonl').read_text(encoding='utf-8').splitlines()
            if x.strip()]
    frozen = [r for r in rows if r.get('kind') == 'atlas-k1-prereg']
    assert len(frozen) == 1 and frozen[0]['freeze']['sha256_lf'] == hashlib.sha256(plan).hexdigest()
    cfg = frozen[0]['cfg']
    assert [[a.name, a.family, a.lookback, a.block, a.signal, a.coef] for a in k.default_population()] == cfg['population']
    assert (k.ROUNDS, k.SEED, k.H, k.N_FUTURES, k.N_CELLS) == (cfg['rounds'], cfg['seed'], cfg['horizon_bars'],
                                                             cfg['futures'], frozen[0]['n_trials'])
    assert k.COSTS == cfg['cost_bps'] and list(k.SYMBOLS) == cfg['symbols']
    assert {p: list(v[:2]) for p, v in k.BOUNDS.items()} == cfg['bounds']
    assert k.PARTS['TRAIN'][0] == cfg['train'][0] and k.PARTS['HOLDOUT'][0] == cfg['holdout'][0] == k.PARTS['TRAIN'][1]
    assert k.PARTS['FRESH'][0] == cfg['fresh'][0] == k.PARTS['HOLDOUT'][1]
    from core.early_stop import COST_ALL_IN_BPS, SPREAD_SLIPPAGE_BPS, TAKER_FEE_BPS
    assert k.COSTS == {'MT': COST_ALL_IN_BPS, 'TT': 2 * (TAKER_FEE_BPS + SPREAD_SLIPPAGE_BPS)}


# ======================================================================================================== data
def test_decision_points_sit_on_quarter_hours_do_not_overlap_and_stay_inside_the_partition():
    b = _bars(n=6000, t0=k.ms('2025-12-28'))                                    # crosses the TRAIN / HOLDOUT boundary
    tr = k.decision_points(b, 'XUSDT', *(k.ms(d) for d in k.PARTS['TRAIN']))
    ho = k.decision_points(b, 'XUSDT', *(k.ms(d) for d in k.PARTS['HOLDOUT']))
    assert tr.idx.size and ho.idx.size
    for p in (tr, ho):
        assert np.all(p.t % (15 * MIN) == 0) and np.all(np.diff(p.t) >= 15 * MIN) and np.all(p.idx >= k.WARMUP)
        assert np.allclose(p.y, b[0].logc[p.idx + k.H] - b[0].logc[p.idx])
    edge = k.ms('2026-01-01')
    assert tr.t.max() + 15 * MIN <= edge                       # the last TRAIN target ends at the boundary at the latest
    assert ho.t.min() == edge                                  # the decision AT the boundary belongs to HOLDOUT
    assert not set(tr.t) & set(ho.t)


def test_a_gap_inside_the_warmup_or_the_horizon_removes_the_point():
    n = 6000
    rng = np.random.default_rng(1)
    close = 100.0 * np.exp(np.cumsum(rng.normal(0, 3e-4, n)))
    t = k.ms('2025-03-01') + np.arange(n) * MIN
    t[4000:] += 3 * MIN                                        # a 3-bar hole (make_bars keeps holes <= 5 bars)
    b = ar.make_bars('XUSDT', t, close, close, close, np.ones(n), np.ones(n) * 0.5)
    assert len(b) == 1
    p = k.decision_points(b, 'XUSDT', *(k.ms(d) for d in k.PARTS['TRAIN']))
    span = b[0].t_ms[p.idx] - b[0].t_ms[p.idx - k.WARMUP]
    assert np.all(span == k.WARMUP * MIN) and np.all(b[0].t_ms[p.idx + k.H] - b[0].t_ms[p.idx] == k.H * MIN)
    assert not np.any((p.idx >= 4000 - k.H) & (p.idx < 4000 + k.WARMUP))


# ======================================================================================================== model
def test_forecast_uses_only_bars_up_to_the_decision():
    b = _bars()[0]
    i = np.array([3000, 4499, 5999])
    u = k.uniforms()
    for cfg in k.default_population():
        q0, d0, s0 = k.forecast(cfg, b, i, u)
        b2 = _bars()[0]
        for name in ('logc', 'close', 'low', 'high'):
            getattr(b2, name)[6000:] += 0.05                   # the future of every point changes
        for name in ('cs_r', 'cs_r2', 'cs_logc', 'cs_imb'):
            getattr(b2, name)[6001:] += 0.05
        q1, d1, s1 = k.forecast(cfg, b2, i, u)
        assert np.array_equal(q0, q1) and np.array_equal(d0, d1) and np.array_equal(s0, s1)


@pytest.mark.parametrize('lookback,block', [(120, 1), (360, 4), (720, 15), (300, 30), (2880, 7)])
def test_vectorised_bootstrap_equals_the_script_recipe_point_by_point(lookback, block):
    b = _bars(seed=3)[0]
    cfg = k.KAgent('T', 'TIDAL', lookback, block, 60, 0.4, 1.3).clipped()
    u = k.uniforms()
    i = np.array([2900, 3333, 8000])
    q, drift, sig = k.forecast(cfg, b, i, u)
    for n, ii in enumerate(i):                                # scripts/autoresearch_agent_swarm.forecast, in bars
        r = np.diff(b.logc[ii - lookback:ii + 1])
        sd = float(np.std(r))
        r = r - r.mean()
        c = np.concatenate(([0.0], np.cumsum(r)))
        m, rem = divmod(k.H, cfg.block)
        starts = (u[:, :m + (rem > 0)] * (lookback - cfg.block + 1)).astype(np.int64)
        sums = (c[starts[:, :m] + cfg.block] - c[starts[:, :m]]).sum(axis=1)
        if rem:
            sums = sums + (c[starts[:, m] + rem] - c[starts[:, m]])
        z = math.tanh((b.logc[ii] - b.logc[ii - 60]) / (sd * math.sqrt(60)))
        want = np.quantile(1.3 * sums + 0.4 * z * sd * math.sqrt(k.H), k.QUANTILES)
        assert np.allclose(q[n], want, rtol=0, atol=1e-12)
        assert math.isclose(drift[n], 0.4 * z * sd * math.sqrt(k.H), rel_tol=1e-9, abs_tol=1e-15)


def test_uniform_table_shares_its_column_prefix_and_signals_are_bounded():
    u = k.uniforms()
    assert u.shape == (k.N_FUTURES, k.H + 1) and np.array_equal(u[:, :3], k.uniforms()[:, :3])
    b = _bars(seed=4)[0]
    i = np.arange(3000, 8000, 15)
    for cfg in k.default_population():
        _, sd = k.window_sd(b, i, cfg.lookback)
        s = k.signal(cfg, b, i, sd)
        assert np.all(np.abs(s) <= 1.0 + 1e-12)
    a = k.KAgent('x', 'NORO', 10, 99, 9999, 9.0, 9.0).clipped()
    assert (a.lookback, a.block, a.signal, a.coef, a.vol_mult) == (60, 30, 60, 1.5, 2.0)
    with pytest.raises(ValueError):
        k.KAgent('x', 'ZEPHR', 100, 1, 10, 0.0).clipped()


def test_a_no_signal_forecaster_is_calibrated_on_a_random_walk_and_wrong_scale_costs_pinball():
    bars = {'XUSDT': _bars(n=60000, seed=5)}
    p = k.Panel(bars, 'TRAIN', k.uniforms())
    q = p.predict(k.B0, 'XUSDT')[0]
    cov = (p.pts['XUSDT'].y[:, None] <= q).mean(axis=0)
    assert np.all(np.abs(cov - k.QUANTILES) < 0.035)
    wide = p.fit(k.KAgent('w', 'TIDAL', 1440, 1, 60, 0.0, 2.0))
    assert wide['obj'] > 1.05 and math.isclose(p.fit(k.B0)['obj'], 1.0, rel_tol=1e-12)
    wrong = p.fit(k.KAgent('m', 'TIDAL', 1440, 1, 60, 1.5, 1.0))               # momentum drift on a random walk
    assert wrong['obj'] > 1.0


# ======================================================================================================== rules
def test_quantile_function_interpolates_between_the_knots():
    q = np.array([[-5.0, -4.0, -1.0, 0.0, 1.0, 4.0, 5.0]])
    assert math.isclose(k.qf(q, 0.15)[0], -4.0 + (0.05 / 0.15) * 3.0) and k.qf(q, 0.5)[0] == 0.0
    assert math.isclose(k.qf(q, 0.85)[0], 1.0 + (0.10 / 0.15) * 3.0) and k.qf(q, 0.95)[0] == 5.0
    with pytest.raises(ValueError):
        k.qf(q, 0.99)


def test_rules_fire_exactly_where_the_plan_says():
    c = 8.5
    base = np.array([-30.0, -20.0, -8.0, 0.0, 8.0, 20.0, 30.0]) * 1e-4
    q = np.vstack([base, base + 30e-4, base - 30e-4, base + 9e-4])
    q[3, 6] += 40e-4                                                           # a fat right tail on row 3
    agree = np.array([1.0, 0.9, 0.5, 0.86])
    mu = np.array([0.0, 5e-4, -1e-3, 2e-4])
    s = k.rule_sides(q, c, agree, mu)
    assert list(s['P85']) == [0.0, 1.0, -1.0, 0.0]            # row 1: Q(0.15) = +14 bps > c; row 3: -7 bps
    assert list(s['D_0.55']) == [0.0, 1.0, -1.0, 1.0] and list(s['D_0.90']) == [0.0, 1.0, -1.0, 0.0]
    assert list(s['EV_1']) == [0.0, 1.0, -1.0, 1.0] and list(s['EV_0.25']) == [0.0, 1.0, -1.0, 1.0]
    assert list(s['TAIL_0.2']) == [0.0, 0.0, 0.0, 1.0] and list(s['TAIL_0.05']) == [0.0, 0.0, 0.0, 1.0]
    assert list(s['AGREE85']) == [0.0, 1.0, 0.0, 1.0] and list(s['AGREE85_EV']) == [0.0, 1.0, 0.0, 1.0]
    assert list(s['STACK_EV_1']) == [0.0, 0.0, -1.0, 0.0] and list(s['STACK_EV_0.25']) == [0.0, 1.0, -1.0, 0.0]
    assert len(s) == 20 and 2 * len(s) == k.N_CELLS


def _pts(y_bps, up, dn, day=None, t=None):
    n = len(y_bps)
    return k.Points('X', np.zeros(n, np.int64), np.arange(n), np.array(y_bps) * 1e-4,
                    np.arange(n) if day is None else np.array(day), np.arange(n) * 15 * MIN if t is None else np.array(t),
                    np.array(up, bool), np.array(dn, bool))


def test_maker_fill_needs_a_trade_through_and_costs_are_charged():
    pts = _pts([20.0, 20.0, -30.0, -30.0, 5.0], up=[1, 0, 1, 1, 1], dn=[1, 1, 1, 0, 1])
    side = np.array([1.0, 1.0, -1.0, -1.0, 0.0])
    rng = np.random.default_rng(0)
    mt = k.trade_stats(side, pts, 'MT', rng)
    assert (mt['decisions'], mt['n']) == (4, 2)                # the buy with no dip and the sell with no pop stay unfilled
    assert math.isclose(mt['mean_net_bps'], (20.0 - 8.5 + 30.0 - 8.5) / 2) and mt['win_rate'] == 1.0
    tt = k.trade_stats(side, pts, 'TT', rng)
    assert tt['n'] == 4 and math.isclose(tt['mean_net_bps'], (20.0 + 20.0 + 30.0 + 30.0) / 4 - 13.0)
    assert math.isclose(tt['gross_mean_bps'], 25.0) and math.isclose(tt['placebo_mean_bps'], -13.0, abs_tol=12.0)
    assert k.trade_stats(np.zeros(5), pts, 'MT', rng) == {'decisions': 0, 'n': 0, 'mean_net_bps': None}


def test_payoff_break_even_and_the_clustered_standard_error():
    pts = _pts([30.0, -10.0, -10.0, 30.0], up=[1] * 4, dn=[1] * 4, day=[0, 0, 1, 1])
    st = k.trade_stats(np.ones(4), pts, 'TT', np.random.default_rng(0))
    assert math.isclose(st['mean_win_bps'], 17.0) and math.isclose(st['mean_loss_bps'], 23.0)
    assert math.isclose(st['breakeven_win_rate'], 23.0 / 40.0) and math.isclose(st['payoff'], 17.0 / 23.0)
    x = np.array([1.0, 1.0, 3.0, 3.0, 5.0, 5.0])
    m, se = k.mean_se(x, np.array([0, 0, 1, 1, 2, 2]))        # three days of two identical observations
    assert m == 3.0 and math.isclose(se, math.sqrt(3 / 2 * (2 * (2 / 3) ** 2)))    # > the i.i.d. SE 0.667
    assert se > x.std(ddof=1) / math.sqrt(x.size)
    assert math.isnan(k.mean_se(np.zeros(0), np.zeros(0))[0]) and math.isnan(k.cluster_se(np.ones(3), np.zeros(3)))


def test_verdict_needs_every_pre_registered_check():
    good = {'n': 400, 'lcb95': 1.0, 'vs_placebo_lcb95': 0.5, 'sr_trade': 0.4, 'skew': 0.0, 'kurt': 3.0, 'mean_net_bps': 3.0}
    fresh = {'n': 40, 'mean_net_bps': 1.0}
    assert k.verdict(good, good, fresh, False)['verdict'] == 'EDGE'
    assert k.verdict(good, good, fresh, True)['verdict'] == 'EDGE'
    assert k.verdict(good, {**good, 'n': 12}, fresh, False)['verdict'] == 'NO OPPORTUNITY SET'
    assert k.verdict(good, {**good, 'lcb95': -0.1}, fresh, False)['verdict'] == 'NO EDGE'
    assert k.verdict(good, {**good, 'vs_placebo_lcb95': -0.1}, fresh, False)['verdict'] == 'NO EDGE'
    assert k.verdict(good, {**good, 'n': 60}, fresh, False)['verdict'] == 'NO EDGE'
    assert k.verdict(good, good, {'n': 5, 'mean_net_bps': None}, False)['verdict'] == 'NO EDGE'
    assert k.verdict(good, good, {'n': 40, 'mean_net_bps': -1.0}, False)['verdict'] == 'NO EDGE'
    weak = {**good, 'sr_trade': 0.05}                          # passes the plain checks, fails the deflation
    assert k.verdict(good, weak, fresh, False)['verdict'] == 'EDGE' and k.verdict(good, weak, fresh, True)['verdict'] == 'NO EDGE'


# ======================================================================================================== loop
def test_loop_keeps_only_train_improvements_and_never_builds_another_partition(monkeypatch):
    bars = {'XUSDT': _bars(n=12000, seed=7), 'YUSDT': _bars(n=12000, seed=8, symbol='YUSDT')}
    built = []
    real = k.Panel.__init__
    monkeypatch.setattr(k.Panel, '__init__', lambda self, b, part, u: (built.append(part), real(self, b, part, u))[1])
    train = k.Panel(bars, 'TRAIN', k.uniforms())
    start = {a.name: a for a in k.default_population()}
    res = k.mutation_loop(train, start, rounds=12, seed=11, log=lambda *a: None)
    assert built == ['TRAIN']
    hist = res['history']
    assert len(hist) == 13 and hist[0]['round'] == 0
    cur = dict(start)
    for row in hist[1:]:
        assert row['kept'] == (row['obj_new'] < row['obj_old'] - 1e-12)
        before = cur[row['agent']]
        if row['kept']:
            cur = {n: k.KAgent(**a) for n, a in row['agents'].items()}
            changed = [f for f in asdict(before) if asdict(before)[f] != asdict(cur[row['agent']])[f]]
            assert changed and set(changed) <= {row['param'], 'block', 'signal'}     # one parameter (+ its clamps)
        assert row['mean_obj'] <= hist[row['round'] - 1]['mean_obj'] + 1e-12         # never worse on TRAIN
    assert cur == res['agents'] and set(res['weights']) == set(start)
    assert all(ar.W_FLOOR <= w <= ar.W_CAP for w in res['weights'].values())
    rep = k.replay_history(train, hist)                        # the reporting replay reproduces the loop's own numbers
    assert [round(r['rel_b0'], 12) for r in rep] == [round(h['ens_train']['rel_b0'], 12) for h in hist]


def test_ensemble_is_the_weighted_quantile_average_and_zero_coef_removes_the_drift():
    bars = {'XUSDT': _bars(n=9000, seed=9)}
    p = k.Panel(bars, 'TRAIN', k.uniforms())
    ags = k.default_population()[:2]
    fits = {a.name: p.fit(a) for a in ags}
    w = {ags[0].name: 2.0, ags[1].name: 1.0}
    e = p.ensemble(fits, w)['XUSDT']
    assert np.allclose(e, (2 * fits[ags[0].name]['q']['XUSDT'] + fits[ags[1].name]['q']['XUSDT']) / 3)
    e0 = p.ensemble(fits, w, zero_coef=True)['XUSDT']
    plain = {a.name: p.fit(k.KAgent(a.name, a.family, a.lookback, a.block, a.signal, 0.0, a.vol_mult)) for a in ags}
    assert np.allclose(e0, p.ensemble(plain, w)['XUSDT'], atol=1e-12)
    assert np.all(np.diff(e, axis=1) >= 0)                     # an average of monotone quantiles stays monotone


def test_a_planted_momentum_is_found_as_location_skill_and_a_random_walk_shows_none():
    def planted(seed, rho):
        rng = np.random.default_rng(seed)
        n = 40000
        e = rng.normal(0, 3e-4, n)
        r = np.empty(n)
        r[0] = 0.0
        for j in range(1, n):
            r[j] = rho * r[j - 1] + e[j]
        close = 100.0 * np.exp(np.cumsum(r))
        t = k.ms('2025-03-01') + np.arange(n) * MIN
        return ar.make_bars('XUSDT', t, close * 1.0005, close * 0.9995, close, np.ones(n), np.ones(n) * 0.5)
    mom = k.KAgent('T', 'TIDAL', 1440, 5, 5, 0.5, 1.0)
    flat = k.KAgent('T', 'TIDAL', 1440, 5, 5, 0.0, 1.0)
    for rho, sign in ((0.6, -1), (0.0, +1)):
        p = k.Panel({'XUSDT': planted(21, rho)}, 'TRAIN', k.uniforms())
        d = p.fit(mom)['loss']['XUSDT'] - p.fit(flat)['loss']['XUSDT']
        m, se = k.mean_se(d, p.pts['XUSDT'].day)
        assert sign * m > 0 and abs(m) > 2 * se


# ======================================================================================================== carry
def test_one_slot_carry_never_overlaps_and_the_gate_refuses_five_usdt(monkeypatch):
    from core.funding_carry import two_leg_capital_gate
    from tools import funding_spread_replay as fsr
    g = two_leg_capital_gate(5.0, None, 'live')
    assert not g.allowed and g.mode == 'RESEARCH_ONLY' and 'TWO_LEG_MIN' in g.reason
    t0 = fsr.ms('2026-02-01') / 1000
    fake = [{'coin': c, 't': t0 + d * 86400, 'days': 28, 'signal_bps': s, 'net_bps': v, 'funding_bps': v, 'basis_bps': 0.0,
             'cost_bps': 15.0} for c, d, s, v in (('A', 0, 40.0, 10.0), ('B', 0, 90.0, 30.0), ('A', 14, 50.0, -5.0),
                                                 ('B', 28, 60.0, 20.0), ('A', 56, 70.0, 6.0))]
    monkeypatch.setattr(fsr, 'COINS', ('A',))
    monkeypatch.setattr(fsr, 'arr', lambda rows: (np.zeros(1, np.int64), np.zeros(1)))
    monkeypatch.setattr(fsr, 'simulate', lambda *a, **kw: fake)
    monkeypatch.setattr(k.json, 'loads', lambda s: [])
    monkeypatch.setattr(Path, 'exists', lambda self: True)
    monkeypatch.setattr(Path, 'read_text', lambda self, *a, **kw: '[]')
    one = k.carry_rounds()['one_slot']
    assert one['net_bps'] == [30.0, 20.0, 6.0] and one['n'] == 3          # the larger signal wins a tie; no overlap
    assert math.isclose(one['time_in_market'], 84 / 273)


def test_overlay_takes_one_position_per_decision_time_and_the_trajectory_adds_the_sleeves():
    pts = _pts([20.0, 40.0, -10.0], up=[0, 1, 1], dn=[1, 1, 1], day=[0, 0, 1], t=[0, 0, 15 * MIN])
    net, day, days = k.overlay_days(np.array([1.0, 1.0, 1.0]), pts, 'MT')
    assert list(net) == [40.0 - 8.5, -10.0 - 8.5] and list(day) == [0, 1] and list(days) == [0, 1]
    tr = k.trajectory([100.0], 1.0, 5.0, np.zeros(0), np.zeros(0, np.int64), np.arange(10), paths=50)
    assert tr['30']['median'] == 5.05 and tr['365']['median'] == round(5.0 + 13 * 0.05, 4)     # 100 bps x 5 U per round
    none = k.trajectory([100.0], 1.0, 0.0, np.zeros(0), np.zeros(0, np.int64), np.arange(10), paths=50)
    assert none['365']['median'] == 5.0 and none['365']['p_plus_10u'] == 0.0
    ov = k.trajectory([], 0.0, 0.0, np.array([-10.0]), np.array([3]), np.arange(10), paths=400)
    assert ov['30']['mean'] < 5.0 and ov['30']['p_ruin'] == 0.0     # one losing trade per 10 days at 5.05 U
    dead = k.trajectory([100.0], 1.0, 5.0, np.array([-2000.0]), np.array([0]), np.arange(1), paths=20)
    assert dead['365']['p_ruin'] == 1.0 and dead['365']['median_days_to_ruin'] == 4.0     # 5 - 4 x 1.01 < 1.01 U
    assert math.isclose(dead['365']['median'], 5.0 - 4 * 1.01) and dead['365']['p05'] >= 0.0   # stopped, never negative


# ======================================================================================================== converter
def _rec(path, rows):
    with gzip.open(path, 'wt', encoding='utf-8') as f:
        for r in rows:
            f.write(json.dumps(r) + '\n')


def test_depth_rebuild_replays_late_diffs_drops_crossed_books_and_stops_at_a_gap(tmp_path):
    ns = 1_794_279_600 * 10 ** 9
    d = lambda u, pu, b=(), a=(): {'r': ns + u * 10 ** 8, 's': 'depth@100ms',                    # noqa: E731
                                   'd': {'U': u, 'u': u, 'pu': pu, 'b': list(b), 'a': list(a)}}
    snap = {'r': ns + 35 * 10 ** 7, 's': 'snapshot',
            'd': {'lastUpdateId': 2, 'bids': [['99', '5'], ['98', '1']], 'asks': [['101', '4'], ['102', '2']]}}
    rows = [d(1, 0, [['97', '9']]), d(2, 1, [['99', '5']]), d(3, 2, [['100', '7']]),            # 3 is newer than the snapshot
            snap, d(4, 3, a=[['101', '0']]), d(5, 4, [['103', '1']]),                           # 5 crosses the book
            d(6, 5, [['103', '0']]), d(8, 7, [['100', '9']]),                                   # 7 is missing: gap
            {'r': ns + 9 * 10 ** 8, 's': 'aggTrade', 'd': {'p': '100', 'q': '1', 'm': False}}]
    (tmp_path / 'rec' / 'XUSDT').mkdir(parents=True)
    _rec(tmp_path / 'rec' / 'XUSDT' / '20261110-07.jsonl.gz', rows)
    (tmp_path / 'rec' / 'XUSDT' / '20261110-08.jsonl.gz').write_bytes(gzip.compress(b'{"r":1,"s":"snapshot"')[:20])  # cut off
    rep = conv.convert(sorted((tmp_path / 'rec' / 'XUSDT').glob('*.gz')), 'XUSDT', tmp_path / 'l2' / 'XUSDT')
    out = [json.loads(x) for x in Path(rep['files'][0]).read_text().splitlines()]
    assert [(float(o['b']), float(o['B']), float(o['a']), float(o['A'])) for o in out] == [
        (100.0, 7.0, 101.0, 4.0),                              # snapshot + the replayed diff 3
        (100.0, 7.0, 102.0, 2.0),                              # diff 4 removes the 101 ask
        (100.0, 7.0, 102.0, 2.0)]                              # diff 6 un-crosses; 5 (crossed) and 8 (gap) wrote nothing
    assert all(o['ch'] == 'bookTicker' and o['src'] == conv.SRC for o in out) and rep['lines'] == 3
    segs = ar.load_segments(rep['files'][0])                  # the ATLAS loader reads the lines as quotes
    assert len(segs) == 1 and math.isclose(math.exp(segs[0].logmid[-1]), 101.0)
    assert math.isclose(segs[0].obi[-1], (7.0 - 2.0) / 9.0)


def test_touch_book_is_unknown_before_the_first_snapshot():
    b = conv.TouchBook()
    b.diff({'u': 5, 'pu': 4, 'b': [['10', '1']], 'a': [['11', '1']]})
    assert b.touch() is None
    b.snapshot({'lastUpdateId': 9, 'bids': [['10', '2']], 'asks': [['12', '3']]})
    assert b.touch() == (10.0, 2.0, 12.0, 3.0)
    b.diff({'u': 7, 'pu': 6, 'b': [['11', '1']], 'a': []})     # older than the snapshot: ignored
    assert b.touch() == (10.0, 2.0, 12.0, 3.0)
