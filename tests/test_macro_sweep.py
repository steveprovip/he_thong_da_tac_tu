"""2026-11-14 MLS-1 macro liquidity sweep (research/macro_sweep_study.py): detector fields, session windows, bracket
limits, the 1 m simulator (cross-checked against research/smc_fibo_backtest.simulate), fee re-pricing, acceptance,
search mutations and the freeze of the pre-registration. Hand-built bars and random walks: code tests, not economic
evidence (that is data/macro_sweep_study.json)."""
import hashlib
import json
import random
from dataclasses import asdict, replace
from pathlib import Path

import numpy as np
import pytest

from core.smc_fibo import Setup
from research import macro_sweep_study as mls
from research import smc_fibo_backtest as bt
from tests.test_smc_fibo import SWEEP, feed, pullback

ROOT = Path(__file__).resolve().parents[1]
MIN = 60_000


# ================================================================================================ detector
def test_candidates_cover_the_wide_zone_and_carry_normalised_fields():
    got = [s for _, s in feed(mls.MacroSweep(), pullback() + [SWEEP])]
    assert len(got) > 1 and all(0.50 <= s.retrace <= 1.0 and s.side == 'BUY' and s.tp == 103.0 for s in got)
    assert all(s.swept == 0.0 for s in got[:-1])                         # only the last bar sweeps a confirmed low
    s = got[-1]
    assert s.entry == pytest.approx(100.40) and s.retrace == pytest.approx(0.75) and s.swept == pytest.approx(100.30)
    assert s.delta_z <= -1.0 and s.clv >= 0.5 and s.sl < 100.0 and (s.leg_lo, s.leg_hi) == (99.0, 103.0)


def test_short_candidate_is_the_mirror_with_the_same_normalised_signs():
    s = [x for _, x in feed(mls.MacroSweep(), pullback() + [SWEEP], mirror=True)][-1]
    assert s.side == 'SELL' and s.entry == pytest.approx(99.60) and s.sl > 100.0 and s.tp == pytest.approx(97.0)
    assert s.delta_z <= -1.0 and s.clv >= 0.5 and s.retrace == pytest.approx(0.75) and s.swept == pytest.approx(99.70)


def test_no_h4_trend_no_candidate():
    assert feed(mls.MacroSweep(), pullback() + [SWEEP], h4=False) == []


def test_filters_select_from_the_candidates():
    c = [s for _, s in feed(mls.MacroSweep(), pullback() + [SWEEP])]
    off = replace(mls.Cfg(), session=False)                              # the fixture's bars close 00:05 .. 04:35 UTC
    assert not any(mls.passes(s, mls.Cfg()) for s in c)                  # outside both windows
    assert [mls.passes(s, off) for s in c] == [False] * (len(c) - 1) + [True]        # only 0.75 is in 0.705-0.886
    assert mls.passes(c[-1], replace(off, trigger='SWP')) and not mls.passes(c[-2], replace(off, trigger='SWP', zone_lo=0.5))
    assert not mls.passes(c[-1], replace(off, zone_hi=0.74)) and not mls.passes(c[-1], replace(off, leg_atr=50.0))
    assert not mls.passes(c[-1], replace(off, delta_z=50.0)) and not mls.passes(c[-1], replace(off, clv=0.95))


@pytest.mark.parametrize('hhmm, hours, want', [
    ((7, 0), 3.0, True), ((6, 55), 3.0, False), ((9, 55), 3.0, True), ((10, 0), 3.0, False),
    ((13, 30), 3.0, True), ((13, 25), 3.0, False), ((16, 25), 3.0, True), ((16, 30), 3.0, False),
    ((8, 0), 1.0, False), ((7, 55), 1.0, True), ((14, 30), 1.0, False), ((0, 0), 3.0, False),
])
def test_session_is_decided_by_the_bar_open(hhmm, hours, want):
    t_close = 3 * 86_400_000 + (hhmm[0] * 60 + hhmm[1] + 5) * MIN        # a 5 m bar that OPENS at hh:mm UTC
    assert mls.in_session(t_close, hours) is want


