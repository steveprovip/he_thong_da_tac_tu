#!/usr/bin/env python3
"""Karpathy-style autoresearch loop over 8 probabilistic forecasters (ATLAS pattern) - OFFLINE research only.

One round:
  1. score: walk-forward pinball loss of the +15 min log-return quantiles; each forecast is built from 400
     block-bootstrap futures of the agent's own lookback window plus its family signal, from data <= t only;
  2. survivor weights: top quartile x1.05, bottom quartile x0.95, clamped to [0.3, 2.5];
  3. atomic mutation: ONE parameter of the WORST agent, written to the experiment file and re-scored on train;
  4. keep it if the train loss improves (`git commit -- <state>` with --git), else roll back the experiment file
     to its committed bytes (checked with `git diff --quiet HEAD -- <state>`). Never `git reset --hard`: this work
     tree carries unrelated uncommitted work. --git also requires a branch named autoresearch/* (suggested:
     `git worktree add ../he_thong_autoresearch -b autoresearch/btcusdt`).
The last --holdout-frac of the time-ordered eval points is a time-separated holdout: scored and reported, never
used to accept a mutation.

Consensus integration is veto-only and leaves invariants I1-I8 intact: `tunables_proposal()` maps the survivor
weights to swarm_weights for NORO/LUMEN/TIDAL in the core/tunables.py schema and writes them to data/ (git-ignored),
never to config/live_tunables.json. Published by the owner, they reach the swarm only through
core.autonomy.Autonomy.demote, which can veto a trade but never add or upsize one. Gate weights, the 76.8 % / 70 %
thresholds, the RUNE veto and VESKA's HMAC AuthToken are not touched by this script.

Data: Binance USD-M recordings data/l2/<SYMBOL>/<YYYYMMDD>.jsonl (research/l2_recorder.py). bookTicker lines become
1 s bars of mid and top-of-book imbalance; gaps > --max-gap-s split segments. (ATLAS used Kraken BTC/USD with 300
levels; only the top of book is used here.)

  python scripts/autoresearch_agent_swarm.py --data "data/l2/BTCUSDT/*.jsonl" --rounds 30
"""
from __future__ import annotations

import argparse
import glob
import json
import math
import os
import subprocess
import sys
import time
from array import array
from dataclasses import asdict, dataclass, replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))                       # `core` when run as python scripts/<this>.py
QUANTILES = np.array([0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95])
HORIZON_S = 900
N_FUTURES = 400
FAMILIES = ('NORO', 'LUMEN', 'TIDAL')
BOUNDS = {'lookback_s': (300, 1800, True), 'block_s': (5, 300, True), 'signal_s': (30, 1800, True),
          'coef': (-1.5, 1.5, False), 'vol_mult': (0.5, 2.0, False)}       # name -> (lo, hi, integer)
WARMUP_S = BOUNDS['lookback_s'][1]
W_UP, W_DOWN, W_FLOOR, W_CAP = 1.05, 0.95, 0.3, 2.5
TUNABLE_CAP = 1.8                                       # core.tunables clips swarm_weights to [0, 1.8]
SCHEMA = 'autoresearch_agent_swarm/1'


@dataclass(frozen=True)
class AgentConfig:
    name: str
    family: str
    lookback_s: int
    block_s: int
    signal_s: int
    coef: float
    vol_mult: float = 1.0

    def clipped(self) -> 'AgentConfig':
        """Bounds, then block <= lookback/2 and signal <= lookback."""
        if self.family not in FAMILIES:
            raise ValueError(f'unknown family {self.family!r}')
        v = {}
        for k, (lo, hi, integer) in BOUNDS.items():
            x = min(max(float(getattr(self, k)), lo), hi)
            v[k] = int(round(x)) if integer else x
        v['block_s'] = min(v['block_s'], v['lookback_s'] // 2)
        v['signal_s'] = min(v['signal_s'], v['lookback_s'])
        return replace(self, **v)


def default_agents() -> list:
    """3 fair-value (NORO), 3 flow (LUMEN), 2 momentum (TIDAL) forecasters."""
    spec = [('NORO-1', 'NORO', 1800, 60, 900, -0.3), ('NORO-2', 'NORO', 900, 30, 300, -0.2),
            ('NORO-3', 'NORO', 1200, 120, 1200, -0.5), ('LUMEN-1', 'LUMEN', 1800, 60, 60, 0.3),
            ('LUMEN-2', 'LUMEN', 900, 15, 30, 0.2), ('LUMEN-3', 'LUMEN', 600, 30, 300, 0.4),
            ('TIDAL-1', 'TIDAL', 1800, 60, 300, 0.2), ('TIDAL-2', 'TIDAL', 1200, 30, 900, 0.3)]
    return [AgentConfig(*s).clipped() for s in spec]


# ----------------------------------------------------------------------------------------------- data
@dataclass
class Segment:
    t0: float                 # epoch second of bar 0
    logmid: np.ndarray        # log mid per 1 s bar, forward-filled (causal)
    obi: np.ndarray           # top-of-book imbalance (B - A) / (B + A)
    source: str


def bars_to_segments(secs, mids, obis, source: str, max_gap_s: float = 30.0) -> list:
    secs, mids, obis = (np.asarray(x) for x in (secs, mids, obis))
    if secs.size == 0:
        return []
    order = np.argsort(secs, kind='stable')
    secs, mids, obis = secs[order], mids[order], obis[order]
    last = np.r_[secs[1:] != secs[:-1], True]                   # last update inside each second wins
    secs, mids, obis = secs[last], mids[last], obis[last]
    cuts = np.flatnonzero(np.diff(secs) > max_gap_s) + 1
    out = []
    for lo, hi in zip(np.r_[0, cuts], np.r_[cuts, secs.size]):
        s = secs[lo:hi]
        idx = np.searchsorted(s, np.arange(s[0], s[-1] + 1), side='right') - 1
        out.append(Segment(float(s[0]), np.log(mids[lo:hi][idx]), obis[lo:hi][idx].astype(np.float64), source))
    return out


def load_segments(path, max_gap_s: float = 30.0, cache_dir=None) -> list:
    """bookTicker JSONL -> segments of 1 s bars. Cached as .npz keyed by file name, size and mtime."""
    path = Path(path)
    st = path.stat()
    cache = None
    if cache_dir:
        cache = Path(cache_dir) / f'autoresearch_{path.parent.name}_{path.stem}_{st.st_size}_{int(st.st_mtime)}.npz'
        if cache.exists():
            z = np.load(cache)
            return bars_to_segments(z['secs'], z['mids'], z['obis'], str(path), max_gap_s)
    secs, mids, obis = array('q'), array('d'), array('d')
    cur = None
    with open(path, 'r', encoding='utf-8', errors='replace') as f:
        for line in f:
            if '"bookTicker"' not in line:
                continue
            try:
                d = json.loads(line)
                b, a, bq, aq = float(d['b']), float(d['a']), float(d['B']), float(d['A'])
                s = int(math.floor(float(d['lt'])))
            except (ValueError, KeyError, TypeError):
                continue                                              # truncated / foreign line
            if not (0 < b < a and bq >= 0 and aq >= 0 and bq + aq > 0):
                continue
            if cur is not None and s != cur[0]:
                secs.append(cur[0]); mids.append(cur[1]); obis.append(cur[2])
            cur = (s, 0.5 * (b + a), (bq - aq) / (bq + aq))
    if cur is not None:
        secs.append(cur[0]); mids.append(cur[1]); obis.append(cur[2])
    s_np, m_np, o_np = np.frombuffer(secs, np.int64), np.frombuffer(mids), np.frombuffer(obis)
    if cache is not None and s_np.size:
        cache.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache, secs=s_np, mids=m_np, obis=o_np)
    return bars_to_segments(s_np, m_np, o_np, str(path), max_gap_s)


