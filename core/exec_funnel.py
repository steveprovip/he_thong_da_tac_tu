"""Execution funnel + blocker accounting (2026-10-08v).

Why does the engine not trade?  One counter per stage, sampled at most once per `every_s` per engine
(time-share of the decision loop, NOT independent opportunities):

    eval -> hard guard (ZEPHR/RUNE/OKAPI/MARIN/VESKA veto) -> no signal -> swarm statistical veto
         -> churn gates (all evaluable blockers, first one = the one the hot path returned)
         -> scanner yield / observer -> submit -> outcome (from engine_orders rows)

Classification (form 10-08 §A): hard guards protect capital / data / order validity and are never relaxed
for trade count; statistical gates are thresholds on noisy features that need ablation + holdout; cost gates
are fee arithmetic (VFR, net RR).  Pure bookkeeping: no orders, no I/O."""
from __future__ import annotations

import time
from collections import Counter
from typing import Iterable, Optional

HARD = 'hard'
STAT = 'statistical'
COST = 'cost'
CONFIG = 'config'

# churn gate name -> class
GATE_CLASS = {
    'VPIN': STAT, 'VFR': COST, 'DRIFT': STAT, 'KER': STAT, 'MACRO': STAT, 'ABSORB': STAT, 'FLOW': STAT,
    'MR_FLOW': STAT, 'NET_RR': COST, 'MR_SCORE': STAT, 'VELOCITY': STAT, 'SCORE': STAT, 'EDGE': STAT,
    # swarm stage
    'ZEPHR': HARD, 'RUNE': HARD, 'OKAPI': HARD, 'MARIN': HARD, 'VESKA': HARD,
    'WEAK': STAT, 'CONFLUENCE': STAT, 'WALL': STAT, 'REGIME': STAT,
    # after the gates
    'OBSERVER': CONFIG, 'YIELD': CONFIG, 'NO_SIGNAL': 'none',
}

# engine_orders outcome -> funnel bucket (attempts that reached the venue have a t_send)
SENT = ('FILLED', 'PARTIAL', 'UNFILLED', 'REJECTED', 'TOXIC', 'ERROR')
FILLS = ('FILLED', 'PARTIAL')
CANCELS = ('UNFILLED', 'TOXIC')


def swarm_key(reason: str) -> str:
    """Statistical swarm veto -> short key (WEAK / CONFLUENCE / WALL / REGIME / OTHER)."""
    r = (reason or '').upper()
    for k in ('CONFLUENCE', 'WALL', 'REGIME', 'WEAK'):
        if r.startswith(k):
            return k
    return 'OTHER'


class Funnel:
    """Per-engine counters.  `sample()` throttles the bookkeeping to one row per `every_s`."""

    def __init__(self, every_s: float = 1.0, clock=time.time):
        self.every_s, self.clock = every_s, clock
        self.started = clock()
        self.last = 0.0
        self.stages: Counter = Counter()          # (lane, stage) -> samples
        self.first: Counter = Counter()           # (lane, gate) -> samples where gate was the returned blocker
        self.all: Counter = Counter()             # (lane, gate) -> samples where gate blocked (any position)
        self.only: Counter = Counter()            # (lane, gate) -> samples where gate was the ONLY blocker
        self.outcomes: Counter = Counter()        # engine_orders outcome -> rows (session)
        self.last_blockers: list = []

    def due(self) -> bool:
        now = self.clock()
        if now - self.last >= self.every_s:
            self.last = now
            return True
        return False

    def record(self, lane: str, stage: str, blockers: Iterable[str] = ()) -> None:
        bl = list(dict.fromkeys(blockers))
        self.stages[(lane, stage)] += 1
        if bl:
            self.first[(lane, bl[0])] += 1
            for b in bl:
                self.all[(lane, b)] += 1
            if len(bl) == 1:
                self.only[(lane, bl[0])] += 1
        self.last_blockers = bl

    def outcome(self, outcome: str) -> None:
        self.outcomes[outcome] += 1

    def counters(self) -> dict:
        """Dashboard counters; None = unknown (never 0 by default)."""
        o = self.outcomes
        sent = sum(o[k] for k in SENT)
        fills = sum(o[k] for k in FILLS)
        hours = max(self.clock() - self.started, 0.0) / 3600.0
        return {'intents': sent, 'fills': fills, 'partial': o['PARTIAL'], 'cancels': sum(o[k] for k in CANCELS),
                'refused': o['REFUSED'], 'rejected': o['REJECTED'],
                'shadow': sum(n for k, n in o.items() if str(k).startswith('SHADOW')),
                'shadow_cross': o['SHADOW_CROSS'], 'errors': o['ERROR'],
                'fill_rate': fills / sent if sent else None,
                'trades_per_hour': fills / hours if hours >= 1 / 60 else None}

    def view(self) -> dict:
        def tab(c: Counter) -> dict:
            out: dict = {}
            for (lane, k), n in c.items():
                out.setdefault(lane, {})[k] = n
            return out
        return {'samples_every_s': self.every_s, 'stages': tab(self.stages), 'first': tab(self.first),
                'all': tab(self.all), 'only': tab(self.only), 'outcomes': dict(self.outcomes),
                'last_blockers': self.last_blockers, 'class': {k: GATE_CLASS.get(k, STAT) for k in
                                                                {k for (_, k) in self.all} | set(self.last_blockers)},
                **self.counters()}


def merge_counters(rows: Iterable[dict]) -> dict:
    """Sum per-engine counters for the scanner tag (None stays None when no engine knows it)."""
    rows = list(rows)
    keys = ('intents', 'fills', 'partial', 'cancels', 'refused', 'rejected', 'shadow')
    out = {k: sum(r.get(k) or 0 for r in rows) for k in keys}
    out['fill_rate'] = out['fills'] / out['intents'] if out['intents'] else None
    tph = [r['trades_per_hour'] for r in rows if r.get('trades_per_hour') is not None]
    out['trades_per_hour'] = sum(tph) if tph else None
    return out


def rr_feasible(rr_max: Optional[float], rr_min_gate: float) -> Optional[bool]:
    if not rr_min_gate:
        return True
    return None if rr_max is None else rr_max >= rr_min_gate - 1e-12
