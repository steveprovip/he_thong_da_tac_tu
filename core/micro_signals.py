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
    ker_lock: float = 0.38              # KER < 0.38 -> TREND lane locked, MR lane may scan
    ker_unlock: float = 0.42            # TREND lane unlocks at KER >= 0.42 (band 0.38-0.42; was 0.40/0.48)
    mr_ker_max: float = 0.38            # MR lane only while KER < 0.38 (zig-zag tape)
    mr_dev_pct: float = 0.12            # price >= 0.12 % away from VWAP 60 s ...
    mr_box_pct: float = 0.40            # ... inside a 60 s range <= 0.40 % ...
    mr_extreme_frac: float = 0.20       # ... at the outer 20 % of that range ...
    mr_depth_imb: float = 0.25          # ... and L10 depth leaning >= 0.25 the reversion way (TIDAL vote)
    absorb_window_s: float = 15.0       # price-delta concordance window
    absorb_usd: float = 500_000.0       # |CVD 15 s| >= $500k (x scale) ...
    absorb_min_move_pct: float = 0.025  # ... must move price >= 0.025 % the same way, else ABSORPTION TRAP
    macro_vwap_s: float = 300.0         # rolling 5 m VWAP
    macro_vwap_min_s: float = 240.0     # VWAP 5 m needs >= 4 min of prints
    macro_candle_s: float = 300.0       # 5 m candles for the EMA
    macro_ema_n: int = 20               # EMA-20 of 5 m closes (seeded from REST klines at start)
    macro_slope_pct: float = 0.05       # one-side lock only when EMA-20 moves >= 0.05 % per 5 m candle
    ofi_levels: int = 10                # multi-level OFI (Cont-Kukanov-Stoikov 2014 per level, summed over L10)
    ofi_window_s: float = 10.0          # OFI_norm = sum(e_t) / sum(|e_t|) over 10 s of depth10@100ms frames
    ofi_min: float = 0.65               # TIDAL OFI vote needs |OFI_norm| >= 0.65 AND CVD 15 s >= absorb_usd same way
    ofi_min_events: int = 5             # fewer book updates in the window -> OFI undefined (one frame is noise)
    conc_cvd60_usd: float = 300_000.0   # triple concordance: CVD 15 s, CVD 60 s >= $300k (x scale), OFI >= 0.35
    conc_ofi: float = 0.35
    vpin_bucket_usd: float = 1_000_000.0  # VPIN volume bucket (x usd_scale), exact aggressor side from aggTrade
    vpin_buckets: int = 10
    vpin_max: float = 0.80              # VPIN > 0.80 -> toxic: only entries AGAINST the toxic flow are vetoed
    bar_s: float = 60.0                 # 1 m OHLC bars for ATR / Yang-Zhang (seeded from REST 1 m klines)
    atr_n: int = 14
    atr_min_bars: int = 5               # fewer closed bars -> ATR unknown -> static brackets
    bars_keep: int = 240                # 4 h of closed 1 m bars: ATR / YZ use the last 120, ATR 15 m all of them
    atr15_s: float = 900.0              # 15 m bars aggregated from the 1 m bars (VFR volatility horizon)
    atr15_min_bars: int = 5             # fewer complete 15 m bars -> estimate sqrt(15) x ATR 1 m (src EST)
    sigma_window_s: float = 60.0        # rolling 1 m realised sigma (bps / sqrt(s)) for Avellaneda-Stoikov


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


