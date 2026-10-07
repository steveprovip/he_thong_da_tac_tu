"""Alpha Radar — Binance USD-M volume-surge breakout scanner (read-only, no orders).

Pivot from 1m scalping: the HFT replays (research/archive_hft/README.md) lost
-11..-22 bps per trade because a +0.35 % target is the same size as fees +
adverse selection. Here the target is +2 % / +5 %, so fees (~0.12 %) are a small
fraction of the move, and entries are only taken when volume actually explodes.

Signal (5m closed bars, no lookahead — the forming bar is dropped):
  surge    = quote volume of the last W bars / (median bar volume of the prior
             240 bars x W), best of W in {3,6,12} (15/30/60 min). >= 3.0 means
             volume up > 200 %.
  breakout = last close beyond the high (long) / low (short) of the 48 bars
             before the window (4 h range).
  RSI(14)  >= 60 long / <= 40 short (Wilder).
  taker    = taker-buy quote share of the window >= 0.55 long / <= 0.45 short.
  OBI(20)  = (bid notional - ask notional)/total over top 20 levels, finalists only.
fired = surge AND breakout AND RSI AND taker (require_taker). OBI is a
confirmation; RSI > 85 or a window move > 8 % is flagged as chase risk.

v3 defaults (after 146 real signals: 0.8 % money-capped stop hit 74-81 %):
  surge >= 5x, taker >= 60 % (<= 40 % short), breakout of the 24 h range (288
  bars), stop = 2.0 x ATR14(5m) (sl_mode='atr'), TP1 = max(4.5 %, 2.5 x SL),
  TP2 = TP1 x 8/4.5, half off at TP1 then stop -> entry, 12 h time barrier.
  Max loss per trade 0.15 USDT on ~5.1 U notional: a trade whose ATR stop would
  cost more is SKIPPED, never squeezed back inside the noise.
Legacy money-capped stop: RadarConfig(sl_mode='money', max_loss_usdt=0.05, ...).

Event study: the same `features()` run on every past bar of up to 1500 bars
per symbol, entry at the NEXT bar open, TP/SL/4h timeout, stop checked first
within a bar. Gives an honest base rate next to every live signal.
"""
from __future__ import annotations

import json
import math
import statistics
import time
import urllib.request
from dataclasses import asdict, dataclass
from typing import Callable, Dict, Iterable, List, Optional, Tuple

FAPI = 'https://fapi.binance.com'
USDT_VND = 26_000.0          # ~100k VND = 3.85 USDT


