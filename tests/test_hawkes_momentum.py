"""HWK-1 (2026-11-15): binned Hawkes volume-shock momentum study and the profit-stack sizer. Synthetic data only."""
from __future__ import annotations

import hashlib
import io
import json
import math
import random
import zipfile
from pathlib import Path

import numpy as np
import pytest

from core.position_sizer import profit_stack_risk
from research import hawkes_momentum_study as g

ROOT = Path(__file__).resolve().parents[1]
DAY = g.DAY_MS


# ------------------------------------------------------------------------------------------------- helpers
def inar(rng, mu, alpha, hl, length, rows=1):
    """Counts of a binned exponential Hawkes process: N_s ~ Poisson(mu + alpha * A_s)."""
    q = math.exp(-g.beta_of(hl))
    n, a = np.zeros((rows, length)), np.zeros(rows)
    for s in range(length):
        if s:
            a = q * (a + n[:, s - 1])
        n[:, s] = rng.poisson(mu + alpha * a)
    return n


def make_mx(days, syms, tags, n=None, seed=1):
    """A matrix of calm pairs: flat price 1.0, 1 000 U per minute, INAR counts (Nbar about 43)."""
    m, rng = len(days), np.random.default_rng(seed)
    mx = {k: np.ones((m, g.L3), dtype=np.float32) for k in 'ohlc'}
    mx['qv'] = np.full((m, g.L3), 1000.0, dtype=np.float32)
    mx['tbq'] = np.full((m, g.L3), 500.0, dtype=np.float32)
    mx['n'] = (inar(rng, 20.0, 0.08, 5, g.L3, m) if n is None else n).astype(np.float32)
    mx.update(sym=np.array(syms), day=np.array(days), tag=np.array(tags), r=np.zeros(m), base=np.ones(m),
              n_bars=np.full(m, g.L3, dtype=np.int32), dropped=np.zeros(2, dtype=np.int64))
    return mx


def shock(mx, p, start, length=30, side=1, step=0.001):
    """Counts x10, volume x20, one-sided taker flow and a 10 bps-per-minute drift on minutes start..start+length-1."""
    j = np.arange(start, start + length)
    mx['n'][p, j] = 400.0
    mx['qv'][p, j] = 20000.0
    mx['tbq'][p, j] = 20000.0 * (0.8 if side > 0 else 0.2)
    c = mx['c'][p].astype(np.float64)
    c[start:] += side * step * np.minimum(np.arange(1, g.L3 - start + 1), length)
    o = np.concatenate(([c[0]], c[:-1]))
    for k, v in (('c', c), ('o', o), ('h', np.maximum(o, c)), ('l', np.minimum(o, c))):
        mx[k][p] = v


def bars(path):
    """o, h, l, c from a list of (open, high, low, close)."""
    a = np.array(path, dtype=np.float64)
    return a[:, 0], a[:, 1], a[:, 2], a[:, 3]


def trades_of(nets, days, entries, exits, v60=1e9, rows=None):
    n = len(nets)
    return {'net': np.array(nets, float), 'day': np.array(days), 't_entry': np.array(entries, dtype=np.int64),
            't_exit': np.array(exits, dtype=np.int64), 'v60': np.full(n, float(v60)),
            'row': np.arange(n) if rows is None else np.array(rows)}


# ------------------------------------------------------------------------------------------------- plan and constants
def test_the_plan_is_the_file_that_was_frozen_and_the_code_runs_its_numbers():
    plan = (ROOT / g.PLAN).read_bytes().replace(b'\r\n', b'\n')
    rows = [json.loads(x) for x in (ROOT / 'docs/research_upgrade/trial_log.jsonl').read_text(encoding='utf-8').splitlines()
            if x.strip()]
    frozen = [r for r in rows if r.get('kind') == 'hwk1-prereg']
    assert len(frozen) == 1 and frozen[0]['freeze']['sha256_lf'] == hashlib.sha256(plan).hexdigest()
    cfg = frozen[0]['cfg']
    assert (g.TOP_K, g.MIN_QV, g.MIN_AGE_DAYS, list(g.EXCLUDE)) == (cfg['universe']['k'], cfg['universe']['min_qv'],
                                                                   cfg['universe']['min_age_days'], cfg['universe']['exclude'])
    assert {k: list(v) for k, v in g.PARTS.items()} == cfg['parts']
    d, p = g.Cfg(), cfg['primary']
    assert (d.hl, d.theta, d.kappa, d.w, d.filt, d.h, d.cooldown, d.arm, d.dist) == (
        p['hl'], p['theta'], p['kappa'], p['w'], p['filt'], p['h'], p['cooldown'], p['arm'], p['dist'])
    assert list(g.F0_REF) == cfg['f0']['ref'] and [list(b) for b in g.F0_BRACKETS] == cfg['f0']['brackets']
    assert list(g.F0_DIR) == cfg['f0']['dir'] and {k: list(v) for k, v in g.GRID.items()} == cfg['grid']
    assert (g.MAKER, g.TAKER, g.SLIP) == (cfg['costs']['maker'], cfg['costs']['taker'], cfg['costs']['slip'])
    assert (g.MIN_TRAIN_N, g.MIN_HOLDOUT_N, g.MIN_FRESH_N, g.ROUNDS) == (
        cfg['min_n']['train'], cfg['min_n']['holdout'], cfg['min_n']['fresh'], cfg['rounds'])
    mc = cfg['mc']
    assert (g.PATHS, g.DAYS, g.SEED, g.EQUITY0, g.BASE_RISK, g.MAX_LEV, g.CAP_SHARE, g.MIN_NOTIONAL, g.RUIN_U) == (
        mc['paths'], mc['days'], mc['seed'], mc['equity0'], mc['base_risk'], mc['max_lev'], mc['cap_share'],
        mc['min_notional'], mc['ruin'])
    cells = g.f0_cells()
    assert len(cells) == frozen[0]['n_trials'] == 12 and len({c.name for c in cells}) == 12
    assert all(c.rr == 2.5 and (c.hl, c.theta, c.kappa, c.h) == (5, 3.0, 3.0, 240) for c in cells)


