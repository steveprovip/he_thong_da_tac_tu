"""HTF range mean reversion: 1-day Bollinger / VWAP bands on 15m decisions, ADX(1h) < 20 regime gate.  Offline research.

HYPOTHESIS
  When the 1h trend strength is low (ADX14 on 1h bars < 20, i.e. a range regime), a 15m-scale excursion of 2-3
  standard deviations away from the 1-day mean is more likely to revert toward that mean within a few hours than
  to continue.  Using 1-day bands (instead of the 100-minute / 5m bands of tools/intraday_replay.py S1) makes the
  target distance (band -> mid) typically 60-200 bps, i.e. 10-30x the VIP0 round-trip cost (4-8 bps), so a small
  directional edge is not eaten by fees; the 4 h horizon gives the reversion time to play out.
  Null: the band touch carries no information about the next 4 h (gross ~ 0, net ~ -cost).

LITERATURE (what I know; none tests exactly this rule on crypto perps with maker fills)
  Bollinger, J. (2001) "Bollinger on Bollinger Bands" (band construction; author himself warns tags are not signals).
  Wilder, J.W. (1978) "New Concepts in Technical Trading Systems" (ATR, ADX; ADX < 20 as non-trending convention).
  Lehmann, B. (1990) QJE "Fads, martingales and market efficiency"; Jegadeesh, N. (1990) JF; Lo & MacKinlay (1990)
    RFS - short-horizon reversal in equities (weekly/monthly, largely microstructure/liquidity provision).
  Avellaneda, M. & Lee, J.-H. (2010) Quant. Finance "Statistical arbitrage in the US equities market" (mean-reversion
    of residuals; edge decayed after costs over time).
  Brock, Lakonishok & LeBaron (1992) JF "Simple technical trading rules..." (MA / range-break rules, pre-cost).
  Crypto, intraday: Shen, Urquhart & Wang (2022, Financial Review) "Bitcoin intraday time series momentum" and
    Wen, Bouri, Xu & Zhao (2022, NAJEF) "Intraday return predictability in the cryptocurrency markets: momentum,
    reversal, or both" - mixed: momentum dominates at some horizons, reversal around jumps / at others.
  Corbet & Katsiampa (2020, IRFA) "Asymmetric mean reversion of Bitcoin price returns" (daily returns).
  Specific "ADX(1h)<20 + 1-day 2-3 sigma band + mid target" on crypto 15m with realistic maker fills: none known.

PRE-REGISTERED (written before the first run; nothing below was tuned on any result)
  bars       15m and 1h = complete clock-aligned groups of 1m bars (ir.resample). Decisions only at 15m CLOSES,
             acting from the next 1m bar; every indicator value is the one known at that 15m close.
  regime     ADX14 (Wilder, ir.wilder_adx) on 1h bars, last COMPLETED 1h bar at the decision time; trade only if
             ADX < 20.
  bands      BB: mid = SMA of the last 96 15m closes (1 day), sigma = population std of those 96 closes.
             VW: mid = rolling 1-day VWAP of the 1m typical price (last 1440 1m bars), sigma = volume-weighted std
                 of the 1m typical price around it (same construction as ir.vwap_bands ROLL, window 1440).
             lower = mid - K*sigma, upper = mid + K*sigma.
  entry      flat + regime ok -> at the 15m close rest a post-only BUY at min(lower, close) and a post-only SELL at
             max(upper, close) (GTX cannot cross: if the close is already beyond the band the order rests at the
             close).  The orders live for the next 15 minutes and are re-anchored at the next 15m close.  A resting
             order fills only when a later 1m bar trades THROUGH it (low < buy px / high > sell px).  If both sides
             would fill in the same minute -> no trade.  Skip a side whose target distance |mid - entry| < 20 bps.
  exits      TP = post-only at the mid of the decision bar (fixed, not re-anchored);  SL = entry -/+ SLK x ATR14
             (Wilder, 15m bars, value at the decision close), stop = taker;  time barrier 240 min -> taker at that
             minute's close.  Exit scan = ir._exit_scan: stop before target inside a 1m bar, in the fill minute only
             the stop counts, gap through the stop fills at the open.
  costs      maker 2 bps, taker 5 + 1 bp slippage (ir.MAKER / ir.TAKER).  Funding not modelled (same as the replay).
  position   one per symbol; after an exit the next decision is the first 15m close after the exit minute.
  VARIANTS   (at most 4, one-at-a-time from a base)
             BB2_SL2   BB, K = 2.0, SLK = 2.0   (base)
             BB3_SL2   BB, K = 3.0, SLK = 2.0   (wider band)
             BB2_SL15  BB, K = 2.0, SLK = 1.5   (tighter stop)
             VW2_SL2   VW, K = 2.0, SLK = 2.0   (VWAP instead of SMA bands)
  selection  DEV 2025-01-01..2025-12-31 pooled over BTCUSDT, SOLUSDT, DOGEUSDT (ir.summarize, by exit time);
             DEV-best = highest mean net bps with n >= 50, or NONE if every variant's mean net is negative.
             2024-07..2024-12 is indicator warm-up only; 2026 is a holdout not computed / looked at here.
"""
from __future__ import annotations

import sys

import numpy as np

sys.path.insert(0, '/home/user/he_thong_da_tac_tu/tools')
import intraday_replay as ir  # noqa: E402

VARIANTS = {
    'BB2_SL2': {'band': 'BB', 'k': 2.0, 'slk': 2.0},
    'BB3_SL2': {'band': 'BB', 'k': 3.0, 'slk': 2.0},
    'BB2_SL15': {'band': 'BB', 'k': 2.0, 'slk': 1.5},
    'VW2_SL2': {'band': 'VW', 'k': 2.0, 'slk': 2.0},
}
COMMON = {'bb_n': 96, 'vw_n': 1440, 'adx_max': 20.0, 'atr_n': 14, 'adx_n': 14, 'hold_min': 240,
          'order_life_s': 900, 'min_target_bps': 20.0}

