"""Opening-range / session breakout (ORB) on Binance USD-M 1-minute klines.  Research module, offline, DEV only.

HYPOTHESIS
  Liquidity / participation regime changes at the three session anchors (00:00 UTC = daily candle + funding,
  08:00 UTC = Europe open + funding, 13:30 UTC = US cash-equity open) concentrate information arrival.  If the first
  30-60 min after an anchor define a range, a later 5m close outside that range on ABNORMAL volume (>= 1.5x the
  same-time-of-day norm) marks directional, information-driven flow that continues (intraday momentum) far enough
  to reach a 1-2x range projection before reverting to the range middle - by more than the taker-entry costs.
  Null: crypto trades 24/7 with no overnight gap / opening auction, so the session "open" carries no special
  information and the breakout's expected net return is <= 0 after VIP0 costs.

LITERATURE (from memory; titles/years as best known, not re-checked here)
  Crabel, T. (1990) Day Trading with Short Term Price Patterns and Opening Range Breakout (book; futures ORB).
  Fisher, M. (2002) The Logical Trader (ACD opening-range method).
  Blume, Easley & O'Hara (1994) Market statistics and technical analysis: the role of volume, J. Finance.
  Holmberg, Lonnbark & Lundstrom (2013) Assessing the profitability of intraday opening range breakout
      strategies, Finance Research Letters (crude-oil futures).
  Gao, Han, Li & Zhou (2018) Market intraday momentum, J. Financial Economics (first half-hour predicts last).
  Zarattini & Aziz (2023) ORB day trading on QQQ (SSRN);  Zarattini, Barbon & Aziz (2024) A profitable day
      trading strategy for the U.S. equity market (SSRN; 5-min ORB on "stocks in play", relative-volume filter).
  Eross, McGroarty, Urquhart & Wolfe (2019) The intraday dynamics of bitcoin, Res. Int. Business & Finance.
  Wen, Bouri, Xu & Zhao (2022) Intraday return predictability in the cryptocurrency markets: momentum,
      reversal, or both, North American J. Economics & Finance.
  None known that tests a session-anchored ORB with volume confirmation on crypto perpetuals NET of taker costs.

PRE-REGISTERED (written before any run; nothing below was changed after seeing results)
  sessions   every variant trades all three anchors 00:00, 08:00, 13:30 UTC, every calendar day, both sides.
  range      RH / RL = max high / min low of the L one-minute bars [anchor, anchor + L); all L bars must exist;
             mid = (RH + RL) / 2, R = RH - RL; session skipped if R < 20 bps of mid (target could not clear costs).
  signal     evaluated at each CLOSED clock-aligned 5m bar whose close time is in (anchor+L, anchor+L+120 min]:
             LONG  if 5m close > RH, SHORT if 5m close < RL, AND the bar's quote volume >= 1.5 x the mean quote
             volume of the SAME 5m time-of-day slot over the previous 20 calendar days (>= 10 of them present;
             current day excluded -> causal).  The first qualifying bar triggers; at most ONE trade per
             (symbol, session); bars while a position is open on the symbol are ignored (one position per symbol).
             Skip if the 5m close is already at / beyond the target.
  entry      taker (market) at the OPEN of the next 1m bar (which must start exactly at the 5m close): 5 + 1 bps.
  target     TP = RH + K x R (long) / RL - K x R (short), resting post-only limit, fills only when a later 1m bar
             trades THROUGH it (high > TP / low < TP): maker 2 bps.
  stop       SL = mid (stop-market, taker 5 + 1 bps); inside a 1m bar the stop is assumed hit before the target;
             a gap through the stop fills at the open; in the entry minute only the stop counts (replay rule).
  time       time barrier H minutes after entry -> taker at that minute's close.
  execution  tools/intraday_replay._exit_scan is reused unchanged (same conservative fill model as the replay).
  variants   (max 4)                 L (range min)   K (target x R)   H (time barrier, min)
             ORB30_T1_H45                 30              1.0              45     user's 15-45 min horizon
             ORB60_T1_H45                 60              1.0              45
             ORB30_T2_H240                30              2.0             240     textbook ORB, half-session
             ORB60_T2_H240                60              2.0             240
  selection  DEV 2025-01-01..2025-12-31, pooled over BTCUSDT, SOLUSDT, DOGEUSDT, ir.summarize (exit-time day);
             2024-07..2024-12 is indicator warm-up only.  DEV-best = max mean net bps with n >= 50; NONE if all
             DEV means are negative.  2026 is a holdout not computed here.
"""
from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, '/home/user/he_thong_da_tac_tu/tools')
import intraday_replay as ir  # noqa: E402