def test_symbols_keeps_usdt_coin_perpetuals_of_any_status_and_drops_the_exclusions():
    info = {'symbols': [
        {'symbol': 'AAAUSDT', 'quoteAsset': 'USDT', 'contractType': 'PERPETUAL', 'underlyingType': 'COIN', 'status': 'TRADING'},
        {'symbol': 'OLDUSDT', 'quoteAsset': 'USDT', 'contractType': 'PERPETUAL', 'underlyingType': 'COIN', 'status': 'SETTLING'},
        {'symbol': 'BTCUSDT', 'quoteAsset': 'USDT', 'contractType': 'PERPETUAL', 'underlyingType': 'COIN', 'status': 'TRADING'},
        {'symbol': 'AAAUSDC', 'quoteAsset': 'USDC', 'contractType': 'PERPETUAL', 'underlyingType': 'COIN', 'status': 'TRADING'},
        {'symbol': 'TSLAUSDT', 'quoteAsset': 'USDT', 'contractType': 'TRADIFI_PERPETUAL', 'underlyingType': 'EQUITY'},
        {'symbol': 'BTCDOMUSDT', 'quoteAsset': 'USDT', 'contractType': 'PERPETUAL', 'underlyingType': 'INDEX'},
        {'symbol': 'AAAUSDT_260327', 'quoteAsset': 'USDT', 'contractType': 'CURRENT_QUARTER', 'underlyingType': 'COIN'}]}
    assert g.symbols(info) == ['AAAUSDT', 'OLDUSDT']


# ------------------------------------------------------------------------------------------------- universe
def daily_fixture(n_syms=12, n_days=30, start='2025-03-01'):
    """Symbol k gains k % a day on odd days and loses k % on even days; volume 50 M."""
    t0, out = g.ms(start), {}
    for k in range(n_syms):
        px, rows = 100.0, []
        for d in range(n_days):
            px *= 1.0 + (0.01 * (k - 5.5)) * (1 if d % 2 else -1)
            rows.append([t0 + d * DAY, px, px, px, px, 5e7])
        out[f'S{k:02d}USDT'] = rows
    return out


def test_universe_ranks_yesterdays_return_and_never_reads_day_d():
    daily = daily_fixture()
    uni = g.build_universe(daily, '2025-03-20', '2025-03-21')
    d20 = uni['2025-03-20']                                   # D-1 = 03-19 is day index 18 (even): high k lost
    assert [x['sym'] for x in d20 if x['tag'] == 'G'] == [f'S{k:02d}USDT' for k in (4, 3, 2, 1, 0)]
    assert {x['sym'] for x in d20 if x['tag'] == 'L'} == {f'S{k:02d}USDT' for k in (7, 8, 9, 10, 11)}
    assert len(d20) == 10 and len({x['sym'] for x in d20}) == 10
    d21 = uni['2025-03-21']                                   # D-1 = 03-20 (odd): high k gained
    assert {x['sym'] for x in d21 if x['tag'] == 'G'} == {f'S{k:02d}USDT' for k in (7, 8, 9, 10, 11)}
    r = next(x for x in d20 if x['sym'] == 'S11USDT')['r']
    assert r == pytest.approx(-0.055)
    for rows in daily.values():                               # day D itself and everything later is changed
        for row in rows:
            if row[0] >= g.ms('2025-03-20'):
                row[4] *= 3.0
                row[5] = 1.0
    assert g.build_universe(daily, '2025-03-20', '2025-03-20')['2025-03-20'] == d20


def test_universe_filters_volume_age_and_needs_ten_symbols():
    daily = daily_fixture()
    for row in daily['S11USDT']:
        row[5] = 1.9e7                                        # under the 20 M floor
    daily['S10USDT'] = [r for r in daily['S10USDT'] if r[0] >= g.ms('2025-03-10')]   # listed 10 days before D
    uni = g.build_universe(daily, '2025-03-20', '2025-03-20')
    syms = {x['sym'] for x in uni['2025-03-20']}
    assert 'S11USDT' not in syms and 'S10USDT' not in syms and len(syms) == 10
    assert g.build_universe(daily, '2025-03-25', '2025-03-25')['2025-03-25']          # S10 is 15 days old there
    assert 'S10USDT' in {x['sym'] for x in g.build_universe(daily, '2025-03-25', '2025-03-25')['2025-03-25']}
    del daily['S00USDT']
    assert g.build_universe(daily, '2025-03-20', '2025-03-20') == {}                  # 9 eligible symbols: day skipped
    assert g.build_universe({'BTCUSDT': daily['S01USDT']}, '2025-03-20', '2025-03-20') == {}


def test_files_are_the_three_days_of_every_pair_without_duplicates():
    uni = {'2025-03-20': [{'sym': 'AUSDT', 'tag': 'G', 'r': 0.1, 'qv': 1}],
           '2025-03-21': [{'sym': 'AUSDT', 'tag': 'L', 'r': -0.1, 'qv': 1}, {'sym': 'BUSDT', 'tag': 'G', 'r': 0.2, 'qv': 1}]}
    assert g.pairs_of(uni) == [('AUSDT', '2025-03-20', 'G', 0.1), ('AUSDT', '2025-03-21', 'L', -0.1),
                               ('BUSDT', '2025-03-21', 'G', 0.2)]
    assert g.files_of(uni) == [('AUSDT', '2025-03-19'), ('AUSDT', '2025-03-20'), ('AUSDT', '2025-03-21'),
                               ('AUSDT', '2025-03-22'), ('BUSDT', '2025-03-20'), ('BUSDT', '2025-03-21'),
                               ('BUSDT', '2025-03-22')]
    assert g.part_of('2025-12-31') == 'TRAIN' and g.part_of('2026-01-01') == 'HOLDOUT' and g.part_of('2026-11-13') == 'FRESH'
    assert g.part_of('2026-11-14') is None and g.part_of('2024-01-31') is None


# ------------------------------------------------------------------------------------------------- zips
def zip_of(lines, header=False):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as zf:
        zf.writestr('x.csv', ('open_time,open,high,low,close,volume,close_time,quote_volume,count,tbv,tbqv,ignore\n'
                              if header else '') + '\n'.join(lines) + '\n')
    return buf.getvalue()


