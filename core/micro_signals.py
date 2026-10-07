"""Microstructure feed for the 2-mode engine: one symbol, Binance USD-M public WS.

Binance split the USD-M WS (upgrade notice 2026-03-06, legacy URLs decommissioned 2026-04-23):
  /public  -> <sym>@depth5@100ms              wss://fstream.binance.com/public/stream?streams=...
  /market  -> <sym>@aggTrade, @markPrice@1s, @forceOrder   wss://fstream.binance.com/market/stream?streams=...
The legacy /stream?streams= URL kept only /public data, so aggTrade never arrived: that was
the "ZEPHR warmup 0/30 trades" stall. Two sockets now; ZEPHR reports a silent /market feed.
Features (all O(1) amortised per message):
  obi_ema     L5 order-book imbalance, EMA                      (research.flow_gauge)
  cvd_norm    CVD 60 s / gross traded 60 s, in [-1, 1]          (research.flow_gauge)
  vwap_dev    (last - VWAP_60s) / VWAP_60s in %                  (micro VWAP 1m)
  liq_usd     liquidated notional in the last `liq_window_s` per side (LONG / SHORT)
  spread_bps  best ask - best bid
Signals (2026-10-07 confluence brief):
  S1 FLOW   |obi_ema| >= 0.20, cvd_norm agrees >= 0.20 AND CVD of the last 10 s agrees (sustained)
  S2 VWAP   |vwap_dev| >= 0.08 % AND price at the local extreme of the 60 s range (bottom/top 25 %)
  S3 LIQ    one side liquidated >= $10k in 3 s                   -> counter-trend (buy the long flush)
  S4 TREND  CVD 60 s <= -$300k, OBI <= -0.15, price below VWAP 1m -> SHORT (mirror -> LONG)  (2026-10-07b)
  S5 SURGE  CVD velocity: cvd_10s/10 >= $50k/s AND >= 2x the 60 s pace (cvd_60s/60)  -> follow (2026-10-07e)
  S6 SQUEEZE price box of [t-120 s, t-15 s] <= 0.08 % wide, then a break of the box edge with SURGE in the
            same direction -> breakout (TIDAL)
  WALL      depth L10 imbalance (sum bid qty - sum ask qty) / total >= +0.35 -> supports LONG (non-voting:
            confluence + blocks entries INTO an opposite wall)

CVD flow-regime gate, multi-timeframe (2026-10-07c; USD thresholds x symbol scale):
  UP       CVD 60 s >= +$400k AND CVD 3 m >= +$600k  -> LONG ONLY
  DOWN     CVD 60 s <= -$400k AND CVD 3 m <= -$600k  -> SHORT ONLY
  NEUTRAL  |CVD 60 s| <= $200k and |CVD 3 m| <= $400k -> 2-way, VWAP mean-reversion allowed
  DRIFT    anything else, incl. 60 s vs 3 m conflict -> 2-way, no TREND, no mean-reversion
A 60 s burst alone can no longer flip UP <-> DOWN (the 08:24 UP -> 08:26 DOWN whipsaw).
"""
from __future__ import annotations

import math
import time
from collections import deque
from dataclasses import dataclass
from typing import Deque, Optional, Tuple

from research.flow_gauge import FlowGauge

WS_PUBLIC = 'wss://fstream.binance.com/public/stream?streams='
WS_MARKET = 'wss://fstream.binance.com/market/stream?streams='


