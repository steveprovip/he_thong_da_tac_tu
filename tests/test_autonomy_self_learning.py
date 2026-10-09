"""10-09 (cc): online swarm learner, parameter bandit, hot tunables, dreaming daemon, capital router, autonomy hub.
Offline; temp dirs only; the real ledger / config are never written."""
import json
import math
import os
import random
import time
from dataclasses import replace
from pathlib import Path

import pytest

from core import autonomy as au
from core import capital_router as cr
from core import dreaming_daemon as dd
from core import engine as eng
from core import micro_signals as msig
from core import micro_swarm as swarm
from core import param_bandit as pb
from core import swarm_learner as sl
from core import trend_lane as tl
from core import tunables as tn

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self, t=1_000_000.0):
        self.t = t

    def __call__(self):
        return self.t


# ============================================================================ swarm learner
def test_learner_neutral_without_data():
    L = sl.SwarmLearner()
    assert L.weights() == {'NORO': 1.0, 'LUMEN': 1.0, 'TIDAL': 1.0}
    assert L.weight('RUNE') == 1.0 and 'RUNE' not in sl.ALPHA_AGENTS


def test_learner_rewards_accurate_agent_and_penalises_noisy_one():
    L = sl.SwarmLearner(mute_streak=99)
    for _ in range(20):
        L.observe({'LUMEN': ['BUY', 0.9], 'TIDAL': ['SELL', 0.8]}, 'BUY', gross=0.5, net_bps=6.0)
    assert 1.6 < L.weight('LUMEN') <= 1.8
    assert 0.2 <= L.weight('TIDAL') < 0.4
    s = L.stats('LUMEN')
    assert s['hit_rate'] == 1.0 and s['brier'] == pytest.approx((0.95 - 1) ** 2) and s['edge_bps'] == 6.0


def test_learner_shrinks_small_samples():
    L = sl.SwarmLearner(mute_streak=99)
    L.observe({'LUMEN': ['BUY', 1.0]}, 'BUY', 1.0, 5.0)
    assert 1.0 < L.weight('LUMEN') < 1.15


def test_learner_mutes_after_three_losses_for_30_min():
    c = Clock()
    L = sl.SwarmLearner(clock=c)
    for _ in range(2):
        assert L.observe({'TIDAL': ['BUY', 0.8]}, 'BUY', -0.2, -8.0) == []
    ev = L.observe({'TIDAL': ['BUY', 0.8]}, 'BUY', -0.2, -8.0)
    assert ev and 'TIDAL muted' in ev[0] and L.weight('TIDAL') == 0.0
    c.t += 1799
    assert L.weight('TIDAL') == 0.0
    c.t += 2
    assert L.weight('TIDAL') > 0.0


def test_learner_window_is_rolling_20():
    L = sl.SwarmLearner(mute_streak=99)
    for _ in range(25):
        L.observe({'NORO': ['BUY', 0.8]}, 'BUY', 1.0, 3.0)
    assert L.stats('NORO')['n'] == 20


def test_learner_warm_start_never_starts_muted():
    rows = [{'agents': {'LUMEN': ['BUY', 0.9]}, 'side': 'BUY', 'gross': -1, 'pnl': -1, 'qty': 1, 'entry': 100}] * 5
    L = sl.SwarmLearner()
    assert L.warm_start(rows) == 5 and L.weight('LUMEN') > 0


def _sig():
    return {'FLOW': {'side': 'BUY', 'strength': 0.8, 'why': 'flow', 'edge_bps': 10.0},
            'TREND': {'side': 'BUY', 'strength': 0.7, 'why': 'trend', 'edge_bps': 8.0}}


def test_reweight_scales_caps_and_mutes():
    s = sl.reweight(_sig(), {'LUMEN': 1.8, 'NORO': 0.5}, swarm.ALPHA)
    assert s['FLOW']['strength'] == 1.0 and s['TREND']['strength'] == pytest.approx(0.35)
    m = sl.reweight(_sig(), {'LUMEN': 0.0}, swarm.ALPHA)
    assert m['FLOW'] is None and m['TREND']['strength'] == 0.7
    assert sl.reweight(_sig(), None, swarm.ALPHA) == _sig()


