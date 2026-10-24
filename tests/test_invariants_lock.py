"""ARCH-2026-10-24 P0a: architectural invariants I1-I8 pinned in one place (core/invariants.py, not wired into any
runtime path yet).  The JEV FAST test describes the CURRENT behaviour (docs/architecture/01 §3.4, finding F1): a
change there has to be a deliberate owner decision (D4), not a side effect.  Synthetic data only; no network."""
from argparse import Namespace
from dataclasses import replace

import pytest

from core import engine as eng
from core import invariants as inv
from core import micro_signals as msig
from core import micro_swarm as ms
from core.consensus_engine import WEIGHTS, ConsensusEngine, TokenAuthority, Vote


def _flow_only(edge_bps=60.0):
    return {'FLOW': {'side': 'BUY', 'strength': 1.0, 'edge_bps': edge_bps, 'why': 'flow'}}


def _intent():
    return eng.MicroIntent('SOLUSDT', 'BUY', '0.4', 'gtx', 'AUTO', 1)


def _votes(**sides):
    return [Vote(a, sides.get(a, 'BUY'), 1.0 if sides.get(a, 'BUY') != 'HOLD' else 0.0, 'unit') for a in WEIGHTS]


# ----------------------------------------------------------------------------- consensus (I1, I3, I4, I8)
def test_weights_and_thresholds_are_pinned():
    assert WEIGHTS == {'NORO': .20, 'LUMEN': .20, 'TIDAL': .20, 'ZEPHR': .10, 'RUNE': .10, 'OKAPI': .08,
                       'MARIN': .07, 'VESKA': .05}
    assert inv.weights_sha256() == inv.WEIGHTS_SHA256
    assert sum(WEIGHTS[a] for a in inv.GATE_AGENTS) == pytest.approx(0.40)
    assert sum(WEIGHTS[a] for a in inv.ALPHA_AGENTS) == pytest.approx(inv.ALPHA_BUDGET)
    c = ConsensusEngine()
    assert (c.threshold, c.veto_threshold) == (inv.SUPERMAJORITY, inv.VETO) == (0.768, 0.70)


def test_engine_path_policy_is_clean_on_normal(tmp_path):
    p = replace(eng.PROFILES['demo'], symbol='SOLUSDT')
    e = eng.Engine(p, eng.MockVenue(p, quote=lambda s: (100.0, 100.01)), msig.MicroFeed('SOLUSDT'),
                   ledger=tmp_path / 'trades.jsonl')
    assert inv.check_consensus(e.consensus, regimes=('NORMAL',)) == []      # micro_swarm always votes NORMAL
    assert e.authority.policy is None and inv.check_token_authority(e.authority) == []


def test_runtime_path_policy_is_clean_on_every_regime(engine_factory):
    rt = engine_factory('testnet')
    assert inv.check_consensus(rt.swarm.consensus) == []                    # trend_floor == threshold (I8)
    assert inv.check_token_authority(rt.authority) == []


def test_class_default_trend_quorum_is_reported():
    """F3: ConsensusEngine() keeps the adaptive TREND quorum (0.70) on purpose; no production caller may use it."""
    errs = inv.check_consensus(ConsensusEngine())
    assert len(errs) == 1 and 'TREND' in errs[0]
    assert inv.check_consensus(ConsensusEngine(trend_floor=.768)) == []


def test_learning_moves_alpha_weights_only():
    c = ConsensusEngine()
    for _ in range(20):
        c.record_outcome(_votes(LUMEN='SELL'), 'BUY', 2.0)
    assert c.weights['LUMEN'] < 0.05 < 0.25 < c.weights['NORO']           # learning really moved the alpha weights
    assert inv.check_consensus(c, regimes=('NORMAL',)) == []
    c.weights['RUNE'] = 0.05
    errs = inv.check_consensus(c, regimes=('NORMAL',))
    assert len(errs) == 1 and 'RUNE' in errs[0]


# ----------------------------------------------------------------------------- token (I1)
def test_token_is_single_use_and_lives_five_seconds():
    t = [0.0]
    a, it = TokenAuthority(clock=lambda: t[0]), _intent()
    d = ConsensusEngine().evaluate(_votes())
    tok = a.issue(it, d)
    t[0] = inv.TOKEN_TTL_S - 0.01
    a.consume(tok, it)
    with pytest.raises(PermissionError, match='replayed'):
        a.consume(tok, it)
    tok = a.issue(it, d)
    t[0] += inv.TOKEN_TTL_S
    with pytest.raises(PermissionError, match='Expired'):
        a.consume(tok, it)