@dataclass(frozen=True)
class SignalConfig:
    obi_min: float = 0.20
    cvd_min: float = 0.20
    cvd_fast_s: float = 10.0
    vwap_window_s: float = 60.0
    vwap_dev_pct: float = 0.08
    extreme_frac: float = 0.25          # S2: price within the bottom/top 25 % of the 60 s range
    liq_usd: float = 10_000.0
    liq_window_s: float = 3.0
    stale_s: float = 3.0
    trade_silent_s: float = 15.0        # depth flowing but no aggTrade -> /market socket is dead
    usd_scale: float = 1.0              # BTC = 1.0; thinner books scale the USD thresholds down
    regime_usd: float = 400_000.0       # CVD 60 s
    regime_long_usd: float = 600_000.0  # CVD 3 m, same sign required
    long_window_s: float = 180.0
    neutral_usd: float = 200_000.0
    neutral_long_usd: float = 400_000.0
    trend_usd: float = 300_000.0
    trend_obi: float = 0.15
    flow_exit_usd: float = 2_000_000.0  # MARIN: adverse CVD within flow_exit_s -> exit (was $500k: 13/17 noise exits)
    flow_exit_s: float = 15.0
    surge_usd_s: float = 50_000.0       # USD/s of net aggressor flow over 10 s
    surge_ratio: float = 2.0            # 10 s pace vs 60 s pace
    wall_min: float = 0.35
    squeeze_range_pct: float = 0.08
    squeeze_box_s: float = 120.0
    squeeze_gap_s: float = 15.0
    ker_window_s: float = 120.0         # Kaufman Efficiency Ratio over 2 min of trade prints
    ker_bucket_s: float = 5.0           # sampled every 5 s (last print): no bid/ask bounce, 24 steps
    ker_min_span_s: float = 60.0        # < 60 s of prints -> KER unknown (treated as CHOP)
    ker_lock: float = 0.40              # KER < 0.40 -> CHOP LOCKED
    ker_unlock: float = 0.48            # unlock only at KER >= 0.48 (hysteresis band 0.40-0.48; was 0.45)
    absorb_window_s: float = 15.0       # price-delta concordance window
    absorb_usd: float = 500_000.0       # |CVD 15 s| >= $500k (x scale) ...
    absorb_min_move_pct: float = 0.03   # ... must move price >= 0.03 % the same way, else ABSORPTION TRAP
    macro_vwap_s: float = 300.0         # rolling 5 m VWAP
    macro_vwap_min_s: float = 240.0     # VWAP 5 m needs >= 4 min of prints
    macro_candle_s: float = 300.0       # 5 m candles for the EMA
    macro_ema_n: int = 20               # EMA-20 of 5 m closes (seeded from REST klines at start)


USD_SCALE = {'BTCUSDT': 1.0, 'ETHUSDT': 0.5, 'SOLUSDT': 0.1, 'XRPUSDT': 0.1, 'DOGEUSDT': 0.1}