def _get_json(url: str, timeout: float = 8.0):
    req = urllib.request.Request(url, headers={'User-Agent': 'alpha-radar/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


@dataclass(frozen=True)
class RadarConfig:
    min_quote_vol_24h: float = 20e6
    prefilter_each: int = 40          # top-N by 24h volume  U  top-N by |24h change|
    interval: str = '5m'
    bars: int = 320
    windows: Tuple[int, ...] = (3, 6, 12)
    baseline_bars: int = 240
    breakout_lookback: int = 288      # 24 h range of 5m bars
    surge_min: float = 5.0
    rsi_len: int = 14
    rsi_long: float = 60.0
    rsi_short: float = 40.0
    rsi_extreme: float = 85.0
    taker_long: float = 0.60
    taker_short: float = 0.40
    require_taker: bool = True
    obi_min: float = 0.15
    max_chase_pct: float = 8.0
    top_n: int = 3
    finalists: int = 10
    tp_pcts: Tuple[float, ...] = (4.5, 8.0)
    hold_bars: int = 144              # 12 h timeout (wider barriers need time)
    equity: float = 3.85
    max_loss_usdt: float = 0.15
    sl_mode: str = 'atr'              # 'atr' | 'money' (legacy fixed-loss stop)
    atr_sl_mult: float = 2.0          # stop = k x ATR14(5m); 1.5..2.0 keeps it outside the wicks
    rr_min: float = 2.5               # TP1 >= rr_min x stop
    ladder: bool = True               # backtest: half at TP1, stop -> entry, rest at TP2
    leverage_choices: Tuple[int, ...] = (2, 3, 5)
    margin_frac: float = 0.7          # margin <= 70 % of equity
    taker_bps: float = 5.0
    maker_bps: float = 2.0
    slip_bps: float = 5.0             # breakouts slip more than quiet books
    min_sl_pct: float = 0.3
    # multi-timeframe trend gate: LONG only when close > EMA50 on every frame, SHORT mirrored
    trend_frames: Tuple[str, ...] = ('1h', '4h')
    trend_ema: int = 50
    # pullback-retest entry zone (maker limit below the surge, never chase the top)
    retest_min_pct: float = 0.5
    retest_max_pct: float = 1.2
    retest_default_pct: float = 0.8
    vwap_bars: int = 48
    retest_ttl_bars: int = 1          # backtest: limit lives one 5m bar (~3-5 min live TTL)


# ----------------------------------------------------------------------------- bars
def parse_klines(raw, now_ms: Optional[float] = None) -> List[dict]:
    """Binance kline rows -> dicts; drops the still-forming bar (no lookahead)."""
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    out = []
    for k in raw or []:
        try:
            bar = {'t': int(k[0]), 'o': float(k[1]), 'h': float(k[2]), 'l': float(k[3]),
                   'c': float(k[4]), 'v': float(k[5]), 'qv': float(k[7]), 'tbq': float(k[10]),
                   'T': int(k[6])}
        except (IndexError, TypeError, ValueError):
            continue
        if bar['T'] < now_ms:
            out.append(bar)
    return out


def rsi_series(closes: List[float], n: int = 14) -> List[Optional[float]]:
    out: List[Optional[float]] = [None] * len(closes)
    if len(closes) <= n:
        return out
    g = l = 0.0
    for i in range(1, n + 1):
        d = closes[i] - closes[i - 1]
        g += max(d, 0.0)
        l += max(-d, 0.0)
    ag, al = g / n, l / n
    out[n] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    for i in range(n + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (n - 1) + max(d, 0.0)) / n
        al = (al * (n - 1) + max(-d, 0.0)) / n
        out[i] = 100.0 if al == 0 else 100.0 - 100.0 / (1.0 + ag / al)
    return out


def atr_pct(bars: List[dict], n: int = 14) -> Optional[float]:
    if len(bars) <= n:
        return None
    trs = [max(b['h'] - b['l'], abs(b['h'] - p['c']), abs(b['l'] - p['c']))
           for p, b in zip(bars[:-1], bars[1:])]
    a = sum(trs[:n]) / n
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a / bars[-1]['c'] * 100.0 if bars[-1]['c'] > 0 else None


def ema_series(vals: List[float], n: int) -> List[Optional[float]]:
    """EMA seeded with the SMA of the first n values; None before that."""
    out: List[Optional[float]] = [None] * len(vals)
    if len(vals) < n or n <= 0:
        return out
    e = sum(vals[:n]) / n
    out[n - 1] = e
    k = 2.0 / (n + 1)
    for i in range(n, len(vals)):
        e += k * (vals[i] - e)
        out[i] = e
    return out


def trend_state(closes: List[float], n: int = 50) -> Optional[str]:
    """'UP' when the last close is above EMA(n), 'DOWN' below, None without enough history."""
    if len(closes) < n:
        return None
    e = ema_series(closes, n)[-1]
    return 'UP' if closes[-1] > e else 'DOWN'


def vwap(bars: List[dict]) -> Optional[float]:
    vol = sum(b.get('v', 0.0) for b in bars)
    if vol <= 0:
        return None
    return sum((b['h'] + b['l'] + b['c']) / 3.0 * b.get('v', 0.0) for b in bars) / vol


def retest_zone(bars: List[dict], side: str, level: float, w: int, cfg: 'RadarConfig') -> dict:
    """Maker limit price for a pullback: the support nearest to price inside the
    [retest_min_pct, retest_max_pct] band below (long) / above (short) the last close.

    Candidates: breakout level (prior 4h high/low), EMA20(5m), rolling VWAP(4h),
    Fibonacci 38.2 % of the surge leg. No candidate in the band -> the band edge
    closest to a support, else retest_default_pct.
    """
    p = bars[-1]['c']
    d = 1 if side == 'LONG' else -1
    closes = [b['c'] for b in bars[-80:]]
    leg = bars[-(w + 1):]
    lo, hi = min(b['l'] for b in leg), max(b['h'] for b in leg)
    fib = hi - 0.382 * (hi - lo) if d > 0 else lo + 0.382 * (hi - lo)
    levels = {'breakout': level, 'ema20': ema_series(closes, 20)[-1],
              'vwap': vwap(bars[-cfg.vwap_bars:]), 'fib382': fib}
    depth = {k: d * (p - v) / p * 100.0 for k, v in levels.items() if v}
    inside = {k: x for k, x in depth.items() if cfg.retest_min_pct <= x <= cfg.retest_max_pct}
    if inside:
        anchor = min(inside, key=inside.get)                 # shallowest support in the band
        pull = inside[anchor]
    else:
        below = {k: x for k, x in depth.items() if x > 0}
        if below and min(below.values()) < cfg.retest_min_pct:
            anchor, pull = min(below, key=below.get) + '>min', cfg.retest_min_pct
        elif below:
            anchor, pull = min(below, key=below.get) + '>max', cfg.retest_max_pct
        else:
            anchor, pull = 'default', cfg.retest_default_pct
    return {'price': p * (1 - d * pull / 100.0), 'pullback_pct': pull, 'anchor': anchor,
            'levels': {k: v for k, v in levels.items() if v}}


def min_history(cfg: RadarConfig) -> int:
    return max(cfg.baseline_bars, cfg.breakout_lookback) + max(cfg.windows) + 1


def features(bars: List[dict], cfg: RadarConfig) -> Optional[dict]:
    """Signal on the LAST bar of `bars`, using only those bars."""
    if len(bars) < min_history(cfg):
        return None
    qv = [b['qv'] for b in bars]
    best = None
    for w in cfg.windows:
        base = statistics.median(qv[-(w + cfg.baseline_bars):-w]) * w
        recent = sum(qv[-w:])
        ratio = recent / base if base > 0 else 0.0
        if best is None or ratio > best[1]:
            best = (w, ratio, recent)
    w, surge, recent = best
    c_now, c_then = bars[-1]['c'], bars[-w - 1]['c']
    ret = (c_now / c_then - 1.0) * 100.0 if c_then > 0 else 0.0
    prior = bars[-(w + cfg.breakout_lookback):-w]
    hi, lo = max(b['h'] for b in prior), min(b['l'] for b in prior)
    side = 'LONG' if ret >= 0 else 'SHORT'
    rsi = rsi_series([b['c'] for b in bars[-(cfg.rsi_len * 8):]], cfg.rsi_len)[-1]
    taker = sum(b['tbq'] for b in bars[-w:]) / recent if recent > 0 else 0.5
    if side == 'LONG':
        breakout = c_now > hi
        rsi_ok = rsi is not None and rsi >= cfg.rsi_long
        taker_ok = taker >= cfg.taker_long
        level = hi
    else:
        breakout = c_now < lo
        rsi_ok = rsi is not None and rsi <= cfg.rsi_short
        taker_ok = taker <= cfg.taker_short
        level = lo
    surge_ok = surge >= cfg.surge_min
    chase = abs(ret) > cfg.max_chase_pct or (rsi is not None and (rsi > cfg.rsi_extreme
                                                                 or rsi < 100 - cfg.rsi_extreme))
    score = (2.0 * min(math.log2(max(surge, 1.0)), 4.0) + 2.0 * breakout + 1.5 * rsi_ok
             + 1.5 * taker_ok - 2.0 * chase)
    retest = retest_zone(bars, side, level, w, cfg)
    return {'window_min': w * 5, 'surge': surge, 'ret_pct': ret, 'side': side, 'retest': retest,
            'breakout': breakout, 'level': level, 'rsi': rsi, 'taker': taker,
            'surge_ok': surge_ok, 'rsi_ok': rsi_ok, 'taker_ok': taker_ok, 'chase': chase,
            'atr_pct': atr_pct(bars[-60:]), 'price': c_now, 'score': score,
            'fired': surge_ok and breakout and rsi_ok and (taker_ok or not cfg.require_taker)}


def obi_from_depth(depth: dict) -> Optional[float]:
    try:
        b = sum(float(p) * float(q) for p, q in depth.get('bids', [])[:20])
        a = sum(float(p) * float(q) for p, q in depth.get('asks', [])[:20])
    except (TypeError, ValueError):
        return None
    return (b - a) / (b + a) if b + a > 0 else None


# ----------------------------------------------------------------------------- sizing
def _decimals(step: float) -> int:
    s = f'{step:.12f}'.rstrip('0')
    return len(s.split('.')[1]) if '.' in s else 0


def trade_plan(price: float, side: str, spec: dict, cfg: RadarConfig,
               atr: Optional[float] = None, entry_maker: bool = False) -> dict:
    """3.85 USDT plan: min-notional size, ATR stop (or legacy money cap), TP ladder >= rr_min."""
    step, min_qty = spec.get('step', 0.001), spec.get('min_qty', 0.0)
    min_notional = spec.get('min_notional', 5.0)
    qty = max(min_notional * 1.02 / price, min_qty)
    qty = round(math.ceil(round(qty / step, 9)) * step, _decimals(step))
    notional = qty * price
    lev = next((lv for lv in sorted(cfg.leverage_choices)
                if notional / lv <= cfg.equity * cfg.margin_frac), None)
    entry_bps = cfg.maker_bps if entry_maker else cfg.taker_bps
    cost_pct = (entry_bps + cfg.taker_bps + cfg.slip_bps) / 100.0   # entry + stop out
    d = 1 if side == 'LONG' else -1
    warnings = []
    risk_ok = True
    if cfg.sl_mode == 'atr':
        if not atr or atr <= 0:
            sl_pct, risk_ok = cfg.min_sl_pct, False
            warnings.append('NO_ATR: không tính được ATR 5m')
        else:
            sl_pct = max(cfg.atr_sl_mult * atr, cfg.min_sl_pct)
            if notional * (sl_pct + cost_pct) / 100.0 > cfg.max_loss_usdt + 1e-12:
                risk_ok = False
                warnings.append(f'RISK: SL {sl_pct:.2f}% ({cfg.atr_sl_mult:g}xATR) x {notional:.2f} U = '
                                f'{notional * (sl_pct + cost_pct) / 100.0:.3f} U > {cfg.max_loss_usdt} U: bỏ qua')
    else:
        sl_pct = cfg.max_loss_usdt / notional * 100.0 - cost_pct
        if atr is not None and sl_pct < 1.5 * atr:
            warnings.append(f'SL {sl_pct:.2f}% < 1.5×ATR5m ({1.5 * atr:.2f}%): dễ bị quét nhiễu')
    if lev is None:
        warnings.append(f'MIN_NOTIONAL {min_notional:g} USDT không vừa vốn {cfg.equity} USDT')
    if sl_pct < cfg.min_sl_pct:
        warnings.append(f'SL tối đa {sl_pct:.2f}% quá hẹp: bỏ qua')
    # targets scale up together so TP1 is never below rr_min x stop
    scale = max(1.0, cfg.rr_min * sl_pct / cfg.tp_pcts[0]) if cfg.sl_mode == 'atr' and sl_pct > 0 else 1.0
    tps = []
    for tp in (t * scale for t in cfg.tp_pcts):
        net = tp - (entry_bps + cfg.maker_bps) / 100.0
        tps.append({'tp_pct': tp, 'price': price * (1 + d * tp / 100.0),
                    'pnl_usdt': notional * net / 100.0, 'pnl_vnd': notional * net / 100.0 * USDT_VND,
                    'rr': tp / sl_pct if sl_pct > 0 else None})
    loss = notional * (sl_pct + cost_pct) / 100.0
    return {'qty': qty, 'notional': notional, 'leverage': lev,
            'margin': notional / lev if lev else None, 'entry': price,
            'sl_pct': sl_pct, 'sl': price * (1 - d * sl_pct / 100.0),
            'loss_usdt': loss, 'loss_vnd': loss * USDT_VND, 'tps': tps,
            'tradeable': lev is not None and sl_pct >= cfg.min_sl_pct and risk_ok, 'warnings': warnings,
            'sl_mode': cfg.sl_mode, 'atr_pct': atr}


def parse_specs(info: dict) -> Dict[str, dict]:
    out = {}
    for s in (info or {}).get('symbols', []):
        if (s.get('status') != 'TRADING' or s.get('quoteAsset') != 'USDT'
                or s.get('contractType') != 'PERPETUAL'):
            continue
        f = {x.get('filterType'): x for x in s.get('filters', [])}
        try:
            out[s['symbol']] = {'step': float(f['LOT_SIZE']['stepSize']),
                                'min_qty': float(f['LOT_SIZE']['minQty']),
                                'min_notional': float(f.get('MIN_NOTIONAL', {}).get('notional', 5.0)),
                                'tick': float(f['PRICE_FILTER']['tickSize'])}
        except (KeyError, TypeError, ValueError):
            continue
    return out


# ----------------------------------------------------------------------------- event study
def simulate(bars: List[dict], i: int, side: str, tp_pct: float, sl_pct: float,
             hold: int, cfg: RadarConfig, limit: Optional[float] = None,
             ttl_bars: int = 1) -> Optional[Tuple[str, float]]:
    """Market: enter at bars[i+1].open. Limit: rests for `ttl_bars` bars after the
    signal and fills when a bar trades through it ('NOFILL' otherwise). Stop is
    checked before target inside every bar, and on the fill bar only the stop can
    trigger (its high may have printed before the fill) - conservative both ways."""
    if i + 1 >= len(bars):
        return None
    d = 1 if side == 'LONG' else -1
    start = i + 1
    entry_bps = cfg.taker_bps
    if limit is None:
        entry = bars[i + 1]['o']
    else:
        entry = None
        for j in range(i + 1, min(len(bars), i + 1 + ttl_bars)):
            b = bars[j]
            if d * (limit - b['o']) >= 0:                  # gapped through: filled at the open
                entry, start = b['o'], j
                break
            if (d > 0 and b['l'] <= limit) or (d < 0 and b['h'] >= limit):
                entry, start = limit, j
                break
        if entry is None:
            return ('NOFILL', 0.0) if i + ttl_bars < len(bars) else None
        entry_bps = cfg.maker_bps
    tp = entry * (1 + d * tp_pct / 100.0)
    sl = entry * (1 - d * sl_pct / 100.0)
    end = min(len(bars) - 1, start - 1 + hold)
    if end < start:
        return None
    for j in range(start, end + 1):
        b = bars[j]
        if (d > 0 and b['l'] <= sl) or (d < 0 and b['h'] >= sl):
            return 'SL', -sl_pct - (entry_bps + cfg.taker_bps + cfg.slip_bps) / 100.0
        fill_bar = limit is not None and j == start and entry != b['o']
        if not fill_bar and ((d > 0 and b['h'] >= tp) or (d < 0 and b['l'] <= tp)):
            return 'TP', tp_pct - (entry_bps + cfg.maker_bps) / 100.0
    if end < start - 1 + hold:
        return None                       # not enough future bars: unresolved, not counted
    gross = d * (bars[end]['c'] / entry - 1.0) * 100.0
    return 'TIME', gross - (entry_bps + cfg.taker_bps + cfg.slip_bps) / 100.0


def _entry(bars: List[dict], i: int, d: int, limit: Optional[float], ttl_bars: int, cfg: RadarConfig):
    """-> (entry, start_index, entry_bps) | 'NOFILL' | None."""
    if i + 1 >= len(bars):
        return None
    if limit is None:
        return bars[i + 1]['o'], i + 1, cfg.taker_bps
    for j in range(i + 1, min(len(bars), i + 1 + ttl_bars)):
        b = bars[j]
        if d * (limit - b['o']) >= 0:
            return b['o'], j, cfg.maker_bps
        if (d > 0 and b['l'] <= limit) or (d < 0 and b['h'] >= limit):
            return limit, j, cfg.maker_bps
    return 'NOFILL' if i + ttl_bars < len(bars) else None


def simulate_ladder(bars: List[dict], i: int, side: str, tp1: float, tp2: float, sl_pct: float,
                    hold: int, cfg: RadarConfig, limit: Optional[float] = None, ttl_bars: int = 1,
                    split: float = 0.5) -> Optional[Tuple[str, float]]:
    """The live bracket: `split` off at TP1 (maker), stop -> entry, rest at TP2 / BE / time.
    Net % of the whole position after every fee. Inside a bar the stop is checked
    first; after TP1 the breakeven stop is also checked in the same bar (the low
    may have come first); on a limit's fill bar no target counts."""
    d = 1 if side == 'LONG' else -1
    e = _entry(bars, i, d, limit, ttl_bars, cfg)
    if e is None or e == 'NOFILL':
        return None if e is None else ('NOFILL', 0.0)
    entry, start, entry_bps = e
    exit_taker = (cfg.taker_bps + cfg.slip_bps) / 100.0
    maker = cfg.maker_bps / 100.0
    stop = entry * (1 - d * sl_pct / 100.0)
    p1, p2 = entry * (1 + d * tp1 / 100.0), entry * (1 + d * tp2 / 100.0)
    realized, half = 0.0, False
    end = min(len(bars) - 1, start - 1 + hold)
    if end < start:
        return None
    for j in range(start, end + 1):
        b = bars[j]
        hit_stop = (d > 0 and b['l'] <= stop) or (d < 0 and b['h'] >= stop)
        if hit_stop:
            left = (1 - split) if half else 1.0
            leg = d * (stop / entry - 1.0) * 100.0 - exit_taker
            return ('TP1+BE' if half else 'SL'), realized + left * leg - entry_bps / 100.0
        if limit is not None and j == start and entry != b['o']:
            continue
        if not half and ((d > 0 and b['h'] >= p1) or (d < 0 and b['l'] <= p1)):
            realized += split * (tp1 - maker)
            half, stop = True, entry
            if (d > 0 and b['l'] <= entry) or (d < 0 and b['h'] >= entry):
                return 'TP1+BE', realized + (1 - split) * (-exit_taker) - entry_bps / 100.0
        if half and ((d > 0 and b['h'] >= p2) or (d < 0 and b['l'] <= p2)):
            return 'TP1+TP2', realized + (1 - split) * (tp2 - maker) - entry_bps / 100.0
    if end < start - 1 + hold:
        return None
    gross = d * (bars[end]['c'] / entry - 1.0) * 100.0
    left = (1 - split) if half else 1.0
    return ('TP1+TIME' if half else 'TIME'), realized + left * (gross - exit_taker) - entry_bps / 100.0


def hourly_trend(bars: List[dict], n: int) -> List[Optional[str]]:
    """Per 5m bar: 1h trend from COMPLETED hours only (no lookahead)."""
    closes, last_idx, cur, cnt = [], [], None, 0
    for idx, b in enumerate(bars):
        h = b['t'] // 3_600_000
        if h != cur:
            cur, cnt = h, 0
        cnt += 1
        if cnt == 12:                                      # 12 x 5m = one closed hour
            closes.append(b['c'])
            last_idx.append(idx)
    ema = ema_series(closes, n)
    out: List[Optional[str]] = [None] * len(bars)
    k = -1
    for idx in range(len(bars)):
        while k + 1 < len(last_idx) and last_idx[k + 1] <= idx:
            k += 1
        if k >= 0 and ema[k] is not None:
            out[idx] = 'UP' if closes[k] > ema[k] else 'DOWN'
    return out


DEFAULT_SPEC = {'step': 1e-9, 'min_qty': 0.0, 'min_notional': 5.0}


def event_study(bars: List[dict], cfg: RadarConfig, sl_pct: Optional[float] = None,
                tp_pct: Optional[float] = None, cooldown: int = 12, entry: str = 'market',
                spec: Optional[dict] = None) -> List[dict]:
    """Every past firing of `features()` on this series, resolved forward.
    entry='retest' rests a maker limit at the retest zone for cfg.retest_ttl_bars.
    The 1h trend gate is applied when '1h' is in cfg.trend_frames (4h needs more
    history than 1500 x 5m bars carry, so it is live-only).
    sl_pct/tp_pct given -> fixed single-target study (legacy). None -> each signal is
    sized by trade_plan() exactly as the live trader would (ATR stop, rr-scaled TP
    ladder); signals the plan refuses are kept as outcome 'SKIP'."""
    out = []
    trend = hourly_trend(bars, cfg.trend_ema) if '1h' in cfg.trend_frames else None
    need = min_history(cfg)
    qv = [b['qv'] for b in bars]
    pref = [0.0]
    for v in qv:
        pref.append(pref[-1] + v)
    last = -10 ** 9
    wmin = min(cfg.windows)
    for i in range(need - 1, len(bars) - 1):
        if i - last < cooldown:
            continue
        # cheap pre-screen: short-window volume vs mean baseline
        base_mean = (pref[i + 1 - wmin] - pref[i + 1 - wmin - cfg.baseline_bars]) / cfg.baseline_bars
        recent = pref[i + 1] - pref[i + 1 - max(cfg.windows)]
        if base_mean <= 0 or recent / (base_mean * max(cfg.windows)) < cfg.surge_min * 0.25:
            continue
        f = features(bars[max(0, i + 1 - cfg.bars):i + 1], cfg)
        if not f or not f['fired'] or f['chase']:
            continue
        if trend is not None and trend[i] != ('UP' if f['side'] == 'LONG' else 'DOWN'):
            continue
        limit = f['retest']['price'] if entry == 'retest' else None
        if sl_pct is not None:
            res = simulate(bars, i, f['side'], tp_pct, sl_pct, cfg.hold_bars, cfg, limit, cfg.retest_ttl_bars)
            s_pct = sl_pct
        else:
            plan = trade_plan(limit or f['price'], f['side'], spec or DEFAULT_SPEC, cfg, f['atr_pct'],
                              entry_maker=limit is not None)
            s_pct = plan['sl_pct']
            if not plan['tradeable']:
                last = i
                out.append({'t': bars[i]['t'], 'side': f['side'], 'outcome': 'SKIP', 'net_pct': 0.0,
                            'sl_pct': s_pct})
                continue
            tps = [t['tp_pct'] for t in plan['tps']]
            if cfg.ladder and len(tps) > 1:
                res = simulate_ladder(bars, i, f['side'], tps[0], tps[1], s_pct, cfg.hold_bars, cfg,
                                      limit, cfg.retest_ttl_bars)
            else:
                res = simulate(bars, i, f['side'], tps[0], s_pct, cfg.hold_bars, cfg, limit, cfg.retest_ttl_bars)
        if res is None:
            continue
        last = i
        out.append({'t': bars[i]['t'], 'side': f['side'], 'outcome': res[0], 'net_pct': res[1],
                    'sl_pct': s_pct})
    return out


def summarize_study(rows: List[dict]) -> dict:
    skipped = sum(r['outcome'] == 'SKIP' for r in rows)
    rows = [r for r in rows if r['outcome'] != 'SKIP']
    signals = len(rows)
    rows = [r for r in rows if r['outcome'] != 'NOFILL']
    n = len(rows)
    if not n:
        return {'n': 0, 'signals': signals, 'skipped_risk': skipped, 'fill_rate': 0.0 if signals else None}
    nets = [r['net_pct'] for r in rows]
    mean = sum(nets) / n
    sd = statistics.stdev(nets) if n > 1 else None
    return {'n': n, 'signals': signals, 'skipped_risk': skipped, 'fill_rate': n / signals,
            'tp_rate': sum(r['outcome'] == 'TP' or r['outcome'].startswith('TP1') for r in rows) / n,
            'tp2_rate': sum(r['outcome'] == 'TP1+TP2' for r in rows) / n,
            'be_rate': sum(r['outcome'] == 'TP1+BE' for r in rows) / n,
            'win_rate': sum(r['net_pct'] > 0 for r in rows) / n,
            'avg_sl_pct': (sum(r.get('sl_pct') or 0 for r in rows) / n) if rows[0].get('sl_pct') is not None else None,
            'sl_rate': sum(r['outcome'] == 'SL' for r in rows) / n,
            'time_rate': sum(r['outcome'] in ('TIME', 'TP1+TIME') for r in rows) / n,
            'mean_net_pct': mean,
            'lower95_pct': mean - 1.96 * sd / math.sqrt(n) if sd is not None else None}


# ----------------------------------------------------------------------------- live scan
def select_universe(tickers: List[dict], specs: Dict[str, dict], cfg: RadarConfig) -> List[str]:
    rows = []
    for t in tickers or []:
        s = t.get('symbol')
        if s not in specs:
            continue
        try:
            qv, chg = float(t['quoteVolume']), float(t['priceChangePercent'])
        except (KeyError, TypeError, ValueError):
            continue
        if qv >= cfg.min_quote_vol_24h:
            rows.append((s, qv, chg))
    by_vol = [r[0] for r in sorted(rows, key=lambda r: -r[1])[:cfg.prefilter_each]]
    by_move = [r[0] for r in sorted(rows, key=lambda r: -abs(r[2]))[:cfg.prefilter_each]]
    return list(dict.fromkeys(by_move + by_vol))


def trend_gate(symbol: str, side: str, cfg: RadarConfig, http: Callable,
               now_ms: float) -> Tuple[Dict[str, Optional[str]], bool]:
    """EMA(trend_ema) on closed 1h/4h bars. Unknown (short history, fetch error) fails closed."""
    want = 'UP' if side == 'LONG' else 'DOWN'
    states: Dict[str, Optional[str]] = {}
    for tf in cfg.trend_frames:
        try:
            bars = parse_klines(http(f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval={tf}'
                                     f'&limit={cfg.trend_ema + 20}'), now_ms)
            states[tf] = trend_state([b['c'] for b in bars], cfg.trend_ema)
        except Exception:
            states[tf] = None
    return states, all(v == want for v in states.values())


def scan(cfg: RadarConfig = RadarConfig(), http: Callable = _get_json,
         now_ms: Optional[float] = None, study_tp: Optional[float] = None) -> dict:
    t0 = time.time()
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    specs = parse_specs(http(f'{FAPI}/fapi/v1/exchangeInfo'))
    tickers = http(f'{FAPI}/fapi/v1/ticker/24hr')
    universe = select_universe(tickers, specs, cfg)
    rows, errors = [], []
    for s in universe:
        try:
            bars = parse_klines(http(f'{FAPI}/fapi/v1/klines?symbol={s}&interval={cfg.interval}'
                                     f'&limit={cfg.bars + 1}'), now_ms)
        except Exception as exc:   # one bad symbol never kills the scan
            errors.append(f'{s}: {type(exc).__name__}')
            continue
        f = features(bars, cfg)
        if f:
            f['symbol'] = s
            rows.append(f)
    rows.sort(key=lambda r: (-r['fired'], -r['score']))
    for r in rows[:cfg.finalists]:
        try:
            r['obi'] = obi_from_depth(http(f'{FAPI}/fapi/v1/depth?symbol={r["symbol"]}&limit=20'))
        except Exception:
            r['obi'] = None
        d = 1 if r['side'] == 'LONG' else -1
        r['obi_ok'] = r['obi'] is not None and d * r['obi'] >= cfg.obi_min
        r['score'] += 1.0 * r['obi_ok']
    for r in rows:
        if r['fired']:
            r['trend'], r['trend_ok'] = trend_gate(r['symbol'], r['side'], cfg, http, now_ms)
            if not r['trend_ok']:
                r['blocked'] = 'TREND'
    fired = sorted((r for r in rows if r['fired'] and r.get('trend_ok')), key=lambda r: -r['score'])
    picks = fired[:cfg.top_n]
    for r in picks:
        spec = specs.get(r['symbol'], {})
        r['plan'] = trade_plan(r['price'], r['side'], spec, cfg, r['atr_pct'])
        r['retest_plan'] = trade_plan(r['retest']['price'], r['side'], spec, cfg, r['atr_pct'], entry_maker=True)
        r['confirmations'] = [k for k in ('taker_ok', 'obi_ok') if r.get(k)]
    watch = sorted((r for r in rows if not (r['fired'] and r.get('trend_ok'))), key=lambda r: -r['surge'])[:10]
    return {'ts': time.time(), 'elapsed_s': time.time() - t0, 'universe': len(specs),
            'scanned': len(rows), 'fired': len(fired), 'picks': picks, 'watchlist': watch,
            'errors': errors[:10], 'config': asdict(cfg)}


def fetch_history(symbol: str, http: Callable, interval: str = '5m', limit: int = 1500,
                  pages: int = 1, now_ms: Optional[float] = None) -> List[dict]:
    """Up to pages x limit closed bars, oldest first (paged backwards with endTime)."""
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    out: List[dict] = []
    end = None
    for _ in range(max(1, pages)):
        url = f'{FAPI}/fapi/v1/klines?symbol={symbol}&interval={interval}&limit={limit}'
        if end is not None:
            url += f'&endTime={end}'
        chunk = parse_klines(http(url), now_ms)
        if not chunk:
            break
        out = chunk + [b for b in out if b['t'] > chunk[-1]['t']]
        if len(chunk) < limit:
            break
        end = chunk[0]['t'] - 1
    return out


def split_summary(rows: List[dict], frac: float = 0.7) -> dict:
    """In-sample (first `frac` of signals by time) vs out-of-sample (the rest)."""
    rows = sorted(rows, key=lambda r: r['t'])
    k = int(len(rows) * frac)
    return {'is': summarize_study(rows[:k]), 'oos': summarize_study(rows[k:])}


def study_symbols(series: Dict[str, List[dict]], cfg: RadarConfig, specs: Optional[dict] = None) -> dict:
    """Pooled plan-driven study (market + retest) over pre-fetched series."""
    market, retest, per_symbol = [], [], {}
    for s, bars in series.items():
        if len(bars) < min_history(cfg) + cfg.hold_bars:
            continue
        spec = (specs or {}).get(s, DEFAULT_SPEC)
        rows = event_study(bars, cfg, spec=spec)
        market += rows
        retest += event_study(bars, cfg, spec=spec, entry='retest')
        per_symbol[s] = summarize_study(rows)
    label = 'ladder' if cfg.ladder and len(cfg.tp_pcts) > 1 else f'{cfg.tp_pcts[0]:g}'
    return {'symbols': len(per_symbol), 'by_tp': {label: summarize_study(market)},
            'by_tp_retest': {label: summarize_study(retest)}, 'split': split_summary(market),
            'per_symbol': per_symbol}


def backtest(symbols: Iterable[str], cfg: RadarConfig = RadarConfig(), http: Callable = _get_json,
             limit: int = 1500, now_ms: Optional[float] = None, specs: Optional[dict] = None,
             pages: int = 1) -> dict:
    """Pooled event study over `symbols`, each signal sized exactly like the live plan."""
    now_ms = time.time() * 1000 if now_ms is None else now_ms
    series = {}
    for s in symbols:
        try:
            series[s] = fetch_history(s, http, cfg.interval, limit, pages, now_ms)
        except Exception:
            continue
    out = study_symbols(series, cfg, specs)
    out.update(ts=time.time(), bars_per_symbol=limit * pages,
               trend_gate=[tf for tf in cfg.trend_frames if tf == '1h'],
               config={k: v for k, v in asdict(cfg).items()
                       if k in ('surge_min', 'taker_long', 'breakout_lookback', 'sl_mode', 'atr_sl_mult',
                                'rr_min', 'tp_pcts', 'hold_bars', 'max_loss_usdt')})
    return out
