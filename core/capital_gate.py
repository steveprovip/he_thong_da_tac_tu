"""Hard financial ceiling for a 3.85 USDT book.

    B_hard = 3.85 USDT            absolute spend ceiling
    N_cap  = 0.15 * B_hard        = 0.5775 USDT, largest single unlevered position
    N_min  > N_cap  =>  PAPER_ONLY
    spend + reserved <= B_hard

N_cap is *unlevered notional*. Raising account leverage to fit a venue minimum
under the cap is explicitly not a way through this gate: leverage multiplies
loss per adverse tick, so it widens the very exposure the cap exists to bound.
The gate reads the venue minimum as given and refuses live execution when the
smallest legal order is larger than the cap allows.

On Binance USD-M futures the minimum notional is 5 USDT, an order of magnitude
above a 0.5775 USDT cap, so this book is structurally PAPER_ONLY. That is the
intended verdict, not a misconfiguration: the honest conclusion from a budget
this small is that it cannot take a compliant live position at all.
"""
import math
from dataclasses import dataclass

__all__ = ['CapitalVerdict', 'capital_verdict', 'position_cap',
           'B_HARD_USDT', 'POSITION_FRACTION', 'N_CAP_USDT',
           'EXECUTE', 'PAPER_ONLY', 'BUDGET_EXHAUSTED']

B_HARD_USDT = 3.85
POSITION_FRACTION = 0.15
N_CAP_USDT = 0.5775            # 0.15 * 3.85, stated in the spec

EXECUTE = 'EXECUTE'
PAPER_ONLY = 'PAPER_ONLY'
BUDGET_EXHAUSTED = 'BUDGET_EXHAUSTED'


def _finite(*values):
    return all(isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v) for v in values)


def position_cap(b_hard=B_HARD_USDT, fraction=POSITION_FRACTION):
    """N_cap = fraction * B_hard, the largest single unlevered position."""
    if not _finite(b_hard, fraction) or b_hard <= 0 or not 0 < fraction <= 1:
        raise ValueError('invalid capital ceiling configuration')
    return fraction * b_hard


@dataclass(frozen=True)
class CapitalVerdict:
    mode: str
    n_cap: float
    headroom: float
    min_notional: float
    reason: str

    @property
    def live_allowed(self):
        return self.mode == EXECUTE


def capital_verdict(min_notional, spend=0.0, reserved=0.0,
                    b_hard=B_HARD_USDT, fraction=POSITION_FRACTION):
    """Decide EXECUTE / PAPER_ONLY / BUDGET_EXHAUSTED for the next order.

    `min_notional` is the venue's smallest legal order for the symbol, already
    including any broker buffer the sizer applies.
    """
    if not _finite(min_notional, spend, reserved) or min_notional <= 0:
        raise ValueError('invalid capital inputs')
    if spend < 0 or reserved < 0:
        raise ValueError('spend and reserved must be non-negative')
    n_cap = position_cap(b_hard, fraction)
    committed = spend + reserved
    headroom = b_hard - committed

    if committed > b_hard:
        return CapitalVerdict(BUDGET_EXHAUSTED, n_cap, headroom, min_notional,
                              f'spend+reserved {committed:.4f} > B_hard {b_hard:.4f}')
    if min_notional > n_cap:
        return CapitalVerdict(PAPER_ONLY, n_cap, headroom, min_notional,
                              f'N_min {min_notional:.4f} > N_cap {n_cap:.4f}')
    if min_notional > headroom:
        return CapitalVerdict(BUDGET_EXHAUSTED, n_cap, headroom, min_notional,
                              f'N_min {min_notional:.4f} > headroom {headroom:.4f}')
    return CapitalVerdict(EXECUTE, n_cap, headroom, min_notional, 'within ceiling')