def test_parse_day_reads_headers_microseconds_and_marks_missing_minutes():
    day, t0 = '2025-03-20', g.ms('2025-03-20')
    line = lambda t, px, n: f'{t},{px},{px + 1},{px - 1},{px + 0.5},10,{t + 59999},{1000 + n},{n},4,{400 + n},0'
    a = g.parse_day(zip_of([line(t0, 10, 7), line(t0 + 2 * 60000, 12, 9)], header=True), day)
    assert a['ok'].sum() == 2 and a['ok'][0] and not a['ok'][1] and a['ok'][2]
    assert (a['o'][0], a['h'][0], a['l'][0], a['c'][0], a['qv'][0], a['n'][0], a['tbq'][0]) == (10, 11, 9, 10.5, 1007, 7, 407)
    assert math.isnan(a['c'][1]) and a['n'][1] == 0 and a['c'][2] == 12.5
    b = g.parse_day(zip_of([line(t0 * 1000 + 5 * 60_000_000, 10, 7)]), day)        # microsecond stamps, no header
    assert b['ok'].sum() == 1 and b['ok'][5] and b['n'][5] == 7
    assert g.parse_day(zip_of([], header=True), day) is None


def test_fill_gaps_carries_the_last_close_and_backfills_the_first_open():
    nan = math.nan
    o, h, l, c = (np.array(x, dtype=float) for x in ([nan, 2, nan, 4], [nan, 3, nan, 5], [nan, 1, nan, 3], [nan, 2.5, nan, 4.5]))
    g.fill_gaps(o, h, l, c)
    assert c.tolist() == [2, 2.5, 2.5, 4.5] and o.tolist() == [2, 2, 2.5, 4] and h.tolist() == [2, 3, 2.5, 5]


# ------------------------------------------------------------------------------------------------- the binned Hawkes
def test_decay_state_is_the_plans_recursion():
    rng = np.random.default_rng(3)
    n = rng.poisson(30, size=(2, 200)).astype(float)
    q, a = math.exp(-math.log(2) / 5), np.zeros((2, 200))
    for s in range(1, 200):
        a[:, s] = q * (a[:, s - 1] + n[:, s - 1])
    assert np.allclose(g.decay_state(n, 5), a) and g.decay_state(n, 5)[:, 0].tolist() == [0.0, 0.0]


def test_conditional_least_squares_recovers_a_simulated_process():
    rng = np.random.default_rng(7)
    n = inar(rng, 5.0, 0.10, 5, g.L_DAY, rows=300)
    mu, alpha, nbar = g.cls_fit(n, g.decay_state(n, 5))
    branching = 0.10 / (math.exp(g.beta_of(5)) - 1.0)                 # 0.673: the mean minute is 3.06 mu
    assert abs(alpha.mean() - 0.10) < 0.006 and abs(mu.mean() - 5.0) < 0.6
    assert abs(nbar.mean() - 5.0 / (1.0 - branching)) < 0.6
    f = g.intensity(np.concatenate([n, n], axis=1), 5)
    assert f['ok'].all() and abs(f['n_hat'].mean() - branching) < 0.04
    assert (f['mu_f'] >= g.MU_FLOOR * f['nbar']).all()


def test_the_literal_three_mu_rule_is_the_normal_state_when_branching_exceeds_two_thirds():
    rng = np.random.default_rng(11)
    n = inar(rng, 5.0, 0.13, 5, 2 * g.L_DAY, rows=50)                 # branching 0.875: mean intensity 8 mu
    f = g.intensity(n, 5)
    lam = f['lam_next'][:, g.L_DAY:]
    assert (lam / f['mu_f'][:, None] >= 3.0).mean() > 0.9             # rho_MU >= 3 almost every minute
    assert (lam / f['nbar'][:, None] >= 3.0).mean() < 0.02            # rho_BAR >= 3 is rare


def test_intensity_uses_d_minus_1_for_the_fit_and_no_later_count_for_a_minute():
    rng = np.random.default_rng(5)
    n = inar(rng, 20.0, 0.08, 5, g.L3, rows=3)
    f0 = g.intensity(n, 5)
    m = n.copy()
    m[:, g.L_DAY:] *= 7.0                                              # day D and later changed
    f1 = g.intensity(m, 5)
    assert np.array_equal(f0['mu'], f1['mu']) and np.array_equal(f0['alpha'], f1['alpha']) and np.array_equal(f0['nbar'], f1['nbar'])
    s = g.L_DAY + 700
    m = n.copy()
    m[:, s + 1:] = 9999.0                                              # counts after minute s
    assert np.allclose(g.intensity(m, 5)['lam_next'][:, :s + 1], f0['lam_next'][:, :s + 1])
    q = math.exp(-g.beta_of(5))
    a = g.decay_state(n[:, :g.D1], 5)
    assert np.allclose(f0['lam_next'][:, s], f0['mu_f'] + f0['alpha'] * q * (a[:, s] + n[:, s]))
    dead = g.intensity(np.zeros((1, g.L3)), 5)
    assert not dead['ok'][0]
    thin = g.intensity(np.full((1, g.L3), 0.5) + rng.random((1, g.L3)) * 0.1, 5)
    assert not thin['ok'][0]                                           # under one trade a minute


def test_volume_ratio_is_the_last_hour_against_the_24_hours_before_it():
    qv = np.full((1, g.D1), 100.0)
    v60, ratio = g.volume_ratio(qv)
    assert np.isnan(ratio[0, :1499]).all() and np.isnan(v60[0, :59]).all()
    assert np.allclose(ratio[0, 1499:], 1.0) and np.allclose(v60[0, 59:], 6000.0)
    s = 2000
    qv[0, s - 9:s + 1] = 1300.0                                        # ten minutes at 13x
    v60, ratio = g.volume_ratio(qv)
    assert v60[0, s] == pytest.approx(50 * 100 + 10 * 1300) and ratio[0, s] == pytest.approx(3.0)
    assert ratio[0, s - 10] == pytest.approx(1.0)                      # nothing of the spike before it starts
    assert ratio[0, s + 60 + 9] < 1.0 + 10 * 12 / 1440 + 1e-9          # an hour later the spike is in the reference only