# ================================================================================================= bracket
def setup(side='BUY', entry=100.0, sl=99.7, leg=(99.2, 102.5), t=425 * MIN, rho=0.8, z=-1.5, clv=0.7, swept=0.0):
    return Setup(t, side, entry, sl, leg[1] if side == 'BUY' else leg[0], abs(entry - sl) / entry * 100, leg[0], leg[1],
                 rho, swept, z, clv, 1.0)                                # ATR(H1) = 1 % of price


def test_tight_and_structural_stops_and_the_clipped_target():
    p = mls.plan(setup(), mls.Cfg())
    assert p['risk_bps'] == pytest.approx(30.0) and p['tp_bps'] == pytest.approx(250.0) and p['tp'] == pytest.approx(102.5)
    q = mls.plan(setup(), mls.Cfg(stop='STRUCT'))                        # L 99.2 - 0.1 x ATR 1.0 = 99.1
    assert q['sl'] == pytest.approx(99.1) and q['risk_bps'] == pytest.approx(90.0) and q['tp_bps'] == pytest.approx(250.0)
    assert mls.plan(setup(leg=(99.2, 104.0)), mls.Cfg())['tp_bps'] == 300.0          # cap
    assert mls.plan(setup(leg=(99.2, 100.8)), mls.Cfg())['tp_bps'] == 150.0          # floor
    assert mls.plan(setup(), replace(mls.Cfg(), trail=1.5))['trail_dist'] == pytest.approx(1.5)
    assert mls.plan(setup(), replace(mls.Cfg(), trail=0.4))['trail_dist'] == 0.0      # under 0.5 = off


def test_brackets_outside_the_frozen_limits_are_refused():
    assert mls.plan(setup(sl=99.0), mls.Cfg()) is None                               # 100 bps > the 91 bps loss cap
    assert mls.plan(setup(sl=99.95), mls.Cfg()) is None                              # 5 bps < 10 bps
    assert mls.plan(setup(leg=(99.2, 100.8)), mls.Cfg(stop='STRUCT')) is None        # 150 < 1.8 x 90
    assert (mls.RISK_MAX_BPS + mls.STOP_COST_BPS) / 1e4 * 5.05 <= 0.05               # the cap is the 0.05 U rule


def test_short_bracket_mirrors_the_long_one():
    p = mls.plan(setup('SELL', 100.0, 100.3, (97.5, 100.8)), mls.Cfg())
    assert p['side'] == -1 and p['risk_bps'] == pytest.approx(30.0) and p['tp'] == pytest.approx(97.5)
    q = mls.plan(setup('SELL', 100.0, 100.3, (97.5, 100.8)), mls.Cfg(stop='STRUCT'))
    assert q['sl'] == pytest.approx(100.9) and q['risk_bps'] == pytest.approx(90.0)


# =============================================================================================== simulator
def bars(rows):
    a = np.asarray(rows, dtype=float)
    return {'t': (np.arange(len(a)) * MIN).astype(np.int64), 'o': a[:, 0], 'h': a[:, 1], 'l': a[:, 2], 'c': a[:, 3]}


def walk(n=6000, seed=7):
    rng = np.random.default_rng(seed)
    c = 100.0 * np.exp(np.cumsum(rng.normal(0, 0.0008, n)))
    o = np.r_[100.0, c[:-1]]
    wick = np.abs(rng.normal(0, 0.0005, (2, n))) * c
    return {'t': (np.arange(n) * MIN).astype(np.int64), 'o': o, 'h': np.maximum(o, c) + wick[0],
            'l': np.minimum(o, c) - wick[1], 'c': c}


