"""Funding-settlement window strategy family (DEV research only).  Offline; 1-minute simulation.

HYPOTHESIS
  Binance USD-M perpetuals settle funding at 00:00 / 08:00 / 16:00 UTC; only positions open AT the snapshot pay or
  receive.  When funding is positive (longs pay; typical after an up-trend, because the perp premium that drives the
  funding rate co-moves with recent returns), (a) leveraged longs who do not want to pay close shortly BEFORE the
  snapshot and (b) basis traders (long spot / short perp) add perp shorts to collect it -> net selling pressure in
  the window before settlement (price drifts AGAINST the 8h trend);  AFTER the snapshot the payers re-open and the
  funding collectors unwind -> buying pressure (price drifts WITH the 8h trend).  Mirror image for negative funding.
  Funding history is not in the data dir, so the sign of the 8h return (the funding interval) is the proxy.

LITERATURE (what I know; none tests this exact settlement-window drift)
  - He, Manela, Ross & von Wachter (2022, rev. 2024) "Fundamentals of Perpetual Futures" - funding mechanism,
    perp-spot deviations, carry returns.
  - Ackerer, Hugonnier & Jermann (2023/2024) "Perpetual Futures Pricing" - no-arbitrage pricing with funding.
  - Christin, Routledge, Soska & Zetlin-Jones (2022/2023) "The Crypto Carry Trade" - funding / basis carry.
  - Eross, McGroarty, Urquhart & Wolfe (2019) "The intraday dynamics of bitcoin" (RIBAF); Baur, Cahill, Godfrey &
    Liu (2019) "Bitcoin time-of-day, day-of-week and month-of-year effects..." (FRL) - intraday seasonality.
  - Analogues of price pressure around fixed settlement / fixing times: Stoll & Whaley (1987) "Program trading and
    expiration-day effects" (FAJ); Melvin & Prins (2015) "Equity hedging and exchange rates at the London 4 p.m.
    fix" (JFM); Evans (2018) "Forex trading and the WMR fix" (JBF).
  - Peer-reviewed evidence of a tradable crypto funding-SETTLEMENT-WINDOW drift: none known.
  Prior: any drift is likely a few bps; a maker-entry / taker-exit round trip costs 8 bps -> expect NO EDGE.

PRE-REGISTERED VARIANTS (written before any run; at most 4; nothing tuned afterwards)
  settlement T in {00:00, 08:00, 16:00 UTC} (every multiple of 28 800 s);  trend r8 = close(decision bar) /
  close(bar 480 min earlier) - 1;  trend_sign = sign(r8) (r8 == 0 -> no trade);  side = dir * trend_sign.
    PRE60_FADE    decision bar closes at T-60 min;  side = -trend_sign;  time exit at T-1 min.
    PRE15_FADE    decision bar closes at T-15 min;  side = -trend_sign;  time exit at T-1 min.
    POST15_TREND  decision bar closes at T;         side = +trend_sign;  time exit at T+15 min.
    POST60_TREND  decision bar closes at T;         side = +trend_sign;  time exit at T+60 min.
  No position ever spans a settlement snapshot (PRE exits 60 s before T; POST orders are placed after T), so the
  funding cash flow of every trade is exactly zero, not an unknown.
EXECUTION (same conservative model as tools/intraday_replay.py)
  entry  post-only limit at the decision bar's close, live for the next 2 one-minute bars; fills only when a later
         bar trades THROUGH it (BUY: low < limit, SELL: high > limit); unfilled -> no trade.
  stop   protective SL = 3.0 x ATR14(5m) (last 5m bar closed at the decision time) from the entry, taker;
         stop checked before anything else inside each 1m bar; in the fill minute only the stop counts (at the SL
         price); a later gap through the stop fills at the open.  No take-profit (drift hypothesis).
  exit   time barrier: taker at the close of the 1m bar whose close is the exit time.
  fees   maker 2 bps entry; taker 5 + 1 bp slippage exit (ir.MAKER, ir.TAKER).  One position per symbol at a time
         (windows are 8 h apart and never overlap).
  select DEV 2025-01-01..2025-12-31 pooled over BTC/SOL/DOGE; DEV-best = max mean net bps with n >= 50, else NONE.
         2024-07..2024-12 = warm-up only;  2026 = holdout, not computed here.
"""
from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, '/home/user/he_thong_da_tac_tu/tools')
import intraday_replay as ir  # noqa: E402

