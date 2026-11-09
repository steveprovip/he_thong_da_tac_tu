"""Smart Money Concepts on CLOSED bars: causal, O(1) amortised per bar, one code path for live and replay.

Per bar (open optional; the previous close stands in when absent, as in core.real_alpha_engine bars):
  swings     fractal pivots with `swing` bars on each side, CONFIRMED `swing` bars later (no lookahead)
  BOS/CHoCH  close beyond the last confirmed, unbroken swing high/low; CHoCH when the break goes against the
             current structure, BOS otherwise
  sweep      wick beyond that swing level with the close back inside (liquidity grab): +1 sell side swept
             (bullish), -1 buy side swept (bearish)
  FVG        3-bar gap: low[i] > high[i-2] (bull) / high[i] < low[i-2] (bear); open until price trades through it
  OB         last opposite-colour bar within `ob_lookback` bars before a structure break; void once a close
             goes through its far side
Distances are (zone mid - close) / ATR (mean true range over `atr_n` bars), clipped to +-`clip`; 0 when no zone.
Heuristic features, not calibrated probabilities: research/smc_backtest.py measures them against costs.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass

import numpy as np

FIELDS = ('trend', 'bos', 'choch', 'sweep', 'fvg_dist', 'ob_dist', 'n_fvg')


@dataclass(frozen=True)
class SMCSnapshot:
    trend: int = 0          # +1 bullish structure, -1 bearish, 0 unknown
    bos: int = 0            # break of structure on this bar
    choch: int = 0          # change of character on this bar
    sweep: int = 0          # liquidity sweep on this bar
    fvg_dist: float = 0.0   # nearest open fair value gap
    ob_dist: float = 0.0    # nearest live order block
    n_fvg: int = 0          # open fair value gaps, both sides


class SMCState:
    def __init__(self, swing: int = 2, atr_n: int = 14, ob_lookback: int = 20, max_zones: int = 32,
                 clip: float = 5.0):
        if swing < 1 or atr_n < 1 or ob_lookback < 1 or max_zones < 1 or not clip > 0:
            raise ValueError('invalid SMC parameters')
        self.k, self.clip = swing, clip
        self.win_h, self.win_l = deque(maxlen=2 * swing + 1), deque(maxlen=2 * swing + 1)
        self.bars = deque(maxlen=max(ob_lookback, 2))          # (open, high, low, close) of PREVIOUS bars
        self.tr = deque(maxlen=atr_n)
        self.prev_close = None
        self.sh = self.sl = None                               # [level, broken]
        self.trend = 0
        self.fvgs = deque(maxlen=max_zones)                    # (side, lo, hi)
        self.ob_bull = self.ob_bear = None                     # (lo, hi)
        self.last = SMCSnapshot()

    def _dist(self, zones, close, atr):
        if not zones or atr <= 0:
            return 0.0
        mid = min((0.5 * (lo + hi) for lo, hi in zones), key=lambda m: abs(m - close))
        return max(-self.clip, min(self.clip, (mid - close) / atr))

    def update(self, high: float, low: float, close: float, open_: float | None = None) -> SMCSnapshot:
        o = open_ if open_ is not None else (self.prev_close if self.prev_close is not None else close)
        pc = self.prev_close
        self.tr.append(high - low if pc is None else max(high, pc) - min(low, pc))
        atr = sum(self.tr) / len(self.tr)
        # 1. confirm the pivot `k` bars back (uses bars <= now only)
        self.win_h.append(high)
        self.win_l.append(low)
        if len(self.win_h) == 2 * self.k + 1:
            hs, ls, c = list(self.win_h), list(self.win_l), self.k
            if all(hs[c] > x for x in hs[:c]) and all(hs[c] >= x for x in hs[c + 1:]):
                self.sh = [hs[c], False]
            if all(ls[c] < x for x in ls[:c]) and all(ls[c] <= x for x in ls[c + 1:]):
                self.sl = [ls[c], False]
        # 2. void zones the current bar trades through (zones born on this bar are added after)
        self.fvgs = deque(((s, lo, hi) for s, lo, hi in self.fvgs if (low > lo if s > 0 else high < hi)),
                          maxlen=self.fvgs.maxlen)
        if self.ob_bull and close < self.ob_bull[0]:
            self.ob_bull = None
        if self.ob_bear and close > self.ob_bear[1]:
            self.ob_bear = None
        # 3. sweeps and structure breaks against the confirmed swing levels
        bos = choch = sweep = 0
        sh, sl = self.sh, self.sl
        if sh and not sh[1] and high > sh[0] and close <= sh[0]:
            sweep -= 1
        if sl and not sl[1] and low < sl[0] and close >= sl[0]:
            sweep += 1
        if sh and not sh[1] and close > sh[0]:
            choch, bos = (1, 0) if self.trend == -1 else (0, 1)
            self.trend, sh[1] = 1, True
            self.ob_bull = next(((lo, hi) for oo, hi, lo, cc in reversed(self.bars) if cc < oo), self.ob_bull)
        if sl and not sl[1] and close < sl[0]:
            choch, bos = (-1, 0) if self.trend == 1 else (0, -1)
            self.trend, sl[1] = -1, True
            self.ob_bear = next(((lo, hi) for oo, hi, lo, cc in reversed(self.bars) if cc > oo), self.ob_bear)
        # 4. fair value gap born on this bar
        if len(self.bars) >= 2:
            h2, l2 = self.bars[-2][1], self.bars[-2][2]
            if low > h2:
                self.fvgs.append((1, h2, low))
            elif high < l2:
                self.fvgs.append((-1, high, l2))
        self.bars.append((o, high, low, close))
        self.prev_close = close
        obs = [z for z in (self.ob_bull, self.ob_bear) if z]
        self.last = SMCSnapshot(self.trend, bos, choch, sweep,
                                self._dist([(lo, hi) for _, lo, hi in self.fvgs], close, atr),
                                self._dist(obs, close, atr), len(self.fvgs))
        return self.last


def smc_features(high, low, close, open_=None, **params) -> dict:
    """Batch replay: the same SMCState over arrays -> {field: np.ndarray}; value i depends on bars <= i only."""
    high, low, close = (np.asarray(x, dtype=np.float64) for x in (high, low, close))
    opens = None if open_ is None else np.asarray(open_, dtype=np.float64)
    n = close.size
    out = {f: np.zeros(n, dtype=np.float64 if f.endswith('dist') else np.int8) for f in FIELDS}
    st = SMCState(**params)
    for i, (h, lo, c) in enumerate(zip(high.tolist(), low.tolist(), close.tolist())):
        s = st.update(h, lo, c, None if opens is None else float(opens[i]))
        for f in FIELDS:
            out[f][i] = getattr(s, f)
    return out
