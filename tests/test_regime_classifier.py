"""2026-11-07: 4-state regime classifier, its permit wiring and its market-risk escalation can only tighten."""
import itertools
import math
from dataclasses import replace

import numpy as np
import pytest

from core import invariants as inv
from core.consensus_engine import WEIGHTS, ConsensusEngine, TokenAuthority
from core.early_stop import REGIME_UNPROFITABLE
from core.market_risk import GarchTailRisk, MarketRisk, escalate
from core.regime_classifier import (CALM_TREND, CRASH, JUMPY, MEAN_REVERSION, POLICIES, SIDEWAYS, STATES,
                                    TREND, UNKNOWN, RegimeClassifier, RegimeConfig, apply_policy,
                                    check_policies, classify_series, policy_for)
from core.regime_permit import CARRY, SCALP, STAND_ASIDE, fast_hands_permit


def prices_from_bps(steps_bps, start=100.0):
    return list(start * np.exp(np.cumsum(np.asarray(steps_bps, dtype=float)) / 1e4))


def zigzag(n, amp_bps=1.0):
    return [amp_bps if i % 2 else -amp_bps for i in range(n)]


def run(steps_bps, t0=0.0, clf=None, dt=1.0):
    clf = clf or RegimeClassifier()
    pxs = prices_from_bps(steps_bps)
    states = [clf.update(t0 + i * dt, p) for i, p in enumerate(pxs)]
    return clf, states


# ------------------------------------------------------------------ policies
def test_policies_only_tighten_and_match_the_brief():
    assert check_policies() == []
    assert POLICIES[JUMPY].size_mult == 0.3 and POLICIES[JUMPY].stop_mult == 1.5    # owner 11-07: -70 %
    assert POLICIES[JUMPY].edge_mult == 1.5
    assert POLICIES[CRASH].flatten and not POLICIES[CRASH].directional and POLICIES[CRASH].carry
    assert POLICIES[SIDEWAYS].styles == (MEAN_REVERSION,) and POLICIES[SIDEWAYS].stop_mult < 1.0
    assert POLICIES[CALM_TREND].styles == (TREND,)
    assert not POLICIES[UNKNOWN].directional
    for p in POLICIES.values():
        assert p.size_mult <= 1.0 and p.size_mult * p.stop_mult <= 1.0 and p.edge_mult >= 1.0


def test_check_policies_catches_a_loosened_state():
    looser = dict(POLICIES, JUMPY=replace(POLICIES[JUMPY], size_mult=1.0))     # risk at stop 1.0 x 1.5
    assert any('risk at the stop' in v for v in check_policies(looser))
    assert check_policies(dict(POLICIES, CRASH=replace(POLICIES[CRASH], flatten=False)))
    assert check_policies(dict(POLICIES, SIDEWAYS=replace(POLICIES[SIDEWAYS], edge_mult=0.9)))


def test_policy_lookup_ignores_custom_objects_and_unknown_names():
    rogue = replace(POLICIES[JUMPY], size_mult=3.0)
    assert policy_for(rogue) is POLICIES[JUMPY]
    assert policy_for('NOT_A_STATE') is POLICIES[UNKNOWN] and policy_for(None) is POLICIES[UNKNOWN]
    assert policy_for(42) is POLICIES[UNKNOWN]


def test_apply_policy_cuts_jumpy_size_and_zeroes_closed_states():
    q, sl, tp = apply_policy(JUMPY, 2.0, 20.0, 30.0)
    assert (q, sl, tp) == (pytest.approx(0.6), 30.0, 30.0) and q * sl < 2.0 * 20.0
    assert apply_policy(CRASH, 2.0, 20.0, 30.0)[0] == 0.0
    assert apply_policy(UNKNOWN, 2.0, 20.0, 30.0)[0] == 0.0
    assert apply_policy(SIDEWAYS, 2.0, 20.0, 30.0) == (2.0, 15.0, 22.5)
    with pytest.raises(ValueError):
        apply_policy(JUMPY, float('nan'), 20.0, 30.0)


def test_config_rejects_inverted_bands():
    with pytest.raises(ValueError):
        RegimeConfig(er_lock=0.5, er_unlock=0.4)
    with pytest.raises(ValueError):
        RegimeConfig(jumpy_vol_ratio=3.5)
    with pytest.raises(ValueError):
        RegimeConfig(crash_move_bps=400.0)


