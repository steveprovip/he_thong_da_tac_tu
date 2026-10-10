"""Intraday time-series momentum (ITSM) on Binance USD-M perps (BTC/SOL/DOGE), 1-minute resolution.  Offline research.

HYPOTHESIS
  The sign of a recent multi-hour return (the session "so far" or the last N hours) predicts the sign of the next
  60-120 min return strongly enough that a maker-first entry plus a taker time exit (8-12 bps round trip at VIP0)
  leaves a positive mean net return per trade.  Mechanisms proposed in the equity/futures literature: late-informed
  or infrequently rebalancing traders (GHLZ 2018) and short-gamma hedging flows into the close (Baltussen et al.
  2021).  For crypto, an extra conjecture of mine (not literature): spot-BTC-ETF creation/redemption hedging
  concentrates in the 15:00-16:00 ET window of the CF Benchmarks New York reference rate.

LITERATURE (as recalled from training; bibliographic details not re-verified in this session)
  Gao, Han, Li & Zhou (2018) "Market intraday momentum", J. Financial Economics 129 - SPY: first half-hour return
      (incl. overnight) predicts the last half-hour return.
  Elaut, Froemmel & Lampaert (2018) "Intraday momentum in FX markets: disentangling informed trading from liquidity
      provision", J. Financial Markets - same pattern in RUB/USD.
  Baltussen, Da, Lammers & Martens (2021) "Hedging demand and market intraday momentum", J. Financial Economics 142 -
      the rest-of-day return predicts the last 30 min in 60+ futures; linked to gamma hedging.
  Shen, Urquhart & Wang (2022) "Bitcoin intraday time series momentum", Financial Review 57 - early-day return
      predicts the last half-hour return of the (UTC) day in Bitcoin.
  Wen, Bouri, Xu & Zhao (2022) "Intraday return predictability in the cryptocurrency markets: momentum, reversal,
      or both", N. American J. Economics & Finance - mixed momentum / reversal evidence in crypto.
  Zarattini, Aziz & Barbon (2024) "Beat the market: an effective intraday momentum strategy for S&P500 ETF (SPY)",
      SSRN working paper - "noise area" breakout from the open, VWAP / boundary trailing stop.
  Moskowitz, Ooi & Pedersen (2012) "Time series momentum", J. Financial Economics 104 - parent concept (1-12 months).
  Replications on 2025-era crypto PERPS with maker/taker costs and through-fill execution: none known.

PRE-REGISTERED VARIANTS (written before the first run; parameters are textbook / paper defaults, none tuned on data)
  US_ROD_LAST60     (GHLZ 2018 / Baltussen 2021 on the US cash session; America/New_York clock, DST-aware)
                    NY weekdays Mon-Fri (NYSE holidays NOT excluded).  r = C(15:00 ET) / C(16:00 ET of the previous
                    weekday) - 1.  side = sign(r).  Decision at 15:00 ET, time exit at 16:00 ET (60 min).
  UTC_ROD_LAST120   (Shen-Urquhart-Wang 2022 / Baltussen 2021 on the UTC day)
                    every UTC day: r = C(22:00) / O(00:00) - 1.  side = sign(r).  Decision 22:00 UTC, exit 24:00 UTC.
  ROLL4H_Z2_H120    (rolling "last N hours" TSMOM)
                    at every HH:00 UTC: r4 = ln C(T) / C(T-4h); s1 = std of the last 720 hourly log returns (>= 600
                    valid, window ending at T); z = r4 / (s1 * sqrt(4)).  |z| >= 2.0 -> side = sign(z).
                    Time exit at T + 120 min.
  NOISE_BO_H120     (Zarattini-Aziz-Barbon 2024 adapted to the UTC day)
                    O = open of 00:00 UTC; checkpoints every 30 min; sigma_k = mean over the previous 14 UTC days of
                    |C_k / O - 1| at the same checkpoint k (all 14 required); U_k / L_k = O (1 +/- sigma_k).
                    Entry at checkpoints 00:30..22:00 when flat: long if C_k > U_k, short if C_k < L_k.
                    Exit at the first later checkpoint where long: C < max(U, session VWAP) / short: C > min(L, VWAP)
                    (taker at the next minute's open), else time exit at decision + 120 min.
                    Session VWAP = sum(typical price x volume) / sum(volume) of the 1m bars since 00:00 UTC.
  COMMON EXECUTION  (same conservative model as tools/intraday_replay.py)
                    C(X) = close of the 1m bar that CLOSES at X; every decision uses closed bars and acts from the
                    next minute.  Entry: post-only limit at the decision bar's close, live for W = 3 minutes; it fills
                    (maker, 2 bps) only in a minute that trades THROUGH it (buy: low < limit, sell: high > limit);
                    if unfilled, taker at the open of minute T+3 (5 bps + 1 bp slippage = 6).  Exits are taker (6) at
                    the open of the minute starting at the exit time (time barrier) or after the checkpoint close
                    (NOISE trailing exit).  No profit target and no intrabar stop (literature: hold to the horizon),
                    so the stop-before-target rule never binds.  One position per symbol per variant; a new decision
                    is accepted only strictly after the previous exit time.  Missing signal / decision bars -> skip.
  SELECTION         DEV = 2025-01-01..2025-12-31 (by exit time; 2024-07..2024-12 warm-up only), pooled over symbols
                    via ir.summarize.  DEV-best = highest mean net bps with n >= 50; NONE if no variant has mean net > 0.
                    The 2026 holdout is NOT computed here.
"""
from __future__ import annotations

