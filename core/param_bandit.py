"""Thompson-sampling bandit over entry/exit parameters, per market regime - 10-09 (cc).

Arms (factorised: one independent bandit per parameter, every closed trade rewards all three chosen arms):
  cs_super_ofi       {0.85, 0.90, 0.95}  OFI needed for the 10 % conviction tier.  0.80 of the request is NOT
                                         offered: it would raise exposure above the current 0.85 baseline.
  hurdle_min_hold_s  {30, 60, 90}        fee-hurdle minimum hold before a FLOW/REGIME exit
  tp1_scaled_pct     {0.30, 0.40, 0.50}  50 % maker TP1 distance (TREND splits)
Regime = KER bucket at selection time: CHOP (< 0.30), MID, TREND (>= 0.40) - the cs_chop/cs_trend cut-offs.
Reward = net bps of the trade on its notional (after fees).
Posterior per arm: Normal mean with prior N(0, prior_sd^2) and plug-in noise sigma (default 15 bps, the ledger's
per-trade sd); sample one value per arm, play the argmax.  Anti-repeat: a full combination that lost
`ban_after` times in a row is banned for the next `ban_trades` selections in that regime.
Selections happen only while the engine is flat (exits never change under an open position).
"""
from __future__ import annotations

import json
import math
import random
import time
from pathlib import Path
from typing import Dict, Optional, Tuple

ARMS: Dict[str, Tuple[float, ...]] = {
    'cs_super_ofi': (0.85, 0.90, 0.95),
    'hurdle_min_hold_s': (30.0, 60.0, 90.0),
    'tp1_scaled_pct': (0.30, 0.40, 0.50),
}
REGIMES = ('CHOP', 'MID', 'TREND')


def regime_of(ker: Optional[float], chop: float = 0.30, trend: float = 0.40) -> str:
    if ker is None:
        return 'MID'
    return 'CHOP' if ker < chop else ('TREND' if ker >= trend else 'MID')


class ParamBandit:
    def __init__(self, arms: Optional[dict] = None, sigma: float = 15.0, prior_sd: float = 10.0,
                 ban_after: int = 2, ban_trades: int = 10, seed: Optional[int] = None, clock=time.time):
        self.arms = dict(arms or ARMS)
        self.sigma, self.prior_sd = sigma, prior_sd
        self.ban_after, self.ban_trades = ban_after, ban_trades
        self.rng = random.Random(seed)
        self.clock = clock
        # stats[regime][param][value] = [n, sum_reward]
        self.stats = {r: {p: {v: [0, 0.0] for v in vals} for p, vals in self.arms.items()} for r in REGIMES}
        self.last_combo: Dict[str, Optional[tuple]] = {r: None for r in REGIMES}
        self.combo_losses: Dict[str, int] = {r: 0 for r in REGIMES}
        self.banned: Dict[str, Dict[tuple, int]] = {r: {} for r in REGIMES}
        self.history = []

    # ------------------------------------------------------------------ posterior
    def posterior(self, regime: str, param: str, value: float) -> Tuple[float, float]:
        n, s = self.stats[regime][param][value]
        prec = 1.0 / self.prior_sd ** 2 + n / self.sigma ** 2
        return (s / self.sigma ** 2) / prec, math.sqrt(1.0 / prec)

    def _draw(self, regime: str) -> dict:
        pick = {}
        for p, vals in self.arms.items():
            best, bv = None, -1e18
            for v in vals:
                m, sd = self.posterior(regime, p, v)
                x = self.rng.gauss(m, sd)
                if x > bv:
                    best, bv = v, x
            pick[p] = best
        return pick

    def select(self, regime: str) -> dict:
        regime = regime if regime in REGIMES else 'MID'
        banned = self.banned[regime]
        pick = self._draw(regime)
        for _ in range(50):
            if tuple(sorted(pick.items())) not in banned:
                break
            pick = self._draw(regime)
        else:                                           # every draw banned: move the first parameter off its arm
            p0 = next(iter(self.arms))
            alts = [v for v in self.arms[p0] if v != pick[p0]]
            pick[p0] = self.rng.choice(alts)
        for k in list(banned):                         # bans count down per selection in that regime
            banned[k] -= 1
            if banned[k] <= 0:
                del banned[k]
        return {'regime': regime, **pick}

    def update(self, choice: Optional[dict], reward_bps: float) -> list:
        """Reward the arms of `choice` (as returned by select, stored on the position)."""
        events = []
        if not choice or choice.get('regime') not in REGIMES:
            return events
        r = choice['regime']
        combo = tuple(sorted((p, choice[p]) for p in self.arms if p in choice))
        for p, v in combo:
            if v in self.stats[r][p]:
                st = self.stats[r][p][v]
                st[0] += 1
                st[1] += reward_bps
        if combo == self.last_combo[r] and reward_bps < 0:
            self.combo_losses[r] += 1
        else:
            self.combo_losses[r] = 1 if reward_bps < 0 else 0
        self.last_combo[r] = combo
        if self.combo_losses[r] >= self.ban_after:
            self.banned[r][combo] = self.ban_trades
            self.combo_losses[r] = 0
            events.append(f'bandit {r}: ban {dict(combo)} for {self.ban_trades} selections '
                          f'({self.ban_after} losses in a row)')
        self.history.append({'t': self.clock(), 'regime': r, 'arms': dict(combo), 'reward_bps': reward_bps})
        self.history = self.history[-500:]
        return events

    def view(self) -> dict:
        out = {}
        for r in REGIMES:
            out[r] = {p: {str(v): {'n': n, 'mean_bps': (s / n if n else None)}
                          for v, (n, s) in vals.items()} for p, vals in self.stats[r].items()}
            out[r]['banned'] = [dict(c) for c in self.banned[r]]
        return out

    def warm_start(self, rows) -> int:
        n = 0
        for row in rows:
            ch = row.get('arms')
            if ch and row.get('qty') and row.get('entry'):
                notional = float(row['qty']) * float(row['entry'])
                self.update(ch, float(row.get('pnl') or 0.0) / notional * 1e4 if notional else 0.0)
                n += 1
        for r in REGIMES:
            self.banned[r].clear()
            self.combo_losses[r] = 0
        return n

    def save(self, path: Path) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps({'t': self.clock(), 'bandit': self.view()}, default=float), encoding='utf-8')
        except OSError:
            pass
