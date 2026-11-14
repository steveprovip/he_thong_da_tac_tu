"""Pre-registered MLS-1 study: macro liquidity sweep at the London / New York opens (offline: no orders, no network).

Spec, variants, controls and acceptance are frozen in docs/research_upgrade/plan_macro_liquidity_sweep_2026_11.md.
    python -m research.macro_sweep_study                 # 11 symbols, TRAIN 2024-12..2025-12, HOLDOUT 2026-01..09
    python -m research.macro_sweep_study --rounds 0      # primary variants + OFF-session ablation only
Detection reuses the live SMCFibo state (H4 trend, H1 legs, 5 m fractals, taker-delta z). `MacroSweep` emits every
5 m bar inside the WIDEST searched zone of a live leg; a `Cfg` then filters candidates, so one detection pass serves
the primary variants, the ablation and the TRAIN-only search. Fills, stops and targets follow the conservative 1 m
rules of research/smc_fibo_backtest.py (trade-through fills, stop before target inside a bar, gap stops at the
open); a fee shield or a trailing stop only uses prices known before the bar that hits it.
`net_R` = net bps / (risk bps + 8): the multiple of the 0.05 U a micro account loses at the stop including costs.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import Counter
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Optional

import numpy as np

from core.fast_eval import dsr
from core.smc_fibo import Setup, SMCFibo
from research.smc_fibo_backtest import (HOLDOUT, KLINES, MAKER_BPS, N_PLACEBO, PLACEBO_DAYS, SLIP_BPS, SYMBOLS,
                                        TAKER_BPS, TRAIN, detect, load_1m, ms, resample, stats, tick_of, welch_lcb)

ROOT = Path(__file__).resolve().parents[1]
PLAN = 'docs/research_upgrade/plan_macro_liquidity_sweep_2026_11.md'
LONDON_MIN, NY_MIN = 7 * 60, 13 * 60 + 30                # session opens, minutes after 00:00 UTC
FILL_BARS = 15
STOP_COST_BPS = MAKER_BPS + TAKER_BPS + SLIP_BPS          # 8: maker in, taker stop, slip
RISK_MIN_BPS, RISK_MAX_BPS = 10.0, 91.0                   # 0.05 U lost at the 5.05 U minimum notional incl. costs
RR_MIN, TRAIL_ARM_BPS, STRUCT_BUF_ATR = 1.8, 100.0, 0.10
MIN_TRAIN_N, MIN_HOLDOUT_N = 50, 100
SCHEDULES = (('Binance VIP0 2/5 (primary)', 2.0, 5.0), ('Binance + BNB 1.8/4.5', 1.8, 4.5),
             ('Hyperliquid base 1.5/4.5', 1.5, 4.5), ('Hyperliquid top rebate -0.3/4.5', -0.3, 4.5),
             ('MEXC API 6/8', 6.0, 8.0), ('MEXC web 0/2', 0.0, 2.0), ('directive -1.5/4.5 (no such tier)', -1.5, 4.5),
             ('zero 0/0', 0.0, 0.0))
SPACE = {'zone_lo': (0.50, 0.80), 'zone_hi': (0.80, 1.00), 'leg_atr': (1.0, 4.0), 'delta_z': (0.0, 2.5),
         'clv': (0.30, 0.80), 'sess_h': (1.0, 4.0), 'hold_h': (4.0, 72.0), 'tp_lo': (100.0, 200.0),
         'tp_hi': (200.0, 500.0), 'trail': (0.0, 3.0), 'trigger': ('ABS', 'SWP'), 'stop': ('TIGHT', 'STRUCT'),
         'shield': (False, True)}


@dataclass(frozen=True)
class Cfg:
    trigger: str = 'ABS'           # 'ABS' absorption bar | 'SWP' absorption + sweep of a confirmed 5 m swing
    stop: str = 'TIGHT'            # 'TIGHT' trigger extreme -+ 0.1 ATR(5 m) | 'STRUCT' leg origin -+ 0.1 ATR(H1)
    zone_lo: float = 0.705
    zone_hi: float = 0.886
    leg_atr: float = 2.0
    delta_z: float = 1.0
    clv: float = 0.5
    sess_h: float = 3.0
    hold_h: float = 24.0
    tp_lo: float = 150.0
    tp_hi: float = 300.0
    shield: bool = False           # stop to entry + 8 bps once the trade has run 1 R in favour
    trail: float = 0.0             # x ATR(H1) behind the best price once +100 bps; < 0.5 = off
    session: bool = True           # False = OFF ablation: triggers outside both 3 h windows

    @property
    def key(self) -> str:
        return f'{self.trigger}_{self.stop}' + ('' if self.session else '_OFF')


PRIMARY = tuple(Cfg(trigger=t, stop=s) for t in ('ABS', 'SWP') for s in ('TIGHT', 'STRUCT'))
ABLATION = tuple(replace(c, session=False) for c in PRIMARY)


# ------------------------------------------------------------------------------------------------- detection
class MacroSweep(SMCFibo):
    """Candidate = a closed 5 m bar whose extreme retraces a live H1 leg (with the H4 trend) by WIDE_ZONE, leg >=
    WIDE_LEG_ATR x ATR(H1). Fields are side-normalised: `delta_z` <= -1 and `clv` >= 0.5 mean absorption for both
    sides; `swept` is the swept 5 m liquidity level or 0.0; `tp` is the leg extreme; `sl` the TIGHT stop."""
    WIDE_ZONE, WIDE_LEG_ATR = (SPACE['zone_lo'][0], SPACE['zone_hi'][1]), SPACE['leg_atr'][0]

    def _setup(self, bar, z: float, atr5: float) -> Optional[Setup]:
        atr1 = self.h1_atr.value
        if atr1 is None or bar.c <= 0 or atr1 / bar.c * 100.0 < self.min_atr_h1_pct:
            return None
        side = 1 if self.trend > 0 else -1
        lg, liq = (self.leg_long, self.liq_lo) if side > 0 else (self.leg_short, self.liq_hi)
        if lg is None or bar.t <= lg['t_end']:
            return None
        span = lg['hi'] - lg['lo']
        if span <= 0 or span < self.WIDE_LEG_ATR * atr1:
            return None
        ext = bar.l if side > 0 else bar.h
        rho = (lg['hi'] - ext) / span if side > 0 else (ext - lg['lo']) / span
        if not self.WIDE_ZONE[0] <= rho <= self.WIDE_ZONE[1]:
            return None
        rng = bar.h - bar.l
        clv = (bar.c - bar.l) / rng if rng > 0 else 0.5
        swept = liq is not None and (bar.l < liq < bar.c if side > 0 else bar.h > liq > bar.c)
        sl = ext - side * self.sl_buf * atr5
        return Setup(bar.t, 'BUY' if side > 0 else 'SELL', bar.c, sl, lg['hi'] if side > 0 else lg['lo'],
                     abs(bar.c - sl) / bar.c * 100.0, lg['lo'], lg['hi'], rho, liq if swept else 0.0,
                     side * z, clv if side > 0 else 1.0 - clv, atr1 / bar.c * 100.0)


def in_session(t_close_ms: int, hours: float = 3.0, bar_min: int = 5) -> bool:
    """True when the bar that CLOSES at t_close_ms opened inside `hours` after the London or the New York open."""
    m = (t_close_ms // 60_000 - bar_min) % 1440
    w = hours * 60.0
    return LONDON_MIN <= m < LONDON_MIN + w or NY_MIN <= m < NY_MIN + w


def passes(s: Setup, cfg: Cfg) -> bool:
    sess = in_session(s.t, cfg.sess_h) if cfg.session else not in_session(s.t, 3.0)
    span_atr = (s.leg_hi - s.leg_lo) / (s.atr_h1_pct / 100.0 * s.entry)
    return (sess and cfg.zone_lo <= s.retrace <= cfg.zone_hi and span_atr >= cfg.leg_atr
            and s.delta_z <= -cfg.delta_z and s.clv >= cfg.clv and (cfg.trigger == 'ABS' or s.swept > 0))


def plan(s: Setup, cfg: Cfg) -> Optional[dict]:
    """Stop, clipped target and eligibility of one candidate; None when the bracket breaks a frozen limit."""
    side = 1 if s.side == 'BUY' else -1
    atr1 = s.atr_h1_pct / 100.0 * s.entry
    sl = s.sl if cfg.stop == 'TIGHT' else (s.leg_lo if side > 0 else s.leg_hi) - side * STRUCT_BUF_ATR * atr1
    risk_bps = side * (s.entry - sl) / s.entry * 1e4
    tp_bps = min(max(side * (s.tp - s.entry) / s.entry * 1e4, cfg.tp_lo), cfg.tp_hi)
    if not RISK_MIN_BPS <= risk_bps <= RISK_MAX_BPS or tp_bps < RR_MIN * risk_bps:
        return None
    return {'side': side, 'entry': s.entry, 'sl': sl, 'tp': s.entry * (1.0 + side * tp_bps / 1e4), 'risk_bps': risk_bps,
            'tp_bps': tp_bps, 'trail_dist': cfg.trail * atr1 if cfg.trail >= 0.5 else 0.0}


# ----------------------------------------------------------------------------------------------- simulation
def simulate(m1: dict, t_signal: int, side: int, entry: float, sl: float, tp: float, tick: float, hold_bars: int,
             shield: bool = False, trail_dist: float = 0.0, trail_arm: float = 0.0, fill_bars: int = FILL_BARS) -> dict:
    """Conservative 1 m path of one order. With trail_dist 0 it equals research.smc_fibo_backtest.simulate (tested).
    The stop for a bar is raised only by prices of EARLIER bars: shield -> entry + 8 bps after +1 R, trail -> best
    price - trail_dist after +trail_arm. Prices are mirrored for shorts."""
    t = m1['t']
    i0 = int(np.searchsorted(t, t_signal, 'left'))
    if i0 >= len(t):
        return {'filled': False, 'why': 'NO_DATA'}
    s = 1.0 if side > 0 else -1.0
    o, h, l, c = (m1['o'], m1['h'], m1['l'], m1['c']) if s > 0 else (-m1['o'], -m1['l'], -m1['h'], -m1['c'])
    e, stop, take = s * entry, s * sl, s * tp
    risk = e - stop
    if risk <= 0:
        return {'filled': False, 'why': 'BAD_RISK'}
    i1 = min(len(t), i0 + fill_bars)
    through, tp_seen = l[i0:i1] <= e - tick, h[i0:i1] >= take + tick
    jt = int(np.argmax(through)) if through.any() else None
    jp = int(np.argmax(tp_seen)) if tp_seen.any() else None
    if jt is None or (jp is not None and jp <= jt):
        return {'filled': False, 'why': 'NO_FILL' if jt is None else 'TP_FIRST'}
    j = i0 + jt
    slip = lambda px: px - abs(px) * SLIP_BPS / 1e4
    if l[j] <= stop:                                      # stopped inside the fill bar (cannot take profit there)
        return _close(m1, j, j, s, entry, slip(min(o[j] if o[j] < e else stop, stop)), 'SL')
    k0, k1 = j + 1, min(len(t), j + 1 + hold_bars)
    if k0 >= k1:
        return {'filled': False, 'why': 'NO_DATA'}
    hh, ll, oo = h[k0:k1], l[k0:k1], o[k0:k1]
    lvl = np.full(len(hh), stop)
    be_lvl = e + abs(e) * STOP_COST_BPS / 1e4
    if shield or trail_dist > 0:
        prev = np.r_[-np.inf, np.maximum.accumulate(hh)[:-1]]     # best price known before each bar trades
        if shield:
            lvl = np.where(prev >= e + risk, np.maximum(lvl, be_lvl), lvl)
        if trail_dist > 0:
            lvl = np.where(prev >= e + trail_arm, np.maximum(lvl, prev - trail_dist), lvl)
    tp_hit, sl_hit = hh >= take + tick, ll <= lvl
    ks = int(np.argmax(sl_hit)) if sl_hit.any() else None
    kt = int(np.argmax(tp_hit)) if tp_hit.any() else None
    if ks is not None and (kt is None or ks <= kt):
        why = 'SL' if lvl[ks] <= stop else ('BE' if lvl[ks] == be_lvl else 'TRAIL')
        return _close(m1, j, k0 + ks, s, entry, slip(min(oo[ks], lvl[ks])), why)
    if kt is not None:
        return _close(m1, j, k0 + kt, s, entry, take, 'TP')
    return _close(m1, j, k1 - 1, s, entry, slip(c[k1 - 1]), 'TIME')


def _close(m1, j, k, s, entry, px_m, why) -> dict:
    gross = s * (s * px_m / entry - 1.0) * 1e4            # slip of a taker exit is already in px_m
    return {'filled': True, 'why': why, 't_fill': int(m1['t'][j]), 't_exit': int(m1['t'][k]) + 60_000,
            'gross_bps': gross, 'net_bps': net_of(gross, why)}


def net_of(gross_bps, why, maker: float = MAKER_BPS, taker: float = TAKER_BPS):
    """Net bps under a fee schedule: maker entry, maker exit only at the target, taker for every stop / time exit."""
    return gross_bps - maker - (maker if why == 'TP' else taker)


# ------------------------------------------------------------------------------------------------- one config
def load_symbol(symbol: str, root: Path = KLINES) -> dict:
    m1 = load_1m(symbol, root)
    return {'symbol': symbol, 'm1': m1, 'tick': tick_of(m1['c']), 't5': resample(m1, 5)['t'],
            'cands': detect(m1, MacroSweep())}


def run_cfg(sym: dict, cfg: Cfg, rng: Optional[random.Random] = None, t_hi: Optional[int] = None) -> list:
    """Trades of one configuration on one symbol: one position at a time, one FILLED trade per leg; an unfilled order
    rests FILL_BARS minutes. With `rng`, each trade carries N_PLACEBO random-time placebos of the same bracket.
    `t_hi` stops at the first candidate at / after it (the TRAIN-only search never simulates a HOLDOUT signal)."""
    m1, tick, hold = sym['m1'], sym['tick'], int(round(cfg.hold_h * 60))
    busy, done, rows = -1, set(), []
    for s in sym['cands']:
        if t_hi is not None and s.t >= t_hi:
            break
        leg = (s.side, s.leg_lo, s.leg_hi)
        if s.t < busy or leg in done or not passes(s, cfg):
            continue
        p = plan(s, cfg)
        if p is None:
            continue
        arm = s.entry * TRAIL_ARM_BPS / 1e4
        out = simulate(m1, s.t, p['side'], p['entry'], p['sl'], p['tp'], tick, hold, cfg.shield, p['trail_dist'], arm)
        if not out['filled']:
            busy = s.t + FILL_BARS * 60_000
            continue
        busy = out['t_exit']
        done.add(leg)
        row = {'symbol': sym['symbol'], 'side': p['side'], 't_signal': s.t, 'risk_bps': p['risk_bps'],
               'tp_bps': p['tp_bps'], 'net_R': out['net_bps'] / (p['risk_bps'] + STOP_COST_BPS),
               **{k: out[k] for k in ('t_fill', 't_exit', 'why', 'gross_bps', 'net_bps')}}
        if rng is not None:
            row['placebo_net_bps'] = _placebos(sym, s, p, cfg, hold, rng)
        rows.append(row)
    return rows


def _placebos(sym: dict, s: Setup, p: dict, cfg: Cfg, hold: int, rng: random.Random) -> list:
    m1, t5, out = sym['m1'], sym['t5'], []
    lo = int(np.searchsorted(t5, s.t - PLACEBO_DAYS * 86_400_000))
    hi = int(np.searchsorted(t5, s.t + PLACEBO_DAYS * 86_400_000)) - 1
    for _ in range(N_PLACEBO if hi > lo else 0):
        tq = int(t5[rng.randint(lo, hi)])
        k = int(np.searchsorted(m1['t'], tq, 'left')) - 1
        if k < 0:
            continue
        pe, side = float(m1['c'][k]), p['side']
        f = pe / p['entry']                               # same distances as fractions of price
        po = simulate(m1, tq, side, pe, pe * (1 - side * p['risk_bps'] / 1e4), pe * (1 + side * p['tp_bps'] / 1e4),
                      sym['tick'], hold, cfg.shield, p['trail_dist'] * f, pe * TRAIL_ARM_BPS / 1e4)
        if po['filled']:
            out.append(po['net_bps'])
    return out


# ------------------------------------------------------------------------------------------------- statistics
def split_rows(rows: list, split: tuple) -> list:
    lo, hi = ms(split[0]), ms(split[1])
    return [x for x in rows if lo <= x['t_fill'] < hi]


def deflated(net: list, n_trials: int) -> Optional[float]:
    """Deflated Sharpe of a per-trade net series: null cross-trial variance 1 / n, `n_trials` configurations tried."""
    x = np.asarray(net, float)
    if len(x) < 3 or x.std(ddof=1) <= 0:
        return None
    z = (x - x.mean()) / x.std(ddof=1)
    v = dsr(float(x.mean() / x.std(ddof=1)), len(x), float((z ** 3).mean()), float((z ** 4).mean()), 1.0 / len(x), n_trials)
    return None if v != v else float(v)


def reprice(rows: list) -> dict:
    out = {}
    for name, m, t in SCHEDULES:
        x = np.asarray([net_of(r['gross_bps'], r['why'], m, t) for r in rows], float)
        se = float(x.std(ddof=1) / math.sqrt(len(x))) if len(x) > 1 else None
        out[name] = {'n': len(x), 'mean_net_bps': round(float(x.mean()), 3) if len(x) else None,
                     'se': None if se is None else round(se, 3),
                     'lcb95': None if se is None else round(float(x.mean()) - 1.645 * se, 3)}
    return out


def summarize(rows: list, split: tuple) -> dict:
    r = split_rows(rows, split)
    days = (ms(split[1]) - ms(split[0])) / 86_400_000
    per = {}
    for x in r:
        per.setdefault(x['symbol'], []).append(x['net_bps'])
    plc = [p for x in r for p in x.get('placebo_net_bps', ())]
    return {'stats': stats([x['net_bps'] for x in r], [x['risk_bps'] + STOP_COST_BPS for x in r]),
            'gross': stats([x['gross_bps'] for x in r]), 'placebo': stats(plc),
            'vs_placebo': welch_lcb([x['net_bps'] for x in r], plc),
            'per_symbol': {k: {'n': len(v), 'mean_net_bps': round(float(np.mean(v)), 3)} for k, v in sorted(per.items())},
            'symbols_positive': sum(1 for v in per.values() if np.mean(v) > 0),
            'exits': dict(Counter(x['why'] for x in r)), 'trades_per_day': round(len(r) / days, 4),
            'mean_risk_bps': round(float(np.mean([x['risk_bps'] for x in r])), 2) if r else None,
            'mean_tp_bps': round(float(np.mean([x['tp_bps'] for x in r])), 2) if r else None,
            'schedules': reprice(r) if r else {}}


def acceptance(train: dict, holdout: dict, n_trials: int, holdout_net: dict) -> dict:
    """Section 9 of the plan. train / holdout: {variant key: summarize()} of the PRIMARY variants."""
    elig = {k: v for k, v in train.items() if v['stats'].get('n', 0) >= MIN_TRAIN_N}
    if not elig:
        return {'chosen': None, 'verdict': 'INCONCLUSIVE', 'why': f'no primary variant with TRAIN n >= {MIN_TRAIN_N}'}
    chosen = max(elig, key=lambda k: elig[k]['stats']['mean_net_bps'])
    h = holdout[chosen]
    d = deflated(holdout_net[chosen], n_trials)
    checks = {'lcb95>0': (h['stats'].get('lcb95') or -1e9) > 0,
              'beats_placebo_lcb95>0': (h['vs_placebo'].get('lcb95') or -1e9) > 0,
              'symbols_positive>=6': h['symbols_positive'] >= 6, 'dsr>=0.95': d is not None and d >= 0.95}
    enough = h['stats'].get('n', 0) >= MIN_HOLDOUT_N
    verdict = ('PASS' if enough else 'INCONCLUSIVE') if all(checks.values()) else 'FAIL'
    return {'chosen': chosen, 'verdict': verdict, 'checks': {f'n>={MIN_HOLDOUT_N}': enough, **checks},
            'deflated_sharpe': d, 'n_trials': n_trials}


# ----------------------------------------------------------------------------------------------------- search
def mutate(cfg: Cfg, rng: random.Random) -> Cfg:
    key = rng.choice(sorted(SPACE))
    lo, hi = SPACE[key]
    if isinstance(lo, (str, bool)):
        return replace(cfg, **{key: hi if getattr(cfg, key) == lo else lo})
    return replace(cfg, **{key: min(hi, max(lo, getattr(cfg, key) + rng.gauss(0.0, 0.25 * (hi - lo))))})


def score_train(syms: list, cfg: Cfg) -> dict:
    x = np.asarray([r['net_bps'] for s in syms for r in split_rows(run_cfg(s, cfg, t_hi=ms(TRAIN[1])), TRAIN)], float)
    n = len(x)
    return {'n': n, 'mean': float(x.mean()) if n else None, 'se': float(x.std(ddof=1) / math.sqrt(n)) if n > 1 else None}


def autoresearch(syms: list, start: Cfg, rounds: int, seed: int, n_before: int, log=print) -> dict:
    """One-parameter mutations scored on TRAIN only (kept if the TRAIN mean improves with n >= MIN_TRAIN_N), then
    ONE HOLDOUT read of the final configuration with placebos."""
    rng = random.Random(seed)
    best, s = start, score_train(syms, start)
    hist = [{'round': 0, 'cfg': asdict(best), **s, 'kept': True}]
    log(f"round 0: TRAIN n {s['n']} mean {s['mean']}")
    for i in range(1, rounds + 1):
        cand = mutate(best, rng)
        c = score_train(syms, cand)
        keep = c['n'] >= MIN_TRAIN_N and (s['n'] < MIN_TRAIN_N or c['mean'] > s['mean'])
        diff = {k: v for k, v in asdict(cand).items() if v != getattr(best, k)}
        hist.append({'round': i, 'cfg': asdict(cand), **c, 'kept': keep})
        log(f"round {i}: {diff} -> TRAIN n {c['n']} mean {None if c['mean'] is None else round(c['mean'], 2)} "
            f"{'KEEP' if keep else 'revert'}")
        if keep:
            best, s = cand, c
    rows = [r for sy in syms for r in run_cfg(sy, best, random.Random(seed + 1))]
    ho = split_rows(rows, HOLDOUT)
    trials = n_before + len(hist)
    return {'start': asdict(start), 'final': asdict(best), 'train_final': s, 'trials_scored': trials,
            'train_positive_configs': sum(1 for r in hist if r['n'] >= MIN_TRAIN_N and (r['mean'] or -1) > 0),
            'holdout_final': summarize(rows, HOLDOUT), 'deflated_sharpe': deflated([r['net_bps'] for r in ho], trials),
            'holdout_net_R': [round(r['net_R'], 4) for r in ho], 'history': hist}


def exit_grid(syms: list, base: Cfg, trials_before: int, seed: int = 20261114) -> dict:
    """Addendum after the search (its random draws never produced a live trail): TRAIN-only grid of the exit geometry
    around `base`. Only if a cell beats the base on TRAIN (n >= MIN_TRAIN_N) does that ONE cell get a HOLDOUT read."""
    cells = [{'trail': tr, 'shield': sh, **score_train(syms, replace(base, trail=tr, shield=sh))}
             for tr in (0.0, 1.0, 1.5, 2.0, 3.0) for sh in (False, True)]
    ref = next(c for c in cells if c['trail'] == base.trail and c['shield'] == base.shield)
    ok = [c for c in cells if c['n'] >= MIN_TRAIN_N and c['mean'] > ref['mean']]
    out = {'base': asdict(base), 'train_cells': cells, 'trials_scored': trials_before + len(cells) - 1, 'holdout_best': None}
    if ok:
        best = max(ok, key=lambda c: c['mean'])
        cfg = replace(base, trail=best['trail'], shield=best['shield'])
        rows = [r for sy in syms for r in run_cfg(sy, cfg, random.Random(seed + 2))]
        out['holdout_best'] = {'trail': best['trail'], 'shield': best['shield'], **summarize(rows, HOLDOUT),
                               'deflated_sharpe': deflated([r['net_bps'] for r in split_rows(rows, HOLDOUT)], out['trials_scored'])}
    return out


# ------------------------------------------------------------------------------------------------------ growth
def growth(net_r: list, trades_per_day: float, days: float = 30.0, paths: int = 2000, seed: int = 20261114) -> dict:
    """Section 11: bootstrap of HOLDOUT net R on a 5 U account (fixed 0.05 U at risk, and 1 % of equity)."""
    from tools.micro_growth_sim import monte_carlo
    if not net_r or trades_per_day <= 0:
        return {'trades': 0}
    kw = dict(n_hour=trades_per_day / 24.0, hours=days * 24.0, paths=paths, seed=seed)
    return {'trades_per_day': trades_per_day, 'mean_R': round(float(np.mean(net_r)), 4), 'n_R': len(net_r),
            'fixed_0.05U': monte_carlo(net_r, **kw), 'compound_1pct': monte_carlo(net_r, risk_usdt=None, risk_frac=0.01, **kw)}


# ------------------------------------------------------------------------------------------------------ driver
def run(symbols, rounds: int = 30, seed: int = 20261114, root: Path = KLINES, log=print) -> dict:
    t0, syms = time.time(), []
    for name in symbols:
        t1 = time.time()
        syms.append(load_symbol(name, root))
        log(f"{name}: {len(syms[-1]['m1']['t'])} 1m bars, {len(syms[-1]['cands'])} candidates ({time.time() - t1:.1f} s)")
    rng = random.Random(seed)
    rows = {c.key: [r for s in syms for r in run_cfg(s, c, rng)] for c in PRIMARY + ABLATION}
    summ = {split: {k: summarize(v, span) for k, v in rows.items()} for split, span in (('train', TRAIN), ('holdout', HOLDOUT))}
    n_trials = len(rows) + rounds + 1
    prim = [c.key for c in PRIMARY]
    ho_net = {k: [r['net_bps'] for r in split_rows(rows[k], HOLDOUT)] for k in prim}
    verdict = acceptance({k: summ['train'][k] for k in prim}, {k: summ['holdout'][k] for k in prim}, n_trials, ho_net)
    session_vs_off = {k: {sp: welch_lcb([r['net_bps'] for r in split_rows(rows[k], rg)],
                                        [r['net_bps'] for r in split_rows(rows[k + '_OFF'], rg)])
                          for sp, rg in (('train', TRAIN), ('holdout', HOLDOUT))} for k in prim}
    out = {'plan': PLAN, 'seed': seed, 'symbols': list(symbols), 'summary': summ, 'acceptance': verdict,
           'session_vs_off': session_vs_off}
    chosen = verdict.get('chosen')
    if chosen:
        ho = split_rows(rows[chosen], HOLDOUT)
        out['growth_chosen'] = growth([r['net_R'] for r in ho], summ['holdout'][chosen]['trades_per_day'], seed=seed)
        out['holdout_trades_chosen'] = [{k: r[k] for k in ('symbol', 'side', 't_fill', 't_exit', 'why', 'gross_bps',
                                                           'net_bps', 'risk_bps', 'tp_bps', 'net_R')} for r in ho]
    if chosen and rounds > 0:
        start = next(c for c in PRIMARY if c.key == chosen)
        out['search'] = autoresearch(syms, start, rounds, seed, len(rows), log)
        out['growth_search'] = growth(out['search']['holdout_net_R'],
                                      out['search']['holdout_final']['trades_per_day'], seed=seed)
    out['seconds'] = round(time.time() - t0, 1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--symbols', default=','.join(SYMBOLS))
    ap.add_argument('--rounds', type=int, default=30)
    ap.add_argument('--seed', type=int, default=20261114)
    ap.add_argument('--out', default=str(ROOT / 'data' / 'macro_sweep_study.json'))
    ap.add_argument('--exit-grid', action='store_true',
                    help='addendum: TRAIN-only trail x shield grid around the search final stored in --out')
    a = ap.parse_args(argv)
    names = [s.strip() for s in a.symbols.split(',') if s.strip()]
    if a.exit_grid:
        prev = json.loads(Path(a.out).read_text(encoding='utf-8'))['search']
        grid = exit_grid([load_symbol(n) for n in names], Cfg(**prev['final']), prev['trials_scored'], a.seed)
        Path(a.out).with_name('macro_sweep_exit_grid.json').write_text(json.dumps(grid, indent=1), encoding='utf-8')
        for c in grid['train_cells']:
            print(f"TRAIN trail {c['trail']:.1f} shield {c['shield']!s:5} n {c['n']:4d} mean {c['mean']:+7.2f} +- {c['se']:.2f}")
        print('HOLDOUT read:', json.dumps(grid['holdout_best']) if grid['holdout_best'] else 'none (no cell beat the base on TRAIN)')
        return 0
    out = run(names, a.rounds, a.seed)
    Path(a.out).write_text(json.dumps(out, indent=1), encoding='utf-8')
    for split in ('train', 'holdout'):
        for k, v in out['summary'][split].items():
            s, p = v['stats'], v['vs_placebo']
            print(f"{split:8s}{k:18s} n={s.get('n', 0):4d} win={s.get('win_rate')} net={s.get('mean_net_bps')}±{s.get('se')} "
                  f"lcb={s.get('lcb95')} R={s.get('mean_R')} gross={v['gross'].get('mean_net_bps')} "
                  f"vsPlacebo={p.get('diff')} (lcb {p.get('lcb95')}) sym+={v['symbols_positive']} exits={v['exits']}")
    print('ACCEPTANCE', json.dumps(out['acceptance']))
    print(f"wrote {a.out} ({out['seconds']:.0f} s)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