def test_without_a_trail_the_simulator_equals_the_smc_fibo_one():
    m, rng, seen = walk(), random.Random(11), set()
    for _ in range(400):
        i, side, be = rng.randint(50, 5000), rng.choice((1, -1)), rng.random() < 0.5
        e = float(m['c'][i - 1]) * (1 - side * rng.uniform(0, 0.001))
        sl, tp = e * (1 - side * rng.uniform(0.002, 0.01)), e * (1 + side * rng.uniform(0.004, 0.03))
        a = bt.simulate(m, int(m['t'][i]), side, e, sl, tp, 0.01, be, hold_bars=720)
        b = mls.simulate(m, int(m['t'][i]), side, e, sl, tp, 0.01, 720, shield=be)
        assert a['filled'] == b['filled'] and a['why'] == b['why']
        if a['filled']:
            assert (a['t_fill'], a['t_exit']) == (b['t_fill'], b['t_exit'])
            assert b['net_bps'] == pytest.approx(a['net_bps'], abs=1e-9) and b['gross_bps'] == pytest.approx(a['gross_bps'], abs=1e-9)
        seen.add(a['why'])
    assert {'SL', 'TP', 'BE', 'NO_FILL'} <= seen                         # the comparison exercised every branch


TRAIL = [(100.0, 100.1, 99.98, 100.05), (100.05, 101.2, 100.1, 101.1), (101.1, 101.5, 100.9, 101.3),
         (101.3, 101.4, 100.95, 101.0), (101.0, 101.1, 100.9, 101.0)]


def test_trailing_stop_follows_the_best_earlier_price_and_exits_as_a_taker():
    r = mls.simulate(bars(TRAIL), 0, 1, 100.0, 99.5, 103.0, 0.01, 720, trail_dist=0.5, trail_arm=1.0)
    assert r['why'] == 'TRAIL' and r['t_exit'] == 4 * MIN                # bar 3: low 100.95 <= 101.5 - 0.5
    assert r['gross_bps'] == pytest.approx((101.0 * (1 - 1e-4) / 100.0 - 1) * 1e4)
    assert r['net_bps'] == pytest.approx(r['gross_bps'] - 2.0 - 5.0)
    plain = mls.simulate(bars(TRAIL), 0, 1, 100.0, 99.5, 103.0, 0.01, 720)
    assert plain['why'] == 'TIME' and plain['t_exit'] == 5 * MIN         # without the trail the trade just runs out


def test_trail_needs_its_arming_move_and_never_uses_the_bar_that_hits_it():
    unarmed = mls.simulate(bars(TRAIL), 0, 1, 100.0, 99.5, 103.0, 0.01, 720, trail_dist=0.5, trail_arm=2.0)
    assert unarmed['why'] == 'TIME'                                      # best 101.5 never reached +2.0
    spike = [(100.0, 100.1, 99.98, 100.05), (100.05, 101.6, 100.2, 101.5), (101.5, 101.6, 101.2, 101.4)]
    r = mls.simulate(bars(spike), 0, 1, 100.0, 99.5, 103.0, 0.01, 720, trail_dist=0.5, trail_arm=1.0)
    assert r['why'] == 'TIME'                                            # bar 1's own high cannot stop bar 1 at 101.1


def test_fee_shield_scratches_at_the_cost_level_after_one_r():
    rows = [(100.0, 100.1, 99.98, 100.05), (100.05, 100.6, 100.02, 100.5), (100.5, 100.55, 100.0, 100.1)]
    r = mls.simulate(bars(rows), 0, 1, 100.0, 99.5, 103.0, 0.01, 720, shield=True)
    assert r['why'] == 'BE' and abs(r['net_bps']) < 0.01                 # entry + 8 bps, minus 1 bp slip, minus 2 + 5
    assert mls.simulate(bars(rows), 0, 1, 100.0, 99.5, 103.0, 0.01, 720)['why'] == 'TIME'