def concordance(f: dict, cfg: 'SignalConfig' = None) -> dict:
    """Triple flow concordance. BUY needs CVD 15 s > 0, CVD 60 s >= +$300k (x scale) and OFI L10 10 s >= +0.35;
    SELL the mirror. -> {'state': BUY|SELL|CONFLICT|UNKNOWN, 'buy_ok', 'sell_ok', 'why'}"""
    cfg = cfg or SignalConfig()
    c15, c60, ofi = f.get('cvd_15s'), f.get('cvd_60s'), f.get('ofi_10s')
    lim = cfg.conc_cvd60_usd * cfg.usd_scale
    buy = c15 is not None and c60 is not None and ofi is not None and c15 > 0 and c60 >= lim and ofi >= cfg.conc_ofi
    sell = c15 is not None and c60 is not None and ofi is not None and c15 < 0 and c60 <= -lim and ofi <= -cfg.conc_ofi
    why = (f'CVD15 {"—" if c15 is None else f"{c15 / 1e6:+.2f}M"} · CVD60 {"—" if c60 is None else f"{c60 / 1e6:+.2f}M"}'
           f' · OFI {"—" if ofi is None else f"{ofi:+.2f}"}')
    state = 'BUY' if buy else 'SELL' if sell else 'UNKNOWN' if None in (c15, c60, ofi) else 'CONFLICT'
    return {'state': state, 'buy_ok': buy, 'sell_ok': sell, 'why': why}


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
    span, ema = f.get('vwap_5m_span') or 0.0, f.get('ema5_20')
    if (not px or vw is None or slope is None or not ema or span < cfg.macro_vwap_min_s
            or n < cfg.macro_ema_n):
        return {'name': 'WARMUP', 'allow': ('BUY', 'SELL'),
                'why': f'VWAP5m {span:.0f}/{cfg.macro_vwap_min_s:.0f}s · EMA {n}/{cfg.macro_ema_n} nến'}
    sp = slope / ema * 100
    why = f'giá {(px / vw - 1) * 100:+.3f}% vs VWAP5m · EMA20 dốc {sp:+.3f}%/5m (khoá khi ≥ {cfg.macro_slope_pct}%)'
    if px > vw and sp >= cfg.macro_slope_pct:
        return {'name': 'LONG ONLY', 'allow': ('BUY',), 'why': why}
    if px < vw and sp <= -cfg.macro_slope_pct:
        return {'name': 'SHORT ONLY', 'allow': ('SELL',), 'why': why}
    return {'name': 'MIXED', 'allow': ('BUY', 'SELL'), 'why': why}


def mlofi(prev_b, prev_a, cur_b, cur_a, levels: int = 10) -> float:
    """Multi-level order flow imbalance of one book update (base-asset units, + = bid pressure).
    Per level l (Cont, Kukanov & Stoikov 2014):
      e_l = q_b,l * 1[Pb_l >= Pb_l'] - q_b,l' * 1[Pb_l <= Pb_l'] - q_a,l * 1[Pa_l <= Pa_l'] + q_a,l' * 1[Pa_l >= Pa_l']
    ( ' = previous snapshot ), summed over l = 1..L (equal weights)."""
    e = 0.0
    for l in range(min(levels, len(prev_b), len(cur_b))):
        (pb0, qb0), (pb1, qb1) = prev_b[l], cur_b[l]
        e += (qb1 if pb1 >= pb0 else 0.0) - (qb0 if pb1 <= pb0 else 0.0)
    for l in range(min(levels, len(prev_a), len(cur_a))):
        (pa0, qa0), (pa1, qa1) = prev_a[l], cur_a[l]
        e -= (qa1 if pa1 <= pa0 else 0.0) - (qa0 if pa1 >= pa0 else 0.0)
    return e


def atr(bars, n: int = 14) -> Optional[float]:
    """Wilder ATR (price units) over closed bars (start, o, h, l, c); TR_1 = h - l."""
    if not bars:
        return None
    trs, pc = [], None
    for _, o, h, l, c in bars:
        trs.append(h - l if pc is None else max(h - l, abs(h - pc), abs(l - pc)))
        pc = c
    a = sum(trs[:n]) / len(trs[:n])
    for tr in trs[n:]:
        a = (a * (n - 1) + tr) / n
    return a


