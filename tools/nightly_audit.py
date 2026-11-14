"""Nightly 00:00 UTC audit (2026-11-07): realized slippage vs the 8.5 bps cost model, and regime-classifier accuracy.

Read-only. It writes one report pair, nightly_<day>.md and nightly_<day>.json, under
docs/research_upgrade/nightly_reviews/ and never writes code, configs, profiles, tunables or execution parameters.
Proposals in the report are text for the owner; nothing applies them.

    python tools/nightly_audit.py                       # the 24 h ending at the last 00:00 UTC
    python tools/nightly_audit.py --end 2026-10-10      # any earlier UTC midnight (backfill)
    python tools/nightly_audit.py --ticks-dir F:/factory-quant-opt/data/ticks --end 2026-09-10
    python tools/nightly_audit.py --dry-run             # print the report, write nothing

Slippage, per closed round trip in data/engine_trades.jsonl whose exit time t falls in the window
(d = +1 long, -1 short; positive = cost):
    entry_slip = d * (entry - venue_mid) / venue_mid       vs the execution venue's own mid at the decision
    exit_slip  = d * (exit_ref - exit) / exit_ref          exit_ref = the soft-exit reference (stop level or mid)
    all_in     = fee_bps + entry_slip + exit_slip          vs COST_ALL_IN_BPS 8.5 = fees 7.0 + spread/slippage 1.5
A missing field makes that component UNKNOWN (counted, never zero); cost_complete=False makes the fee UNKNOWN.
Exchange-side exits (stop/TP orders) carry no exit_ref, so their exit slippage is UNKNOWN by construction.

Regime accuracy: core.regime_classifier runs causally over per-second prices (data/recordings aggTrade or
bookTicker, data/l2 bookTicker, or public aggTrades in --ticks-dir). Every 60 s its state is compared with a label
computed after the fact on the next 15 minutes, using the classifier's own thresholds: CRASH if the move from the
start price reaches 150 bps within 5 minutes; JUMPY if realized volatility is >= 1.8 x the day's median 15-minute
volatility; CALM_TREND if the 30 s-bucket efficiency ratio is >= 0.42; otherwise SIDEWAYS. UNKNOWN states count as
coverage, not errors. The majority-class baseline is printed beside the accuracy. Agreement with a forward label
measures persistence of the state; it is not trading evidence. The circuit-breaker cooldown is meant to disagree
with calm forward labels, so CRASH detection is also reported per event (latency, false episodes).
"""
from __future__ import annotations

import argparse
import gzip
import json
import math
import os
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.early_stop import COST_ALL_IN_BPS, MAKER_FEE_BPS, SPREAD_SLIPPAGE_BPS, TAKER_FEE_BPS  # noqa: E402
from core.regime_classifier import (CALM_TREND, CRASH, JUMPY, SIDEWAYS, UNKNOWN,  # noqa: E402
                                    RegimeClassifier, RegimeConfig)

NIGHTLY_DIR = ROOT / 'docs' / 'research_upgrade' / 'nightly_reviews'
LEDGER = ROOT / 'data' / 'engine_trades.jsonl'
RECORDINGS, L2_DIR = ROOT / 'data' / 'recordings', ROOT / 'data' / 'l2'
FEE_MODEL_BPS = MAKER_FEE_BPS + TAKER_FEE_BPS
EVAL_STEP_S, HORIZON_S, MIN_N = 60, 900, 30
LABELS = (CALM_TREND, SIDEWAYS, JUMPY, CRASH)


def utc(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).strftime('%Y-%m-%d %H:%M')


def window_for(end: str | None, now: datetime | None = None) -> tuple:
    """(start_s, end_s): the 24 h ending at `end` (YYYY-MM-DD, 00:00 UTC) or at the last 00:00 UTC before now."""
    if end is None:
        now = now or datetime.now(timezone.utc)
        stop = now.replace(hour=0, minute=0, second=0, microsecond=0)
    else:
        stop = datetime.strptime(end, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return (stop - timedelta(days=1)).timestamp(), stop.timestamp()


# ------------------------------------------------------------------ slippage
def _num(x):
    return float(x) if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) else None


