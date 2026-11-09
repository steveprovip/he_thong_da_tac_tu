"""Demo-only high-consensus CHOP override; all swarm, RUNE and VESKA authorization gates remain active."""
from dataclasses import replace
import json

import pytest

from core import engine as eng
from core import regime_classifier as rc
from core.consensus_engine import Vote, WEIGHTS
from tests.test_execution_funnel_feasibility import passing_state, prod_engine
from tests.test_two_mode_engine import fill_through


def setup_override(e, score=0.927):
    e.p = replace(e.p, mode='demo', swarm_chop_override=True, scalp_only=True, regime_gate='off')
    d, diag = passing_state(e)
    d = replace(d, score=score, allowed=True, veto=False, reason='AUTHORIZED', threshold=0.768,
                regime='NORMAL', blockers=())
    diag = {**diag, 'consensus': score, 'path': 'SWARM', 'confluence': {'count': 3}}
    e.features.update(ker=0.11)
    e.chop_locked = True
    votes = [Vote(agent, 'BUY', 1.0, 'ready') for agent in WEIGHTS]
    return d, votes, diag


def test_override_at_80_percent_skips_only_ker_and_uses_rr_qualified_bracket(tmp_path):
    e, _, _ = prod_engine(tmp_path, regime_gate='off')
    d, votes, diag = setup_override(e, 0.811)

    assert e.swarm_override_eligible(d, votes, diag)
    ok, why = e.churn_gate(d, diag, swarm_override=True)
    bp = e.bracket_plan('SWARM_OVERRIDE', {}, 'gtx')
    assert ok and why == '' and 'KER' not in e.blockers
    assert bp['tp_pct'] == 0.50 and bp['sl_pct'] <= 0.25 and bp['rr_net'] >= 1.70
    assert e.lane_feasibility()['SWARM_OVERRIDE']['feasible'] is True


def test_override_rejects_subthreshold_consensus_and_still_hits_ker(tmp_path):
    e, _, _ = prod_engine(tmp_path, regime_gate='off')
    d, votes, diag = setup_override(e, 0.799)

    assert not e.swarm_override_eligible(d, votes, diag)
    ok, why = e.churn_gate(d, diag)
    assert not ok and why.startswith('CHOP LOCKED KER') and 'KER' in e.blockers


def test_six_directional_votes_at_81_1_percent_clear_the_override_floor(tmp_path):
    e, _, _ = prod_engine(tmp_path, regime_gate='off')
    d, _, diag = setup_override(e, 0.811)
    support = {'NORO', 'LUMEN', 'TIDAL', 'ZEPHR', 'RUNE', 'OKAPI'}
    votes = [Vote(a, 'BUY', 0.655 if a == 'NORO' else 1.0, 'ready') if a in support
             else Vote(a, 'HOLD', 0.0, 'no directional vote') for a in WEIGHTS]

    assert sum(v.side == 'BUY' for v in votes) == 6
    assert e.swarm_override_eligible(d, votes, diag)


async def test_sideways_regime_allows_override_and_keeps_the_regime_risk_scaling(tmp_path):
    e, venue, feed = prod_engine(tmp_path, regime_gate='on')
    await e.sync()
    d, _, diag = setup_override(e, 0.811)
    e.p = replace(e.p, regime_gate='on')
    e.regime_state = rc.RegimeState(rc.SIDEWAYS, 'test', 0.0, rc.POLICIES[rc.SIDEWAYS], None, None, None, 1)

    why, policy = e.regime_check('SWARM_OVERRIDE', 'AUTO')
    assert why is None and policy is rc.POLICIES[rc.SIDEWAYS]
    result = await fill_through(e, feed, e.execute('BUY', d, 'SWARM_OVERRIDE'))
    assert result['ok'] and e.pos and e.pos['path'] == 'SWARM_OVERRIDE'
    assert e.pos['tp_pct'] == pytest.approx(0.375)
    assert e.pos['sl_pct'] == pytest.approx(0.135)
    assert e.pos['rr_net'] >= 1.30 and e.authorized == 1


async def test_demo_override_can_trade_during_clean_warmup_with_the_normal_authorization_path(tmp_path):
    e, venue, feed = prod_engine(tmp_path, regime_gate='on')
    await e.sync()
    d, votes, diag = setup_override(e, 0.811)
    e.p = replace(e.p, regime_gate='on')
    e.regime_state = rc.RegimeState(rc.UNKNOWN, 'WARMUP', None, rc.POLICIES[rc.UNKNOWN], None, None, None, 3)
    e.decision, e.votes, e.diag = d, votes, diag

    why, policy = e.regime_check('SWARM_OVERRIDE', 'AUTO', d)
    assert why is None and policy is None                 # UNKNOWN's zero-size policy is not applied to this lane
    result = await fill_through(e, feed, e.execute('BUY', d, 'SWARM_OVERRIDE'))

    assert result['ok'] and e.pos and e.authorized == 1
    assert e.pos['path'] == 'SWARM_OVERRIDE'
    row = json.loads((tmp_path / 'engine_orders.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    assert row['outcome'] == 'FILLED' and row['regime_gate'] == 'WARMUP_OVERRIDE'


@pytest.mark.parametrize('reason', ['DATA_GAP 31s', 'INVALID_INPUT', 'WARMUP after DATA_GAP 31s'])
def test_demo_override_remains_blocked_for_unknown_data_faults(tmp_path, reason):
    e, _, _ = prod_engine(tmp_path, regime_gate='on')
    d, votes, diag = setup_override(e, 0.811)
    e.p = replace(e.p, regime_gate='on')
    e.regime_state = rc.RegimeState(rc.UNKNOWN, reason, None, rc.POLICIES[rc.UNKNOWN], None, None, None, 3)
    e.decision, e.votes, e.diag = d, votes, diag

    why, policy = e.regime_check('SWARM_OVERRIDE', 'AUTO', d)

    assert why.startswith('UNKNOWN: no directional entries')
    assert policy is rc.POLICIES[rc.UNKNOWN]


@pytest.mark.parametrize('blocked_agent', ['RUNE', 'VESKA'])
def test_override_never_bypasses_rune_or_veska_veto(tmp_path, blocked_agent):
    e, _, _ = prod_engine(tmp_path, regime_gate='off')
    d, votes, diag = setup_override(e)
    votes = [replace(v, side='HOLD', veto=True) if v.agent == blocked_agent else v for v in votes]

    assert not e.swarm_override_eligible(d, votes, diag)


async def test_override_order_keeps_veska_token_and_posts_override_bracket(tmp_path):
    e, venue, feed = prod_engine(tmp_path, regime_gate='off')
    await e.sync()
    d, _, diag = setup_override(e)
    e.diag = diag
    res = await fill_through(e, feed, e.execute('BUY', d, 'SWARM_OVERRIDE'))

    assert res['ok'] and e.pos and e.pos['path'] == 'SWARM_OVERRIDE' and e.authorized == 1
    assert e.pos['tp_pct'] == pytest.approx(0.50) and e.pos['sl_pct'] <= 0.25 and e.pos['rr_net'] >= 1.70
    row = json.loads((tmp_path / 'engine_orders.jsonl').read_text(encoding='utf-8').splitlines()[-1])
    assert row['path'] == 'SWARM_OVERRIDE' and row['route'] == 'gtx' and row['outcome'] == 'FILLED'


@pytest.mark.production_profile
def test_swarm_override_is_shipped_in_demo_and_disabled_in_live():
    assert eng.PROFILES['demo'].swarm_chop_override
    assert not eng.PROFILES['live'].swarm_chop_override