def test_muting_changes_swarm_vote_but_not_gates():
    g = swarm.Gates()
    d0, v0, _ = swarm.evaluate(_sig(), g, jev_min=None, min_confluence=0)
    d1, v1, _ = swarm.evaluate(sl.reweight(_sig(), {'LUMEN': 0.0}, swarm.ALPHA), g, jev_min=None, min_confluence=0)
    lum0 = next(v for v in v0 if v.agent == 'LUMEN')
    lum1 = next(v for v in v1 if v.agent == 'LUMEN')
    assert lum0.side == 'BUY' and lum1.side == 'HOLD'
    assert [v.side for v in v1 if v.agent in swarm.GATES] == ['BUY'] * 5
    assert d1.score < d0.score


# ============================================================================ bandit
def test_bandit_arms_never_raise_exposure():
    assert min(pb.ARMS['cs_super_ofi']) >= 0.85
    b = pb.ParamBandit(seed=1)
    for _ in range(50):
        c = b.select('MID')
        assert c['cs_super_ofi'] in pb.ARMS['cs_super_ofi'] and c['tp1_scaled_pct'] in (0.30, 0.40, 0.50)
        assert c['hurdle_min_hold_s'] in (30.0, 60.0, 90.0)


def test_regime_buckets():
    assert pb.regime_of(0.1) == 'CHOP' and pb.regime_of(0.35) == 'MID' and pb.regime_of(0.6) == 'TREND'
    assert pb.regime_of(None) == 'MID'


def test_bandit_learns_best_tp1_arm():
    b = pb.ParamBandit(seed=2, ban_after=99)
    rng = random.Random(3)
    for _ in range(400):
        c = b.select('TREND')
        r = (6.0 if c['tp1_scaled_pct'] == 0.5 else -6.0) + rng.gauss(0, 10)
        b.update(c, r)
    picks = [b.select('TREND')['tp1_scaled_pct'] for _ in range(100)]
    assert picks.count(0.5) > 80
    assert b.posterior('TREND', 'tp1_scaled_pct', 0.5)[0] > 0 > b.posterior('TREND', 'tp1_scaled_pct', 0.3)[0]


def test_bandit_bans_combo_after_two_losses_in_a_row():
    b = pb.ParamBandit(seed=4, ban_after=2, ban_trades=5)
    c = {'regime': 'MID', 'cs_super_ofi': 0.9, 'hurdle_min_hold_s': 60.0, 'tp1_scaled_pct': 0.4}
    assert b.update(c, -3.0) == []
    ev = b.update(c, -2.0)
    assert ev and 'ban' in ev[0]
    combo = tuple(sorted((k, v) for k, v in c.items() if k != 'regime'))
    for _ in range(5):
        s = b.select('MID')
        assert tuple(sorted((k, v) for k, v in s.items() if k != 'regime')) != combo
    assert combo not in b.banned['MID']


def test_bandit_regimes_are_independent_and_view_serialises(tmp_path):
    b = pb.ParamBandit(seed=5)
    b.update({'regime': 'CHOP', 'cs_super_ofi': 0.95, 'hurdle_min_hold_s': 30.0, 'tp1_scaled_pct': 0.3}, 4.0)
    assert b.stats['CHOP']['tp1_scaled_pct'][0.3][0] == 1 and b.stats['TREND']['tp1_scaled_pct'][0.3][0] == 0
    b.save(tmp_path / 'b.json')
    assert json.loads((tmp_path / 'b.json').read_text())['bandit']['CHOP']['tp1_scaled_pct']['0.3']['n'] == 1


# ============================================================================ tunables
BASE = eng.PROFILES['demo']