MAKER, TAKER = ir.MAKER, ir.TAKER          # 2.0, 6.0 (5 taker + 1 slippage)
ANCHORS = (0, 8 * 3600, 13 * 3600 + 1800)  # 00:00, 08:00, 13:30 UTC
P = {'min_range_bps': 20.0, 'vol_mult': 1.5, 'vol_days': 20, 'vol_min_days': 10, 'window_min': 120}
VARIANTS = {
    'ORB30_T1_H45': {'L': 30, 'K': 1.0, 'H': 45},
    'ORB60_T1_H45': {'L': 60, 'K': 1.0, 'H': 45},
    'ORB30_T2_H240': {'L': 30, 'K': 2.0, 'H': 240},
    'ORB60_T2_H240': {'L': 60, 'K': 2.0, 'H': 240},
}

_CACHE: dict = {}


def _prep(m: dict) -> dict:
    """5m bars + quote volume + causal same-slot 20-day volume baseline (cached per data object)."""
    key = (id(m), len(m['t']), float(m['t'][0]), float(m['t'][-1]))
    if key in _CACHE:
        return _CACHE[key]
    import pandas as pd
    b5 = ir.resample(m, 300)
    il = b5['i_last']
    cs = np.r_[0.0, np.cumsum(m['qv'])]
    qv5 = cs[il + 1] - cs[il + 1 - 5]                  # complete 5m bars = 5 consecutive 1m rows ending at il
    d = (b5['t'] // 86400).astype(np.int64)
    slot = ((b5['t'] % 86400) // 300).astype(np.int64)
    d0 = d.min()
    mat = np.full((d.max() - d0 + 1, 288), np.nan)
    mat[d - d0, slot] = qv5
    base = (pd.DataFrame(mat).shift(1)
            .rolling(P['vol_days'], min_periods=P['vol_min_days']).mean().to_numpy())
    out = {'b5': b5, 'qv5': qv5, 'base5': base[d - d0, slot]}
    _CACHE.clear()
    _CACHE[key] = out
    return out


def simulate(m: dict, name: str) -> list:
    """All sessions in the data (no date filter) -> list of trade dicts (t = exit-minute open time, s)."""
    v = VARIANTS[name]
    L, K, H, W = v['L'], v['K'], v['H'], P['window_min']
    f = _prep(m)
    b5, qv5, base5 = f['b5'], f['qv5'], f['base5']
    t1, o1, h1, l1 = m['t'], m['o'], m['h'], m['l']
    n1 = len(t1)
    trades = []
    busy_until = -1.0
    day_lo, day_hi = int(t1[0] // 86400), int(t1[-1] // 86400)
    for day in range(day_lo, day_hi + 1):
        for a in ANCHORS:
            t0 = day * 86400 + a
            i0 = int(np.searchsorted(t1, t0))
            i1 = int(np.searchsorted(t1, t0 + L * 60))
            if i1 - i0 != L or i0 >= n1 or t1[i0] != t0:
                continue
            rh, rl = h1[i0:i1].max(), l1[i0:i1].min()
            mid, rng = (rh + rl) / 2.0, rh - rl
            if rng / mid * 1e4 < P['min_range_bps']:
                continue
            # 5m bars whose CLOSE is in (t0+L, t0+L+W]  <=>  open in [t0+L, t0+L+W-5min]
            k_lo = int(np.searchsorted(b5['t'], t0 + L * 60))
            k_hi = int(np.searchsorted(b5['t'], t0 + (L + W) * 60 - 300, side='right'))
            for k in range(k_lo, k_hi):
                tc = b5['t_close'][k]
                if tc <= busy_until:
                    continue
                c = b5['c'][k]
                if not (np.isfinite(base5[k]) and base5[k] > 0 and qv5[k] >= P['vol_mult'] * base5[k]):
                    continue
                side = 1 if c > rh else (-1 if c < rl else 0)
                if not side:
                    continue
                tp = rh + K * rng if side > 0 else rl - K * rng
                if side * (tp - c) <= 0:
                    continue
                ie = int(b5['i_last'][k]) + 1
                if ie >= n1 or t1[ie] != tc:
                    continue
                entry, sl = o1[ie], mid
                if side * (entry - sl) <= 0:                         # opened through the stop: out at once
                    px, how, ix, mk = entry, 'SL', ie, False
                else:
                    px, how, ix, mk = ir._exit_scan(m, ie, side, entry, tp, sl, hold=H)
                gross = side * (px - entry) / entry * 1e4
                net = gross - TAKER - (MAKER if mk else TAKER)
                trades.append({'t': float(t1[ix]), 't_entry': float(t1[ie]), 'side': side, 'entry': float(entry),
                               'exit_px': float(px), 'gross_bps': float(gross), 'net_bps': float(net), 'exit': how,
                               'hold_min': (t1[ix] - t1[ie]) / 60 + 1, 'session': a,
                               'range_bps': float(rng / mid * 1e4), 'tp': float(tp), 'sl': float(sl)})
                busy_until = t1[ix] + 60
                break                                                # one trade per (symbol, session)
    return trades