def test_direction_modes():
    c = np.array([[1.0, 1.0, 1.0, 1.01, 1.02, 1.0, 0.99, 0.99]])
    qv = np.full((1, 8), 100.0)
    tbq = np.array([[50, 50, 50, 80, 20, 20, 20, 80.0]])
    px = g.direction(c, tbq, qv, 2, 'PRICE')
    assert px[0].tolist() == [0, 0, 0, 1, 1, -1, -1, -1]
    cvd = g.direction(c, tbq, qv, 2, 'CVD')                           # sums of (2 tbq - qv) over 2 minutes
    assert cvd[0].tolist() == [0, 0, 0, 1, 0, -1, -1, 0]
    assert g.direction(c, tbq, qv, 2, 'BOTH')[0].tolist() == [0, 0, 0, 1, 0, -1, -1, 0]
    with pytest.raises(ValueError):
        g.direction(c, tbq, qv, 2, 'OFI')


# ------------------------------------------------------------------------------------------------- one trade
def test_bracket_exits_target_stop_same_bar_gap_time_and_end_of_data():
    flat = (100, 100, 100, 100)
    o, h, l, c = bars([flat, (100, 101, 99.5, 100.5), (100.5, 104.5, 100.4, 104.2), flat, flat])
    assert g.sim_trade(o, h, l, c, 1, 1, 400, 160, 3) == (2, 400.0, g.TP)             # 104.5 >= 104
    assert g.sim_trade(o, h, l, c, 1, -1, 400, 160, 3)[1:] == (-160.0, g.STOP)        # a short is stopped at 101.6
    o, h, l, c = bars([flat, (100, 104.5, 98.0, 100)])
    assert g.sim_trade(o, h, l, c, 1, 1, 400, 160, 1) == (1, -160.0, g.STOP)          # both in one bar: the stop
    o, h, l, c = bars([flat, (100, 100.2, 99.9, 100.1), (97.0, 97.5, 96.0, 97.2)])
    j, gross, why = g.sim_trade(o, h, l, c, 1, 1, 400, 160, 5)
    assert (j, why) == (2, g.STOP) and gross == pytest.approx(-300.0)                 # opened through the stop: the open
    o, h, l, c = bars([flat, (100, 100.5, 99.5, 100.3), (100.3, 100.6, 100.0, 100.4), (100.4, 100.9, 100.2, 100.8)])
    assert g.sim_trade(o, h, l, c, 1, 1, 400, 160, 2) == (2, pytest.approx(40.0), g.TIME)
    j, gross, why = g.sim_trade(o, h, l, c, 1, 1, 400, 160, 240)
    assert (j, why) == (3, g.EOD) and gross == pytest.approx(80.0)
    assert g.sim_trade(o, h, l, c, 1, 1, 400, 160, 240, n_bars=3)[::2] == (2, g.EOD)  # D+1 not on disk
    assert g.sim_trade(o, h, l, c, 1, -1, 400, 160, 2) == (2, pytest.approx(-40.0), g.TIME)


def test_ratchet_only_tightens_and_uses_completed_bars():
    flat = (100, 100, 100, 100)
    path = [flat, (100, 102.0, 99.9, 101.9), (101.9, 102.0, 100.9, 101.0), (101.0, 101.2, 100.0, 100.1)]
    o, h, l, c = bars(path)
    # arm 1.0 x 160: bar 1 reaches +200, so from bar 2 the stop is 200 - 0.5 x 160 = +120; bar 2 trades down to +90
    assert g.sim_trade(o, h, l, c, 1, 1, 400, 160, 3, arm=1.0, dist=0.5) == (2, pytest.approx(120.0), g.STOP)
    assert g.sim_trade(o, h, l, c, 1, 1, 400, 160, 3) == (3, pytest.approx(10.0), g.TIME)       # ratchet off
    o, h, l, c = bars([flat, (100, 102.0, 100.5, 100.6), flat])
    # the +200 and the +50 are in the SAME bar: the ratchet is not armed inside it
    assert g.sim_trade(o, h, l, c, 1, 1, 400, 160, 1, arm=1.0, dist=0.5) == (1, pytest.approx(60.0), g.TIME)
    o, h, l, c = bars([flat, (100, 102.0, 99.9, 101.9), (100.5, 100.6, 100.4, 100.5)])
    # next bar opens under the ratcheted stop: filled at the open (+50), not at the level (+120)
    assert g.sim_trade(o, h, l, c, 1, 1, 400, 160, 2, arm=1.0, dist=0.5) == (2, pytest.approx(50.0), g.STOP)
    o, h, l, c = bars([flat, (100, 100.5, 99.9, 100.4), (100.4, 100.5, 98.3, 98.5)])
    assert g.sim_trade(o, h, l, c, 1, 1, 400, 160, 2, arm=1.0, dist=0.5)[1] == pytest.approx(-160.0)   # never looser


def test_costs_and_funding():
    r = np.array([g.STOP, g.TP, g.TIME, g.EOD])
    assert g.cost_bps(r).tolist() == [16.0, 10.0, 16.0, 16.0]
    assert g.cost_bps(r, 'TP_TAKER').tolist() == [16.0] * 4 and g.cost_bps(r, 'ZERO').tolist() == [0.0] * 4
    assert g.cost_bps(r, 'SLIP10').tolist() == [30.0, 17.0, 30.0, 30.0] and g.cost_bps(r, 'SLIP1.5').tolist() == [13.0, 8.5, 13.0, 13.0]
    assert g.Cfg(t=400, rr=2.5).s == 160 and g.Cfg(t=600, rr=4.0).s == 150
    h8 = 8 * 3_600_000
    f = g.Funding(root=ROOT / 'no_such_dir', ext={'AUSDT': [[0, 0.0], [h8, 10.0], [2 * h8, -4.0], [3 * h8, 1.0]]})
    assert f.paid('AUSDT', 1, h8, 2 * h8) == (4.0, True)              # entry AT a print does not pay it; exit at one does
    assert f.paid('AUSDT', -1, h8 - 1, 2 * h8 - 1) == (10.0, True)    # a short receives a positive print
    assert f.paid('AUSDT', 1, h8 + 1, 2 * h8 - 1) == (0.0, True)      # no print inside: known zero
    assert f.paid('AUSDT', 1, h8 - 1, 3 * h8) == (-7.0, True)
    assert f.paid('AUSDT', 1, -1, h8) == (0.0, False)                 # before the cached range: UNKNOWN
    assert f.paid('AUSDT', 1, h8, 3 * h8 + 3_600_001) == (0.0, False)
    assert f.paid('ZUSDT', 1, h8, 2 * h8) == (0.0, False)