def test_validate_caps_risk_at_baseline_and_floor():
    ch, obs, w, rej = tn.validate({'profile': {'cs_max_notional': 1000, 'portfolio_risk_usdt': 0.1,
                                               'cooldown_s': 0, 'hurdle_min_hold_s': 45}}, BASE)
    assert ch['cs_max_notional'] == BASE.cs_max_notional          # never above the baseline
    assert ch['portfolio_risk_usdt'] == 0.5                         # floor
    assert ch['cooldown_s'] == BASE.cooldown_s                     # 'floor' fields never below the baseline
    assert ch['hurdle_min_hold_s'] == 45 and not rej and not obs


def test_validate_rejects_untunable_and_gate_agents():
    ch, obs, w, rej = tn.validate({'profile': {'leverage': 20, 'mode': 'live', 'tp1_scaled_pct': 0.9},
                                   'swarm_weights': {'RUNE': 0.0, 'VESKA': 0, 'LUMEN': 3.0}, 'observe': True}, BASE)
    assert ch == {} and obs is True and w == {'LUMEN': 1.8}
    assert any('leverage' in r for r in rej) and any('RUNE' in r for r in rej) and any('tp1_scaled_pct' in r for r in rej)


def test_validate_live_baseline_without_cap_is_rejected():
    ch, _, _, rej = tn.validate({'profile': {'cs_max_notional': 100}}, eng.PROFILES['live'])
    assert ch == {} and rej


def test_watcher_mtime_reload_broken_file_and_expiry(tmp_path):
    c = Clock(5000.0)
    p = tmp_path / 'live_tunables.json'
    w = tn.TunableWatcher(p, clock=c)
    assert w.poll() is False
    tn.atomic_write(p, {'version': 1, 'profile': {'cs_max_notional': 120}, 'expires_at': 6000})
    assert w.poll() is True and w.active()['version'] == 1 and w.poll() is False
    p.write_text('{broken')
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert w.poll() is False and w.error and w.active()['version'] == 1
    c.t = 6001
    assert w.active() == {}


def test_apply_profile_observer():
    p = tn.apply_profile(BASE, {'cs_max_notional': 120.0}, True, ['SOLUSDT'])
    assert p.cs_max_notional == 120.0 and 'SOLUSDT' in p.observe_only and BASE.cs_max_notional == 350.0


# ============================================================================ dreaming daemon
def _row(i, pnl, gross, fee=0.06, qty=1.0, entry=100.0, markout=None, mode='demo', **kw):
    r = {'t': 1000.0 + i, 'mode': mode, 'symbol': 'SOLUSDT', 'side': 'BUY', 'qty': qty, 'entry': entry,
         'pnl': pnl, 'gross': gross, 'fee': fee, 'fee_bps': fee / (qty * entry) * 1e4, 'reason': 'X',
         'markout_bps': {'1s': [0.0, 1.0], '5s': [markout if markout is not None else 0.0, 5.0], '30s': [1.0, 30.0]}}
    r.update(kw)
    return r


def _ledger(tmp_path, rows):
    p = tmp_path / 'trades.jsonl'
    p.write_text(''.join(json.dumps(r) + '\n' for r in rows))
    return p


def test_autopsy_decomposition():
    a = dd.autopsy(_row(0, -0.08, -0.02, fee=0.06, markout=-2.5, entry_type='IOC', signal_mid=99.9))
    assert a['net_bps'] == pytest.approx(-8.0) and a['gross_bps'] == pytest.approx(-2.0)
    assert a['fee_bps'] == pytest.approx(6.0) and a['markout_5s'] == -2.5
    assert a['slip_bps'] == pytest.approx((100 - 99.9) / 99.9 * 1e4)


def test_detect_churn_streak_adverse_fee_drag():
    churn = [dd.autopsy(_row(i, -0.05, 0.01 * (-1) ** i, fee=0.04)) for i in range(6)]
    names = [n for n, _ in dd.detect(churn)]
    assert 'CHURN' in names and 'STREAK' in names and 'FEE_DRAG' in names
    adv = [dd.autopsy(_row(i, 0.02 * (-1) ** i, 0.1, markout=-4.0)) for i in range(6)]
    assert 'ADVERSE' in [n for n, _ in dd.detect(adv)]
    ok = [dd.autopsy(_row(i, 0.5, 0.56, markout=2.0)) for i in range(6)]
    assert dd.detect(ok) == []


