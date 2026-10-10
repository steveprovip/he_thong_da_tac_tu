"""Volume-shock (liquidation-cascade proxy) reversal on Binance USD-M 1-minute klines.  Offline research, DEV only.

HYPOTHESIS
  A 1m bar with abnormal volume AND an abnormal return is, in crypto perps, often forced / one-sided taker flow
  (stop-outs, liquidation cascades).  Price-pressure theory says such non-informational flow pushes price past its
  efficient level and the overshoot partially reverts once the forced flow stops; a liquidity provider who fades it
  is paid for supplying immediacy.  Counter-hypothesis: part of the shocks are informed / news-driven and continue,
  and a resting fade order is filled exactly when the cascade keeps going (adverse selection).
  Literature: Grossman & Miller (1988, JF) liquidity provision and reversals; Campbell, Grossman & Wang (1993, QJE)
  high-volume price changes reverse more; Llorente, Michaely, Saar & Wang (2002, RFS) informed volume continues,
  hedging/liquidity volume reverses; Brunnermeier & Pedersen (2005, JF) predatory trading / forced liquidation
  overshoot; Nagel (2012, RFS) reversal profits = liquidity-provision returns, larger in stressed markets;
  Fung, Mok & Lam (2000, JBF) and Grant, Wolf & Yu (2005, JBF) intraday reversals after large moves in index futures;
  Zawadowski, Andor & Kertesz (2006, Quant. Finance) short-term reaction after extreme 1-minute price changes.
  Crypto-specific liquidation-cascade reversal at 1m with realistic maker/taker costs: none known.

PRE-REGISTERED (written before any run; nothing below was tuned on data)
  shock bar i   r_i = ln(c_i / c_{i-1});  vol_i >= 5 x mean(vol_{i-60..i-1})  AND  |r_i| >= 3 x std(r_{i-60..i-1})
                (60 previous bars, contiguous 1m data required); decided at the CLOSE of bar i, acting from i+1.
  side          fade: side = -sign(r_i)  (down shock -> BUY, up shock -> SELL).
  shock leg     P0 = c_{i-1} (pre-shock price), X = shock extreme (l_i for a down shock, h_i for an up shock),
                leg = |P0 - X|,  leg_bps = leg / P0 * 1e4.
  target        TP = X + 0.5 (P0 - X) = 50 % retracement of the leg, post-only maker (fills only when traded THROUGH).
  stop          SL = X - side * 0.25 * leg  (beyond the shock extreme), stop -> taker + 1 bp slippage.
  time          hold up to 60 minutes after the fill -> taker at that minute's close.
  guard         skip unless the TP distance from the shock close is >= 10 bps (round trip worst case is 8 bps).
  execution     identical to tools/intraday_replay.py: maker limit fills only when a later 1m bar trades THROUGH it;
                ir._exit_scan (stop before target inside a 1m bar; in the fill minute only the stop counts; gap through
                the stop fills at the open).  Fees VIP0: maker 2 bps, taker 5 + 1 bp slippage = 6 bps.
                One position (or pending entry order) per symbol at a time; a shock is only acted on if its bar index
                is after the previous trade's exit bar / the pending order's cancel bar.
  maker entry   post-only BUY (SELL) limit at the shock close c_i, live for bars i+1..i+5 (5 min); filled at the first
                bar with low < limit (high > limit); cancelled if price reaches TP first (high >= TP for a buy) or at
                the end of the window.  Entry fee 2 bps.
  taker entry   market at the open of bar i+1 (entry fee 6 bps incl. slippage); skipped if that open is already at /
                beyond the TP.
  VARIANTS (max 4):
    MK_ALL        maker entry, every shock passing the 10-bp guard.
    MK_L40        maker entry, shock leg >= 40 bps (only cascades big enough for the 50 % target to clear fees).
    TK_L40        taker entry at the next open, shock leg >= 40 bps (no fill adverse selection, pays taker).
    MK_L40_FLOW   MK_L40 + the shock bar's taker flow agrees with a forced cascade: taker-buy share of quote volume
                  <= 0.40 for a down shock, >= 0.60 for an up shock (liquidations are market orders).
  selection     DEV 2025-01-01..2025-12-31 pooled over BTCUSDT, SOLUSDT, DOGEUSDT via ir.summarize; best mean net
                bps with n >= 50, or NONE if all are negative.  2024-07..12 is warm-up only; 2026 is the holdout and
                is not computed here (the DEV run loads 2024-07..2025-12 files only; trades whose exit would fall
                past the loaded data, exit 'END', are dropped from the DEV summary).
simulate(m, name) never filters dates.
"""
from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, '/home/user/he_thong_da_tac_tu/tools')
import intraday_replay as ir  # noqa: E402