def test_engine_token_refuses_a_research_policy_decision_below_supermajority():
    hold = {a: 'HOLD' for a in ('LUMEN', 'TIDAL')}
    d = ConsensusEngine(threshold=.5, veto_threshold=.4).evaluate(_votes(**hold))
    assert d.allowed and d.score == pytest.approx(0.60)
    with pytest.raises(PermissionError, match='Consensus denied'):
        TokenAuthority().issue(_intent(), d)                                # engine path: fixed 0.768 quorum


# ----------------------------------------------------------------------------- JEV FAST (I2, I3): current behaviour
def test_jev_fast_lifts_statistical_veto_but_never_a_gate_veto():
    """One alpha trigger: raw consensus 0.60 < 0.70 is a statistical veto, yet the JEV path authorises it because the
    five gates are green (designed 2026-10-07).  A red gate stays a hard veto.  Pinned: changing it is decision D4."""
    raw, _, _ = ms.evaluate(_flow_only(), ms.Gates(cost_bps=6.0), jev_min=None)
    assert (raw.allowed, raw.veto) == (False, True) and raw.score == pytest.approx(0.60)
    d, _, diag = ms.evaluate(_flow_only(), ms.Gates(cost_bps=6.0), jev_min=inv.JEV_MIN_FLOOR)
    assert diag['consensus'] == pytest.approx(0.60) and diag['consensus'] < inv.VETO
    assert (diag['path'], d.allowed, d.veto) == ('JEV', True, False)
    assert d.score == pytest.approx(diag['jev'] / 10) and d.score >= inv.SUPERMAJORITY
    assert diag['confluence']['count'] == 2                               # C1_FLOW + C3_EDGE
    a, it = TokenAuthority(), _intent()
    a.consume(a.issue(it, d), it)                                         # one forecaster is enough to reach VESKA
    red, _, rdiag = ms.evaluate(_flow_only(), ms.Gates(cost_bps=6.0, rune=(False, 'risk budget')),
                                jev_min=inv.JEV_MIN_FLOOR)
    assert (red.allowed, red.veto, rdiag['path'], red.reason) == (False, True, None, 'RUNE VETO [RUNE]')


# ----------------------------------------------------------------------------- Profiles (I5, I6)
@pytest.mark.production_profile
def test_shipped_profiles_hold_every_invariant():
    for name, p in eng.PROFILES.items():
        assert inv.check_profile(p) == [], name
    live = eng.PROFILES['live']
    assert (live.equity_cap, live.max_loss_usdt, live.autonomy) == (3.85, 0.05, 'off')


@pytest.mark.parametrize('mode, change, word', [
    ('demo', {'entry': 'market'}, 'entry'),
    ('demo', {'super_edge': True}, 'super_edge'),
    ('demo', {'super_cross': 'on'}, 'super_cross'),
    ('demo', {'jev_min': 7.5}, 'jev_min'),
    ('demo', {'peg_min_score': 0.70}, 'peg_min_score'),
    ('live', {'equity_cap': 10.0}, 'equity_cap'),
    ('live', {'equity_cap': None}, 'equity_cap'),
    ('live', {'max_loss_usdt': 0.10}, 'max_loss_usdt'),
    ('live', {'autonomy': 'on'}, 'autonomy'),
])
def test_check_profile_reports_each_violation(mode, change, word):
    errs = inv.check_profile(replace(eng.PROFILES[mode], **change))
    assert len(errs) == 1 and word in errs[0]


# ----------------------------------------------------------------------------- core.runtime overrides (F2, D1)
def test_runtime_overrides_are_reported_on_mainnet_only():
    low = Namespace(threshold=.50, veto_threshold=.45)
    assert inv.check_runtime_args(low, 'testnet') == [] == inv.check_runtime_args(low, 'paper')
    errs = inv.check_runtime_args(low, 'mainnet')
    assert len(errs) == 2 and errs[0].startswith('--threshold') and errs[1].startswith('--veto-threshold')
    assert inv.check_runtime_args(Namespace(threshold=.90, veto_threshold=.75), 'mainnet') == []
    assert inv.check_runtime_args(Namespace(threshold=None, veto_threshold=None), 'mainnet') == []
