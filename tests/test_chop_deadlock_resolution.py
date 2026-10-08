"""10-08x chop deadlock: adaptive KER for super-conviction TREND entries, fixed MR bracket with net RR >= 1.35,
funnel combinations.  Behaviour tests only; the replay (tools/replay_chop_lanes.py) is negative for both levers."""
import json
import sys
from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from core import engine as eng
from core import exec_funnel as xf
from core import micro_signals as msig
from core import micro_swarm as ms
from tests.test_execution_funnel_feasibility import PROD, passing_state, prod_engine
from tests.test_multi_asset_scanner import SOL, fill_sol
from tests.test_two_mode_engine import sig

ROOT = Path(__file__).resolve().parents[1]
DEMO = eng.PROFILES['demo']
BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]


def chop_case(e, ker=0.37, score=0.99, vel=7_000.0, conf=3):
    d, diag = passing_state(e)
    d = replace(d, score=score)
    diag = {**diag, 'consensus': score, 'confluence': {'count': conf}}
    e.features.update(ker=ker, cvd_vel_10s=vel)
    e.chop_locked = True
    return d, diag


def rows(tmp_path):
    p = tmp_path / 'engine_orders.jsonl'
    return [json.loads(x) for x in p.read_text(encoding='utf-8').splitlines()] if p.exists() else []


# ============================================================================ 1. adaptive KER
@pytest.mark.parametrize('score,expect', [(0.95, 0.37), (0.99, 0.362), (1.0, 0.36), (0.90, 0.38)])
def test_formula_as_given(tmp_path, score, expect):
    e, _, _ = prod_engine(tmp_path, adaptive_ker='on')
    d, diag = chop_case(e, score=score)
    assert e.adaptive_ker(d, diag)['ker_eff'] == pytest.approx(expect, abs=1e-9)


def test_formula_floor(tmp_path):
    e, _, _ = prod_engine(tmp_path, adaptive_ker='on', adaptive_ker_slope=5.0)
    d, diag = chop_case(e, score=1.0)
    assert e.adaptive_ker(d, diag)['ker_eff'] == pytest.approx(0.15)


def test_formula_moves_the_lock_by_at_most_two_hundredths(tmp_path):
    """score <= 1 -> ker_lock - 0.20 x 0.10 = 0.38 - 0.02: KER 0.05-0.30 chop stays locked."""
    e, _, _ = prod_engine(tmp_path, adaptive_ker='on')
    for k in (0.05, 0.20, 0.30, 0.35):
        d, diag = chop_case(e, ker=k, score=1.0)
        assert not e.adaptive_ker(d, diag)['unlock']


@pytest.mark.parametrize('kw', [dict(score=0.949), dict(vel=2_999.0), dict(vel=-7_000.0), dict(conf=2), dict(ker=0.36)])
def test_every_condition_is_required(tmp_path, kw):
    e, _, _ = prod_engine(tmp_path, adaptive_ker='on')
    d, diag = chop_case(e, **kw)
    assert not e.adaptive_ker(d, diag)['unlock']
    assert 'KER' in [k for k, _ in e.churn_checks(d, diag)[0]]


def test_on_unlocks_the_trend_lane_to_submit(tmp_path):
    e, _, _ = prod_engine(tmp_path, adaptive_ker='on')
    d, diag = chop_case(e)
    ok, why = e.churn_gate(d, diag)
    assert ok and why == '' and e.ker_adapt['unlock'] and any('ADAPTIVE KER' in ev['message'] for ev in e.events)


async def test_on_reaches_a_fill(tmp_path):
    e, venue, feed = prod_engine(tmp_path, adaptive_ker='on')
    await e.sync()
    d, diag = chop_case(e)
    assert e.churn_gate(d, diag)[0]
    res = await fill_sol(e, feed, e.execute('BUY', d, 'SWARM'))
    assert res['ok'] and e.pos


def test_shadow_keeps_the_lock_and_logs_once_per_20s(tmp_path):
    e, _, _ = prod_engine(tmp_path, adaptive_ker='shadow')
    d, diag = chop_case(e)
    for _ in range(3):
        ok, why = e.churn_gate(d, diag)
        assert not ok and why.startswith('CHOP LOCKED') and 'adaptive KER 0.362 (shadow)' in why
    r = [x for x in rows(tmp_path) if x['outcome'] == 'SHADOW_KER']
    assert len(r) == 1 and r[0]['other_blockers'] == [] and r[0]['ker_eff'] == pytest.approx(0.362)