# ------------------------------------------------------------------------------------------------- triggers -> trades
def test_a_shock_triggers_once_enters_on_the_next_open_and_respects_the_cooldown():
    mx = make_mx(['2025-03-20'], ['AUSDT'], ['G'])
    a = g.L_DAY + 600
    shock(mx, 0, a)
    shock(mx, 0, a + 260)                                              # a second shock 20 minutes after the first exit
    f = g.Features(mx, [0])
    cfg = g.Cfg(ref='BAR', dir='BOTH')
    m, side = f.mask(cfg)
    first = int(np.flatnonzero(m[0])[0]) + g.D0
    assert a + 5 <= first <= a + 8 and side[0, first - g.D0] == 1     # the hourly volume crosses 3x in the 7th minute
    tr = g.run_trades(f, cfg)
    assert len(tr['s']) == 1 and tr['s'][0] == first and tr['side'][0] == 1
    assert tr['reason'][0] == g.TIME and tr['j_exit'][0] == first + 240
    e = float(mx['o'][0, first + 1])
    assert tr['gross'][0] == pytest.approx((float(mx['c'][0, first + 240]) - e) / e * 1e4, abs=1e-3)
    assert tr['t_entry'][0] == g.ms('2025-03-19') + (first + 1) * 60000 and tr['t_exit'][0] == g.ms('2025-03-19') + (first + 241) * 60000
    assert tr['v60'][0] == pytest.approx(float(f.v60[0, first - g.D0]))
    two = g.run_trades(f, g.Cfg(ref='BAR', dir='BOTH', cooldown=10))
    assert len(two['s']) == 2 and two['s'][1] >= tr['j_exit'][0] + 10
    fade = g.run_trades(f, cfg, 'FADE')                               # the same trigger, short into the drift
    assert fade['s'][0] == first and fade['side'][0] == -1 and fade['reason'][0] == g.STOP
    assert fade['gross'][0] == pytest.approx(-160.0, abs=0.5)
    assert len(g.run_trades(f, g.Cfg(ref='BAR', filt='WITH'))['s']) == 1          # long a gainer
    assert len(g.run_trades(f, g.Cfg(ref='BAR', filt='AGAINST'))['s']) == 0
    assert len(g.run_trades(f, g.Cfg(ref='BAR', theta=8.0))['s']) == 0             # the ratio peaks near 5.4
    assert len(g.run_trades(f, g.Cfg(ref='MU'))['s']) >= 1


def test_the_trigger_reads_nothing_after_the_decision_minute():
    mx = make_mx(['2025-03-20'], ['AUSDT'], ['L'])
    a = g.L_DAY + 600
    shock(mx, 0, a, side=-1)
    cfg = g.Cfg(ref='BAR')
    m0, s0 = g.Features(mx, [0]).mask(cfg)
    cut = a + 12
    for k in ('o', 'h', 'l', 'c'):
        mx[k][0, cut + 1:] *= 1.5
    mx['n'][0, cut + 1:] = 5000.0
    mx['qv'][0, cut + 1:] = 9e6
    mx['tbq'][0, cut + 1:] = 9e6
    m1, s1 = g.Features(mx, [0]).mask(cfg)
    k = cut - g.D0 + 1
    assert m0[0, :k].any() and np.array_equal(m0[0, :k], m1[0, :k]) and np.array_equal(s0[0, :k], s1[0, :k])
    assert s0[0, np.flatnonzero(m0[0])[0]] == -1


def test_one_position_per_symbol_is_carried_into_the_next_days_pair():
    mx = make_mx(['2025-03-20', '2025-03-21', '2025-03-21'], ['AUSDT', 'AUSDT', 'BUSDT'], ['G', 'G', 'G'])
    shock(mx, 0, g.L_DAY + 1400)                                       # 23:20 of day D: the trade runs past midnight
    shock(mx, 1, g.L_DAY + 100)                                        # 01:40 of D+1 on the same symbol: still open
    shock(mx, 1, g.L_DAY + 700)
    shock(mx, 2, g.L_DAY + 100)                                        # another symbol at the same minute: free
    f = g.Features(mx, [0, 1, 2])
    tr = g.run_trades(f, g.Cfg(ref='BAR'))
    by = {(str(s), str(d)): int(k) for s, d, k in zip(tr['sym'], tr['day'], tr['s'])}
    assert len(tr['s']) == 3
    exit_abs = int(tr['t_exit'][0])
    assert g.L_DAY + 1405 <= by[('AUSDT', '2025-03-20')] <= g.L_DAY + 1408
    assert by[('AUSDT', '2025-03-21')] >= g.L_DAY + 700                # the 01:40 shock was skipped
    assert tr['t_entry'][1] >= exit_abs + 60 * 60000
    assert g.L_DAY + 105 <= by[('BUSDT', '2025-03-21')] <= g.L_DAY + 108
    mx['n_bars'][0] = 2 * g.L_DAY                                      # D+1 of the first pair is not on disk
    cut = g.run_trades(g.Features(mx, [0]), g.Cfg(ref='BAR'))
    assert cut['reason'].tolist() == [g.EOD] and cut['j_exit'][0] == 2 * g.L_DAY - 1


def test_random_controls_keep_the_count_and_draw_the_side_or_the_minute():
    mx = make_mx(['2025-03-20'], ['AUSDT'], ['G'])
    shock(mx, 0, g.L_DAY + 600)
    f = g.Features(mx, [0])
    cfg = g.Cfg(ref='BAR')
    sides = {int(g.run_trades(f, cfg, 'RANDOM', seed=k)['side'][0]) for k in range(20)}
    assert sides == {1, -1}
    rt = g.run_trades(f, cfg, 'RTIME', seed=3, counts=np.array([40]))
    assert 1 <= len(rt['s']) <= 40 and ((rt['s'] >= g.D0) & (rt['s'] < g.D1)).all()
    assert len(set(rt['s'].tolist()) - {int(g.run_trades(f, cfg)['s'][0])}) >= 1
    assert len(g.run_trades(f, cfg, 'RTIME', seed=3, counts=np.array([0]))['s']) == 0


