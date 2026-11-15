"""MCF-1 (research/micro_capital_frontiers.py): prop-challenge simulator against its closed forms, LVR, DLMM budget,
airdrop / streaming / services funnels, decision-matrix scoring. Synthetic inputs only; no network."""
import json
import math

import numpy as np
import pytest

from research import micro_capital_frontiers as m


def scripted(monkeypatch, increments):
    """Replace the random draw with a fixed sequence of per-step P&L increments (same for every path)."""
    seq = iter(increments)
    monkeypatch.setattr(m, '_draw', lambda rng, n, mu, sd, df: np.full(n, next(seq, 0.0)))


# ------------------------------------------------------------------------------------------------ F1 closed forms
def test_driftless_pass_rates_match_the_closed_forms():
    rng = np.random.default_rng(1)
    static = m.run_account(rng, 20_000, m.Phase(0.08, 0.10), 0.0, 0.01, 2_000, steps=4, df=None)
    assert static['passed'].mean() == pytest.approx(m.pass_prob_static(0.08, 0.10), abs=0.02)     # 10 / 18
    trail = m.run_account(rng, 20_000, m.Phase(0.04, 0.06, trailing=True), 0.0, 0.01, 2_000, steps=4, df=None)
    assert trail['passed'].mean() == pytest.approx(m.pass_prob_trailing(0.04, 0.06), abs=0.02)    # exp(-2/3)
    assert m.pass_prob_static(0.08, 0.10) == pytest.approx(10 / 18)
    assert m.pass_prob_trailing(0.04, 0.06) == pytest.approx(math.exp(-2 / 3))


def test_a_driftless_funded_account_pays_out_at_most_its_loss_buffer():
    rng = np.random.default_rng(2)
    f = m.run_account(rng, 20_000, m.Phase(0.0, 0.10), 0.0, 0.01, 1_500, steps=16, df=None, payout_days=10, split=1.0,
                      stop_at_target=False)
    over = 0.5826 * 0.01 / 4.0                                            # Siegmund: a discrete path overshoots the floor
    assert not f['alive'].any() and f['final'].mean() == pytest.approx(-(0.10 + over), rel=0.01)
    # optional stopping: E[withdrawals] = buffer + overshoot when trading runs to ruin, never more
    assert f['paid'].mean() == pytest.approx(0.10 + over, rel=0.03)
    assert f['hist'].sum(axis=1) == pytest.approx(f['paid'])


def test_the_daily_loss_limit_ends_an_account_inside_one_day(monkeypatch):
    scripted(monkeypatch, [-0.03, -0.025, 0.0, 0.0])
    r = m.run_account(np.random.default_rng(0), 1, m.Phase(0.08, 0.10, daily_loss=0.05), 0.0, 0.0, 1, steps=4)
    assert not r['alive'][0] and not r['passed'][0]                      # -5.5 % on the day, the 10 % floor untouched


def test_a_trailing_floor_stops_rising_at_the_initial_balance(monkeypatch):
    scripted(monkeypatch, [0.07, -0.065])
    locked = m.run_account(np.random.default_rng(0), 1, m.Phase(0.5, 0.06, trailing=True), 0.0, 0.0, 1, steps=2,
                           lock_at_start=True)
    scripted(monkeypatch, [0.07, -0.065])
    free = m.run_account(np.random.default_rng(0), 1, m.Phase(0.5, 0.06, trailing=True), 0.0, 0.0, 1, steps=2,
                         lock_at_start=False)
    assert locked['alive'][0] and not free['alive'][0]                   # +0.5 % above start: floor 0 vs +1 %


def test_payout_dates_pay_the_split_and_reset_the_balance(monkeypatch):
    scripted(monkeypatch, [0.03, 0.03, 0.01, 0.01])
    r = m.run_account(np.random.default_rng(0), 1, m.Phase(0.0, 0.10), 0.0, 0.0, 4, steps=1, payout_days=2, split=0.8,
                      min_payout_frac=0.0, stop_at_target=False)
    assert r['paid'][0] == pytest.approx(0.8 * 0.06 + 0.8 * 0.02) and r['n_paid'][0] == 2 and r['first_pay'][0] == 2
    scripted(monkeypatch, [0.003, 0.0])
    small = m.run_account(np.random.default_rng(0), 1, m.Phase(0.0, 0.10), 0.0, 0.0, 2, steps=1, payout_days=2, split=0.8,
                          min_payout_frac=0.01, stop_at_target=False)
    assert small['paid'][0] == 0.0 and small['final'][0] == pytest.approx(0.003)   # under the minimum: kept, not paid