def test_shadow_row_lists_the_other_blockers(tmp_path):
    e, _, _ = prod_engine(tmp_path, adaptive_ker='shadow')
    d, diag = chop_case(e)
    diag['edge_bps'] = 10.0
    e.churn_gate(d, diag)
    assert rows(tmp_path)[-1]['other_blockers'] == ['EDGE']


def test_off_is_the_old_gate(tmp_path):
    e, _, _ = prod_engine(tmp_path, adaptive_ker='off')
    d, diag = chop_case(e)
    ok, why = e.churn_gate(d, diag)
    assert not ok and why == e.chop_reason() and not rows(tmp_path)


def test_mr_lane_is_not_touched_by_adaptive_ker(tmp_path):
    e, _, _ = prod_engine(tmp_path, adaptive_ker='on')
    d, diag = chop_case(e)
    e.sig = {'MR': {'side': 'BUY', 'strength': 1.0, 'edge_bps': 14, 'why': 'MR'}}
    assert e.lane(d) == 'MR'
    e.churn_checks(d, diag)
    assert e.ker_adapt is None


def test_defaults():
    assert DEMO.adaptive_ker == 'shadow' and eng.PROFILES['live'].adaptive_ker == 'off'
    assert (DEMO.adaptive_ker_score, DEMO.adaptive_ker_cvd_vel_usd, DEMO.adaptive_ker_confluence,
            DEMO.adaptive_ker_floor, DEMO.adaptive_ker_slope) == (0.95, 3000.0, 3, 0.15, 0.20)
    assert DEMO.mr_bracket == 'vwap' and (DEMO.mr_tp_fixed, DEMO.mr_sl_fixed) == (0.29, 0.10)


# ============================================================================ 2. MR bracket
def rr(tp, sl):
    return (tp - 0.04) / (sl + 0.08)                       # maker in + maker TP / maker in + taker stop + slip


def test_form_arithmetic_vs_real_fees():
    """Form: (0.24 - 0.04) / (0.11 + 0.04) = 1.33; the stop is a STOP_MARKET (taker 5 + slip 1 + maker in 2)."""
    assert (0.24 - 0.04) / (0.11 + 0.04) == pytest.approx(1.333, abs=1e-3)
    assert rr(0.24, 0.11) == pytest.approx(1.053, abs=1e-3)
    assert max(rr(tp, sl) for tp in (0.22, 0.24, 0.26) for sl in (0.10, 0.11, 0.12)) < 1.30
    assert rr(0.29, 0.10) >= 1.35 and rr(0.28, 0.10) < 1.35


def test_fixed_bracket_reaches_rr_135(tmp_path):
    e, _, _ = prod_engine(tmp_path, mr_bracket='fixed')
    bp = e.bracket_plan('MR', {'vwap_dev_pct': -0.13})
    assert bp['tp_pct'] == 0.29 and bp['sl_pct'] == 0.10 and bp['rr_net'] >= 1.35 and bp['src'] == 'MR-FIXED'


def test_vwap_bracket_unchanged(tmp_path):
    e, _, _ = prod_engine(tmp_path)
    bp = e.bracket_plan('MR', {'vwap_dev_pct': -0.13})
    assert bp['tp_pct'] == pytest.approx(0.13) and bp['sl_pct'] == 0.08 and bp['rr_net'] < 1.0


def test_feasibility_matrix_both_lanes(tmp_path):
    e, _, _ = prod_engine(tmp_path, mr_bracket='fixed', adaptive_ker='on')
    m = e.lane_feasibility()
    assert m['TREND']['feasible'] is True and m['MR']['feasible'] is True and m['MR']['rr_max'] >= 1.35
    e2, _, _ = prod_engine(tmp_path)
    assert e2.lane_feasibility()['MR']['feasible'] is False                  # default stays closed