MAKER, TAKER = ir.MAKER, ir.TAKER          # 2.0, 6.0 (taker incl. 1 bp slippage)
COMMON = {'lookback': 60, 'vol_mult': 5.0, 'sig_mult': 3.0, 'retrace': 0.5, 'sl_buf': 0.25, 'hold': 60,
          'min_tp_bps': 10.0, 'window': 5}
VARIANTS = {
    'MK_ALL': {**COMMON, 'entry': 'maker', 'min_leg_bps': 0.0, 'flow': False},
    'MK_L40': {**COMMON, 'entry': 'maker', 'min_leg_bps': 40.0, 'flow': False},
    'TK_L40': {**COMMON, 'entry': 'taker', 'min_leg_bps': 40.0, 'flow': False},
    'MK_L40_FLOW': {**COMMON, 'entry': 'maker', 'min_leg_bps': 40.0, 'flow': True},
}


def shock_bars(m: dict, p: dict) -> np.ndarray:
    """Indices i of shock bars (stats from the previous `lookback` bars only, contiguous data)."""
    import pandas as pd
    t, c, v = m['t'], m['c'], m['v']
    L = p['lookback']
    r = np.r_[np.nan, np.log(c[1:] / c[:-1])]
    sig = pd.Series(r).rolling(L).std().shift(1).to_numpy()
    vm = pd.Series(v).rolling(L).mean().shift(1).to_numpy()
    contig = np.zeros(len(t), bool)
    contig[L + 1:] = (t[L + 1:] - t[:-(L + 1)]) == 60.0 * (L + 1)       # bars i-L-1 .. i all present
    with np.errstate(invalid='ignore'):
        ok = contig & np.isfinite(sig) & (sig > 0) & np.isfinite(vm) & (vm > 0) \
            & (v >= p['vol_mult'] * vm) & (np.abs(r) >= p['sig_mult'] * sig)
    return np.flatnonzero(ok)


def simulate(m: dict, name: str) -> list:
    p = VARIANTS[name]
    t, o, h, l, c, qv, tbq = m['t'], m['o'], m['h'], m['l'], m['c'], m['qv'], m['tbq']
    n = len(t)
    trades = []
    busy = -1                                          # shocks at index <= busy are ignored
    for i in shock_bars(m, p):
        if i <= busy or i + 1 >= n or t[i + 1] != t[i] + 60:
            continue
        side = -1 if c[i] > c[i - 1] else 1
        p0 = c[i - 1]
        x = l[i] if side > 0 else h[i]
        leg = side * (p0 - x)
        if leg <= 0 or leg / p0 * 1e4 < p['min_leg_bps']:
            continue
        if p['flow']:
            share = tbq[i] / qv[i] if qv[i] > 0 else np.nan
            if not np.isfinite(share) or (side > 0 and share > 0.40) or (side < 0 and share < 0.60):
                continue
        tp = x + side * p['retrace'] * leg
        sl = x - side * p['sl_buf'] * leg
        if side * (tp - c[i]) / c[i] * 1e4 < p['min_tp_bps']:
            continue
        if p['entry'] == 'maker':
            entry, fill, j = c[i], None, i
            for j in range(i + 1, min(i + 1 + p['window'], n)):
                if t[j] != t[i] + 60 * (j - i):          # data gap -> cancel
                    break
                if (side > 0 and l[j] < entry) or (side < 0 and h[j] > entry):
                    fill = j
                    break
                if (side > 0 and h[j] >= tp) or (side < 0 and l[j] <= tp):   # ran to target without us
                    break
            if fill is None:
                busy = j - 1                             # flat again at the close of bar j
                continue
            fee_in = MAKER
        else:
            fill, entry = i + 1, o[i + 1]
            if side * (tp - entry) <= 0 or side * (entry - sl) <= 0:
                continue
            fee_in = TAKER
        px, how, ix, mk = ir._exit_scan(m, fill, side, entry, tp, sl, hold=p['hold'])
        gross = side * (px - entry) / entry * 1e4
        trades.append({'t': float(t[ix]), 't_entry': float(t[fill]), 'side': int(side), 'entry': float(entry),
                       'exit_px': float(px), 'gross_bps': float(gross),
                       'net_bps': float(gross - fee_in - (MAKER if mk else TAKER)), 'exit': how,
                       'hold_min': float((t[ix] - t[fill]) / 60 + 1), 'leg_bps': float(leg / p0 * 1e4),
                       't_shock': float(t[i])})
        busy = ix
    return trades