# ------------------------------------------------------------------ state machine
def test_warmup_is_unknown_then_chop_is_sideways_and_drift_is_calm_trend():
    clf, states = run(zigzag(400))
    assert states[0].state == UNKNOWN and states[300].state == UNKNOWN      # efficiency span not filled
    _, states = run(zigzag(1200), clf=RegimeClassifier())
    assert states[-1].state == SIDEWAYS and states[-1].efficiency < 0.38
    rng = np.random.default_rng(3)
    _, states = run(0.4 + rng.normal(0.0, 0.2, 1200))
    assert states[-1].state == CALM_TREND and states[-1].efficiency > 0.9
    assert states[-1].vol_ratio < 1.8


def test_vol_spike_is_jumpy_at_once_and_relief_waits_for_confirm():
    clf, _ = run(zigzag(1200))
    assert clf.state == SIDEWAYS
    t = 1200.0
    seen = []
    for i, p in enumerate(prices_from_bps(zigzag(40, 8.0), start=clf._sec_px)):
        seen.append(clf.update(t + i, p).state)
    assert JUMPY in seen and CRASH not in seen
    first = seen.index(JUMPY)
    assert all(s == JUMPY for s in seen[first:])
    t += 40
    calm = [clf.update(t + i, p) for i, p in enumerate(prices_from_bps(zigzag(900), start=clf._sec_px))]
    left = next(i for i, s in enumerate(calm) if s.state != JUMPY)
    assert left >= clf.cfg.confirm_s                                    # looser only after the confirm window
    assert calm[-1].state == SIDEWAYS


def test_crash_is_immediate_holds_through_cooldown_and_steps_down_via_jumpy():
    clf, _ = run(zigzag(1200))
    t0 = 1200.0
    drop = [clf.update(t0 + i, p) for i, p in enumerate(prices_from_bps([-6.0] * 60, start=clf._sec_px))]
    assert drop[-1].state == CRASH and drop[-1].policy.flatten and not drop[-1].policy.directional
    crash_at = next(s.since for s in drop if s.state == CRASH)
    t1 = t0 + 60
    after = [clf.update(t1 + i, p) for i, p in enumerate(prices_from_bps(zigzag(2400, 0.5), start=clf._sec_px))]
    out = next(i for i, s in enumerate(after) if s.state != CRASH)
    assert after[out].state == JUMPY
    assert t1 + out - crash_at >= clf.cfg.crash_cooldown_s + clf.cfg.crash_window_s - 60
    assert after[-1].state == SIDEWAYS


def test_crash_needs_vol_or_tail_below_the_hard_move():
    cfg = RegimeConfig()
    clf, _ = run(zigzag(1200))
    # 200 bps in 5 min at a steady pace: above crash_move_bps, below the hard move, no vol spike, no tail
    st = [clf.update(1200.0 + i, p) for i, p in enumerate(prices_from_bps([-200.0 / 290] * 290, start=clf._sec_px))]
    assert cfg.crash_move_bps < max(s.move_bps for s in st) < cfg.crash_hard_move_bps
    assert max(s.vol_ratio for s in st) < cfg.crash_vol_ratio
    assert all(s.state != CRASH for s in st)
    # the same drift through +-10 bps noise (tails, vol ratio > 3) is a CRASH once the move passes 150 bps
    clf, _ = run(zigzag(1200))
    noisy = np.asarray([-200.0 / 290] * 290) + np.asarray(zigzag(290, 10.0))
    st = [clf.update(1200.0 + i, p) for i, p in enumerate(prices_from_bps(noisy, start=clf._sec_px))]
    hit = next(s for s in st if s.state == CRASH)
    assert hit.move_bps >= cfg.crash_move_bps and hit.move_bps < cfg.crash_hard_move_bps
    assert 'TAIL' in hit.reason or 'VOL' in hit.reason


def test_data_gap_is_unknown_and_resets_windows_but_crash_holds():
    clf, _ = run(zigzag(1200))
    assert clf.state == SIDEWAYS
    st = clf.update(clf.last_t + 120.0, clf._sec_px)
    assert st.state == UNKNOWN and 'DATA_GAP' in st.reason
    clf, _ = run(zigzag(1200))
    for i, p in enumerate(prices_from_bps([-6.0] * 60, start=clf._sec_px)):
        clf.update(1200.0 + i, p)
    assert clf.state == CRASH
    assert clf.update(clf.last_t + 120.0, clf._sec_px).state == CRASH
    assert clf.update(clf.last_t + 1.0, float('nan')).state == CRASH
    assert clf.update(clf.last_t - 5.0, 100.0).state == CRASH