def bars_15m(bars, span: float = 900.0, min_fill: int = 12) -> list:
    """Aggregate closed 1 m bars (start, o, h, l, c) into complete span-second bars aligned on the clock.
    A bucket counts only with >= min_fill of its 15 one-minute bars (gaps, and the bucket still forming, drop)."""
    out = {}
    for b in sorted(bars):
        k = math.floor(b[0] / span) * span
        if k not in out:
            out[k] = [k, b[1], b[2], b[3], b[4], 1]
        else:
            x = out[k]
            x[2], x[3], x[4], x[5] = max(x[2], b[2]), min(x[3], b[3]), b[4], x[5] + 1
    return [tuple(x[:5]) for k, x in sorted(out.items()) if x[5] >= min_fill]


def vfr(atr_pct: Optional[float], fee_bps: float, spread_bps: float = 0.0) -> Optional[float]:
    """Volatility-to-Fee Ratio: ATR (bps) / (round-trip fee + spread, bps). < 3 -> the move cannot pay the toll."""
    if atr_pct is None or fee_bps + spread_bps <= 0:
        return None
    return atr_pct * 100.0 / (fee_bps + max(0.0, spread_bps))


def yang_zhang(bars) -> Optional[float]:
    """Yang-Zhang (2000) per-bar volatility (fraction): s2 = s2_open + k s2_close + (1-k) s2_RS,
    k = 0.34 / (1.34 + (n+1)/(n-1)); open jump = ln(O_i / C_i-1)."""
    o_r, c_r, rs = [], [], []
    for prev, cur in zip(bars, list(bars)[1:]):
        _, o, h, l, c = cur
        pc = prev[4]
        if min(o, h, l, c, pc) <= 0:
            continue
        o_r.append(math.log(o / pc))
        c_r.append(math.log(c / o))
        rs.append(math.log(h / c) * math.log(h / o) + math.log(l / c) * math.log(l / o))
    n = len(o_r)
    if n < 2:
        return None

    def var(xs):
        m = sum(xs) / len(xs)
        return sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    k = 0.34 / (1.34 + (n + 1) / (n - 1))
    return math.sqrt(max(var(o_r) + k * var(c_r) + (1 - k) * sum(rs) / n, 0.0))


def realized_sigma_bps(points, now: float, window_s: float = 60.0) -> Optional[float]:
    """Rolling realised volatility, bps per sqrt(second), from the last print of each second."""
    last = {}
    for t, px in points:
        if 0 <= now - t <= window_s and px:
            last[int(t)] = px
    secs = sorted(last)
    if len(secs) < 10:
        return None
    acc = sum((math.log(last[b] / last[a]) * 1e4) ** 2 / (b - a) for a, b in zip(secs, secs[1:]))
    return math.sqrt(acc / (len(secs) - 1))


def as_quote(mid: float, q: float, sigma_bps: float, gamma: float, k: float, tau_s: float,
             max_offset_bps: Optional[float] = None) -> dict:
    """Avellaneda-Stoikov (2008), in bps of mid:
      reservation r = s - q * gamma * sigma^2 * tau ; total spread = gamma * sigma^2 * tau + (2/gamma) ln(1 + gamma/k)
    q = signed inventory in [-1, 1] (0 = flat), sigma in bps/sqrt(s), gamma and k in 1/bps.
    max_offset_bps caps |quote - mid| so a violent tape cannot push the quote out of reach."""
    shift = q * gamma * sigma_bps ** 2 * tau_s
    spread = gamma * sigma_bps ** 2 * tau_s + (2.0 / gamma) * math.log(1.0 + gamma / k)
    half = spread / 2.0
    bid_off, ask_off = shift + half, half - shift          # bps below / above mid
    if max_offset_bps is not None:
        bid_off, ask_off = min(bid_off, max_offset_bps), min(ask_off, max_offset_bps)
    return {'reservation': mid * (1 - shift / 1e4), 'half_spread_bps': half, 'shift_bps': shift,
            'bid': mid * (1 - bid_off / 1e4), 'ask': mid * (1 + ask_off / 1e4)}


