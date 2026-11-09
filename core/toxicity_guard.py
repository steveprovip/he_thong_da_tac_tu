"""Passive-quote toxicity guard (2026-11-07, Pillar 3): pull or deepen GTX entry quotes when the flow is toxic.

Two causal measurements:
  VPIN      core.micro_signals.MicroFeed.vpin_now() (volume buckets, exact aggressor side). Owner rule: no passive
            entry quote on either side while VPIN > 0.65. The engine's older asymmetric VPIN pause at 0.80
            (vpin_gate) stays as it is.
  Markouts  TapeMarkouts. Every aggTrade is a passive fill for whoever rested on the other side. Per second and
            passive side, the fill VWAP is marked against the book mid 1 s / 5 s / 30 s after the end of that second:
                passive BUY  (a seller hit the bid):   (mid_later - vwap) / vwap x 1e4
                passive SELL (a buyer lifted the ask): (vwap - mid_later) / vwap x 1e4
            Rolling mean over the last 120 s. Own fills: the engine's 5 s markouts of its own entries (last 20).
Action for a GTX entry on `side` (decide):
  PULL   VPIN > 0.65; or the 5 s tape markout of our passive side <= -3.0 bps over >= 30 one-second samples (worse
         than the 3 bps adverse-selection allowance inside the 8.5 bps hurdle); or own fills with n >= 10,
         mean <= -3.0 bps and one-sided t <= -1.645.
  SKEW   the same evidence at <= -1.5 bps: quote 1 tick behind the touch (pegging keeps the offset).
  QUOTE  otherwise. Missing VPIN or markouts are no evidence either way; the engine's other gates still apply.
Thresholds come from the owner's cost model, not from fitting. The guard can only withhold or deepen a quote.
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass
from typing import Optional, Sequence

QUOTE, SKEW, PULL = 'QUOTE', 'SKEW', 'PULL'
SIDES = ('BUY', 'SELL')


class TapeMarkouts:
    """Rolling 1 / 5 / 30 s markouts of the tape's passive fills, per passive side. O(1) amortised per message."""

    HORIZONS = (1.0, 5.0, 30.0)

    def __init__(self, window_s: float = 120.0):
        self.window = float(window_s)
        self.cur: Optional[list] = None                        # [second, {'BUY': [pq, q], 'SELL': [pq, q]}]
        self.pending = {h: deque() for h in self.HORIZONS}     # (t_end_of_second, side, vwap)
        self.done = {(h, s): deque() for h in self.HORIZONS for s in SIDES}
        self.sums = {(h, s): 0.0 for h in self.HORIZONS for s in SIDES}

    def on_trade(self, t: float, px: float, qty: float, buyer_is_maker: bool) -> None:
        if not (px > 0 and qty > 0 and math.isfinite(px) and math.isfinite(qty)):
            return
        sec = int(t)
        if self.cur is None or sec != self.cur[0]:
            self._flush()
            self.cur = [sec, {'BUY': [0.0, 0.0], 'SELL': [0.0, 0.0]}]
        acc = self.cur[1]['BUY' if buyer_is_maker else 'SELL']    # buyer is maker: the bid was hit, a passive BUY
        acc[0] += px * qty
        acc[1] += qty

    def _flush(self) -> None:
        if self.cur is None:
            return
        sec, sides = self.cur
        for side, (pq, q) in sides.items():
            if q > 0:
                for h in self.HORIZONS:
                    self.pending[h].append((sec + 1.0, side, pq / q))
        self.cur = None

    def on_mid(self, t: float, mid: float) -> None:
        """Mature every passive fill whose horizon has passed, against this mid; expire old samples."""
        if not (mid > 0 and math.isfinite(mid)):
            return
        if self.cur is not None and int(t) > self.cur[0]:
            self._flush()
        for h, q in self.pending.items():
            while q and q[0][0] + h <= t:
                _, side, vwap = q.popleft()
                m = ((mid - vwap) if side == 'BUY' else (vwap - mid)) / vwap * 1e4
                self.done[(h, side)].append((t, m))
                self.sums[(h, side)] += m
        for key, q in self.done.items():
            while q and q[0][0] < t - self.window:
                self.sums[key] -= q.popleft()[1]

    def view(self, side: str, horizon: float = 5.0) -> tuple:
        """-> (mean bps or None, n samples) for passive fills on `side`."""
        q = self.done[(float(horizon), side)]
        return (self.sums[(float(horizon), side)] / len(q) if q else None), len(q)

    def snapshot(self) -> dict:
        out = {}
        for h in self.HORIZONS:
            row = {}
            for s in SIDES:
                m, n = self.view(s, h)
                row[s] = None if m is None else round(m, 3)
                row[f'n_{s.lower()}'] = n
            out[f'{h:g}s'] = row
        return out


@dataclass(frozen=True)
class ToxPolicy:
    vpin_max: float = 0.65
    skew_bps: float = 1.5
    pull_bps: float = 3.0
    min_n: int = 30
    own_min_n: int = 10
    own_t: float = -1.645
    skew_ticks: int = 1

    def __post_init__(self):
        if not (0 < self.vpin_max <= 1 and 0 < self.skew_bps < self.pull_bps and self.min_n >= 1
                and self.own_min_n >= 2 and self.own_t < 0 and self.skew_ticks >= 1):
            raise ValueError('invalid toxicity policy')


def _own_stats(xs: Sequence[float]) -> tuple:
    x = [float(v) for v in xs if isinstance(v, (int, float)) and math.isfinite(v)]
    n = len(x)
    if n < 2:
        return (x[0] if x else None), None, n
    mean = sum(x) / n
    sd = math.sqrt(sum((v - mean) ** 2 for v in x) / (n - 1))
    return mean, (mean / (sd / math.sqrt(n)) if sd > 0 else (-math.inf if mean < 0 else math.inf)), n


def decide(side: str, vpin: Optional[float], tape_5s: Optional[tuple], own_5s: Sequence[float] = (),
           policy: ToxPolicy = ToxPolicy()) -> tuple:
    """-> (QUOTE | SKEW | PULL, ticks behind the touch, reason)."""
    if side not in SIDES:
        raise ValueError(f'side must be BUY or SELL, got {side!r}')
    p = policy
    if vpin is not None and math.isfinite(vpin) and vpin > p.vpin_max:
        return PULL, 0, f'VPIN {vpin:.2f} > {p.vpin_max:g}'
    action, why = QUOTE, 'no toxicity evidence'
    m, n = tape_5s if tape_5s else (None, 0)
    if m is not None and n >= p.min_n:
        if m <= -p.pull_bps:
            return PULL, 0, f'5s maker markout {side} {m:+.2f} bps <= -{p.pull_bps:g} (n {n})'
        if m <= -p.skew_bps:
            action, why = SKEW, f'5s maker markout {side} {m:+.2f} bps <= -{p.skew_bps:g} (n {n})'
    om, ot, on = _own_stats(own_5s)
    if on >= p.own_min_n and ot is not None and ot <= p.own_t:
        if om <= -p.pull_bps:
            return PULL, 0, f'own 5s markout {om:+.2f} bps, t {ot:.2f} (n {on})'
        if om <= -p.skew_bps and action == QUOTE:
            action, why = SKEW, f'own 5s markout {om:+.2f} bps, t {ot:.2f} (n {on})'
    return action, (p.skew_ticks if action == SKEW else 0), why
