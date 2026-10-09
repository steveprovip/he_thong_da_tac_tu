"""Multi-horizon trend lane (1D / 4H): TSMOM ensemble + Donchian breakout, volatility targeting, funding overlay.

Pure Python (no numpy) so the engine process can run it as a PAPER lane next to the micro engine.

Signal on the last CLOSED bar i (never the forming bar):
  tsmom   = mean over lookbacks L of sign(close_i / close_{i-L} - 1)                    in [-1, 1]
  donch   = +1 after a close above the prior N-bar high, -1 after a close below the prior N-bar low,
            else the previous state (stateful breakout)                                 in {-1, 0, 1}
  score   = w_tsmom * tsmom + (1 - w_tsmom) * donch                                    in [-1, 1]
Sizing (per symbol, share 1/N of equity):
  weight  = score * min(max_lev, vol_target / realised_vol_ann(vol_window)) / N
Funding overlay (crowding): trailing 3-day mean funding per 8 h > +funding_hot -> long weight x funding_scale;
  < -funding_hot -> short weight x funding_scale.
Rebalance only when |target - current| > band (fraction of equity): fewer, larger trades.

Evidence (docs/research_upgrade/report_high_speed_replay_and_horizon_pivot_2026-10-09.md): literature + our
factor scan support 1D trend after fees as a HYPOTHESIS (OOS PSR 0.61-0.72, DSR < 0.95).  The lane is
PAPER ONLY: it never sends orders.
"""
from __future__ import annotations

import json
import math
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

BAR_HOURS = {'1d': 24, '4h': 4}


@dataclass(frozen=True)
class TrendConfig:
    bar: str = '1d'
    lookbacks: Tuple[int, ...] = (5, 20, 60)        # bars (days on 1d; on 4h the caller scales x6)
    donchian_n: int = 20
    w_tsmom: float = 0.5
    vol_target: float = 0.20                         # annualised, per symbol sleeve
    vol_window: int = 30
    max_lev: float = 1.5                             # cap of vol_target / realised vol
    long_only: bool = False
    funding_hot: float = 0.00025                     # 2.5x the 0.01 %/8 h baseline (fixed a priori, untuned)
    funding_scale: float = 0.5
    funding_window_h: float = 72.0
    band: float = 0.05                               # rebalance band, fraction of equity per symbol
    cost_bps: float = 6.0                            # per side: taker 5 + slip 1 (maker 2 if it fills)

    def bars_per_year(self) -> float:
        return 365.0 * 24.0 / BAR_HOURS[self.bar]

    def scaled(self) -> 'TrendConfig':
        """4h bars: same calendar horizons as the daily spec (x6 bars)."""
        if self.bar != '4h':
            return self
        k = 24 // BAR_HOURS['4h']
        from dataclasses import replace
        return replace(self, lookbacks=tuple(L * k for L in self.lookbacks), donchian_n=self.donchian_n * k,
                       vol_window=self.vol_window * k)


# ------------------------------------------------------------------ components
def sign(x: float) -> float:
    return 1.0 if x > 0 else (-1.0 if x < 0 else 0.0)


def tsmom(closes: Sequence[float], i: int, lookbacks: Sequence[int]) -> Optional[float]:
    if i < max(lookbacks):
        return None
    return sum(sign(closes[i] / closes[i - L] - 1.0) for L in lookbacks) / len(lookbacks)


def donchian_states(highs, lows, closes, n: int) -> List[float]:
    out, st = [], 0.0
    for i in range(len(closes)):
        if i >= n:
            if closes[i] > max(highs[i - n:i]):
                st = 1.0
            elif closes[i] < min(lows[i - n:i]):
                st = -1.0
        out.append(st if i >= n else 0.0)
    return out


def realised_vol(closes: Sequence[float], i: int, n: int, bars_per_year: float) -> Optional[float]:
    if i < n:
        return None
    r = [math.log(closes[k] / closes[k - 1]) for k in range(i - n + 1, i + 1)]
    m = sum(r) / n
    var = sum((x - m) ** 2 for x in r) / (n - 1)
    return math.sqrt(var * bars_per_year) if var > 0 else None


def trailing_funding(funding: Sequence[Tuple[float, float]], t: float, window_h: float) -> Optional[float]:
    """funding: [(t_s, rate)] sorted; mean of prints in (t - window, t]."""
    xs = [r for ft, r in funding if t - window_h * 3600 < ft <= t]
    return sum(xs) / len(xs) if xs else None


