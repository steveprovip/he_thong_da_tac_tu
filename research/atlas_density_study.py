#!/usr/bin/env python3
"""ATLAS-K1: +15 m density forecasters on 1 m klines, tail trigger, carry overlay. OFFLINE research only.

Frozen in docs/research_upgrade/plan_atlas_density_2026_11.md BEFORE any kline outcome was computed (the SHA-256 of
the LF-normalised plan is in docs/research_upgrade/trial_log.jsonl, kind `atlas-k1-prereg`).

    python -m research.atlas_density_study                 # loop on TRAIN, then HOLDOUT / FRESH once -> data/
    python -m research.atlas_density_study --l2            # paired block SE of the L2 runs (descriptive)

The forecasters are the ones of scripts/autoresearch_agent_swarm.py (block bootstrap of the own lookback window plus a
family drift), ported to 1 m bars and vectorised over the decision points. The mutation loop scores TRAIN only; no
number of HOLDOUT or FRESH exists before it ends. Nothing here places an order, starts an engine or writes
config/live_tunables.json.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
import zipfile
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
for _p in (ROOT, ROOT / 'scripts'):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

import autoresearch_agent_swarm as ar  # noqa: E402

PLAN = 'docs/research_upgrade/plan_atlas_density_2026_11.md'
SYMBOLS = ('BTCUSDT', 'SOLUSDT', 'DOGEUSDT')
KLINES, FRESH_DIR = ROOT / 'data' / 'klines_1m', ROOT / 'data' / 'klines' / 'fresh'
H = 15                                                  # horizon, bars
WARMUP = 2880
QUANTILES = ar.QUANTILES
N_FUTURES = ar.N_FUTURES
SEED, ROUNDS = 20261114, 60
BOUNDS = {'lookback': (60, 2880, True), 'block': (1, 30, True), 'signal': (5, 2880, True),
          'coef': (-1.5, 1.5, False), 'vol_mult': (0.5, 2.0, False)}
PARTS = {'TRAIN': ('2024-12-01', '2026-01-01'), 'HOLDOUT': ('2026-01-01', '2026-10-01'),
         'FRESH': ('2026-10-01', '2026-11-13')}        # [start, end) of the decision time; the target ends inside
MIN, DAY = 60_000, 86_400_000
COSTS = {'MT': 8.5, 'TT': 13.0}                          # bps per round trip (plan section 3)
N_CELLS = 40
MICRO_NOTIONAL = 5.05                                   # U, the micro lot of the overlay sleeve
CHUNK_ELEMS = 6_000_000


def ms(day: str) -> int:
    return int(np.datetime64(day, 'ms').astype(np.int64))


@dataclass(frozen=True)
class KAgent:
    name: str
    family: str
    lookback: int
    block: int
    signal: int
    coef: float
    vol_mult: float = 1.0

    def clipped(self) -> 'KAgent':
        if self.family not in ar.FAMILIES:
            raise ValueError(f'unknown family {self.family!r}')
        v = {}
        for k, (lo, hi, integer) in BOUNDS.items():
            x = min(max(float(getattr(self, k)), lo), hi)
            v[k] = int(round(x)) if integer else x
        v['block'] = max(1, min(v['block'], v['lookback'] // 2))
        v['signal'] = min(v['signal'], v['lookback'])
        return replace(self, **v)


def default_population() -> list:
    spec = [('NORO-1', 'NORO', 1440, 5, 240, -0.3), ('NORO-2', 'NORO', 720, 3, 60, -0.2),
            ('NORO-3', 'NORO', 2880, 15, 720, -0.5), ('LUMEN-1', 'LUMEN', 1440, 5, 15, 0.3),
            ('LUMEN-2', 'LUMEN', 360, 1, 5, 0.2), ('LUMEN-3', 'LUMEN', 720, 3, 60, 0.4),
            ('TIDAL-1', 'TIDAL', 1440, 5, 60, 0.2), ('TIDAL-2', 'TIDAL', 2880, 15, 240, 0.3)]
    return [KAgent(*s).clipped() for s in spec]


B0 = KAgent('B0', 'TIDAL', 1440, 1, 60, 0.0, 1.0)


# ------------------------------------------------------------------------------------------------- data
def read_zip(path: Path) -> np.ndarray:
    """Binance kline zip -> rows [open ms, high, low, close, volume, taker buy base]; header optional."""
    import pandas as pd
    with zipfile.ZipFile(path) as zf:
        name = zf.namelist()[0]
        head = zf.open(name).readline()
        return pd.read_csv(zf.open(name), header=0 if not head[:1].isdigit() else None,
                           usecols=[0, 2, 3, 4, 5, 9]).to_numpy(np.float64)


def load_bars(symbol: str, klines: Path = KLINES, fresh: Path = FRESH_DIR) -> list:
    """Archive months joined to the fresh files -> gap-split ar.Bars (prefix sums ready)."""
    files = sorted(Path(klines).glob(f'{symbol}-1m-????-??.zip')) + sorted(Path(fresh).glob(f'{symbol}-1m-*.zip'))
    if not files:
        return []
    a = np.concatenate([read_zip(p) for p in files])
    return ar.make_bars(symbol, a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])


@dataclass
class Points:
    """Decision points of one symbol in one partition, concatenated over its gap-free runs."""
    symbol: str
    seg: np.ndarray           # index of the Bars run
    idx: np.ndarray           # bar index of the decision close inside that run
    y: np.ndarray             # ln(close[i + H] / close[i])
    day: np.ndarray           # UTC day number of the decision time
    t: np.ndarray             # decision time, ms
    up_fill: np.ndarray       # the next bar trades below the decision close (a resting buy fills)
    dn_fill: np.ndarray       # the next bar trades above it (a resting sell fills)


def decision_points(bars: list, symbol: str, t_lo: int, t_hi: int) -> Points:
    """Closes of bars opening at minute 14 / 29 / 44 / 59 with WARMUP gap-free bars behind and H ahead; the decision
    time (close of the bar) is in [t_lo, t_hi) and the target ends at or before t_hi."""
    cols = {k: [] for k in ('seg', 'idx', 'y', 'day', 't', 'up', 'dn')}
    for k, b in enumerate(bars):
        n = b.t_ms.size
        if n <= WARMUP + H:
            continue
        i = np.arange(WARMUP, n - H)
        dec = b.t_ms[i] + MIN
        ok = ((b.t_ms[i] // MIN) % 15 == 14) & (dec >= t_lo) & (dec + H * MIN <= t_hi)
        ok &= (b.t_ms[i + H] - b.t_ms[i] == H * MIN) & (b.t_ms[i] - b.t_ms[i - WARMUP] == WARMUP * MIN)
        i = i[ok]
        cols['seg'].append(np.full(i.size, k)); cols['idx'].append(i)
        cols['y'].append(b.logc[i + H] - b.logc[i]); cols['t'].append(b.t_ms[i] + MIN)
        cols['day'].append((b.t_ms[i] + MIN) // DAY)
        cols['up'].append(b.low[i + 1] < b.close[i]); cols['dn'].append(b.high[i + 1] > b.close[i])
    cat = {k: (np.concatenate(v) if v else np.zeros(0)) for k, v in cols.items()}
    return Points(symbol, cat['seg'].astype(np.int64), cat['idx'].astype(np.int64), cat['y'].astype(np.float64),
                  cat['day'].astype(np.int64), cat['t'].astype(np.int64), cat['up'].astype(bool),
                  cat['dn'].astype(bool))


# ------------------------------------------------------------------------------------------------- model
def uniforms(seed: int = SEED) -> np.ndarray:
    """(N_FUTURES, H + 1) fixed table, drawn column-major: any column prefix is shared across block sizes."""
    return np.random.default_rng(seed).random((H + 1, N_FUTURES)).T


def window_sd(b, i: np.ndarray, lb: int) -> tuple:
    """(mean, sd with ddof 0) of the lb 1 m returns ending at bar i."""
    s1 = b.cs_r[i + 1] - b.cs_r[i + 1 - lb]
    s2 = b.cs_r2[i + 1] - b.cs_r2[i + 1 - lb]
    mean = s1 / lb
    return mean, np.sqrt(np.maximum(s2 / lb - mean * mean, 0.0))


def signal(cfg: KAgent, b, i: np.ndarray, sd: np.ndarray) -> np.ndarray:
    """Family signal in [-1, 1] from bars <= i (the script's definitions, on bars)."""
    w = cfg.signal
    den = np.where(sd > 0, sd, 1e-12) * math.sqrt(w)
    if cfg.family == 'LUMEN':
        return (b.cs_imb[i + 1] - b.cs_imb[i + 1 - w]) / w
    if cfg.family == 'NORO':
        return np.tanh((b.logc[i] - (b.cs_logc[i + 1] - b.cs_logc[i + 1 - w]) / w) / den)
    return np.tanh((b.logc[i] - b.logc[i - w]) / den)


def forecast(cfg: KAgent, b, i: np.ndarray, u: np.ndarray) -> tuple:
    """-> (quantiles (n, 7), drift (n,), signal (n,)) of the H-bar log return at bars i, from bars <= i only."""
    lb, L = cfg.lookback, cfg.block
    mean, sd = window_sd(b, i, lb)
    m, rem = divmod(H, L)
    cols = m + (rem > 0)
    off = (u[:, :cols] * (lb - L + 1)).astype(np.int64)                 # (F, cols) block starts inside the window
    sig = signal(cfg, b, i, sd)
    drift = cfg.coef * sig * sd * math.sqrt(H)
    q = np.empty((i.size, QUANTILES.size))
    step = max(64, CHUNK_ELEMS // (N_FUTURES * cols))
    for lo in range(0, i.size, step):
        base = (i[lo:lo + step] - lb + 1)[:, None, None] + off[None]     # (n, F, cols) first return of each block
        sums = np.zeros(base.shape[:2])
        if m:
            sums += (b.cs_r[base[:, :, :m] + L] - b.cs_r[base[:, :, :m]]).sum(axis=2)
        if rem:
            sums += b.cs_r[base[:, :, m] + rem] - b.cs_r[base[:, :, m]]
        sums -= H * mean[lo:lo + step, None]                             # demeaned: drift comes from the signal only
        q[lo:lo + step] = np.quantile(cfg.vol_mult * sums + drift[lo:lo + step, None], QUANTILES, axis=1).T
    return q, drift, sig


def pinball(y: np.ndarray, q: np.ndarray) -> np.ndarray:
    return ar.pinball(y, q)


class Panel:
    """Bars and decision points of every symbol for one partition; forecasts are cached per configuration."""

    def __init__(self, bars: dict, part: str, u: np.ndarray):
        self.part, self.u, self.bars = part, u, bars
        lo, hi = (ms(d) for d in PARTS[part])
        self.pts = {s: decision_points(bars[s], s, lo, hi) for s in bars}
        self.n_calls = 0
        b0 = {s: self.predict(B0, s)[0] for s in bars}
        self.b0_loss = {s: pinball(self.pts[s].y, b0[s]) for s in bars}

    def predict(self, cfg: KAgent, s: str) -> tuple:
        p = self.pts[s]
        q = np.empty((p.idx.size, QUANTILES.size)); d = np.empty(p.idx.size); g = np.empty(p.idx.size)
        for k, b in enumerate(self.bars[s]):
            mask = p.seg == k
            if mask.any():
                q[mask], d[mask], g[mask] = forecast(cfg, b, p.idx[mask], self.u)
        self.n_calls += 1
        return q, d, g

    def fit(self, cfg: KAgent) -> dict:
        """-> {'q', 'drift', 'sig': per symbol, 'loss': per-point pinball per symbol, 'obj': equal-symbol mean of the
        loss relative to B0}."""
        out = {'q': {}, 'drift': {}, 'sig': {}, 'loss': {}}
        for s in self.bars:
            out['q'][s], out['drift'][s], out['sig'][s] = self.predict(cfg, s)
            out['loss'][s] = pinball(self.pts[s].y, out['q'][s])
        out['obj'] = float(np.mean([out['loss'][s].mean() / self.b0_loss[s].mean() for s in self.bars]))
        return out

    def ensemble(self, fits: dict, weights: dict, zero_coef: bool = False) -> dict:
        """Quantile-wise weighted average per symbol (zero_coef: every drift removed)."""
        wsum = sum(weights[n] for n in fits)
        return {s: sum(weights[n] * (fits[n]['q'][s] - (fits[n]['drift'][s][:, None] if zero_coef else 0.0))
                       for n in fits) / wsum for s in self.bars}

    def loss_bps(self, q: dict) -> dict:
        """Mean pinball in bps per symbol, the equal-symbol mean and the ratio to B0."""
        per = {s: float(pinball(self.pts[s].y, q[s]).mean() * 1e4) for s in self.bars}
        rel = float(np.mean([per[s] / (self.b0_loss[s].mean() * 1e4) for s in self.bars]))
        return {'per_symbol': per, 'mean': float(np.mean(list(per.values()))), 'rel_b0': rel}


# ------------------------------------------------------------------------------------------------- statistics
def cluster_se(contrib: np.ndarray, groups: np.ndarray) -> float:
    """SE of sum(contrib) when observations of one group (UTC day) move together: sqrt(G / (G - 1) x sum of squared
    deviations of the group sums from their mean)."""
    if contrib.size == 0:
        return math.nan
    _, inv = np.unique(groups, return_inverse=True)
    g = np.bincount(inv, weights=contrib)
    if g.size < 2:
        return math.nan
    return float(math.sqrt(g.size / (g.size - 1.0) * ((g - g.mean()) ** 2).sum()))


def mean_se(x: np.ndarray, groups: np.ndarray) -> tuple:
    """(mean, clustered SE) of a pooled sample."""
    if x.size == 0:
        return math.nan, math.nan
    return float(x.mean()), cluster_se(x / x.size, groups)


def equal_symbol(panel: Panel, per_point: dict) -> tuple:
    """(mean over symbols of each symbol's mean, SE clustered by day across the symbols)."""
    c, g = [], []
    for s, v in per_point.items():
        c.append(v / (v.size * len(per_point))); g.append(panel.pts[s].day)
    c, g = np.concatenate(c), np.concatenate(g)
    return float(c.sum()), cluster_se(c, g)


def qf(q: np.ndarray, p: float) -> np.ndarray:
    """Quantile function at probability p, linear between the knots of QUANTILES (p inside the knots)."""
    if not QUANTILES[0] <= p <= QUANTILES[-1]:
        raise ValueError(f'p {p} outside the knots')
    k = int(np.searchsorted(QUANTILES, p, side='right') - 1)
    if k >= QUANTILES.size - 1:
        return q[:, -1].copy()
    w = (p - QUANTILES[k]) / (QUANTILES[k + 1] - QUANTILES[k])
    return q[:, k] + w * (q[:, k + 1] - q[:, k])


def rule_sides(q: np.ndarray, c_bps: float, agree: np.ndarray, mu: np.ndarray) -> dict:
    """Side (+1 / -1 / 0) per decision point for every pre-registered rule; q in log-return units, c in bps."""
    c = c_bps * 1e-4
    m = q[:, 3]
    out = {'P85': np.where(qf(q, 0.15) > c, 1.0, np.where(qf(q, 0.85) < -c, -1.0, 0.0))}
    for p in (0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90):
        out[f'D_{p:.2f}'] = np.where(qf(q, 1.0 - p) > 0, 1.0, np.where(qf(q, p) < 0, -1.0, 0.0))
    for h in (0.25, 0.5, 1.0):
        out[f'EV_{h:g}'] = np.sign(m) * (np.abs(m) >= h * c)
    width = q[:, 6] - q[:, 0]
    a = np.where(width > 0, ((q[:, 6] - q[:, 3]) - (q[:, 3] - q[:, 0])) / np.where(width > 0, width, 1.0), 0.0)
    for thr in (0.05, 0.10, 0.20):
        out[f'TAIL_{thr:g}'] = np.sign(a) * (np.abs(a) >= thr)
    out['AGREE85'] = np.sign(m) * (agree >= 0.85)
    out['AGREE85_EV'] = np.sign(m) * ((agree >= 0.85) & (np.abs(m) >= 0.25 * c))
    for h in (0.25, 0.5, 1.0):
        out[f'STACK_EV_{h:g}'] = np.sign(mu) * (np.abs(mu) >= h * c)
    return out


def trade_stats(side: np.ndarray, pts: Points, fill: str, rng: np.random.Generator, draws: int = 200) -> dict:
    """Net bps of the trades of one rule on one set of points (several symbols may be concatenated)."""
    c = COSTS[fill]
    dec = side != 0
    filled = dec & (np.where(side > 0, pts.up_fill, pts.dn_fill) if fill == 'MT' else True)
    net = side[filled] * pts.y[filled] * 1e4 - c
    out = {'decisions': int(dec.sum()), 'n': int(net.size)}
    if not net.size:
        return {**out, 'mean_net_bps': None}
    mean, se = mean_se(net, pts.day[filled])
    win, loss = net[net > 0], net[net <= 0]
    mw, ml = (float(win.mean()) if win.size else 0.0), (float(-loss.mean()) if loss.size else 0.0)
    y_d, up_d, dn_d = pts.y[dec] * 1e4, pts.up_fill[dec], pts.dn_fill[dec]
    means = []
    for _ in range(0, draws, 20):                                        # 20 draws at a time: bounded memory
        rs = rng.choice([-1.0, 1.0], (20, y_d.size))
        rf = np.where(rs > 0, up_d, dn_d) if fill == 'MT' else np.ones_like(rs, bool)
        cnt = rf.sum(axis=1)
        means += list(((rs * y_d - c) * rf).sum(axis=1)[cnt > 0] / cnt[cnt > 0])
    plc = float(np.mean(means)) if means else math.nan
    sd = float(net.std(ddof=1)) if net.size > 1 else 0.0
    return {**out, 'mean_net_bps': mean, 'se': se, 'lcb95': mean - 1.645 * se, 'gross_mean_bps': mean + c,
            'win_rate': float((net > 0).mean()), 'mean_win_bps': mw, 'mean_loss_bps': ml,
            'payoff': mw / ml if ml > 0 else None, 'breakeven_win_rate': ml / (mw + ml) if mw + ml > 0 else None,
            'placebo_mean_bps': plc, 'vs_placebo_lcb95': mean - plc - 1.645 * se,
            'long_share': float((side[filled] > 0).mean()), 'sr_trade': mean / sd if sd > 0 else 0.0,
            'skew': float(((net - mean) ** 3).mean() / sd ** 3) if sd > 0 else 0.0,
            'kurt': float(((net - mean) ** 4).mean() / sd ** 4) if sd > 0 else 3.0}


def concat_points(panel: Panel) -> Points:
    p = [panel.pts[s] for s in panel.bars]
    return Points('ALL', *(np.concatenate([getattr(x, k) for x in p])
                           for k in ('seg', 'idx', 'y', 'day', 't', 'up_fill', 'dn_fill')))


def verdict(tr, ho, fr, need_dsr: bool) -> dict:
    """Plan section 3: EDGE / NO EDGE / NO OPPORTUNITY SET for one cell, read in the order TRAIN, HOLDOUT, FRESH."""
    from core.fast_eval import dsr
    n = ho['n']
    if n < 30:
        return {'verdict': 'NO OPPORTUNITY SET', 'holdout_n': n}
    d = dsr(ho['sr_trade'], n, ho['skew'], ho['kurt'], 1.0 / n, N_CELLS) if need_dsr else None
    checks = {'n>=100': n >= 100, 'lcb95>0': ho['lcb95'] > 0, 'beats_placebo_lcb95>0': ho['vs_placebo_lcb95'] > 0,
              'fresh_n>=20_and_mean>0': bool(fr['n'] >= 20 and fr['mean_net_bps'] > 0)}
    if need_dsr:
        checks['dsr>=0.95'] = bool(d is not None and d >= 0.95)
    return {'verdict': 'EDGE' if all(checks.values()) else 'NO EDGE', 'checks': checks, 'dsr': d}


# ------------------------------------------------------------------------------------------------- loop
def mutation_loop(train: Panel, agents: dict, rounds: int = ROUNDS, seed: int = SEED, log=print) -> dict:
    """The script's loop on TRAIN only: survivor weights, one parameter of the worst forecaster, keep iff its TRAIN
    objective falls. -> {'agents', 'fits', 'weights', 'history'}; history rows carry the population snapshot of the
    kept rounds so that HOLDOUT can be scored afterwards without having influenced anything."""
    fits = {n: train.fit(a) for n, a in agents.items()}
    weights = {n: 1.0 for n in agents}
    history = [{'round': 0, 'kept': None, 'mean_obj': float(np.mean([f['obj'] for f in fits.values()])),
                'ens_train': train.loss_bps(train.ensemble(fits, weights)), 'weights': dict(weights),
                'agents': {n: asdict(a) for n, a in agents.items()}}]
    for rnd in range(1, rounds + 1):
        obj = {n: fits[n]['obj'] for n in agents}
        weights = ar.survivor_update(weights, obj)
        worst = max(obj, key=lambda n: (obj[n], n))
        cand, pname = ar.mutate(agents[worst], np.random.default_rng([seed, rnd]), BOUNDS)
        new = train.fit(cand) if cand != agents[worst] else fits[worst]
        keep = new['obj'] < obj[worst] - 1e-12
        row = {'round': rnd, 'agent': worst, 'param': pname, 'old': getattr(agents[worst], pname),
               'new': getattr(cand, pname), 'obj_old': obj[worst], 'obj_new': new['obj'], 'kept': bool(keep)}
        if keep:
            agents = {**agents, worst: cand}
            fits = {**fits, worst: new}
            row['agents'] = {n: asdict(a) for n, a in agents.items()}
        row.update(mean_obj=float(np.mean([f['obj'] for f in fits.values()])),
                   ens_train=train.loss_bps(train.ensemble(fits, weights)), weights=dict(weights))
        history.append(row)
        log(f"r{rnd:03d} {worst:8s} {pname:9s} {row['old']:>9.4g} -> {row['new']:<9.4g} obj {row['obj_old']:.5f} -> "
            f"{row['obj_new']:.5f} {'KEEP' if keep else 'ROLLBACK'} | mean {row['mean_obj']:.5f} "
            f"ens {row['ens_train']['rel_b0']:.5f}")
    return {'agents': agents, 'fits': fits, 'weights': weights, 'history': history}


def replay_history(panel: Panel, history: list) -> list:
    """Ensemble loss of `panel` after every round, from the saved populations (reporting only)."""
    out, fits, cur = [], {}, {}
    for row in history:
        if 'agents' in row:
            for n, a in row['agents'].items():
                cfg = KAgent(**a)
                if cur.get(n) != cfg:
                    cur[n], fits[n] = cfg, panel.fit(cfg)
        out.append({'round': row['round'], **panel.loss_bps(panel.ensemble(fits, row['weights']))})
    return out


# ------------------------------------------------------------------------------------------------- reference
def ewma_scale(bars: list, pts: Points, halflife: float = 120.0) -> np.ndarray:
    """sqrt(H x EWMA of squared 1 m returns) at the decision bars (causal)."""
    import pandas as pd
    out = np.empty(pts.idx.size)
    for k, b in enumerate(bars):
        mask = pts.seg == k
        if mask.any():
            r = np.diff(b.logc, prepend=b.logc[0])
            v = pd.Series(r * r).ewm(halflife=halflife, adjust=False).mean().to_numpy()
            out[mask] = np.sqrt(H * v[pts.idx[mask]])
    return out


def sd_ref(bars: list, pts: Points, lb: int = 1440) -> np.ndarray:
    out = np.empty(pts.idx.size)
    for k, b in enumerate(bars):
        mask = pts.seg == k
        if mask.any():
            out[mask] = window_sd(b, pts.idx[mask], lb)[1]
    return out


# ------------------------------------------------------------------------------------------------- evaluation
def evaluate(panel: Panel, agents: dict, weights: dict, ref_z: np.ndarray, stack_beta: np.ndarray,
             top_thr, rng: np.random.Generator) -> dict:
    """Every metric of plan sections 2-3 on one partition. -> (report, per-rule sides for the overlay)."""
    fits = {n: panel.fit(a) for n, a in agents.items()}
    syms = list(panel.bars)
    eq = {n: 1.0 for n in agents}
    E, E0, Eeq = panel.ensemble(fits, weights), panel.ensemble(fits, weights, zero_coef=True), panel.ensemble(fits, eq)
    scale = {s: ewma_scale(panel.bars[s], panel.pts[s]) for s in syms}
    ref = {s: scale[s][:, None] * ref_z[None, :] for s in syms}
    sdr = {s: sd_ref(panel.bars[s], panel.pts[s]) for s in syms}
    loss = lambda q: {s: pinball(panel.pts[s].y, q[s]) for s in syms}              # noqa: E731
    lE, lE0, lEq, lR, lB = loss(E), loss(E0), loss(Eeq), loss(ref), panel.b0_loss
    diff = lambda a, b: tuple(round(v * 1e4, 5) for v in equal_symbol(panel, {s: a[s] - b[s] for s in syms}))  # noqa: E731
    rep = {'points': {s: int(panel.pts[s].idx.size) for s in syms},
           'days': int(np.unique(np.concatenate([panel.pts[s].day for s in syms])).size),
           'loss_bps': {'E': panel.loss_bps(E), 'E_equal_weights': panel.loss_bps(Eeq),
                        'E_zero_coef': panel.loss_bps(E0), 'REF_ewma': panel.loss_bps(ref),
                        'B0': {'per_symbol': {s: float(lB[s].mean() * 1e4) for s in syms},
                               'mean': float(np.mean([lB[s].mean() * 1e4 for s in syms])), 'rel_b0': 1.0},
                        'agents': {n: {'obj_rel_b0': fits[n]['obj'],
                                       'mean_bps': float(np.mean([fits[n]['loss'][s].mean() * 1e4 for s in syms]))}
                                   for n in agents}},
           'paired_bps_mean_se': {'E_minus_B0': diff(lE, lB), 'E_minus_REF': diff(lE, lR),
                                  'E_minus_E_zero_coef': diff(lE, lE0), 'E_minus_E_equal': diff(lE, lEq),
                                  'REF_minus_B0': diff(lR, lB)},
           'coverage': {}, 'direction': {}, 'cells': {}}
    yy = np.concatenate([panel.pts[s].y for s in syms])
    for name, q in (('E', E), ('REF_ewma', ref), ('B0', {s: panel.predict(B0, s)[0] for s in syms})):
        qq = np.concatenate([q[s] for s in syms])
        cov = (yy[:, None] <= qq).mean(axis=0)
        rep['coverage'][name] = {'quantiles': [round(float(v), 4) for v in cov],
                                 'central_50': round(float(cov[4] - cov[2]), 4),
                                 'central_90': round(float(cov[6] - cov[0]), 4),
                                 'width_90_bps': round(float((qq[:, 6] - qq[:, 0]).mean() * 1e4), 3)}
    # direction (M4)
    pall = concat_points(panel)
    m = np.concatenate([E[s][:, 3] for s in syms])
    z = np.abs(m) / np.concatenate([sdr[s] for s in syms])
    g_all = np.sign(m) * pall.y * 1e4
    top = z >= (top_thr if top_thr is not None else np.quantile(z, 0.9))
    rep['direction'] = {'corr_median_y': {s: float(np.corrcoef(E[s][:, 3], panel.pts[s].y)[0, 1]) for s in syms},
                        'gross_sign_bps_all': mean_se(g_all, pall.day),
                        'gross_sign_bps_top_decile': mean_se(g_all[top], pall.day[top]), 'top_n': int(top.sum()),
                        'median_abs_bps': {'mean': float(np.abs(m).mean() * 1e4),
                                           'p99': float(np.quantile(np.abs(m), 0.99) * 1e4),
                                           'max': float(np.abs(m).max() * 1e4)},
                        'top_threshold': float(top_thr if top_thr is not None else np.quantile(z, 0.9))}
    # trading cells (section 3)
    wsum = sum(weights.values())
    agree, mu, feats = {}, {}, {}
    for s in syms:
        sgn_m = np.sign(E[s][:, 3])
        agree[s] = sum(weights[n] * (np.sign(fits[n]['drift'][s]) == sgn_m) for n in agents) / wsum
        feats[s] = np.column_stack([np.ones(panel.pts[s].idx.size)] + [fits[n]['sig'][s] for n in agents])
        mu[s] = feats[s] @ stack_beta * sdr[s] * math.sqrt(H) if stack_beta is not None else np.zeros_like(sgn_m)
    qq, ag, mm = (np.concatenate([d[s] for s in syms]) for d in (E, agree, mu))
    sides = {}
    for fill, c in COSTS.items():
        for rule, side in rule_sides(qq, c, ag, mm).items():
            sides[f'{rule}|{fill}'] = side
            rep['cells'][f'{rule}|{fill}'] = trade_stats(side, pall, fill, rng)
    if stack_beta is not None:
        zt = pall.y / (np.concatenate([sdr[s] for s in syms]) * math.sqrt(H))
        fit = np.concatenate([feats[s] for s in syms]) @ stack_beta
        rep['stack'] = {'r2_vs_zero': float(1.0 - ((zt - fit) ** 2).sum() / (zt ** 2).sum()),
                        'corr': float(np.corrcoef(fit, zt)[0, 1])}
    return rep, {'sides': sides, 'pts': pall, 'feats': feats, 'sdr': sdr, 'scale': scale, 'E': E, 'fits': fits}


def fit_stack(extra: dict, panel: Panel) -> np.ndarray:
    """OLS on TRAIN of y / (sd1440 x sqrt(H)) on the 8 final signals + intercept, pooled."""
    x = np.concatenate([extra['feats'][s] for s in panel.bars])
    z = np.concatenate([panel.pts[s].y / (extra['sdr'][s] * math.sqrt(H)) for s in panel.bars])
    ok = np.isfinite(z) & np.isfinite(x).all(axis=1)
    return np.linalg.lstsq(x[ok], z[ok], rcond=None)[0]


# ------------------------------------------------------------------------------------------------- carry + overlay
def carry_rounds(lo: str = '2026-01-01', hi: str = '2026-09-30') -> dict:
    """XV HEDGE|H28|k2 from the funding cache (no network: a coin whose cache is incomplete is skipped), reduced to
    one slot: earliest entry first, then the larger signal; no overlap."""
    from tools import funding_spread_replay as fsr
    trades, skipped = [], []
    for c in fsr.COINS:
        sym, hl = f'{c}USDT', fsr.HL_NAME.get(c, c)
        names = {'bn': f'bnf_{sym}', 'hl': f'hlf_{hl}', 'px_bn': f'bnk_{sym}', 'px_other': f'hlk_{hl}'}
        if not all((fsr.CACHE / f'{v}.json').exists() for v in names.values()):
            skipped.append(c)
            continue
        ser = {k: fsr.arr(json.loads((fsr.CACHE / f'{v}.json').read_text())) for k, v in names.items()}
        if any(v is None for v in ser.values()):
            skipped.append(c)
            continue
        trades += fsr.simulate(ser, 'XV', c, 28, 2.0, 'HEDGE')
    full = fsr.summarize(trades, lo, hi)
    a, b = fsr.ms(lo) / 1000, (fsr.ms(hi) + DAY) / 1000
    one, end = [], -1.0
    for r in sorted((x for x in trades if a <= x['t'] < b), key=lambda x: (x['t'], -x['signal_bps'])):
        if r['t'] >= end:
            one.append(r)
            end = r['t'] + r['days'] * 86400
    span_days = (b - a) / 86400
    return {'all_slots': full, 'skipped_coins': skipped,
            'all_net_bps': [round(x['net_bps'], 3) for x in trades if a <= x['t'] < b],
            'one_slot': {'n': len(one), 'net_bps': [round(r['net_bps'], 3) for r in one],
                         'mean_net_bps': float(np.mean([r['net_bps'] for r in one])) if one else None,
                         'time_in_market': min(1.0, sum(r['days'] for r in one) / span_days),
                         'coins': sorted({r['coin'] for r in one})}}


def overlay_days(side: np.ndarray, pts: Points, fill: str) -> tuple:
    """One position at a time: among the symbols deciding at the same minute, the first that fills. -> (net bps per
    trade, UTC day of each trade, days of the partition)."""
    c = COSTS[fill]
    filled = (side != 0) & (np.where(side > 0, pts.up_fill, pts.dn_fill) if fill == 'MT' else True)
    order = np.flatnonzero(filled)
    order = order[np.argsort(pts.t[order], kind='stable')]
    first = np.r_[True, pts.t[order][1:] != pts.t[order][:-1]] if order.size else np.zeros(0, bool)
    keep = order[first]
    return side[keep] * pts.y[keep] * 1e4 - c, pts.day[keep], np.unique(pts.day)


def trajectory(carry_bps: list, time_in_market: float, carry_notional: float, ov_net: np.ndarray, ov_day: np.ndarray,
               days_all: np.ndarray, paths: int = 2000, seed: int = SEED, bankroll: float = 5.0) -> dict:
    """5 U under carry rounds (28 d, bootstrapped, booked at the end of each round) + overlay days (bootstrapped whole
    days of the partition). A path stops for good on the day its equity can no longer margin one micro lot at 5x
    (MICRO_NOTIONAL / 5 U): equity is never negative."""
    rng = np.random.default_rng(seed)
    floor = MICRO_NOTIONAL / 5.0
    per_day = np.zeros(days_all.size)
    if ov_net.size:
        np.add.at(per_day, np.searchsorted(days_all, ov_day), ov_net * 1e-4 * MICRO_NOTIONAL)
    cb = np.asarray(carry_bps, dtype=np.float64)
    out = {}
    for horizon in (30, 90, 365):
        inc = per_day[rng.integers(0, per_day.size, (paths, horizon))] if per_day.size else np.zeros((paths, horizon))
        k = horizon // 28
        if cb.size and k and carry_notional > 0:
            on = rng.random((paths, k)) < time_in_market
            inc[:, 27:28 * k:28] += cb[rng.integers(0, cb.size, (paths, k))] * on * 1e-4 * carry_notional
        path = bankroll + np.cumsum(inc, axis=1)
        dead = path < floor
        ruined = dead.any(axis=1)
        first = np.where(ruined, dead.argmax(axis=1), horizon - 1)
        eq = path[np.arange(paths), first]
        out[str(horizon)] = {'p05': round(float(np.quantile(eq, 0.05)), 4), 'median': round(float(np.median(eq)), 4),
                             'p95': round(float(np.quantile(eq, 0.95)), 4), 'mean': round(float(eq.mean()), 4),
                             'p_plus_10u': float((eq >= bankroll + 10).mean()), 'p_below_4u': float((eq < 4).mean()),
                             'p_ruin': float(ruined.mean()),
                             'median_days_to_ruin': float(np.median(first[ruined]) + 1) if ruined.any() else None}
    return out


def hybrid(extra_ho: dict, t1: str, t2, carry: dict) -> dict:
    from core.funding_carry import two_leg_capital_gate
    gate = two_leg_capital_gate(5.0, None, 'live')
    one = carry['one_slot']
    out = {'capital_gate_5u': {'mode': gate.mode, 'reason': gate.reason},
           'carry_one_slot': {k: one[k] for k in ('n', 'mean_net_bps', 'time_in_market', 'coins')},
           'carry_usdt_per_round_at_5u_per_leg': None if one['mean_net_bps'] is None else one['mean_net_bps'] * 5e-4,
           'sleeves': {}}
    pts = extra_ho['pts']
    days = np.unique(pts.day)
    none = (np.zeros(0), np.zeros(0, np.int64), days)
    ov = {'none': none, 'T1': overlay_days(extra_ho['sides'][t1], pts, t1.split('|')[1])}
    if t2:
        ov['T2'] = overlay_days(extra_ho['sides'][t2], pts, t2.split('|')[1])
    for name, (net, day, _) in ov.items():
        for cname, notional, rounds, tim in (('carry_not_fundable', 0.0, one['net_bps'], one['time_in_market']),
                                             ('carry_hyp_5u_one_slot', 5.0, one['net_bps'], one['time_in_market']),
                                             ('carry_hyp_5u_any_round', 5.0, carry['all_net_bps'], 1.0)):
            key = f'overlay_{name}+{cname}'
            out['sleeves'][key] = {
                'overlay_trades': int(net.size), 'overlay_trades_per_day': float(net.size / max(1, days.size)),
                'overlay_mean_net_bps': float(net.mean()) if net.size else None,
                'overlay_usdt_per_day': float(net.sum() * 1e-4 * MICRO_NOTIONAL / max(1, days.size)),
                'equity': trajectory(rounds, tim, notional, net, day, days)}
    return out


# ------------------------------------------------------------------------------------------------- L2 (descriptive)
def l2_paired(tag: str, patterns: list, seed: int = SEED, step_s: int = 30, holdout_frac: float = 0.3) -> dict:
    """Paired difference (ensemble - no-signal baseline) of the L2 run `tag` on its holdout, with a block SE over
    non-overlapping 15 min windows (30 points at a 30 s step)."""
    import glob
    rep = json.loads((ROOT / 'data' / f'atlas_l2_{tag}_report.json').read_text())
    agents = ar.load_state(ROOT / 'data' / f'atlas_l2_{tag}_agents.json')
    files = sorted({p for pat in patterns for p in glob.glob(str(ROOT / pat))})
    segs = []
    for p in files:
        segs += ar.load_segments(p, 30.0, str(ROOT / 'data' / 'npz'))
    pts = ar.eval_points(segs, step_s)
    hold = pts[int(round(len(pts) * (1.0 - holdout_frac))):]
    base = ar.AgentConfig('BASELINE', 'TIDAL', 1800, 60, 60, 0.0).clipped()
    w = rep['weights']
    d = np.empty(len(hold))
    for n, (k, i) in enumerate(hold):
        s = segs[k]
        y = s.logmid[i + ar.HORIZON_S] - s.logmid[i]
        qs = {a.name: ar.forecast(a, s.logmid, s.obi, i, ar.crn_uniforms(seed, k, i, -(-ar.HORIZON_S // a.block_s)))
              for a in agents}
        ens = sum(w[a] * q for a, q in qs.items()) / sum(w[a] for a in qs)
        qb = ar.forecast(base, s.logmid, s.obi, i, ar.crn_uniforms(seed, k, i, -(-ar.HORIZON_S // base.block_s)))
        d[n] = (ar.pinball(y, ens) - ar.pinball(y, qb)) * 1e4
    blk = ar.HORIZON_S // step_s
    bm = np.array([d[j:j + blk].mean() for j in range(0, d.size, blk)])
    return {'tag': tag, 'rounds': rep['rounds'], 'accepted': rep['accepted'], 'points': rep['eval_points'],
            'mean_loss_bps': rep['mean_loss_bps'], 'baseline_loss_bps': rep['baseline_loss_bps'],
            'holdout_windows_15m': int(bm.size), 'ensemble_minus_baseline_bps': float(d.mean()),
            'block_se': float(bm.std(ddof=1) / math.sqrt(bm.size)) if bm.size > 1 else None,
            'swarm_weights': rep['proposal']['swarm_weights']}


# ------------------------------------------------------------------------------------------------- run
def run(symbols=SYMBOLS, rounds: int = ROUNDS, seed: int = SEED, klines: Path = KLINES, fresh: Path = FRESH_DIR,
        log=print) -> dict:
    t0 = time.time()
    u = uniforms(seed)
    bars = {s: load_bars(s, klines, fresh) for s in symbols}
    log('bars', {s: [b.t_ms.size for b in v] for s, v in bars.items()}, f'{time.time() - t0:.0f} s')
    train = Panel(bars, 'TRAIN', u)
    log('TRAIN points', {s: p.idx.size for s, p in train.pts.items()})
    loop = mutation_loop(train, {a.name: a for a in default_population()}, rounds, seed, log)
    agents, weights = loop['agents'], loop['weights']
    t_loop = time.time() - t0
    # ---- everything below is computed after the loop has ended
    rng = np.random.default_rng([seed, 99])
    tr_rep0, tr_extra = evaluate(train, agents, weights, np.zeros(QUANTILES.size), None, None, rng)
    scale_tr = np.concatenate([tr_extra['scale'][s] for s in symbols])
    y_tr = np.concatenate([train.pts[s].y for s in symbols])
    ref_z = np.quantile(y_tr / scale_tr, QUANTILES)
    beta = fit_stack(tr_extra, train)
    top_thr = tr_rep0['direction']['top_threshold']
    reps, extras = {}, {}
    for part in PARTS:
        panel = train if part == 'TRAIN' else Panel(bars, part, u)
        reps[part], extras[part] = evaluate(panel, agents, weights, ref_z, beta, None if part == 'TRAIN' else top_thr,
                                            np.random.default_rng([seed, 99]))
        start = {a.name: panel.fit(a) for a in default_population()}
        e0 = panel.ensemble(start, {n: 1.0 for n in start})
        gain = {s: pinball(panel.pts[s].y, extras[part]['E'][s]) - pinball(panel.pts[s].y, e0[s]) for s in symbols}
        reps[part]['paired_bps_mean_se']['E_final_minus_E_round0'] = tuple(
            round(v * 1e4, 5) for v in equal_symbol(panel, gain))
        log(part, 'points', reps[part]['points'], 'E rel B0', round(reps[part]['loss_bps']['E']['rel_b0'], 5),
            f'{time.time() - t0:.0f} s')
        if part == 'HOLDOUT':
            progression_ho = replay_history(panel, loop['history'])
    # ---- tests, in the pre-registered order
    cells = lambda part: reps[part]['cells']                                        # noqa: E731
    t1 = 'P85|MT'
    eligible = {k: v for k, v in cells('TRAIN').items() if v['n'] >= 300}
    t2 = max(eligible, key=lambda k: eligible[k]['mean_net_bps']) if eligible else None
    tests = {'T1': {'cell': t1, **{p: cells(p)[t1] for p in PARTS},
                    **verdict(cells('TRAIN')[t1], cells('HOLDOUT')[t1], cells('FRESH')[t1], False)}}
    tests['T2'] = ({'cell': None, 'verdict': 'NO OPPORTUNITY SET'} if t2 is None else
                   {'cell': t2, **{p: cells(p)[t2] for p in PARTS},
                    **verdict(cells('TRAIN')[t2], cells('HOLDOUT')[t2], cells('FRESH')[t2], True)})
    carry = carry_rounds()
    out = {'plan': PLAN, 'seed': seed, 'rounds': rounds, 'symbols': list(symbols), 'quantiles': QUANTILES.tolist(),
           'costs_bps': COSTS, 'population_start': [asdict(a) for a in default_population()],
           'population_final': [asdict(a) for a in agents.values()], 'weights': weights,
           'accepted': int(sum(bool(r.get('kept')) for r in loop['history'])),
           'params_tried': {p: int(sum(r.get('param') == p for r in loop['history'])) for p in BOUNDS},
           'params_kept': {p: int(sum(r.get('param') == p and r.get('kept') for r in loop['history'])) for p in BOUNDS},
           'history': [{k: v for k, v in r.items() if k not in ('agents', 'weights')} for r in loop['history']],
           'progression_holdout': progression_ho, 'ref_z': ref_z.tolist(),
           'stack_beta': dict(zip(['intercept'] + list(agents), (float(v) for v in beta))),
           'partitions': reps, 'tests': tests, 'carry': {k: v for k, v in carry.items() if k != 'all_net_bps'},
           'hybrid': hybrid(extras['HOLDOUT'], t1, t2, carry),
           'proposal': ar.tunables_proposal(list(agents.values()), weights, time.time(),
                                            reason=f'ATLAS-K1 survivor weights after {rounds} rounds (research only)'),
           'seconds': {'loop': round(t_loop, 1), 'total': round(time.time() - t0, 1)}}
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--rounds', type=int, default=ROUNDS)
    ap.add_argument('--seed', type=int, default=SEED)
    ap.add_argument('--out', default=str(ROOT / 'data' / 'atlas_density_study.json'))
    ap.add_argument('--l2', action='store_true', help='paired block SE of the stored L2 runs, then exit')
    a = ap.parse_args(argv)
    if a.l2:
        runs = {'btcusdt': ['data/l2/BTCUSDT/*.jsonl'], 'solusdt': ['data/l2/SOLUSDT/*.jsonl'],
                'dogeusdt': ['data/l2/DOGEUSDT/*.jsonl'],
                'pooled': ['data/l2/BTCUSDT/*.jsonl', 'data/l2/SOLUSDT/*.jsonl', 'data/l2/DOGEUSDT/*.jsonl']}
        res = {t: l2_paired(t, p) for t, p in runs.items()}
        ar.atomic_write(ROOT / 'data' / 'atlas_l2_summary.json', (json.dumps(res, indent=1) + '\n').encode('utf-8'))
        for t, r in res.items():
            m = r['mean_loss_bps']
            print(f"{t:9s} acc {r['accepted']}/{r['rounds']} train {m['start']['train']:.4f} -> {m['end']['train']:.4f} "
                  f"holdout {m['start']['holdout']:.4f} -> {m['end']['holdout']:.4f} (baseline "
                  f"{r['baseline_loss_bps']['holdout']:.4f}) | ensemble - baseline {r['ensemble_minus_baseline_bps']:+.4f} "
                  f"+- {r['block_se']:.4f} bps over {r['holdout_windows_15m']} windows")
        return 0
    out = run(rounds=a.rounds, seed=a.seed)
    ar.atomic_write(Path(a.out), (json.dumps(out, indent=1, default=float) + '\n').encode('utf-8'))
    ar.write_proposal(ROOT / 'data' / 'atlas_k1_tunables_proposal.json', out['proposal'])
    for p, r in out['partitions'].items():
        e, pr = r['loss_bps'], r['paired_bps_mean_se']
        print(f"{p:8s} n {sum(r['points'].values())} | pinball bps E {e['E']['mean']:.4f} B0 {e['B0']['mean']:.4f} REF "
              f"{e['REF_ewma']['mean']:.4f} | E/B0 {e['E']['rel_b0']:.5f} | E-B0 {pr['E_minus_B0']} E-REF "
              f"{pr['E_minus_REF']} location {pr['E_minus_E_zero_coef']}")
    for k, t in out['tests'].items():
        print(k, t.get('cell'), t['verdict'], json.dumps({p: {x: t[p].get(x) for x in ('n', 'mean_net_bps', 'se')}
                                                         for p in PARTS if p in t}, default=float))
    print(f"wrote {a.out} ({out['seconds']['total']:.0f} s, loop {out['seconds']['loop']:.0f} s)")
    return 0


if __name__ == '__main__':
    sys.exit(main())