_CACHE: dict = {}


def _rolling_mean_std(x, n):
    cs, cs2 = np.r_[0.0, np.cumsum(x)], np.r_[0.0, np.cumsum(x * x)]
    out_m, out_s = np.full(len(x), np.nan), np.full(len(x), np.nan)
    if len(x) < n:
        return out_m, out_s
    s, s2 = cs[n:] - cs[:-n], cs2[n:] - cs2[:-n]
    mu = s / n
    out_m[n - 1:] = mu
    out_s[n - 1:] = np.sqrt(np.maximum(s2 / n - mu * mu, 0.0))
    return out_m, out_s


def _vwap_roll(m, n):
    """Rolling n-minute VWAP and vol-weighted sigma of the 1m typical price (known at each 1m close)."""
    tp = (m['h'] + m['l'] + m['c']) / 3.0
    ref = float(np.median(tp))                       # de-mean for numerical stability of the variance
    x = tp - ref
    v = m['v']
    cv, cxv, cx2v = (np.r_[0.0, np.cumsum(a)] for a in (v, x * v, x * x * v))
    i = np.arange(len(v))
    lo = np.maximum(i + 1 - n, 0)
    sv, sxv, sx2v = cv[i + 1] - cv[lo], cxv[i + 1] - cxv[lo], cx2v[i + 1] - cx2v[lo]
    ok = (i + 1 >= n) & (sv > 0)
    safe = np.where(sv > 0, sv, 1.0)
    mu = np.where(ok, sxv / safe, np.nan)
    var = np.where(ok, sx2v / safe - mu * mu, np.nan)
    return mu + ref, np.sqrt(np.maximum(var, 0.0))


def features(m):
    key = (id(m), len(m['t']), float(m['t'][0]), float(m['t'][-1]))
    if key in _CACHE:
        return _CACHE[key]
    b15 = ir.resample(m, 900)
    b1h = ir.resample(m, 3600)
    atr15 = ir.wilder_atr(b15['h'], b15['l'], b15['c'], COMMON['atr_n'])
    adx1h = ir.wilder_adx(b1h['h'], b1h['l'], b1h['c'], COMMON['adx_n'])
    j = np.searchsorted(b1h['t_close'], b15['t_close'], side='right') - 1      # last 1h bar CLOSED by then
    adx_at = np.where(j >= 0, adx1h[np.maximum(j, 0)], np.nan)
    bb_mid, bb_sd = _rolling_mean_std(b15['c'], COMMON['bb_n'])
    vw, vsd = _vwap_roll(m, COMMON['vw_n'])
    il = b15['i_last']
    f = {'b15': b15, 'atr15': atr15, 'adx1h': adx_at, 'BB': (bb_mid, bb_sd), 'VW': (vw[il], vsd[il])}
    _CACHE.clear()
    _CACHE[key] = f
    return f


def simulate(m, name):
    var = VARIANTS[name]
    f = features(m)
    b15 = f['b15']
    n15 = len(b15['t'])
    t1, lo1, hi1 = m['t'], m['l'], m['h']
    N = len(t1)
    mid_a, sd_a = f[var['band']]
    K, SLK = var['k'], var['slk']
    life, min_tgt = COMMON['order_life_s'], COMMON['min_target_bps']
    trades = []
    busy_until = -np.inf
    for k in range(n15):
        tc = b15['t_close'][k]
        if tc <= busy_until:
            continue
        i0 = b15['i_last'][k] + 1
        if i0 >= N or t1[i0] != tc:
            continue
        adx, mid, sd, atr, c = f['adx1h'][k], mid_a[k], sd_a[k], f['atr15'][k], b15['c'][k]
        if not (np.isfinite(adx) and np.isfinite(mid) and np.isfinite(sd) and np.isfinite(atr)):
            continue
        if adx >= COMMON['adx_max'] or sd <= 0 or atr <= 0:
            continue
        eb, es = min(mid - K * sd, c), max(mid + K * sd, c)
        okb = (mid - eb) / eb * 1e4 >= min_tgt
        oks = (es - mid) / es * 1e4 >= min_tgt
        if not (okb or oks):
            continue
        fill = None
        i = i0
        while i < N and t1[i] < tc + life:
            hb = okb and lo1[i] < eb
            hs = oks and hi1[i] > es
            if hb and hs:
                break
            if hb:
                fill = (i, 1, eb)
                break
            if hs:
                fill = (i, -1, es)
                break
            i += 1
        if fill is None:
            continue
        i_f, side, entry = fill
        tp = mid
        sl = entry - side * SLK * atr
        px, how, ix, mk = ir._exit_scan(m, i_f, side, entry, tp, sl, hold=COMMON['hold_min'])
        gross = side * (px - entry) / entry * 1e4
        net = gross - ir.MAKER - (ir.MAKER if mk else ir.TAKER)
        trades.append({'t': float(t1[ix]), 't_entry': float(t1[i_f]), 'side': side, 'entry': float(entry),
                       'exit_px': float(px), 'gross_bps': float(gross), 'net_bps': float(net), 'exit': how,
                       'hold_min': float((t1[ix] - t1[i_f]) / 60 + 1), 'tgt_bps': float(side * (tp - entry) / entry * 1e4),
                       'sl_bps': float(SLK * atr / entry * 1e4), 'adx': float(adx)})
        busy_until = t1[ix] + 60
    return trades
