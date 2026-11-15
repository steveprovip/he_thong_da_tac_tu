"""ODH-1 (research/one_dollar_hour_study.py): grid, funding steps, carry accounting, pairs accounting, statistics,
capital matrix. Synthetic data only; no network."""
import hashlib
import json
import math
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from research import one_dollar_hour_study as odh

ROOT = Path(__file__).resolve().parents[1]
T0 = odh.ms('2025-01-01')
H = 3_600_000


def grid(n: int, t0: int = T0) -> np.ndarray:
    return t0 + np.arange(n, dtype=np.int64) * odh.H4


# ------------------------------------------------------------------------------------------------ freeze
def test_the_plan_is_the_file_that_was_frozen_and_the_code_runs_its_numbers():
    rows = [json.loads(x) for x in (ROOT / 'docs/research_upgrade/trial_log.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    pre = [r for r in rows if r.get('kind') == 'odh1-prereg']
    assert len(pre) == 1
    body = (ROOT / odh.PLAN).read_bytes().replace(b'\r\n', b'\n')
    assert hashlib.sha256(body).hexdigest() == pre[0]['freeze']['sha256_lf']
    cfg = pre[0]['cfg']
    assert {k: list(v) for k, v in odh.PARTS.items()} == cfg['parts']
    assert (odh.KLINE_START, odh.FUND_START) == (cfg['grid']['kline_start'], cfg['grid']['funding_start'])
    t = cfg['tradable']
    assert (odh.MIN_QV_A, odh.MIN_QV_B, odh.MAX_GAP) == (t['min_qv_a'], t['min_qv_b'], t['max_leg_gap'])
    c = cfg['carry']
    assert (odh.K_SLOTS, odh.MIN_HOLD, odh.LIQ_MOVE, odh.LIQ_PENALTY) == (c['k_slots'], c['min_hold_steps'], c['liq_move'], c['liq_penalty_bps'])
    assert list(odh.M1_THETA) == c['m1']['theta'] and list(odh.M2_THETA) == c['m2']['theta']
    assert list(odh.THETA_OUT_FRAC) == c['m1']['theta_out_frac'] == c['m2']['theta_out_frac']
    assert odh.SIDE_COST['M1'] == c['m1']['side_cost'] and odh.SIDE_COST['M2'] == c['m2']['side_cost']
    p = cfg['pairs']
    assert list(odh.PAIR_COINS) == p['coins'] and list(odh.PAIR_BARS) == p['bars'] and list(odh.Z_ENTRY) == p['z_entry']
    assert (odh.Z_EXIT, odh.Z_STOP, odh.TRACKER_COST, odh.K_PAIRS) == (p['z_exit'], p['z_stop'], p['tracker_cost_bps'], p['k_pairs'])
    assert odh.SIDE_COST['M3'] == p['side_cost']
    v = cfg['verdict']
    assert (odh.MIN_TRAIN_ROUNDS, odh.MIN_HOLDOUT_ROUNDS, odh.NW_LAGS, odh.N_TRIALS) == (
        v['min_train_rounds'], v['min_holdout_rounds'], v['nw_lags'], v['n_trials'])
    assert odh.N_TRIALS == len(odh.carry_cells('M1')) + len(odh.carry_cells('M2')) + len(odh.PAIR_BARS) * len(odh.Z_ENTRY)
    k = cfg['capital']
    assert odh.PER_BUDGET == k['per_budget'] and odh.MIN_LEG == k['min_leg'] and odh.TRANSFER_30D == k['transfer_per_30d']
    assert (odh.TARGET_DAY, list(odh.TIERS), odh.PATHS, list(odh.HORIZONS), odh.SEED, odh.BLOCK_DAYS, odh.RUIN_FRAC) == (
        k['target_per_day'], k['tiers'], k['paths'], k['horizons'], k['seed'], k['block_days'], k['ruin_frac'])


def test_the_addendum_is_the_file_that_was_registered_and_the_code_runs_its_numbers():
    rows = [json.loads(x) for x in (ROOT / 'docs/research_upgrade/trial_log.jsonl').read_text(encoding='utf-8').splitlines() if x.strip()]
    add = [r for r in rows if r.get('kind') == 'odh1-addendum']
    assert len(add) == 1
    body = (ROOT / odh.ADDENDUM).read_bytes().replace(b'\r\n', b'\n')
    assert hashlib.sha256(body).hexdigest() == add[0]['freeze']['sha256_lf']
    c = add[0]['cfg']['m3r']
    assert (odh.EG_L, odh.EG_CRIT, list(odh.EG_HL), list(odh.EG_BETA), odh.EG_TIME_MIN, odh.N_TRIALS_A1) == (
        c['window_bars'], c['eg_crit'], c['half_life_bars'], c['beta_range'], c['time_stop_min_bars'], c['n_trials'])
    assert (list(odh.PAIR_BARS), list(odh.Z_ENTRY), odh.Z_EXIT, odh.Z_STOP) == (c['bars'], c['z_entry'], c['z_exit'], c['z_stop'])
    assert odh.HOLE_H == add[0]['cfg']['funding_hole_h']
    pre = [r for r in rows if r.get('kind') == 'odh1-prereg'][0]
    assert add[0]['t'] > pre['t']
    out = ROOT / 'data' / 'one_dollar_hour_study.json'
    assert not out.exists() or add[0]['t'] < out.stat().st_mtime         # registered before the run wrote its output


# ------------------------------------------------------------------------------------------------ data
def test_spot_name_strips_the_multiplier_prefix_only_when_the_plain_spot_exists():
    spot = {'PEPEUSDT', 'MOGUSDT', 'BABYDOGEUSDT', 'BTCUSDT', '1000SATSUSDT'}
    assert odh.spot_name('1000PEPEUSDT', spot) == ('PEPEUSDT', 1e3)
    assert odh.spot_name('1000000MOGUSDT', spot) == ('MOGUSDT', 1e6)
    assert odh.spot_name('1MBABYDOGEUSDT', spot) == ('BABYDOGEUSDT', 1e6)
    assert odh.spot_name('BTCUSDT', spot) == ('BTCUSDT', 1.0)
    assert odh.spot_name('1000SATSUSDT', spot) == ('1000SATSUSDT', 1.0)       # the spot itself carries the prefix
    assert odh.spot_name('ETHUSDT', spot) is None


def test_perp_symbols_keeps_usdt_coin_perpetuals_only():
    info = {'symbols': [
        {'symbol': 'BTCUSDT', 'quoteAsset': 'USDT', 'contractType': 'PERPETUAL', 'underlyingType': 'COIN'},
        {'symbol': 'BTCUSDT_261225', 'quoteAsset': 'USDT', 'contractType': 'CURRENT_QUARTER', 'underlyingType': 'COIN'},
        {'symbol': 'BTCUSDC', 'quoteAsset': 'USDC', 'contractType': 'PERPETUAL', 'underlyingType': 'COIN'},
        {'symbol': 'XAUUSDT', 'quoteAsset': 'USDT', 'contractType': 'PERPETUAL', 'underlyingType': 'COMMODITY'}]}
    assert odh.perp_symbols(info) == ['BTCUSDT']


def test_on_grid_places_aligned_bars_and_leaves_the_rest_nan():
    t = grid(5)
    rows = [[t[0], 1.0], [t[2] + 400, 3.0], [t[3] + 2 * H, 9.0], [t[4] + odh.H4, 7.0]]
    out = odh.on_grid(rows, t, 1)
    assert out[0] == 1.0 and out[2] == 3.0
    assert np.isnan(out[[1, 3, 4]]).all()


def test_funding_steps_books_a_print_on_the_step_that_ends_at_or_after_it():
    t = grid(10)
    prints = [[t[1], 5.0], [t[3] + 200, 7.0], [t[4] + 2 * H, 3.0]]
    f = odh.funding_steps(prints, t)
    assert np.isnan(f[0]) and f[1] == 5.0 and f[2] == 0.0 and f[3] == 7.0 and f[4] == 0.0 and f[5] == 3.0
    assert np.isnan(f[6:]).all()                                         # after the last print's step: UNKNOWN


def test_funding_steps_marks_a_hole_in_the_record_unknown():
    t = grid(10)
    f = odh.funding_steps([[t[0], 1.0], [t[9], 2.0]], t)                 # 36 h without a print
    assert f[0] == 1.0 and (f[1:5] == 0.0).all()                          # up to 16 h after the print: no print is zero
    assert np.isnan(f[5:9]).all() and f[9] == 2.0
    g = odh.funding_steps([[t[k], 1.0] for k in range(0, 10, 2)], t)     # 8 h prints: no hole up to the last print
    assert np.isfinite(g[:9]).all() and g[:9].sum() == 5.0 and np.isnan(g[9])


def test_trailing_sum_uses_the_values_before_each_index():
    out = odh.trailing_sum(np.array([1.0, 2.0, 3.0, 4.0, 5.0]), 2)
    assert np.isnan(out[:2]).all() and list(out[2:]) == [3.0, 5.0, 7.0]
    two = odh.trailing_sum(np.array([[1.0, 1.0, 1.0], [np.nan, 1.0, 1.0]]), 1)
    assert list(two[0, 1:]) == [1.0, 1.0] and np.isnan(two[1, 1]) and two[1, 2] == 1.0
    late = np.r_[np.full(5, np.nan), np.ones(8), np.nan, np.ones(7)]   # listed late, one missing bar later on
    s = odh.trailing_sum(late, 6)
    assert np.isnan(s[:11]).all() and s[11] == 6.0 and s[13] == 6.0
    assert np.isnan(s[14:20]).all() and s[20] == 6.0                     # the hole poisons only its own windows


def test_bn_klines_pages_forward_from_the_last_bar():
    t = grid(5)
    calls = []

    def get(url):
        calls.append(url)
        start = int(url.split('startTime=')[1].split('&')[0])
        rows = [[int(x), 1, 2, 0.5, 1.5, 10, 0, 100] for x in t if x >= start][:2]
        return rows

    out = odh.bn_klines('B', '/k', 'XUSDT', int(t[0]), int(t[-1]), 2, get=get)
    assert [r[0] for r in out] == list(t)
    assert out[0][1:] == [1.0, 2.0, 0.5, 1.5, 100.0]                    # open, high, low, close, quote volume
    assert f'startTime={t[2]}' in calls[1]


def test_bybit_paged_walks_backwards_and_returns_oldest_first():
    t = grid(5)
    calls = []

    def get(url):
        calls.append(url)
        hi = int(url.split('endTime=')[1].split('&')[0])
        rows = [{'fundingRateTimestamp': str(x), 'fundingRate': '0.0001'} for x in sorted(t, reverse=True) if x <= hi][:2]
        return {'result': {'list': rows}}

    row = lambda r: [int(r['fundingRateTimestamp']), float(r['fundingRate']) * 1e4]
    out = odh.bybit_paged('/v5/market/funding/history?category=linear', 'XUSDT', int(t[0]), int(t[-1]), 2,
                          ('startTime', 'endTime'), row, get=get)
    assert [r[0] for r in out] == list(t) and all(abs(r[1] - 1.0) < 1e-12 for r in out)
    assert f'endTime={t[3] - 1}' in calls[1] and len(calls) == 3
    assert odh.bybit_funding('XUSDT', int(t[0]), int(t[-1]), get=get)[-1][0] == t[-1]


# ------------------------------------------------------------------------------------------------ carry engine
def market(n_t: int, n_s: int = 1, price: float = 100.0) -> dict:
    """Flat prices, ample volume, zero funding on every leg."""
    full = lambda v: np.full((n_s, n_t), v, dtype=np.float64)
    m = {'sym': np.array([f'S{i}USDT' for i in range(n_s)]), 't': grid(n_t), 'mult': np.ones(n_s)}
    for k in ('pa', 'ha', 'la', 'pb', 'pc', 'hc', 'lc'):
        m[k] = full(price)
    m['qa'], m['qb'], m['qc'] = full(1e7), full(1e6), full(1e6)
    m['fa'], m['fc'] = full(0.0), full(0.0)
    return m


def eight_hour_prints(n_t: int, bps: float) -> np.ndarray:
    f = np.zeros(n_t)
    f[::2] = bps
    return f


def test_m1_receives_the_perp_funding_and_pays_two_sides_of_cost():
    n_t = 16
    m = market(n_t)
    m['fa'][0] = eight_hour_prints(n_t, 10.0)
    x = odh.carry_inputs(m, 'M1')
    assert not x['ok'][0, :6].any() and x['ok'][0, 6]                    # 24 h of volume first
    r = odh.run_carry(x, 5.0, 0.0, 15.0)
    (rd,) = r['rounds']
    assert rd['d'] == 1 and rd['why'] == 'END' and rd['t_in'] == int(m['t'][6])
    assert rd['funding'] == pytest.approx(m['fa'][0, 7:].sum()) and rd['price'] == 0.0
    assert rd['cost'] == 30.0 and rd['net'] == pytest.approx(rd['funding'] - 30.0)
    assert r['step'][6] == pytest.approx((m['fa'][0, 7] - 15.0) / odh.K_SLOTS)
    assert r['step'].sum() == pytest.approx((rd['funding'] - 15.0) / odh.K_SLOTS)   # the END exit falls on no step


def test_m1_short_perp_loses_when_the_perp_rises_against_a_flat_spot():
    n_t = 12
    m = market(n_t)
    m['fa'][0] = eight_hour_prints(n_t, 10.0)
    m['pa'][0, 8:] = 101.0
    r = odh.run_carry(odh.carry_inputs(m, 'M1'), 5.0, 0.0, 0.0)
    assert r['rounds'][0]['price'] == pytest.approx(-100.0)
    assert r['step'][7] == pytest.approx((m['fa'][0, 8] - 100.0) / odh.K_SLOTS)


def test_m1_exits_on_the_signal_but_not_before_the_minimum_hold():
    n_t = 20
    m = market(n_t)
    f = eight_hour_prints(n_t, 10.0)
    f[7:] = 0.0                                                          # last print at step 6
    m['fa'][0] = f
    r = odh.run_carry(odh.carry_inputs(m, 'M1'), 5.0, 2.5, 15.0)
    (rd,) = r['rounds']
    # entry at step 6 (S = 10); S(7) = 10, S(8) = 0 < 2.5 and 8 - 6 >= MIN_HOLD -> exit at step 8
    assert rd['why'] == 'SIGNAL' and rd['t_out'] == int(m['t'][8]) and rd['steps'] == 2
    assert r['step'][8] == pytest.approx(-15.0 / odh.K_SLOTS)


def test_m2_negative_spread_is_long_binance_short_bybit():
    n_t = 12
    m = market(n_t)
    m['fc'][0] = eight_hour_prints(n_t, 10.0)
    m['pa'][0, 9:] = 102.0                                               # long Binance gains
    r = odh.run_carry(odh.carry_inputs(m, 'M2'), 3.0, 0.0, 10.0)
    rd = r['rounds'][0]
    assert rd['d'] == -1
    assert rd['funding'] == pytest.approx(m['fc'][0, 7:].sum()) and rd['price'] == pytest.approx(200.0)


def test_a_perp_leg_that_moves_45_percent_against_the_position_is_liquidated():
    n_t = 14
    m = market(n_t)
    m['fa'][0] = eight_hour_prints(n_t, 10.0)
    m['ha'][0, 8] = 146.0                                                # short Binance leg: high 46 % above the step's price
    r = odh.run_carry(odh.carry_inputs(m, 'M2'), 3.0, 0.0, 10.0)
    rd = r['rounds'][0]
    assert rd['why'] == 'LIQUIDATION' and rd['penalty'] == odh.LIQ_PENALTY and rd['t_out'] == int(m['t'][9])
    assert r['step'][8] == pytest.approx((m['fa'][0, 9] - odh.LIQ_PENALTY - 10.0) / odh.K_SLOTS)
    m1 = market(n_t)                                                     # a spot leg has no liquidation
    m1['fa'][0] = eight_hour_prints(n_t, 10.0)
    m1['pb'][0, 9:] = 60.0
    assert odh.run_carry(odh.carry_inputs(m1, 'M1'), 5.0, 0.0, 10.0)['rounds'][0]['why'] == 'END'


def test_a_leg_whose_data_ends_closes_at_its_last_price_and_blocks_entries():
    n_t = 14
    m = market(n_t)
    m['fa'][0] = eight_hour_prints(n_t, 10.0)
    m['pb'][0, 10:] = np.nan
    x = odh.carry_inputs(m, 'M1')
    assert not x['ok'][0, 9:].any()
    r = odh.run_carry(x, 5.0, 0.0, 15.0)
    (rd,) = r['rounds']
    assert rd['why'] == 'DATA' and rd['t_out'] == int(m['t'][9])
    assert (r['step'][10:] == 0.0).all()


def test_free_slots_take_the_largest_signals_and_one_symbol_holds_one_slot():
    n_t, n_s = 12, 7
    m = market(n_t, n_s)
    for i in range(n_s):
        m['fa'][i] = eight_hour_prints(n_t, 6.0 + i)                     # S = 6 .. 12 bps
    r = odh.run_carry(odh.carry_inputs(m, 'M1'), 5.0, 0.0, 0.0)
    held = sorted(x['sym'] for x in r['rounds'])
    assert held == [f'S{i}USDT' for i in range(2, 7)]
    gap = market(n_t)
    gap['fa'][0] = eight_hour_prints(n_t, 10.0)
    gap['pb'][0] = 120.0                                                 # 20 % apart: not the same coin
    assert not odh.carry_inputs(gap, 'M1')['ok'].any()


def test_daily_sums_the_steps_of_each_utc_day():
    t = grid(12)
    d = odh.daily(t, np.ones(12), '2025-01-01', '2025-01-03')
    assert d == {'2025-01-01': 6.0, '2025-01-02': 6.0, '2025-01-03': 0.0}


# ------------------------------------------------------------------------------------------------ pairs
class ScriptedTracker:
    """Stands in for OnlineKalmanPairTracker: ENTER_LONG_SPREAD on call 2, EXIT_SPREAD on call 4."""
    script = {2: ('ENTER_LONG_SPREAD', 0.5, -0.5), 4: ('EXIT_SPREAD', 0.0, 0.0)}

    def __init__(self, a, b):
        self.calls = 0

    def evaluate_signal(self, pa, pb, **kw):
        self.calls += 1
        act, wa, wb = self.script.get(self.calls, ('HOLD', 0.0, 0.0))
        return SimpleNamespace(action=act, weight_a=wa, weight_b=wb)


def test_run_pair_books_weights_times_returns_minus_funding_and_costs(monkeypatch):
    monkeypatch.setattr(odh, 'OnlineKalmanPairTracker', ScriptedTracker)
    pa = np.array([100.0, 100.0, 110.0, 110.0, 110.0])
    pb = np.full(5, 100.0)
    fa = np.array([0.0, 0.0, 0.0, 10.0, 0.0])                           # paid over the bar that ends at index 3
    r = odh.run_pair(pa, pb, fa, np.zeros(5), 2.5, 4.0)
    assert r['pnl'][1] == pytest.approx(-4.0 + 500.0)                    # entry cost + the move of the bar 1 -> 2
    assert r['pnl'][2] == pytest.approx(-5.0)                            # long A pays 0.5 x 10 bps
    assert r['pnl'][3] == pytest.approx(-4.0)                            # exit cost
    assert list(r['open']) == [False, True, True, False, False]
    (tr,) = r['trades']
    assert tr['gross'] == pytest.approx(495.0) and tr['cost'] == 8.0 and tr['net'] == pytest.approx(487.0)


def test_run_pair_skips_bars_without_both_prices(monkeypatch):
    monkeypatch.setattr(odh, 'OnlineKalmanPairTracker', ScriptedTracker)
    pa = np.array([100.0, 100.0, np.nan, 110.0, 110.0, 110.0])
    r = odh.run_pair(pa, np.full(6, 100.0), np.array([0, 0, 3.0, 4.0, 0, 0]), np.zeros(6), 2.5, 0.0)
    assert r['pnl'][1] == pytest.approx(1000.0 * 0.5 - 0.5 * 7.0)        # 1 -> 3 in one step, both funding prints
    assert r['open'][1] and not r['open'][2]


def test_run_pairs_divides_by_the_larger_of_the_budget_and_the_pairs_held(monkeypatch):
    monkeypatch.setattr(odh, 'OnlineKalmanPairTracker', ScriptedTracker)
    t = grid(5)
    prices = {'A': np.array([100.0, 100.0, 110.0, 110.0, 110.0]), 'B': np.full(5, 100.0), 'C': np.full(5, 100.0)}
    fund = {c: np.zeros(5) for c in prices}
    wide = odh.run_pairs(prices, fund, t, 1, 2.5, 0.0, k_pairs=10)
    tight = odh.run_pairs(prices, fund, t, 1, 2.5, 0.0, k_pairs=2)
    # pairs A/B and A/C gain 500 bps each on bar 1 -> 2, B/C nothing; three pairs are held
    assert wide['step'][1] == pytest.approx(1000.0 / 10) and tight['step'][1] == pytest.approx(1000.0 / 3)
    assert len(wide['rounds']) == 3 and wide['max_open'] == 3


def test_daily_pair_bars_start_at_midnight_and_carry_the_day_s_funding(monkeypatch):
    monkeypatch.setattr(odh, 'OnlineKalmanPairTracker', ScriptedTracker)
    t = grid(6 * 5)
    prices = {'A': np.full(30, 100.0), 'B': np.full(30, 100.0)}
    fa = np.zeros(30)
    fa[8] = 10.0                                                         # print in (t[7], t[8]]: inside day 1 (t[6]..t[12])
    r = odh.run_pairs(prices, {'A': fa, 'B': np.zeros(30)}, t, 6, 2.5, 0.0, k_pairs=1)
    assert r['step'][6] == pytest.approx(-5.0)                           # long A (0.5) pays 10 bps over day 1
    assert np.count_nonzero(r['step']) == 1


def synthetic_pair(n: int, level: float, phi: float = 0.9, sd: float = 0.01, seed: int = 3):
    rng = np.random.default_rng(seed)
    a = level * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    s = np.zeros(n)
    for i in range(1, n):
        s[i] = phi * s[i - 1] + rng.normal(0, sd)
    return a, a * np.exp(s)


@pytest.mark.parametrize('level', [1e5, 100.0, 1.0, 0.01])
def test_the_module_tracker_opens_nothing_on_a_mean_reverting_pair_at_bar_resolution(level):
    """Addendum A1, section 1: the reason M3R exists. A spread of 229 bps sd with a 6.6-bar half-life never reaches
    |z| >= 2.1 under the tracker's defaults, at any price level."""
    a, b = synthetic_pair(3000, level)
    r = odh.run_pair(a, b, np.zeros(3000), np.zeros(3000), 2.1, 4.0)
    assert r['trades'] == [] and not r['open'].any()


def test_eg_fit_excludes_the_current_bar_and_recovers_the_hedge():
    rng = np.random.default_rng(5)
    n, L = 400, odh.EG_L
    x = np.cumsum(rng.normal(0, 0.01, n))
    e = np.zeros(n)
    for i in range(1, n):
        e[i] = 0.5 * e[i - 1] + rng.normal(0, 0.002)
    y = 0.3 + 1.7 * x + e
    f = odh.eg_fit(y, x)
    assert np.isnan(f['beta'][:L]).all() and np.isfinite(f['beta'][L:]).all()
    assert abs(np.median(f['beta'][L:]) - 1.7) < 0.1 and np.median(f['eg'][L:]) < -5
    assert 0.5 < np.median(f['hl'][L:]) < 2.0                            # AR 0.5: half-life one bar
    y2 = y.copy()
    y2[300] += 1.0                                                       # a jump AT bar 300 is not in bar 300's window
    g = odh.eg_fit(y2, x)
    assert g['beta'][300] == f['beta'][300] and g['beta'][301] != f['beta'][301]
    walk = np.cumsum(rng.normal(0, 0.01, n))                             # no cointegration: rarely below -3.34
    assert np.mean(odh.eg_fit(walk, x)['eg'][L:] < odh.EG_CRIT) < 0.2
    x3 = x.copy()
    x3[200] = np.nan
    assert np.isnan(odh.eg_fit(y, x3)['beta'][201:201 + L]).all()


def fixed_fit(monkeypatch, hl: float = 5.0):
    monkeypatch.setattr(odh, 'eg_fit', lambda y, x, L=odh.EG_L: {
        'alpha': np.zeros(len(y)), 'beta': np.ones(len(y)), 'sd': np.full(len(y), 0.01), 'eg': np.full(len(y), -5.0),
        'hl': np.full(len(y), hl)})


def zpath(z):
    """Prices whose spread log pA - log pB is z x 1 %, with pB flat."""
    z = np.asarray(z, dtype=np.float64)
    return 100.0 * np.exp(0.01 * z), np.full(z.size, 100.0)


def test_m3r_enters_beyond_the_band_and_exits_near_zero(monkeypatch):
    fixed_fit(monkeypatch)
    pa, pb = zpath([0.0, 0.0, -2.5, -1.0, -0.2, 0.0])
    r = odh.run_pair_eg(pa, pb, np.zeros(6), np.zeros(6), 2.1, 4.0)
    (tr,) = r['trades']
    assert (tr['i_in'], tr['i_out'], tr['why']) == (2, 4, 'EXIT_SPREAD')
    g1 = 0.5 * (pa[3] / pa[2] - 1.0) * 1e4                               # long A half, short B half (flat)
    assert r['pnl'][2] == pytest.approx(-4.0 + g1) and tr['cost'] == pytest.approx(8.0)
    assert list(r['open']) == [False, False, True, True, False, False]
    short = odh.run_pair_eg(*zpath([0.0, 2.6, 1.0, 0.3, 0.2]), np.zeros(5), np.zeros(5), 2.1, 0.0)
    assert short['trades'][0]['i_out'] == 4 and short['trades'][0]['gross'] > 0


def test_m3r_stops_against_the_position_and_never_enters_beyond_the_stop(monkeypatch):
    fixed_fit(monkeypatch)
    r = odh.run_pair_eg(*zpath([0.0, -2.2, -3.0, -3.7, -1.0]), np.zeros(5), np.zeros(5), 2.1, 0.0)
    assert [(x['i_out'], x['why']) for x in r['trades']] == [(3, 'STOP_BREAKDOWN')] and r['trades'][0]['net'] < 0
    assert odh.run_pair_eg(*zpath([0.0, -3.7, -3.0, 0.0]), np.zeros(4), np.zeros(4), 2.1, 0.0)['trades'][0]['i_in'] == 2
    gated = odh.run_pair_eg(*zpath([0.0, -2.5, 0.0]), np.zeros(3), np.zeros(3), 2.6, 0.0)
    assert gated['trades'] == []


def test_m3r_time_stop_and_the_entry_filters(monkeypatch):
    fixed_fit(monkeypatch, hl=1.0)
    monkeypatch.setattr(odh, 'EG_TIME_MIN', 3)
    r = odh.run_pair_eg(*zpath([0.0, -2.5, -2.0, -2.0, -2.0, -2.0, -2.0]), np.zeros(7), np.zeros(7), 2.1, 0.0)
    assert r['trades'][0]['why'] == 'TIME_STOP' and r['trades'][0]['i_out'] == 4      # held 3 bars, as the module
    for key, bad in (('eg', -3.0), ('hl', 61.0), ('beta', 3.6), ('beta', 0.1)):
        fit = {'alpha': np.zeros(4), 'beta': np.ones(4), 'sd': np.full(4, 0.01), 'eg': np.full(4, -5.0), 'hl': np.full(4, 5.0)}
        fit[key] = np.full(4, bad)
        monkeypatch.setattr(odh, 'eg_fit', lambda y, x, L=odh.EG_L, fit=fit: fit)
        assert odh.run_pair_eg(*zpath([0.0, -2.5, -1.0, 0.0]), np.zeros(4), np.zeros(4), 2.1, 0.0)['trades'] == [], key


def test_m3r_trades_a_real_mean_reverting_pair_with_the_real_fit():
    a, b = synthetic_pair(3000, 100.0, phi=0.8, sd=0.01)
    r = odh.run_pair_eg(a, b, np.zeros(3000), np.zeros(3000), 2.1, 4.0)
    assert len(r['trades']) > 20
    assert all(x['cost'] == pytest.approx(8.0) for x in r['trades'])      # |w_a| + |w_b| = 1 per side
    assert np.mean([x['gross'] for x in r['trades']]) > 0                 # a true OU spread pays before costs


# ------------------------------------------------------------------------------------------------ statistics
def test_newey_west_is_never_below_iid_and_grows_with_autocorrelation():
    rng = np.random.default_rng(1)
    iid = rng.normal(0, 1, 2000)
    assert odh.nw_se(iid) >= iid.std(ddof=1) / math.sqrt(iid.size)
    ar = np.zeros(2000)
    for i in range(1, 2000):
        ar[i] = 0.8 * ar[i - 1] + rng.normal()
    assert odh.nw_se(ar) > 1.5 * ar.std(ddof=1) / math.sqrt(ar.size)


def test_day_stats_reads_only_the_partition():
    days = {'2025-12-31': 100.0, '2026-01-01': 1.0, '2026-01-02': 3.0, '2026-10-01': -50.0}
    s = odh.day_stats(days, 'HOLDOUT', n_trials=18)
    assert s['days'] == 2 and s['mean'] == 2.0 and s['apr_pct'] == pytest.approx(7.3)
    assert 0.0 < s['p_one_sided'] < 0.5 and 'dsr' in s


def test_the_verdict_needs_every_check():
    good_h = {'lcb95': 0.5, 'dsr': 0.99}
    assert odh.verdict(good_h, {'mean': 1.0}, 30)['verdict'] == 'EDGE'
    assert odh.verdict(good_h, {'mean': 1.0}, 29)['verdict'] == 'NO EDGE'
    assert odh.verdict({'lcb95': -0.1, 'dsr': 0.99}, {'mean': 1.0}, 50)['verdict'] == 'NO EDGE'
    assert odh.verdict({'lcb95': 0.5, 'dsr': 0.9}, {'mean': 1.0}, 50)['verdict'] == 'NO EDGE'
    assert odh.verdict({'lcb95': 0.5, 'dsr': None}, {'mean': 1.0}, 50)['verdict'] == 'NO EDGE'
    assert odh.verdict(good_h, {'mean': -0.01}, 50)['verdict'] == 'NO EDGE'


def test_choose_takes_the_best_train_mean_among_cells_with_enough_rounds():
    table = {'a': {'days': {'mean': 9.0}, 'rounds': {'n': 29}}, 'b': {'days': {'mean': 2.0}, 'rounds': {'n': 30}},
             'c': {'days': {'mean': 1.0}, 'rounds': {'n': 300}}, 'd': {'days': {'days': 1}, 'rounds': {'n': 99}}}
    assert odh.choose(table) == 'b'
    assert odh.choose({'a': table['a']}) is None


# ------------------------------------------------------------------------------------------------ capital
def test_slots_shrink_at_the_venue_minimum():
    assert odh.slots_fundable('M1', 50) == 5 and odh.slots_fundable('M1', 20) == 2 and odh.slots_fundable('M1', 7) == 0
    assert odh.slots_fundable('M3', 50) == 10 and odh.slots_fundable('M3', 20) == 4
    assert odh.slots_fundable('XVHL', 50) == 5 and odh.slots_fundable('XVHL', 500) == 18


def test_capital_star_is_the_target_over_the_daily_yield():
    flat = np.full(100, 10.0)                                            # 10 bps a day on capital
    cs = odh.capital_star(flat, 'M1')
    assert cs['point'] == pytest.approx(24_000.0) and cs['lcb95'] == pytest.approx(24_000.0)
    assert odh.capital_star(flat, 'M2')['point'] == pytest.approx((24.0 + 2.0 / 30.0) / 1e-3)
    assert odh.capital_star(np.array([1.0, -3.0, 1.0]), 'M1')['point'] is None


def test_capital_mc_constant_yield_and_ruin():
    r = odh.capital_mc(np.full(60, 20.0), 'M1', 24_000.0, 30, paths=50)
    assert r['fundable'] and r['hourly_median'] == pytest.approx(2.0) and r['p_hourly_ge_1'] == 1.0
    assert r['p_loss'] == 0.0 and r['p_ruin'] == 0.0
    crash = np.full(60, 1.0)
    crash[::7] = -6000.0                                                 # every block holds a -60 % day
    assert odh.capital_mc(crash, 'M1', 1000.0, 30, paths=50)['p_ruin'] == 1.0
    assert odh.capital_mc(np.full(60, 20.0), 'M1', 5.0, 30, paths=50) == {'fundable': False, 'slots': 0}


def test_capital_matrix_reads_holdout_and_fresh_only():
    days = {'2025-06-01': 1e6}
    days.update({odh.day_of(odh.ms('2026-01-01') + k * odh.DAY_MS): 15.0 for k in range(316)})
    cm = odh.capital_matrix(days, 'M1', paths=20)
    assert cm['days'] == 316 and cm['c_star']['mean_bps_day'] == pytest.approx(10.0)
    assert set(cm['tiers']) == {str(c) for c in odh.TIERS}


def test_capacity_compares_the_slot_at_c_star_with_the_volume_held():
    out = {'capital': {'c_star': {'point': 75_000.0, 'lcb95': None}}, 'holdout': {'rounds': {'median_qv24': 1e7}}}
    c = odh.capacity(out, 'M1')
    assert c['slot_notional_point'] == pytest.approx(10_000.0) and c['share_of_24h_volume_point'] == pytest.approx(1e-3)
    assert c['slot_notional_lcb95'] is None


# ------------------------------------------------------------------------------------------------ reference lane
def test_reference_reads_the_replay_rounds_and_drops_rounds_past_its_cache(monkeypatch):
    from tools import funding_spread_replay as fsr
    t_ok, t_late = odh.ms('2026-02-01'), fsr.ms(fsr.END) - 5 * odh.DAY_MS
    some = (np.array([0]), np.array([0.0]))
    monkeypatch.setattr(fsr, 'COINS', ('BTC',))
    monkeypatch.setattr(fsr, 'load', lambda coin, refresh: {'bn': some, 'hl': some, 'px_bn': some, 'px_hl': some, 'px_spot': None})
    seen = {}

    def sim(series, kind, coin, h, k, mode):
        seen.update(series=set(series), args=(kind, coin, h, k, mode))
        mk = lambda t: {'t': t / 1000, 'days': 28, 'net_bps': 28.0, 'cost_bps': 12.0, 'funding_bps': 40.0, 'basis_bps': 0.0}
        return [mk(t_ok), mk(t_late)]

    monkeypatch.setattr(fsr, 'simulate', sim)
    ref = odh.reference_xvhl()
    assert seen['series'] == {'bn', 'hl', 'px_bn', 'px_other'} and seen['args'] == ('XV', 'BTC', 28, 2.0, 'HEDGE')
    (rd,) = ref['rounds']
    assert rd['net'] == 28.0 and rd['cost'] == 12.0 and rd['t_out'] - rd['t_in'] == 28 * odh.DAY_MS
    assert ref['days']['2026-02-01'] == pytest.approx(1.0 / 18) and ref['days']['2026-02-28'] == pytest.approx(1.0 / 18)
    assert ref['days']['2026-03-01'] == 0.0


# ------------------------------------------------------------------------------------------------ end to end
def write_fixture(root: Path, funding: Path):
    """Three majors: random-walk prices, spot and Bybit copies that start at FUND_START as the real fetch does, SOL listed
    late (2025-03-01), Binance prints that switch on and off every two days."""
    rng = np.random.default_rng(11)
    t = odh.grid()
    perps = ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']
    first = {'bn_perp': odh.ms(odh.KLINE_START), 'bn_spot': odh.ms(odh.FUND_START), 'by_kl': odh.ms(odh.FUND_START)}
    (root / 'meta.json').parent.mkdir(parents=True, exist_ok=True)
    (root / 'meta.json').write_text(json.dumps({'perps': perps, 'spot': {p: [p, 1.0] for p in perps}, 'bybit': perps}))
    funding.mkdir(parents=True, exist_ok=True)
    common = np.cumsum(rng.normal(0, 0.01, len(t)))
    for j, p in enumerate(perps):
        px = 100.0 * np.exp(common + 0.3 * np.cumsum(rng.normal(0, 0.004, len(t))))
        rows = [[int(x), float(v), float(v) * 1.01, float(v) * 0.99, float(v), 2e7] for x, v in zip(t, px)]
        for kind in ('bn_perp', 'bn_spot', 'by_kl'):
            (root / kind).mkdir(exist_ok=True)
            lo = odh.ms('2025-03-01') if p == 'SOLUSDT' else first[kind]
            (root / kind / f'{p}.json').write_text(json.dumps([r for r in rows if r[0] >= lo]))
        ft = [x for x in range(int(odh.ms(odh.FUND_START)), int(t[-1]), 8 * H)]
        on = [(x // (2 * odh.DAY_MS) + j) % 2 == 0 for x in ft]
        (funding / f'fr_{p}.json').write_text(json.dumps([[x, 12.0 if o else 0.5] for x, o in zip(ft, on)]))
        (root / 'by_fund').mkdir(exist_ok=True)
        (root / 'by_fund' / f'{p}.json').write_text(json.dumps([[x, 1.0] for x in ft]))


def test_the_runner_goes_end_to_end_on_a_fixture(tmp_path):
    src, fund = tmp_path / 'odh', tmp_path / 'fu'
    write_fixture(src, fund)
    res = odh.run(log=lambda *a: None, src=src, funding=fund, workers=1, paths=20, reference=False)
    json.dumps(res, default=float)
    assert res['symbols'] == {'perps': 3, 'with_spot': 3, 'with_bybit': 3}
    assert res['m1_tradable_share'] > 0.5 and res['m2_tradable_share'] > 0.5      # leading NaNs must not poison 24 h volume
    for key in ('m1', 'm2'):
        r = res[key]
        assert {x['sym'] for x in r['rounds_list']} == {'BTCUSDT', 'ETHUSDT', 'SOLUSDT'}
        assert len(r['train']) == 6 and r['cell'] in r['train']
        assert r['verdict'] in ('EDGE', 'NO EDGE') and set(r['sensitivity']) == set(odh.SIDE_COST[key.upper()]) - {'HEDGE'}
        assert r['holdout']['rounds']['n'] > 30 and 'c_star' in r['capital'] and 'capacity' in r
    assert len(res['m3']['train']) == 6 and res['m3']['coins'] == ['BTC', 'ETH', 'SOL']
    assert len(res['m3r']['train']) == 6 and all(k.startswith('eg|bar') for k in res['m3r']['train'])
    assert res['m3r']['addendum'] == odh.ADDENDUM