def eval_points(segments: list, step_s: int = 30) -> list:
    """(segment, bar) pairs with a full warm-up behind and the horizon ahead, in time order."""
    pts = [(segments[k].t0 + i, k, i) for k in range(len(segments))
           for i in range(WARMUP_S, segments[k].logmid.size - HORIZON_S, step_s)]
    return [(k, i) for _, k, i in sorted(pts)]


# ----------------------------------------------------------------------------------------------- model
def signal(cfg: AgentConfig, logmid, obi, i: int, sd: float) -> float:
    """Family signal in [-1, 1] from bars <= i."""
    w = cfg.signal_s
    sd = sd if sd > 0 else 1e-12
    if cfg.family == 'LUMEN':                                    # flow: mean top-of-book imbalance
        return float(np.mean(obi[i - w + 1:i + 1]))
    if cfg.family == 'NORO':                                     # fair value: distance from the window mean
        z = (logmid[i] - float(np.mean(logmid[i - w + 1:i + 1]))) / (sd * math.sqrt(w))
    else:                                                        # TIDAL: momentum z-score over the window
        z = (logmid[i] - logmid[i - w]) / (sd * math.sqrt(w))
    return math.tanh(z)


def crn_uniforms(seed: int, seg: int, i: int, cols: int) -> np.ndarray:
    """Common random numbers per eval point. Column-major draws make any column prefix identical across block
    sizes, so two configs are compared on the same futures."""
    return np.random.default_rng([seed, seg, i]).random((cols, N_FUTURES)).T


def forecast(cfg: AgentConfig, logmid, obi, i: int, u: np.ndarray) -> np.ndarray:
    """QUANTILES of the HORIZON_S log return at bar i, from bars <= i only."""
    lb, L = cfg.lookback_s, cfg.block_s
    r = np.diff(logmid[i - lb:i + 1])
    sd = float(np.std(r))
    r = r - r.mean()                                             # drift comes from the signal only
    c = np.concatenate(([0.0], np.cumsum(r)))
    m, rem = divmod(HORIZON_S, L)
    starts = (u[:, :m + (rem > 0)] * (lb - L + 1)).astype(np.int64)
    sums = (c[starts[:, :m] + L] - c[starts[:, :m]]).sum(axis=1)
    if rem:
        j = starts[:, m]
        sums = sums + (c[j + rem] - c[j])
    drift = cfg.coef * signal(cfg, logmid, obi, i, sd) * sd * math.sqrt(HORIZON_S)
    return np.quantile(cfg.vol_mult * sums + drift, QUANTILES)


def pinball(y, q) -> np.ndarray:
    """Mean pinball (quantile) loss over QUANTILES."""
    d = np.asarray(y, dtype=np.float64)[..., None] - q
    return np.maximum(QUANTILES * d, (QUANTILES - 1.0) * d).mean(axis=-1)