import sys
from datetime import date, datetime, time as dtime, timedelta, timezone
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

sys.path.insert(0, '/home/user/he_thong_da_tac_tu/tools')
import intraday_replay as ir  # noqa: E402

MAKER, TAKER = ir.MAKER, ir.TAKER          # 2.0, 6.0 (5 + 1 slippage)
W = 3                                      # maker window, minutes
NY = ZoneInfo('America/New_York')

VARIANTS = {
    'US_ROD_LAST60': {'kind': 'US_ROD', 'dec': (15, 0), 'exit': (16, 0)},
    'UTC_ROD_LAST120': {'kind': 'UTC_ROD', 'dec_h': 22, 'hold': 120},
    'ROLL4H_Z2_H120': {'kind': 'ROLL', 'look_h': 4, 'z': 2.0, 'hold': 120, 'sig_n': 720, 'sig_min': 600},
    'NOISE_BO_H120': {'kind': 'NOISE', 'lookback_d': 14, 'step': 1800, 'first_k': 1, 'last_k': 44, 'hold': 120},
}


# ------------------------------------------------------------------ helpers
def _close_idx(t, ts):
    """index of the 1m bar that CLOSES at ts (open == ts - 60), or -1."""
    i = int(np.searchsorted(t, ts - 60.0))
    return i if i < len(t) and t[i] == ts - 60.0 else -1


def _closes_at(m, times):
    """vectorised C(X) for an array of times X (NaN where the bar is missing)."""
    t = m['t']
    times = np.asarray(times, dtype=float)
    i = np.searchsorted(t, times - 60.0)
    ok = i < len(t)
    ic = np.where(ok, i, 0)
    ok &= t[ic] == times - 60.0
    return np.where(ok, m['c'][ic], np.nan)


def _trade(m, i_dec, side, deadline, exit_times=None):
    """Execute one decision taken at the close of bar i_dec.  -> trade dict or None."""
    t, o, h, l, c = m['t'], m['o'], m['h'], m['l'], m['c']
    n = len(t)
    T = t[i_dec] + 60.0
    lim = c[i_dec]
    fill_i = None
    for j in range(i_dec + 1, min(i_dec + 1 + W, n)):
        if t[j] >= T + W * 60:
            break
        if (side > 0 and l[j] < lim) or (side < 0 and h[j] > lim):
            fill_i, entry, mk_in = j, lim, True
            break
    if fill_i is None:
        j = int(np.searchsorted(t, T + W * 60))
        if j >= n or t[j] != T + W * 60:
            return None
        fill_i, entry, mk_in = j, o[j], False
    how, t_x = 'TIME', deadline
    if exit_times is not None:
        done = t[fill_i] + 60.0
        for tau in exit_times:
            if done <= tau < deadline:
                how, t_x = 'TRAIL', tau
                break
    k = int(np.searchsorted(t, t_x))
    if k >= n:
        k, px, how, tx = n - 1, c[n - 1], 'END', t[n - 1] + 60.0
    else:
        px, tx = o[k], t[k]
    if tx <= t[fill_i]:
        return None
    gross = side * (px - entry) / entry * 1e4
    net = gross - (MAKER if mk_in else TAKER) - TAKER
    return {'t': float(tx), 't_entry': float(t[fill_i]), 't_dec': float(T), 'side': int(side), 'entry': float(entry),
            'exit_px': float(px), 'gross_bps': float(gross), 'net_bps': float(net), 'exit': how,
            'maker_entry': bool(mk_in), 'hold_min': float((tx - t[fill_i]) / 60.0)}