def weights_series(bars: Dict[str, dict], cfg: TrendConfig, funding: Optional[Dict[str, list]] = None):
    """bars[sym] = {'t': [open_s], 'o','h','l','c': [...]} on cfg.bar.  -> {sym: [target weight per bar]} and
    component dicts.  Weight at index i is decided on the close of bar i."""
    c = cfg.scaled()
    n_sym = max(1, len(bars))
    out, comp = {}, {}
    for s, b in bars.items():
        closes, highs, lows, ts = b['c'], b['h'], b['l'], b['t']
        don = donchian_states(highs, lows, closes, c.donchian_n)
        hours = BAR_HOURS[c.bar]
        ws, cs = [], []
        for i in range(len(closes)):
            ts_i = tsmom(closes, i, c.lookbacks)
            rv = realised_vol(closes, i, c.vol_window, c.bars_per_year())
            if ts_i is None or rv is None:
                ws.append(0.0)
                cs.append(None)
                continue
            score = c.w_tsmom * ts_i + (1.0 - c.w_tsmom) * don[i]
            if c.long_only:
                score = max(0.0, score)
            w = score * min(c.max_lev, c.vol_target / rv) / n_sym
            fr = trailing_funding(funding.get(s, []), ts[i] + hours * 3600, c.funding_window_h) if funding else None
            if fr is not None and ((w > 0 and fr > c.funding_hot) or (w < 0 and fr < -c.funding_hot)):
                w *= c.funding_scale
            ws.append(w)
            cs.append({'tsmom': ts_i, 'donch': don[i], 'score': score, 'rv': rv, 'funding': fr})
        out[s], comp[s] = ws, cs
    return out, comp


def apply_band(targets: Sequence[float], band: float) -> List[float]:
    """Held weight: move to the target only when it differs by more than `band` (or flips / goes flat)."""
    held, cur = [], 0.0
    for w in targets:
        if (w == 0.0 and cur != 0.0) or sign(w) != sign(cur) or abs(w - cur) > band:
            cur = w
        held.append(cur)
    return held


# ------------------------------------------------------------------ backtest
def backtest(bars: Dict[str, dict], cfg: TrendConfig, funding: Optional[Dict[str, list]] = None,
             cost_bps: Optional[float] = None):
    """-> {'bar_returns': {t_open_s: net return}, 'turnover': {t: sum |dw|}, 'trades': [...] }.
    Return of bar i+1 uses the weight decided on the close of bar i (no look-ahead).  Funding charged on the
    weight held through each print (long pays positive)."""
    cost = (cfg.cost_bps if cost_bps is None else cost_bps) / 1e4
    tw, _ = weights_series(bars, cfg, funding)
    hours = BAR_HOURS[cfg.bar]
    rets, turn, trades = {}, {}, []
    for s, b in bars.items():
        held = apply_band(tw[s], cfg.band)
        closes, ts = b['c'], b['t']
        fr = funding.get(s, []) if funding else []
        fi = 0
        open_tr = None
        for i in range(1, len(closes)):
            w_prev = held[i - 1]
            r = closes[i] / closes[i - 1] - 1.0
            f_sum = 0.0
            t_lo, t_hi = ts[i], ts[i] + hours * 3600
            while fi < len(fr) and fr[fi][0] < t_lo:
                fi += 1
            k = fi
            while k < len(fr) and fr[k][0] < t_hi:
                f_sum += fr[k][1]
                k += 1
            dw = abs(held[i] - w_prev)
            pnl = w_prev * r - w_prev * f_sum - dw * cost      # trade at the close of bar i
            rets[ts[i]] = rets.get(ts[i], 0.0) + pnl
            turn[ts[i]] = turn.get(ts[i], 0.0) + dw
            # trade bookkeeping (sign episodes)
            if open_tr is not None:
                open_tr['pnl'] += w_prev * r - w_prev * f_sum
                open_tr['exposure'] += abs(w_prev)
                open_tr['bars'] += 1
            if sign(held[i]) != sign(w_prev):
                if open_tr is not None:
                    open_tr['pnl'] -= abs(w_prev) * cost
                    open_tr['exit_t'] = ts[i]
                    trades.append(open_tr)
                    open_tr = None
                if held[i] != 0.0:
                    open_tr = {'symbol': s, 'side': 'LONG' if held[i] > 0 else 'SHORT', 'entry_t': ts[i],
                               'pnl': -abs(held[i]) * cost, 'exposure': 0.0, 'bars': 0}
            elif open_tr is not None and dw:
                open_tr['pnl'] -= dw * cost
        if open_tr is not None:
            open_tr['exit_t'] = None
            trades.append(open_tr)
    for tr in trades:
        avg = tr['exposure'] / tr['bars'] if tr['bars'] else 0.0
        tr['hold_h'] = tr['bars'] * hours
        tr['ret_on_notional_bps'] = tr['pnl'] / avg * 1e4 if avg else 0.0
    return {'bar_returns': rets, 'turnover': turn, 'trades': trades}