SETTLE = 28800                       # 8 h; 00/08/16 UTC are its multiples since the epoch
P = {'trend_min': 480, 'entry_live': 2, 'sl_atr': 3.0, 'z_win': 1440}
VARIANTS = {
    'PRE60_FADE':   {'phase': 'PRE',  'win': 60, 'dir': -1},
    'PRE15_FADE':   {'phase': 'PRE',  'win': 15, 'dir': -1},
    'POST15_TREND': {'phase': 'POST', 'win': 15, 'dir': +1},
    'POST60_TREND': {'phase': 'POST', 'win': 60, 'dir': +1},
}
_CACHE: dict = {}


def _prep(m):
    key = id(m)
    if key in _CACHE:
        return _CACHE[key]
    b5 = ir.resample(m, 300)
    atr5 = ir.wilder_atr(b5['h'], b5['l'], b5['c'])
    # descriptive only (not used by any rule): sigma of 1m log returns over the previous 1440 bars
    lr = np.r_[0.0, np.diff(np.log(m['c']))]
    cs, cs2 = np.r_[0.0, np.cumsum(lr)], np.r_[0.0, np.cumsum(lr * lr)]
    i = np.arange(len(lr))
    lo = np.maximum(i + 1 - P['z_win'], 0)
    nn = i + 1 - lo
    mu = (cs[i + 1] - cs[lo]) / nn
    sig = np.sqrt(np.maximum((cs2[i + 1] - cs2[lo]) / nn - mu * mu, 0.0))
    out = (b5, atr5, sig)
    _CACHE.clear()
    _CACHE[key] = out
    return out


def _idx(t, ts):
    """index of the 1m bar opening at ts, or -1 if missing."""
    j = int(np.searchsorted(t, ts))
    return j if j < len(t) and t[j] == ts else -1


def _exit_scan(m, i0, side, sl, t_exit):
    """stop first (fill minute: stop only, at the SL price), then the clock time barrier at bar close >= t_exit."""
    t, o, h, l, c = m['t'], m['o'], m['h'], m['l'], m['c']
    n = len(t)
    i = i0
    while i < n:
        first = i == i0
        if side > 0 and l[i] <= sl:
            return (sl if first else min(o[i], sl)), 'SL', i
        if side < 0 and h[i] >= sl:
            return (sl if first else max(o[i], sl)), 'SL', i
        if t[i] + 60 >= t_exit:
            return c[i], 'TIME', i
        i += 1
    return c[n - 1], 'END', n - 1


def simulate(m, name):
    v = VARIANTS[name]
    b5, atr5, sig = _prep(m)
    t, h, l, c = m['t'], m['h'], m['l'], m['c']
    first_T = (int(t[0]) // SETTLE + 1) * SETTLE
    trades = []
    for T in range(first_T, int(t[-1]) + 1, SETTLE):
        if v['phase'] == 'PRE':
            dec_close = T - v['win'] * 60
            t_exit = T - 60
        else:
            dec_close = T
            t_exit = T + v['win'] * 60
        i_dec = _idx(t, dec_close - 60)
        i_ref = _idx(t, dec_close - 60 - P['trend_min'] * 60)
        if i_dec < 0 or i_ref < 0:
            continue
        r8 = c[i_dec] / c[i_ref] - 1.0
        if r8 == 0:
            continue
        side = v['dir'] * (1 if r8 > 0 else -1)
        k5 = int(np.searchsorted(b5['t_close'], dec_close, side='right')) - 1
        if k5 < 0:
            continue
        atr = atr5[k5]
        if not np.isfinite(atr) or atr <= 0:
            continue
        entry = c[i_dec]
        sl = entry - side * P['sl_atr'] * atr
        fill_i = -1
        for j in range(1, P['entry_live'] + 1):
            i = i_dec + j
            if i >= len(t) or t[i] != dec_close + (j - 1) * 60:
                break
            if (side > 0 and l[i] < entry) or (side < 0 and h[i] > entry):
                fill_i = i
                break
        if fill_i < 0:
            continue
        px, how, ix = _exit_scan(m, fill_i, side, sl, t_exit)
        gross = side * (px - entry) / entry * 1e4
        net = gross - ir.MAKER - ir.TAKER
        z = r8 / (sig[i_dec] * np.sqrt(P['trend_min'])) if sig[i_dec] > 0 else np.nan
        trades.append({'t': float(t[ix]), 't_entry': float(t[fill_i]), 'side': side, 'entry': float(entry),
                       'exit_px': float(px), 'gross_bps': float(gross), 'net_bps': float(net), 'exit': how,
                       'hold_min': (t[ix] - t[fill_i]) / 60 + 1, 'settle': T, 'hour': (T % 86400) // 3600,
                       'r8_bps': float(r8 * 1e4), 'z8': float(z)})
    return trades