def test_invalid_input_fails_closed():
    clf, _ = run(zigzag(1200))
    assert clf.update(clf.last_t + 1, -1.0).state == UNKNOWN
    clf, _ = run(zigzag(1200))
    assert clf.update(clf.last_t - 1, 100.0).state == UNKNOWN


def test_external_toxic_market_risk_is_at_least_jumpy():
    clf, _ = run(zigzag(1200))
    toxic = MarketRisk('TOXIC', False, 'BOOK_SKEW_VPIN_TOXIC', 2.0, 1.0, 0.1, 0.97, 100)
    st = clf.update(clf.last_t + 1, clf._sec_px, market_risk=toxic)
    assert st.state == JUMPY and 'MARKET_RISK TOXIC' in st.reason
    assert clf.update(clf.last_t + 1, clf._sec_px, market_risk=toxic.as_dict()).state == JUMPY


def test_classifier_is_causal_prefix_invariant():
    rng = np.random.default_rng(11)
    steps = np.r_[rng.normal(0, 1.5, 1500), np.full(50, -7.0), rng.normal(0, 3.0, 1500)]
    full = classify_series(np.arange(steps.size, dtype=float), prices_from_bps(steps))
    for k in (700, 1520, 2400):
        part = classify_series(np.arange(k, dtype=float), prices_from_bps(steps[:k]))
        assert [s.as_dict() for s in part] == [s.as_dict() for s in full[:k]]


def test_minute_bars_work_with_a_matching_stale_window():
    rng = np.random.default_rng(5)
    states = classify_series(np.arange(300) * 60.0, prices_from_bps(rng.normal(0, 8, 300)),
                             RegimeConfig(stale_s=180.0))
    assert states[-1].state in (SIDEWAYS, CALM_TREND, JUMPY)
    assert all('DATA_GAP' not in s.reason for s in states)


# ------------------------------------------------------------------ regime_permit wiring
GRID = [dict(spread_bps=s, atr_bps=a, oos_gross_bps=g, oos_gross_lcb_bps=lcb, lab_status=lab,
             funding_bps_per_period=f, funding_periods=n)
        for s in (1.0, 3.0, 9.0) for a in (5.0, 10.0, 25.0)
        for g, lcb in ((None, None), (8.0, 7.0), (10.0, 9.0), (12.0, 10.0), (14.0, 13.0))
        for lab in (None, REGIME_UNPROFITABLE) for f, n in ((0.0, 0), (10.0, 6))]


def test_permit_without_regime_is_bit_identical():
    for k in GRID:
        assert fast_hands_permit(**k) == fast_hands_permit(**k, market_regime=None, style=None)


def test_regime_permit_never_grants_what_legacy_denies_and_keeps_carry():
    for k in GRID:
        legacy = fast_hands_permit(**k)
        for state, style in itertools.product(STATES, (None, TREND, MEAN_REVERSION)):
            out = fast_hands_permit(**k, market_regime=state, style=style)
            assert not out.allowed or legacy.allowed
            if legacy.mode == CARRY:
                assert out == legacy                                   # carry keeps its own G > 3C gate
            if state in (CRASH, UNKNOWN) and legacy.mode != CARRY:
                assert not out.allowed


def test_regime_permit_raises_the_jumpy_hurdle_and_enforces_styles():
    ok = dict(spread_bps=3.0, atr_bps=10.0, oos_gross_bps=12.0, oos_gross_lcb_bps=10.0)
    assert fast_hands_permit(**ok).mode == SCALP
    jumpy = fast_hands_permit(**ok, market_regime=JUMPY, style=TREND)
    assert not jumpy.allowed and '13.5' in jumpy.reason
    strong = dict(ok, oos_gross_bps=14.0, oos_gross_lcb_bps=13.0)
    assert fast_hands_permit(**strong, market_regime=JUMPY, style=MEAN_REVERSION).mode == SCALP
    assert fast_hands_permit(**ok, market_regime=CALM_TREND, style=TREND) == fast_hands_permit(**ok)
    side = fast_hands_permit(**ok, market_regime=SIDEWAYS, style=TREND)
    assert side.mode == STAND_ASIDE and 'style TREND' in side.reason
    assert fast_hands_permit(**ok, market_regime=SIDEWAYS, style=MEAN_REVERSION).mode == SCALP
    assert 'UNKNOWN' in fast_hands_permit(**ok, market_regime=CALM_TREND).reason
    crash = fast_hands_permit(**ok, market_regime=CRASH, style=TREND)
    assert crash.mode == STAND_ASIDE and 'REGIME CRASH' in crash.reason
    carry = fast_hands_permit(**ok, funding_bps_per_period=10.0, funding_periods=6, market_regime=CRASH)
    assert carry.allowed and carry.mode == CARRY


