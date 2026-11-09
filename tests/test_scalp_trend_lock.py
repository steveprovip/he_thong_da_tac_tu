"""11-08 (i) owner brief: chop SCALP entries may not fight the macro trend or the 3-minute CVD, and the scalp bracket is
widened to an SL floor of 0.15 % with fee-true net RR >= 1.35 and a 45 s maker EJECT-BE.

Evidence (data/engine_trades.jsonl, SOLUSDT demo, 2026-11-08): the SCALP SELLs closed at 11:00:45 and 11:02:11 were
opened against CVD 3 m +245k and +61.9k (ALIGN3, only 'shadow', logged "not with SELL") while the 5 m macro read MIXED
and the CVD regime DRIFT; both hit the SIDEWAYS-scaled 0.09 % stop after 14 s and 3 s.  Behaviour tests only; no
replay or profitability claim."""
from dataclasses import replace

import pytest

from core import engine as eng
from core import invariants as inv
from core import regime_classifier as rc
from tests.test_continuous_active_trading import BUY, DIAG, SCALP_F, orders
from tests.test_two_mode_engine import fill_through, mock_engine

# qty 0.008 BTC: the half-size scalp resized for the floor (0.003) or SIDEWAYS (0.002) stays >= the venue minimum
# (0.002 at $99.8k) and under RUNE's 1 U portfolio risk.
LOCKED = dict(chop_scalper='on', qty=0.008, net_rr_min=1.30, scalp_net_rr=True, scalp_min_edge_bps=0.0,
              scalp_macro_lock=True, scalp_cvd3m_usd=50_000.0, scalp_align3=True, scalp_sl_min_pct=0.15,
              scalp_net_rr_min=1.35, scalp_floor_resize=True)
AUTH = replace(BUY, allowed=True, veto=False, reason='AUTHORIZED')
WITH_BUY = {'cvd_3m': 300_000.0, 'cvd_60s': 200_000.0, 'cvd_15s': 50_000.0, 'ofi_10s': 0.6, 'obi': 0.4}
MIXED = {'name': 'MIXED', 'allow': ('BUY', 'SELL')}


def entries(venue):
    return [o for o in venue.orders.values() if o['kind'] == 'ENTRY']


def messages(e):
    return [x['message'] for x in e.events]


# ----------------------------------------------------------------------------- Task 1: macro / CVD 3 m / ALIGN3
@pytest.mark.parametrize('ker,ofi,cvd3m', [(0.40, -0.8585, 245_000.0), (0.10, -0.79, 61_900.0)])
def test_the_two_losing_sol_sell_scalps_are_refused_on_cvd_3m(tmp_path, ker, ofi, cvd3m):
    e, _, _ = mock_engine(tmp_path, **LOCKED, scalp_ker_max=0.42)
    e.macro, e.reg = dict(MIXED), {'name': 'DRIFT', 'allow': ('BUY', 'SELL')}
    side, why = e.scalp_candidate({'ker': ker, 'spread_bps': 0.5, 'ofi_10s': ofi, 'cvd_3m': cvd3m})
    assert side is None and why == f'CVD_3M conflict ({cvd3m:+.0f}) with SELL'


@pytest.mark.parametrize('macro,reg,why', [
    ({'name': 'LONG ONLY', 'allow': ('BUY',)}, {}, 'MACRO LONG ONLY forbids SELL scalp'),
    (MIXED, {'name': 'UP', 'allow': ('BUY',), 'label': 'STRONG UPTREND (LONG ONLY)'},
     'MACRO STRONG UPTREND (LONG ONLY) forbids SELL scalp')])
def test_a_scalp_may_not_fight_the_macro_trend(tmp_path, macro, reg, why):
    e, _, _ = mock_engine(tmp_path, **LOCKED)
    e.macro, e.reg = macro, reg
    assert e.scalp_candidate({**SCALP_F, 'ofi_10s': -0.6, 'cvd_3m': -100_000.0}) == (None, why)
    assert e.scalp_candidate({**SCALP_F, 'cvd_3m': 100_000.0})[0] == 'BUY'          # the trend side still scalps


