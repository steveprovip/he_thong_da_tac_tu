"""2026-11-07 Pillar 2: the regime classifier gates AUTO entries in core/engine.py and CRASH flattens (tighten only)."""
import json
import math

import pytest

from core import engine as eng
from core import micro_swarm as ms
from core import regime_classifier as rc
from core.trend_executor import TrendExecutor
from tests.test_two_mode_engine import fill_through, mock_engine, sig

BUY = ms.evaluate(sig(FLOW=('BUY', 1.0, 20), TREND=('BUY', 1.0, 20)), ms.Gates(cost_bps=4))[0]


class StubClassifier:
    """Stands in for core.regime_classifier.RegimeClassifier: every update returns the forced state."""

    def __init__(self, state):
        self.n = 0
        self.set(state)

    def set(self, state):
        self.st = rc.RegimeState(state, 'test', 0.0, rc.POLICIES[state], None, None, None, 1)

    def update(self, t, price):
        self.n += 1
        return self.st


def gated(tmp_path, state=None, mode='on', **kw):
    e, venue, feed = mock_engine(tmp_path, regime_gate=mode, **kw)
    if state is not None:
        e.regime_clf = StubClassifier(state)
        e.regime_state = e.regime_clf.st
    return e, venue, feed


def last_row(tmp_path):
    return json.loads((tmp_path / 'engine_orders.jsonl').read_text(encoding='utf-8').splitlines()[-1])


@pytest.mark.parametrize('state, path, refused', [
    (rc.CRASH, 'SWARM', True), (rc.UNKNOWN, 'SWARM', True), (rc.CRASH, 'MR', True),
    (rc.JUMPY, 'SCALP', True), (rc.SIDEWAYS, 'SWARM', True), (rc.CALM_TREND, 'MR', True),
    (rc.CALM_TREND, 'SCALP', True), (rc.SIDEWAYS, 'MR', False), (rc.SIDEWAYS, 'SCALP', False),
    (rc.CALM_TREND, 'SWARM', False), (rc.JUMPY, 'SWARM', False), (rc.JUMPY, 'MR', False),
    (rc.SIDEWAYS, 'SWARM_OVERRIDE', False), (rc.CALM_TREND, 'SWARM_OVERRIDE', False),
    (rc.JUMPY, 'SWARM_OVERRIDE', False), (rc.CRASH, 'SWARM_OVERRIDE', True),
    (rc.UNKNOWN, 'SWARM_OVERRIDE', True)])
def test_regime_check_maps_states_to_allowed_entry_styles(tmp_path, state, path, refused):
    e, _, _ = gated(tmp_path, state)
    why, pol = e.regime_check(path, 'AUTO')
    assert (why is not None) == refused
    assert pol is rc.POLICIES[state]
    assert e.regime_check(path, 'TEST') == (None, None)                 # the operator TEST button is not gated


def test_no_classifier_update_yet_is_unknown_and_shadow_never_refuses(tmp_path):
    e, _, _ = gated(tmp_path)
    assert e.regime_state is None and e.regime_check('SWARM', 'AUTO')[0].startswith('UNKNOWN')
    s, _, _ = gated(tmp_path / 's', rc.CRASH, mode='shadow')
    assert s.regime_check('SWARM', 'AUTO') == (None, None)
    off, _, _ = gated(tmp_path / 'o', mode='off')
    assert off.regime_clf is None and off.regime_check('SWARM', 'AUTO') == (None, None)


async def test_refused_entry_spends_no_token_and_is_logged(tmp_path):
    e, venue, feed = gated(tmp_path, rc.CRASH)
    await e.sync()
    issued = e.authorized
    res = await e.execute('BUY', BUY, 'SWARM')
    assert not res['ok'] and res['error'].startswith('REGIME GATE: CRASH')
    assert e.authorized == issued and not [o for o in venue.orders.values() if o['kind'] == 'ENTRY']
    assert last_row(tmp_path)['outcome'] == 'REGIME_REFUSED'


async def test_jumpy_cuts_size_by_70pct_and_refuses_below_the_venue_minimum(tmp_path):
    small, venue, feed = gated(tmp_path, rc.JUMPY)                          # 0.002 BTC x 0.3 < 0.001 minimum
    await small.sync()
    res = await small.execute('BUY', BUY, 'SWARM')
    assert not res['ok'] and 'below the venue minimum' in res['error'] and not venue.orders
    big, venue, feed = gated(tmp_path / 'b', rc.JUMPY, qty=0.01, portfolio_risk_usdt=5.0)   # 1.5x stop fits
    await big.sync()
    res = await fill_through(big, feed, big.execute('BUY', BUY, 'SWARM'))
    assert res['ok'] and big.pos['qty'] == pytest.approx(0.003)