def mr_state(e, side='BUY'):
    e.sig = {'MR': {'side': side, 'strength': 1.0, 'edge_bps': 14.0, 'why': 'MR'}}
    e.features = {'ofi_10s': 0.1, 'cvd_60s': 0.0, 'vwap_dev_pct': -0.13, 'atr15_pct': 0.6, 'spread_bps': 0.9,
                  'atr_pct': 0.1, 'ker': 0.1, 'px_vel_15s': 0.0}
    e.reg = {'name': 'CHOP'}
    e.chop_locked = True
    e.macro = {'name': 'MIXED', 'allow': ('BUY', 'SELL')}
    e.absorb = {'blocks': None}
    e.flow = {'buy_ok': True, 'sell_ok': True}
    d = replace(BUY, score=0.95)
    return d, {'consensus': 0.95, 'jev': 0.0, 'gates_ok': True, 'edge_bps': 14.0}


def test_mr_lane_has_a_path_through_every_gate_with_the_fixed_bracket(tmp_path):
    e, _, _ = prod_engine(tmp_path, mr_bracket='fixed')
    d, diag = mr_state(e)
    assert e.lane(d) == 'MR' and e.churn_gate(d, diag) == (True, '')
    e2, _, _ = prod_engine(tmp_path)
    d, diag = mr_state(e2)
    ok, why = e2.churn_gate(d, diag)
    assert not ok and why.startswith('NET RR')                                # default: blocked by net RR


async def test_mr_fixed_bracket_reaches_a_fill_with_rr_135(tmp_path):
    e, venue, feed = prod_engine(tmp_path, mr_bracket='fixed')
    await e.sync()
    d, _ = mr_state(e)
    import asyncio
    from tests.test_two_mode_engine import depth
    task = asyncio.ensure_future(e.execute('BUY', d, 'MR'))
    await asyncio.sleep(0.15)
    for _ in range(3):                                     # sellers sweep under the MR quote
        feed.on_message(depth(149.80, 149.81, sym=SOL))
    res = await task
    assert res['ok'] and e.pos and e.pos['path'] == 'MR'
    assert e.pos['tp_pct'] == pytest.approx(0.29) and e.pos['rr_net'] >= 1.35


# ============================================================================ funnel
def test_no_trigger_is_named():
    assert xf.swarm_key('SCANNING · no trigger') == 'NO_TRIGGER' and xf.GATE_CLASS['NO_TRIGGER'] == xf.STAT


def test_combos_after_the_swarm():
    f = xf.Funnel(clock=lambda: 0.0)
    f.record('TREND', 'CHURN', ['KER', 'VELOCITY', 'EDGE'])
    f.record('TREND', 'CHURN', ['KER', 'VELOCITY', 'EDGE'])
    f.record('TREND', 'SWARM', ['WEAK', 'KER'])
    v = f.view()['combos_after_swarm']
    assert v == [{'lane': 'TREND', 'stage': 'CHURN', 'blockers': 'KER+VELOCITY+EDGE', 'n': 2}]


# ============================================================================ replay tool
def test_replay_rr_and_simulation():
    sys.path.insert(0, str(ROOT / 'tools'))
    import replay_chop_lanes as r
    assert r.net_rr(0.29, 0.10) == pytest.approx(1.389, abs=1e-3) and r.net_rr(0.18, 0.08) == pytest.approx(0.875)
    t = np.arange(0.0, 400.0, 0.5)
    p = np.full(len(t), 100.0)
    p[3:] = 99.99                                                    # trades through a 100.00 bid at t=1.5
    p[100:] = 100.40                                                 # TP 0.29 % hit
    net, t_end = r.simulate(t, p, 'BUY', 0.0, 100.0, 0.29, 0.10, 'cons')
    assert net == pytest.approx(29 - 4) and t_end == t[100]
    p2 = np.full(len(t), 100.0)
    net, _ = r.simulate(t, p2, 'BUY', 0.0, 100.0, 0.29, 0.10, 'cons')
    assert net is None                                               # never traded through: unfilled
    p3 = p.copy()
    p3[50:] = 99.85                                                  # SL 0.10 % first
    net, _ = r.simulate(t, p3, 'BUY', 0.0, 100.0, 0.29, 0.10, 'cons')
    assert net == pytest.approx(-10 - 2 - 5 - 1)