def test_run_once_writes_directive_lessons_state_never_ledger(tmp_path):
    rows = [_row(i, -0.05, 0.01 * (-1) ** i, fee=0.04) for i in range(6)]
    led = _ledger(tmp_path, rows)
    before = led.read_bytes()
    tun, st, les = tmp_path / 'cfg' / 'live_tunables.json', tmp_path / 'cfg' / 'dynamic_state.json', \
        tmp_path / 'brain_learnings.jsonl'
    res = dd.run_once(led, les, st, tun, now=10_000.0)
    assert led.read_bytes() == before
    d = json.loads(tun.read_text())
    assert d['observe'] is True and d['profile']['cs_max_notional'] == 100.0 and d['expires_at'] == 10_000 + 1800
    lessons = [json.loads(x) for x in les.read_text().splitlines()]
    assert {x['pattern'] for x in lessons} >= {'CHURN', 'STREAK'} and all(x['standard'] for x in lessons)
    state = json.loads(st.read_text())
    assert state['trades_seen'] == 6 and 'CHURN' in state['traps']
    # no new trades -> no new lessons; after expiry the directive is cleared back to baseline
    res2 = dd.run_once(led, les, st, tun, now=10_000.0 + 1801)
    assert res2['lessons'] == [] and res2['directive'] == 'cleared'
    assert json.loads(tun.read_text())['profile'] == {} and json.loads(tun.read_text())['observe'] is False


def test_lesson_promotion_and_decay(tmp_path):
    state = {}
    for k in range(3):
        state, new, promoted = dd.consolidate(state, [('CHURN', {'g': 1.0, 'f': 4.0, 'n': 5})], 100.0 + k)
    assert promoted == ['CHURN'] and state['lessons']['CHURN']['rule'] is True
    state, _, _ = dd.consolidate(state, [], 100.0 + dd.DECAY_S + 10)
    assert 'CHURN' not in state['lessons']


def test_daemon_ignores_live_rows(tmp_path):
    led = _ledger(tmp_path, [_row(i, -0.05, 0.0, mode='live') for i in range(6)])
    res = dd.run_once(led, tmp_path / 'l.jsonl', tmp_path / 's.json', tmp_path / 't.json', now=1.0)
    assert res['trades'] == 0 and res['traps'] == []


# ============================================================================ capital router
def test_router_regimes():
    chop = cr.route({'SOLUSDT': {'ker': 0.1, 'cvd_60s': 0}, 'DOGEUSDT': {'ker': 0.2, 'cvd_60s': 0}})
    assert chop['regime'] == 'CHOP' and chop['scalp_alloc'] == 0.0
    burst = cr.route({'SOLUSDT': {'ker': 0.6, 'cvd_60s': 150_000}, 'DOGEUSDT': {'ker': 0.2, 'cvd_60s': 0}},
                     {'SOLUSDT': 0.1})
    assert burst['regime'] == 'BURST' and burst['trend_trail'] and burst['scalp_alloc'] == 1.0
    assert cr.route({'SOLUSDT': {'ker': 0.3}, 'DOGEUSDT': {'ker': None}})['regime'] == 'NORMAL'


def test_router_never_mentions_leverage():
    src = (ROOT / 'core' / 'capital_router.py').read_text()
    assert 'leverage=' not in src and 'set_leverage' not in src


# ============================================================================ autonomy hub on a real engine
def _engines(tmp_path, autonomy='on'):
    feeds = {s: msig.MicroFeed(s, msig.config_for(s)) for s in ('SOLUSDT', 'DOGEUSDT')}
    profiles = {s: replace(eng.PROFILES['demo'], symbol=s, qty=eng.SYMBOL_QTY.get(s, 0.0), autonomy=autonomy)
                for s in feeds}
    venue = eng.MockVenue(profiles['SOLUSDT'], quote=lambda s: (100.0, 100.01))
    es = eng.build_scanner(profiles, venue, feeds, ledger=tmp_path / 'trades.jsonl')
    c = Clock()
    a = au.Autonomy(tmp_path / 'data', tmp_path / 'config', tmp_path / 'trades.jsonl', clock=c, seed=1)
    a.attach(es)
    return es, a, c


