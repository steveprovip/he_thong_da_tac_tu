"""Online learner for the DIRECTIONAL swarm agents (NORO, LUMEN, TIDAL) - 10-09 (cc).

Why only three: in core/micro_swarm.py the other five agents are not forecasters.  ZEPHR (data/spread), RUNE
(risk veto), OKAPI (size), MARIN (exits) and VESKA (AuthToken gateway) are safety gates; re-weighting or muting
them would let a vote outvote a stale book or a risk breach.  Their vetoes stay untouched (CLAUDE.md invariant).

Per settled trade the engine stores each alpha agent's vote at entry: {agent: [side, confidence]}.  For the
last `window` trades in which an agent voted a side:
  hit      = agent's side matched the realised direction (gross > 0 for the trade side, < 0 for the other)
  brier    = mean((p - y)^2) with p = 0.5 + 0.5 * confidence, y = hit
  edge     = mean net bps of the trades the agent voted WITH
Weight (shrunk toward 1.0 while the sample is small, Beta(5, 5)-like prior):
  skill    = (hits + 5) / (n + 10) - 0.5                         in about [-0.33, +0.33]
  weight   = clip(1 + 2.4 * skill, 0.2, 1.8)    -> 20/20 hits ~ 1.8, 0/20 ~ 0.2
Mute: the agent voted WITH the last `mute_streak` (3) losing trades in a row -> weight 0 for `mute_s` (1800 s).
Applied in micro_swarm.evaluate as confidence x weight (capped at 1.0); weight 0 = HOLD, its triggers removed.
Honest limits: ~2 trades/h and sigma ~15 bps/trade -> 20 trades separate a 60 % from a 40 % hitter only at
~1 sigma.  The shrinkage keeps the weights near 1 until evidence accumulates; the replay A/B decides.
"""
from __future__ import annotations

import json
import time
from collections import deque
from pathlib import Path
from typing import Dict, Optional

ALPHA_AGENTS = ('NORO', 'LUMEN', 'TIDAL')
W_MIN, W_MAX = 0.2, 1.8


class SwarmLearner:
    def __init__(self, window: int = 20, mute_streak: int = 3, mute_s: float = 1800.0, prior: float = 5.0,
                 gain: float = 2.4, clock=time.time):
        self.window, self.mute_streak, self.mute_s = window, mute_streak, mute_s
        self.prior, self.gain, self.clock = prior, gain, clock
        self.obs: Dict[str, deque] = {a: deque(maxlen=window) for a in ALPHA_AGENTS}
        self.with_losses: Dict[str, int] = {a: 0 for a in ALPHA_AGENTS}
        self.muted_until: Dict[str, float] = {a: 0.0 for a in ALPHA_AGENTS}
        self.updates = 0

    # ------------------------------------------------------------------ learning
    def observe(self, agents: Optional[dict], side: str, gross: float, net_bps: float) -> list:
        """One settled trade.  agents = {name: [vote_side, confidence]} recorded at entry.  -> events."""
        events = []
        if not agents or side not in ('BUY', 'SELL'):
            return events
        self.updates += 1
        for a in ALPHA_AGENTS:
            v = agents.get(a)
            if not v or v[0] not in ('BUY', 'SELL'):
                continue
            vside, conf = v[0], float(v[1] or 0.0)
            with_trade = vside == side
            hit = (gross > 0) if with_trade else (gross < 0)
            self.obs[a].append((hit, min(1.0, max(0.0, conf)), net_bps if with_trade else None))
            if with_trade:
                self.with_losses[a] = self.with_losses[a] + 1 if net_bps < 0 else 0
                if self.with_losses[a] >= self.mute_streak:
                    self.muted_until[a] = self.clock() + self.mute_s
                    self.with_losses[a] = 0
                    events.append(f'{a} muted {self.mute_s / 60:.0f} min ({self.mute_streak} losing trades in a row)')
        return events

    def stats(self, agent: str) -> dict:
        o = list(self.obs[agent])
        n = len(o)
        hits = sum(1 for h, _, _ in o if h)
        brier = sum(((0.5 + 0.5 * c) - (1.0 if h else 0.0)) ** 2 for h, c, _ in o) / n if n else None
        edges = [e for _, _, e in o if e is not None]
        return {'n': n, 'hits': hits, 'hit_rate': hits / n if n else None, 'brier': brier,
                'edge_bps': sum(edges) / len(edges) if edges else None, 'n_with': len(edges)}

    def weight(self, agent: str) -> float:
        if agent not in ALPHA_AGENTS:
            return 1.0
        if self.clock() < self.muted_until[agent]:
            return 0.0
        s = self.stats(agent)
        skill = (s['hits'] + self.prior) / (s['n'] + 2 * self.prior) - 0.5
        return max(W_MIN, min(W_MAX, 1.0 + self.gain * skill))

    def weights(self) -> Dict[str, float]:
        return {a: round(self.weight(a), 4) for a in ALPHA_AGENTS}

    def view(self) -> dict:
        now = self.clock()
        return {a: {**self.stats(a), 'weight': self.weight(a),
                    'muted_s': max(0.0, self.muted_until[a] - now)} for a in ALPHA_AGENTS}

    # ------------------------------------------------------------------ replay from the ledger at start-up
    def warm_start(self, rows) -> int:
        n = 0
        for r in rows:
            if r.get('agents') and r.get('qty') and r.get('entry'):
                notional = float(r['qty']) * float(r['entry'])
                self.observe(r['agents'], r.get('side'), float(r.get('gross') or 0.0),
                             float(r.get('pnl') or 0.0) / notional * 1e4 if notional else 0.0)
                n += 1
        for a in ALPHA_AGENTS:                       # history must not start the session muted
            self.muted_until[a] = 0.0
            self.with_losses[a] = 0
        return n

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'t': self.clock(), 'agents': self.view()}, default=float), encoding='utf-8')
        except OSError:
            pass


def reweight(sig: dict, weights: Optional[dict], alpha_keys: dict) -> dict:
    """Signals with each alpha agent's strength x weight (cap 1.0); weight 0 removes that agent's triggers.
    alpha_keys = micro_swarm.ALPHA.  Gate-agent inputs are not touched (they are not in `sig`)."""
    if not weights:
        return sig
    out = dict(sig)
    for agent, keys in alpha_keys.items():
        w = weights.get(agent, 1.0)
        if w == 1.0:
            continue
        for k in keys:
            s = out.get(k)
            if not s:
                continue
            if w <= 0:
                out[k] = None
            else:
                out[k] = {**s, 'strength': min(1.0, s['strength'] * w)}
    return out