def score(cfg: AgentConfig, segments: list, points: list, seed: int) -> float:
    """Mean pinball loss in bps of log return over `points`."""
    if not points:
        return math.nan
    cols = -(-HORIZON_S // cfg.block_s)
    loss = np.empty(len(points))
    for n, (k, i) in enumerate(points):
        s = segments[k]
        q = forecast(cfg, s.logmid, s.obi, i, crn_uniforms(seed, k, i, cols))
        loss[n] = pinball(s.logmid[i + HORIZON_S] - s.logmid[i], q)
    return float(loss.mean() * 1e4)


def survivor_update(weights: dict, losses: dict) -> dict:
    """Top quartile (lowest loss) x1.05, bottom quartile x0.95, clamp [0.3, 2.5]; ties broken by name."""
    names = sorted(losses, key=lambda n: (losses[n], n))
    q = max(1, len(names) // 4)
    out = dict(weights)
    for n in names[:q]:
        out[n] = min(W_CAP, max(W_FLOOR, out[n] * W_UP))
    for n in names[-q:]:
        out[n] = min(W_CAP, max(W_FLOOR, out[n] * W_DOWN))
    return out


def mutate(cfg, rng: np.random.Generator, bounds=None):
    """One parameter, -> (candidate, parameter). A candidate equal to cfg means it is stuck at a bound."""
    bounds = bounds or BOUNDS
    name = list(bounds)[int(rng.integers(len(bounds)))]
    integer = bounds[name][2]
    old = getattr(cfg, name)
    for _ in range(16):
        new = old + rng.normal(0.0, 0.25) if name == 'coef' else old * math.exp(rng.normal(0.0, 0.35 if integer else 0.15))
        cand = replace(cfg, **{name: int(round(new)) if integer else float(new)}).clipped()
        if cand != cfg:
            return cand, name
    return cfg, name


# ----------------------------------------------------------------------------------------------- outputs
def atomic_write(path: Path, data: bytes) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_bytes(data)
    os.replace(tmp, path)


def state_bytes(agents: list) -> bytes:
    return (json.dumps({'schema': SCHEMA, 'agents': [asdict(a) for a in agents]}, indent=1) + '\n').encode('utf-8')


def load_state(path: Path):
    if not Path(path).exists():
        return None
    doc = json.loads(Path(path).read_text(encoding='utf-8'))
    if doc.get('schema') != SCHEMA:
        raise SystemExit(f'{path}: schema {doc.get("schema")!r} != {SCHEMA!r}')
    return [AgentConfig(**a).clipped() for a in doc['agents']]


def tunables_proposal(agents: list, weights: dict, now: float, ttl_s: float = 86400.0, reason: str = '') -> dict:
    """Survivor weights -> core/tunables.py document: alpha agents only, mean-normalised, clipped to [0, 1.8]."""
    fam = {}
    for a in agents:
        fam.setdefault(a.family, []).append(weights[a.name])
    mean_all = sum(weights[a.name] for a in agents) / len(agents)
    sw = {f: round(min(TUNABLE_CAP, max(0.0, (sum(v) / len(v)) / mean_all)), 4)
          for f, v in sorted(fam.items()) if f in FAMILIES}
    return {'version': 1, 'source': 'autoresearch_agent_swarm', 'reason': reason[:200],
            'expires_at': now + ttl_s, 'swarm_weights': sw}


def write_proposal(path, doc: dict) -> None:
    if Path(path).name == 'live_tunables.json':
        raise ValueError('proposal only: publishing config/live_tunables.json is an owner action')
    atomic_write(Path(path), (json.dumps(doc, indent=1) + '\n').encode('utf-8'))


class GitGate:
    """Commit or roll back ONE experiment file. Other paths, staged or not, are never touched."""

    def __init__(self, state: Path, enabled: bool, branch_prefix: str = 'autoresearch/'):
        self.state, self.enabled, self.prefix = Path(state).resolve(), enabled, branch_prefix
        self.repo, self.rel = None, None

    def _git(self, *args, check=True):
        return subprocess.run(['git', '-C', str(self.repo or self.state.parent), *args],
                              capture_output=True, text=True, check=check)

    def preflight(self) -> None:
        if not self.enabled:
            return
        self.state.parent.mkdir(parents=True, exist_ok=True)
        top = self._git('rev-parse', '--show-toplevel', check=False)
        if top.returncode:
            raise SystemExit(f'--git: {self.state.parent} is not inside a git work tree')
        self.repo = Path(top.stdout.strip()).resolve()
        rel = os.path.relpath(self.state, self.repo)
        if rel.startswith('..'):
            raise SystemExit(f'--git: {self.state} is outside {self.repo}')
        self.rel = Path(rel).as_posix()
        branch = self._git('rev-parse', '--abbrev-ref', 'HEAD').stdout.strip()
        if not branch.startswith(self.prefix):
            raise SystemExit(f'--git needs a {self.prefix}* branch (on {branch!r}); e.g. '
                             f'git worktree add ../he_thong_autoresearch -b {self.prefix}btcusdt')
        st = self._git('status', '--porcelain', '--', self.rel).stdout
        if st and not st.startswith('??'):
            raise SystemExit(f'--git: {self.rel} has uncommitted changes; commit or restore it first')

    def tracked(self) -> bool:
        return self._git('ls-files', '--error-unmatch', '--', self.rel, check=False).returncode == 0

    def accept(self, message: str):
        if not self.enabled:
            return None
        self._git('add', '--', self.rel)
        self._git('commit', '-q', '-m', message, '--', self.rel)    # pathspec => --only
        return self._git('rev-parse', '--short', 'HEAD').stdout.strip()

    def rollback(self, previous: bytes) -> None:
        """Write back the pre-experiment bytes (= HEAD in --git mode). `git restore` is not used: with
        core.autocrlf=true (this machine) it rewrites LF as CRLF, so the result would not be bit-exact."""
        if previous:
            atomic_write(self.state, previous)
        else:
            self.state.unlink(missing_ok=True)
        if (self.state.read_bytes() if self.state.exists() else b'') != previous:
            raise RuntimeError(f'rollback of {self.state} is not bit-exact')
        if self.enabled and self.tracked() and self._git('diff', '--quiet', 'HEAD', '--', self.rel,
                                                         check=False).returncode:
            raise RuntimeError(f'{self.rel} differs from HEAD after rollback')


# ----------------------------------------------------------------------------------------------- loop
def run(args) -> dict:
    t_start = time.perf_counter()
    files = sorted({p for pat in args.data for p in (glob.glob(pat) or [pat])})
    segments = []
    for p in files:
        segments += load_segments(p, args.max_gap_s, args.cache_dir)
    t_data = time.perf_counter() - t_start
    points = eval_points(segments, args.step_s)
    n_train = int(round(len(points) * (1.0 - args.holdout_frac)))
    train, hold = points[:n_train], points[n_train:]
    if not train:
        raise SystemExit(f'no eval points: need segments > {WARMUP_S + HORIZON_S} s; got '
                         f'{[s.logmid.size for s in segments]} bars from {files}')
    state_path = Path(args.state)
    gate = GitGate(state_path, args.git)
    gate.preflight()
    agents = {a.name: a for a in (load_state(state_path) or default_agents())}
    if not state_path.exists():
        atomic_write(state_path, state_bytes(list(agents.values())))
        gate.accept('autoresearch: initial 8-agent population') if args.git else None
    ledger_path = Path(args.ledger)
    ledger = json.loads(ledger_path.read_text(encoding='utf-8')) if ledger_path.exists() else {}
    weights = {n: float(ledger.get('weights', {}).get(n, 1.0)) for n in agents}
    r0 = int(ledger.get('round', 0)) if set(ledger.get('weights', {})) == set(agents) else 0
    tr = {n: score(a, segments, train, args.seed) for n, a in agents.items()}
    ho = {n: score(a, segments, hold, args.seed) for n, a in agents.items()}
    base = AgentConfig('BASELINE', 'TIDAL', 1800, 60, 60, 0.0).clipped()
    baseline = {'train': score(base, segments, train, args.seed), 'holdout': score(base, segments, hold, args.seed)}
    first = {'train': float(np.mean(list(tr.values()))), 'holdout': float(np.mean(list(ho.values())))}
    history = list(ledger.get('history', []))[-200:]
    accepted = 0
    for rnd in range(r0 + 1, r0 + args.rounds + 1):
        weights = survivor_update(weights, tr)
        worst = max(tr, key=lambda n: (tr[n], n))
        cand, pname = mutate(agents[worst], np.random.default_rng([args.seed, rnd]))
        previous = state_path.read_bytes()
        trial = {**agents, worst: cand}
        atomic_write(state_path, state_bytes(list(trial.values())))           # the experiment, in the work tree
        new_tr = score(cand, segments, train, args.seed) if cand != agents[worst] else tr[worst]
        new_ho = score(cand, segments, hold, args.seed) if cand != agents[worst] else ho[worst]
        old_v, new_v = getattr(agents[worst], pname), getattr(cand, pname)
        keep = new_tr < tr[worst] - 1e-9
        row = {'round': rnd, 'agent': worst, 'param': pname, 'old': old_v, 'new': new_v,
               'train_old': tr[worst], 'train_new': new_tr, 'holdout_old': ho[worst], 'holdout_new': new_ho,
               'kept': keep, 'commit': None}
        if keep:
            msg = (f'autoresearch r{rnd}: {worst}.{pname} {old_v:g}->{new_v:g} train {tr[worst]:.4f}->{new_tr:.4f} bps '
                   f'(holdout {ho[worst]:.4f}->{new_ho:.4f})')
            row['commit'] = gate.accept(msg)
            agents, tr[worst], ho[worst] = trial, new_tr, new_ho
            accepted += 1
        else:
            gate.rollback(previous)
        history.append(row)
        print(f"r{rnd:03d} {worst:8s} {pname:10s} {old_v:>9.4g} -> {new_v:<9.4g} train {row['train_old']:.4f} -> "
              f"{new_tr:.4f}  {'KEEP' if keep else 'ROLLBACK'}" + (f" {row['commit']}" if row['commit'] else ''),
              flush=True)
        ledger = {'schema': SCHEMA, 'round': rnd, 'seed': args.seed, 'weights': weights, 'train_loss_bps': tr,
                  'holdout_loss_bps': ho, 'history': history[-200:]}
        atomic_write(ledger_path, (json.dumps(ledger, indent=1) + '\n').encode('utf-8'))
    final = {'train': float(np.mean(list(tr.values()))), 'holdout': float(np.mean(list(ho.values())))}
    prop = tunables_proposal(list(agents.values()), weights, time.time(),
                             reason=f'survivor weights after round {r0 + args.rounds}; mean train {final["train"]:.4f} '
                                    f'holdout {final["holdout"]:.4f} bps (baseline {baseline["holdout"]:.4f})')
    write_proposal(args.proposal, prop)
    report = {'schema': SCHEMA, 'files': files, 'segments': [(s.source, s.logmid.size) for s in segments],
              'eval_points': {'train': len(train), 'holdout': len(hold), 'step_s': args.step_s},
              'horizon_s': HORIZON_S, 'futures': N_FUTURES, 'quantiles': QUANTILES.tolist(),
              'rounds': args.rounds, 'accepted': accepted, 'git': bool(args.git),
              'mean_loss_bps': {'start': first, 'end': final}, 'baseline_loss_bps': baseline,
              'train_loss_bps': tr, 'holdout_loss_bps': ho, 'weights': weights, 'proposal': prop,
              'seconds': {'data': round(t_data, 2), 'total': round(time.perf_counter() - t_start, 2)}}
    if args.report:
        atomic_write(Path(args.report), (json.dumps(report, indent=1) + '\n').encode('utf-8'))
    return report


# ----------------------------------------------------------------------------------------------- trade objective
# --source klines: the same mutate/survivor/git loop, scored on NET trading results of 1m klines instead of
# pinball loss. Decisions on closed bars only; non-overlapping holds of `horizon` bars; costs from
# core.early_stop. The holdout (last 30% of time, after an embargo of the longest horizon) is scored ONCE,
# after the loop: selecting on it would turn it into training data.
TRADE_SCHEMA = 'autoresearch_trade/1'
TRADE_BOUNDS = {'lookback': (60, 2880, True), 'signal': (5, 2880, True), 'coef': (-3.0, 3.0, False),
                'hurdle': (1.0, 4.0, False), 'horizon': (5, 240, True)}
TRADE_WARMUP = TRADE_BOUNDS['lookback'][1]
EMBARGO_BARS = TRADE_BOUNDS['horizon'][1]
DAY_MS, BAR_MS = 86_400_000, 60_000
FILLS = ('maker', 'ideal', 'ioc', 'dynamic')


@dataclass(frozen=True)
class TradeAgent:
    name: str
    family: str          # NORO fair-value reversion | LUMEN taker flow | TIDAL momentum
    lookback: int        # bars of 1m returns behind sigma
    signal: int          # bars behind the family signal
    coef: float          # forecast drift = coef * signal * sigma * sqrt(horizon)
    hurdle: float = 1.0  # trade only when |drift| >= hurdle * cost of the chosen fill
    horizon: int = 15    # bars held

    def clipped(self) -> 'TradeAgent':
        if self.family not in FAMILIES:
            raise ValueError(f'unknown family {self.family!r}')
        v = {}
        for k, (lo, hi, integer) in TRADE_BOUNDS.items():
            x = min(max(float(getattr(self, k)), lo), hi)
            v[k] = int(round(x)) if integer else x
        v['signal'] = min(v['signal'], v['lookback'])
        return replace(self, **v)


def default_trade_agents() -> list:
    spec = [('NORO-15', 'NORO', 1440, 240, -1.0, 1.0, 15), ('NORO-60', 'NORO', 2880, 720, -1.0, 1.0, 60),
            ('NORO-240', 'NORO', 2880, 1440, -1.0, 1.0, 240), ('LUMEN-15', 'LUMEN', 1440, 15, 1.0, 1.0, 15),
            ('LUMEN-60', 'LUMEN', 1440, 60, 1.0, 1.0, 60), ('LUMEN-240', 'LUMEN', 2880, 240, 1.0, 1.0, 240),
            ('TIDAL-60', 'TIDAL', 2880, 240, 1.0, 1.0, 60), ('TIDAL-240', 'TIDAL', 2880, 1440, 1.0, 1.0, 240)]
    return [TradeAgent(*s).clipped() for s in spec]


def fill_costs() -> dict:
    """bps per round trip: maker entry + taker exit (= COST_ALL_IN_BPS), or taker on both legs for IOC."""
    from core.early_stop import COST_ALL_IN_BPS, SPREAD_SLIPPAGE_BPS, TAKER_FEE_BPS
    return {'maker': COST_ALL_IN_BPS, 'ioc': 2 * (TAKER_FEE_BPS + SPREAD_SLIPPAGE_BPS)}


@dataclass
class Bars:
    """One gap-free run of 1m klines; cs_* are prefix sums with a leading 0 (window sums in O(1))."""
    symbol: str
    t_ms: np.ndarray
    logc: np.ndarray
    close: np.ndarray
    low: np.ndarray
    high: np.ndarray
    cs_r: np.ndarray
    cs_r2: np.ndarray
    cs_logc: np.ndarray
    cs_imb: np.ndarray


def make_bars(symbol, t_ms, high, low, close, volume, taker_buy, max_gap_bars: int = 5) -> list:
    t_ms, high, low, close, volume, taker_buy = (np.asarray(x) for x in (t_ms, high, low, close, volume, taker_buy))
    order = np.argsort(t_ms, kind='stable')
    t_ms, high, low, close, volume, taker_buy = (x[order] for x in (t_ms, high, low, close, volume, taker_buy))
    keep = np.r_[True, t_ms[1:] != t_ms[:-1]]
    t_ms, high, low, close, volume, taker_buy = (x[keep] for x in (t_ms, high, low, close, volume, taker_buy))
    cuts = np.flatnonzero(np.diff(t_ms) > max_gap_bars * BAR_MS) + 1
    out = []
    for lo_, hi_ in zip(np.r_[0, cuts], np.r_[cuts, t_ms.size]):
        c = close[lo_:hi_].astype(np.float64)
        v, tb = volume[lo_:hi_].astype(np.float64), taker_buy[lo_:hi_].astype(np.float64)
        imb = np.where(v > 0, (2.0 * tb - v) / np.where(v > 0, v, 1.0), 0.0)
        logc = np.log(c)
        r = np.diff(logc, prepend=logc[0])
        z = np.zeros(1)
        out.append(Bars(symbol, t_ms[lo_:hi_].astype(np.int64), logc, c, low[lo_:hi_].astype(np.float64),
                        high[lo_:hi_].astype(np.float64), np.concatenate((z, np.cumsum(r))),
                        np.concatenate((z, np.cumsum(r * r))), np.concatenate((z, np.cumsum(logc))),
                        np.concatenate((z, np.cumsum(imb)))))
    return out


def load_klines(symbol: str, months=None, root=None) -> list:
    """data/klines_1m/<SYMBOL>-1m-<YYYY-MM>.zip (Binance public USD-M klines, header optional)."""
    import zipfile
    import pandas as pd
    files = sorted(Path(root or ROOT / 'data' / 'klines_1m').glob(f'{symbol}-1m-*.zip'))
    if months:
        files = [p for p in files if p.stem.split('-1m-')[-1] in set(months)]
    frames = []
    for p in files:
        with zipfile.ZipFile(p) as zf:
            name = zf.namelist()[0]
            head = zf.open(name).readline()
            frames.append(pd.read_csv(zf.open(name), header=0 if not head[:1].isdigit() else None,
                                      usecols=[0, 2, 3, 4, 5, 9]))
    if not frames:
        return []
    a = np.concatenate([f.to_numpy(np.float64) for f in frames])
    return make_bars(symbol, a[:, 0].astype(np.int64), a[:, 1], a[:, 2], a[:, 3], a[:, 4], a[:, 5])


def trade_eval(ag: TradeAgent, b: Bars, t_lo: int, t_hi: int, fill: str = 'maker', ioc_z: float = 1.5,
               costs=None):
    """Trades decided on closed bars with t in [t_lo, t_hi). -> (decision index, side, net bps) of FILLED trades.

    maker: GTX limit at the decision close, filled only if the next bar trades THROUGH it (low < close for a
    buy); ideal: maker cost, always filled (upper bound); ioc: taker on both legs, always filled; dynamic:
    ioc when |momentum z| >= ioc_z, maker otherwise. Exit at the close `horizon` bars after the decision."""
    costs = costs or fill_costs()
    n, H, L, w = b.t_ms.size, ag.horizon, ag.lookback, ag.signal
    i0 = max(TRADE_WARMUP, int(np.searchsorted(b.t_ms, t_lo)))
    i1 = min(int(np.searchsorted(b.t_ms, t_hi)), n - H - 1)
    empty = np.zeros(0, np.int64), np.zeros(0), np.zeros(0)
    if i1 <= i0:
        return empty
    idx = np.arange(i0, i1, H)
    s1, s2 = b.cs_r[idx + 1] - b.cs_r[idx + 1 - L], b.cs_r2[idx + 1] - b.cs_r2[idx + 1 - L]
    sd = np.sqrt(np.maximum((s2 - s1 * s1 / L) / (L - 1), 1e-18))
    den = sd * math.sqrt(w)
    mom = (b.logc[idx] - b.logc[idx - w]) / den
    if ag.family == 'LUMEN':
        sig = (b.cs_imb[idx + 1] - b.cs_imb[idx + 1 - w]) / w
    elif ag.family == 'NORO':
        sig = np.tanh((b.logc[idx] - (b.cs_logc[idx + 1] - b.cs_logc[idx + 1 - w]) / w) / den)
    else:
        sig = np.tanh(mom)
    drift = ag.coef * sig * sd * math.sqrt(H) * 1e4
    route_ioc = np.full(idx.size, fill == 'ioc') if fill != 'dynamic' else np.abs(mom) >= ioc_z
    cost = np.where(route_ioc, costs['ioc'], costs['maker'])
    take = np.abs(drift) >= ag.hurdle * cost
    side = np.sign(drift) * take
    if fill in ('maker', 'dynamic'):
        through = np.where(side > 0, b.low[idx + 1] < b.close[idx], b.high[idx + 1] > b.close[idx])
        take &= route_ioc | through
    net = side * (b.logc[idx + H] - b.logc[idx]) * 1e4 - cost
    return idx[take], side[take], net[take]


def portfolio(ag: TradeAgent, bars: list, t_lo: int, t_hi: int, fill: str = 'maker', ioc_z: float = 1.5):
    """Equal capital per symbol: daily net bps = mean over symbols of that day's summed trade results
    (idle days count as 0). -> (daily array, all trade nets)."""
    d0, nd = t_lo // DAY_MS, (t_hi - 1) // DAY_MS - t_lo // DAY_MS + 1
    syms = sorted({b.symbol for b in bars})
    daily, nets = np.zeros(nd), []
    for b in bars:
        idx, _, net = trade_eval(ag, b, t_lo, t_hi, fill, ioc_z)
        if idx.size:
            daily += np.bincount(b.t_ms[idx] // DAY_MS - d0, weights=net, minlength=nd)[:nd] / len(syms)
            nets.append(net)
    return daily, (np.concatenate(nets) if nets else np.zeros(0))


def perf(daily: np.ndarray, nets: np.ndarray) -> dict:
    from core.fast_eval import psr
    n, days = int(nets.size), int(daily.size)
    sd = float(daily.std(ddof=1)) if days > 1 else 0.0
    sr = float(daily.mean()) / sd if sd > 0 else 0.0
    m = daily - daily.mean()
    skew = float((m ** 3).mean() / sd ** 3) if sd > 0 else 0.0
    kurt = float((m ** 4).mean() / sd ** 4) if sd > 0 else 3.0
    tsd = float(nets.std(ddof=1)) if n > 1 else 0.0
    return {'trades': n, 'days': days, 'trades_per_day': n / days if days else 0.0,
            'mean_net_bps': float(nets.mean()) if n else None,
            't_per_trade': float(nets.mean()) / (tsd / n ** 0.5) if n > 1 and tsd > 0 else None,
            'hit': float((nets > 0).mean()) if n else None, 'daily_bps': float(daily.mean()),
            'sr_day': sr, 'sharpe_ann': sr * math.sqrt(365), 'skew': skew, 'kurt': kurt,
            'psr0': psr(sr, days, skew, kurt) if sd > 0 else None}


def trade_score(p: dict, min_trades: int) -> float:
    return p['sharpe_ann'] if p['trades'] >= min_trades else -math.inf


def run_trade(args) -> dict:
    from core.fast_eval import dsr, expected_max_sr
    t_start = time.perf_counter()
    bars = [b for s in args.symbols for b in load_klines(s, args.months or None, args.klines_dir)]
    if not bars:
        raise SystemExit(f'no klines for {args.symbols} in data/klines_1m')
    t_data = time.perf_counter() - t_start
    t_first = min(int(b.t_ms[min(TRADE_WARMUP, b.t_ms.size - 1)]) for b in bars)
    t_last = max(int(b.t_ms[-1]) for b in bars) + BAR_MS
    t_split = t_first + int((t_last - t_first) * (1.0 - args.holdout_frac))
    train, hold = (t_first, t_split), (t_split + EMBARGO_BARS * BAR_MS, t_last)
    state_path = Path(args.state)
    gate = GitGate(state_path, args.git)
    gate.preflight()
    if state_path.exists():
        doc = json.loads(state_path.read_text(encoding='utf-8'))
        if doc.get('schema') != TRADE_SCHEMA:
            raise SystemExit(f'{state_path}: schema {doc.get("schema")!r} != {TRADE_SCHEMA!r}')
        agents = {a['name']: TradeAgent(**a).clipped() for a in doc['agents']}
    else:
        agents = {a.name: a for a in default_trade_agents()}
    tbytes = lambda ags: (json.dumps({'schema': TRADE_SCHEMA, 'agents': [asdict(a) for a in ags]}, indent=1)
                          + '\n').encode('utf-8')
    if not state_path.exists():
        atomic_write(state_path, tbytes(agents.values()))
        gate.accept('autoresearch-trade: initial 8-agent population') if args.git else None
    tried = {}                                             # config -> train perf (every distinct trial)

    def evaluate(a):
        if a not in tried:
            tried[a] = perf(*portfolio(a, bars, *train, args.fill, args.ioc_z))
        return tried[a]
    tr = {n: trade_score(evaluate(a), args.min_trades) for n, a in agents.items()}
    weights, accepted, history = {n: 1.0 for n in agents}, 0, []
    for rnd in range(1, args.rounds + 1):
        weights = survivor_update(weights, {n: -s for n, s in tr.items()})
        worst = min(tr, key=lambda n: (tr[n], n))
        cand, pname = mutate(agents[worst], np.random.default_rng([args.seed, rnd]), TRADE_BOUNDS)
        previous = state_path.read_bytes()
        trial = {**agents, worst: cand}
        atomic_write(state_path, tbytes(trial.values()))
        new = trade_score(evaluate(cand), args.min_trades)
        keep = new > tr[worst] + 1e-9
        old_v, new_v = getattr(agents[worst], pname), getattr(cand, pname)
        row = {'round': rnd, 'agent': worst, 'param': pname, 'old': old_v, 'new': new_v,
               'train_sharpe_old': tr[worst], 'train_sharpe_new': new, 'kept': keep, 'commit': None}
        if keep:
            row['commit'] = gate.accept(f'autoresearch-trade r{rnd}: {worst}.{pname} {old_v:g}->{new_v:g} '
                                        f'train Sharpe {tr[worst]:.3f}->{new:.3f}')
            agents, tr[worst] = trial, new
            accepted += 1
        else:
            gate.rollback(previous)
        history.append(row)
    # ---- the holdout, once
    sr_trials = [p['sr_day'] for p in tried.values() if p['trades'] >= args.min_trades]
    sr_var = float(np.var(sr_trials)) if len(sr_trials) > 1 else 0.0
    rep_agents, rng = {}, np.random.default_rng(args.seed)
    for n, a in agents.items():
        ptr = tried[a]
        dh, nh = portfolio(a, bars, *hold, args.fill, args.ioc_z)
        ph = perf(dh, nh)
        by_fill = {f: perf(*portfolio(a, bars, *hold, f, args.ioc_z))['mean_net_bps'] for f in FILLS}
        rand = []
        for b in bars:                                      # same decisions, fills and costs, random sides
            idx, side, net = trade_eval(a, b, *hold, args.fill, args.ioc_z)
            fwd = (b.logc[idx + a.horizon] - b.logc[idx]) * 1e4
            cost = side * fwd - net
            rand.append(rng.choice([-1.0, 1.0], idx.size) * fwd - cost)
        rand = np.concatenate(rand) if rand else np.zeros(0)
        valid = ptr['trades'] >= args.min_trades and ptr['sr_day']
        # sr_var of the trials is inflated by cost drag (trial SRs span about -26..+1), so the criteria use the
        # null variance of a per-day SR estimate, 1/T (Lo 2002); the empirical-variance DSR is reported too.
        dsr_train = dsr(ptr['sr_day'], ptr['days'], ptr['skew'], ptr['kurt'], sr_var, len(tried)) if valid else None
        dsr_null = (dsr(ptr['sr_day'], ptr['days'], ptr['skew'], ptr['kurt'], 1.0 / ptr['days'], len(tried))
                    if valid else None)
        ok = (ph['trades'] >= args.min_trades and ph['mean_net_bps'] is not None and ph['mean_net_bps'] > 0
              and ph['sharpe_ann'] >= args.target_sharpe and (ph['psr0'] or 0) >= 0.95 and (dsr_null or 0) >= 0.95)
        rep_agents[n] = {'config': asdict(a), 'weight': weights[n], 'train': ptr, 'holdout': ph,
                         'holdout_mean_net_by_fill': by_fill, 'dsr_train': dsr_train, 'dsr_train_null': dsr_null,
                         'random_side_holdout_mean_net_bps': float(rand.mean()) if rand.size else None,
                         'meets_criteria': bool(ok)}
    wsum = sum(weights.values())
    port = sum(weights[n] * portfolio(agents[n], bars, *hold, args.fill, args.ioc_z)[0] for n in agents) / wsum
    pport = perf(port, np.concatenate([portfolio(agents[n], bars, *hold, args.fill, args.ioc_z)[1] for n in agents]))
    prop = tunables_proposal(list(agents.values()), weights, time.time(),
                             reason=f'trade autoresearch {args.rounds} rounds; holdout portfolio Sharpe '
                                    f'{pport["sharpe_ann"]:.2f}')
    write_proposal(args.proposal, prop)
    report = {'schema': TRADE_SCHEMA, 'symbols': args.symbols, 'fill': args.fill, 'costs_bps': fill_costs(),
              'bars': {s: int(sum(b.t_ms.size for b in bars if b.symbol == s)) for s in args.symbols},
              'split_ms': {'train': train, 'holdout': hold}, 'rounds': args.rounds, 'accepted': accepted,
              'trials': len(tried), 'sr_var_trials': sr_var, 'min_trades': args.min_trades,
              'expected_max_sharpe_ann_of_noise_trials': expected_max_sr(sr_var, len(tried)) * math.sqrt(365),
              'target_sharpe': args.target_sharpe, 'agents': rep_agents, 'portfolio_holdout': pport,
              'any_meets_criteria': any(v['meets_criteria'] for v in rep_agents.values()),
              'history': history[-200:], 'proposal': prop,
              'seconds': {'data': round(t_data, 2), 'total': round(time.perf_counter() - t_start, 2)}}
    if args.report:
        atomic_write(Path(args.report), (json.dumps(report, indent=1, default=float) + '\n').encode('utf-8'))
    return report


def parse_args(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--data', nargs='+', default=[str(ROOT / 'data/l2/BTCUSDT/*.jsonl')])
    ap.add_argument('--rounds', type=int, default=20)
    ap.add_argument('--seed', type=int, default=7)
    ap.add_argument('--step-s', type=int, default=30)
    ap.add_argument('--holdout-frac', type=float, default=0.3)
    ap.add_argument('--max-gap-s', type=float, default=30.0)
    ap.add_argument('--git', action='store_true', help='commit kept mutations / git-restore rejected ones')
    ap.add_argument('--state', default=str(ROOT / 'config/autoresearch_agents.json'))
    ap.add_argument('--ledger', default=str(ROOT / 'data/autoresearch_ledger.json'))
    ap.add_argument('--proposal', default=str(ROOT / 'data/autoresearch_tunables_proposal.json'))
    ap.add_argument('--report', default=str(ROOT / 'data/autoresearch_report.json'))
    ap.add_argument('--cache-dir', default=str(ROOT / 'data/npz'))
    ap.add_argument('--source', choices=('l2', 'klines'), default='l2',
                    help='l2: pinball loss on L2 recordings; klines: net trading result on 1m klines')
    ap.add_argument('--symbols', nargs='+', default=['BTCUSDT', 'SOLUSDT', 'DOGEUSDT', 'BNBUSDT'])
    ap.add_argument('--months', nargs='*', default=None, help='YYYY-MM list (klines; default every file)')
    ap.add_argument('--klines-dir', default=str(ROOT / 'data' / 'klines_1m'))
    ap.add_argument('--fill', choices=FILLS, default='maker')
    ap.add_argument('--ioc-z', type=float, default=1.5, help='dynamic fill: IOC when |momentum z| >= this')
    ap.add_argument('--min-trades', type=int, default=60)
    ap.add_argument('--target-sharpe', type=float, default=1.8)
    a = ap.parse_args(argv)
    if not 0 <= a.holdout_frac < 1 or a.rounds < 0 or a.step_s < 1:
        ap.error('need 0 <= --holdout-frac < 1, --rounds >= 0, --step-s >= 1')
    if a.source == 'klines':
        for k, v in (('state', 'config/autoresearch_trade_agents.json'), ('ledger', 'data/autoresearch_ledger.json'),
                     ('proposal', 'data/autoresearch_tunables_proposal.json'), ('report', 'data/autoresearch_report.json')):
            if getattr(a, k) == str(ROOT / v):
                setattr(a, k, str(ROOT / v.replace('autoresearch_', 'autoresearch_trade_', 1)))
    return a


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.source == 'klines':
        rep = run_trade(args)
        for n, v in rep['agents'].items():
            h = v['holdout']
            print(f"{n:10s} train Sharpe {v['train']['sharpe_ann']:6.2f} | holdout trades {h['trades']:5d} "
                  f"({h['trades_per_day']:.2f}/day) net {h['mean_net_bps'] or 0:7.2f} bps Sharpe {h['sharpe_ann']:6.2f} "
                  f"PSR0 {h['psr0'] or 0:.2f} | DSR(train, null var) {v['dsr_train_null'] or 0:.2f} | random-side "
                  f"{v['random_side_holdout_mean_net_bps'] or 0:7.2f}")
        p = rep['portfolio_holdout']
        print(f"portfolio holdout Sharpe {p['sharpe_ann']:.2f} daily {p['daily_bps']:.2f} bps | trials {rep['trials']} "
              f"accepted {rep['accepted']}/{rep['rounds']} | any_meets_criteria {rep['any_meets_criteria']}")
        return 0
    rep = run(args)
    m = rep['mean_loss_bps']
    print(f"accepted {rep['accepted']}/{rep['rounds']} | mean pinball bps train {m['start']['train']:.4f} -> "
          f"{m['end']['train']:.4f}, holdout {m['start']['holdout']:.4f} -> {m['end']['holdout']:.4f} "
          f"(baseline {rep['baseline_loss_bps']['holdout']:.4f}) | swarm_weights {rep['proposal']['swarm_weights']}")
    return 0


if __name__ == '__main__':
    sys.exit(main())