def test_short_trailing_stop_is_mirrored():
    rows = [(-o, -l, -h, -c) for o, h, l, c in TRAIL]                    # negate and swap high / low, then shift
    m = bars([(200 + o, 200 + h, 200 + l, 200 + c) for o, h, l, c in rows])
    r = mls.simulate(m, 0, -1, 100.0, 100.5, 97.0, 0.01, 720, trail_dist=0.5, trail_arm=1.0)
    assert r['why'] == 'TRAIL' and r['t_exit'] == 4 * MIN and r['gross_bps'] == pytest.approx((1 - 99.0 * (1 + 1e-4) / 100.0) * 1e4)


# ====================================================================================== fees and one config
def test_net_under_each_fee_schedule():
    assert mls.net_of(200.0, 'TP') == 196.0 and mls.net_of(-30.0, 'SL') == -37.0 and mls.net_of(10.0, 'TRAIL') == 3.0
    rows = [{'gross_bps': 200.0, 'why': 'TP'}, {'gross_bps': -30.0, 'why': 'SL'}]
    got = {k: v['mean_net_bps'] for k, v in mls.reprice(rows).items()}
    assert got['Binance VIP0 2/5 (primary)'] == pytest.approx((196.0 - 37.0) / 2)
    assert got['MEXC web 0/2'] == pytest.approx((200.0 - 32.0) / 2) and got['MEXC API 6/8'] == pytest.approx((188.0 - 44.0) / 2)
    assert got['directive -1.5/4.5 (no such tier)'] == pytest.approx((203.0 - 33.0) / 2) and got['zero 0/0'] == 85.0