def toxicity(f: dict, cfg: 'SignalConfig' = None) -> dict:
    """VPIN gate (Easley, Lopez de Prado & O'Hara 2012) on exact aggressor-signed volume buckets."""
    cfg = cfg or SignalConfig()
    v, side = f.get('vpin'), f.get('vpin_side')
    toxic = v is not None and v > cfg.vpin_max
    return {'vpin': v, 'buckets': f.get('vpin_n', 0), 'max': cfg.vpin_max, 'toxic': toxic,
            'side': side if toxic else None,
            'blocks': ({'BUY': 'SELL', 'SELL': 'BUY'}.get(side) if toxic else None),   # only AGAINST the flow
            'state': 'WARMUP' if v is None else (f'TOXIC {side}' if toxic else 'CLEAN')}


VPIN_BUCKET_USD = {'BTCUSDT': 2_500_000.0, 'SOLUSDT': 250_000.0, 'DOGEUSDT': 200_000.0}   # per-symbol bucket size


# Asset-specific KER gates (2026-10-07o, ledger 34 trades: BTC 27 lệnh net -1.45 U, SOL 7 lệnh net +0.23 U).
# BTC needs a cleaner tape (KER >= 0.55) before the TREND lane opens; its MR lane is off (mr_ker_max 0).
# SOL/DOGE keep the 0.38 lock / 0.42 unlock band and the MR lane below 0.38.
KER_GATES = {'BTCUSDT': {'ker_lock': 0.55, 'ker_unlock': 0.55, 'mr_ker_max': 0.0},
             'SOLUSDT': {'ker_lock': 0.38, 'ker_unlock': 0.42, 'mr_ker_max': 0.38},
             'DOGEUSDT': {'ker_lock': 0.38, 'ker_unlock': 0.42, 'mr_ker_max': 0.38}}


def config_for(symbol: str) -> SignalConfig:
    sym, scale = symbol.upper(), USD_SCALE.get(symbol.upper(), 0.1)
    vb = VPIN_BUCKET_USD.get(sym)
    return SignalConfig(usd_scale=scale, **({'vpin_bucket_usd': vb / scale} if vb else {}),
                        **KER_GATES.get(sym, {}))