async def test_sideways_tightens_mr_brackets(tmp_path):
    e, _, _ = gated(tmp_path, rc.SIDEWAYS)
    plain, _, _ = gated(tmp_path / 'p', mode='off')
    why, pol = e.regime_check('MR', 'AUTO')
    assert why is None and pol.stop_mult == pytest.approx(0.75) and pol.tp_mult == pytest.approx(0.75)
    b0 = plain.bracket_plan('MR', plain.feed.features(plain.clock()))['profile']
    assert b0.sl_pct * pol.stop_mult < b0.sl_pct and b0.tp_pct * pol.tp_mult < b0.tp_pct


async def test_ticks_feed_the_classifier_and_crash_flattens_the_position(tmp_path):
    e, venue, feed = gated(tmp_path, mode='on')
    real = e.regime_clf
    assert isinstance(real, rc.RegimeClassifier)
    for _ in range(3):
        await e.tick()
    assert real.n >= 1 and e.regime_state is not None and e.regime_state.state == rc.UNKNOWN   # warming up
    e.regime_clf = StubClassifier(rc.CALM_TREND)
    e.regime_state = e.regime_clf.st
    await e.sync()
    res = await fill_through(e, feed, e.execute('BUY', BUY, 'SWARM'))
    assert res['ok'] and e.pos
    e.regime_clf.set(rc.CRASH)
    await e.tick()
    assert e.pos is None
    trades = [json.loads(x) for x in (tmp_path / 'trades.jsonl').read_text(encoding='utf-8').splitlines()]
    assert trades[-1]['reason'] == 'REGIME-CRASH'


def test_trend_lane_entries_stop_while_crash_is_armed(tmp_path):
    e, _, _ = gated(tmp_path, rc.CRASH)
    assert TrendExecutor.allowed(e) == 'REGIME CRASH: circuit breaker (no trend entries)'
    e.regime_clf.set(rc.CALM_TREND)
    e.regime_state = e.regime_clf.st
    assert TrendExecutor.allowed(e) is None
    s, _, _ = gated(tmp_path / 's', rc.CRASH, mode='shadow')
    assert not s.regime_crash() and TrendExecutor.allowed(s) is None


def test_policies_still_only_tighten_after_the_jumpy_cut():
    assert rc.POLICIES[rc.JUMPY].size_mult == pytest.approx(0.3) and rc.check_policies() == []


@pytest.mark.production_profile
def test_live_profile_keeps_the_regime_gate_off():
    assert eng.PROFILES['live'].regime_gate == 'off'
    assert eng.PROFILES['demo'].regime_gate == 'on'                 # RC-1 passed (pre-registered)


def test_engine_builds_the_preregistered_v1_classifier(tmp_path):
    """RC-1 v1 'n3c' (docs/research_upgrade/snapshot_2026-11-07_regime_v1/preregistration.json)."""
    pre = json.loads((eng.ROOT / 'docs/research_upgrade/snapshot_2026-11-07_regime_v1/preregistration.json')
                     .read_text(encoding='utf-8'))
    e, _, _ = gated(tmp_path, mode='on')
    cfg = e.regime_clf.cfg
    assert (cfg.tail_z, cfg.tail_count, cfg.tail_memory_s, cfg.crash_tail_z, cfg.crash_tail_count) == (
        pre['v1']['tail_z'], pre['v1']['tail_count'], pre['v1']['tail_memory_s'], pre['v1']['crash_tail_z'],
        pre['v1']['crash_tail_count'])


def test_demo_classifier_uses_shorter_warmup_without_opening_unknown(tmp_path):
    e, _, _ = gated(tmp_path, mode='on')
    assert (e.regime_clf.cfg.trend_window_s, e.regime_clf.cfg.er_bucket_s,
            e.regime_clf.cfg.er_min_span_s) == (60.0, 5.0, 45.0)
    assert e.regime_state is None
    why, policy = e.regime_check('SWARM_OVERRIDE', 'AUTO')
    assert why.startswith('UNKNOWN') and policy is rc.POLICIES[rc.UNKNOWN]

    first_known = None
    state = None
    for t in range(100):
        price = 100.0 * math.exp(0.00002 * math.sin(t / 17.0))
        state = e.regime_clf.update(float(t), price)
        if state.state != rc.UNKNOWN and first_known is None:
            first_known = t
    assert first_known == 45 and state.state == rc.SIDEWAYS


def test_regime_warmup_telemetry_reports_elapsed_and_required_seconds(tmp_path):
    e, _, _ = gated(tmp_path, mode='on')
    now = e.clock()
    e.regime_state = rc.RegimeState(rc.UNKNOWN, 'WARMUP', None, rc.POLICIES[rc.UNKNOWN], None, None, None, 1)
    e.regime_clf.warmup_since = now - 35.0

    q = e.quant_view()

    assert q['regime_gate'] == 'on' and q['regime_state']['reason'] == 'WARMUP'
    assert q['regime_warmup_elapsed_s'] == pytest.approx(35.0, abs=0.2)   # real clock between the two reads
    assert q['regime_warmup_required_s'] == 45.0