def test_hot_reload_applies_when_flat_and_restores_on_expiry(tmp_path):
    es, a, c = _engines(tmp_path)
    sol = es['SOLUSDT']
    events = []
    sol.event = events.append
    tn.atomic_write(tmp_path / 'config' / 'live_tunables.json',
                    {'version': 3, 'source': 'test', 'reason': 'unit', 'expires_at': c.t + 60,
                     'profile': {'cs_max_notional': 120}, 'observe': True})
    sol.pos = {'side': 'BUY'}                                   # open position: nothing changes mid-trade
    a.poll()
    a.refresh(sol, {'ker': 0.5})
    assert sol.p.cs_max_notional == 350.0
    assert any(au.ADAPTED in x for x in events)
    sol.pos = None
    a.refresh(sol, {'ker': 0.5})
    assert sol.p.cs_max_notional == 120.0 and sol.observer
    c.t += 61
    a.poll()
    a.refresh(sol, {'ker': 0.5})
    assert sol.p.cs_max_notional == 350.0 and not sol.observer
    assert any('baseline restored' in x for x in events)


def test_bandit_arms_applied_only_while_flat(tmp_path):
    es, a, c = _engines(tmp_path)
    sol = es['SOLUSDT']
    a.refresh(sol, {'ker': 0.6})
    arm = a.arms['SOLUSDT']
    assert arm['regime'] == 'TREND'
    assert sol.p.hurdle_min_hold_s == arm['hurdle_min_hold_s'] and sol.p.tp1_scaled_pct == arm['tp1_scaled_pct']
    assert a.entry_tags(sol)['arms'] == arm


def test_on_settle_updates_learner_bandit_and_persists(tmp_path):
    es, a, c = _engines(tmp_path)
    sol = es['SOLUSDT']
    a.refresh(sol, {'ker': 0.35})
    row = {'qty': 1.0, 'entry': 100.0, 'pnl': -0.08, 'gross': -0.02, 'side': 'BUY',
           'agents': {'LUMEN': ['BUY', 0.9], 'NORO': ['HOLD', 0.0]}, 'arms': a.arms['SOLUSDT']}
    a.on_settle(sol, row)
    assert a.learner.stats('LUMEN')['n'] == 1 and a.learner.stats('NORO')['n'] == 0
    assert sum(v[0] for v in a.bandit.stats['MID']['tp1_scaled_pct'].values()) == 1
    assert (tmp_path / 'data' / 'swarm_learner.json').exists() and (tmp_path / 'data' / 'param_bandit.json').exists()
    assert a.need_draw['SOLUSDT'] is True


def _demote_case(tmp_path, mode):
    es, a, c = _engines(tmp_path, autonomy=mode)
    sol = es['SOLUSDT']
    sol.sig = {k: {**v, 'strength': 1.0} for k, v in _sig().items()}     # 2 alpha triggers + gates = 80 %
    for _ in range(3):
        a.learner.observe({'LUMEN': ['BUY', 0.9]}, 'BUY', -1.0, -8.0)       # LUMEN muted in the learner
    g = swarm.Gates()
    ev = lambda sg: swarm.evaluate(sg, g, jev_min=None, min_confluence=0)[0]
    d = ev(sol.sig)
    return a, sol, d, ev


def test_learner_can_only_veto_never_add_or_upsize(tmp_path):
    a, sol, d, ev = _demote_case(tmp_path, 'on')
    assert d.allowed and a.weights()['LUMEN'] == 0.0
    d2 = a.demote(sol, d, ev, swarm.ALPHA)
    assert not d2.allowed and d2.reason.startswith('LEARNER VETO') and d2.score == d.score
    a.learner.muted_until['LUMEN'] = 0.0
    for _ in range(20):
        a.learner.observe({'LUMEN': ['BUY', 0.9]}, 'BUY', 1.0, 9.0)       # LUMEN up-weighted
    assert a.weights()['LUMEN'] > 1.0
    assert a.demote(sol, d, ev, swarm.ALPHA) is d                          # same decision, same score / size