def to_daily(bar_returns: Dict[float, float]) -> Dict[int, float]:
    out: Dict[int, float] = {}
    for t, v in bar_returns.items():
        d = int(t // 86400)
        out[d] = out.get(d, 0.0) + v
    return out


# ------------------------------------------------------------------ paper book
@dataclass
class PaperBook:
    """Paper positions for the trend lane (never sends orders).  Persisted as JSON; fills appended to a jsonl."""
    equity0: float = 5000.0
    cash_pnl: float = 0.0
    qty: Dict[str, float] = field(default_factory=dict)
    entry: Dict[str, float] = field(default_factory=dict)
    last_bar: Dict[str, float] = field(default_factory=dict)
    fees: float = 0.0

    def equity(self, prices: Dict[str, float]) -> float:
        upnl = sum((prices.get(s, self.entry.get(s, 0.0)) - self.entry.get(s, 0.0)) * q for s, q in self.qty.items())
        return self.equity0 + self.cash_pnl + upnl

    def rebalance(self, symbol: str, target_w: float, price: float, equity: float, cost_bps: float,
                  band: float, now: Optional[float] = None) -> Optional[dict]:
        cur_q = self.qty.get(symbol, 0.0)
        cur_w = cur_q * price / equity if equity else 0.0
        if not ((target_w == 0.0 and cur_q != 0.0) or sign(target_w) != sign(cur_w) or abs(target_w - cur_w) > band):
            return None
        tgt_q = target_w * equity / price
        dq = tgt_q - cur_q
        if abs(dq) * price < 1e-9:
            return None
        fee = abs(dq) * price * cost_bps / 1e4
        realized = 0.0
        if cur_q and (sign(dq) != sign(cur_q)):                  # reducing / flipping
            closing = min(abs(dq), abs(cur_q))
            realized = (price - self.entry[symbol]) * closing * sign(cur_q)
        new_q = cur_q + dq
        if new_q == 0 or sign(new_q) != sign(cur_q):
            self.entry[symbol] = price if new_q else 0.0
        elif abs(new_q) > abs(cur_q):
            self.entry[symbol] = (self.entry.get(symbol, price) * abs(cur_q) + price * abs(dq)) / abs(new_q)
        self.qty[symbol] = new_q
        self.cash_pnl += realized - fee
        self.fees += fee
        return {'t': time.time() if now is None else now, 'symbol': symbol, 'side': 'BUY' if dq > 0 else 'SELL',
                'qty': abs(dq), 'price': price, 'fee': fee, 'realized': realized, 'target_w': target_w,
                'paper': True}

    def to_json(self) -> str:
        return json.dumps(asdict(self))

    @classmethod
    def load(cls, path: Path, equity0: float = 5000.0) -> 'PaperBook':
        try:
            return cls(**json.loads(Path(path).read_text(encoding='utf-8')))
        except (OSError, ValueError, TypeError):
            return cls(equity0=equity0)


def klines_to_bars(rows) -> dict:
    """Binance kline rows [open_ms, o, h, l, c, ...] -> bars dict (drops nothing; caller drops the open bar)."""
    return {'t': [r[0] / 1000.0 for r in rows], 'o': [float(r[1]) for r in rows], 'h': [float(r[2]) for r in rows],
            'l': [float(r[3]) for r in rows], 'c': [float(r[4]) for r in rows]}


def closed_only(bars: dict, now: float, hours: float) -> dict:
    keep = [i for i, t in enumerate(bars['t']) if t + hours * 3600 <= now]
    return {k: [v[i] for i in keep] for k, v in bars.items()}


def paper_step(book: PaperBook, bars: Dict[str, dict], funding: Dict[str, list], cfg: TrendConfig,
               now: Optional[float] = None) -> dict:
    """One paper rebalance on the last closed bar of each symbol.  Returns {'targets', 'fills', 'equity'}."""
    now = time.time() if now is None else now
    hours = BAR_HOURS[cfg.bar]
    closed = {s: closed_only(b, now, hours) for s, b in bars.items()}
    closed = {s: b for s, b in closed.items() if len(b['c']) > 2}
    if not closed:
        return {'targets': {}, 'fills': [], 'equity': book.equity({})}
    tw, comp = weights_series(closed, cfg, funding)
    prices = {s: b['c'][-1] for s, b in closed.items()}
    eq = book.equity(prices)
    targets, fills = {}, []
    for s, b in closed.items():
        w = tw[s][-1]
        targets[s] = {'w': w, 'bar_t': b['t'][-1], **(comp[s][-1] or {})}
        if book.last_bar.get(s) == b['t'][-1]:
            continue                                          # this bar already handled
        book.last_bar[s] = b['t'][-1]
        f = book.rebalance(s, w, prices[s], eq, cfg.cost_bps, cfg.band, now)
        if f:
            fills.append(f)
    return {'targets': targets, 'fills': fills, 'equity': book.equity(prices)}