def test_cvd_3m_lock_is_symmetric_with_a_50k_dead_band(tmp_path):
    e, _, _ = mock_engine(tmp_path, **LOCKED)
    sell = {**SCALP_F, 'ofi_10s': -0.6}
    assert e.scalp_candidate({**SCALP_F, 'cvd_3m': -60_000.0}) == (None, 'CVD_3M conflict (-60000) with BUY')
    assert e.scalp_candidate({**sell, 'cvd_3m': 40_000.0})[0] == 'SELL'              # inside +-$50k: ALIGN3 decides
    assert e.scalp_candidate({**SCALP_F, 'cvd_3m': -40_000.0})[0] == 'BUY'
    assert e.scalp_candidate(sell)[0] == 'SELL'                     # CVD 3 m UNKNOWN: refused by ALIGN3 in 'on'
    off, _, _ = mock_engine(tmp_path / 'off', **{**LOCKED, 'scalp_cvd3m_usd': 0.0, 'scalp_macro_lock': False})
    off.macro = {'name': 'LONG ONLY', 'allow': ('BUY',)}
    assert off.scalp_candidate({**sell, 'cvd_3m': 245_000.0})[0] == 'SELL'          # both switches off: pre-11-08


async def test_on_scalp_needs_every_align3_layer_even_when_align3_is_shadow(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **LOCKED, align3='shadow')
    await e.sync()
    e.diag = dict(DIAG)
    await e.chop_scalp(BUY, {**feed.features(), **SCALP_F, **WITH_BUY, 'cvd_15s': -5_000.0})
    assert e.pos is None and e.authorized == 0 and not entries(venue)
    assert e.funnel.outcomes['ALIGN3_LOCK'] == 1
    assert 'SCALP LOCK: ALIGN3 cvd_15s -5e+03 not with BUY' in messages(e)


async def test_macro_refusals_are_counted_before_any_token_and_logged_once_a_minute(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **LOCKED)
    await e.sync()
    e.diag = dict(DIAG)
    e.macro = {'name': 'SHORT ONLY', 'allow': ('SELL',)}
    f = {**feed.features(), **SCALP_F, **WITH_BUY}
    await e.chop_scalp(BUY, f)
    e.scalp_next = 0.0                                                   # skip the 20 s scalp throttle
    await e.chop_scalp(BUY, f)
    assert e.funnel.outcomes['MACRO_LOCK'] == 2 and e.pos is None and e.authorized == 0 and not entries(venue)
    assert messages(e).count('SCALP LOCK: MACRO SHORT ONLY forbids BUY scalp') == 1


async def test_shadow_scalps_keep_research_rows_without_the_align3_lock(tmp_path):
    e, _, feed = mock_engine(tmp_path, **{**LOCKED, 'chop_scalper': 'shadow'})
    await e.sync()
    await e.chop_scalp(BUY, {**feed.features(), **SCALP_F, **WITH_BUY, 'obi': -0.2})
    assert [r['outcome'] for r in orders(tmp_path)] == ['SHADOW_SCALP'] and e.funnel.outcomes['ALIGN3_LOCK'] == 0


# ----------------------------------------------------------------------------- Task 2: SL >= 0.15 %, net RR >= 1.35
async def test_aligned_on_scalp_opens_with_the_widened_bracket_and_45s_eject(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **LOCKED, align3='shadow')
    await e.sync()
    e.diag = dict(DIAG)
    e.align3_last = 'ALIGN3 cvd_3m -1e+05 not with SELL'          # stale churn check for the other side
    await fill_through(e, feed, e.chop_scalp(BUY, {**feed.features(), **SCALP_F, **WITH_BUY}))
    p = e.pos
    assert p and p['path'] == 'SCALP' and e.authorized == 1 and e.funnel.outcomes['ALIGN3_LOCK'] == 0
    assert p['sl_pct'] == pytest.approx(0.15) and p['tp_pct'] == pytest.approx(0.3505)
    assert p['rr_net'] == pytest.approx(1.35) and p['eject_s'] == 45.0
    assert p['align3'] == 'OK'                                    # the ledger records the scalp's own alignment
    assert all(x['maker'] for x in venue.fills)


def test_scalp_bracket_floors_the_stop_and_lifts_tp_to_net_rr_1_35(tmp_path):
    e, _, _ = mock_engine(tmp_path, **LOCKED)
    bp = e.bracket_plan('SCALP', {})
    assert bp['sl_pct'] == pytest.approx(0.15) and bp['tp_pct'] == pytest.approx(0.3505)  # (0.15 + 0.08) x 1.35 + 0.04
    assert bp['rr_net'] == pytest.approx(1.35) and e.scalp_rr_min() == 1.35
    wide, _, _ = mock_engine(tmp_path / 'w', **LOCKED, scalp_sl_pct=0.20, scalp_tp_pct=0.50)
    bw = wide.bracket_plan('SCALP', {})
    assert (bw['sl_pct'], bw['tp_pct']) == (0.20, 0.50) and bw['rr_net'] > 1.35       # wider configs pass through