def trade_costs(r: dict) -> dict:
    d = {'BUY': 1.0, 'SELL': -1.0}.get(r.get('side'))
    entry, exit_, vmid, ref = (_num(r.get(k)) for k in ('entry', 'exit', 'venue_mid', 'exit_ref'))
    fee = None if r.get('cost_complete') is False else _num(r.get('fee_bps'))
    e_slip = d * (entry - vmid) / vmid * 1e4 if d and entry and vmid else None
    x_slip = d * (ref - exit_) / ref * 1e4 if d and exit_ and ref else None
    known = None not in (fee, e_slip, x_slip)
    return {'fee_bps': fee, 'entry_slip_bps': e_slip, 'exit_slip_bps': x_slip,
            'all_in_bps': fee + e_slip + x_slip if known else None,
            'entry_type': r.get('entry_type') or 'UNKNOWN', 'exit_mode': r.get('exit_mode') or 'UNKNOWN'}


def _summary(xs: list, seed: int = 7) -> dict:
    x = np.asarray([v for v in xs if v is not None], dtype=float)
    out = {'n': int(x.size), 'unknown': len(xs) - int(x.size)}
    if x.size:
        out.update(mean=float(x.mean()), median=float(np.median(x)), p90=float(np.percentile(x, 90)))
    if x.size >= 2:
        rng = np.random.default_rng(seed)
        means = x[rng.integers(0, x.size, (4000, x.size))].mean(axis=1)
        out['ci95_mean'] = [float(np.percentile(means, 2.5)), float(np.percentile(means, 97.5))]
    return out


def slippage_section(rows: list, start: float, end: float) -> dict:
    inside = [r for r in rows if _num(r.get('t')) is not None and start <= r['t'] < end]
    costs = [trade_costs(r) for r in inside]
    sec = {'rows': len(inside), 'modes': dict(Counter(str(r.get('mode')) for r in inside)),
           'venues': dict(Counter(str(r.get('venue')) for r in inside)),
           'model_bps': {'all_in': COST_ALL_IN_BPS, 'fees': FEE_MODEL_BPS, 'slippage': SPREAD_SLIPPAGE_BPS}}
    for k in ('all_in_bps', 'fee_bps', 'entry_slip_bps', 'exit_slip_bps'):
        sec[k] = _summary([c[k] for c in costs])
    sec['slippage_bps'] = _summary([c['entry_slip_bps'] + c['exit_slip_bps'] for c in costs
                                    if None not in (c['entry_slip_bps'], c['exit_slip_bps'])]
                                   + [None] * sum(1 for c in costs
                                                  if None in (c['entry_slip_bps'], c['exit_slip_bps'])))
    sec['by_entry_type'] = {t: _summary([c['all_in_bps'] for c in costs if c['entry_type'] == t])
                            for t in sorted({c['entry_type'] for c in costs})}
    s = sec['all_in_bps']
    if not inside:
        sec['verdict'] = 'UNKNOWN: no round trip closed in the window'
    elif s['n'] == 0:
        sec['verdict'] = 'UNKNOWN: no round trip in the window with fee, venue_mid and exit_ref all recorded'
    elif s['n'] < MIN_N:
        sec['verdict'] = f'UNKNOWN: n={s["n"]} < {MIN_N}, no inference'
    else:
        lo, hi = s['ci95_mean']
        sec['verdict'] = ('ABOVE MODEL' if lo > COST_ALL_IN_BPS else 'BELOW MODEL' if hi < COST_ALL_IN_BPS
                          else 'CONSISTENT WITH MODEL') + f' (CI95 {lo:+.2f}..{hi:+.2f} vs {COST_ALL_IN_BPS:g})'
    return sec


# ------------------------------------------------------------------ prices
def _open(path: Path):
    return gzip.open(path, 'rt', encoding='utf-8') if path.suffix == '.gz' else open(path, encoding='utf-8')