def flat_symbol(moves: dict, cands: list, n=720) -> dict:
    a = np.full((n, 4), 100.0)
    for i, (h, l) in moves.items():
        a[i, 1], a[i, 2] = h, l
    m = bars(a)
    return {'symbol': 'TESTUSDT', 'm1': m, 'tick': 0.01, 't5': (np.arange(1, n // 5 + 1) * 5 * MIN).astype(np.int64),
            'cands': cands}


def test_one_position_one_filled_trade_per_leg_and_a_resting_order_blocks():
    cands = [setup(t=425 * MIN),                                         # A fills at 426, stopped at 430
             setup(t=440 * MIN),                                         # B same leg: refused
             setup(t=445 * MIN, leg=(99.2, 102.6)),                      # C new leg: fills at 446, target at 450
             setup(t=500 * MIN, leg=(99.2, 102.7)),                      # D never trades through: rests 15 min
             setup(t=505 * MIN, leg=(99.2, 102.8)),                      # E inside D's resting window: skipped
             setup(t=520 * MIN, leg=(99.2, 102.9))]                      # F fills at 521 and runs out of data
    sym = flat_symbol({426: (100.0, 99.95), 430: (100.0, 99.6), 446: (100.0, 99.95), 450: (102.7, 100.0),
                       517: (100.0, 99.95), 521: (100.0, 99.95)}, cands)   # 517 would fill E had it been posted
    rows = mls.run_cfg(sym, mls.Cfg())
    assert [(r['t_signal'] // MIN, r['why']) for r in rows] == [(425, 'SL'), (445, 'TP'), (520, 'TIME')]
    assert rows[0]['net_R'] == pytest.approx(rows[0]['net_bps'] / (30.0 + 8.0)) and rows[0]['net_R'] == pytest.approx(-1.0, abs=0.05)
    assert rows[1]['tp_bps'] == pytest.approx(260.0) and rows[1]['net_bps'] == pytest.approx(260.0 - 4.0)
    assert [r['t_signal'] // MIN for r in mls.run_cfg(sym, mls.Cfg(), t_hi=445 * MIN)] == [425]
    assert mls.run_cfg(sym, replace(mls.Cfg(), session=False)) == []     # every candidate is inside the London window


def test_placebos_are_seeded_and_use_the_same_bracket():
    sym = flat_symbol({426: (100.0, 99.95), 430: (100.0, 99.6)}, [setup(t=425 * MIN)])
    a = mls.run_cfg(sym, mls.Cfg(), random.Random(3))[0]['placebo_net_bps']
    b = mls.run_cfg(sym, mls.Cfg(), random.Random(3))[0]['placebo_net_bps']
    assert a == b and len(a) <= bt.N_PLACEBO


# ================================================================================= statistics and acceptance
def summ(n, mean, lcb, plc_lcb, pos):
    return {'stats': {'n': n, 'mean_net_bps': mean, 'lcb95': lcb}, 'vs_placebo': {'lcb95': plc_lcb}, 'symbols_positive': pos}


def test_acceptance_follows_section_9():
    good = [9.0, 11.0] * 75
    train = {'ABS_TIGHT': summ(120, 3.0, 1.0, 1.0, 8), 'SWP_TIGHT': summ(20, 30.0, 5.0, 5.0, 9)}
    v = mls.acceptance(train, {'ABS_TIGHT': summ(150, 10.0, 9.8, 4.0, 7)}, 39, {'ABS_TIGHT': good})
    assert v['chosen'] == 'ABS_TIGHT' and v['verdict'] == 'PASS'         # SWP has TRAIN n < 50: not eligible
    few = mls.acceptance(train, {'ABS_TIGHT': summ(60, 10.0, 9.8, 4.0, 7)}, 39, {'ABS_TIGHT': good[:60]})
    assert few['verdict'] == 'INCONCLUSIVE' and few['checks']['n>=100'] is False
    for bad in (summ(150, -1.0, -2.0, 4.0, 7), summ(150, 10.0, 9.8, -0.1, 7), summ(150, 10.0, 9.8, 4.0, 5)):
        assert mls.acceptance(train, {'ABS_TIGHT': bad}, 39, {'ABS_TIGHT': good})['verdict'] == 'FAIL'
    noise = list(np.random.default_rng(5).normal(0.5, 100.0, 150))
    assert mls.acceptance(train, {'ABS_TIGHT': summ(150, 10.0, 9.8, 4.0, 7)}, 39, {'ABS_TIGHT': noise})['verdict'] == 'FAIL'
    assert mls.acceptance({'ABS_TIGHT': summ(10, 3.0, 1.0, 1.0, 8)}, {}, 39, {})['chosen'] is None


def test_deflated_sharpe_shrinks_with_the_number_of_trials():
    x = list(np.random.default_rng(1).normal(8.0, 60.0, 200))
    one, many = mls.deflated(x, 1), mls.deflated(x, 39)
    assert 0.0 <= many < one <= 1.0
    assert mls.deflated([1.0, 2.0], 39) is None and mls.deflated([5.0] * 30, 39) is None


def test_summary_counts_exits_rate_and_symbols():
    t = bt.ms('2025-06-01')
    rows = [{'symbol': 'A', 't_fill': t, 'why': 'TP', 'gross_bps': 204.0, 'net_bps': 200.0, 'risk_bps': 32.0, 'tp_bps': 204.0},
            {'symbol': 'A', 't_fill': t + 1, 'why': 'SL', 'gross_bps': -33.0, 'net_bps': -40.0, 'risk_bps': 32.0, 'tp_bps': 204.0},
            {'symbol': 'B', 't_fill': t + 2, 'why': 'SL', 'gross_bps': -33.0, 'net_bps': -40.0, 'risk_bps': 32.0, 'tp_bps': 204.0},
            {'symbol': 'B', 't_fill': bt.ms('2026-03-01'), 'why': 'TP', 'gross_bps': 204.0, 'net_bps': 200.0, 'risk_bps': 32.0, 'tp_bps': 204.0}]
    s = mls.summarize(rows, bt.TRAIN)
    assert s['stats']['n'] == 3 and s['exits'] == {'TP': 1, 'SL': 2} and s['symbols_positive'] == 1
    assert s['stats']['mean_R'] == pytest.approx(40.0 / 40.0, abs=1e-4)  # (5 - 1 - 1) / 3 in units of risk + 8 bps
    assert s['trades_per_day'] == pytest.approx(3 / 396, abs=1e-4) and s['schedules']['zero 0/0']['mean_net_bps'] == pytest.approx(46.0)
    assert mls.summarize(rows, bt.HOLDOUT)['stats']['n'] == 1


# =================================================================================================== search
def test_mutation_changes_one_parameter_inside_its_range():
    rng, cfg = random.Random(20261114), mls.Cfg()
    for _ in range(300):
        new = mls.mutate(cfg, rng)
        diff = [k for k, v in asdict(new).items() if v != getattr(cfg, k)]
        assert len(diff) <= 1 and 'session' not in diff
        for k, (lo, hi) in mls.SPACE.items():
            v = getattr(new, k)
            assert v in (lo, hi) if isinstance(lo, (str, bool)) else lo <= v <= hi
        cfg = new
    assert mls.mutate(mls.Cfg(), random.Random(1)) == mls.mutate(mls.Cfg(), random.Random(1))
    assert mls.MacroSweep.WIDE_ZONE == (0.50, 1.00) and mls.MacroSweep.WIDE_LEG_ATR == 1.0   # candidates cover the space


def test_primary_grid_and_ablation_match_the_plan():
    assert [c.key for c in mls.PRIMARY] == ['ABS_TIGHT', 'ABS_STRUCT', 'SWP_TIGHT', 'SWP_STRUCT']
    assert [c.key for c in mls.ABLATION] == [k + '_OFF' for k in ('ABS_TIGHT', 'ABS_STRUCT', 'SWP_TIGHT', 'SWP_STRUCT')]
    c = mls.Cfg()
    assert (c.zone_lo, c.zone_hi, c.leg_atr, c.delta_z, c.clv, c.sess_h, c.hold_h, c.tp_lo, c.tp_hi) == \
           (0.705, 0.886, 2.0, 1.0, 0.5, 3.0, 24.0, 150.0, 300.0) and not c.shield and c.trail == 0.0
    assert (mls.RR_MIN, mls.FILL_BARS, mls.MIN_TRAIN_N, mls.MIN_HOLDOUT_N) == (1.8, 15, 50, 100)


def test_exit_grid_reads_the_holdout_only_for_a_cell_that_beats_the_base_on_train(monkeypatch):
    sym = flat_symbol({426: (100.0, 99.95), 430: (100.0, 99.6)}, [setup(t=425 * MIN)])
    g = mls.exit_grid([sym], mls.Cfg(), trials_before=39)                # 1970 timestamps: no TRAIN trade at all
    assert len(g['train_cells']) == 10 and g['trials_scored'] == 48 and g['holdout_best'] is None
    score = lambda syms, c: {'n': 80, 'mean': 5.0 + (3.0 if c.trail == 1.5 and not c.shield else 0.0), 'se': 1.0}
    monkeypatch.setattr(mls, 'score_train', score)
    best = mls.exit_grid([sym], mls.Cfg(), trials_before=39)['holdout_best']
    assert (best['trail'], best['shield']) == (1.5, False) and best['stats'] == {'n': 0}


def test_growth_of_a_losing_distribution_shrinks_the_account():
    g = mls.growth([-1.0, -1.0, -1.0, 2.0], trades_per_day=2.0, days=30.0, paths=200)
    assert g['fixed_0.05U']['median'] < 5.0 and g['compound_1pct']['median'] < 5.0 and g['fixed_0.05U']['p_plus_10u'] == 0.0
    assert mls.growth([], 1.0) == {'trades': 0}


# ============================================================================================ pre-registration
def test_the_plan_is_the_file_that_was_frozen():
    plan = (ROOT / mls.PLAN).read_bytes().replace(b'\r\n', b'\n')
    rows = [json.loads(l) for l in (ROOT / 'docs/research_upgrade/trial_log.jsonl').read_text(encoding='utf-8').splitlines() if l.strip()]
    frozen = [r for r in rows if r.get('kind') == 'macro-sweep-prereg']
    assert len(frozen) == 1 and frozen[0]['freeze']['sha256_lf'] == hashlib.sha256(plan).hexdigest()