def test_simulate_program_accounts_for_entry_and_activation_fees():
    prog = m.PROGRAMS['FTUK_FLEX_5K']
    r = m.simulate_program(prog, 0.0, 0.01, n=3_000, seed=5)
    assert r['e_fees_usd'] == pytest.approx(prog.entry_fee + r['p_pass'] * prog.activation_fee)
    assert r['ev_usd'] == pytest.approx(r['e_payout_usd'] - r['e_fees_usd'])
    assert r['p_lose_all'] == pytest.approx(1.0 - r['p_payout'])
    assert r['e_payout_given_funded_usd'] <= r['bound_payout_given_funded_usd'] * 1.05
    assert r['e_payout_90d_usd'] <= r['e_payout_usd'] + 1e-9
    assert r['bound_pass_driftless'] == pytest.approx(math.exp(-4 / 6))


def test_more_edge_never_lowers_the_pass_rate():
    prog = m.PROGRAMS['RF_SILVER_5K']
    rates = [m.simulate_program(prog, s, 0.01, n=3_000, seed=9)['p_pass'] for s in (-1.5, 0.0, 2.0)]
    assert rates[0] < rates[1] < rates[2]


def test_the_directive_bot_needs_an_implausible_sharpe():
    assert m.sharpe_needed(0.005, 0.005) == pytest.approx(math.sqrt(252))  # 0.5 %/day at 0.5 % vol: Sharpe 15.9
    assert m.sharpe_needed(0.005, 0.02) > 3.9


# ------------------------------------------------------------------------------------------------ F2 DLMM
def v3_value(p, pa, pb, L=1.0):
    return L * (2 * math.sqrt(p) - math.sqrt(pa) - p / math.sqrt(pb))


def test_lvr_matches_the_full_range_limit_and_the_v3_curvature():
    s = 0.05
    assert m.lvr_per_day(s) == pytest.approx(s * s / 8)
    assert m.lvr_per_day(s, 1e12) == pytest.approx(s * s / 8, rel=1e-5)
    p, k, h = 100.0, 1.1, 1e-3
    d2 = (v3_value(p + h, p / k, p * k) - 2 * v3_value(p, p / k, p * k) + v3_value(p - h, p / k, p * k)) / h ** 2
    lvr = 0.5 * s * s * p * p * abs(d2) / v3_value(p, p / k, p * k)
    assert m.lvr_per_day(s, k) == pytest.approx(lvr, rel=1e-4)
    assert m.lvr_per_day(s, 1.1) > 10 * m.lvr_per_day(s)                # a +-10 % range carries ~21x the LVR


def test_breakeven_sigma_inverts_the_full_range_lvr():
    for f in (0.001, 0.0048, 0.15):
        assert m.lvr_per_day(m.breakeven_sigma(f)) == pytest.approx(f)
    assert m.breakeven_sigma(-0.1) == 0.0


def test_dlmm_budget_rent_takes_most_of_ten_dollars():
    b = m.dlmm_budget(73.0)
    assert b['rent_locked_usd'] == pytest.approx(0.059 * 73.0)
    assert b['deployable_usd'] == pytest.approx((10 - 4.307 - 0.0292) * (1 - 0.0015), rel=1e-3)
    nb = m.dlmm_budget(73.0, new_bin_array=True)
    assert nb['deployable_usd'] < 0.5 and nb['lost_usd'] > 5.0
    assert m.dlmm_budget(200.0, new_bin_array=True)['deployable_usd'] == 0.0


def test_pool_stats_reads_fee_yield_volatility_and_drawdown():
    flat = {'name': 'A-USDC', 'tvl': 1000.0, 'fees_24h': 10.0, 'volume_24h': 5000.0, 'closes': [1.0] * 31, 'stable_pair': False}
    crash = {'name': 'B-SOL', 'tvl': 1000.0, 'fees_24h': 50.0, 'volume_24h': 9000.0,
             'closes': list(np.geomspace(1.0, 0.05, 31)), 'stable_pair': False}
    short = {'name': 'C-SOL', 'tvl': 1000.0, 'fees_24h': 50.0, 'volume_24h': 1.0, 'closes': [1.0] * 5, 'stable_pair': False}
    st = m.pool_stats([flat, crash, short])
    assert st['n'] == 2                                                    # under 21 closes: UNKNOWN, left out
    rows = {r['name']: r for r in st['rows']}
    assert rows['A-USDC']['net_full'] == pytest.approx(0.01) and rows['A-USDC']['sigma_d'] == 0.0
    assert rows['B-SOL']['drawdown'] == pytest.approx(0.95) and rows['B-SOL']['sigma_d'] == pytest.approx(0.0, abs=1e-12)
    assert st['share_drawdown_over_90pct'] == 0.5 and st['share_vol_tvl_over_5'] == 0.5


