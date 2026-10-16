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
from collections import deque
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
    trail_atr: Optional[float] = None                # 10-09 (cc) chandelier: flat after a close beyond k x ATR(14)
                                                     # from the best close since entry; re-entry on a new signal sign
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
        if c.trail_atr:
            ws = chandelier(ws, highs, lows, closes, c.trail_atr)
        out[s], comp[s] = ws, cs
    return out, comp


def atr_series(highs, lows, closes, n: int = 14) -> List[Optional[float]]:
    tr = [highs[0] - lows[0]] + [max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1]))
                                 for i in range(1, len(closes))]
    out, acc = [], 0.0
    for i, x in enumerate(tr):
        acc += x
        if i >= n:
            acc -= tr[i - n]
        out.append(acc / n if i >= n - 1 else None)
    return out


def chandelier(weights: Sequence[float], highs, lows, closes, k: float) -> List[float]:
    """Trailing exit on top of the target weights: once a close retraces k x ATR(14) from the best close of the
    current side, stay flat until the target changes sign (a fresh signal)."""
    atr = atr_series(highs, lows, closes)
    out, side, best, stopped = [], 0.0, None, 0.0
    for i, w in enumerate(weights):
        d = sign(w)
        if stopped and d == stopped:
            out.append(0.0)
            continue
        stopped = 0.0 if d != stopped else stopped
        if d != side:
            side, best = d, closes[i]
        if side and atr[i]:
            best = max(best, closes[i]) if side > 0 else min(best, closes[i])
            if (side > 0 and closes[i] < best - k * atr[i]) or (side < 0 and closes[i] > best + k * atr[i]):
                stopped, side, best = side, 0.0, None
                out.append(0.0)
                continue
        out.append(w)
    return out


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
    """Binance kline rows [open_ms, o, h, l, c, ...] -> bars dict (drops nothing; caller drops the open bar).
    Raw REST rows (12 fields) also give 'v' (quote volume, r[7]) and 'tb' (taker-buy quote volume, r[10])."""
    out = {'t': [r[0] / 1000.0 for r in rows], 'o': [float(r[1]) for r in rows], 'h': [float(r[2]) for r in rows],
           'l': [float(r[3]) for r in rows], 'c': [float(r[4]) for r in rows]}
    if rows and all(len(r) >= 11 for r in rows):
        out['v'] = [float(r[7]) for r in rows]
        out['tb'] = [float(r[10]) for r in rows]
    return out


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


# ------------------------------------------------------------------ 10-09 (dd) bracket exits, event-based
@dataclass(frozen=True)
class Brackets:
    """Exits layered on the trend signal (request 10-09 dd).  None = that leg is off.
    tp1_pct      50 % (tp1_frac) closed at +tp1_pct %, maker fee
    sl_atr       hard stop at entry -/+ sl_atr x ATR(14) of the bar, taker fee + slip, checked intrabar FIRST
    trail        'chandelier' (best close -/+ trail_atr x ATR) | 'donchian_mid' ((N-high + N-low) / 2) | None
    Signal exits (sign change / flat target) at the bar close, taker.  After a bracket exit the same side is
    not re-entered until the signal changes sign or goes flat (no instant re-entry into the stop).
    10-09 (ee) profit lock: lock_pct arms the trail only once the best close is >= lock_pct % in favour; soft=True
    checks the trail on the bar CLOSE and exits at the close (taker), instead of an intrabar stop."""
    tp1_pct: Optional[float] = None
    tp1_frac: float = 0.5
    sl_atr: Optional[float] = None
    trail: Optional[str] = None
    trail_atr: float = 3.0
    donchian_n: int = 20
    lock_pct: Optional[float] = None
    soft: bool = False


