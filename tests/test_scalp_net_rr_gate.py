"""11-07 (e) owner decision: chop SCALP entries pass the ZEPHR net-RR gate (Profile net_rr_min) instead of being exempt.

The old bracket (TP 0.08 % / SL 0.12 %, maker 2 / taker 5 / slip 1 bps) nets RR 0.20. The owner-requested default
TP 0.32 % nets RR 1.40 with the route-specific maker take-profit and taker stop costs. Behaviour tests only."""
from dataclasses import replace

import pytest

from core import engine as eng
from core import regime_classifier as rc
from tests.test_continuous_active_trading import BUY, DIAG, SCALP_F, orders
from tests.test_two_mode_engine import fill_through, mock_engine

GATED = dict(chop_scalper='on', qty=0.004, net_rr_min=1.30, scalp_net_rr=True, scalp_min_edge_bps=0.0)


def entries(venue):
    return [o for o in venue.orders.values() if o['kind'] == 'ENTRY']


async def test_shipped_scalp_bracket_is_refused_before_any_token(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **GATED, scalp_tp_pct=0.08)
    await e.sync()
    e.diag = dict(DIAG)                                            # modeled edge 40 bps: the edge floor is not the cause
    await e.chop_scalp(BUY, {**feed.features(), **SCALP_F})
    assert e.pos is None and e.authorized == 0 and not entries(venue) and orders(tmp_path) == []
    assert e.funnel.outcomes['RR_LOW'] == 1
    msgs = [x['message'] for x in e.events]
    assert ('ZEPHR NET RR 0.20 < 1.3: SCALP BUY TP 0.080% / SL 0.120% after fees (break-even win rate 83%)'
            in msgs)


async def test_a_bracket_that_clears_net_rr_still_scalps(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **GATED)   # shipped default: (0.32 - 0.04) / (0.12 + 0.08) = 1.40
    await e.sync()
    e.diag = dict(DIAG)
    assert e.bracket_plan('SCALP', {})['rr_net'] == pytest.approx(1.40)
    await fill_through(e, feed, e.chop_scalp(BUY, {**feed.features(), **SCALP_F}))
    assert e.pos and e.pos['path'] == 'SCALP' and e.authorized == 1 and e.funnel.outcomes['RR_LOW'] == 0


async def test_sideways_scalp_keeps_the_minimum_net_rr_after_policy_scaling(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **GATED)
    e.p = replace(e.p, regime_gate='on')
    e.regime_state = rc.RegimeState(rc.SIDEWAYS, 'test', 0.0, rc.POLICIES[rc.SIDEWAYS], None, None, None, 1)
    await e.sync()
    d = replace(BUY, allowed=True, veto=False, reason='AUTHORIZED')

    result = await fill_through(e, feed, e.execute('BUY', d, 'SCALP'))

    assert result['ok'] and e.pos and e.pos['path'] == 'SCALP'
    assert e.pos['sl_pct'] == pytest.approx(0.09)
    assert e.pos['tp_pct'] == pytest.approx(0.261)
    assert e.pos['rr_net'] == pytest.approx(1.30)


async def test_order_path_refuses_a_low_rr_scalp_and_the_flag_restores_the_exemption(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **GATED, scalp_tp_pct=0.08)
    await e.sync()
    res = await e.execute('BUY', replace(BUY, allowed=True, veto=False, reason='t'), 'SCALP')
    assert not res['ok'] and res['error'].startswith('ZEPHR NET RR 0.20 < 1.3') and e.authorized == 0
    e2, venue2, feed2 = mock_engine(tmp_path / 'b', **{**GATED, 'scalp_net_rr': False})
    await e2.sync()
    res2 = await fill_through(e2, feed2, e2.execute('BUY', replace(BUY, allowed=True, veto=False, reason='t'), 'SCALP'))
    assert res2['ok'] and e2.pos['path'] == 'SCALP'               # pre-11-07 behaviour: SCALP exempt


async def test_shadow_scalper_keeps_recording_research_rows(tmp_path):
    e, venue, feed = mock_engine(tmp_path, **{**GATED, 'chop_scalper': 'shadow'})
    await e.sync()
    e.diag = dict(DIAG)
    await e.chop_scalp(BUY, {**feed.features(), **SCALP_F})
    assert [r['outcome'] for r in orders(tmp_path)] == ['SHADOW_SCALP'] and e.funnel.outcomes['RR_LOW'] == 0


def test_feasibility_matrix_shows_the_gated_scalp_lane(tmp_path):
    e, _, _ = mock_engine(tmp_path, **GATED, scalp_tp_pct=0.08)
    s = e.lane_feasibility()['SCALP']
    assert (s['state'], s['gate'], s['feasible']) == ('ENABLED', 1.30, False)
    assert s['rr_max'] == pytest.approx(0.2) and 'vs net RR 1.3' in s['why']
    e2, _, _ = mock_engine(tmp_path / 'b', **{**GATED, 'scalp_net_rr': False})
    s2 = e2.lane_feasibility()['SCALP']
    assert s2['gate'] is None and s2['feasible'] is True and 'exempt from net RR' in s2['why']


@pytest.mark.production_profile
def test_shipped_profiles_gate_scalps_on_net_rr():
    d, live = eng.PROFILES['demo'], eng.PROFILES['live']
    assert d.scalp_net_rr and live.scalp_net_rr and d.net_rr_min == live.net_rr_min == 1.30
    assert d.chop_scalper == 'on' and live.chop_scalper == 'off'
    assert d.scalp_tp_pct == live.scalp_tp_pct == 0.32 and d.scalp_sl_pct == live.scalp_sl_pct == 0.12
