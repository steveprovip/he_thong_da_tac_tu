"""8-agent vote over the micro signals (core.micro_signals) for the 2-mode engine.

Alpha agents (60 % weight) each own one trigger and vote its side/strength:
  NORO = VWAP mean-reversion · LUMEN = OBI+CVD flow · TIDAL = liquidation impulse.
Gate agents (40 %) vote the consensus side at confidence 1, or HOLD + veto:
  ZEPHR data fresh & spread · RUNE risk budget · OKAPI sizing/capital · MARIN exits · VESKA gateway.
Weights are core.consensus_engine.WEIGHTS, so the 76.8 % / 70 % invariants are unchanged:
two alpha triggers agreeing at full strength + all gates = 80 % -> AUTHORIZED.
JEV fast path: all five gates green and score_from_ratio(aligned edge / cost) >= jev_min
(8.0 <=> edge > 2.54x cost); the decision score is then jev/10 (>= 0.80 > 0.768), so the
same TokenAuthority accepts it - the threshold is never lowered, the evidence is different.
Confluence filter (2026-10-07): either path also needs >= 2 of
  C1 FLOW aligned (OBI L5 >= 0.20 + sustained CVD) · C2 VWAP aligned (>= 0.08 % at the local extreme,
  NEUTRAL regime only) · C4 TREND aligned (CVD >= $300k + OBI >= 0.15 + price through VWAP)
  · C3 expected edge / total cost >= 2.5x (cost = maker in + maker out + spread, bootstrap-calibrated).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Dict, List, Optional, Tuple

from core.consensus_engine import ConsensusEngine, Decision, Vote
from core.jev_gatekeeper import score_from_ratio

# NORO = fair value / trend alignment (TREND in a flow regime, VWAP mean-reversion only when NEUTRAL)
ALPHA = {'NORO': ('TREND', 'VWAP'), 'LUMEN': ('FLOW', 'SURGE'), 'TIDAL': ('SQUEEZE', 'LIQ')}
SIGNAL_KEYS = ('FLOW', 'VWAP', 'LIQ', 'TREND', 'SURGE', 'SQUEEZE')     # WALL never votes, never adds edge


def alpha_signal(sig: dict, keys: tuple) -> Optional[dict]:
    return next((sig.get(k) for k in keys if sig.get(k)), None)
GATES = ('ZEPHR', 'RUNE', 'OKAPI', 'MARIN', 'VESKA')


@dataclass(frozen=True)
class Gates:
    zephr: Tuple[bool, str] = (True, 'data fresh · spread ok')
    rune: Tuple[bool, str] = (True, 'risk budget ok')
    okapi: Tuple[bool, str] = (True, 'size ok')
    marin: Tuple[bool, str] = (True, 'exits armed')
    veska: Tuple[bool, str] = (True, 'gateway ready')
    cost_bps: float = 12.0

    def as_map(self) -> Dict[str, Tuple[bool, str]]:
        return {'ZEPHR': self.zephr, 'RUNE': self.rune, 'OKAPI': self.okapi, 'MARIN': self.marin,
                'VESKA': self.veska}


def direction_of(sig: dict) -> Optional[str]:
    tally = {'BUY': 0.0, 'SELL': 0.0}
    for keys in ALPHA.values():
        s = alpha_signal(sig, keys)
        if s:
            tally[s['side']] += s['strength']
    if tally['BUY'] == tally['SELL']:
        return None
    return max(tally, key=tally.get)


def confluence(sig: dict, side: Optional[str], edge_bps: float, cost_bps: float, ratio_min: float = 2.5) -> dict:
    ratio = edge_bps / max(cost_bps, 1e-9)
    def al(k):
        return bool(side and sig.get(k) and sig[k]['side'] == side)
    c = {'C1_FLOW': al('FLOW') or al('SURGE'),
         'C2_VWAP': bool(side and sig.get('VWAP') and sig['VWAP']['side'] == side),
         'C4_TREND': al('TREND'), 'C5_SQUEEZE': al('SQUEEZE'), 'C6_WALL': al('WALL'),
         'C3_EDGE': bool(side and ratio >= ratio_min)}
    return {**c, 'count': sum(c.values()), 'edge_cost': ratio}


def evaluate(sig: dict, gates: Gates, engine: Optional[ConsensusEngine] = None,
             jev_min: Optional[float] = 8.0, min_confluence: int = 2,
             edge_ratio_min: float = 2.5, allow: Tuple[str, ...] = ('BUY', 'SELL')) -> Tuple[Decision, List[Vote], dict]:
    engine = engine or ConsensusEngine()
    side = direction_of(sig)
    votes: List[Vote] = []
    for agent, keys in ALPHA.items():
        s = alpha_signal(sig, keys)
        votes.append(Vote(agent, s['side'], round(s['strength'], 4), s['why']) if s
                     else Vote(agent, 'HOLD', 0.0, f'scanning · no {"/".join(keys)} trigger'))
    gmap = gates.as_map()
    for agent in GATES:
        ok, why = gmap[agent]
        if not ok:
            votes.append(Vote(agent, 'HOLD', 0.0, why, veto=True))
        elif side is None:
            votes.append(Vote(agent, 'HOLD', 0.0, f'ready · {why}'))
        else:
            votes.append(Vote(agent, side, 1.0, why))
    decision = engine.evaluate(votes)
    consensus = decision.score                                   # raw swarm score, before any JEV lift
    edge = sum(sig[k]['edge_bps'] for k in SIGNAL_KEYS if sig.get(k) and sig[k]['side'] == side) if side else 0.0
    jev = score_from_ratio(edge / max(gates.cost_bps, 1e-9)) if side else 0.0
    gates_ok = all(ok for ok, _ in gmap.values())
    path = 'SWARM' if decision.allowed else None
    if (not decision.allowed and jev_min is not None and side and gates_ok and jev >= jev_min):
        decision = replace(decision, side=side, score=max(decision.score, jev / 10.0), allowed=True, veto=False,
                           reason=f'JEV FAST {jev:.1f}/10', blockers=())
        path = 'JEV'
    conf = confluence(sig, side, edge, gates.cost_bps, edge_ratio_min)
    if decision.allowed and conf['count'] < min_confluence:
        decision = replace(decision, allowed=False,
                           reason=f'CONFLUENCE {conf["count"]}/3 < {min_confluence} · edge/cost {conf["edge_cost"]:.2f}x')
        path = None
    wall = sig.get('WALL')
    if decision.allowed and wall and wall['side'] != decision.side:  # do not buy into a sell wall (or vice versa)
        decision = replace(decision, allowed=False, reason=f'WALL against {decision.side}: {wall["why"]}')
        path = None
    if decision.allowed and decision.side not in allow:          # hard CVD regime gate (defence in depth)
        decision = replace(decision, allowed=False, reason=f'REGIME BAN {decision.side} (allowed: {"/".join(allow)})')
        path = None
    if side is None and gates_ok:
        decision = replace(decision, reason='SCANNING · no trigger')
    elif side and gates_ok and not decision.allowed and not decision.reason.startswith(('CONFLUENCE', 'REGIME', 'WALL')):
        decision = replace(decision, reason=f'WEAK {decision.score * 100:.1f}% < {decision.threshold * 100:.1f}% · JEV {jev:.1f}')
    return decision, votes, {'side': side, 'edge_bps': edge, 'jev': jev, 'gates_ok': gates_ok, 'path': path,
                             'confluence': conf, 'consensus': consensus}
