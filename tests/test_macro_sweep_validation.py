"""2026-11-14 (e) MLS-2 (research/macro_sweep_validation.py), the profit-funded Kelly budget (core/position_sizer.py)
and the two public-data tools. Synthetic klines and hand-built rows: code tests, not economic evidence (that is
data/macro_sweep_validation.json)."""
import datetime as dt
import hashlib
import json
import math
import random
import zipfile
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pytest

from core.position_sizer import MicroCapitalSizer, fractional_kelly_risk, kelly_lcb, profit_funded_risk
from research import macro_sweep_study as mls
from research import macro_sweep_validation as v
from research import smc_fibo_backtest as bt
from tools import fetch_klines_fresh as ff
from tools import universe_snapshot as us

ROOT = Path(__file__).resolve().parents[1]
H, DAY = 3_600_000, 86_400_000


def row(symbol='AUSDT', t_fill=0, hours=10.0, net=10.0, risk=60.0, why='TP', t_signal=None, plc=()):
    return {'symbol': symbol, 'side': 1, 't_signal': t_fill if t_signal is None else t_signal, 't_fill': t_fill,
            't_exit': t_fill + int(hours * H), 'why': why, 'gross_bps': net + (4.0 if why == 'TP' else 7.0), 'net_bps': net,
            'risk_bps': risk, 'tp_bps': 200.0, 'net_R': net / (risk + 8.0), 'placebo_net_bps': list(plc)}


