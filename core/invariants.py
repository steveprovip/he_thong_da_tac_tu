"""Architectural invariants I1-I8 in one place (ARCH-2026-10-24 P0a, docs/architecture/01 §10).

The constants are the values the code enforces today.  Every check returns a list of violations (empty = ok) and
never raises, so each caller decides.  P0b (2026-10-24): core.engine.Engine.__init__ refuses a breached quorum or
token in every mode and a breached Profile on an armed live engine (demo announces it); core.runtime.arguments
refuses lowered --threshold / --veto-threshold on mainnet (owner decision D1).  Sandbox hard gates come with P3.
"""
from __future__ import annotations

import hashlib
import json
import math

from core.consensus_engine import WEIGHTS

SUPERMAJORITY = 0.768                  # ConsensusEngine NORMAL quorum and the engine-path TokenAuthority (I1)
VETO = 0.70                            # statistical veto (I3); micro_swarm's JEV FAST path may lift it (F1, D4)
WEIGHTS_SHA256 = '9d9b776482d1e96068c1f439ade6ae2de725dc600142be5c6e933cef3437bf84'   # json, sort_keys
ALPHA_AGENTS = ('NORO', 'LUMEN', 'TIDAL')
GATE_AGENTS = ('ZEPHR', 'RUNE', 'OKAPI', 'MARIN', 'VESKA')
ALPHA_BUDGET = 0.60                    # learning moves weight between alpha agents only; gates stay fixed (I4)
REGIMES = ('NORMAL', 'TOXIC', 'CHOP', 'TREND')
ENTRY = 'gtx'                          # Post-Only GTX entries (I5)
NO_TAKER_ENTRY = ('off', 'shadow')     # super_cross values that never send an IOC entry
JEV_MIN_FLOOR = 8.0                    # JEV FAST score = jev / 10 must clear SUPERMAJORITY
TOKEN_TTL_S = 5.0                      # single-use HMAC token lifetime (core/consensus_engine.py:132)
LIVE_EQUITY_CAP = 3.85                 # live micro-capital gate, USDT (I6)
LIVE_MAX_LOSS = 0.05
_EPS = 1e-12


def weights_sha256(weights: dict = WEIGHTS) -> str:
    return hashlib.sha256(json.dumps(weights, sort_keys=True).encode()).hexdigest()


def check_consensus(engine, regimes=REGIMES) -> list:
    """I1/I3/I4/I8 on a ConsensusEngine.  The 2-mode engine only votes NORMAL; core.runtime may pass any regime, so
    its policy has to hold the quorum on every regime it can produce."""
    out = []
    if not math.isclose(engine.threshold, SUPERMAJORITY):
        out.append(f'threshold {engine.threshold} != {SUPERMAJORITY}')
    if not math.isclose(engine.veto_threshold, VETO):
        out.append(f'veto_threshold {engine.veto_threshold} != {VETO}')
    for r in regimes:
        if engine.threshold_for(r) < SUPERMAJORITY - _EPS:
            out.append(f'threshold_for({r}) = {engine.threshold_for(r)} < {SUPERMAJORITY}')
    if weights_sha256() != WEIGHTS_SHA256:
        out.append('consensus_engine.WEIGHTS changed')
    for a in GATE_AGENTS:
        if not math.isclose(engine.weights.get(a, 0.0), WEIGHTS[a], rel_tol=1e-9):
            out.append(f'gate weight {a} = {engine.weights.get(a)} != {WEIGHTS[a]}')
    alpha = math.fsum(engine.weights.get(a, 0.0) for a in ALPHA_AGENTS)
    if not math.isclose(alpha, ALPHA_BUDGET, rel_tol=1e-9):
        out.append(f'alpha weight budget {alpha} != {ALPHA_BUDGET}')
    return out


def check_token_authority(auth) -> list:
    """I1: the engine path's TokenAuthority() has no policy and a fixed 0.768 quorum; a policy-bound authority
    (core.runtime) takes the policy's per-regime quorum, which must never dip below it."""
    if auth.policy is not None:
        return check_consensus(auth.policy)
    if not math.isclose(auth.threshold, SUPERMAJORITY):
        return [f'token threshold {auth.threshold} != {SUPERMAJORITY}']
    return []


def check_profile(p) -> list:
    """I5/I6 and the JEV floor on an engine Profile (duck-typed: core.engine is not imported here)."""
    out = []
    if p.entry != ENTRY:
        out.append(f'entry {p.entry!r} != {ENTRY!r} (Post-Only GTX)')
    if p.super_edge:
        out.append('super_edge on: IOC taker entry')
    if p.super_cross not in NO_TAKER_ENTRY:
        out.append(f'super_cross {p.super_cross!r}: LIMIT IOC on the remaining quantity')
    if p.jev_min is not None and p.jev_min < JEV_MIN_FLOOR - _EPS:
        out.append(f'jev_min {p.jev_min} < {JEV_MIN_FLOOR}: JEV FAST would emit decisions TokenAuthority refuses')
    if getattr(p, 'micro_ioc', 'off') == 'on':
        out.append('micro_ioc on: IOC taker entry in micro mode')
    if p.peg_min_score < SUPERMAJORITY - _EPS:
        out.append(f'peg_min_score {p.peg_min_score} < {SUPERMAJORITY}')
    if p.mode == 'live':
        if p.equity_cap is None or p.equity_cap > LIVE_EQUITY_CAP + _EPS:
            out.append(f'live equity_cap {p.equity_cap} > {LIVE_EQUITY_CAP}')
        if p.max_loss_usdt is None or p.max_loss_usdt > LIVE_MAX_LOSS + _EPS:
            out.append(f'live max_loss_usdt {p.max_loss_usdt} > {LIVE_MAX_LOSS}')
        if p.autonomy != 'off':
            out.append(f'live autonomy {p.autonomy!r} != off')
        if getattr(p, 'micro_harvester', 'off') == 'on':      # 11-09e: maker-only, but the lane has no holdout evidence
            out.append("live micro_harvester 'on': chop harvester is a Demo rehearsal (shadow is allowed)")
        if getattr(p, 'micro_sniper_runner', 'off') == 'on':    # 11-10 (e): unvalidated exit policy (no eject, far TP)
            out.append("live micro_sniper_runner 'on': the runner exit policy is a Demo rehearsal")
        if getattr(p, 'micro_max_positions', 1) > 1:             # 11-09f: several concurrent positions are a Demo rehearsal
            out.append(f'live micro_max_positions {p.micro_max_positions} > 1')
        if getattr(p, 'micro_accel_k', 0.0) > 0:
            out.append(f'live micro_accel_k {p.micro_accel_k} > 0: no PnL-linked lot growth on a real account')
    return out


def check_runtime_args(args, mode: str) -> list:
    """F2 / D1: core.runtime research overrides may lower the quorum on paper/testnet by design; on mainnet that is
    a violation.  Stricter values are always fine."""
    if mode != 'mainnet':
        return []
    out = []
    thr, veto = getattr(args, 'threshold', None), getattr(args, 'veto_threshold', None)
    if thr is not None and thr < SUPERMAJORITY - _EPS:
        out.append(f'--threshold {thr} < {SUPERMAJORITY} on mainnet')
    if veto is not None and veto < VETO - _EPS:
        out.append(f'--veto-threshold {veto} < {VETO} on mainnet')
    return out