def _days(m):
    d0 = int(m['t'][0] // 86400)
    d1 = int(m['t'][-1] // 86400)
    return np.arange(d0, d1 + 1)


# ------------------------------------------------------------------ candidate decisions (time-ordered)
def _cand_us_rod(m, p):
    out = []
    for d in _days(m):
        day = date(1970, 1, 1) + timedelta(days=int(d))
        if day.weekday() >= 5:
            continue
        prev = day - timedelta(days=3 if day.weekday() == 0 else 1)
        T = datetime.combine(day, dtime(*p['dec']), NY).timestamp()
        X = datetime.combine(day, dtime(*p['exit']), NY).timestamp()
        P = datetime.combine(prev, dtime(*p['exit']), NY).timestamp()
        i_dec, i_p = _close_idx(m['t'], T), _close_idx(m['t'], P)
        if i_dec < 0 or i_p < 0:
            continue
        r = m['c'][i_dec] / m['c'][i_p] - 1.0
        if r != 0:
            out.append((T, i_dec, 1 if r > 0 else -1, X, None))
    return out


def _cand_utc_rod(m, p):
    out = []
    t = m['t']
    for d in _days(m):
        s = d * 86400.0
        i0 = int(np.searchsorted(t, s))
        if i0 >= len(t) or t[i0] != s:
            continue
        T = s + p['dec_h'] * 3600.0
        i_dec = _close_idx(t, T)
        if i_dec < 0:
            continue
        r = m['c'][i_dec] / m['o'][i0] - 1.0
        if r != 0:
            out.append((T, i_dec, 1 if r > 0 else -1, T + p['hold'] * 60.0, None))
    return out


def _cand_roll(m, p):
    t = m['t']
    h0 = np.ceil(t[0] / 3600.0) * 3600.0
    H = np.arange(h0, t[-1] + 60.0 + 1, 3600.0)
    C = _closes_at(m, H)
    lr = pd.Series(np.log(C)).diff()
    s1 = lr.rolling(p['sig_n'], min_periods=p['sig_min']).std().to_numpy()
    L = p['look_h']
    r4 = np.full(len(H), np.nan)
    r4[L:] = np.log(C[L:] / C[:-L])
    z = r4 / (s1 * np.sqrt(L))
    out = []
    for k in np.flatnonzero(np.isfinite(z) & (np.abs(z) >= p['z'])):
        T = H[k]
        i_dec = _close_idx(t, T)
        if i_dec >= 0:
            out.append((T, i_dec, 1 if z[k] > 0 else -1, T + p['hold'] * 60.0, None))
    return out


def _cand_noise(m, p):
    t = m['t']
    days = _days(m)
    step = p['step']
    K = 86400 // step                                   # 48 checkpoints, k = 1..48 (k = 48 is 24:00)
    ks = np.arange(1, K + 1)
    tau = days[:, None] * 86400.0 + ks[None, :] * step  # (D, K)
    C = _closes_at(m, tau.ravel()).reshape(tau.shape)
    i0 = np.searchsorted(t, days * 86400.0)
    ok0 = (i0 < len(t))
    ok0[ok0] &= t[i0[ok0]] == days[ok0] * 86400.0
    O = np.where(ok0, m['o'][np.minimum(i0, len(t) - 1)], np.nan)
    move = np.abs(C / O[:, None] - 1.0)
    sig = pd.DataFrame(move).rolling(p['lookback_d'], min_periods=p['lookback_d']).mean().shift(1).to_numpy()
    U, Lo = O[:, None] * (1 + sig), O[:, None] * (1 - sig)
    # session VWAP at each checkpoint (bars with open in [day start, tau))
    tp = (m['h'] + m['l'] + m['c']) / 3.0
    cpv = np.r_[0.0, np.cumsum(tp * m['v'])]
    cv = np.r_[0.0, np.cumsum(m['v'])]
    a = np.searchsorted(t, days * 86400.0)[:, None]
    b = np.searchsorted(t, tau)                         # bars with open < tau
    sv = cv[b] - cv[a]
    vw = np.where(sv > 0, (cpv[b] - cpv[a]) / np.where(sv > 0, sv, 1.0), np.nan)
    with np.errstate(invalid='ignore'):
        long_x = C < np.fmax(U, vw)
        short_x = C > np.fmin(Lo, vw)
        long_in = C > U
        short_in = C < Lo
    out = []
    for di in range(len(days)):
        if not np.isfinite(O[di]):
            continue
        for k in range(p['first_k'] - 1, p['last_k']):      # column index; checkpoint = (k + 1) * step
            if not (np.isfinite(C[di, k]) and np.isfinite(U[di, k])):
                continue
            side = 1 if long_in[di, k] else (-1 if short_in[di, k] else 0)
            if not side:
                continue
            T = tau[di, k]
            i_dec = _close_idx(t, T)
            if i_dec < 0:
                continue
            flags = long_x[di, k + 1:] if side > 0 else short_x[di, k + 1:]
            ex = tau[di, k + 1:][flags & np.isfinite(C[di, k + 1:])]
            out.append((T, i_dec, side, T + p['hold'] * 60.0, ex.tolist()))
    return out


_CAND = {'US_ROD': _cand_us_rod, 'UTC_ROD': _cand_utc_rod, 'ROLL': _cand_roll, 'NOISE': _cand_noise}


def candidates(m, name):
    p = VARIANTS[name]
    return sorted(_CAND[p['kind']](m, p), key=lambda x: x[0])


def simulate(m, name):
    """All decisions of variant `name` on the whole of m (no date filter), one position at a time."""
    trades = []
    last_exit = -np.inf
    for T, i_dec, side, deadline, ex in candidates(m, name):
        if T <= last_exit:
            continue
        tr = _trade(m, i_dec, side, deadline, ex)
        if tr is None:
            continue
        trades.append(tr)
        last_exit = tr['t']
    return trades
