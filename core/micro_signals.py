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


USD_SCALE = {'BTCUSDT': 1.0, 'ETHUSDT': 0.5, 'SOLUSDT': 0.1, 'XRPUSDT': 0.1, 'DOGEUSDT': 0.1}


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

    def _expire(self, now: float) -> None:
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
                'trades': self.n_trades}


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
