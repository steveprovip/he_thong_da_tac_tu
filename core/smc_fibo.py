"""SMC Fibo-discount liquidity-sweep detector on CLOSED bars (2026-11-13), one code path for replay and live.

Long setup (short = mirror), spec frozen in docs/harness/plan_alpha_reengineering_2026_11_13.md section B:
  1. H4 context    core.smc.SMCState(swing) trend == +1
  2. H1 leg        last confirmed swing low L, then a confirmed swing high H; H - L >= leg_atr_min x ATR(H1);
                   since H no trade above H and no close below L (the pullback is live, the structure intact)
  3. discount zone the 5 m bar's low lies in [L, H - zone_lo x (H - L)]   (0.705 .. 1.0 retracement)
  4. sweep         that low is below the last unswept confirmed 5 m swing low and the close is back above it
  5. absorption    taker delta z <= -delta_z vs the previous delta_n bars, close location (C-L)/(H-L) >= clv_min
  6. filters       ATR(H1) / price >= min_atr_h1_pct; risk (entry - SL) / entry in [risk_min_pct, risk_max_pct]
  Orders proposed: entry = sweep-bar close (maker limit), SL = sweep low - sl_atr_buf x ATR(5 m), TP = entry + R x risk.
Swings are fractals with `swing` bars on each side, usable only once confirmed (no lookahead). Feed bars in time order;
when an H4 / H1 bar and a 5 m bar close at the same instant, feed the higher timeframe first.
Heuristic structure, not a calibrated probability: research/smc_fibo_backtest.py measures it against costs.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Optional

from core.smc import SMCState


@dataclass(frozen=True)
class Bar:
    t: int                 # close time (ms); any monotonic clock works
    o: float
    h: float
    l: float
    c: float
    v: float = 0.0         # base volume
    tb: float = 0.0        # taker-buy base volume (delta = 2 tb - v)


@dataclass(frozen=True)
class Setup:
    t: int
    side: str              # 'BUY' | 'SELL'
    entry: float
    sl: float
    tp: float
    risk_pct: float
    leg_lo: float
    leg_hi: float
    retrace: float         # depth of the swept extreme inside the leg, 0 = at the leg end, 1 = at its origin
    swept: float           # liquidity level that was swept
    delta_z: float
    clv: float
    atr_h1_pct: float


class _ATR:
    def __init__(self, n: int):
        self.tr, self.prev = deque(maxlen=n), None

    def update(self, h: float, l: float, c: float) -> None:
        pc = self.prev
        self.tr.append(h - l if pc is None else max(h - l, abs(h - pc), abs(l - pc)))
        self.prev = c

    @property
    def value(self) -> Optional[float]:
        return sum(self.tr) / len(self.tr) if len(self.tr) == self.tr.maxlen else None


class _Fractal:
    """Confirmed fractal pivots: the middle of 2k+1 bars is a swing high (low) if strictly above (below) the rest."""

    def __init__(self, k: int):
        self.k, self.win = k, deque(maxlen=2 * k + 1)

    def update(self, bar: Bar) -> tuple[Optional[Bar], Optional[Bar]]:
        self.win.append(bar)
        if len(self.win) < self.win.maxlen:
            return None, None
        mid = self.win[self.k]
        others = [b for i, b in enumerate(self.win) if i != self.k]
        hi = mid if all(mid.h > b.h for b in others) else None
        lo = mid if all(mid.l < b.l for b in others) else None
        return hi, lo


class SMCFibo:
    def __init__(self, r_multiple: float = 2.0, leg_atr_min: float = 2.0, zone_lo: float = 0.705,
                 swing_5m: int = 2, swing_h1: int = 2, swing_h4: int = 2, delta_n: int = 48, delta_z: float = 1.0,
                 clv_min: float = 0.5, atr_n: int = 14, min_atr_h1_pct: float = 0.25, risk_min_pct: float = 0.10,
                 risk_max_pct: float = 1.5, sl_atr_buf: float = 0.10):
        if not (r_multiple > 0 and leg_atr_min >= 0 and 0 < zone_lo < 1 and delta_n >= 2 and 0 <= clv_min <= 1
                and 0 < risk_min_pct < risk_max_pct):
            raise ValueError('invalid SMCFibo parameters')
        self.r, self.leg_atr_min, self.zone_lo = r_multiple, leg_atr_min, zone_lo
        self.delta_n, self.delta_zmin, self.clv_min = delta_n, delta_z, clv_min
        self.min_atr_h1_pct, self.risk_min, self.risk_max, self.sl_buf = min_atr_h1_pct, risk_min_pct, risk_max_pct, sl_atr_buf
        self.h4 = SMCState(swing=swing_h4, atr_n=atr_n)
        self.trend = 0
        self.h1_frac, self.h1_atr = _Fractal(swing_h1), _ATR(atr_n)
        self.h1_swings: deque = deque(maxlen=8)          # ('H' | 'L', bar) confirmed, in time order
        self.m5_frac, self.m5_atr = _Fractal(swing_5m), _ATR(atr_n)
        self.liq_lo: Optional[float] = None             # last unswept confirmed 5 m swing low
        self.liq_hi: Optional[float] = None
        self.deltas: deque = deque(maxlen=delta_n)
        self.leg_long = self.leg_short = None           # dict(lo, hi, t_end): the live impulse legs

    # ------------------------------------------------------------------ higher timeframes
    def on_h4(self, bar: Bar) -> None:
        self.trend = self.h4.update(bar.h, bar.l, bar.c, bar.o).trend

    def on_h1(self, bar: Bar) -> None:
        self.h1_atr.update(bar.h, bar.l, bar.c)
        hi, lo = self.h1_frac.update(bar)
        for kind, piv in (('L', lo), ('H', hi)):
            if piv is not None:
                self.h1_swings.append((kind, piv))
        after = list(self.h1_frac.win)[self.h1_frac.k + 1:]   # bars between the pivot and its confirmation
        if hi is not None:                               # a new swing high closes a bullish leg L -> H
            prev_lo = next((b for k, b in reversed(self.h1_swings) if k == 'L' and b.t < hi.t), None)
            ok = prev_lo is not None and not any(b.c < prev_lo.l for b in after)
            self.leg_long = {'lo': prev_lo.l, 'hi': hi.h, 't_end': hi.t} if ok else None
        if lo is not None:                               # a new swing low closes a bearish leg H -> L
            prev_hi = next((b for k, b in reversed(self.h1_swings) if k == 'H' and b.t < lo.t), None)
            ok = prev_hi is not None and not any(b.c > prev_hi.h for b in after)
            self.leg_short = {'lo': lo.l, 'hi': prev_hi.h, 't_end': lo.t} if ok else None
        self._track(bar)

    def _track(self, bar: Bar) -> None:
        """Invalidate a leg once price trades beyond its end (no pullback) or closes beyond its origin."""
        lg = self.leg_long
        if lg is not None and bar.t > lg['t_end']:
            if bar.h > lg['hi'] or bar.c < lg['lo']:
                self.leg_long = None
        sg = self.leg_short
        if sg is not None and bar.t > sg['t_end']:
            if bar.l < sg['lo'] or bar.c > sg['hi']:
                self.leg_short = None

    # ------------------------------------------------------------------ execution timeframe
    def on_5m(self, bar: Bar) -> Optional[Setup]:
        delta = 2.0 * bar.tb - bar.v
        z = None
        if len(self.deltas) == self.deltas.maxlen:
            m = sum(self.deltas) / len(self.deltas)
            sd = (sum((d - m) ** 2 for d in self.deltas) / (len(self.deltas) - 1)) ** 0.5
            z = (delta - m) / sd if sd > 0 else None
        atr5_prev = self.m5_atr.value
        out = None
        if z is not None and atr5_prev is not None and self.trend != 0:
            out = self._setup(bar, z, atr5_prev)
        # state updates AFTER the decision: this bar's own pivot / ATR cannot inform its own setup
        self.deltas.append(delta)
        self.m5_atr.update(bar.h, bar.l, bar.c)
        if self.liq_lo is not None and bar.l < self.liq_lo:
            self.liq_lo = None                           # swept (whether or not it made a setup)
        if self.liq_hi is not None and bar.h > self.liq_hi:
            self.liq_hi = None
        hi, lo = self.m5_frac.update(bar)
        if lo is not None:
            self.liq_lo = lo.l
        if hi is not None:
            self.liq_hi = hi.h
        self._track(bar)
        return out

    def _setup(self, bar: Bar, z: float, atr5: float) -> Optional[Setup]:
        atr1 = self.h1_atr.value
        if atr1 is None or bar.c <= 0 or atr1 / bar.c * 100.0 < self.min_atr_h1_pct:
            return None
        rng = bar.h - bar.l
        clv = (bar.c - bar.l) / rng if rng > 0 else 0.5
        atr1_pct = atr1 / bar.c * 100.0
        if self.trend > 0 and self.leg_long is not None and self.liq_lo is not None:
            lg = self.leg_long
            span = lg['hi'] - lg['lo']
            zone_top = lg['hi'] - self.zone_lo * span
            if (span >= self.leg_atr_min * atr1 and bar.t > lg['t_end'] and lg['lo'] <= bar.l <= zone_top
                    and bar.l < self.liq_lo < bar.c and z <= -self.delta_zmin and clv >= self.clv_min):
                sl = bar.l - self.sl_buf * atr5
                risk = (bar.c - sl) / bar.c * 100.0
                if self.risk_min <= risk <= self.risk_max:
                    return Setup(bar.t, 'BUY', bar.c, sl, bar.c + self.r * (bar.c - sl), risk, lg['lo'], lg['hi'],
                                 (lg['hi'] - bar.l) / span, self.liq_lo, z, clv, atr1_pct)
        if self.trend < 0 and self.leg_short is not None and self.liq_hi is not None:
            sg = self.leg_short
            span = sg['hi'] - sg['lo']
            zone_bot = sg['lo'] + self.zone_lo * span
            if (span >= self.leg_atr_min * atr1 and bar.t > sg['t_end'] and zone_bot <= bar.h <= sg['hi']
                    and bar.h > self.liq_hi > bar.c and z >= self.delta_zmin and clv <= 1.0 - self.clv_min):
                sl = bar.h + self.sl_buf * atr5
                risk = (sl - bar.c) / bar.c * 100.0
                if self.risk_min <= risk <= self.risk_max:
                    return Setup(bar.t, 'SELL', bar.c, sl, bar.c - self.r * (sl - bar.c), risk, sg['lo'], sg['hi'],
                                 (bar.h - sg['lo']) / span, self.liq_hi, z, clv, atr1_pct)
        return None