async def test_sideways_scaling_cannot_tighten_the_scalp_stop_below_0_15(tmp_path):
    e, _, feed = mock_engine(tmp_path, **LOCKED)
    e.p = replace(e.p, regime_gate='on')
    e.regime_state = rc.RegimeState(rc.SIDEWAYS, 'test', 0.0, rc.POLICIES[rc.SIDEWAYS], None, None, None, 1)
    await e.sync()
    d = replace(BUY, allowed=True, veto=False, reason='AUTHORIZED')
    result = await fill_through(e, feed, e.execute('BUY', d, 'SCALP'))
    assert result['ok'] and e.pos and e.pos['path'] == 'SCALP'
    assert e.pos['sl_pct'] == pytest.approx(0.15)                         # 0.15 x 0.75 = 0.1125 -> floor 0.15
    assert e.pos['tp_pct'] == pytest.approx(0.3505) and e.pos['rr_net'] == pytest.approx(1.35)
    assert e.pos['scalp_risk_mult'] == pytest.approx((0.09 + 0.08) / (0.15 + 0.08))   # x0.739 vs the old 0.09 % stop


async def _scalp(tmp_path, **prof):
    e, venue, feed = mock_engine(tmp_path, **{**LOCKED, **prof})
    await e.sync()
    return e, venue, await fill_through(e, feed, e.execute('BUY', AUTH, 'SCALP'))


async def test_floored_scalp_shrinks_size_to_keep_the_unfloored_loss_at_the_stop(tmp_path):
    e1, _, r1 = await _scalp(tmp_path / 'floor')
    e0, _, r0 = await _scalp(tmp_path / 'nofloor', scalp_sl_min_pct=0.0)
    assert r1['ok'] and r0['ok']
    p1, p0, step = e1.pos, e0.pos, e1.spec['step']
    assert p1['sl_pct'] == pytest.approx(0.15) and p0['sl_pct'] == pytest.approx(0.12)
    m = (0.12 + 0.08) / (0.15 + 0.08)                                     # loss at the stop, fees in, plan()
    assert p1['scalp_risk_mult'] == pytest.approx(m) and p0['scalp_risk_mult'] == 1.0
    assert p1['qty'] == pytest.approx(eng.floor_step(p0['qty'] * m, step))
    loss1, loss0 = p1['qty'] * (0.15 + 0.08), p0['qty'] * (0.12 + 0.08)   # % of the same entry price
    assert loss0 - step * 0.23 < loss1 <= loss0 + 1e-12                   # never more, at most one step less


async def test_a_floored_scalp_below_the_venue_minimum_is_refused_before_any_token(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **LOCKED)
    await e.sync()
    e.p = replace(e.p, qty=2 * e.min_qty(feed.bid))                       # half size = the venue minimum
    res = await e.execute('BUY', AUTH, 'SCALP')
    assert not res['ok'] and res['error'].startswith('SCALP SIZE: stop 0.120% -> floor 0.150% gives size x0.870')
    assert e.authorized == 0 and not entries(venue) and e.pos is None


def test_feasibility_reports_the_scalp_lane_against_net_rr_1_35(tmp_path):
    e, _, _ = mock_engine(tmp_path, **LOCKED)
    s = e.lane_feasibility()['SCALP']
    assert (s['state'], s['gate'], s['feasible']) == ('ENABLED', 1.35, True) and s['rr_max'] == pytest.approx(1.35)
    assert 'TP 0.3505 % / SL 0.15 %' in s['why'] and 'vs net RR 1.35' in s['why']


@pytest.mark.production_profile
def test_shipped_demo_locks_the_scalp_and_keeps_the_invariants(tmp_path):
    d, live = eng.PROFILES['demo'], eng.PROFILES['live']
    assert (d.scalp_macro_lock, d.scalp_cvd3m_usd, d.scalp_align3) == (True, 50_000.0, True)
    assert (d.scalp_sl_min_pct, d.scalp_net_rr_min, d.scalp_be_s, d.scalp_floor_resize) == (0.15, 1.35, 45.0, True)
    assert d.chop_scalper == 'on' and live.chop_scalper == 'off'                     # live never scalps
    assert inv.SUPERMAJORITY == 0.768 and inv.VETO == 0.70
    e, _, _ = mock_engine(tmp_path)                                                  # shipped demo profile
    bp = e.bracket_plan('SCALP', {})
    assert bp['sl_pct'] == pytest.approx(0.15) and bp['tp_pct'] == pytest.approx(0.3505)
    assert bp['rr_net'] == pytest.approx(1.35)