def _iter_file(path: Path, kind: str):
    """-> (ms, price, source) from one recorder / public-tape file; malformed lines are skipped."""
    with _open(path) as fh:
        for line in fh:
            try:
                o = json.loads(line)
                if kind == 'recordings':
                    d = o['d']
                    if o.get('s') == 'aggTrade':
                        yield int(d['T']), float(d['p']), 'aggTrade'
                    elif o.get('s') == 'bookTicker':
                        yield int(d.get('T') or d['E']), (float(d['b']) + float(d['a'])) / 2, 'bookTicker'
                elif kind == 'l2':
                    if o.get('e') == 'bookTicker':
                        yield int(o.get('T') or o['E']), (float(o['b']) + float(o['a'])) / 2, 'bookTicker'
                elif o.get('source_time_unit') == 'ms':
                    yield int(o['source_timestamp']), float(o['p']), 'aggTrade'
            except (ValueError, KeyError, TypeError):
                continue


def source_files(kind: str, base: Path, symbol: str, days: list) -> list:
    if kind == 'recordings':
        return [p for d in days for p in sorted((base / symbol).glob(f'{d}-*.jsonl.gz'))]
    pattern = '{d}.jsonl' if kind == 'l2' else '{d}.jsonl.gz'
    return [p for d in days for p in [base / symbol / pattern.format(d=d)] if p.exists()]


def load_prices(files: list, kind: str, lo: float, hi: float) -> tuple:
    """Last price per whole second in [lo, hi]. aggTrade prices win over bookTicker mids (no bid/ask mixing)."""
    by_src = {'aggTrade': {}, 'bookTicker': {}}
    for path in files:
        for ms, px, src in _iter_file(path, kind):
            s = ms // 1000
            if lo <= s <= hi and px > 0 and math.isfinite(px):
                by_src[src][s] = px
    pick = 'aggTrade' if by_src['aggTrade'] else 'bookTicker'
    secs = np.array(sorted(by_src[pick]), dtype=np.int64)
    return secs, np.array([by_src[pick][s] for s in secs], dtype=float), (pick if secs.size else None)