def test_price_adds_costs_and_funding_and_summary_reports_the_break_even():
    mx = make_mx(['2025-03-20'], ['AUSDT'], ['G'])
    shock(mx, 0, g.L_DAY + 600)
    tr = g.run_trades(g.Features(mx, [0]), g.Cfg(ref='BAR'))
    t_mid = int(tr['t_entry'][0]) + 60000
    fund = g.Funding(root=ROOT / 'no_such_dir', ext={'AUSDT': [[t_mid - 10 * DAY, 0.0], [t_mid, 25.0], [t_mid + 10 * DAY, 0.0]]})
    p = g.price(tr, fund)
    assert p['funding'].tolist() == [-25.0] and p['funding_known'].tolist() == [True]
    assert p['net'][0] == pytest.approx(tr['gross'][0] - 16.0 - 25.0)
    assert g.price(tr, None)['net'][0] == pytest.approx(tr['gross'][0] - 16.0)
    s = g.summary(p, g.Cfg(ref='BAR'))
    assert s['n'] == 1 and s['breakeven_win'] == pytest.approx((160 + 16) / 560) and s['exits']['TIME'] == 1.0
    assert s['mean_r'] == pytest.approx(p['net'][0] / 160.0) and s['funding_known'] == 1.0
    assert g.summary(g.price(g.run_trades(g.Features(mx, [0]), g.Cfg(ref='BAR', theta=8.0)), fund), g.Cfg()) == {'n': 0}


# ------------------------------------------------------------------------------------------------- statistics, rules
def test_cluster_stats_never_goes_below_the_iid_error_and_widens_for_day_clusters():
    x = np.array([10.0, 10.0, 10.0, -10.0, -10.0, -10.0, 10.0, 10.0, 10.0, -10.0, -10.0, -10.0])
    days = np.repeat(['a', 'b', 'c', 'd'], 3)
    st = g.cluster_stats(x, days, n_trials=12)
    iid = x.std(ddof=1) / math.sqrt(12)
    assert st['n'] == 12 and st['mean'] == 0 and st['days'] == 4 and st['se'] > iid * 1.5
    assert st['lcb95'] == pytest.approx(-1.645 * st['se']) and 0.0 <= st['dsr'] <= 1.0
    alone = g.cluster_stats(x, np.arange(12))
    assert alone['se'] == pytest.approx(iid, rel=0.05) and 'dsr' not in alone
    assert g.cluster_stats([], []) == {'n': 0} and g.deflated([1.0, 1.0, 1.0], 5) is None


def test_verdict_needs_every_check_and_a_thin_fresh_is_unconfirmed():
    good = {'n': 150, 'lcb95': 1.0, 'dsr': 0.97}
    assert g.verdict(good, {'n': 30, 'mean': 2.0})['verdict'] == 'EDGE'
    assert g.verdict(good, {'n': 30, 'mean': -2.0})['verdict'] == 'NO EDGE'
    assert g.verdict(good, {'n': 5, 'mean': 2.0})['verdict'] == 'UNCONFIRMED'
    assert g.verdict({**good, 'lcb95': -0.1}, {'n': 5, 'mean': 2.0})['verdict'] == 'NO EDGE'
    assert g.verdict({**good, 'n': 99}, {'n': 30, 'mean': 2.0})['verdict'] == 'NO EDGE'
    assert g.verdict({**good, 'dsr': None}, {'n': 30, 'mean': 2.0})['verdict'] == 'NO EDGE'
    assert g.verdict({'n': 0}, {'n': 0})['verdict'] == 'NO EDGE'


def test_choose_takes_the_best_mean_r_among_cells_with_enough_train_trades():
    table = {'a': {'n': 199, 'mean_r': 0.9}, 'b': {'n': 200, 'mean_r': -0.05}, 'c': {'n': 900, 'mean_r': -0.20}, 'd': {'n': 0}}
    assert g.choose(table) == 'b' and g.choose({'a': {'n': 10, 'mean_r': 1.0}}) is None


def test_mutate_moves_one_parameter_to_another_grid_value():
    rng, cfg = random.Random(1), g.Cfg()
    for _ in range(300):
        new = g.mutate(cfg, rng)
        diff = [k for k in g.GRID if getattr(new, k) != getattr(cfg, k)]
        assert len(diff) == 1 and getattr(new, diff[0]) in g.GRID[diff[0]]
        assert not (diff[0] == 'dist' and cfg.arm <= 0)               # no draw that cannot change the result
        cfg = new
    assert all(getattr(g.Cfg(), k) in g.GRID[k] for k in g.GRID if k not in ('t', 'rr')) and g.Cfg().t in g.GRID['t']


def test_search_scores_train_only_and_counts_each_configuration_once():
    mx = make_mx(['2025-03-20'], ['AUSDT'], ['G'])
    shock(mx, 0, g.L_DAY + 600)
    out = g.search(g.Features(mx, [0]), g.Cfg(ref='BAR'), None, rounds=6, seed=4, log=lambda *_: None)
    assert len(out['history']) == 7 and out['configs_evaluated'] <= 7
    assert out['final'] == out['start']                               # n < 200 on a toy matrix: nothing can be kept
    assert all(not h['kept'] for h in out['history'][1:]) and out['train_positive_configs'] == 0