# ========================================================================================= frozen configuration
def test_the_configuration_is_the_one_frozen_before_the_download():
    plan = (ROOT / v.PLAN).read_bytes().replace(b'\r\n', b'\n')
    log = [json.loads(l) for l in (ROOT / 'docs/research_upgrade/trial_log.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    frozen = [r for r in log if r.get('kind') == 'mls2-prereg']
    assert len(frozen) == 1 and frozen[0]['freeze']['sha256_lf'] == hashlib.sha256(plan).hexdigest()
    assert asdict(v.FINAL) == frozen[0]['cfg'] and (v.N_TRIALS, v.MIN_N, v.SEED) == (49, 100, 20261115)
    assert (v.FINAL.trigger, v.FINAL.stop, v.FINAL.shield, v.FINAL.trail, v.FINAL.session) == ('ABS', 'STRUCT', False, 0.0, True)


# ======================================================================================================== data
def test_month_runs_split_at_a_gap():
    names = ['AUSDT-1m-2025-11.zip', 'AUSDT-1m-2025-12.zip', 'AUSDT-1m-2026-01.zip', 'AUSDT-1m-2026-04.zip']
    runs = v.month_runs([Path(n) for n in reversed(names)])
    assert [[p.name[-11:-4] for p in r] for r in runs] == [['2025-11', '2025-12', '2026-01'], ['2026-04']]
    assert v.month_of(bt.ms('2026-01-31') + 23 * H) == '2026-01'
    assert v.months_by_symbol({'2025-01': ['A', 'B'], '2025-02': ['A']}) == {'A': {'2025-01', '2025-02'}, 'B': {'2025-01'}}


def test_a_trade_closed_by_the_end_of_its_data_is_flagged():
    rows = v.mark_cut([row(why='TIME', hours=20.0), row(why='TIME', hours=v.FINAL.hold_h), row(why='SL', hours=1.0)], v.FINAL)
    assert [r['cut'] for r in rows] == [True, False, False]


def write_month(root: Path, symbol: str, stamp: str, t0: int, n: int, rng, p0: float) -> float:
    c = p0 * np.exp(np.cumsum(rng.normal(0, 0.0008, n)))
    o = np.r_[p0, c[:-1]]
    hi, lo = np.maximum(o, c) * (1 + np.abs(rng.normal(0, 0.0004, n))), np.minimum(o, c) * (1 - np.abs(rng.normal(0, 0.0004, n)))
    vol = rng.uniform(500, 1500, n)
    tb = vol * rng.uniform(0.2, 0.8, n)
    text = '\n'.join(f'{t0 + i * 60_000},{o[i]:.6f},{hi[i]:.6f},{lo[i]:.6f},{c[i]:.6f},{vol[i]:.3f},{t0 + i * 60_000 + 59_999},0,1,{tb[i]:.3f},0,0'
                     for i in range(n))
    with zipfile.ZipFile(root / f'{symbol}-1m-{stamp}.zip', 'w', zipfile.ZIP_DEFLATED) as z:
        z.writestr(f'{symbol}-1m-{stamp}.csv', text)
    return float(c[-1])


@pytest.fixture(scope='module')
def tape(tmp_path_factory):
    """CUSDT: 2026-09 monthly + two fresh days.  XUSDT: 2025-01..02 and, after a gap, 2025-05."""
    root = tmp_path_factory.mktemp('klines')
    fresh = root / 'fresh'
    fresh.mkdir()
    rng = np.random.default_rng(3)
    p = write_month(root, 'CUSDT', '2026-09', bt.ms('2026-09-01'), 30 * 1440, rng, 100.0)
    for d in ('2026-10-01', '2026-10-02'):
        p = write_month(fresh, 'CUSDT', d, bt.ms(d), 1440, rng, p)
    p = 50.0
    for stamp, n in (('2025-01', 31 * 1440), ('2025-02', 28 * 1440), ('2025-05', 31 * 1440)):
        p = write_month(root, 'XUSDT', stamp, bt.ms(stamp + '-01'), n, rng, p)
    (root / 'universe.json').write_text(json.dumps({'months': {'2025-02': ['CUSDT', 'XUSDT'], '2025-05': ['XUSDT']}}))
    return root, fresh


def test_loaders_join_archive_and_fresh_and_keep_the_partitions_apart(tape, monkeypatch):
    root, fresh = tape
    seen = {}

    def fake(sym, cfg, rng=None, t_hi=None):                             # record what each symbol was fed
        t = sym['m1']['t']
        seen.setdefault(sym['symbol'], []).append((int(t[0]), int(t[-1]), len(t)))
        return [row(sym['symbol'], int(t[0]) + 5 * DAY), row(sym['symbol'], int(t[-1]) - 2 * H, why='TIME', hours=1.5)]

    monkeypatch.setattr(mls, 'run_cfg', fake)
    core, end = v.core_trades(random.Random(1), ('CUSDT',), root, fresh, log=lambda *_: None)
    assert seen['CUSDT'] == [(bt.ms('2026-09-01'), bt.ms('2026-10-03') - 60_000, 32 * 1440)] and end == bt.ms('2026-10-03')
    assert [r['t_fill'] >= bt.ms(v.FRESH_START) for r in core] == [False, True] and core[1]['cut'] is True
    uni = json.loads((root / 'universe.json').read_text())['months']
    x = v.xsec_trades(uni, random.Random(1), ('CUSDT',), root, log=lambda *_: None)
    assert [(a, b) for a, b, _ in seen['XUSDT']] == [(bt.ms('2025-01-01'), bt.ms('2025-03-01') - 60_000),
                                                     (bt.ms('2025-05-01'), bt.ms('2025-06-01') - 60_000)]   # two runs
    assert {r['symbol'] for r in x} == {'XUSDT'} and sorted(v.month_of(r['t_fill']) for r in x) == ['2025-02', '2025-05', '2025-05']


def test_the_whole_pipeline_runs_on_synthetic_klines(tape):
    root, fresh = tape
    out = v.run(seed=5, core_syms=('CUSDT',), klines=root, fresh=fresh, universe_path=root / 'universe.json',
                universe40_path=root / 'none.json', paths=20, log=lambda *_: None)
    assert out['fresh_days'] == 2.0 and out['verdict']['verdict'] in ('CONFIRMED', 'NOT CONFIRMED', 'REFUTED')
    assert set(out) >= {'F', 'X', 'U', 'HOLDOUT_completed', 'POOLED_HOLDOUT_F_X', 'frequency', 'kelly', 'trajectory', 'trades'}
    assert 'top40_extrapolated' not in out['frequency'] and json.dumps(out, default=str)


# ================================================================================================== statistics
def test_clustered_standard_error_by_hand():
    assert v.clustered_se([1.0, 1.0, -1.0, -1.0], [0, 0, 1, 1]) == pytest.approx(1.0)       # sqrt(2 (4 + 4)) / 4
    assert v.clustered_se([1.0, 1.0, -1.0, -1.0], [0, 1, 2, 3]) == pytest.approx(math.sqrt(4 / 3 * 4) / 4)
    assert v.clustered_se([1.0, 2.0], [7, 7]) is None and v.clustered_se([1.0], [0]) is None


def test_read_uses_the_larger_standard_error_and_counts_symbols():
    rows = [row('A', d * DAY, net=n) for d, n in ((0, 100.0), (0, 100.0), (0, 100.0), (1, -60.0), (1, -60.0), (1, -60.0))]
    rows += [row('B', 2 * DAY, net=5.0, why='TIME')]
    rows[-1]['cut'] = True
    s = v.read(rows, 49)
    assert s['n'] == 7 and s['se'] == s['se_clustered'] > s['se_iid'] and s['cut_at_data_end'] == 1
    assert s['lcb95'] == pytest.approx(s['mean_net_bps'] - 1.645 * s['se'], abs=2e-3) and s['ucb95'] > s['mean_net_bps']
    assert (s['symbols'], s['symbols_ge5'], s['symbols_ge5_positive']) == (2, 1, 1) and 0.0 <= s['dsr'] <= 1.0
    assert v.read([]) == {'n': 0} and v.read([row()])['se'] is None


def u(n=150, lcb=2.0, ucb=20.0, plc=1.0, ge5=8, pos=5, dsr=0.97):
    return {'n': n, 'lcb95': lcb, 'ucb95': ucb, 'vs_placebo': {'lcb95': plc}, 'symbols_ge5': ge5, 'symbols_ge5_positive': pos, 'dsr': dsr}


def test_verdict_follows_section_3():
    assert v.verdict(u())['verdict'] == 'CONFIRMED'
    for bad in (u(n=99), u(lcb=-0.1), u(plc=-0.1), u(pos=3), u(dsr=0.94), u(ge5=0, pos=0)):
        assert v.verdict(bad)['verdict'] == 'NOT CONFIRMED'
    assert v.verdict(u(lcb=-30.0, ucb=-1.0))['verdict'] == 'REFUTED'
    assert v.verdict({'n': 0})['verdict'] == 'NOT CONFIRMED'


# =================================================================================================== frequency
def test_frequency_and_open_positions_are_time_weighted():
    t0 = bt.ms('2025-01-01')
    rows = [row('A', t0 + 8 * H, hours=24.0, t_signal=t0 + 8 * H),              # London bar, day 0
            row('B', t0 + 20 * H, hours=24.0, t_signal=t0 + 15 * H),            # New York bar, day 0: overlaps 12 h
            row('C', t0 + 3 * DAY, hours=48.0, t_signal=t0 + 3 * DAY + 9 * H)]  # runs past the window end
    f = v.frequency(rows, '2025-01-01', '2025-01-05')
    assert (f['days'], f['trades'], f['max_per_day'], f['open_max']) == (4, 3, 2, 2) and f['days_without_trade'] == 0.5
    assert f['open_mean'] == pytest.approx((24 + 24 + 24) / 96) and f['time_flat'] == pytest.approx((8 + 28) / 96, abs=1e-4)
    assert f['mean_hold_h'] == pytest.approx(32.0) and f['london_share'] == pytest.approx(2 / 3, abs=1e-4)


def test_top_40_is_an_extrapolation_at_the_measured_rate():
    u20, u40 = {'2025-01': ['A', 'B'], '2025-02': ['A']}, {'2025-01': ['A', 'B', 'C', 'D'], '2025-02': ['A', 'C'], '2025-03': ['Z']}
    assert v.symbol_days(u20, lambda s: s == 'A') == 31 + 28 and v.symbol_days(u20, lambda s: s != 'A') == 31
    e = v.expand({'trades_per_day': 0.5, 'days': 59, 'mean_hold_h': 12.0}, 0.02, u20, u40)
    assert e['extra_symbol_days'] == 2 * 31 + 28 and e['months'] == 2
    assert e['trades_per_day'] == pytest.approx(0.5 + 0.02 * 90 / 59, abs=1e-4)
    assert e['open_mean'] == pytest.approx(e['trades_per_day'] * 0.5, abs=1e-4)             # Little's law


# ============================================================================================ Kelly, trajectory
def test_kelly_on_the_lower_bound_and_the_existing_rule_is_unchanged():
    f = kelly_lcb(600, 400, 2.0)
    assert f == pytest.approx(0.3615, abs=2e-3) and f < 0.6 - 0.4 / 2.0                      # below the point estimate
    assert kelly_lcb(33, 64, 2.5) < 0 and kelly_lcb(10, 10, None) == 0.0 and kelly_lcb(10, 10, 0.0) == 0.0
    assert fractional_kelly_risk(5.0, 600, 400, 2.0, 0.05) == pytest.approx(min(0.05, 0.25 * f * 5.0, 0.15))
    assert fractional_kelly_risk(5.0, 33, 64, 2.5, 0.05) == 0.0 and fractional_kelly_risk(5.0, 600, 400, None, 0.05) == 0.0


def test_only_profit_and_a_proven_edge_raise_the_budget():
    f = 0.25 * kelly_lcb(600, 400, 2.0)
    assert profit_funded_risk(5.0, 5.0, 600, 400, 2.0) == 0.05                               # no profit yet
    assert profit_funded_risk(4.0, 5.0, 600, 400, 2.0) == 0.05                               # drawdown: the cap, not less
    assert profit_funded_risk(10.0, 5.0, 33, 64, 2.5) == 0.05                                # profit, edge not proven
    assert profit_funded_risk(6.0, 5.0, 600, 400, 2.0) == pytest.approx(0.05 + f * 1.0)
    assert profit_funded_risk(10.0, 5.0, 600, 400, 2.0) == pytest.approx(0.30)               # 3 % of equity cap
    assert profit_funded_risk(None, 5.0, 600, 400, 2.0) == 0.05 and profit_funded_risk(10.0, None, 600, 400, 2.0) == 0.05
    assert profit_funded_risk(10.0, 5.0, 600, 400, 2.0, base_cap_usdt=0.0) == 0.0


def test_the_sizer_takes_the_budget_only_when_it_is_passed():
    m = MicroCapitalSizer()
    assert m.risk_notional(0.60, 0.08) == pytest.approx(0.05 / 0.0068)
    assert m.risk_notional(0.60, 0.08, max_loss_usdt=0.10) == pytest.approx(0.10 / 0.0068)
    for bad in (None, float('nan'), 0.0, -1.0):
        assert m.risk_notional(0.60, 0.08, max_loss_usdt=bad) == pytest.approx(0.05 / 0.0068)
    assert m.risk_notional(0.0, 0.0, max_loss_usdt=0.10) is None


def test_evidence_and_trajectory():
    ev = v.evidence([row(net=136.0, risk=60.0), row(net=-68.0, risk=60.0), row(net=-68.0, risk=60.0)])   # +2 R, -1 R, -1 R
    assert (ev['wins'], ev['losses'], ev['payoff_b']) == (1, 2, pytest.approx(2.0))
    assert ev['kelly_point'] == pytest.approx(1 / 3 - (2 / 3) / 2.0) and ev['kelly_lcb'] < ev['kelly_point'] and ev['f_applied'] == 0.0
    flat = v.trajectory([1.0], 2.0, ev, days=(10, 30), paths=5)                              # no loss ever: payoff stays as given
    assert flat['trades_in_365d'] == 60 and flat['equity'][10]['median'] > 5.0
    none = {'wins': 0, 'losses': 0, 'sum_win': 0.0, 'sum_loss': 0.0}
    base = v.trajectory([1.0], 2.0, none, days=(10, 30), paths=5)                            # payoff unknown: 0.05 U per trade
    assert base['equity'][10]['median'] == pytest.approx(5.0 + 0.05 * 20) and base['equity'][30]['p95'] == pytest.approx(8.0)
    assert base['mean_risk_end'] == 0.05 and base['paths_scaled_at_end'] == 0.0
    strong = {'wins': 600, 'losses': 400, 'sum_win': 1200.0, 'sum_loss': 400.0}
    grow = v.trajectory([2.0, 2.0, 2.0, -1.0, -1.0], 2.0, strong, days=(30, 365), paths=200)
    same = v.trajectory([2.0, 2.0, 2.0, -1.0, -1.0], 2.0, none, days=(30, 365), paths=200)
    assert grow['paths_scaled_at_end'] > 0.9 and grow['equity'][365]['median'] > same['equity'][365]['median']
    assert v.trajectory([], 2.0, none, days=(30,), paths=3)['equity'][30]['median'] == 5.0   # nothing to draw: flat


# ======================================================================================================= tools
def test_fresh_fetch_plan_monthly_then_daily():
    w = ff.wanted(dt.date(2026, 10, 1), dt.date(2026, 11, 3))
    assert w == [('monthly', '2026-10'), ('daily', '2026-11-01'), ('daily', '2026-11-02'), ('daily', '2026-11-03')]
    assert ff.wanted(dt.date(2026, 10, 30), dt.date(2026, 11, 1)) == [('daily', '2026-10-30'), ('daily', '2026-10-31'), ('daily', '2026-11-01')]
    assert ff.month_days('2026-10', dt.date(2026, 10, 3)) == [('daily', f'2026-10-0{d}') for d in (1, 2, 3)]


def test_fresh_fetch_verifies_the_checksum_and_never_writes_a_missing_file(tmp_path, monkeypatch):
    blob = b'zipbytes'
    good = {'.CHECKSUM': (hashlib.sha256(blob).hexdigest() + '  x.zip').encode(), '.zip': blob}
    monkeypatch.setattr(ff, 'fetch', lambda url, tries=4: good['.CHECKSUM' if url.endswith('.CHECKSUM') else '.zip'])
    assert ff.get('daily', 'AUSDT', '2026-11-01', tmp_path) == 'done' and ff.get('daily', 'AUSDT', '2026-11-01', tmp_path) == 'have'
    assert (tmp_path / 'AUSDT-1m-2026-11-01.zip').read_bytes() == blob

    def gone(url, tries=4):
        raise OSError('404')
    monkeypatch.setattr(ff, 'fetch', gone)
    assert ff.get('daily', 'AUSDT', '2026-11-02', tmp_path) == 'missing' and not (tmp_path / 'AUSDT-1m-2026-11-02.zip').exists()
    monkeypatch.setattr(ff, 'fetch', lambda url, tries=4: b'0' * 64 + b'  x' if url.endswith('.CHECKSUM') else blob)
    with pytest.raises(SystemExit):
        ff.get('daily', 'AUSDT', '2026-11-03', tmp_path)


def test_snapshot_filters_volume_then_atr_over_spread():
    k = [[0, '100', '101', '99', '100', '1', 0, '1', 1, '1', '1', '0']] * 16 + [[0, '100', '130', '70', '100', '1', 0, '1', 1, '1', '1', '0']]
    assert us.atr_bps(k) == pytest.approx(200.0) and us.atr_bps(k[:10]) is None            # the open last bar is ignored
    rows = [{'symbol': 'A', 'qv': 9e8, 'spread_bps': 1.0, 'atr_bps': 80.0}, {'symbol': 'B', 'qv': 4e7, 'spread_bps': 1.0, 'atr_bps': 80.0},
            {'symbol': 'C', 'qv': 8e8, 'spread_bps': 6.0, 'atr_bps': 80.0}, {'symbol': 'D', 'qv': 7e8, 'spread_bps': None, 'atr_bps': 80.0},
            {'symbol': 'E', 'qv': 6e8, 'spread_bps': 2.0, 'atr_bps': 90.0}, {'symbol': 'F', 'qv': 5e8, 'spread_bps': 1.0, 'atr_bps': 50.0}]
    picked, why = us.pick(rows, top=2)
    assert [r['symbol'] for r in picked] == ['A', 'E'] and picked[1]['ratio'] == 45.0
    assert why == {'volume': 1, 'unknown': 1, 'atr_spread': 1, 'below_top': 1}