def backtest_brackets(bars: Dict[str, dict], cfg: TrendConfig, br: Brackets,
                      funding: Optional[Dict[str, list]] = None, taker_bps: float = 6.0, maker_bps: float = 2.0,
                      gate: Optional['RegimeGate'] = None, sizing: Optional['Sizing'] = None,
                      align: Optional['BtcAlign'] = None, pyramid: Optional['Pyramid'] = None,
                      conv: Optional[Dict[str, List[bool]]] = None, conv_sizing: Optional['Sizing'] = None):
    """Event-based: size fixed at entry (|target weight|, or sizing.fraction), exits by brackets or signal.  -> same
    shape as backtest(): {'bar_returns', 'turnover', 'trades'}.  Conservative intrabar order: stop before TP1.
    gate (10-09 ee): entries only where gate_entry() passes on the entry bar; chop_exit closes on a CHOP close.
    10-09 (ff): align = BTC shield for alt entries (+ ejection); pyramid = break-even stop + one soft-stopped add at
    +arm_pct; conv[sym][i] True -> conv_sizing instead of sizing (1H + 4H + 1D agree)."""
    tw, _ = weights_series(bars, cfg, funding)
    hours = BAR_HOURS[cfg.bar]
    dn = br.donchian_n * (24 // hours if cfg.bar == '4h' else 1)
    rets, turn, trades = {}, {}, []
    tk, mk = taker_bps / 1e4, maker_bps / 1e4
    ctx = btc_context(bars, tw, align.leader, gate or RegimeGate()) if align and align.leader in bars else None
    for s, b in bars.items():
        o, h, l, cl, ts = b['o'], b['h'], b['l'], b['c'], b['t']
        atr = atr_series(h, l, cl)
        info = regime_info(b, gate or RegimeGate()) if (gate or align) else None
        alt = align is not None and s != align.leader
        fr = funding.get(s, []) if funding else []
        fi = 0
        pos = None                      # {'side','w','entry','sl','tp','tp_done','best','frac'}
        blocked = 0.0
        for i in range(len(cl)):
            pnl, dturn = 0.0, 0.0
            if i > 0 and pos is not None:
                d, w = pos['side'], pos['w'] * pos['frac']
                prev = cl[i - 1]
                exit_px, why = None, None
                if pos['sl'] is not None and ((d > 0 and l[i] <= pos['sl']) or (d < 0 and h[i] >= pos['sl'])):
                    exit_px = min(o[i], pos['sl']) if d > 0 else max(o[i], pos['sl'])     # gap -> open
                    why = 'SL' if not pos.get('trailed') else 'TRAIL'
                if exit_px is None and br.tp1_pct and not pos['tp_done']:
                    tp = pos['entry'] * (1 + d * br.tp1_pct / 100)
                    if (d > 0 and h[i] >= tp) or (d < 0 and l[i] <= tp):
                        part = w * br.tp1_frac
                        pnl += part * (d * (tp / prev - 1)) - part * mk
                        pos['tr']['pnl'] += part * (d * (tp / pos['entry'] - 1)) - part * mk
                        dturn += part
                        pos['frac'] *= (1 - br.tp1_frac)
                        pos['tp_done'] = True
                        w = pos['w'] * pos['frac']
                ad = pos.get('add')
                if exit_px is not None:
                    if ad:                                             # the closePosition stop takes the add too
                        pnl += ad['w'] * d * (exit_px / prev - 1) - ad['w'] * tk
                        pos['tr']['pnl'] += ad['w'] * d * (exit_px / ad['entry'] - 1) - ad['w'] * tk
                        dturn += ad['w']
                    pnl += w * d * (exit_px / prev - 1) - w * tk
                    pos['tr']['pnl'] += w * d * (exit_px / pos['entry'] - 1) - w * tk
                    dturn += w
                    pos['tr'].update(exit_t=ts[i], why='BE' if pos.get('be') and why == 'SL' else why,
                                     bars=i - pos['i0'])
                    trades.append(pos['tr'])
                    blocked, pos = d, None
                else:
                    pnl += w * d * (cl[i] / prev - 1)
                    if ad:
                        pnl += ad['w'] * d * (cl[i] / prev - 1)
                        w += ad['w']                                   # funding on the add as well
                # funding on the held weight
                t_lo, t_hi = ts[i], ts[i] + hours * 3600
                while fi < len(fr) and fr[fi][0] < t_lo:
                    fi += 1
                k = fi
                while k < len(fr) and fr[k][0] < t_hi:
                    pnl -= w * d * fr[k][1]
                    k += 1
            sig = sign(tw[s][i])
            if blocked and sig != blocked:
                blocked = 0.0
            if pos is not None:
                d = pos['side']
                pos['best'] = max(pos['best'], cl[i]) if d > 0 else min(pos['best'], cl[i])
                why = None
                if sig != d:
                    why = 'SIGNAL'
                elif gate and gate.chop_exit and info['regime'][i] == 'CHOP':
                    why = 'CHOP'
                elif alt and align.eject and ctx.get(ts[i], (0.0, None)) == (-d, 'TREND'):
                    why = 'BTC-EJECT'
                elif atr[i]:
                    if br.trail == 'chandelier':
                        ts_px = pos['best'] - d * br.trail_atr * atr[i]
                    elif br.trail == 'donchian_mid' and i >= dn:
                        ts_px = (max(h[i - dn + 1:i + 1]) + min(l[i - dn + 1:i + 1])) / 2
                    else:
                        ts_px = None
                    if br.lock_pct is not None and d * (pos['best'] / pos['entry'] - 1) * 100 < br.lock_pct:
                        ts_px = None                                   # profit lock not armed yet
                    if ts_px is not None and br.soft:
                        if (d > 0 and cl[i] < ts_px) or (d < 0 and cl[i] > ts_px):
                            why = 'LOCK'
                    elif ts_px is not None and (pos['sl'] is None or (d > 0 and ts_px > pos['sl']) or
                                                (d < 0 and ts_px < pos['sl'])):
                        pos['sl'], pos['trailed'] = ts_px, True
                ad = pos.get('add')
                if ad and (why or (d > 0 and cl[i] < ad['sl']) or (d < 0 and cl[i] > ad['sl'])):
                    pnl -= ad['w'] * tk                                # soft add stop / exit with the base
                    pos['tr']['pnl'] += ad['w'] * d * (cl[i] / ad['entry'] - 1) - ad['w'] * tk
                    dturn += ad['w']
                    pos['add'] = None
                if why:                                                # exit at the close, taker
                    w = pos['w'] * pos['frac']
                    pnl -= w * tk
                    pos['tr']['pnl'] += w * d * (cl[i] / pos['entry'] - 1) - w * tk
                    dturn += w
                    pos['tr'].update(exit_t=ts[i], why=why, bars=i - pos['i0'])
                    trades.append(pos['tr'])
                    if why == 'LOCK':
                        blocked = d
                    pos = None
                elif pyramid and not pos.get('be') and d * (cl[i] / pos['entry'] - 1) * 100 >= pyramid.arm_pct:
                    be = pos['entry'] * (1 + d * pyramid.be_pct / 100)
                    if pos['sl'] is None or d * (be - pos['sl']) > 0:
                        pos['sl'] = be                                 # base stop -> break-even + fees
                    pos['be'] = True
                    if pyramid.add_frac > 0:
                        aw = pos['w'] * pyramid.add_frac
                        pos['add'] = {'w': aw, 'entry': cl[i], 'sl': l[i] if d > 0 else h[i]}
                        pnl -= aw * tk
                        pos['tr']['pnl'] -= aw * tk
                        dturn += aw
                        pos['tr']['added'] = True
            if pos is None and sig != 0 and sig != blocked and atr[i] and (gate is None or gate_entry(info, i, sig, gate)[0]) \
                    and (not alt or align_entry(ctx.get(ts[i]), sig, info, i, align)[0]):
                sz = conv_sizing if conv_sizing and conv and conv.get(s) and conv[s][i] else sizing
                w = sz.fraction(atr[i], cl[i]) if sz else abs(tw[s][i])
                if w > 0:
                    sl = cl[i] - sig * br.sl_atr * atr[i] if br.sl_atr else None
                    pos = {'side': sig, 'w': w, 'entry': cl[i], 'sl': sl, 'tp_done': False, 'best': cl[i],
                           'frac': 1.0, 'i0': i,
                           'tr': {'symbol': s, 'side': 'LONG' if sig > 0 else 'SHORT', 'entry_t': ts[i],
                                  'w': w, 'pnl': -w * tk, 'conv': sz is conv_sizing and sz is not None}}
                    pnl -= w * tk
                    dturn += w
            rets[ts[i]] = rets.get(ts[i], 0.0) + pnl
            turn[ts[i]] = turn.get(ts[i], 0.0) + dturn
        if pos is not None:
            pos['tr'].update(exit_t=None, why='OPEN', bars=len(cl) - 1 - pos['i0'])
            trades.append(pos['tr'])
    for tr in trades:
        tr['hold_h'] = tr['bars'] * hours
        tr['ret_on_notional_bps'] = tr['pnl'] / tr['w'] * 1e4 if tr['w'] else 0.0
    return {'bar_returns': rets, 'turnover': turn, 'trades': trades}


# ------------------------------------------------------------------ 10-09 (ee) regime gate, breakout confirmation, sizing
@dataclass(frozen=True)
class RegimeGate:
    """Entry gate of the 4H lane (request 10-09 ee).  Thresholds are the request's; windows are the textbook
    defaults (Kaufman ER 10, Wilder ADX 14, volume SMA 20), fixed before the replay, not tuned.
      regime   TREND  KER > ker_trend and ADX > adx_trend
               CHOP   KER < ker_chop  and ADX < adx_chop
               MIXED  otherwise
      mode     'trend_only' new entries only in TREND | 'no_chop' entries unless CHOP | 'off' no regime condition
      confirm  bar quote volume > vol_mult x SMA(vol_n) of the prior bars AND the bar's CVD (taker buy - taker sell
               quote volume) has the sign of the entry.  Missing volume is UNKNOWN -> no entry (never a silent pass).
      chop_exit close a held position at the close of a CHOP bar (Cash / Observer while the market ranges).
    Signal exits are never gated: the gate only delays or skips entries."""
    mode: str = 'trend_only'
    confirm: bool = True
    ker_n: int = 10
    adx_n: int = 14
    ker_trend: float = 0.40
    adx_trend: float = 25.0
    ker_chop: float = 0.25
    adx_chop: float = 20.0
    vol_mult: float = 1.2
    vol_n: int = 20
    chop_exit: bool = False
    hurst_n: int = 60                # 10-16 tri-state: variance-ratio Hurst window (log returns), mode 'hurst'


def ker_series(closes, n: int = 10) -> List[Optional[float]]:
    """Kaufman efficiency ratio |c_i - c_{i-n}| / sum |c_k - c_{k-1}| over the last n bars."""
    out = []
    for i in range(len(closes)):
        if i < n:
            out.append(None)
            continue
        path = sum(abs(closes[k] - closes[k - 1]) for k in range(i - n + 1, i + 1))
        out.append(abs(closes[i] - closes[i - n]) / path if path else 0.0)
    return out


def adx_series(highs, lows, closes, n: int = 14) -> List[Optional[float]]:
    """Wilder ADX(n); None until 2n bars."""
    m = len(closes)
    out: List[Optional[float]] = [None] * m
    if m < 2 * n + 1:
        return out
    tr, pdm, ndm = [], [], []
    for i in range(1, m):
        up, dn = highs[i] - highs[i - 1], lows[i - 1] - lows[i]
        pdm.append(up if up > dn and up > 0 else 0.0)
        ndm.append(dn if dn > up and dn > 0 else 0.0)
        tr.append(max(highs[i] - lows[i], abs(highs[i] - closes[i - 1]), abs(lows[i] - closes[i - 1])))
    a, p, q = sum(tr[:n]), sum(pdm[:n]), sum(ndm[:n])
    dx = []                                      # dx[j] belongs to bar j + n
    for k in range(n - 1, len(tr)):
        if k >= n:
            a, p, q = a - a / n + tr[k], p - p / n + pdm[k], q - q / n + ndm[k]
        pdi, ndi = (100 * p / a, 100 * q / a) if a else (0.0, 0.0)
        dx.append(100 * abs(pdi - ndi) / (pdi + ndi) if pdi + ndi else 0.0)
    adx = sum(dx[:n]) / n
    out[2 * n - 1] = adx
    for j in range(n, len(dx)):
        adx = (adx * (n - 1) + dx[j]) / n
        out[j + n] = adx
    return out


def regime_info(bars: dict, g: RegimeGate) -> dict:
    """Per bar: 'regime' (TREND / CHOP / MIXED / None), 'ker', 'adx', 'vol_ratio', 'cvd' (None = UNKNOWN)."""
    c = bars['c']
    ker, adx = ker_series(c, g.ker_n), adx_series(bars['h'], bars['l'], c, g.adx_n)
    reg = []
    for k, x in zip(ker, adx):
        if k is None or x is None:
            reg.append(None)
        elif k > g.ker_trend and x > g.adx_trend:
            reg.append('TREND')
        elif k < g.ker_chop and x < g.adx_chop:
            reg.append('CHOP')
        else:
            reg.append('MIXED')
    v, tb = bars.get('v'), bars.get('tb')
    vr, cvd = [None] * len(c), [None] * len(c)
    if v and tb and len(v) == len(c) == len(tb):
        for i in range(g.vol_n, len(c)):
            base = sum(v[i - g.vol_n:i]) / g.vol_n
            vr[i] = v[i] / base if base > 0 else None
            cvd[i] = 2.0 * tb[i] - v[i]
    share = [x / v[i] if x is not None and v[i] else None for i, x in enumerate(cvd)]
    hu = hurst_series(c, g.hurst_n)
    return {'regime': reg, 'ker': ker, 'adx': adx, 'vol_ratio': vr, 'cvd': cvd, 'cvd_share': share,
            'hurst': hu, 'state3': [tri_state(h) for h in hu]}


def gate_entry(info: dict, i: int, side: float, g: Optional[RegimeGate]) -> Tuple[bool, str]:
    """May the lane open `side` on the close of bar i?  -> (ok, reason)."""
    if g is None:
        return True, 'no gate'
    reg = info['regime'][i]
    if g.mode == 'hurst':
        st = (info.get('state3') or [None] * (i + 1))[i]
        if st != TRENDING:
            h = info['hurst'][i]
            return False, f'H {h:.2f} {st}' if h is not None else 'H UNKNOWN (needs TRENDING)'
        reg = f'TRENDING H {info["hurst"][i]:.2f}'
    if g.mode == 'trend_only' and reg != 'TREND':
        return False, f'regime {reg or "UNKNOWN"} (needs TREND)'
    if g.mode == 'no_chop' and reg in (None, 'CHOP'):
        return False, f'regime {reg or "UNKNOWN"}'
    if g.confirm:
        vr, cvd = info['vol_ratio'][i], info['cvd'][i]
        if vr is None or cvd is None:
            return False, 'volume/CVD UNKNOWN'
        if vr <= g.vol_mult:
            return False, f'volume {vr:.2f}x SMA{g.vol_n} <= {g.vol_mult:g}x'
        if side * cvd <= 0:
            return False, 'CVD against the breakout'
    return True, f'regime {reg} confirmed'


# ------------------------------------------------------------------ 10-16 tri-state regime (variance-ratio Hurst)
TRENDING, RANDOM_WALK, MEAN_REVERTING = 'TRENDING', 'RANDOM_WALK', 'MEAN_REVERTING'
# lanes a state may route to; a lane still needs its own verdict / Profile switch (this map never activates one)
STATE_LANES = {TRENDING: ('trend',), RANDOM_WALK: ('funding_carry',), MEAN_REVERTING: ('stat_arb', 'maker_spread')}


def tri_state(h: Optional[float], lo: float = 0.45, hi: float = 0.55) -> Optional[str]:
    """H > hi TRENDING | lo <= H <= hi RANDOM_WALK | H < lo MEAN_REVERTING | None (warm-up) -> UNKNOWN."""
    if h is None:
        return None
    return TRENDING if h > hi else (MEAN_REVERTING if h < lo else RANDOM_WALK)


class RollingHurst:
    """O(1)-per-bar local Hurst exponent from the Lo-MacKinlay (1988) overlapping variance ratio of the last
    `n` log returns at aggregation q:  VR(q) = sigma_c^2(q) / sigma_a^2,  H = 0.5 + ln VR / (2 ln q).
    Random walk VR = 1 (H 0.5); positive return autocorrelation VR > 1 (H > 0.5); negative VR < 1.
    Rolling sums only (deques of r and of q-sums) - no window re-scan.  Unlike KER this does not depend on the
    path length of one window, but at n = 60 the estimate is noisy: sd(H) ~ 0.09 under a random walk
    (asymptotic sd(VR) = sqrt(2(2q-1)(q-1) / (3 q n)) = 0.24 at q = 4), so a single bar's state is weak
    evidence; tests/test_regime_hawkes_carry_2026_10_16.py measures the misclassification rate."""

    def __init__(self, n: int = 60, q: int = 4) -> None:
        self.n, self.q = max(int(n), 4 * int(q)), max(int(q), 2)
        self.r: deque = deque()          # last n one-bar returns
        self.rq: deque = deque()         # last n - q + 1 overlapping q-bar returns
        self.last_q: deque = deque()     # last q one-bar returns (running q-sum)
        self.s1 = self.s2 = self.sq1 = self.sq2 = self.run_q = 0.0
        self.prev: Optional[float] = None

    def update(self, close: float) -> Optional[float]:
        if close is None or close <= 0:
            return None
        lp = math.log(close)
        if self.prev is None:
            self.prev = lp
            return None
        x, self.prev = lp - self.prev, lp
        self.r.append(x)
        self.s1 += x
        self.s2 += x * x
        if len(self.r) > self.n:
            o = self.r.popleft()
            self.s1 -= o
            self.s2 -= o * o
        self.last_q.append(x)
        self.run_q += x
        if len(self.last_q) > self.q:
            self.run_q -= self.last_q.popleft()
        if len(self.last_q) == self.q:
            self.rq.append(self.run_q)
            self.sq1 += self.run_q
            self.sq2 += self.run_q * self.run_q
            if len(self.rq) > self.n - self.q + 1:
                o = self.rq.popleft()
                self.sq1 -= o
                self.sq2 -= o * o
        return self.value()

    def value(self) -> Optional[float]:
        nq, q = len(self.r), self.q
        if nq < self.n or len(self.rq) != nq - q + 1:
            return None
        mu = self.s1 / nq
        var_a = (self.s2 - nq * mu * mu) / (nq - 1)
        m = q * (nq - q + 1) * (1.0 - q / nq)
        var_c = (self.sq2 - 2 * q * mu * self.sq1 + len(self.rq) * (q * mu) ** 2) / m
        if var_a <= 1e-18 or var_c <= 1e-18:
            return None
        return 0.5 + math.log(var_c / var_a) / (2.0 * math.log(q))


def hurst_series(closes: Sequence[float], n: int = 60, q: int = 4) -> List[Optional[float]]:
    """Per bar the RollingHurst value after that close (None during the warm-up of n + 1 closes)."""
    rh = RollingHurst(n, q)
    return [rh.update(c) for c in closes]


@dataclass(frozen=True)
class Sizing:
    """10-09 (ee) fraction of equity per position = min(alloc_pct, risk_pct / stop distance), stop = stop_atr x ATR(14).
    Low ATR: the alloc_pct ceiling binds.  High ATR: the size shrinks so the loss at the disaster stop stays
    <= risk_pct of equity (a gap through the stop can still exceed it)."""
    alloc_pct: float = 0.10
    risk_pct: float = 0.01
    stop_atr: float = 8.0

    def fraction(self, atr: Optional[float], price: float) -> float:
        if not atr or not price or atr <= 0:
            return 0.0
        return min(self.alloc_pct, self.risk_pct / (self.stop_atr * atr / price))


# ------------------------------------------------------------------ 10-09 (ff) BTC alignment, pyramiding, conviction
@dataclass(frozen=True)
class BtcAlign:
    """BTC macro shield for alt entries (request 10-09 ff).
      mode   'sign'  an alt may not open against the sign of the BTC 4H trend signal
             'trend' ... only while BTC's regime is also TREND (KER > 0.40 and ADX > 25)
      exception: the alt's own bar volume > rvol_exc x SMA20 AND its CVD share (CVD / volume) >= cvd_exc with the
             alt side (fixed a priori: 3.0x, 10 %, i.e. >= 55 % of the volume taker-aggressed on the alt side)
      eject  close an open alt position on a close where BTC is TREND with its signal against the position.
    BTC data missing = UNKNOWN -> no alt entry."""
    mode: str = 'sign'
    eject: bool = False
    rvol_exc: float = 3.0
    cvd_exc: float = 0.10
    leader: str = 'BTCUSDT'


def btc_context(bars: Dict[str, dict], tw: Dict[str, list], leader: str, g: 'RegimeGate') -> dict:
    """{bar open t: (sign of the BTC 4H trend target, BTC regime)}."""
    b = bars[leader]
    reg = regime_info(b, g)['regime']
    return {t: (sign(tw[leader][i]), reg[i]) for i, t in enumerate(b['t'])}


def align_entry(ctx_t, side: float, info: dict, i: int, al: BtcAlign) -> Tuple[bool, str]:
    """May an alt open `side` given the BTC context (dir, regime) of the same bar?  -> (ok, reason)."""
    if ctx_t is None:
        return False, 'BTC context UNKNOWN'
    bdir, breg = ctx_t
    if not bdir or side != -bdir or (al.mode == 'trend' and breg != 'TREND'):
        return True, 'with BTC'
    vr, cs = info['vol_ratio'][i], info['cvd_share'][i]
    if vr is not None and cs is not None and vr > al.rvol_exc and side * cs >= al.cvd_exc:
        return True, f'against BTC on its own flow (vol {vr:.1f}x, CVD {cs:+.0%})'
    return False, f'against BTC {"BUY" if bdir > 0 else "SELL"} ({breg})'


@dataclass(frozen=True)
class Pyramid:
    """Once a close is >= arm_pct % in favour: the base's exchange stop moves to entry +/- be_pct % (break-even
    after fees) and ONE add of add_frac x the base size is bought at that close; the add carries a SOFT stop at the
    low (long) / high (short) of that 4H bar, checked on the closes; the base's closePosition stop also takes it."""
    arm_pct: float = 2.0
    be_pct: float = 0.3
    add_frac: float = 0.5


H1_LOOKBACKS = (24, 72, 168)      # 1H trend = TSMOM sign over 1 / 3 / 7 days of 1h closes (fixed a priori)


def h1_sign(closes_1h: Sequence[float]) -> float:
    x = tsmom(closes_1h, len(closes_1h) - 1, H1_LOOKBACKS) if closes_1h else None
    return sign(x) if x is not None else 0.0


def conviction_series(b4: dict, ts_1h: Sequence[float], c_1h: Sequence[float], ts_1d: Sequence[float],
                      w_1d: Sequence[float], w_4h: Sequence[float]) -> List[bool]:
    """Per 4H bar i: 1H (TSMOM 24/72/168 h), 4H and 1D (SPEC_1D_LS target) signs all equal and non-zero, using only
    bars CLOSED by the 4H close (1h bar open + 1 h <= close, 1d bar open + 24 h <= close)."""
    import bisect
    out = []
    for i, t in enumerate(b4['t']):
        close_t = t + 4 * 3600
        k1 = bisect.bisect_right(ts_1h, close_t - 3600)        # 1h bars with open <= close - 1 h
        kd = bisect.bisect_right(ts_1d, close_t - 86400)
        s4 = sign(w_4h[i])
        if not s4 or k1 < max(H1_LOOKBACKS) + 1 or kd < 1:
            out.append(False)
            continue
        x = tsmom(c_1h, k1 - 1, H1_LOOKBACKS)
        out.append(x is not None and sign(x) == s4 and sign(w_1d[kd - 1]) == s4)
    return out