def test_run_end_to_end_on_a_synthetic_matrix(tmp_path, monkeypatch):
    days = ['2025-03-20', '2025-03-21', '2025-04-02', '2025-05-07', '2026-02-03', '2026-02-04', '2026-03-09', '2026-10-05']
    mx = make_mx(days, [f'S{k}USDT' for k in range(len(days))], ['G', 'L'] * 4)
    for p in range(len(days)):
        shock(mx, p, g.L_DAY + 300 + 100 * p, side=1 if p % 2 == 0 else -1)
    path = tmp_path / 'matrix.npz'
    np.savez(path, **mx)
    monkeypatch.setattr(g, 'MIN_TRAIN_N', 2)
    res = g.run(log=lambda *_: None, rounds=3, paths=25, matrix=path, fund=g.Funding(root=tmp_path))
    assert res['pairs'] == {'TRAIN': 4, 'HOLDOUT': 3, 'FRESH': 1} and len(res['t1']['train']) == 12
    assert res['t1']['cell'] in res['t1']['train'] and res['t1']['n_trials'] == 12
    assert res['t2']['n_trials'] == 12 + res['t2']['search']['configs_evaluated'] <= 12 + 4
    for key in ('t1', 't2'):
        assert res[key]['verdict'] == 'NO EDGE' and res[key]['checks']['holdout_n>=100'] is False
        st = res[key]['holdout']['stats']
        assert st['n'] >= 1 and st['funding_known'] == 0.0             # no funding file: UNKNOWN, flagged, not zero-known
        assert set(res[key]['holdout']['schedules']) == set(g.SCHEDULES)
        assert res[key]['holdout']['schedules']['ZERO']['mean'] > res[key]['holdout']['schedules']['SLIP10']['mean']
        gr = res['growth'][key]
        assert set(gr['sizing']) == set(g.SIZINGS) and gr['sizing']['FLAT_1U']['paths'] == 25
        assert set(gr['sizing_addendum']) == set(g.ADDENDUM_SIZINGS)   # A1, labelled as not pre-registered
        assert gr['one_slot_trades'] <= gr['all_trades'] and len(gr['trades']['net']) == gr['all_trades']
    assert set(res['t3']['accuracy']) == {'TRAIN', 'HOLDOUT', 'FRESH'} and res['t3']['accuracy']['TRAIN']['pairs'] == 4
    json.dumps(res, default=float)                                    # the result is what the CLI writes


# ------------------------------------------------------------------------------------------------- descriptives
def test_accuracy_beats_the_flat_mean_on_a_self_exciting_process():
    rng = np.random.default_rng(9)
    mx = make_mx(['2025-03-20'] * 40, ['AUSDT'] * 40, ['G'] * 40, n=inar(rng, 5.0, 0.12, 5, g.L3, 40))
    acc = g.accuracy(mx, np.arange(40), 5)
    assert acc['pairs'] == 40 and acc['skill_vs']['MEAN']['deviance'] > 0.2 and acc['skill_vs']['LAST']['mae'] > 0.0
    assert acc['n_hat']['median'] == pytest.approx(0.12 / (math.exp(g.beta_of(5)) - 1.0), abs=0.08)
    assert acc['share_minutes_rho_mu_ge3'] > acc['share_minutes_rho_bar_ge3']
    assert g.accuracy(make_mx(['2025-03-20'], ['AUSDT'], ['G'], n=np.zeros((1, g.L3))), [0], 5) == {'pairs': 0}


def test_forward_measures_signed_moves_and_excursions():
    mx = make_mx(['2025-03-20'], ['AUSDT'], ['G'])
    shock(mx, 0, g.L_DAY + 600, length=45)                             # +10 bps a minute for 45 minutes
    s = g.L_DAY + 600
    tr = {'row': np.array([0, 0]), 's': np.array([s - 1, s - 1]), 'side': np.array([1, -1]), 'day': np.array(['a', 'b'])}
    fw = g.forward(mx, tr)
    assert fw['n'] == 2 and fw['fwd_5m']['mean'] == pytest.approx(0.0, abs=1e-3) and fw['fwd_5m']['win'] == 0.5
    assert fw['reach_400']['favourable'] == 0.5 and fw['reach_400']['adverse'] == 0.5 and fw['reach_600']['favourable'] == 0.0
    assert fw['mfe_median'] == pytest.approx(225.0, abs=0.5)           # +450 for the long, 0 for the short
    assert g.forward(mx, {'row': np.array([], dtype=int), 's': [], 'side': [], 'day': []}) == {'n': 0}


# ------------------------------------------------------------------------------------------------- growth
def test_one_slot_skips_a_trigger_while_a_position_is_open():
    tr = trades_of([1, 2, 3, 4], ['a', 'a', 'a', 'b'], [0, 50, 100, 500], [100, 400, 300, 600])
    assert g.one_slot(tr).tolist() == [0, 2, 3]                        # the 50 entry is inside 0..100; 100 is free
    book = g.day_book(tr, ['a', 'b', 'c'], 160.0)
    assert [len(d) for d in book] == [2, 1, 0] and book[0][0][:3] == (1.0, 160.0, 1e9)


def test_growth_path_sizes_by_the_stop_clamps_losses_and_stops_at_ruin():
    rng = np.random.default_rng(1)
    flat = g.SIZINGS['FLAT_1U']
    lose = [[(-160.0, 160.0, 1e9, 3)]]
    p = g.growth_path(lose, rng, flat, days=3)
    assert p['equity'] == pytest.approx(7.0) and p['trades'] == 3 and p['low'] == pytest.approx(7.0) and not p['ruined']
    assert p['at'] == {1: pytest.approx(9.0)}
    ruin = g.growth_path(lose, rng, flat, days=30)
    assert ruin['ruined'] and ruin['equity'] < g.RUIN_U and ruin['trades'] == 10      # 10 -> 0 in ten 1 U losses
    gap = g.growth_path([[(-50000.0, 160.0, 1e9, 3)]], rng, flat, days=5)
    assert gap['equity'] == 0.0 and gap['ruined'] and gap['trades'] == 1               # the account cannot owe
    win = g.growth_path([[(390.0, 160.0, 1e9, 3)]], rng, flat, days=2)
    assert win['equity'] == pytest.approx(10 + 2 * 62.5 * 0.039) and win['best_hour'] == pytest.approx(62.5 * 0.039)
    thin = g.growth_path([[(390.0, 160.0, 1000.0, 3)]], rng, flat, days=1)            # 1 % of 1 000 U = 10 U notional
    assert thin['equity'] == pytest.approx(10 + 10 * 0.039)
    lev = g.growth_path([[(390.0, 10.0, 1e9, 3)]], rng, flat, days=1)                 # 1 U / 10 bps = 1 000 U > 20 x 10
    assert lev['equity'] == pytest.approx(10 + 200 * 0.039)


