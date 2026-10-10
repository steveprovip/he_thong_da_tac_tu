"""Lane 3: intraday 5m / 15m micro-swing (request 10-10 b).  Signals and execution model = tools/intraday_replay.py.

S1  VWAP-band mean reversion at each 5m close: the 5m low <= VWAP - 2 sigma (high >= VWAP + 2 sigma) AND CVD of the
    last minute with the reversal AND |EMA20(5m) slope| < 0.08 %; entry post-only at the 5m close for one minute;
    TP = VWAP, SL = 1 ATR(5m), skipped unless R:R >= 1.5.  VWAP = ROLL100 (100 x 1m) or SESSION (00:00 UTC).
S2  15m momentum continuation: EMA20(5m) slope over 3 bars >= 0.15 % AND ADX(15m) > 25 AND close beyond EMA20;
    post-only at EMA20, re-anchored at each 5m close while the trend holds; TP +60 / +100 bps; stop at the extreme
    of the last 2 closed 5m bars (1 ATR if within 5 bps), trailed to each new 5m bar.
Both: 45 min time barrier (taker), one position per symbol, the stop before the target inside a minute.

10-10 c regime-aligned variants (PRE-REGISTERED before their first run; thresholds = the request's):
  regime at a 5m close  UP    (KER10(5m) > 0.40 with the 10-bar move up) OR EMA20(5m) slope1 > +0.10 %
                        DOWN  mirrored;  CHOP  KER10(5m) < 0.25 AND ADX14(15m) < 20;  else MIXED
R1  S1 (SESSION VWAP) only in CHOP: no fade of a band touch while the market trends (the 16:08-16:32 BTC shorts).
P5  5m pull-back with the regime: UP -> the 5m low dipped to max(EMA20(5m), session VWAP), the close is back above
    EMA20, and the last minute's CVD > 0 (sellers exhausted) -> post-only BUY at the close for one minute (DOWN
    mirrored).  Stop 1.5 ATR(5m).  TP1 = 50 % at +35 | +50 bps (maker); from the next minute the rest has its stop
    at break-even (entry) and trails a chandelier 2.5 ATR(5m) from the best 5m close; 180 min time barrier.
P15 15m / 1h pull-back: UP = EMA20(1h) rose >= 0.10 % over the last closed 1h bar AND the 15m close > EMA20(15m);
    the 15m low dipped to max(EMA20(15m), session VWAP), the 15m close is back above EMA20(15m), and the 15m bar's CVD
    > 0 -> post-only BUY at the 15m close, live 5 min (DOWN mirrored).  Stop 1.0 ATR(15m); TP1 50 % at +35 | +50
    bps; then break-even + chandelier 2.5 ATR(15m) on 15m closes; 180 min time barrier.
Rows of P variants book the whole trade on its full notional (TP1 leg + rest), win = net > 0.

Modes (Profile.intraday_lane): 'off' | 'shadow'.
  shadow  one independent VIRTUAL book per variant (exactly the replay's per-variant books) fed with each newly
          CLOSED mainnet 1m kline (public REST, with the retry / cache / spot-proxy layer of core/engine.py):
          fills only when a later bar trades THROUGH the limit -> data/intraday_trades_shadow.jsonl.  No venue call.
          Each row also records `would_block`: why a real entry on the shared account would have been refused at
          the signal (trend / micro position, the 10 min after a 4H close, the daily loss breaker, a lot size below
          the exchange minimum notional) - measured, not applied: virtual books use the replay's fixed notional,
          so the rows stay comparable with the replay and with config/intraday_verdict.json.
          Data: a rolling store of closed 1m klines (>= 2 pages = 2999 bars after a back-fill, kept to 3 days) so
          ADX(15m) is warmed up and the SESSION VWAP of every replayed bar starts at its own 00:00 UTC; bars are
          admitted on the Binance SERVER clock (+ margin), never on the local clock; a hole the REST window cannot
          fill ends every open virtual trade as UNKNOWN (no invented exit price) and restarts the books.
  'active' is NOT implemented on purpose: no (symbol, strategy) is ACTIVE-ELIGIBLE (replay 10-10 b: every variant
          negative), and the 10-10 b review of a first active path found safety defects (non reduce-only TP able to
          open the opposite position, unprotected remainders after partial fills / rejected stops, no state
          persistence, the 4H trend lane blocked up to 45 min).  Requesting it logs a refusal and runs shadow.
          A future executor must reuse the trend lane's persisted, reduce-only order path.
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path
from typing import Optional

import numpy as np

VERDICT = Path(__file__).resolve().parents[1] / 'config' / 'intraday_verdict.json'     # written by the replay
P = {'band_k': 2.0, 'mr_slope_max': 0.08, 'rr_min': 1.5, 'sl_atr': 1.0, 'trend_slope': 0.15, 'adx_min': 25.0,
     'hold_min': 45, 'sl_min_bps': 5.0, 'vwap_roll': 100, 'session_min': 60}      # = tools/intraday_replay.py
VARIANTS = {
    'S1_ROLL100': {'kind': 'S1', 'vwap': 'ROLL100'},
    'S1_SESSION': {'kind': 'S1', 'vwap': 'SESSION'},
    'S2_slope3_tp60': {'kind': 'S2', 'slope': 3, 'tp': 60.0},
    'S2_slope3_tp100': {'kind': 'S2', 'slope': 3, 'tp': 100.0},
    'S2_slope1_tp60': {'kind': 'S2', 'slope': 1, 'tp': 60.0},
    'S2_slope1_tp100': {'kind': 'S2', 'slope': 1, 'tp': 100.0},
    # 10-10 c regime-aligned (see the docstring)
    'R1_SESSION': {'kind': 'R1', 'vwap': 'SESSION'},
    'P5_tp35': {'kind': 'P5', 'tp1': 35.0, 'sl_atr': 1.5, 'trail_atr': 2.5, 'hold': 180, 'live_s': 60},
    'P5_tp50': {'kind': 'P5', 'tp1': 50.0, 'sl_atr': 1.5, 'trail_atr': 2.5, 'hold': 180, 'live_s': 60},
    'P15_tp35': {'kind': 'P15', 'tp1': 35.0, 'sl_atr': 1.0, 'trail_atr': 2.5, 'hold': 180, 'live_s': 300},
    'P15_tp50': {'kind': 'P15', 'tp1': 50.0, 'sl_atr': 1.0, 'trail_atr': 2.5, 'hold': 180, 'live_s': 300},
}
REG = {'ker_trend': 0.40, 'slope_trend': 0.10, 'ker_chop': 0.25, 'adx_chop': 20.0, 'h1_slope': 0.10}


# ------------------------------------------------------------------ indicators (shared with the replay)
def resample(m: dict, span: int) -> dict:
    """Complete clock-aligned bars of `span` seconds from 1m bars (t = open, t_close = open + span)."""
    k = (m['t'] // span).astype(np.int64)
    starts = np.flatnonzero(np.r_[True, k[1:] != k[:-1]])
    ends = np.r_[starts[1:], len(k)]
    full = (ends - starts) == span // 60
    s, e = starts[full], ends[full]
    return {'t': k[s] * float(span), 't_close': k[s] * float(span) + span, 'o': m['o'][s],
            'h': np.maximum.reduceat(m['h'], starts)[full], 'l': np.minimum.reduceat(m['l'], starts)[full],
            'c': m['c'][e - 1], 'i_last': e - 1}


def ema(x, n):
    out = np.empty(len(x))
    a = 2.0 / (n + 1)
    acc = x[0] if len(x) else 0.0
    for i, v in enumerate(x):
        acc = v if i == 0 else acc + a * (v - acc)
        out[i] = acc
    return out


def wilder_atr(h, l, c, n=14):
    tr = np.maximum(h - l, np.maximum(np.abs(h - np.r_[c[0], c[:-1]]), np.abs(l - np.r_[c[0], c[:-1]])))
    out = np.full(len(c), np.nan)
    if len(c) < n:
        return out
    a = tr[:n].mean()
    out[n - 1] = a
    for i in range(n, len(c)):
        a = (a * (n - 1) + tr[i]) / n
        out[i] = a
    return out


def wilder_adx(h, l, c, n=14):
    out = np.full(len(c), np.nan)
    if len(c) < 2 * n + 1:
        return out
    up, dn = h[1:] - h[:-1], l[:-1] - l[1:]
    pdm = np.where((up > dn) & (up > 0), up, 0.0)
    ndm = np.where((dn > up) & (dn > 0), dn, 0.0)
    tr = np.maximum(h[1:] - l[1:], np.maximum(np.abs(h[1:] - c[:-1]), np.abs(l[1:] - c[:-1])))
    a, p, q = tr[:n].sum(), pdm[:n].sum(), ndm[:n].sum()
    dx = []
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


def vwap_bands(m: dict, kind: str):
    """Per 1m bar (known at its CLOSE): VWAP and sigma of the typical price, ROLL100 or SESSION (00:00 UTC)."""
    tp = (m['h'] + m['l'] + m['c']) / 3.0
    v = m['v']
    if kind == 'ROLL100':
        n = P['vwap_roll']
        cv, cpv, cp2v = (np.r_[0.0, np.cumsum(x)] for x in (v, tp * v, tp * tp * v))
        i = np.arange(len(v))
        lo = np.maximum(i + 1 - n, 0)
        sv, spv, sp2v = cv[i + 1] - cv[lo], cpv[i + 1] - cpv[lo], cp2v[i + 1] - cp2v[lo]
        ok = (i + 1 >= n) & (sv > 0)
    else:
        day = (m['t'] // 86400).astype(np.int64)
        start = np.r_[True, day[1:] != day[:-1]]
        grp = np.cumsum(start) - 1
        sv, spv, sp2v = (np.zeros(len(v)) for _ in range(3))
        for arr, src in ((sv, v), (spv, tp * v), (sp2v, tp * tp * v)):
            cs = np.cumsum(src)
            arr[:] = cs - np.r_[0.0, cs][np.flatnonzero(start)][grp]
        mins = np.arange(len(v)) - np.flatnonzero(start)[grp] + 1
        ok = (mins >= P['session_min']) & (sv > 0)
    vw = np.where(ok, spv / np.where(sv > 0, sv, 1.0), np.nan)
    var = np.where(ok, sp2v / np.where(sv > 0, sv, 1.0) - vw * vw, np.nan)
    return vw, np.sqrt(np.maximum(var, 0.0))


def features(m: dict) -> dict:
    """Indicator values at each COMPLETE 5m close (index k of f['b5'])."""
    b5, b15 = resample(m, 300), resample(m, 900)
    e20 = ema(b5['c'], 20)
    slope1 = np.r_[np.nan, (e20[1:] / e20[:-1] - 1) * 100] if len(e20) else e20
    slope3 = np.r_[np.full(3, np.nan), (e20[3:] / e20[:-3] - 1) * 100] if len(e20) > 3 else np.full(len(e20), np.nan)
    atr5 = wilder_atr(b5['h'], b5['l'], b5['c']) if len(b5['c']) else np.array([])
    adx15 = wilder_adx(b15['h'], b15['l'], b15['c']) if len(b15['c']) else np.array([])
    j = np.searchsorted(b15['t_close'], b5['t_close'], side='right') - 1
    adx_at = np.where(j >= 0, adx15[np.maximum(j, 0)], np.nan) if len(adx15) else np.full(len(e20), np.nan)
    il = b5['i_last']
    out = {'b5': b5, 'ema20': e20, 'slope1': slope1, 'slope3': slope3, 'atr5': atr5, 'adx15': adx_at,
           'cvd60': 2 * m['tbq'][il] - m['qv'][il]}
    vw_s = None
    for kind in ('ROLL100', 'SESSION'):
        vw, sd = vwap_bands(m, kind)
        out[f'vwap_{kind}'], out[f'sd_{kind}'] = vw[il], sd[il]
        if kind == 'SESSION':
            vw_s = vw
    # 10-10 c: KER10 on 5m closes, the 15m / 1h blocks, 1m-index -> 5m / 15m close maps
    c5 = b5['c']
    ker5, dir5 = np.full(len(c5), np.nan), np.zeros(len(c5))
    if len(c5) > 10:
        path = _roll_sum(np.abs(np.diff(c5, prepend=c5[0])), 10)
        net = c5[10:] - c5[:-10]
        ker5[10:] = np.abs(net) / np.where(path[10:] > 0, path[10:], np.inf)
        dir5[10:] = np.sign(net)
    out['ker5'], out['dir5'] = ker5, dir5
    il15 = b15['i_last']
    e15 = ema(b15['c'], 20)
    atr15 = wilder_atr(b15['h'], b15['l'], b15['c']) if len(b15['c']) else np.array([])
    cvd1 = 2 * m['tbq'] - m['qv']
    cs = np.r_[0.0, np.cumsum(cvd1)]
    cvd15 = cs[il15 + 1] - cs[np.maximum(il15 + 1 - 15, 0)] if len(il15) else np.array([])
    b1h = resample(m, 3600)
    e1h = ema(b1h['c'], 20)
    s1h = np.r_[np.nan, (e1h[1:] / e1h[:-1] - 1) * 100] if len(e1h) > 1 else np.full(len(e1h), np.nan)
    jh = np.searchsorted(b1h['t_close'], b15['t_close'], side='right') - 1 if len(b15['t_close']) else np.array([], int)
    out['b15'] = b15
    out['ema20_15'], out['atr15'], out['cvd15'] = e15, atr15, cvd15
    out['vwap_SESSION_15'] = vw_s[il15] if len(il15) else np.array([])
    out['h1_slope'] = np.where(jh >= 0, s1h[np.maximum(jh, 0)], np.nan) if len(s1h) else np.full(len(il15), np.nan)
    n = len(m['t'])
    out['k5_of'] = np.full(n, -1, dtype=np.int64)
    out['k5_of'][il] = np.arange(len(il))
    out['k15_of'] = np.full(n, -1, dtype=np.int64)
    out['k15_of'][il15] = np.arange(len(il15))
    return out


def _roll_sum(x, w):
    c = np.r_[0.0, np.cumsum(x)]
    k = np.arange(len(x))
    return c[k + 1] - c[np.maximum(k + 1 - w, 0)]


def regime5(f: dict, k: int) -> str:
    """UP / DOWN / CHOP / MIXED at 5m close k (10-10 c, see the module docstring)."""
    ker, d, s1, adx = f['ker5'][k], f['dir5'][k], f['slope1'][k], f['adx15'][k]
    up = (np.isfinite(ker) and ker > REG['ker_trend'] and d > 0) or (np.isfinite(s1) and s1 > REG['slope_trend'])
    dn = (np.isfinite(ker) and ker > REG['ker_trend'] and d < 0) or (np.isfinite(s1) and s1 < -REG['slope_trend'])
    if up and not dn:
        return 'UP'
    if dn and not up:
        return 'DOWN'
    if np.isfinite(ker) and np.isfinite(adx) and ker < REG['ker_chop'] and adx < REG['adx_chop']:
        return 'CHOP'
    return 'MIXED'


def p5_signal(f: dict, k: int) -> Optional[tuple]:
    """5m pull-back with the regime -> (side, entry, sl_dist) or None."""
    reg = regime5(f, k)
    if reg not in ('UP', 'DOWN'):
        return None
    b5, e20, vw, atr = f['b5'], f['ema20'][k], f['vwap_SESSION'][k], f['atr5'][k]
    if not (np.isfinite(atr) and atr > 0 and np.isfinite(vw)):
        return None
    c, cvd = b5['c'][k], f['cvd60'][k]
    if reg == 'UP' and b5['l'][k] <= max(e20, vw) and c > e20 and cvd > 0:
        return 1, c, atr
    if reg == 'DOWN' and b5['h'][k] >= min(e20, vw) and c < e20 and cvd < 0:
        return -1, c, atr
    return None


def p15_signal(f: dict, j: int) -> Optional[tuple]:
    """15m / 1h pull-back at 15m close j -> (side, entry, sl_dist) or None."""
    b15, e15, vw, atr, s1h, cvd = (f['b15'], f['ema20_15'][j], f['vwap_SESSION_15'][j], f['atr15'][j],
                                   f['h1_slope'][j], f['cvd15'][j])
    if not (np.isfinite(atr) and atr > 0 and np.isfinite(vw) and np.isfinite(s1h)):
        return None
    c = b15['c'][j]
    if s1h >= REG['h1_slope'] and c > e15 and b15['l'][j] <= max(e15, vw) and cvd > 0:
        return 1, c, atr
    if s1h <= -REG['h1_slope'] and c < e15 and b15['h'][j] >= min(e15, vw) and cvd < 0:
        return -1, c, atr
    return None


def s1_signal(f: dict, k: int, vwap: str) -> Optional[tuple]:
    """-> (side, entry, tp, sl) at 5m close k, or None."""
    b5 = f['b5']
    c, atr = b5['c'][k], f['atr5'][k]
    vw, sd, sl1 = f[f'vwap_{vwap}'][k], f[f'sd_{vwap}'][k], f['slope1'][k]
    if not (np.isfinite(atr) and atr > 0 and np.isfinite(vw) and np.isfinite(sd) and sd > 0 and np.isfinite(sl1)):
        return None
    if abs(sl1) >= P['mr_slope_max']:
        return None
    if b5['l'][k] <= vw - P['band_k'] * sd and f['cvd60'][k] > 0 and c < vw:
        side = 1
    elif b5['h'][k] >= vw + P['band_k'] * sd and f['cvd60'][k] < 0 and c > vw:
        side = -1
    else:
        return None
    sl = c - side * P['sl_atr'] * atr
    if side * (vw - c) < P['rr_min'] * side * (c - sl):
        return None
    return side, c, vw, sl


def s2_trend(f: dict, k: int, slope: int) -> int:
    """+1 / -1 / 0: the S2 trend condition at 5m close k."""
    s_ = f['slope3' if slope == 3 else 'slope1'][k]
    adx, e20, c = f['adx15'][k], f['ema20'][k], f['b5']['c'][k]
    if not (np.isfinite(s_) and np.isfinite(adx)) or adx <= P['adx_min']:
        return 0
    if s_ >= P['trend_slope'] and c > e20:
        return 1
    if s_ <= -P['trend_slope'] and c < e20:
        return -1
    return 0


def s2_stop(f: dict, last_k: int, side: int, entry: float) -> float:
    b5 = f['b5']
    sl = b5['l'][max(0, last_k - 1):last_k + 1].min() if side > 0 else b5['h'][max(0, last_k - 1):last_k + 1].max()
    if side * (entry - sl) / entry * 1e4 < P['sl_min_bps']:
        sl = entry - side * P['sl_atr'] * f['atr5'][last_k]
    return float(sl)


def rows_to_m(rows) -> Optional[dict]:
    """engine.fetch_klines rows (open_s, o, h, l, c, close_s, quote vol, taker-buy quote, base vol) -> arrays.
    Rows without the volume fields (spot proxy) -> None: volume / CVD UNKNOWN, no signal."""
    if not rows or any(len(r) < 9 for r in rows):
        return None
    a = np.array([[r[0], r[1], r[2], r[3], r[4], r[8], r[6], r[7]] for r in rows], dtype=float)
    return {'t': a[:, 0], 'o': a[:, 1], 'h': a[:, 2], 'l': a[:, 3], 'c': a[:, 4], 'v': a[:, 5], 'qv': a[:, 6],
            'tbq': a[:, 7]}


class IntradayLane:
    def __init__(self, e, ledger_dir: Path, fetch=None, clock=time.time, verdict_path: Path = VERDICT,
                 server_offset=None):
        p = e.p
        self.e, self.clock, self.fetch = e, clock, fetch
        # server clock: production asks Binance; an injected fetch (tests / replays) without an offset uses 0
        self.server_offset_fn = server_offset if server_offset is not None else (None if fetch is None else (lambda: 0.0))
        self.offset, self.offset_t = 0.0, -1e18
        self.store = {}             # open_s -> kline row (closed bars only)
        self.sink = None            # replay hook: f(row) instead of the shadow ledger
        self.gaps = self.skew = 0
        self.variants = [v for v in p.intraday_variants if v in VARIANTS]
        self.mode = 'off' if p.intraday_lane == 'off' else 'shadow'
        if p.intraday_lane == 'active':
            try:
                v = json.loads(Path(verdict_path).read_text(encoding='utf-8')).get(p.symbol, {})
            except (OSError, ValueError):
                v = {}
            elig = [k for k, x in v.items() if isinstance(x, dict) and x.get('verdict') == 'ACTIVE-ELIGIBLE']
            e.event(f'INTRADAY active refused for {p.symbol}: '
                    + (f'{elig} eligible but no active executor is built yet' if elig else 'no ACTIVE-ELIGIBLE verdict')
                    + ' -> shadow')
        self.ledger = Path(ledger_dir) / 'intraday_trades_shadow.jsonl'
        self.books = {name: {'order': None, 'pos': None} for name in self.variants}
        self.last_t = None          # open time of the last processed 1m bar
        self.day, self.day_pnl, self.n = None, 0.0, 0
        self.last = {'mode': self.mode}

    # ------------------------------------------------------------------ the shared account (measured only)
    def would_block(self, t: float, px: Optional[float] = None) -> Optional[str]:
        """Why a REAL entry on the shared one-way account would be refused now (recorded on shadow rows)."""
        e, p = self.e, self.e.p
        if e.trend_owned or e.pos or e.busy:
            return 'symbol held by another lane'
        if t % 14400 < p.intraday_trend_quiet_s:
            return '4H close window reserved for the trend lane'
        if self.day_pnl <= -p.intraday_daily_kill_usdt:
            return f'daily loss breaker {self.day_pnl:+.2f} U'
        if px:
            wallet = float((e.account or {}).get('wallet') or 0.0)
            spec = e.spec or {}
            budget = min(p.intraday_notional_usdt, p.intraday_budget_pct * wallet) if wallet else None
            if budget is not None and budget < p.intraday_notional_usdt:
                return f'budget {p.intraday_budget_pct:.0%} of wallet = {budget:.0f} U < {p.intraday_notional_usdt:g} U'
            step, min_n = spec.get('step', 0.001), spec.get('min_notional', 5.0)
            q = math.floor(p.intraday_notional_usdt / px / step + 1e-9) * step
            if q * px < min_n:
                return f'below min notional: {q:g} = {q * px:.1f} U < {min_n:g} U'
        return None

    # ------------------------------------------------------------------ ledger
    def _close(self, bk: dict, px: float, how: str, t: float, maker: bool) -> None:
        """Book the whole trade on its full notional: the TP1 leg (if any) + the remaining fraction at px."""
        p, ps = self.e.p, bk['pos']
        frac = ps.get('frac', 1.0)
        leg = ps['side'] * (px - ps['entry']) / ps['entry'] * 1e4
        exit_fee = p.maker_bps if maker else p.taker_bps + p.intraday_slip_bps
        gross = ps.get('real_gross', 0.0) + frac * leg
        net = gross - p.maker_bps - ps.get('real_fee', 0.0) - frac * exit_fee
        usd = net / 1e4 * ps['qty'] * ps['entry']
        self.day_pnl += usd
        self.n += 1
        if ps.get('tp1_done'):
            how = f'TP1+{how}'
        row = {'t': float(t), 'lane': 'INTRADAY', 'mode': self.mode, 'symbol': p.symbol, 'variant': ps['var'],
               'side': 'BUY' if ps['side'] > 0 else 'SELL', 'qty': float(ps['qty']), 'entry': float(ps['entry']),
               'exit': float(px), 'exit_how': how, 'hold_min': float((t - ps['fill_t']) / 60),
               'tp': float(ps['tp']) if ps.get('tp') is not None else None,
               'tp1': float(ps['tp1']) if ps.get('tp1') is not None else None,
               'sl0': float(ps['sl0']), 'regime': ps.get('regime'), 'fill_t': float(ps['fill_t']),
               'gross_bps': float(gross), 'net_bps': float(net),
               'net_usd': float(usd), 'would_block': ps.get('would_block'),
               'fees_model': f'maker {p.maker_bps:g} / taker {p.taker_bps:g} + {p.intraday_slip_bps:g} bps'}
        bk['pos'] = None
        if self.sink is not None:
            self.sink(row)
            return
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ledger, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(row) + '\n')
        except OSError:
            pass
        self.e.event(f'INTRADAY SHADOW {row["side"]} {p.symbol} {ps["var"]} {how} {net:+.1f} bps ({usd:+.3f} U)')

    def _qty(self, px: float) -> float:
        """Virtual size = the replay's fixed notional (no lot rounding, no minimum: those go to would_block)."""
        return self.e.p.intraday_notional_usdt / px

    # ------------------------------------------------------------------ bar-by-bar engine (each book == the replay)
    def on_bar(self, m: dict, f: dict, i: int) -> None:
        for name, bk in self.books.items():
            self._bar(bk, name, m, f, i)

    def _bar(self, bk: dict, name: str, m: dict, f: dict, i: int) -> None:
        """Process CLOSED 1m bar i for one variant: exits, then the resting entry, then a signal at a 5m / 15m close."""
        t = float(m['t'][i])
        o, h, l, c = m['o'][i], m['h'][i], m['l'][i], m['c'][i]
        b5 = f['b5']
        v = VARIANTS[name]
        kind = v['kind']
        k5 = int(f['k5_of'][i])
        closes5 = k5 >= 0
        k15 = int(f['k15_of'][i])
        closes15 = k15 >= 0
        ps = bk['pos']
        if ps is not None:
            first = ps['fill_t'] == t
            d = ps['side']
            if ps.get('be_next'):                         # break-even from the minute after TP1
                ps['sl'] = max(ps['sl'], ps['entry']) if d > 0 else min(ps['sl'], ps['entry'])
                ps['be_next'] = False
            hold = v.get('hold', self.e.p.intraday_hold_min)
            if (d > 0 and l <= ps['sl']) or (d < 0 and h >= ps['sl']):
                self._close(bk, ps['sl'] if first else (min(o, ps['sl']) if d > 0 else max(o, ps['sl'])), 'SL', t + 60, False)
            elif not first and ps.get('tp') is not None and ((d > 0 and h > ps['tp']) or (d < 0 and l < ps['tp'])):
                self._close(bk, ps['tp'], 'TP', t + 60, True)
            elif t + 60 >= ps['fill_t'] + hold * 60:
                self._close(bk, c, 'TIME', t + 60, False)
            else:
                if not first and ps.get('tp1') is not None and not ps.get('tp1_done') and \
                        ((d > 0 and h > ps['tp1']) or (d < 0 and l < ps['tp1'])):
                    half = 0.5                            # TP1: half at the target, maker
                    ps['real_gross'] = ps.get('real_gross', 0.0) + half * d * (ps['tp1'] - ps['entry']) / ps['entry'] * 1e4
                    ps['real_fee'] = ps.get('real_fee', 0.0) + half * self.e.p.maker_bps
                    ps['frac'] = 1.0 - half
                    ps['tp1_done'], ps['be_next'] = True, True
                if kind == 'S2' and closes5:
                    nb = b5['l'][k5] if d > 0 else b5['h'][k5]
                    ps['sl'] = max(ps['sl'], nb) if d > 0 else min(ps['sl'], nb)
                elif ps.get('tp1_done') and ((kind == 'P5' and closes5) or (kind == 'P15' and closes15)):
                    cl, atr = (b5['c'][k5], f['atr5'][k5]) if kind == 'P5' else (f['b15']['c'][k15], f['atr15'][k15])
                    ps['best'] = max(ps.get('best', cl), cl) if d > 0 else min(ps.get('best', cl), cl)
                    if np.isfinite(atr):
                        lvl = ps['best'] - d * v['trail_atr'] * atr
                        ps['sl'] = max(ps['sl'], lvl) if d > 0 else min(ps['sl'], lvl)
            return
        od = bk['order']
        if od is not None:
            if (od['side'] > 0 and l < od['px']) or (od['side'] < 0 and h > od['px']):
                bk['order'] = None
                tp1 = None
                if kind == 'S2':
                    last_k = int(np.searchsorted(b5['t_close'], t, side='right')) - 1
                    sl = s2_stop(f, last_k, od['side'], od['px'])
                    tp = od['px'] * (1 + od['side'] * v['tp'] / 1e4)
                elif kind in ('P5', 'P15'):
                    sl = od['px'] - od['side'] * v['sl_atr'] * od['atr']
                    tp, tp1 = None, od['px'] * (1 + od['side'] * v['tp1'] / 1e4)
                else:
                    sl, tp = od['sl'], od['tp']
                bk['pos'] = {'var': name, 'side': od['side'], 'entry': od['px'], 'sl': sl, 'sl0': sl, 'tp': tp,
                             'tp1': tp1, 'fill_t': t, 'qty': od['qty'], 'would_block': od.get('would_block'),
                             'regime': od.get('regime')}
                self._bar(bk, name, m, f, i)              # the fill minute: only the stop can trigger
                return
            if kind in ('S1', 'R1', 'P5', 'P15') and t >= od['expire_t']:
                bk['order'] = None                        # one window to fill, no chase
            elif kind == 'S2' and closes5:
                if s2_trend(f, k5, v['slope']) != od['side']:
                    bk['order'] = None
                else:
                    od['px'] = float(f['ema20'][k5])      # re-anchor at the new EMA20
            if bk['order'] is not None or not (closes15 if kind == 'P15' else closes5):
                return
        od = None
        if kind in ('S1', 'R1') and closes5:
            sig = s1_signal(f, k5, v['vwap'])
            if sig and (kind == 'S1' or regime5(f, k5) == 'CHOP'):
                side, entry, tp, sl = sig
                od = {'side': side, 'px': float(entry), 'tp': float(tp), 'sl': float(sl), 'expire_t': t + 60}
        elif kind == 'S2' and closes5:
            sd = s2_trend(f, k5, v['slope'])
            if sd:
                od = {'side': sd, 'px': float(f['ema20'][k5])}
        elif kind == 'P5' and closes5:
            sig = p5_signal(f, k5)
            if sig:
                od = {'side': sig[0], 'px': float(sig[1]), 'atr': float(sig[2]), 'expire_t': t + v['live_s']}
        elif kind == 'P15' and closes15:
            sig = p15_signal(f, k15)
            if sig:
                od = {'side': sig[0], 'px': float(sig[1]), 'atr': float(sig[2]), 'expire_t': t + v['live_s']}
        if od is not None:
            od['qty'] = self._qty(od['px'])
            od['would_block'] = self.would_block(t + 60, od['px'])
            od['regime'] = regime5(f, k5) if closes5 else None
            bk['order'] = od

    # ------------------------------------------------------------------ data
    def _fetch_rows(self, fetch, n: int, end_ms=None) -> list:
        if end_ms is None:
            return list(fetch(self.e.p.symbol, '1m', n))
        try:
            return list(fetch(self.e.p.symbol, '1m', n, end_ms=end_ms))
        except TypeError:                                          # an injected fetch without paging
            return []

    def _gap(self, t_first_new: float) -> None:
        """A hole the REST window cannot fill: every open virtual trade ends UNKNOWN, the books restart."""
        self.gaps += 1
        p = self.e.p
        for name, bk in self.books.items():
            ps = bk['pos']
            if ps is not None:
                row = {'t': float(t_first_new), 'lane': 'INTRADAY', 'mode': self.mode, 'symbol': p.symbol,
                       'variant': name, 'side': 'BUY' if ps['side'] > 0 else 'SELL', 'qty': float(ps['qty']),
                       'entry': float(ps['entry']), 'exit': None, 'exit_how': 'UNKNOWN-DATA-GAP',
                       'gross_bps': None, 'net_bps': None, 'net_usd': None, 'would_block': ps.get('would_block'),
                       'gap_from': self.last_t, 'gap_to': float(t_first_new)}
                try:
                    with open(self.ledger, 'a', encoding='utf-8') as fh:
                        fh.write(json.dumps(row) + '\n')
                except OSError:
                    pass
            bk['order'] = bk['pos'] = None
        self.e.event(f'INTRADAY {p.symbol} data gap {self.last_t} -> {t_first_new}: open virtual trades UNKNOWN, '
                     'books restarted')
        self.last_t = None                                         # restart like a cold start (no history replay)

    # ------------------------------------------------------------------ one pass
    async def step(self) -> dict:
        import asyncio
        p = self.e.p
        local = self.clock()
        if self.server_offset_fn is None:
            from core.engine import fetch_server_offset
            self.server_offset_fn = fetch_server_offset
        if local - self.offset_t > 3600:                           # refresh the server-clock offset hourly
            try:
                self.offset = float(await asyncio.to_thread(self.server_offset_fn))
            except Exception:
                pass                                               # keep the last offset (0 at start)
            self.offset_t = local
        now = local + self.offset
        d = time.strftime('%Y-%m-%d', time.gmtime(now))           # UTC day, like the replay and the 4H clock
        if d != self.day:
            self.day, self.day_pnl = d, 0.0
        margin = p.intraday_close_margin_s
        if self.mode == 'off' or (self.last_t is not None and now < self.last_t + 120 + margin):
            return self.last                                       # the next 1m bar has not closed (+ margin)
        fetch = self.fetch
        if fetch is None:
            from core.engine import fetch_klines
            fetch = fetch_klines

        def fresh(rows):
            return [r for r in rows if r[5] <= now - margin]       # server clock: never the open candle
        cold = not self.store
        rows = fresh(await asyncio.to_thread(self._fetch_rows, fetch, 1500 if cold else 120))
        if not rows:
            return self.last
        if not cold and rows[0][0] > self.last_t + 60:             # more missed than the short page: full page
            rows = fresh(await asyncio.to_thread(self._fetch_rows, fetch, 1500))
            if rows and rows[0][0] > self.last_t + 60:
                self._gap(rows[0][0])
                self.store, cold = {}, True
        for r in rows:
            old = self.store.get(r[0])
            if old is not None and r[0] <= (self.last_t or -1) and tuple(old[:5]) != tuple(r[:5]):
                self.skew += 1                                     # a processed bar changed: it was still open
                self.e.event(f'INTRADAY {p.symbol} bar {r[0]:.0f} changed after processing (clock skew?) '
                             f'-> raise intraday_close_margin_s')
            self.store[r[0]] = r
        if cold and rows:                                          # back-fill one page: ADX / SESSION warm-up
            back = fresh(await asyncio.to_thread(self._fetch_rows, fetch, 1500, int(rows[0][0] * 1000) - 1))
            for r in back:
                self.store.setdefault(r[0], r)
        keys = sorted(self.store)[-int(p.intraday_store_bars):]
        self.store = {k: self.store[k] for k in keys}
        m = rows_to_m([self.store[k] for k in keys])
        if m is None or len(m['t']) < 600:
            self.last = {**self.last, 'status': 'volume UNKNOWN / warming up'}
            return self.last
        f = features(m)
        start = self.last_t if self.last_t is not None else m['t'][-2]
        new = np.flatnonzero(m['t'] > start)
        for i in new:
            self.on_bar(m, f, int(i))
        self.last_t = float(m['t'][-1])
        k = len(f['b5']['c']) - 1

        def num(x):
            return float(x) if k >= 0 and np.isfinite(x) else None
        self.last = {'mode': self.mode, 'variants': self.variants, 'day_pnl': float(self.day_pnl), 'n': self.n,
                     'bar_t': self.last_t, 'new_bars': int(len(new)), 'store_bars': len(keys), 'gaps': self.gaps,
                     'clock_skew_events': self.skew, 'server_offset_s': round(self.offset, 3),
                     'books': {n: {'order': bool(b['order']), 'pos': (b['pos'] or {}).get('side')}
                               for n, b in self.books.items()},
                     'vwap_session': num(f['vwap_SESSION'][k]), 'slope3': num(f['slope3'][k]),
                     'adx15': num(f['adx15'][k])}
        return self.last
