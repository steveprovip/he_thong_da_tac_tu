"""Lane 3: intraday 5m / 15m micro-swing (request 10-10 b).  Signals and execution model = tools/intraday_replay.py.

S1  VWAP-band mean reversion at each 5m close: the 5m low <= VWAP - 2 sigma (high >= VWAP + 2 sigma) AND CVD of the
    last minute with the reversal AND |EMA20(5m) slope| < 0.08 %; entry post-only at the 5m close for one minute;
    TP = VWAP, SL = 1 ATR(5m), skipped unless R:R >= 1.5.  VWAP = ROLL100 (100 x 1m) or SESSION (00:00 UTC).
S2  15m momentum continuation: EMA20(5m) slope over 3 bars >= 0.15 % AND ADX(15m) > 25 AND close beyond EMA20;
    post-only at EMA20, re-anchored at each 5m close while the trend holds; TP +60 / +100 bps; stop at the extreme
    of the last 2 closed 5m bars (1 ATR if within 5 bps), trailed to each new 5m bar.
Both: 45 min time barrier (taker), one position per symbol, the stop before the target inside a minute.

Modes (Profile.intraday_lane): 'off' | 'shadow' | 'active'.
  shadow  re-plays each newly CLOSED mainnet 1m kline (public REST) through exactly the replay's rules (fills only
          when a later bar trades THROUGH the limit) -> data/intraday_trades_shadow.jsonl.  No venue call.
  active  DEMO venue orders (post-only entry / TP, exchange closePosition stop, IOC time exit) ->
          data/intraday_trades.jsonl.  Refused (-> shadow) unless config/intraday_verdict.json marks the
          (symbol, strategy) ACTIVE-ELIGIBLE: HOLDOUT CI95 lower bound > 0 AND win rate > 50 % (replay 10-10 b:
          none qualifies).  Live venues / profiles are refused.
Coexistence (one-way mode = one net position per symbol): an active lane order / position sets e.busy so the trend
lane waits; no new entry while the symbol holds a trend / micro position or in the 10 min after a 4H close;
daily loss breaker intraday_daily_kill_usdt.
"""
from __future__ import annotations

import json
import math
import time
import uuid
from pathlib import Path
from typing import Optional

import numpy as np

VERDICT = Path(__file__).resolve().parents[1] / 'config' / 'intraday_verdict.json'
P = {'band_k': 2.0, 'mr_slope_max': 0.08, 'rr_min': 1.5, 'sl_atr': 1.0, 'trend_slope': 0.15, 'adx_min': 25.0,
     'hold_min': 45, 'sl_min_bps': 5.0, 'vwap_roll': 100, 'session_min': 60}
VARIANTS = {
    'S1_ROLL100': {'kind': 'S1', 'vwap': 'ROLL100'},
    'S1_SESSION': {'kind': 'S1', 'vwap': 'SESSION'},
    'S2_slope3_tp60': {'kind': 'S2', 'slope': 3, 'tp': 60.0},
    'S2_slope3_tp100': {'kind': 'S2', 'slope': 3, 'tp': 100.0},
    'S2_slope1_tp60': {'kind': 'S2', 'slope': 1, 'tp': 60.0},
    'S2_slope1_tp100': {'kind': 'S2', 'slope': 1, 'tp': 100.0},
}


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
    for kind in ('ROLL100', 'SESSION'):
        vw, sd = vwap_bands(m, kind)
        out[f'vwap_{kind}'], out[f'sd_{kind}'] = vw[il], sd[il]
    return out


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


def _fmt(x: float, step: float) -> str:
    n = max(0, -int(math.floor(math.log10(step)))) if step < 1 else 0
    return f'{round(x / step) * step:.{n}f}'