def test_shadow_counts_vetoes_without_acting(tmp_path):
    a, sol, d, ev = _demote_case(tmp_path, 'shadow')
    assert a.demote(sol, d, ev, swarm.ALPHA) is d and a.shadow_disagree == 1
    p0 = sol.p
    a.refresh(sol, {'ker': 0.5})
    assert sol.p is p0                                                    # shadow: profile untouched


def test_router_hysteresis_and_dream_trigger(tmp_path):
    es, a, c = _engines(tmp_path)
    for e in es.values():
        e.features = {'ker': 0.1, 'cvd_60s': 0.0}
    assert a.route()['regime'] == 'NORMAL'                 # CHOP must persist 60 s first
    c.t += 61
    assert a.route()['regime'] == 'CHOP'
    a.refresh(es['SOLUSDT'], {'ker': 0.1})
    assert es['SOLUSDT'].observer
    for e in es.values():
        e.features = {'ker': 0.45, 'cvd_60s': 0.0}
    assert a.route()['regime'] == 'NORMAL'                 # leaving CHOP is immediate
    a.settled_since_dream = 3
    assert a.dream_due()
    res = a.dream()
    assert res['trades'] == 0 and (tmp_path / 'config' / 'dynamic_state.json').exists()


def test_tick_hook_and_ledger_tags(tmp_path):
    es, a, c = _engines(tmp_path)
    sol = es['SOLUSDT']
    assert sol.autonomy is a and a.mode(sol) == 'on'
    assert 'agents' in (ROOT / 'core' / 'engine.py').read_text()


def test_profiles_autonomy_on_demo_off_live():
    assert eng.PROFILES['demo'].autonomy == 'on' and eng.PROFILES['live'].autonomy == 'off'


def test_runtime_files_are_gitignored():
    gi = (ROOT / '.gitignore').read_text()
    assert 'config/live_tunables.json' in gi and 'config/dynamic_state.json' in gi


# ============================================================================ trend lane trailing stop
def test_chandelier_goes_flat_after_retrace_until_new_signal():
    closes = [100, 102, 104, 106, 108, 110, 112, 114, 116, 118, 120, 122, 124, 126, 128, 129, 120, 119, 118, 117]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    w = [0.2] * 18 + [-0.2, -0.2]
    out = tl.chandelier(w, highs, lows, closes, 3.0)
    assert out[15] == 0.2 and out[16] == 0.0 and out[17] == 0.0       # retrace > 3 ATR -> flat, stays flat
    assert out[18] == -0.2                                            # a fresh (opposite) signal re-enters
    assert tl.TrendConfig().trail_atr is None                         # default off: A/B worse (see report)


def test_no_stop_trap_caps_size_and_ledger_keeps_reject_reason():
    tr = [dd.autopsy(_row(0, 0.1, 0.12)), dd.autopsy(_row(1, -0.49, -0.43, reason='NO_STOP'))]
    assert ('NO_STOP' in [n for n, _ in dd.detect(tr)])
    assert dd.directive_for([('NO_STOP', {'b': -49.0})], 0.0, 1)['profile'] == {'cs_max_notional': 150.0}
    src = (ROOT / 'core' / 'engine.py').read_text()
    assert "self.pos['stop_reject']" in src and "'stop_reject': p.get('stop_reject')" in src


def test_conviction_inverted_trap():
    top = [dd.autopsy(_row(i, -0.06, -0.02, conviction={'tier': 'score>=0.95 & OFI>=0.85'})) for i in range(10)]
    low = [dd.autopsy(_row(i, 0.03 * (-1) ** i + 0.01, 0.05, conviction={'tier': 'score>=0.8'})) for i in range(10)]
    names = [n for n, _ in dd.detect(low + top)]
    assert 'CONVICTION' in names
    assert 'CONVICTION' not in [n for n, _ in dd.detect(top[:5] + low)]