def ker(points, now: float, window_s: float = 120.0, bucket_s: float = 5.0, min_span_s: float = 60.0):
    """Kaufman Efficiency Ratio = |P_t - P_{t-window}| / sum |P_i - P_{i-1}| on bucket-sampled prints.
    points: iterable of (t, px). -> (ker 0..1 or None, span_s). Flat tape (no path) -> 0.0 (no trend)."""
    last = {}
    first_t = None
    for t, px in points:
        age = now - t
        if age < 0 or age > window_s or not px:
            continue
        first_t = t if first_t is None else min(first_t, t)
        k = int(age // bucket_s)
        if k not in last or t >= last[k][0]:
            last[k] = (t, px)
    span = now - first_t if first_t is not None else 0.0
    if span < min_span_s or len(last) < 3:
        return None, span
    pxs = [last[k][1] for k in sorted(last, reverse=True)]          # oldest bucket first
    path = sum(abs(b - a) for a, b in zip(pxs, pxs[1:]))
    return (abs(pxs[-1] - pxs[0]) / path if path > 0 else 0.0), span


def chop_locked(k, was_locked: bool = True, cfg: 'SignalConfig' = None) -> bool:
    """Hysteresis: lock below ker_lock, unlock at >= ker_unlock, keep the previous state in between."""
    cfg = cfg or SignalConfig()
    if k is None or k < cfg.ker_lock:
        return True
    if k >= cfg.ker_unlock:
        return False
    return was_locked


def absorption(f: dict, cfg: 'SignalConfig' = None) -> dict:
    """Price-Delta Concordance: heavy aggressor flow that does not move price = a limit wall absorbing it.
    -> {'state': CLEAR|TRAPPED|UNKNOWN, 'blocks': 'BUY'|'SELL'|None, 'cvd_15s', 'vel_pct', 'why'}"""
    cfg = cfg or SignalConfig()
    cvd, vel = f.get('cvd_15s'), f.get('px_vel_15s')
    if cvd is None or vel is None:
        return {'state': 'UNKNOWN', 'blocks': None, 'cvd_15s': cvd, 'vel_pct': vel, 'why': 'no 15 s price history'}
    lim, mv = cfg.absorb_usd * cfg.usd_scale, cfg.absorb_min_move_pct
    why = f'CVD 15s {cvd / 1e6:+.2f}M · giá {vel:+.3f}%'
    if cvd >= lim and vel < mv:
        return {'state': 'TRAPPED', 'blocks': 'BUY', 'cvd_15s': cvd, 'vel_pct': vel,
                'why': f'{why} < +{mv}% (tường Ask hấp thụ lực mua)'}
    if cvd <= -lim and vel > -mv:
        return {'state': 'TRAPPED', 'blocks': 'SELL', 'cvd_15s': cvd, 'vel_pct': vel,
                'why': f'{why} > -{mv}% (tường Bid hấp thụ lực bán)'}
    return {'state': 'CLEAR', 'blocks': None, 'cvd_15s': cvd, 'vel_pct': vel, 'why': why}


def macro_bias(f: dict, cfg: 'SignalConfig' = None) -> dict:
    """5 m macro gate: price > VWAP 5 m AND EMA-20(5 m) rising -> LONG ONLY; mirrored -> SHORT ONLY."""
    cfg = cfg or SignalConfig()
    px, vw, slope, n = f.get('price'), f.get('vwap_5m'), f.get('ema5_slope'), f.get('ema5_n') or 0
    span = f.get('vwap_5m_span') or 0.0
    if not px or vw is None or slope is None or span < cfg.macro_vwap_min_s or n < cfg.macro_ema_n:
        return {'name': 'WARMUP', 'allow': ('BUY', 'SELL'),
                'why': f'VWAP5m {span:.0f}/{cfg.macro_vwap_min_s:.0f}s · EMA {n}/{cfg.macro_ema_n} nến'}
    why = f'giá {(px / vw - 1) * 100:+.3f}% vs VWAP5m · EMA20 dốc {slope:+.1f}'
    if px > vw and slope > 0:
        return {'name': 'LONG ONLY', 'allow': ('BUY',), 'why': why}
    if px < vw and slope < 0:
        return {'name': 'SHORT ONLY', 'allow': ('SELL',), 'why': why}
    return {'name': 'MIXED', 'allow': ('BUY', 'SELL'), 'why': why}


def config_for(symbol: str) -> SignalConfig:
    return SignalConfig(usd_scale=USD_SCALE.get(symbol.upper(), 0.1))


def streams(symbol: str) -> dict:
    s = symbol.lower()
    return {'public': WS_PUBLIC + f'{s}@depth10@100ms',
            'market': WS_MARKET + f'{s}@aggTrade/{s}@markPrice@1s/{s}@forceOrder'}


class MicroFeed:
    def __init__(self, symbol: str, cfg: SignalConfig = SignalConfig()):
        self.symbol = symbol.upper()
        self.cfg = cfg
        self.flow = FlowGauge(self.symbol, window_s=60.0)
        self.vw: Deque[Tuple[float, float, float]] = deque()      # (t, px*qty, qty)
        self.vw_pq = self.vw_q = 0.0
        self.cvd_long: Deque[Tuple[float, float]] = deque()      # (t, signed usd) for the 3 m window
        self.cvd_long_sum = 0.0
        self.liq: Deque[Tuple[float, str, float]] = deque()      # (t, LONG|SHORT, usd)
        self.bid = self.ask = self.last = self.mark = None
        self.bid_qty = self.ask_qty = 0.0
        self.bid10 = self.ask10 = 0.0
        self.last_msg = 0.0
        self.last_book = 0.0
        self.last_trade = 0.0
        self.first_msg = 0.0
        self.n_trades = 0
        self.frames = 0
        self.vw5: Deque[list] = deque()                          # [second, px*qty, qty] for VWAP 5 m
        self.vw5_pq = self.vw5_q = 0.0
        self.c5_bucket = None                                    # open 5 m candle (index, close)
        self.c5_close = None
        self.ema5 = self.ema5_prev = None
        self.ema5_n = 0

    # ------------------------------------------------------------------ ingest
    def on_message(self, msg: dict, now: Optional[float] = None) -> None:
        now = time.time() if now is None else now
        stream = str(msg.get('stream', '')) if isinstance(msg, dict) else ''
        d = msg.get('data') if isinstance(msg, dict) else None
        if not isinstance(d, dict):
            return
        self.frames += 1
        self.last_msg = now
        self.first_msg = self.first_msg or now
        if stream.endswith('@aggTrade'):
            self.flow.on_agg_trade(d, now)
            try:
                px, q = float(d['p']), float(d['q'])
            except (KeyError, TypeError, ValueError):
                return
            self.last = px
            self.last_trade = now
            signed = -px * q if d.get('m') else px * q
            self.cvd_long.append((now, signed, px))
            self.cvd_long_sum += signed
            self.n_trades += 1
            self.vw.append((now, px * q, q))
            self.vw_pq += px * q
            self.vw_q += q
            sec = int(now)
            if self.vw5 and self.vw5[-1][0] == sec:
                self.vw5[-1][1] += px * q
                self.vw5[-1][2] += q
            else:
                self.vw5.append([sec, px * q, q])
            self.vw5_pq += px * q
            self.vw5_q += q
            self._candle(now, px)
            self._expire(now)
        elif '@depth' in stream:
            self.flow.on_depth(d, now)
            try:
                self.bid, self.bid_qty = float(d['b'][0][0]), float(d['b'][0][1])
                self.ask, self.ask_qty = float(d['a'][0][0]), float(d['a'][0][1])
                self.bid10 = sum(float(q) for _, q in d['b'][:10])
                self.ask10 = sum(float(q) for _, q in d['a'][:10])
                self.last_book = now
            except (KeyError, IndexError, TypeError, ValueError):
                pass
        elif '@markPrice' in stream:
            try:
                self.mark = float(d['p'])
            except (KeyError, TypeError, ValueError):
                pass
        elif stream.endswith('@forceOrder'):
            o = d.get('o') or {}
            try:
                usd = float(o.get('z') or o.get('q')) * float(o.get('ap') or o.get('p'))
            except (TypeError, ValueError):
                return
            if o.get('s') == self.symbol and usd > 0:
                self.liq.append((now, 'LONG' if o.get('S') == 'SELL' else 'SHORT', usd))

    def _candle(self, now: float, px: float) -> None:
        b = int(now // self.cfg.macro_candle_s)
        if self.c5_bucket is not None and b > self.c5_bucket and self.c5_close is not None:
            self._close_candle(self.c5_close)
        if self.c5_bucket is None or b >= self.c5_bucket:
            self.c5_bucket, self.c5_close = b, px

    def _close_candle(self, close: float) -> None:
        a = 2.0 / (self.cfg.macro_ema_n + 1)
        self.ema5_prev = self.ema5
        self.ema5 = close if self.ema5 is None else self.ema5 + a * (close - self.ema5)
        self.ema5_n += 1

    def seed_candles(self, closes, now: Optional[float] = None) -> int:
        """Seed the 5 m EMA with closed candles [(close_time_s, close)], oldest first (REST klines)."""
        now = time.time() if now is None else now
        n = 0
        for ct, c in sorted(closes):
            if ct <= now and c:
                self._close_candle(float(c))
                n += 1
        return n

    def _expire(self, now: float) -> None:
        while self.vw5 and now - self.vw5[0][0] > self.cfg.macro_vwap_s:
            _, pq, q = self.vw5.popleft()
            self.vw5_pq -= pq
            self.vw5_q -= q
        while self.vw and now - self.vw[0][0] > self.cfg.vwap_window_s:
            _, pq, q = self.vw.popleft()
            self.vw_pq -= pq
            self.vw_q -= q
        while self.liq and now - self.liq[0][0] > self.cfg.liq_window_s:
            self.liq.popleft()
        while self.cvd_long and now - self.cvd_long[0][0] > self.cfg.long_window_s:
            self.cvd_long_sum -= self.cvd_long.popleft()[1]
        while self.cvd_long and now - self.cvd_long[0][0] > max(self.cfg.long_window_s, self.cfg.squeeze_box_s):
            self.cvd_long_sum -= self.cvd_long.popleft()[1]

    def _concordance(self, now: float) -> dict:
        """CVD and price change over the last absorb_window_s (price = last print at/before t-15 s)."""
        w = self.cfg.absorb_window_s
        ref, cvd = None, 0.0
        for t, s, px in self.cvd_long:
            if now - t >= w:
                ref = px
            elif now - t >= 0:
                cvd += s
        vel = (self.last / ref - 1) * 100 if (ref and self.last) else None
        return {'cvd_15s': cvd if ref is not None else None, 'px_vel_15s': vel}

    # ------------------------------------------------------------------ features
    def price(self) -> Optional[float]:
        if self.bid and self.ask:
            return (self.bid + self.ask) / 2
        return self.last or self.mark

    def vwap(self) -> Optional[float]:
        return self.vw_pq / self.vw_q if self.vw_q > 1e-12 else None

    def features(self, now: Optional[float] = None) -> dict:
        now = time.time() if now is None else now
        self._expire(now)
        gross = self.flow.buy_usd + self.flow.sell_usd
        vwap, px = self.vwap(), self.last
        liq_long = sum(u for _, s, u in self.liq if s == 'LONG')
        liq_short = sum(u for _, s, u in self.liq if s == 'SHORT')
        spread = ((self.ask - self.bid) / self.price() * 1e4) if (self.bid and self.ask) else None
        fast = sum(s for t, s in self.flow.trades if now - t <= self.cfg.cvd_fast_s)
        exit_win = sum(s for t, s in self.flow.trades if now - t <= self.cfg.flow_exit_s)
        lo = hi = None
        if self.vw:
            pxs = [pq / q for _, pq, q in self.vw if q > 0]
            lo, hi = (min(pxs), max(pxs)) if pxs else (None, None)
        rng_pos = (px - lo) / (hi - lo) if (px and lo is not None and hi - lo > 1e-12) else None
        book_age = now - self.last_book if self.last_book else None
        box = [x[2] for x in self.cvd_long if self.cfg.squeeze_gap_s < now - x[0] <= self.cfg.squeeze_box_s]
        box_lo, box_hi = (min(box), max(box)) if len(box) >= 10 else (None, None)
        box_pct = (box_hi - box_lo) / box_lo * 100 if box_lo else None
        tot10 = self.bid10 + self.ask10
        trade_age = now - self.last_trade if self.last_trade else None
        return {'t': now, 'symbol': self.symbol, 'price': self.price(), 'last': px, 'mark': self.mark,
                'bid': self.bid, 'ask': self.ask, 'spread_bps': spread,
                'obi': self.flow.obi_ema, 'cvd_60s': self.flow.cvd_window(),
                'cvd_norm': self.flow.cvd_window() / gross if gross > 0 else 0.0,
                'cvd_10s': fast, 'cvd_3m': self.cvd_long_sum, 'cvd_exit_win': exit_win, 'vwap': vwap,
                'bid_qty': self.bid_qty, 'ask_qty': self.ask_qty,
                'cvd_vel_10s': fast / self.cfg.cvd_fast_s, 'cvd_vel_60s': self.flow.cvd_window() / 60.0,
                'depth10_imb': (self.bid10 - self.ask10) / tot10 if tot10 > 0 else None,
                'box_lo': box_lo, 'box_hi': box_hi, 'box_pct': box_pct, 'vwap_dev_pct': (px / vwap - 1) * 100 if (vwap and px) else None,
                'range_lo': lo, 'range_hi': hi, 'range_pos': rng_pos,
                'liq_long_usd': liq_long, 'liq_short_usd': liq_short,
                'stale': book_age is None or book_age > self.cfg.stale_s,
                'book_age_s': book_age, 'trade_age_s': trade_age,
                'trades_silent': (trade_age is None and self.first_msg and now - self.first_msg > self.cfg.trade_silent_s)
                or (trade_age is not None and trade_age > self.cfg.trade_silent_s),
                'trades': self.n_trades,
                **self._concordance(now),
                'vwap_5m': self.vw5_pq / self.vw5_q if self.vw5_q > 0 else None,
                'vwap_5m_span': now - self.vw5[0][0] if self.vw5 else 0.0,
                'ema5_20': self.ema5, 'ema5_n': self.ema5_n,
                'ema5_slope': (self.ema5 - self.ema5_prev) if self.ema5_prev is not None else None,
                **dict(zip(('ker', 'ker_span_s'), ker(((t, x) for t, _, x in self.cvd_long), now,
                                                       self.cfg.ker_window_s, self.cfg.ker_bucket_s,
                                                       self.cfg.ker_min_span_s)))}


def strength(x: float) -> float:
    """x = trigger value / threshold. 1.0x -> 0.70, 1.5x -> 1.00 (linear, capped).
    Two triggers at ~1.4x (or three at 1.0x) clear the 76.8 % supermajority."""
    return max(0.0, min(1.0, 0.70 + 0.60 * (x - 1.0)))


def regime(f: dict, cfg: SignalConfig = SignalConfig()) -> dict:
    """Hard multi-timeframe CVD gate. -> {'name', 'allow': (sides), 'mr_ok', 'trend_ok', 'label', 'why'}."""
    k = cfg.usd_scale
    cvd, long_ = f.get('cvd_60s') or 0.0, f.get('cvd_3m')
    long_ = cvd if long_ is None else long_
    why = f'CVD 60s {cvd / 1e6:+.2f}M · 3m {long_ / 1e6:+.2f}M · norm {(f.get("cvd_norm") or 0):+.2f}'
    if cvd <= -cfg.regime_usd * k and long_ <= -cfg.regime_long_usd * k:
        return {'name': 'DOWN', 'allow': ('SELL',), 'mr_ok': False, 'trend_ok': True,
                'label': 'STRONG DOWNTREND (SHORT ONLY)', 'why': why}
    if cvd >= cfg.regime_usd * k and long_ >= cfg.regime_long_usd * k:
        return {'name': 'UP', 'allow': ('BUY',), 'mr_ok': False, 'trend_ok': True,
                'label': 'STRONG UPTREND (LONG ONLY)', 'why': why}
    if abs(cvd) <= cfg.neutral_usd * k and abs(long_) <= cfg.neutral_long_usd * k:
        return {'name': 'NEUTRAL', 'allow': ('BUY', 'SELL'), 'mr_ok': True, 'trend_ok': False,
                'label': 'NEUTRAL (2-WAY)', 'why': why}
    conflict = (cvd > 0) != (long_ > 0)
    return {'name': 'DRIFT', 'allow': ('BUY', 'SELL'), 'mr_ok': False, 'trend_ok': False,
            'label': 'DRIFT (60s/3m CONFLICT · WAIT)' if conflict else 'DRIFT (UNCONFIRMED · WAIT)', 'why': why}


def signals(f: dict, cfg: SignalConfig = SignalConfig(), reg: dict = None) -> dict:
    """-> {'FLOW'|'VWAP'|'LIQ'|'TREND': {'side', 'strength' 0..1, 'edge_bps', 'why'} or None,
           'BANNED': [signals dropped by the regime gate]}."""
    reg = reg or regime(f, cfg)
    out = {'FLOW': None, 'VWAP': None, 'LIQ': None, 'TREND': None, 'SURGE': None, 'SQUEEZE': None, 'WALL': None}
    obi, cvd, fast = f.get('obi'), f.get('cvd_norm') or 0.0, f.get('cvd_10s') or 0.0
    if (obi is not None and abs(obi) >= cfg.obi_min and abs(cvd) >= cfg.cvd_min and (obi > 0) == (cvd > 0)
            and fast != 0 and (fast > 0) == (obi > 0)):
        s = strength(math.sqrt(abs(obi) / cfg.obi_min * abs(cvd) / cfg.cvd_min))
        out['FLOW'] = {'side': 'BUY' if obi > 0 else 'SELL', 'strength': s,
                       'edge_bps': 10.0 * abs(obi) * abs(cvd) * 2, 'why': f'OBI {obi:+.2f} · CVD {cvd:+.2f}'}
    dev, pos = f.get('vwap_dev_pct'), f.get('range_pos')
    at_extreme = pos is not None and (pos <= cfg.extreme_frac if (dev or 0) < 0 else pos >= 1 - cfg.extreme_frac)
    if dev is not None and abs(dev) >= cfg.vwap_dev_pct and at_extreme and reg['mr_ok']:
        s = strength(abs(dev) / cfg.vwap_dev_pct)
        out['VWAP'] = {'side': 'SELL' if dev > 0 else 'BUY', 'strength': s,
                       'edge_bps': abs(dev) * 100 * 0.5, 'why': f'VWAP dev {dev:+.3f}% @ {pos * 100:.0f}% of 60s range'}
    ll, ls = f.get('liq_long_usd') or 0.0, f.get('liq_short_usd') or 0.0
    big, side = (ll, 'BUY') if ll >= ls else (ls, 'SELL')
    if big >= cfg.liq_usd:
        s = strength(big / cfg.liq_usd)
        out['LIQ'] = {'side': side, 'strength': s, 'edge_bps': min(20.0, 3.0 * math.log2(1 + big / cfg.liq_usd)),
                      'why': f'{"LONG" if side == "BUY" else "SHORT"} liq ${big:,.0f}/{cfg.liq_window_s:g}s'}
    usd, k = f.get('cvd_60s') or 0.0, cfg.usd_scale
    if (reg.get('trend_ok', True) and obi is not None and dev is not None and abs(usd) >= cfg.trend_usd * k
            and abs(obi) >= cfg.trend_obi):
        down = usd < 0 and obi < 0 and dev < 0                 # sellers + ask-heavy book + below VWAP
        up = usd > 0 and obi > 0 and dev > 0
        if down or up:
            x = min(abs(usd) / (cfg.trend_usd * k), abs(obi) / cfg.trend_obi)
            out['TREND'] = {'side': 'SELL' if down else 'BUY', 'strength': strength(x), 'edge_bps': min(16.0, 4.0 * x),
                            'why': f'TREND CVD {usd / 1e6:+.2f}M · OBI {obi:+.2f} · VWAP {dev:+.3f}%'}
    v10, v60 = f.get('cvd_vel_10s') or 0.0, f.get('cvd_vel_60s') or 0.0
    if abs(v10) >= cfg.surge_usd_s * k and abs(v10) >= cfg.surge_ratio * abs(v60) and (v60 == 0 or (v10 > 0) == (v60 > 0)
                                                                                      or abs(v10) > abs(v60)):
        x = abs(v10) / (cfg.surge_usd_s * k)
        out['SURGE'] = {'side': 'BUY' if v10 > 0 else 'SELL', 'strength': strength(x), 'edge_bps': min(12.0, 4.0 * x),
                        'why': f'CVD velocity {v10 / 1e3:+.0f}k/s vs {v60 / 1e3:+.0f}k/s (60s)'}
    lo, hi, box, px = f.get('box_lo'), f.get('box_hi'), f.get('box_pct'), f.get('last')
    if (out['SURGE'] and box is not None and box <= cfg.squeeze_range_pct and px
            and ((out['SURGE']['side'] == 'BUY' and px > hi) or (out['SURGE']['side'] == 'SELL' and px < lo))):
        side = out['SURGE']['side']
        out['SQUEEZE'] = {'side': side, 'strength': 1.0, 'edge_bps': 10.0,
                          'why': f'SQUEEZE box {box:.3f}% -> break {"above" if side == "BUY" else "below"} '
                                 f'{hi if side == "BUY" else lo:g}'}
    wall = f.get('depth10_imb')
    if wall is not None and abs(wall) >= cfg.wall_min:
        out['WALL'] = {'side': 'BUY' if wall > 0 else 'SELL', 'strength': min(1.0, abs(wall)), 'edge_bps': 0.0,
                       'why': f'L10 wall {wall:+.2f}'}
    banned = []
    for key in ('FLOW', 'VWAP', 'LIQ', 'TREND', 'SURGE', 'SQUEEZE', 'WALL'):
        s = out[key]
        if s and s['side'] not in reg['allow']:
            banned.append({'key': key, 'side': s['side'], 'why': f'REGIME {reg["name"]} bans {s["side"]}: {s["why"]}'})
            out[key] = None
    out['BANNED'] = banned
    return out