# ------------------------------------------------------------------ regime accuracy
def forward_labels(ts: np.ndarray, px: np.ndarray, eval_t: np.ndarray, cfg: RegimeConfig) -> list:
    """Ex-post label from prices in (t, t + 15 min], or None where the forward window is incomplete."""
    logp = np.log(px)
    tf = np.asarray(ts, dtype=np.float64)          # one cast: a float probe into an int array copies it per call
    raw = []
    for t in eval_t:
        i, j = int(np.searchsorted(ts, t, 'right')), int(np.searchsorted(ts, t + HORIZON_S, 'right'))
        if i == 0 or j - i < 30 or ts[j - 1] < t + HORIZON_S - cfg.stale_s or t - ts[i - 1] > cfg.stale_s:
            raw.append(None)
            continue
        p0 = logp[i - 1]
        k5 = int(np.searchsorted(tf, float(t) + cfg.crash_window_s, 'right'))
        move = float(np.abs(logp[i:k5] - p0).max()) * 1e4 if k5 > i else 0.0
        st, sp = ts[i - 1:j].astype(float), logp[i - 1:j]
        rv = float(np.sqrt(np.mean((np.diff(sp) * 1e4) ** 2 / np.diff(st))))
        buckets = ((st[1:] - t - 1) // cfg.er_bucket_s).astype(np.int64)    # (t, t+30], (t+30, t+60], ...
        last = np.r_[np.flatnonzero(np.diff(buckets)), buckets.size - 1]
        path = np.r_[sp[0], sp[1:][last]]                                      # start price, then bucket closes
        if path.size < 3:
            raw.append(None)
            continue
        steps = np.abs(np.diff(path)).sum()
        raw.append((move, rv, float(abs(path[-1] - path[0]) / steps) if steps > 0 else 0.0))
    rvs = [x[1] for x in raw if x is not None]
    median_rv = float(np.median(rvs)) if rvs else None
    out = []
    for x in raw:
        if x is None:
            out.append(None)
        elif x[0] >= cfg.crash_move_bps:
            out.append(CRASH)
        elif median_rv and x[1] >= cfg.jumpy_vol_ratio * median_rv:
            out.append(JUMPY)
        else:
            out.append(CALM_TREND if x[2] >= cfg.er_unlock else SIDEWAYS)
    return out


def classifier_states(ts: np.ndarray, px: np.ndarray, eval_t: np.ndarray, cfg: RegimeConfig) -> tuple:
    """Causal state at each eval time (the last update at or before it), update counts per state, transitions
    and the state after every update."""
    clf = RegimeClassifier(cfg)
    states = [clf.update(float(t), float(p)).state for t, p in zip(ts, px)]
    out = []
    for t in eval_t:
        i = int(np.searchsorted(ts, t, 'right')) - 1
        out.append(states[i] if i >= 0 and t - ts[i] <= cfg.stale_s else UNKNOWN)
    transitions = sum(1 for a, b in zip(states, states[1:]) if a != b)
    return out, dict(Counter(states)), transitions, states


def crash_events(ts: np.ndarray, px: np.ndarray, states_all: list, cfg: RegimeConfig) -> dict:
    """Realized crash moments vs CRASH episodes of the classifier. A realized crash is the first second at which
    the move from the trailing 5-min extreme reaches crash_move_bps, i.e. the first moment any causal rule could
    see it (events closer than crash_cooldown_s merge). Detected = the classifier enters CRASH while that move is
    still inside the window; latency is measured from the crossing, not from the start of the move."""
    logp = np.log(px)
    tf = np.asarray(ts, dtype=np.float64)
    lo = np.searchsorted(tf, tf - cfg.crash_window_s, 'left')     # vectorized once (was O(n) per second)
    crossings, k = [], -math.inf
    for i, t in enumerate(ts):
        if t < k:
            continue
        w = logp[lo[i]:i + 1]
        if max(w.max() - logp[i], logp[i] - w.min()) * 1e4 >= cfg.crash_move_bps:
            crossings.append(int(t))
            k = t + cfg.crash_cooldown_s
    entries = [int(ts[i]) for i in range(1, len(states_all)) if states_all[i] == CRASH != states_all[i - 1]]
    if states_all and states_all[0] == CRASH:
        entries.insert(0, int(ts[0]))
    latency = []                                   # 0 when the classifier was already in CRASH at the crossing
    for s in crossings:
        i0, i1 = int(np.searchsorted(tf, s, 'left')), int(np.searchsorted(tf, s + cfg.crash_window_s, 'right'))
        hit = next((int(ts[i]) for i in range(i0, i1) if states_all[i] == CRASH), None)
        latency.append(None if hit is None else hit - s)
    false = [e for e in entries if not any(s <= e <= s + cfg.crash_window_s for s in crossings)]
    return {'realized_crash_starts': len(crossings), 'classifier_crash_episodes': len(entries),
            'detected': sum(x is not None for x in latency), 'latency_s': latency, 'false_episodes': len(false)}


RESTRICTIVE = (JUMPY, CRASH, UNKNOWN)          # states that cut size or block directional entries


def _share(num: int, den: int):
    return num / den if den else None


def guard_metrics(pairs: list) -> dict:
    """pairs = (classifier state, ex-post label) where a label exists. A guard should be restrictive when the
    label is JUMPY/CRASH (protection_recall) and not when it is calm (false_restriction)."""
    danger = [p for p, t in pairs if t in (JUMPY, CRASH)]
    calm = [p for p, t in pairs if t in (CALM_TREND, SIDEWAYS)]
    covered = [(p, t) for p, t in pairs if p != UNKNOWN]
    return {'danger_points': len(danger), 'calm_points': len(calm),
            'protection_recall': _share(sum(p in RESTRICTIVE for p in danger), len(danger)),
            'false_restriction': _share(sum(p in RESTRICTIVE for p in calm), len(calm)),
            'recall': {s: _share(sum(p == t for p, t in covered if t == s), sum(t == s for _, t in covered))
                       for s in LABELS},
            'precision': {s: _share(sum(p == t for p, t in covered if p == s), sum(p == s for p, _ in covered))
                          for s in LABELS}}


def regime_section(kind: str | None, base: Path | None, symbols: list, start: float, end: float,
                   cfg: RegimeConfig | None = None) -> dict:
    cfg = cfg or RegimeConfig()
    sec = {'source': kind, 'dir': None if base is None else str(base), 'symbols': {},
           'definition': f'state at t vs ex-post label of (t, t+{HORIZON_S}s], every {EVAL_STEP_S}s'}
    if kind is None:
        sec['verdict'] = 'UNKNOWN: no price source (data/recordings, data/l2 or --ticks-dir) for the window'
        return sec
    lo, hi = start - 3600, end + HORIZON_S
    days = sorted({datetime.fromtimestamp(x, timezone.utc).strftime('%Y%m%d') for x in (lo, start, end - 1, hi)})
    pooled_hit = pooled_n = 0
    pooled_truth, pooled_pairs = Counter(), []
    for sym in symbols:
        files = source_files(kind, base, sym, days)
        ts, px, src = load_prices(files, kind, lo, hi)
        row = {'files': len(files), 'seconds_with_price': int(((ts >= start) & (ts < end)).sum()), 'price_field': src}
        if ts.size < 1800:
            row['verdict'] = 'UNKNOWN: fewer than 30 min of prices'
            sec['symbols'][sym] = row
            continue
        eval_t = np.arange(math.ceil(start / EVAL_STEP_S) * EVAL_STEP_S, end, EVAL_STEP_S, dtype=np.int64)
        truth = forward_labels(ts, px, eval_t, cfg)
        pred, shares, transitions, all_states = classifier_states(ts, px, eval_t, cfg)
        both = [(p, t) for p, t in zip(pred, truth) if t is not None and p != UNKNOWN]
        hits = sum(p == t for p, t in both)
        tc = Counter(t for t in truth if t is not None)
        row.update({'eval_points': int(eval_t.size), 'labelled': sum(t is not None for t in truth),
                    'covered': len(both), 'coverage': len(both) / max(1, sum(t is not None for t in truth)),
                    'accuracy': hits / len(both) if both else None,
                    'majority_baseline': max(tc.values()) / sum(tc.values()) if tc else None,
                    'truth_counts': dict(tc), 'pred_counts': dict(Counter(pred)),
                    'confusion': {p: dict(Counter(t for q, t in both if q == p)) for p in LABELS},
                    'state_updates': shares, 'transitions': transitions,
                    'crash': crash_events(ts, px, all_states, cfg)})
        pairs = [(p, t) for p, t in zip(pred, truth) if t is not None]
        row['guard'] = guard_metrics(pairs)
        pooled_hit, pooled_n = pooled_hit + hits, pooled_n + len(both)
        pooled_truth.update(tc)
        pooled_pairs += pairs
        sec['symbols'][sym] = row
    sec['pooled'] = {'covered': pooled_n, 'accuracy': pooled_hit / pooled_n if pooled_n else None,
                     'majority_baseline': (max(pooled_truth.values()) / sum(pooled_truth.values())
                                           if pooled_truth else None),
                     'guard': guard_metrics(pooled_pairs)}
    p = sec['pooled']
    sec['verdict'] = ('UNKNOWN: no labelled, covered point in the window' if not pooled_n else
                      f'accuracy {p["accuracy"]:.3f} vs majority baseline {p["majority_baseline"]:.3f} '
                      f'on {pooled_n} points' + (' (n < 30: no inference)' if pooled_n < MIN_N else ''))
    return sec


def pick_source(ticks_dir: Path | None, start: float, end: float) -> tuple:
    day = datetime.fromtimestamp(start, timezone.utc).strftime('%Y%m%d')
    options = [('ticks', ticks_dir)] if ticks_dir else []
    options += [('recordings', RECORDINGS), ('l2', L2_DIR)]
    for kind, base in options:
        if base is None or not Path(base).is_dir():
            continue
        syms = sorted(p.name for p in Path(base).iterdir() if p.is_dir() and source_files(kind, Path(base), p.name, [day]))
        if syms:
            return kind, Path(base), syms
    return None, None, []


# ------------------------------------------------------------------ proposals and report
def proposals(slip: dict, regime: dict) -> list:
    out = []
    s = slip['all_in_bps']
    if s['n'] >= MIN_N and s['ci95_mean'][0] > COST_ALL_IN_BPS:
        out.append(f'Replays and gates use {COST_ALL_IN_BPS:g} bps; realized all-in CI95 lower bound is '
                   f'{s["ci95_mean"][0]:.2f} bps. Owner decision: re-run net-EV replays at the realized cost.')
    if s['n'] < MIN_N:
        out.append('Slippage sample below 30 round trips: no change to the 8.5 bps model is supported.')
    if slip['exit_slip_bps']['unknown']:
        out.append(f'{slip["exit_slip_bps"]["unknown"]} round trip(s) have no exit_ref (exchange stop/TP exits); '
                   'recording the trigger price would make their exit slippage measurable.')
    p = regime.get('pooled') or {}
    if p.get('covered', 0) >= MIN_N and p['accuracy'] is not None and p['accuracy'] <= p['majority_baseline']:
        out.append('Regime classifier does not beat the majority-class baseline in this window: keep it out of '
                   'order flow; any threshold change needs pre-registration and a time-separated test.')
    gm = p.get('guard') or {}
    if gm.get('calm_points', 0) >= MIN_N and (gm.get('false_restriction') or 0) > 0.30:
        out.append(f'The classifier is restrictive on {gm["false_restriction"]:.0%} of calm-labelled points '
                   f'(protection recall {gm["protection_recall"] if gm["protection_recall"] is None else round(gm["protection_recall"], 3)}). '
                   'Candidate for a pre-registered change tested on unseen dates (e.g. the 4.5-sigma tail memory), '
                   'not for tuning on this window.')
    if regime.get('source') is None:
        out.append(r'No prices were recorded for the window: run scripts\GHI_BANG_L2.bat (public streams, no '
                   'keys) so the next audit can score the classifier.')
    return out


def build(end: str | None = None, ticks_dir: Path | None = None, ledger: Path = LEDGER,
          now: datetime | None = None) -> dict:
    start, stop = window_for(end, now)
    rows = []
    if Path(ledger).exists():
        with open(ledger, encoding='utf-8') as fh:
            for line in fh:
                try:
                    rows.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    slip = slippage_section(rows, start, stop)
    kind, base, syms = pick_source(ticks_dir, start, stop)
    regime = regime_section(kind, base, syms, start, stop)
    return {'schema': 'nightly_audit/1', 'window_utc': [utc(start), utc(stop)],
            'day': datetime.fromtimestamp(start, timezone.utc).strftime('%Y-%m-%d'),
            'generated_utc': (now or datetime.now(timezone.utc)).strftime('%Y-%m-%dT%H:%M:%SZ'),
            'ledger': str(ledger), 'slippage': slip, 'regime': regime, 'proposals': proposals(slip, regime),
            'read_only': 'report files only; no code, config, profile, tunable or execution parameter is written'}


def _fmt(s: dict, key: str = 'mean') -> str:
    if not s.get('n'):
        return f'UNKNOWN (n 0, unknown {s.get("unknown", 0)})'
    ci = s.get('ci95_mean')
    return (f'{s[key]:+.2f} bps (n {s["n"]}, unknown {s["unknown"]}'
            + (f', CI95 {ci[0]:+.2f}..{ci[1]:+.2f})' if ci else ')'))


def _guard(gm: dict) -> str:
    def f(x):
        return 'n/a' if x is None else f'{x:.3f}'
    return (f'protection recall {f(gm["protection_recall"])} on {gm["danger_points"]} JUMPY/CRASH-labelled points, '
            f'false restriction {f(gm["false_restriction"])} on {gm["calm_points"]} calm-labelled points; '
            f'JUMPY precision {f(gm["precision"][JUMPY])} recall {f(gm["recall"][JUMPY])}')


def markdown(rep: dict) -> str:
    s, g = rep['slippage'], rep['regime']
    lines = [f'# Nightly audit {rep["day"]}', '',
             f'Window {rep["window_utc"][0]} to {rep["window_utc"][1]} UTC. Generated {rep["generated_utc"]}. '
             f'{rep["read_only"]}.', '', '## Realized slippage vs the 8.5 bps model', '',
             f'- Round trips in window: {s["rows"]} (modes {s["modes"] or "none"}, venues {s["venues"] or "none"}).',
             f'- All-in (fees + entry + exit slippage): {_fmt(s["all_in_bps"])} vs model {COST_ALL_IN_BPS:g}.',
             f'- Fees: {_fmt(s["fee_bps"])} vs {FEE_MODEL_BPS:g}. Slippage (entry + exit): {_fmt(s["slippage_bps"])}'
             f' vs {SPREAD_SLIPPAGE_BPS:g}.',
             f'- Entry slippage {_fmt(s["entry_slip_bps"])}; exit slippage {_fmt(s["exit_slip_bps"])}.',
             f'- Verdict: {s["verdict"]}.', '', '## Regime-classifier accuracy (last 24 h)', '',
             f'- Source: {g["source"] or "none"}' + (f' ({g["dir"]})' if g.get('dir') else '')
             + f'. Definition: {g["definition"]}.']
    for sym, r in g.get('symbols', {}).items():
        if r.get('accuracy') is None and 'verdict' in r:
            lines.append(f'- {sym}: {r["verdict"]} ({r["seconds_with_price"]} s of prices).')
            continue
        acc = 'n/a' if r['accuracy'] is None else f'{r["accuracy"]:.3f}'
        base = 'n/a' if r['majority_baseline'] is None else f'{r["majority_baseline"]:.3f}'
        c = r['crash']
        lines.append(f'- {sym}: accuracy {acc} vs majority {base}; covered {r["covered"]}/{r["labelled"]} labelled '
                     f'points; truth {r["truth_counts"]}; predicted {r["pred_counts"]}; transitions '
                     f'{r["transitions"]}; crashes realized {c["realized_crash_starts"]}, detected {c["detected"]}, '
                     f'false CRASH episodes {c["false_episodes"]}.')
        lines.append(f'  - guard: {_guard(r["guard"])}')
    if (g.get('pooled') or {}).get('guard'):
        lines.append(f'- Pooled guard: {_guard(g["pooled"]["guard"])}')
    lines += [f'- Verdict: {g["verdict"]}.', '', '## Proposals (text only, not applied)', '']
    lines += [f'- {p}' for p in rep['proposals']] or ['- None.']
    return '\n'.join(lines) + '\n'


def write_report(rep: dict, out_dir: Path = NIGHTLY_DIR) -> list:
    """The only writer: nightly_<day>.json / .md inside out_dir, each via a temp file + atomic replace."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, body in ((f'nightly_{rep["day"]}.json', json.dumps(rep, indent=1, default=float) + '\n'),
                       (f'nightly_{rep["day"]}.md', markdown(rep))):
        path = out_dir / name
        tmp = path.with_suffix(path.suffix + '.tmp')
        tmp.write_text(body, encoding='utf-8')
        os.replace(tmp, path)
        paths.append(path)
    return paths


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--end', help='window end, YYYY-MM-DD at 00:00 UTC (default: the last 00:00 UTC)')
    ap.add_argument('--ticks-dir', type=Path, help='public aggTrades <dir>/<SYMBOL>/<YYYYMMDD>.jsonl.gz')
    ap.add_argument('--ledger', type=Path, default=LEDGER)
    ap.add_argument('--out-dir', type=Path, default=NIGHTLY_DIR)
    ap.add_argument('--dry-run', action='store_true')
    a = ap.parse_args(argv)
    rep = build(a.end, a.ticks_dir, a.ledger)
    print(markdown(rep))
    if not a.dry_run:
        for p in write_report(rep, a.out_dir):
            print(f'wrote {p}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