# ------------------------------------------------------------------------------------------------ F3 / F4 funnels
def test_airdrop_ev_is_a_product_of_probabilities_minus_gas():
    r = m.airdrop_ev(0.5, 0.3, 0.6, 30.0, programs=4)
    assert r['p_program_pays'] == pytest.approx(0.09) and r['e_value_usd'] == pytest.approx(10.8)
    assert r['ev_usd'] == pytest.approx(0.8) and r['p_lose_all'] == pytest.approx(0.91 ** 4)
    assert r['tx_affordable'] == 3333


def test_stream_revenue_recovers_its_calibration():
    from statistics import NormalDist
    r = m.stream_revenue(hours_per_day=4.0, ccv_median=1.0, p_ccv_ge5=0.08, usd_per_ccv_hour=0.02)
    assert 1.0 - NormalDist().cdf(math.log(5.0) / r['sigma_ln']) == pytest.approx(0.08)
    assert r['mean_ccv'] > 1.0 and r['median_usd_day'] == pytest.approx(0.08)
    assert r['days_to_50usd_median'] == pytest.approx(625.0)


def test_services_funnel_arithmetic():
    r = m.services_funnel(weeks=10, proposals_per_week=10, win_rate=0.05, order_value=40, fee=0.2, hours_per_order=4,
                          hours_per_proposal=0.25)
    assert r['orders'] == pytest.approx(5.0) and r['net_usd'] == pytest.approx(160.0)
    assert r['owner_hours'] == pytest.approx(45.0) and r['usd_per_owner_hour'] == pytest.approx(160 / 45)
    assert r['p_no_order'] == pytest.approx(0.95 ** 100) and r['weeks_to_first_order_mean'] == pytest.approx(2.0)


# ------------------------------------------------------------------------------------------------ decision matrix
def test_scores_are_bounded_and_monotone():
    assert m.score_velocity(1.0) == 100.0 and m.score_velocity(0.001) == 1.0 and m.score_velocity(-5) == 1.0
    assert m.score_velocity(0.01) == pytest.approx(100 / 3)
    assert m.score_ruin(0.0) == 100.0 and m.score_ruin(1.0) == 1.0
    assert m.score_time(None) == 1.0 and m.score_time(0.0) == 100.0 and m.score_time(7) > m.score_time(30) > m.score_time(90)
    assert m.score_friction(0.0, 0) == 100.0 and m.score_friction(40.0, 2) < m.score_friction(40.0, 0)
    assert m.score_scalability(10_000) == 100.0 and m.score_scalability(0) == 1.0 and m.score_scalability(100) == 50.0
    for w in m.WEIGHTS.values():
        assert sum(w.values()) == pytest.approx(1.0) and set(w) == set(m.CRITERIA)


def test_a_dominant_frontier_ranks_first_under_every_weighting():
    best = {'p_lose_all': 0.0, 'days_to_first_dollar': 5.0, 'usd_per_hour': 0.5, 'eng_hours': 5.0, 'gates': 0, 'usd_month_p90': 500.0}
    worse = {'p_lose_all': 0.6, 'days_to_first_dollar': 90.0, 'usd_per_hour': 0.002, 'eng_hours': 60.0, 'gates': 2, 'usd_month_p90': 5.0}
    d = m.decision_matrix({'best': best, 'worse': worse})
    assert all(r[0] == 'best' for r in d['ranks'].values())
    assert d['scores']['best']['ruin'] == 100.0


def test_run_end_to_end_on_a_synthetic_snapshot():
    snap = [{'name': 'SOL-USDC', 'tvl': 1e6, 'fees_24h': 4000.0, 'volume_24h': 2e7, 'sol_usd': 73.0, 'stable_pair': False,
             'closes': list(73.0 * np.exp(np.cumsum(np.r_[0.0, np.random.default_rng(3).normal(0, 0.04, 30)])))},
            {'name': 'MEME-SOL', 'tvl': 5e4, 'fees_24h': 2500.0, 'volume_24h': 4e5, 'stable_pair': False,
             'closes': list(np.geomspace(1.0, 0.02, 31))}]
    res = m.run(snap, n=400, log=lambda *a: None)
    json.dumps(res, default=float)
    assert set(res['matrix']['ranks']) == set(m.WEIGHTS)
    assert len(res['matrix_inputs']) == 6 and res['dlmm']['sol_usd'] == 73.0
    strict = res['matrix_inputs']['F1 prop challenge, strict 10 USD (FTUK Flex 5k)']
    assert strict['p_lose_all'] == 1.0                                    # the 56 USD activation is out of budget