def streams_multi(symbols) -> dict:
    """One public + one market socket for several symbols (combined streams, demuxed by stream prefix)."""
    ss = [s.lower() for s in symbols]
    return {'public': WS_PUBLIC + '/'.join(f'{s}@depth10@100ms' for s in ss),
            'market': WS_MARKET + '/'.join(f'{s}@aggTrade/{s}@markPrice@1s/{s}@forceOrder' for s in ss)}


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
        self.book_b: list = []                                  # last depth10 levels [(px, qty)] for MLOFI
        self.book_a: list = []
        self.ofi: Deque[Tuple[float, float]] = deque()          # (t, e_t) multi-level OFI per book update
        self.vb_buy = self.vb_sell = 0.0                        # VPIN: open volume bucket (USD)
        self.vb_closed: Deque[float] = deque(maxlen=max(cfg.vpin_buckets * 3, 30))   # (buy - sell) / V per bucket
        self.bar: Optional[list] = None                         # open 1 m bar [start, o, h, l, c]
        self.bars: Deque[tuple] = deque(maxlen=cfg.bars_keep)   # closed 1 m bars (4 h: ATR 15 m)

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
            self._bucket(px * q, not d.get('m'))
            self._bar(now, px)
            self._expire(now)
        elif '@depth' in stream:
            self.flow.on_depth(d, now)
            try:
                self.bid, self.bid_qty = float(d['b'][0][0]), float(d['b'][0][1])
                self.ask, self.ask_qty = float(d['a'][0][0]), float(d['a'][0][1])
                self.bid10 = sum(float(q) for _, q in d['b'][:10])
                self.ask10 = sum(float(q) for _, q in d['a'][:10])
                self.last_book = now
                lv = self.cfg.ofi_levels
                cb = [(float(x), float(q)) for x, q in d['b'][:lv]]
                ca = [(float(x), float(q)) for x, q in d['a'][:lv]]
                if self.book_b and self.book_a:
                    e = mlofi(self.book_b, self.book_a, cb, ca, lv)
                    if e:
                        self.ofi.append((now, e))
                self.book_b, self.book_a = cb, ca
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

    def _bucket(self, usd: float, is_buy: bool) -> None:
        size = self.cfg.vpin_bucket_usd * self.cfg.usd_scale
        if size <= 0:
            return
        while usd > 1e-9:
            take = min(usd, size - self.vb_buy - self.vb_sell)
            if is_buy:
                self.vb_buy += take
            else:
                self.vb_sell += take
            usd -= take
            if self.vb_buy + self.vb_sell >= size - 1e-6:
                self.vb_closed.append((self.vb_buy - self.vb_sell) / size)            # signed: keeps the side
                self.vb_buy = self.vb_sell = 0.0

    def vpin_now(self) -> Optional[float]:
        n = self.cfg.vpin_buckets
        if len(self.vb_closed) < n:
            return None
        return sum(abs(x) for x in list(self.vb_closed)[-n:]) / n

    def vpin_side(self) -> Optional[str]:
        """Direction of the toxic flow: sign of the net imbalance over the VPIN buckets."""
        n = self.cfg.vpin_buckets
        if len(self.vb_closed) < n:
            return None
        net = sum(list(self.vb_closed)[-n:])
        return 'BUY' if net > 0 else 'SELL' if net < 0 else None

    def _bar(self, now: float, px: float) -> None:
        b = math.floor(now / self.cfg.bar_s) * self.cfg.bar_s
        if self.bar is None or b > self.bar[0]:
            if self.bar is not None:
                self.bars.append(tuple(self.bar))
            self.bar = [b, px, px, px, px]
        elif b == self.bar[0]:
            self.bar[2], self.bar[3], self.bar[4] = max(self.bar[2], px), min(self.bar[3], px), px

    def seed_bars(self, rows, now: Optional[float] = None) -> int:
        """Merge closed 1 m bars [(open_s, o, h, l, c)] (REST klines) under the live ones."""
        now = time.time() if now is None else now
        have = {b[0] for b in self.bars}
        new = [tuple(map(float, r)) for r in rows if r[0] + self.cfg.bar_s <= now and r[0] not in have
               and (self.bar is None or r[0] < self.bar[0])]
        k = self.cfg.bars_keep
        self.bars = deque(sorted(list(self.bars) + new)[-k:], maxlen=k)
        return len(new)

    def _expire(self, now: float) -> None:
        while self.ofi and now - self.ofi[0][0] > self.cfg.ofi_window_s:
            self.ofi.popleft()
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

    def _quant(self, now: float) -> dict:
        """OFI 10 s, VPIN, ATR / Yang-Zhang on closed 1 m bars, rolling 1 m sigma."""
        ev = [e for t, e in self.ofi if now - t <= self.cfg.ofi_window_s]
        tot = sum(abs(e) for e in ev)
        allb = list(self.bars)
        bars = allb[-120:]                                     # ATR / YZ window unchanged (2 h of 1 m bars)
        a = atr(bars, self.cfg.atr_n) if len(bars) >= self.cfg.atr_min_bars else None
        yz = yang_zhang(bars) if len(bars) >= self.cfg.atr_min_bars else None
        px = self.last or self.price()
        b15 = bars_15m(allb, self.cfg.atr15_s)
        a15, src15 = None, None
        if len(b15) >= self.cfg.atr15_min_bars and px:
            a15, src15 = atr(b15, self.cfg.atr_n) / px * 100, '15m'
        elif a and px:
            a15, src15 = a / px * 100 * math.sqrt(self.cfg.atr15_s / self.cfg.bar_s), 'EST'   # random-walk scaling
        ok = tot > 0 and len(ev) >= self.cfg.ofi_min_events
        return {'ofi_10s': sum(ev) / tot if ok else None, 'ofi_qty_10s': sum(ev), 'ofi_n': len(ev),
                'vpin': self.vpin_now(), 'vpin_n': len(self.vb_closed), 'vpin_side': self.vpin_side(),
                'atr_1m': a, 'atr_pct': a / px * 100 if (a and px) else None,
                'yz_pct': yz * 100 if yz is not None else None, 'bars_n': len(bars),
                'atr15_pct': a15, 'atr15_src': src15, 'bars15_n': len(b15),
                'sigma_bps': realized_sigma_bps(((t, x) for t, _, x in self.cvd_long), now, self.cfg.sigma_window_s)}

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
                **self._quant(now),
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
    out = {'FLOW': None, 'VWAP': None, 'LIQ': None, 'TREND': None, 'SURGE': None, 'SQUEEZE': None, 'WALL': None,
           'MR': None, 'DEPTH': None, 'OFI': None}
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
    of, c15 = f.get('ofi_10s'), f.get('cvd_15s')
    if (of is not None and c15 is not None and abs(of) >= cfg.ofi_min and abs(c15) >= cfg.absorb_usd * k
            and (of > 0) == (c15 > 0)):
        x = min(abs(of) / cfg.ofi_min, abs(c15) / (cfg.absorb_usd * k))
        out['OFI'] = {'side': 'BUY' if of > 0 else 'SELL', 'strength': strength(x), 'edge_bps': min(10.0, 6.0 * x),
                      'why': f'OFI L{cfg.ofi_levels} {of:+.2f} · CVD 15s {c15 / 1e6:+.2f}M (liquidity sweep)'}
    wall = f.get('depth10_imb')
    if wall is not None and abs(wall) >= cfg.wall_min:
        out['WALL'] = {'side': 'BUY' if wall > 0 else 'SELL', 'strength': min(1.0, abs(wall)), 'edge_bps': 0.0,
                       'why': f'L10 wall {wall:+.2f}'}
    k_, lo60, hi60 = f.get('ker'), f.get('range_lo'), f.get('range_hi')
    rng = (hi60 - lo60) / lo60 * 100 if (lo60 and hi60) else None
    mr_edge = pos is not None and (pos <= cfg.mr_extreme_frac if (dev or 0) < 0 else pos >= 1 - cfg.mr_extreme_frac)
    if (k_ is not None and k_ < cfg.mr_ker_max and dev is not None and abs(dev) >= cfg.mr_dev_pct and mr_edge
            and rng is not None and rng <= cfg.mr_box_pct):
        side = 'SELL' if dev > 0 else 'BUY'
        out['MR'] = {'side': side, 'strength': strength(abs(dev) / cfg.mr_dev_pct), 'edge_bps': min(20.0, abs(dev) * 100),
                     'why': f'MR KER {k_:.2f} · VWAP {dev:+.3f}% · box {rng:.2f}% @ {pos * 100:.0f}%'}
        lean = (wall or 0.0) * (1 if side == 'BUY' else -1)
        if wall is not None and lean >= cfg.mr_depth_imb:
            out['DEPTH'] = {'side': side, 'strength': strength(lean / cfg.mr_depth_imb), 'edge_bps': 0.0,
                            'why': f'L10 depth {wall:+.2f} backs the reversion'}
    banned = []
    for key in ('FLOW', 'VWAP', 'LIQ', 'TREND', 'SURGE', 'SQUEEZE', 'WALL', 'MR', 'DEPTH', 'OFI'):
        s = out[key]
        if s and s['side'] not in reg['allow']:
            banned.append({'key': key, 'side': s['side'], 'why': f'REGIME {reg["name"]} bans {s["side"]}: {s["why"]}'})
            out[key] = None
    out['BANNED'] = banned
    return out