# ------------------------------------------------------------------ the lane
class IntradayLane:
    def __init__(self, e, ledger_dir: Path, fetch=None, clock=time.time, verdict_path: Path = VERDICT):
        p = e.p
        self.e, self.clock, self.fetch = e, clock, fetch
        self.variants = [v for v in p.intraday_variants if v in VARIANTS]
        self.mode = self._mode(p.intraday_lane, verdict_path)
        name = 'intraday_trades.jsonl' if self.mode == 'active' else 'intraday_trades_shadow.jsonl'
        self.ledger = Path(ledger_dir) / name
        self.last_t = None          # open time of the last processed 1m bar
        self.order = None           # {'var','side','px','sl','tp','expire_i'/'kind','cid'}
        self.pos = None             # {'var','side','entry','sl','tp','t','fill_t','qty','sl_cid','tp_cid'}
        self.day, self.day_pnl, self.n = None, 0.0, 0
        self.last = {'mode': self.mode}

    # ------------------------------------------------------------------ guards
    def _mode(self, want: str, verdict_path: Path) -> str:
        if want != 'active':
            return want
        e = self.e
        why = None
        if e.p.mode != 'demo' or 'MAINNET' in str(getattr(e.venue, 'label', '')):
            why = 'active intraday lane is demo-only'
        else:
            try:
                v = json.loads(Path(verdict_path).read_text(encoding='utf-8')).get(e.p.symbol, {})
            except (OSError, ValueError):
                v = {}
            ok = [x for x in self.variants if (v.get(VARIANTS[x]['kind']) or {}).get('variant') == x
                  and (v.get(VARIANTS[x]['kind']) or {}).get('verdict') == 'ACTIVE-ELIGIBLE']
            if not ok:
                why = f'no ACTIVE-ELIGIBLE replay verdict for {e.p.symbol} {self.variants}'
            else:
                self.variants = ok
        if why:
            e.event(f'INTRADAY lane active refused ({why}) -> shadow')
            return 'shadow'
        return 'active'

    def _free(self, t: float) -> Optional[str]:
        e, p = self.e, self.e.p
        if e.trend_owned or e.pos or (e.busy and not self._holding()):
            return 'symbol held by another lane'
        if t % 14400 < p.intraday_trend_quiet_s:
            return '4H close window reserved for the trend lane'
        if self.day_pnl <= -p.intraday_daily_kill_usdt:
            return f'daily loss breaker {self.day_pnl:+.2f} U'
        return None

    def _holding(self) -> bool:
        return self.mode == 'active' and (self.order is not None or self.pos is not None)

    # ------------------------------------------------------------------ ledger
    def _close(self, px: float, how: str, t: float, maker: bool) -> None:
        p, ps = self.e.p, self.pos
        gross = ps['side'] * (px - ps['entry']) / ps['entry'] * 1e4
        net = gross - p.maker_bps - (p.maker_bps if maker else p.taker_bps + p.intraday_slip_bps)
        usd = net / 1e4 * ps['qty'] * ps['entry']
        self.day_pnl += usd
        self.n += 1
        row = {'t': t, 'lane': 'INTRADAY', 'mode': self.mode, 'symbol': p.symbol, 'variant': ps['var'],
               'side': 'BUY' if ps['side'] > 0 else 'SELL', 'qty': ps['qty'], 'entry': ps['entry'], 'exit': px,
               'exit_how': how, 'hold_min': (t - ps['fill_t']) / 60, 'tp': ps['tp'], 'sl0': ps['sl0'],
               'gross_bps': gross, 'net_bps': net, 'net_usd': usd,
               'fees_model': f'maker {p.maker_bps:g} / taker {p.taker_bps:g} + {p.intraday_slip_bps:g} bps'}
        try:
            self.ledger.parent.mkdir(parents=True, exist_ok=True)
            with open(self.ledger, 'a', encoding='utf-8') as fh:
                fh.write(json.dumps(row, default=float) + '\n')
        except OSError:
            pass
        self.e.event(f'INTRADAY {"SHADOW " if self.mode == "shadow" else ""}{row["side"]} {p.symbol} {ps["var"]} '
                     f'{how} {net:+.1f} bps ({usd:+.3f} U)')
        self.pos = None
        if self.mode == 'active':
            self.e.busy = False

    def _qty(self, px: float) -> float:
        e, p = self.e, self.e.p
        wallet = float((e.account or {}).get('wallet') or p.mock_balance or 0.0)
        notional = min(p.intraday_notional_usdt, p.intraday_budget_pct * wallet) if wallet else p.intraday_notional_usdt
        step = (e.spec or {}).get('step', 0.001)
        q = math.floor(notional / px / step + 1e-9) * step
        return q if q * px >= (e.spec or {}).get('min_notional', 5.0) else 0.0

    # ------------------------------------------------------------------ bar-by-bar engine (shadow == replay)
    def on_bar(self, m: dict, f: dict, i: int) -> None:
        """Process CLOSED 1m bar i: exits, then the resting entry, then signals if it closes a 5m bar."""
        t = m['t'][i]
        o, h, l, c = m['o'][i], m['h'][i], m['l'][i], m['c'][i]
        b5 = f['b5']
        k5 = int(np.searchsorted(b5['i_last'], i))
        closes5 = k5 < len(b5['i_last']) and b5['i_last'][k5] == i
        if self.pos is not None:
            ps = self.pos
            first = ps['fill_i_t'] == t
            d = ps['side']
            if (d > 0 and l <= ps['sl']) or (d < 0 and h >= ps['sl']):
                self._close(ps['sl'] if first else (min(o, ps['sl']) if d > 0 else max(o, ps['sl'])), 'SL', t + 60, False)
            elif not first and ((d > 0 and h > ps['tp']) or (d < 0 and l < ps['tp'])):
                self._close(ps['tp'], 'TP', t + 60, True)
            elif t + 60 >= ps['fill_i_t'] + self.e.p.intraday_hold_min * 60:
                self._close(c, 'TIME', t + 60, False)
            elif closes5 and VARIANTS[ps['var']]['kind'] == 'S2':
                nb = b5['l'][k5] if d > 0 else b5['h'][k5]
                ps['sl'] = max(ps['sl'], nb) if d > 0 else min(ps['sl'], nb)
            return
        od = self.order
        if od is not None:
            if (od['side'] > 0 and l < od['px']) or (od['side'] < 0 and h > od['px']):
                self.order = None
                v = VARIANTS[od['var']]
                if v['kind'] == 'S2':
                    last_k = int(np.searchsorted(b5['t_close'], t, side='right')) - 1
                    sl = s2_stop(f, last_k, od['side'], od['px'])
                    tp = od['px'] * (1 + od['side'] * v['tp'] / 1e4)
                else:
                    sl, tp = od['sl'], od['tp']
                self.pos = {'var': od['var'], 'side': od['side'], 'entry': od['px'], 'sl': sl, 'sl0': sl, 'tp': tp,
                            'fill_t': t, 'fill_i_t': t, 'qty': od['qty']}
                self.on_bar(m, f, i)                      # the fill minute: only the stop can trigger
                return
            if VARIANTS[od['var']]['kind'] == 'S1' and t >= od['expire_t']:
                self.order = None                         # S1: one minute to fill, no chase
            elif VARIANTS[od['var']]['kind'] == 'S2' and closes5:
                sd = s2_trend(f, k5, VARIANTS[od['var']]['slope'])
                if sd != od['side']:
                    self.order = None
                else:
                    od['px'] = float(f['ema20'][k5])      # re-anchor at the new EMA20
            if self.order is not None or not closes5:
                return
        if not closes5 or self._free(t + 60):
            return
        for name in self.variants:
            v = VARIANTS[name]
            if v['kind'] == 'S1':
                sig = s1_signal(f, k5, v['vwap'])
                if sig:
                    side, entry, tp, sl = sig
                    self.order = {'var': name, 'side': side, 'px': float(entry), 'tp': float(tp), 'sl': float(sl),
                                  'expire_t': t + 60, 'qty': self._qty(entry)}
            else:
                sd = s2_trend(f, k5, v['slope'])
                if sd:
                    px = float(f['ema20'][k5])
                    self.order = {'var': name, 'side': sd, 'px': px, 'qty': self._qty(px)}
            if self.order is not None:
                if not self.order['qty']:
                    self.order = None
                    continue
                break

    # ------------------------------------------------------------------ one pass
    async def step(self) -> dict:
        import asyncio
        e = self.e
        now = self.clock()
        d = time.strftime('%Y-%m-%d', time.localtime(now))
        if d != self.day:
            self.day, self.day_pnl = d, 0.0
        if self.last_t is not None and now < self.last_t + 120 + 2:      # next 1m bar not closed yet
            if self.mode == 'active':
                await self._venue_sync()
            return self.last
        fetch = self.fetch
        if fetch is None:
            from core.engine import fetch_klines
            fetch = fetch_klines
        rows = await asyncio.to_thread(fetch, e.p.symbol, '1m', 1500)
        m = rows_to_m([r for r in rows if r[5] <= now])
        if m is None or len(m['t']) < 600:
            self.last = {**self.last, 'status': 'volume UNKNOWN / warming up'}
            return self.last
        f = features(m)
        new = np.flatnonzero(m['t'] > (self.last_t if self.last_t is not None else m['t'][-2]))
        if self.mode == 'shadow':
            for i in new:
                self.on_bar(m, f, int(i))
        else:
            await self._active_bars(m, f, new)
        self.last_t = float(m['t'][-1])
        k = len(f['b5']['c']) - 1
        self.last = {'mode': self.mode, 'variants': self.variants, 'order': self.order, 'pos': self.pos,
                     'day_pnl': self.day_pnl, 'n': self.n, 'bar_t': self.last_t,
                     'vwap': float(f['vwap_ROLL100'][k]) if k >= 0 and np.isfinite(f['vwap_ROLL100'][k]) else None,
                     'slope3': float(f['slope3'][k]) if k >= 0 and np.isfinite(f['slope3'][k]) else None,
                     'adx15': float(f['adx15'][k]) if k >= 0 and np.isfinite(f['adx15'][k]) else None}
        return self.last

    # ------------------------------------------------------------------ active venue path
    async def _active_bars(self, m, f, new) -> None:
        """Signals from the closed bars; orders, fills and stops on the DEMO venue."""
        e = self.e
        await self._venue_sync()
        if self.pos is not None or not len(new):
            if self.pos is not None and self.clock() >= self.pos['fill_t'] + e.p.intraday_hold_min * 60:
                await self._exit_ioc('TIME')
            elif self.pos is not None and VARIANTS[self.pos['var']]['kind'] == 'S2':
                await self._trail(f, m)
            return
        i = int(new[-1])
        b5 = f['b5']
        k5 = int(np.searchsorted(b5['i_last'], i))
        closes5 = k5 < len(b5['i_last']) and b5['i_last'][k5] == i
        if self.order is not None:
            v = VARIANTS[self.order['var']]
            if (v['kind'] == 'S1' and self.clock() >= self.order['expire_t']) or \
                    (v['kind'] == 'S2' and closes5 and s2_trend(f, k5, v['slope']) != self.order['side']):
                await self._cancel_entry()
            elif v['kind'] == 'S2' and closes5:
                await self._cancel_entry()
                await self._post_entry(self.order_template(v, f, k5, self.order['side']))
            return
        if not closes5 or self._free(m['t'][i] + 60):
            return
        save, self.order = self.order, None
        self.on_bar_signals(f, k5, m['t'][i])
        od, self.order = self.order, save
        if od:
            od['expire_t'] = self.clock() + 60
            await self._post_entry(od)

    def order_template(self, v, f, k5, side):
        """S2 entry at EMA20 with its stop / target fixed at posting time (the limit is the fill price)."""
        name = next(n for n, x in VARIANTS.items() if x is v)
        px = float(f['ema20'][k5])
        return {'var': name, 'side': side, 'px': px, 'qty': self._qty(px), 'sl': s2_stop(f, k5, side, px),
                'tp': px * (1 + side * v['tp'] / 1e4)}

    def on_bar_signals(self, f, k5, t):
        for name in self.variants:
            v = VARIANTS[name]
            if v['kind'] == 'S1':
                sig = s1_signal(f, k5, v['vwap'])
                if sig:
                    side, entry, tp, sl = sig
                    self.order = {'var': name, 'side': side, 'px': float(entry), 'tp': float(tp), 'sl': float(sl),
                                  'qty': self._qty(entry)}
            else:
                sd = s2_trend(f, k5, v['slope'])
                if sd:
                    self.order = self.order_template(v, f, k5, sd)
            if self.order is not None:
                if not self.order['qty']:
                    self.order = None
                    continue
                return

    async def _post_entry(self, od) -> None:
        e = self.e
        od['cid'] = 'gI-' + uuid.uuid4().hex[:28]
        try:
            await e.venue.limit_gtx(e.p.symbol, 'BUY' if od['side'] > 0 else 'SELL', _fmt(od['qty'], e.spec['step']),
                                    _fmt(od['px'], e.spec['tick']), od['cid'])
        except Exception:
            self.order = None
            return
        self.order = od
        e.busy = True

    async def _cancel_entry(self) -> float:
        e, od = self.e, self.order
        try:
            await e.venue.cancel(e.p.symbol, od['cid'])
        except Exception:
            pass
        st = await e.venue.order(e.p.symbol, od['cid'])
        got = float(st.get('filled') or 0.0)
        self.order = None
        if got:
            await self._on_fill(od, float(st.get('avg') or od['px']), got)
        else:
            e.busy = False
        return got

    async def _on_fill(self, od, px, qty) -> None:
        e = self.e
        sl, tp = od['sl'], od['tp']
        self.pos = {'var': od['var'], 'side': od['side'], 'entry': px, 'sl': sl, 'sl0': sl, 'tp': tp,
                    'fill_t': self.clock(), 'fill_i_t': None, 'qty': qty, 'sl_cid': None, 'tp_cid': None}
        close_side = 'SELL' if od['side'] > 0 else 'BUY'
        try:
            cid = 'gIS-' + uuid.uuid4().hex[:26]
            await e.venue.stop(e.p.symbol, close_side, _fmt(sl, e.spec['tick']), cid)
            self.pos['sl_cid'] = cid
        except Exception as exc:
            e.event(f'INTRADAY stop rejected ({type(exc).__name__}) -> closing')
            await self._exit_ioc('STOP-REJECTED')
            return
        try:
            cid = 'gIT-' + uuid.uuid4().hex[:26]
            await e.venue.limit_gtx(e.p.symbol, close_side, _fmt(qty, e.spec['step']), _fmt(tp, e.spec['tick']), cid)
            self.pos['tp_cid'] = cid
        except Exception:
            pass                                          # TP not resting: stop / time exit still apply
        e.busy = True

    async def _venue_sync(self) -> None:
        e = self.e
        if self.order is not None and self.order.get('cid'):
            st = await e.venue.order(e.p.symbol, self.order['cid'])
            if st.get('status') == 'FILLED':
                od, self.order = self.order, None
                await self._on_fill(od, float(st.get('avg') or od['px']), float(st.get('filled') or od['qty']))
        if self.pos is not None:
            ps = self.pos
            view = await e.venue.position_view(e.p.symbol)
            if not float(view.get('amt') or 0.0):         # the exchange stop or the TP closed it
                how = 'TP'
                px, maker = ps['tp'], True
                if ps.get('tp_cid'):
                    st = await e.venue.order(e.p.symbol, ps['tp_cid'])
                    if st.get('status') != 'FILLED':
                        how, px, maker = 'SL', ps['sl'], False
                        try:
                            await e.venue.cancel(e.p.symbol, ps['tp_cid'])
                        except Exception:
                            pass
                else:
                    how, px, maker = 'SL', ps['sl'], False
                if how == 'TP' and ps.get('sl_cid'):
                    try:
                        await e.venue.cancel_stop(ps['sl_cid'])
                    except Exception:
                        pass
                self._close(px, how, self.clock(), maker)

    async def _trail(self, f, m) -> None:
        e, ps = self.e, self.pos
        k = len(f['b5']['c']) - 1
        nb = f['b5']['l'][k] if ps['side'] > 0 else f['b5']['h'][k]
        better = (nb > ps['sl']) if ps['side'] > 0 else (nb < ps['sl'])
        if not better or not ps.get('sl_cid'):
            return
        close_side = 'SELL' if ps['side'] > 0 else 'BUY'
        try:
            await e.venue.cancel_stop(ps['sl_cid'])
        except Exception:
            pass
        cid = 'gIS-' + uuid.uuid4().hex[:26]
        try:
            await e.venue.stop(e.p.symbol, close_side, _fmt(nb, e.spec['tick']), cid)
            ps.update(sl=float(nb), sl_cid=cid)
        except Exception:
            await e.venue.stop(e.p.symbol, close_side, _fmt(ps['sl'], e.spec['tick']), cid)
            ps['sl_cid'] = cid

    async def _exit_ioc(self, how: str) -> None:
        e, ps = self.e, self.pos
        for cid, kind in ((ps.get('tp_cid'), 'tp'), (ps.get('sl_cid'), 'sl')):
            if not cid:
                continue
            try:
                await (e.venue.cancel(e.p.symbol, cid) if kind == 'tp' else e.venue.cancel_stop(cid))
            except Exception:
                pass
        view = await e.venue.position_view(e.p.symbol)
        amt = abs(float(view.get('amt') or 0.0))
        if not amt:
            self._close(ps['tp'], 'TP', self.clock(), True)
            return
        bk = e.book()
        ref = (bk.get('bid') if ps['side'] > 0 else bk.get('ask')) or ps['entry']
        avg, got = await e.venue.ioc(e.p.symbol, 'SELL' if ps['side'] > 0 else 'BUY', _fmt(amt, e.spec['step']),
                                     _fmt(ref * (1 - ps['side'] * 0.0015), e.spec['tick']), 'gI-' + uuid.uuid4().hex[:28])
        if got:
            self._close(avg, how, self.clock(), False)
        else:
            e.event(f'INTRADAY {e.p.symbol} IOC exit not filled -> retry next pass')
