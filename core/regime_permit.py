"""Slow-Brain -> Fast-Hands permit.  The slow brain may only BLOCK.

Fast-Hands (the 8-agent swarm) is allowed to quote only when the regime can
pay for the round trip.  Three independent ways to earn a permit, checked in
order; the first hard block wins and nothing downstream can override it:

  1. Slow-brain verdict (TradingAgents debate / regime model, validated by
     phan_tich.slow_brain_contract.validate_verdict): stale, malformed, CHOP,
     TOXIC or veto  ->  STAND_ASIDE.  A PASS verdict grants nothing by itself.
  2. CARRY: funding income over the holding horizon covers the two-leg cost
     with the ROLE.md margin  G > 3C  (G - C > 2C).  Direction-neutral, so it
     does not need a directional edge.
  3. SCALP: forward evidence (signal lab / early-stop gate) shows mean gross
     edge >= 9 bps with LCB95(gross) > all-in cost 8.5 bps, the spread sits in
     the 2-8 bps band and ATR in the 6-20 bps band, and the early-stop gate
     has not declared REGIME_UNPROFITABLE.  No evidence -> no permit.

Measured on this repo (docs/CHAN_DOAN_TOI_UU_VONG_LAP.md): 6795 lab probes
gross +0.96 bps, best out-of-sample filter F4 +4.39 bps, L2 maker markout
-0.7..-1.2 bps.  Nothing clears 9 bps, so today the permit is STAND_ASIDE for
scalping -- which is the point: zero fills beats negative fills.
"""
from dataclasses import dataclass, asdict
import math

from core.early_stop import COST_ALL_IN_BPS, GROSS_EDGE_HURDLE_BPS, REGIME_UNPROFITABLE

SCALP, CARRY, STAND_ASIDE = 'SCALP', 'CARRY', 'STAND_ASIDE'


@dataclass(frozen=True)
class PermitPolicy:
    cost_all_in_bps: float = COST_ALL_IN_BPS
    gross_hurdle_bps: float = GROSS_EDGE_HURDLE_BPS
    min_spread_bps: float = 2.0
    max_spread_bps: float = 8.0
    min_atr_bps: float = 6.0
    max_atr_bps: float = 20.0
    pair_cost_bps: float = 2 * COST_ALL_IN_BPS     # spot leg + perp leg, in and out
    carry_cost_multiple: float = 3.0               # G > 3C

    def __post_init__(self):
        values = tuple(asdict(self).values())
        if not all(math.isfinite(v) and v >= 0 for v in values):
            raise ValueError('permit policy values must be finite and non-negative')
        if self.min_spread_bps > self.max_spread_bps or self.min_atr_bps > self.max_atr_bps:
            raise ValueError('band floor above ceiling')
        if self.gross_hurdle_bps < self.cost_all_in_bps:
            raise ValueError('gross hurdle below cost would license fee drag')


@dataclass(frozen=True)
class Permit:
    allowed: bool
    mode: str
    reason: str

    def as_dict(self):
        return asdict(self)


def _finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) for v in values)


def fast_hands_permit(spread_bps, atr_bps, oos_gross_bps=None, oos_gross_lcb_bps=None,
                      lab_status=None, funding_bps_per_period=0.0, funding_periods=0,
                      verdict=None, intent_ts=None, policy=None):
    policy = policy or PermitPolicy()
    if not _finite(spread_bps, atr_bps, funding_bps_per_period) or spread_bps < 0 or atr_bps < 0:
        return Permit(False, STAND_ASIDE, 'INVALID_INPUT')
    if not isinstance(funding_periods, int) or isinstance(funding_periods, bool) or funding_periods < 0:
        return Permit(False, STAND_ASIDE, 'INVALID_INPUT')
    if verdict is not None:
        from phan_tich.slow_brain_contract import validate_verdict
        ok, why = validate_verdict(verdict, intent_ts)
        if not ok:
            return Permit(False, STAND_ASIDE, f'SLOW_BRAIN {why}')

    carry_income = abs(funding_bps_per_period) * funding_periods
    carry_needed = policy.carry_cost_multiple * policy.pair_cost_bps
    if funding_periods and carry_income > carry_needed:
        return Permit(True, CARRY, f'FUNDING {carry_income:.1f}>{carry_needed:.1f} bps (G>3C)')

    if lab_status == REGIME_UNPROFITABLE:
        return Permit(False, STAND_ASIDE, 'REGIME_UNPROFITABLE (early-stop gate)')
    if not policy.min_spread_bps <= spread_bps <= policy.max_spread_bps:
        return Permit(False, STAND_ASIDE,
                      f'SPREAD {spread_bps:.2f} outside {policy.min_spread_bps:g}-{policy.max_spread_bps:g} bps')
    if not policy.min_atr_bps <= atr_bps <= policy.max_atr_bps:
        return Permit(False, STAND_ASIDE,
                      f'ATR {atr_bps:.2f} outside {policy.min_atr_bps:g}-{policy.max_atr_bps:g} bps')
    if oos_gross_bps is None or oos_gross_lcb_bps is None or not _finite(oos_gross_bps, oos_gross_lcb_bps):
        return Permit(False, STAND_ASIDE, 'NO_OOS_EVIDENCE')
    if oos_gross_bps < policy.gross_hurdle_bps:
        return Permit(False, STAND_ASIDE,
                      f'OOS gross {oos_gross_bps:.2f}<{policy.gross_hurdle_bps:g} bps hurdle')
    if oos_gross_lcb_bps <= policy.cost_all_in_bps:
        return Permit(False, STAND_ASIDE,
                      f'OOS LCB95 {oos_gross_lcb_bps:.2f}<=cost {policy.cost_all_in_bps:g} bps')
    return Permit(True, SCALP, f'OOS gross {oos_gross_bps:.2f} LCB95 {oos_gross_lcb_bps:.2f} bps')