def test_the_standing_cap_cannot_post_this_bracket_and_the_ratchet_halts_at_the_principal():
    rng = np.random.default_rng(1)
    tiny = g.growth_path([[(390.0, 160.0, 1e9, 3)]], rng, g.SIZINGS['FLAT_0.05U'], days=4)
    assert tiny['trades'] == 0 and tiny['skipped'] == 4 and tiny['equity'] == 10.0    # 0.05 / 1.6 % = 3.1 U < 5 U
    assert g.MIN_NOTIONAL * 160 * 1e-4 == pytest.approx(0.08)                         # the smallest loss at this stop
    day = [[(160.0, 160.0, 1e9, 1), (-160.0, 160.0, 1e9, 2), (-160.0, 160.0, 1e9, 3)]]
    r = g.growth_path(day, rng, g.SIZINGS['STACK50_RATCHET'], days=3)
    # +1.0 -> 11 (stop ratchets to 10); stacked risk 1.5 capped at 11 - 10 = 1.0 -> 10; then the sizer returns 0
    assert r['halted'] and r['equity'] == pytest.approx(10.0) and r['trades'] == 2 and not r['ruined']
    s = g.growth_path(day, rng, g.SIZINGS['STACK50'], days=1)
    assert s['equity'] == pytest.approx(10 + 1.0 - 1.5 - 1.0)                         # no ratchet: the stack is lost
    mc = g.growth_mc(day, g.SIZINGS['STACK50_RATCHET'], paths=20, days=3)
    assert mc['p_halted_at_ratchet'] == 1.0 and mc['p_ruin'] == 0.0 and mc['median'] == pytest.approx(10.0)
    assert mc['p_pace_20u_per_hour'] == 0.0 and mc['p_principal_ever_impaired'] == 0.0 and mc['at_day']['1']['median'] == pytest.approx(10.0)
    assert g.growth_mc([], flat := g.SIZINGS['FLAT_1U']) == {'paths': 0} and flat['base_risk'] == 1.0


def test_addendum_stacks_only_after_two_wins_in_a_row():
    rng = np.random.default_rng(1)
    wins = [[(160.0, 160.0, 1e9, 1)] * 3]                               # +1 R each at the base size
    one = g.growth_path(wins, rng, g.SIZINGS['STACK50'], days=1)
    two = g.growth_path(wins, rng, g.ADDENDUM_SIZINGS['STACK50_AFTER_2_WINS'], days=1)
    assert one['equity'] == pytest.approx(10 + 1.0 + 1.5 + 2.25)       # stacked from the second trade
    assert two['equity'] == pytest.approx(10 + 1.0 + 1.0 + 2.0)        # stacked from the third
    assert not set(g.ADDENDUM_SIZINGS) & set(g.SIZINGS)
    assert all(v['min_streak'] == 2 and v['base_risk'] == 1.0 for v in g.ADDENDUM_SIZINGS.values())


def test_target_arithmetic_of_twenty_u_per_hour():
    t = g.target_arithmetic(160.0, 400.0, trades_per_day=2.4, mean_net_bps=-10.0)
    assert t['notional_u'] == pytest.approx(62.5) and t['leverage_x'] == pytest.approx(6.25)
    assert t['net_win_u'] == pytest.approx(62.5 * 0.039) and t['net_winners_per_hour_needed'] == pytest.approx(20 / 2.4375)
    assert t['triggers_per_hour_one_slot'] == pytest.approx(0.1) and t['measured_u_per_hour'] == pytest.approx(-0.00625)
    assert t['net_bps_per_hour_needed_on_that_notional'] == pytest.approx(3200.0)


# ------------------------------------------------------------------------------------------------- the sizer
def test_profit_stack_risk_base_stack_and_ratchet():
    assert profit_stack_risk(10.0) == 1.0                              # the principal carries the base risk
    assert profit_stack_risk(9.0, streak=3) == 1.0                     # under water: no stack whatever the streak
    assert profit_stack_risk(14.0, streak=1) == pytest.approx(3.0)     # 1 + 50 % of 4 U of profit
    assert profit_stack_risk(14.0, streak=0) == 1.0                    # the last trade lost: base only
    assert profit_stack_risk(14.0, streak=1, min_streak=2) == 1.0
    assert profit_stack_risk(14.0, streak=2, stack_frac=0.7) == pytest.approx(3.8)
    assert profit_stack_risk(0.6) == pytest.approx(0.6)                # never more than the equity
    # ratchet: armed once the peak reached principal + base risk; then the stop is the principal
    assert profit_stack_risk(10.9, peak=10.9, ratchet=True) == 1.0     # not armed yet: the principal is still at risk
    assert profit_stack_risk(11.0, streak=1, peak=11.0, ratchet=True) == pytest.approx(1.0)   # 1.5 wanted, 1.0 of room
    assert profit_stack_risk(14.0, streak=1, peak=14.0, ratchet=True) == pytest.approx(3.0)
    assert profit_stack_risk(10.4, peak=11.5, ratchet=True) == pytest.approx(0.4)
    assert profit_stack_risk(10.0, peak=11.0, ratchet=True) == 0.0     # back at the money put in: halt
    assert profit_stack_risk(9.5, peak=11.0, ratchet=True) == 0.0      # a gap through the stop does not re-arm risk
    assert profit_stack_risk(9.5, peak=11.0, ratchet=False) == 1.0


def test_profit_stack_risk_never_sizes_unknown_inputs_and_has_no_engine_call_site():
    for bad in (None, float('nan'), float('inf'), 0.0, -5.0, '10', True):
        assert profit_stack_risk(bad) == 0.0
    assert profit_stack_risk(10.0, principal=0.0) == 0.0 and profit_stack_risk(10.0, base_risk=0.0) == 0.0
    assert profit_stack_risk(10.0, stack_frac=-0.1) == 0.0 and profit_stack_risk(10.0, peak=float('nan')) == 0.0
    assert profit_stack_risk(10.0, streak=None) == 0.0
    for path in list((ROOT / 'core').glob('*.py')) + list((ROOT / 'scripts').glob('*.py')):
        if path.name != 'position_sizer.py':
            assert 'profit_stack_risk' not in path.read_text(encoding='utf-8', errors='ignore'), path
    eng = (ROOT / 'core' / 'engine.py').read_text(encoding='utf-8', errors='ignore')
    assert 'hawkes' not in eng.lower()
