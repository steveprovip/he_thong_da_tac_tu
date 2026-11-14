"""Net-expectancy entry gate for bracket trades (2026-11-13): no order without evidence that the lane's net EV > 0.

Bracket arithmetic (bps, per trade, fees included):
    EV(p)          = p (TP - c_win) - (1 - p) (SL + c_loss)
    break-even p   = (SL + c_loss) / (TP - c_win + SL + c_loss)
    driftless p    = SL / (TP + SL)        (a random walk hits +TP before -SL with this probability)
    EV(driftless)  = -(c_win SL + c_loss TP) / (TP + SL)  < 0 whenever costs are > 0, for ANY reward:risk.
So R:R alone never creates edge; only directional skill (p above the driftless p by more than the cost margin) does.

Gate (`verdict`): the evidence is the NET bps per settled trade of the lane, real (ledger) and virtual (shadow path).
    real n >= min_n and UCB95 < 0            -> REFUSE  (proven negative; virtual evidence cannot override it)
    real n >= min_n and LCB95 > 0            -> ALLOW
    virtual n >= min_n and LCB95 > 0         -> ALLOW   (real inconclusive or too short)
    otherwise                                -> REFUSE  (unproven: the price is treated as driftless, EV = -costs)
UNKNOWN results (None / NaN) are dropped, never counted as zero. core/net_ev.py (p_fill delta-method head of the
swarm) is a different model and is left untouched.
"""
from __future__ import annotations

import math
from typing import Iterable, Optional

Z_95_ONE_SIDED = 1.645
MIN_N = 30


def ev_bps(p_win: float, tp_bps: float, sl_bps: float, c_win_bps: float, c_loss_bps: float) -> float:
    return p_win * (tp_bps - c_win_bps) - (1.0 - p_win) * (sl_bps + c_loss_bps)


def breakeven_p(tp_bps: float, sl_bps: float, c_win_bps: float, c_loss_bps: float) -> float:
    den = tp_bps - c_win_bps + sl_bps + c_loss_bps
    if den <= 0:
        return 1.0
    return min(1.0, max(0.0, (sl_bps + c_loss_bps) / den))


def driftless_p(tp_bps: float, sl_bps: float) -> float:
    if tp_bps <= 0 or sl_bps <= 0:
        raise ValueError('TP and SL distances must be > 0')
    return sl_bps / (tp_bps + sl_bps)


def driftless_ev_bps(tp_bps: float, sl_bps: float, c_win_bps: float, c_loss_bps: float) -> float:
    return ev_bps(driftless_p(tp_bps, sl_bps), tp_bps, sl_bps, c_win_bps, c_loss_bps)


def mean_bounds(xs: Iterable[Optional[float]], z: float = Z_95_ONE_SIDED) -> dict:
    """n, mean, standard error and one-sided bounds of a sample; UNKNOWN entries are skipped."""
    v = [float(x) for x in xs if x is not None and math.isfinite(float(x))]
    n = len(v)
    if n == 0:
        return {'n': 0, 'mean': None, 'se': None, 'lcb': None, 'ucb': None}
    m = math.fsum(v) / n
    if n < 2:
        return {'n': n, 'mean': m, 'se': None, 'lcb': None, 'ucb': None}
    sd = math.sqrt(math.fsum((x - m) ** 2 for x in v) / (n - 1))
    se = sd / math.sqrt(n)
    return {'n': n, 'mean': m, 'se': se, 'lcb': m - z * se, 'ucb': m + z * se}


def verdict(real_bps: Iterable[Optional[float]], virtual_bps: Iterable[Optional[float]] = (), *,
            tp_bps: Optional[float] = None, sl_bps: Optional[float] = None,
            c_win_bps: float = 4.0, c_loss_bps: float = 8.0, min_n: int = MIN_N,
            z: float = Z_95_ONE_SIDED) -> dict:
    """ALLOW / REFUSE an entry from the lane's evidence; see the module docstring for the rule."""
    r, v = mean_bounds(real_bps, z), mean_bounds(virtual_bps, z)
    ev0 = (driftless_ev_bps(tp_bps, sl_bps, c_win_bps, c_loss_bps)
           if tp_bps and sl_bps and tp_bps > 0 and sl_bps > 0 else None)
    out = {'real': r, 'virtual': v, 'driftless_ev_bps': ev0, 'min_n': min_n,
           'breakeven_p': breakeven_p(tp_bps, sl_bps, c_win_bps, c_loss_bps) if ev0 is not None else None}
    fmt = lambda x: '—' if x is None else f'{x:+.2f}'
    if r['n'] >= min_n and r['ucb'] is not None and r['ucb'] < 0:
        return {**out, 'allow': False, 'state': 'NEGATIVE',
                'why': f'NET EV NEGATIVE: real {r["n"]} trades mean {fmt(r["mean"])} bps, UCB95 {fmt(r["ucb"])} < 0'}
    if r['n'] >= min_n and r['lcb'] is not None and r['lcb'] > 0:
        return {**out, 'allow': True, 'state': 'PROVEN_REAL',
                'why': f'NET EV PROVEN: real {r["n"]} trades LCB95 {fmt(r["lcb"])} bps > 0'}
    if v['n'] >= min_n and v['lcb'] is not None and v['lcb'] > 0:
        return {**out, 'allow': True, 'state': 'PROVEN_VIRTUAL',
                'why': f'NET EV PROVEN (virtual): {v["n"]} shadow trades LCB95 {fmt(v["lcb"])} bps > 0'}
    return {**out, 'allow': False, 'state': 'UNPROVEN',
            'why': (f'NET EV UNPROVEN: real {r["n"]}/{min_n} mean {fmt(r["mean"])} LCB95 {fmt(r["lcb"])}; virtual '
                    f'{v["n"]}/{min_n} LCB95 {fmt(v["lcb"])} bps; driftless EV of this bracket {fmt(ev0)} bps')}
