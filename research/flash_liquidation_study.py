"""FLS-1 evaluator: flash-liquidation sniping on the recorded tape (offline: no orders, no network).

Spec, grid and acceptance are frozen in docs/research_upgrade/plan_flash_liquidation_sniping_2026_11.md.
Input = the output of `python tools/record_market.py --liq`:
    <root>/liquidations_<YYYYMMDD>.jsonl        !forceOrder@arr rows (+ the touch at that instant)
    <root>/<SYMBOL>/<YYYYMMDD-HH>.jsonl.gz      bookTicker (touch) + aggTrade (prints)
Everything is ordered by the local receive time `r`. Until 21 full UTC days are recorded the evaluator only reports
the data status (no outcome is computed, so the holdout cannot be peeked at).

    python -m research.flash_liquidation_study --root data/recordings            # status, or the full study
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import random
from collections import Counter, OrderedDict, defaultdict, deque
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np

from research.fast_liquidation_sniper import parse_force_order
from research.smc_fibo_backtest import daily_sharpe, micro_portfolio, stats, welch_lcb

ROOT = Path(__file__).resolve().parents[1]
SYMBOLS = ('BTCUSDT', 'ETHUSDT', 'SOLUSDT', 'DOGEUSDT', 'XRPUSDT', '1000PEPEUSDT')
MAKER_BPS, TAKER_BPS, SLIP_BPS = 2.0, 5.0, 1.0
WINDOW_S, CASCADE_USD, CASCADE_GAP_S = 5.0, 100_000.0, 60.0
SENSITIVITY_USD = (50_000.0, 250_000.0)
ATR_N = 14
K_GRID, R_GRID, G_GRID = (0.5, 1.0), (1.5, 2.0), ('G0', 'G1')
SL_ATR = 1.0
TTL_S, FLIP_WAIT_S, OFI_WIN_S, OFI_MIN = 60.0, 60.0, 5.0, 0.65
TIME_STOP_S = 900.0
MARKOUT_CHECKS_S = (1.0, 5.0)
REPORT_MARKOUTS_S = (1.0, 5.0, 30.0)
N_PLACEBO, PLACEBO_GAP_S = 5, 600.0
MIN_DAYS, TRAIN_DAYS = 21, 15
MIN_TRAIN_N, MIN_HOLDOUT_N, MIN_SYMBOL_N = 30, 100, 10
LOOK_BACK_S = (ATR_N + 6) * 60.0
LOOK_AHEAD_S = FLIP_WAIT_S + TTL_S + TIME_STOP_S + 60.0


@dataclass(frozen=True)
class Params:
    """Execution rules of one attempt. The defaults ARE the frozen FLS-1 values; FLS-1-CM's search (TRAIN only,
    docs/research_upgrade/plan_fls1_cm_history_2026_11.md section 6) varies time_stop_s and ttl_s."""
    ttl_s: float = TTL_S
    flip_wait_s: float = FLIP_WAIT_S
    flow_win_s: float = OFI_WIN_S
    flow_min: float = OFI_MIN
    time_stop_s: float = TIME_STOP_S
    sl_atr: float = SL_ATR

    @property
    def look_ahead_s(self) -> float:
        return self.flip_wait_s + self.ttl_s + self.time_stop_s + 60.0


DEFAULT = Params()


# ------------------------------------------------------------------------------------------------------ data
def utc_hour(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y%m%d-%H')


def utc_day(t: float) -> str:
    return dt.datetime.fromtimestamp(t, dt.timezone.utc).strftime('%Y%m%d')


def load_liquidations(root: Path) -> list:
    """forceOrder rows -> [{'r', 'symbol', 'side', 'notional', 'ap'}] by receive time; a broken line is skipped."""
    out = []
    for p in sorted(Path(root).glob('liquidations_*.jsonl')):
        with open(p, encoding='utf-8') as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except ValueError:
                    continue                                 # the line a killed recorder was writing
                ev = parse_force_order(rec.get('d') or {}, now=rec.get('r', 0) / 1e9)
                if ev and rec.get('r'):
                    out.append({'r': rec['r'] / 1e9, 'symbol': ev['symbol'], 'side': ev['side'],
                                'notional': ev['notional'], 'ap': ev['price']})
    out.sort(key=lambda x: x['r'])
    return out


class HourTape:
    """Prints and touches of one symbol, read per UTC hour file on demand (LRU), ordered by receive time.
    Prints keep the quantity and the aggressor (m = buyer is maker = a seller hit the bid) for the trade-flow
    imbalance and the trade-derived touch of tapes that carry no bookTicker (FLS-1-CM)."""

    def __init__(self, root: Path, sym: str, max_hours: int = 12):
        self.dir, self.max_hours = Path(root) / sym, max_hours
        self.cache: OrderedDict = OrderedDict()

    def _hour(self, hk: str):
        if hk in self.cache:
            self.cache.move_to_end(hk)
            return self.cache[hk]
        pr, pp, pq, pm, tr, tb, tbq, ta, taq = [], [], [], [], [], [], [], [], []
        p = self.dir / f'{hk}.jsonl.gz'
        if p.exists():
            try:
                with gzip.open(p, 'rt', encoding='utf-8') as f:
                    for line in f:
                        try:
                            if '"s":"aggTrade"' in line:
                                rec = json.loads(line)
                                d = rec['d']
                                pr.append(rec['r'] / 1e9)
                                pp.append(float(d['p']))
                                pq.append(float(d.get('q') or 0.0))
                                pm.append(bool(d.get('m')))
                            elif '"s":"bookTicker"' in line:
                                rec = json.loads(line)
                                d = rec['d']
                                tr.append(rec['r'] / 1e9)
                                tb.append(float(d['b']))
                                tbq.append(float(d['B']))
                                ta.append(float(d['a']))
                                taq.append(float(d['A']))
                        except (ValueError, KeyError, TypeError):
                            continue
            except (EOFError, OSError):
                pass                                         # truncated gzip of a killed run: keep complete lines
        o1, o2 = np.argsort(pr, kind='stable'), np.argsort(tr, kind='stable')
        got = (tuple(np.asarray(x)[o1] for x in (pr, pp, pq)) + (np.asarray(pm, dtype=bool)[o1],),
               tuple(np.asarray(x, dtype=float)[o2] for x in (tr, tb, tbq, ta, taq)))
        self.cache[hk] = got
        if len(self.cache) > self.max_hours:
            self.cache.popitem(last=False)
        return got

    def window(self, t0: float, t1: float):
        hours, h = [], math.floor(t0 / 3600)
        while h <= math.floor(t1 / 3600):
            hours.append(self._hour(utc_hour(h * 3600)))
            h += 1
        pt = [np.concatenate([x[0][i] for x in hours]) for i in range(4)]
        tt = [np.concatenate([x[1][i] for x in hours]) for i in range(5)]
        a, b = np.searchsorted(pt[0], t0, 'left'), np.searchsorted(pt[0], t1, 'right')
        c, d = np.searchsorted(tt[0], t0, 'left'), np.searchsorted(tt[0], t1, 'right')
        prints = tuple(x[a:b] for x in pt)
        if d > c:
            return Window(prints, tuple(x[c:d] for x in tt))
        return Window(prints, trade_touch(*prints), synthetic=True)


def trade_touch(pr, pp, pq, pm):
    """Touch from prints only (no book on the tape): bid = last seller-initiated print, ask = last buyer-initiated
    print, from the first instant both are known; a crossed pair (a stale side) collapses onto the latest print.
    Sizes are unknown (NaN): the level-1 OFI cannot be computed from it, the trade-flow imbalance is used instead."""
    n = len(pr)
    if n == 0:
        return tuple(np.zeros(0) for _ in range(5))
    idx = np.arange(n)
    last_sell = np.maximum.accumulate(np.where(pm, idx, -1))
    last_buy = np.maximum.accumulate(np.where(~pm, idx, -1))
    ok = (last_sell >= 0) & (last_buy >= 0)
    bid = np.where(ok, pp[np.maximum(last_sell, 0)], np.nan)
    ask = np.where(ok, pp[np.maximum(last_buy, 0)], np.nan)
    crossed = ok & (bid > ask)
    bid = np.where(crossed, pp, bid)
    ask = np.where(crossed, pp, ask)
    nan = np.full(n, np.nan)
    return pr[ok], bid[ok], nan[ok], ask[ok], nan[ok]


def recorded_days(root: Path, symbols) -> list:
    """UTC days with a liquidation file and >= 23 hour files for every symbol (a full day of tape)."""
    root = Path(root)
    days = sorted(p.stem.split('_', 1)[1] for p in root.glob('liquidations_*.jsonl'))
    out = []
    for d in days:
        if all(len(list((root / s).glob(f'{d}-*.jsonl.gz'))) >= 23 for s in symbols):
            out.append(d)
    return out


# ------------------------------------------------------------------------------------------------ features
class Window:
    """Prints (r, p[, q, m]) and touches (r, bid, bid qty, ask, ask qty) of one symbol in a time window.
    synthetic = the touches were derived from the prints (no book on the tape)."""

    def __init__(self, prints, touches, synthetic: bool = False):
        self.pr, self.pp = prints[0], prints[1]
        n = len(self.pr)
        self.pq = prints[2] if len(prints) > 2 else np.full(n, np.nan)
        self.pm = prints[3] if len(prints) > 3 else np.zeros(n, dtype=bool)
        self.tr, self.tb, self.tbq, self.ta, self.taq = touches
        self.synthetic = synthetic

    def tfi(self, t: float, win: float = OFI_WIN_S):
        """Trade-flow imbalance over (t - win, t]: (taker buy qty - taker sell qty) / total qty; no volume = None."""
        a, b = int(np.searchsorted(self.pr, t - win, 'right')), int(np.searchsorted(self.pr, t, 'right'))
        q, m = self.pq[a:b], self.pm[a:b]
        tot = float(np.nansum(q))
        if not tot > 0:
            return None
        return float(np.nansum(np.where(m, -q, q))) / tot

    def touch(self, t: float):
        i = int(np.searchsorted(self.tr, t, 'right')) - 1
        if i < 0:
            return None
        return float(self.tb[i]), float(self.ta[i])

    def mid(self, t: float):
        tc = self.touch(t)
        return None if tc is None else 0.5 * (tc[0] + tc[1])

    def atr(self, t: float, n: int = ATR_N):
        """ATR(n) of 1-minute bars of prints received BEFORE the minute that contains t; < n + 1 bars = None."""
        end = int(np.searchsorted(self.pr, math.floor(t / 60.0) * 60.0, 'left'))
        r, p = self.pr[:end], self.pp[:end]
        if len(r) == 0:
            return None
        m = np.floor(r / 60.0).astype(np.int64)
        starts = np.flatnonzero(np.r_[True, m[1:] != m[:-1]])
        if len(starts) < n + 1:
            return None
        ends = np.r_[starts[1:], len(m)]
        hi, lo = np.maximum.reduceat(p, starts), np.minimum.reduceat(p, starts)
        cl = p[ends - 1]
        hi, lo, cl = hi[-(n + 1):], lo[-(n + 1):], cl[-(n + 1):]
        tr_ = np.maximum(hi[1:], cl[:-1]) - np.minimum(lo[1:], cl[:-1])
        return float(tr_.mean())

    def ofi(self, t: float, win: float = OFI_WIN_S):
        """Level-1 order-flow imbalance over (t - win, t] (Cont, Kukanov, Stoikov 2014), sum e / sum |e| in [-1, 1].
        The touch just before the window is the reference of the first update. < 2 touches or no flow = None."""
        a = max(int(np.searchsorted(self.tr, t - win, 'right')) - 1, 0)
        b = int(np.searchsorted(self.tr, t, 'right'))
        if b - a < 2:
            return None
        bp, bq, ap_, aq = self.tb[a:b], self.tbq[a:b], self.ta[a:b], self.taq[a:b]
        e = ((bp[1:] >= bp[:-1]) * bq[1:] - (bp[1:] <= bp[:-1]) * bq[:-1]
             - (ap_[1:] <= ap_[:-1]) * aq[1:] + (ap_[1:] >= ap_[:-1]) * aq[:-1])
        s = float(np.abs(e).sum())
        return None if s <= 0 else float(e.sum()) / s


# ------------------------------------------------------------------------------------------------- cascades
def cascades(events: list, threshold: float = CASCADE_USD, window_s: float = WINDOW_S,
             gap_s: float = CASCADE_GAP_S) -> list:
    """First crossing of the same-side 5 s notional sum, once per cluster; a cluster ends after gap_s with no
    same-side event of that symbol. Sums are lower bounds (<= 1 pushed liquidation per symbol per second)."""
    out, state = [], {}
    for ev in events:
        key = (ev['symbol'], ev['side'])
        st = state.get(key)
        if st is None or ev['r'] - st['last'] > gap_s:
            st = state[key] = {'q': deque(), 'fired': False, 'start': ev['r'], 'last': ev['r']}
        st['last'] = ev['r']
        q = st['q']
        q.append((ev['r'], ev['notional']))
        while q and ev['r'] - q[0][0] >= window_s:
            q.popleft()
        total = sum(n for _, n in q)
        if not st['fired'] and total >= threshold:
            st['fired'] = True
            out.append({'r': ev['r'], 'symbol': ev['symbol'], 'side': ev['side'], 'p_liq': ev['ap'],
                        'cascade_usd': total, 'start': st['start'],
                        'sign': 1 if ev['side'] == 'SELL' else -1})      # forced SELL = longs liquidated -> buy
    return out


# ------------------------------------------------------------------------------------------------ simulation
def simulate(w: Window, t0: float, sign: int, p_ref: float, atr: float, k: float, r_mult: float, guard: str,
             e1: bool = True, P: Params = DEFAULT) -> dict:
    """One attempt (spec sections 4-6). Fills at the limit only on a print strictly through it.
    G1 reads the level-1 OFI of the book, or the trade-flow imbalance when the touch is trade-derived (FLS-1-CM)."""
    t_place = t0
    if guard == 'G1':
        flow = w.tfi if w.synthetic else w.ofi
        times = w.pr if w.synthetic else w.tr
        cand = [t0] + [float(x) for x in times[(times > t0) & (times <= t0 + P.flip_wait_s)]]
        t_place = next((t for t in cand if (o := flow(t, P.flow_win_s)) is not None and sign * o >= P.flow_min), None)
        if t_place is None:
            return {'outcome': 'NO_FLIP'}
    lim = p_ref - sign * k * atr
    tc = w.touch(t_place)
    if tc is None:
        return {'outcome': 'UNKNOWN_TOUCH'}
    if (sign > 0 and lim >= tc[1]) or (sign < 0 and lim <= tc[0]):
        return {'outcome': 'REJECT_CROSS'}
    i0 = int(np.searchsorted(w.pr, t_place, 'right'))
    i1 = int(np.searchsorted(w.pr, t_place + P.ttl_s, 'right'))
    thru = np.flatnonzero(sign * (w.pp[i0:i1] - lim) < 0)
    if len(thru) == 0:
        return {'outcome': 'MISSED', 't_place': t_place}
    j = i0 + int(thru[0])
    t_fill = float(w.pr[j])
    sl, tp = lim - sign * P.sl_atr * atr, lim + sign * r_mult * P.sl_atr * atr
    risk_bps = P.sl_atr * atr / lim * 1e4
    marks = {}
    for h in REPORT_MARKOUTS_S:
        m = w.mid(t_fill + h)
        marks[f'{h:g}s'] = None if m is None else sign * (m / lim - 1.0) * 1e4
    k_end = int(np.searchsorted(w.pr, t_fill + P.time_stop_s, 'right'))
    path = w.pp[j:k_end]                                     # the fill print itself can stop the trade out
    hit_sl = np.flatnonzero(sign * (path - sl) <= 0)
    hit_tp = np.flatnonzero(sign * (path[1:] - tp) > 0) + 1 if len(path) > 1 else np.zeros(0, int)
    t_sl = float(w.pr[j + hit_sl[0]]) if len(hit_sl) else math.inf
    t_tp = float(w.pr[j + hit_tp[0]]) if len(hit_tp) else math.inf
    t_e1, e1_px = math.inf, None
    if e1:
        for h in MARKOUT_CHECKS_S:
            tc_h, m = w.touch(t_fill + h), w.mid(t_fill + h)
            if m is not None and sign * (m - lim) < 0 and t_fill + h < min(t_sl, t_tp):
                t_e1, e1_px = t_fill + h, (tc_h[0] if sign > 0 else tc_h[1])
                break
    slip = lambda px: px * (1.0 - sign * SLIP_BPS / 1e4)
    first = min(t_sl, t_tp, t_e1)
    if first == math.inf:
        tc_t = w.touch(t_fill + P.time_stop_s)
        if tc_t is None:
            return {'outcome': 'UNKNOWN_TOUCH'}
        how, t_exit, px, fee = 'TIME', t_fill + P.time_stop_s, slip(tc_t[0] if sign > 0 else tc_t[1]), TAKER_BPS
    elif first == t_sl:                                      # a print through both at once is the stop
        how, t_exit, px, fee = 'SL', t_sl, slip(float(w.pp[j + hit_sl[0]])), TAKER_BPS
    elif first == t_e1:
        how, t_exit, px, fee = 'E1', t_e1, slip(e1_px), TAKER_BPS
    else:
        how, t_exit, px, fee = 'TP', t_tp, tp, MAKER_BPS
    gross = sign * (px / lim - 1.0) * 1e4
    return {'outcome': how, 't_place': t_place, 't_fill': t_fill, 't_exit': t_exit, 'entry': lim, 'exit': px,
            'gross_bps': gross, 'net_bps': gross - MAKER_BPS - fee, 'risk_bps': risk_bps, 'markouts': marks}


# --------------------------------------------------------------------------------------------------- driver
def variants():
    return [(k, r, g) for k in K_GRID for r in R_GRID for g in G_GRID]


def vkey(k, r, g, e1=True) -> str:
    return f'k{k:g}_R{r:g}_{g}' + ('' if e1 else '_E0')


def _new_state() -> dict:
    return {'busy': defaultdict(lambda: -math.inf), 'trades': [], 'counts': defaultdict(int)}


def _starts(casc: list) -> dict:
    starts = defaultdict(list)
    for c in casc:
        starts[c['symbol']].extend([c['start'], c['r']])
    return starts


def _attempt(c: dict, tape: HourTape, k, r_mult, guard, e1, P: Params, s: dict, rng, placebo: bool, avoid: list,
             w: Window | None = None) -> None:
    """One cascade for one variant: one order / position per symbol (s['busy']), result appended to s."""
    sym = c['symbol']
    if c['r'] < s['busy'][sym]:
        s['counts']['BUSY'] += 1
        return
    w = w if w is not None else tape.window(c['r'] - LOOK_BACK_S, c['r'] + P.look_ahead_s)
    atr = w.atr(c['r'])
    if atr is None or atr <= 0:
        s['counts']['UNKNOWN_ATR'] += 1
        return
    out = simulate(w, c['r'], c['sign'], c['p_liq'], atr, k, r_mult, guard, e1, P)
    s['counts'][out['outcome']] += 1
    if 't_fill' not in out:
        s['busy'][sym] = out.get('t_place', c['r']) + P.ttl_s
        return
    s['busy'][sym] = out['t_exit']
    row = {'symbol': sym, 'side': c['sign'], 'day': utc_day(c['r']), 'cascade_usd': c['cascade_usd'],
           't_fill': int(out['t_fill'] * 1000), 't_exit': int(out['t_exit'] * 1000), 'why': out['outcome'],
           'gross_bps': out['gross_bps'], 'net_bps': out['net_bps'], 'risk_bps': out['risk_bps'],
           'net_R': out['net_bps'] / out['risk_bps'], 'markouts': out['markouts'], 'placebo_net_bps': []}
    if placebo and rng is not None:
        row['placebo_net_bps'] = placebo_runs(tape, c, k, r_mult, guard, e1, rng, avoid, P)
    s['trades'].append(row)


def run_variant(root: Path, casc: list, k: float, r_mult: float, guard: str, e1: bool = True, rng=None,
                placebo: bool = True, all_casc: list | None = None, P: Params = DEFAULT,
                tapes: dict | None = None) -> dict:
    """One variant over the cascades. `tapes` (symbol -> HourTape) may be shared across calls to reuse the parsed
    hours (the FLS-1-CM search scores many variants on the same tape)."""
    tapes = {} if tapes is None else tapes
    s, starts = _new_state(), _starts(all_casc or casc)
    for c in casc:
        tape = tapes.setdefault(c['symbol'], HourTape(root, c['symbol']))
        _attempt(c, tape, k, r_mult, guard, e1, P, s, rng, placebo, starts[c['symbol']])
    return {'trades': s['trades'], 'counts': dict(s['counts'])}


def run_variants(root: Path, casc: list, specs: list, seed: int, placebo: bool = True, tapes: dict | None = None,
                 max_hours: int = 72) -> dict:
    """Several variants in ONE chronological sweep, so each hour of tape is parsed once (an archive of months does
    not fit a cache): specs = [(key, k, r_mult, guard, e1, P)]. Each variant keeps its own position state and its
    own placebo RNG (seed + index), so its result does not depend on the other variants."""
    tapes = {} if tapes is None else tapes
    states = {spec[0]: (_new_state(), random.Random(seed + i)) for i, spec in enumerate(specs)}
    starts = _starts(casc)
    for c in casc:
        tape = tapes.setdefault(c['symbol'], HourTape(root, c['symbol'], max_hours=max_hours))
        windows = {}
        for key, k, r_mult, guard, e1, P in specs:
            la = P.look_ahead_s
            if la not in windows:
                windows[la] = tape.window(c['r'] - LOOK_BACK_S, c['r'] + la)
            s, rng = states[key]
            _attempt(c, tape, k, r_mult, guard, e1, P, s, rng, placebo, starts[c['symbol']], windows[la])
    return {key: {'trades': s['trades'], 'counts': dict(s['counts'])} for key, (s, _) in states.items()}


def placebo_runs(tape: HourTape, c: dict, k, r_mult, guard, e1, rng, avoid: list, P: Params = DEFAULT) -> list:
    day0 = math.floor(c['r'] / 86_400) * 86_400
    out, tries = [], 0
    while len(out) < N_PLACEBO and tries < 50 * N_PLACEBO:
        tries += 1
        t = day0 + LOOK_BACK_S + rng.random() * (86_400 - LOOK_BACK_S - P.look_ahead_s)
        if any(abs(t - a) < PLACEBO_GAP_S for a in avoid):
            continue
        w = tape.window(t - LOOK_BACK_S, t + P.look_ahead_s)
        atr, mid = w.atr(t), w.mid(t)
        if not atr or mid is None:
            continue
        res = simulate(w, t, c['sign'], mid, atr, k, r_mult, guard, e1, P)
        if 't_fill' in res:
            out.append(res['net_bps'])
    return out


def summarize(trades: list, lo_ms: int, hi_ms: int) -> dict:
    rows = [x for x in trades if lo_ms <= x['t_fill'] < hi_ms]
    plc = [p for x in rows for p in x['placebo_net_bps']]
    per_sym = defaultdict(list)
    for x in rows:
        per_sym[x['symbol']].append(x['net_bps'])
    elig = {s: v for s, v in per_sym.items() if len(v) >= MIN_SYMBOL_N}
    mk = {h: [x['markouts'][h] for x in rows if x['markouts'].get(h) is not None] for h in ('1s', '5s', '30s')}
    return {'stats': stats([x['net_bps'] for x in rows], [x['risk_bps'] for x in rows]),
            'placebo': stats(plc), 'vs_placebo': welch_lcb([x['net_bps'] for x in rows], plc),
            'symbols_eligible': len(elig), 'symbols_positive': sum(1 for v in elig.values() if np.mean(v) > 0),
            'exits': dict(sorted(Counter(x['why'] for x in rows).items())),
            'markout_bps': {h: (round(float(np.mean(v)), 3) if v else None) for h, v in mk.items()},
            'daily': daily_sharpe(rows, lo_ms, hi_ms), 'micro': micro_portfolio(rows, lo_ms, hi_ms)}


def acceptance(train: dict, holdout_of) -> dict:
    elig = {k: v for k, v in train.items() if v['stats'].get('n', 0) >= MIN_TRAIN_N}
    if not elig:
        return {'verdict': 'INCONCLUSIVE', 'why': f'no variant with n >= {MIN_TRAIN_N} on TRAIN'}
    chosen = max(elig, key=lambda k: elig[k]['stats']['mean_net_bps'])
    h = holdout_of(chosen)
    n = h['stats'].get('n', 0)
    if n < MIN_HOLDOUT_N:
        return {'verdict': 'INCONCLUSIVE', 'chosen': chosen, 'why': f'HOLDOUT n {n} < {MIN_HOLDOUT_N}'}
    checks = {'lcb95>0': (h['stats'].get('lcb95') or -math.inf) > 0,
              'beats_placebo_lcb95>0': (h['vs_placebo'].get('lcb95') or -math.inf) > 0,
              'half_symbols_positive': h['symbols_eligible'] > 0 and 2 * h['symbols_positive'] >= h['symbols_eligible']}
    return {'verdict': 'PASS' if all(checks.values()) else 'FAIL', 'chosen': chosen, 'checks': checks}


def evaluate(root: Path, symbols=SYMBOLS, seed: int = 20261113) -> dict:
    root = Path(root)
    events = load_liquidations(root)
    days = recorded_days(root, symbols)
    casc = [c for c in cascades(events) if c['symbol'] in symbols]
    status = {'days_recorded': len(days), 'days_needed': MIN_DAYS, 'liquidation_events': len(events),
              'cascades_by_symbol': {s: sum(1 for c in casc if c['symbol'] == s) for s in symbols},
              'sensitivity_cascades': {f'{u:g}': len([c for c in cascades(events, u) if c['symbol'] in symbols])
                                       for u in SENSITIVITY_USD}}
    if len(days) < MIN_DAYS:
        return {'verdict': 'COLLECTING', 'status': status}        # no outcome before the data is complete
    days = days[:MIN_DAYS]
    edge = lambda d: int(dt.datetime.strptime(d, '%Y%m%d').replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
    t_lo, t_mid, t_hi = edge(days[0]), edge(days[TRAIN_DAYS]), edge(days[-1]) + 86_400_000
    casc = [c for c in casc if t_lo <= c['r'] * 1000 < t_hi]
    rng = random.Random(seed)
    train, runs = {}, {}
    for k, r, g in variants():
        key = vkey(k, r, g)
        runs[key] = run_variant(root, casc, k, r, g, True, rng)
        train[key] = summarize(runs[key]['trades'], t_lo, t_mid)
    verdict = acceptance(train, lambda key: summarize(runs[key]['trades'], t_mid, t_hi))
    out = {'verdict': verdict['verdict'], 'acceptance': verdict, 'status': status,
           'train': train, 'counts': {k: v['counts'] for k, v in runs.items()}}
    if verdict.get('chosen'):
        ch = verdict['chosen']
        out['holdout'] = summarize(runs[ch]['trades'], t_mid, t_hi)
        k, r, g = ch.split('_')
        e0 = run_variant(root, casc, float(k[1:]), float(r[1:]), g, False, None, placebo=False)
        out['holdout_E0_info_only'] = summarize(e0['trades'], t_mid, t_hi)['stats']
    return out


# ------------------------------------------------------------------ FLS-1-CM: historical archive (amendment, 2026-11-14)
# docs/research_upgrade/plan_fls1_cm_history_2026_11.md: same trading rules, COIN-M liquidation archive, fixed split.
SEARCH_SPACE = {'threshold': (50e3, 250e3, 'log'), 'window_s': (2.0, 10.0, 'lin'), 'k': (0.2, 1.5, 'lin'),
                'r_mult': (1.0, 3.0, 'lin'), 'time_stop_s': (120.0, 1800.0, 'log'), 'ttl_s': (10.0, 120.0, 'log'),
                'guard': ('G0', 'G1'), 'e1': (True, False)}


def day_start_ms(t: float) -> int:
    return int(math.floor(t / 86_400) * 86_400 * 1000)


def history_cascades(events: list, symbols, threshold: float, window_s: float) -> list:
    return cascades([e for e in events if e['symbol'] in symbols], threshold, window_s)


def evaluate_history(root: Path, symbols, split_ts: float, threshold: float = CASCADE_USD, window_s: float = WINDOW_S,
                     P: Params = DEFAULT, e1: bool = True, seed: int = 20261114, tapes: dict | None = None,
                     events: list | None = None, placebo: bool = True) -> dict:
    """The 8 pre-registered variants on the archive: every variant on both partitions (transparency), the verdict
    from the TRAIN choice read once on HOLDOUT (FLS-1 section 8 acceptance)."""
    events = load_liquidations(root) if events is None else events
    casc = history_cascades(events, symbols, threshold, window_s)
    if not casc:
        return {'verdict': 'NO_DATA', 'cascades': 0}
    lo, hi, mid = day_start_ms(casc[0]['r']), day_start_ms(casc[-1]['r']) + 86_400_000, int(split_ts * 1000)
    specs = [(vkey(k, r, g, e1), k, r, g, e1, P) for k, r, g in variants()]
    runs = run_variants(root, casc, specs, seed, placebo, tapes)
    out = {key: {'train': summarize(run['trades'], lo, mid), 'holdout': summarize(run['trades'], mid, hi),
                 'counts': run['counts']} for key, run in runs.items()}
    verdict = acceptance({k: v['train'] for k, v in out.items()}, lambda key: out[key]['holdout'])
    return {'verdict': verdict['verdict'], 'acceptance': verdict, 'variants': out, 'cascades': len(casc),
            'cascades_train': sum(1 for c in casc if c['r'] * 1000 < mid),
            'by_symbol': dict(Counter(c['symbol'] for c in casc)), 'span_ms': [lo, mid, hi]}


def mutate(cfg: dict, rng: random.Random) -> dict:
    key = rng.choice(sorted(SEARCH_SPACE))
    lo, hi, *kind = SEARCH_SPACE[key]
    new = dict(cfg)
    if key in ('guard', 'e1'):
        new[key] = hi if cfg[key] == lo else lo
    elif kind == ['log']:
        new[key] = min(hi, max(lo, cfg[key] * math.exp(rng.gauss(0.0, 0.35))))
    else:
        new[key] = min(hi, max(lo, cfg[key] + rng.gauss(0.0, 0.25 * (hi - lo))))
    return new


def score_cfg(root: Path, events: list, symbols, cfg: dict, lo_ts: float, hi_ts: float, tapes: dict,
              placebo_rng=None) -> dict:
    """Run one configuration on the cascades triggered in [lo_ts, hi_ts)."""
    casc = [c for c in history_cascades(events, symbols, cfg['threshold'], cfg['window_s']) if lo_ts <= c['r'] < hi_ts]
    P = replace(DEFAULT, time_stop_s=cfg['time_stop_s'], ttl_s=cfg['ttl_s'])
    run = run_variant(root, casc, cfg['k'], cfg['r_mult'], cfg['guard'], cfg['e1'], placebo_rng,
                      placebo_rng is not None, P=P, tapes=tapes)
    x = np.asarray([t['net_bps'] for t in run['trades']], float)
    n = len(x)
    return {'n': n, 'mean': float(x.mean()) if n else None,
            'se': float(x.std(ddof=1) / math.sqrt(n)) if n > 1 else None, 'trades': run['trades'],
            'counts': run['counts'], 'cascades': len(casc)}


def autoresearch(root: Path, symbols, split_ts: float, start: dict, rounds: int = 30, seed: int = 20261114,
                 events: list | None = None, tapes: dict | None = None, log=print) -> dict:
    """Atomic one-parameter mutations scored on TRAIN only; keep if TRAIN mean net bps improves with n >= 30;
    after the last round ONE HOLDOUT read of the final configuration (with placebos) + deflated Sharpe."""
    from core.fast_eval import dsr
    events = load_liquidations(root) if events is None else events
    tapes = {} if tapes is None else tapes
    rng = random.Random(seed)
    t_lo = events[0]['r'] if events else 0.0
    best = dict(start)
    s = score_cfg(root, events, symbols, best, t_lo, split_ts, tapes)
    hist = [{'round': 0, 'cfg': dict(best), 'n': s['n'], 'mean': s['mean'], 'se': s['se'], 'kept': True}]
    log(f"round 0: TRAIN n {s['n']} mean {s['mean']}")
    for i in range(1, rounds + 1):
        cand = mutate(best, rng)
        c = score_cfg(root, events, symbols, cand, t_lo, split_ts, tapes)
        better = (c['n'] >= MIN_TRAIN_N and c['mean'] is not None
                  and (s['mean'] is None or s['n'] < MIN_TRAIN_N or c['mean'] > s['mean']))
        hist.append({'round': i, 'cfg': cand, 'n': c['n'], 'mean': c['mean'], 'se': c['se'], 'kept': better})
        log(f"round {i}: {', '.join(f'{k}={v:.4g}' if isinstance(v, float) else f'{k}={v}' for k, v in cand.items())}"
            f" -> TRAIN n {c['n']} mean {None if c['mean'] is None else round(c['mean'], 2)} {'KEEP' if better else 'revert'}")
        if better:
            best, s = cand, c
    h = score_cfg(root, events, symbols, best, split_ts, math.inf, {}, placebo_rng=random.Random(seed + 1))
    rows = h['trades']
    x = np.asarray([t['net_bps'] for t in rows], float)
    plc = [p for t in rows for p in t['placebo_net_bps']]
    deflated = None
    if len(x) > 2 and x.std(ddof=1) > 0:
        sr = float(x.mean() / x.std(ddof=1))
        z = (x - x.mean()) / x.std(ddof=1)
        deflated = dsr(sr, len(x), float((z ** 3).mean()), float((z ** 4).mean()), 1.0 / len(x), len(hist) + 8)
    train_pos = [r for r in hist if r['n'] >= MIN_TRAIN_N and (r['mean'] or -1) > 0]
    return {'start': start, 'final': best, 'train_final': {k: s[k] for k in ('n', 'mean', 'se')},
            'holdout_final': {**stats(list(x), [t['risk_bps'] for t in rows]), 'counts': h['counts'],
                              'cascades': h['cascades']},
            'holdout_vs_placebo': welch_lcb(list(x), plc), 'deflated_sharpe': deflated,
            'trials_scored': len(hist) + 8, 'train_positive_configs': len(train_pos), 'history': hist,
            'holdout_trades': [{k: t[k] for k in ('symbol', 't_fill', 't_exit', 'why', 'net_bps', 'risk_bps', 'net_R')}
                               for t in rows]}


def run_history(root: Path, symbols, split_ts: float, rounds: int, seed: int = 20261114) -> dict:
    """FLS-1-CM: the 8 variants (both partitions), then the TRAIN-only search from the TRAIN-best variant."""
    events = load_liquidations(root)
    primary = evaluate_history(root, symbols, split_ts, events=events, seed=seed)   # one chronological sweep
    tapes = {s: HourTape(root, s, max_hours=2000) for s in symbols}   # search: TRAIN windows, parsed once
    out = {'amendment': 'docs/research_upgrade/plan_fls1_cm_history_2026_11.md', 'split_ts': split_ts,
           'symbols': list(symbols), 'primary': primary}
    train = {k: v['train']['stats'] for k, v in primary.get('variants', {}).items()}
    elig = {k: v for k, v in train.items() if v.get('n', 0) >= MIN_TRAIN_N}
    pick = max(elig, key=lambda k: elig[k]['mean_net_bps']) if elig else max(train, key=lambda k: train[k].get('n', 0))
    k_, r_, g_ = pick.split('_')[:3]
    start = {'threshold': CASCADE_USD, 'window_s': WINDOW_S, 'k': float(k_[1:]), 'r_mult': float(r_[1:]),
             'time_stop_s': TIME_STOP_S, 'ttl_s': TTL_S, 'guard': g_, 'e1': True}
    out['search'] = autoresearch(root, symbols, split_ts, start, rounds=rounds, seed=seed, events=events, tapes=tapes)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--root', type=Path, default=ROOT / 'data' / 'recordings')
    ap.add_argument('--symbols', default=','.join(SYMBOLS))
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'flash_liquidation_study.json')
    ap.add_argument('--history-split', default='', help='YYYY-MM-DD: FLS-1-CM archive mode (TRAIN before, HOLDOUT from)')
    ap.add_argument('--rounds', type=int, default=30)
    a = ap.parse_args(argv)
    syms = tuple(s.strip().upper() for s in a.symbols.split(',') if s.strip())
    if a.history_split:
        split = dt.datetime.fromisoformat(a.history_split).replace(tzinfo=dt.timezone.utc).timestamp()
        res = run_history(a.root, syms, split, a.rounds)
        a.out.write_text(json.dumps(res, indent=1, default=str), encoding='utf-8')
        print(json.dumps({'primary_verdict': res['primary'].get('verdict'), 'acceptance': res['primary'].get('acceptance'),
                          'search_final': res['search']['final'], 'search_holdout': res['search']['holdout_final'],
                          'deflated_sharpe': res['search']['deflated_sharpe']}, indent=1, default=str))
        return 0
    res = evaluate(a.root, syms)
    a.out.write_text(json.dumps(res, indent=1, default=str), encoding='utf-8')
    print(json.dumps({'verdict': res['verdict'], 'status': res['status'], 'acceptance': res.get('acceptance')}, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