def test_permit_accepts_a_live_regime_state():
    clf, _ = run(zigzag(1200))
    st = clf.last
    assert st.state == SIDEWAYS
    ok = dict(spread_bps=3.0, atr_bps=10.0, oos_gross_bps=12.0, oos_gross_lcb_bps=10.0)
    assert fast_hands_permit(**ok, market_regime=st, style=MEAN_REVERSION).mode == SCALP


# ------------------------------------------------------------------ market_risk wiring
SEVERITY = {'CALM': 0, 'ELEVATED': 1, 'TOXIC': 2}


def test_escalate_only_adds_risk():
    for base_regime, allowed, state in itertools.product(('UNKNOWN', 'CALM', 'ELEVATED', 'TOXIC'), (True, False),
                                                         STATES + (None, 'garbage')):
        base = MarketRisk(base_regime, allowed, 'BASE', 2.0, 1.0, 0.0, 0.0, 50)
        out = escalate(base, state)
        assert out.allowed <= base.allowed
        if base_regime in SEVERITY and out.regime in SEVERITY:
            assert SEVERITY[out.regime] >= SEVERITY[base_regime]
        if state == CRASH:
            assert out.regime == 'TOXIC' and not out.allowed
        elif state == JUMPY and base_regime == 'CALM':
            assert out.regime == 'ELEVATED' and out.allowed == base.allowed
        else:
            assert out == base


def test_garch_assess_with_regime_none_is_unchanged_and_crash_vetoes():
    risk = GarchTailRisk(warmup=10)
    assert risk.assess(book_skew=0.0, vpin=None).regime == 'UNKNOWN'
    for _ in range(10):
        risk.update(0.0)
    calm = risk.assess(book_skew=0.0, vpin=0.1)
    assert calm.regime == 'CALM' and calm == risk.assess(book_skew=0.0, vpin=0.1, regime=None)
    assert risk.assess(book_skew=0.95, vpin=0.70).regime == 'ELEVATED'
    vetoed = risk.assess(book_skew=0.0, vpin=0.1, regime=CRASH)
    assert not vetoed.allowed and vetoed.reason == 'REGIME_CRASH'
    assert risk.assess(book_skew=0.0, vpin=0.1, regime=JUMPY).regime == 'ELEVATED'
    risk.update(20.0)
    tail = risk.assess(book_skew=0.0, vpin=0.1)
    assert tail.regime == 'TOXIC' and not tail.allowed and tail.reason == 'GARCH_TAIL'


def test_consensus_quorum_rune_veto_and_token_are_untouched():
    assert inv.weights_sha256() == inv.WEIGHTS_SHA256
    assert inv.check_consensus(ConsensusEngine(trend_floor=.768)) == []
    assert inv.check_token_authority(TokenAuthority()) == []
    assert math.isclose(sum(WEIGHTS.values()), 1.0)


def test_crash_keeps_its_own_tail_rule_when_jumpy_is_stricter():
    """RC-1: one -60 bps second (a tail), 100 s of calm while the vol ratio decays, then a steady 100 bps drift:
    the 160 bps move is a CRASH only through the tail. A 3-tail JUMPY rule must not hide it unless the CRASH rule
    is coupled to it (crash_tail_* None = coupled, the v0 behaviour)."""
    steps = [-60.0] + zigzag(100, 0.2) + [-1.0] * 100

    def states(cfg):
        clf, _ = run(zigzag(1200), clf=RegimeClassifier(cfg))
        return [clf.update(1200.0 + i, p) for i, p in enumerate(prices_from_bps(steps, start=clf._sec_px))]
    v0 = states(RegimeConfig())
    hit = next(s for s in v0 if s.state == CRASH)
    assert hit.vol_ratio < 3.0 and 'TAIL' in hit.reason                    # the tail, not the vol ratio
    assert all(s.state != CRASH for s in states(RegimeConfig(tail_count=3)))          # coupled: hidden
    assert any(s.state == CRASH for s in states(RegimeConfig(tail_count=3, crash_tail_z=4.5, crash_tail_count=1)))
    with pytest.raises(ValueError):
        RegimeConfig(crash_tail_count=0)
