"""Regime classifier variant scoring on public aggTrade days (2026-11-07, RC-1). Offline; reads ticks, writes --out only.

    python -m research.regime_v1_eval --ticks-dir F:/factory-quant-opt/data/ticks --start 2026-09-01 --days 14 \
        --out F:/gt_scratch/rc1_dev.json            # development days: choose v1 here
    python -m research.regime_v1_eval --ticks-dir data/ticks --start 2026-10-01 --days 14 --variants v0 v1 \
        --out docs/research_upgrade/snapshot_2026-11-07_regime_v1/rc1_unseen.json    # once, after pre-registration

Each variant runs once over the continuous per-second price series of each symbol (as it would live: no restart
per day). Labels and metrics are tools/nightly_audit's own: every 60 s the causal state is compared with the ex-post
label of the next 15 minutes, the JUMPY label uses each UTC day's median 15-minute volatility.
    jumpy_false_alarm = share of calm-labelled points (CALM_TREND or SIDEWAYS) on which the state is JUMPY
    jumpy_share       = share of all covered points on which the state is JUMPY (v0 on Sept dev days: 38.8 %)
plus JUMPY precision/recall, protection recall vs false restriction, accuracy vs majority, and CRASH crossings caught.
"""
from __future__ import annotations

import argparse
import json
import math
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from core.regime_classifier import CALM_TREND, CRASH, JUMPY, SIDEWAYS, UNKNOWN, RegimeConfig
from tools import nightly_audit as na

SYMBOLS = ('BTCUSDT', 'SOLUSDT', 'DOGEUSDT', 'BNBUSDT')
VARIANTS = {
    'v0': {},
    'z6': {'tail_z': 6.0}, 'z8': {'tail_z': 8.0}, 'z10': {'tail_z': 10.0},
    'n2': {'tail_count': 2}, 'n3': {'tail_count': 3},
    'm60': {'tail_memory_s': 60.0}, 'z6n2': {'tail_z': 6.0, 'tail_count': 2},
    'z8m60': {'tail_z': 8.0, 'tail_memory_s': 60.0}, 'z6m60': {'tail_z': 6.0, 'tail_memory_s': 60.0},
    'notail': {'tail_z': 1e9},
    # dev grid 2: the CRASH trigger keeps v0's tail rule (4.5 sigma, 1 tail); only JUMPY changes
    'n2c': {**{'tail_count': 2}, 'crash_tail_z': 4.5, 'crash_tail_count': 1},
    'n3c': {**{'tail_count': 3}, 'crash_tail_z': 4.5, 'crash_tail_count': 1},
    'z8c': {**{'tail_z': 8.0}, 'crash_tail_z': 4.5, 'crash_tail_count': 1},
    'z10c': {**{'tail_z': 10.0}, 'crash_tail_z': 4.5, 'crash_tail_count': 1},
    'z6n2c': {**{'tail_z': 6.0, 'tail_count': 2}, 'crash_tail_z': 4.5, 'crash_tail_count': 1},
    'z6m60c': {**{'tail_z': 6.0, 'tail_memory_s': 60.0}, 'crash_tail_z': 4.5, 'crash_tail_count': 1},
    'notailc': {**{'tail_z': 1e9}, 'crash_tail_z': 4.5, 'crash_tail_count': 1},
}


def day_list(start: str, days: int) -> list:
    d0 = datetime.strptime(start, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    return [(d0 + timedelta(days=i)) for i in range(days)]


def second_prices(ticks_dir: Path, symbol: str, lo: float, hi: float) -> tuple:
    """Last aggTrade price per whole second in [lo, hi] from <dir>/<SYMBOL>/<YYYYMMDD>.jsonl.gz (PyArrow)."""
    import pyarrow.json as pj
    days = sorted({datetime.fromtimestamp(x, timezone.utc).strftime('%Y%m%d') for x in np.arange(lo, hi + 86400, 86400)})
    secs, pxs = [], []
    for d in days:
        p = ticks_dir / symbol / f'{d}.jsonl.gz'
        if not p.exists():
            continue
        t = pj.read_json(p)
        if t.column('source_time_unit')[0].as_py() != 'ms':
            raise ValueError(f'{p}: expected ms timestamps')
        ts = t.column('source_timestamp').combine_chunks().to_numpy(zero_copy_only=False) // 1000
        px = t.column('p').combine_chunks().to_numpy(zero_copy_only=False).astype(float)
        keep = (ts >= lo) & (ts <= hi) & np.isfinite(px) & (px > 0)
        secs.append(ts[keep]); pxs.append(px[keep])
    if not secs:
        return np.zeros(0, np.int64), np.zeros(0)
    s, p = np.concatenate(secs), np.concatenate(pxs)
    order = np.argsort(s, kind='stable')
    s, p = s[order], p[order]
    last = np.r_[np.flatnonzero(np.diff(s)), s.size - 1]               # last print of each second
    return s[last].astype(np.int64), p[last]


def score_symbol(args) -> dict:
    """One (variant, symbol): run the classifier once over the whole span, score every day's 60 s points."""
    name, overrides, ticks_dir, symbol, start, days = args
    cfg = replace(RegimeConfig(), **overrides)
    dlist = day_list(start, days)
    lo, hi = dlist[0].timestamp() - 3600, dlist[-1].timestamp() + 86400 + na.HORIZON_S
    ts, px = second_prices(Path(ticks_dir), symbol, lo, hi)
    out = {'variant': name, 'symbol': symbol, 'seconds': int(ts.size), 'pairs': [], 'crash': None}
    if ts.size < 1800:
        return out
    eval_all = np.arange(math.ceil(dlist[0].timestamp() / 60) * 60, dlist[-1].timestamp() + 86400, 60, dtype=np.int64)
    pred, _, transitions, states = na.classifier_states(ts, px, eval_all, cfg)
    truth = []
    for d in dlist:                                                    # the audit's per-day JUMPY normalisation
        sel = (eval_all >= d.timestamp()) & (eval_all < d.timestamp() + 86400)
        truth += na.forward_labels(ts, px, eval_all[sel], cfg)
    in_span = (ts >= dlist[0].timestamp()) & (ts < dlist[-1].timestamp() + 86400)
    first = int(np.argmax(in_span)) if in_span.any() else 0
    out.update(pairs=[(p, t) for p, t in zip(pred, truth) if t is not None], transitions=transitions,
               crash=na.crash_events(ts[first:], px[first:], states[first:], cfg))
    return out


def summarize(rows: list) -> dict:
    pairs = [x for r in rows for x in r['pairs']]
    cov = [(p, t) for p, t in pairs if p != UNKNOWN]
    calm = [p for p, t in pairs if t in (CALM_TREND, SIDEWAYS)]
    tc = Counter(t for _, t in cov)
    g = na.guard_metrics(pairs)
    crash = Counter()
    for r in rows:
        if r['crash']:
            crash.update({k: r['crash'][k] for k in ('realized_crash_starts', 'detected', 'false_episodes',
                                                      'classifier_crash_episodes')})
    return {'labelled': len(pairs), 'covered': len(cov),
            'accuracy': sum(p == t for p, t in cov) / len(cov) if cov else None,
            'majority_baseline': max(tc.values()) / len(cov) if cov else None,
            'jumpy_share': sum(p == JUMPY for p, _ in cov) / len(cov) if cov else None,
            'jumpy_false_alarm': sum(p == JUMPY for p in calm) / len(calm) if calm else None,
            'jumpy_precision': g['precision'][JUMPY], 'jumpy_recall': g['recall'][JUMPY],
            'protection_recall': g['protection_recall'], 'false_restriction': g['false_restriction'],
            'crash': dict(crash)}


def run(ticks_dir: str, start: str, days: int, variants: list, symbols=SYMBOLS, workers: int = 8) -> dict:
    t0 = time.perf_counter()
    jobs = [(v, VARIANTS[v], ticks_dir, s, start, days) for v in variants for s in symbols]
    with ProcessPoolExecutor(max_workers=workers) as ex:
        rows = list(ex.map(score_symbol, jobs))
    out = {'ticks_dir': ticks_dir, 'start': start, 'days': days, 'symbols': list(symbols), 'variants': {},
           'definitions': {'jumpy_false_alarm': 'P(state JUMPY | label CALM_TREND or SIDEWAYS)',
                           'jumpy_share': 'P(state JUMPY) over covered points',
                           'labels': 'tools.nightly_audit.forward_labels, per-day JUMPY median'}}
    for v in variants:
        vr = [r for r in rows if r['variant'] == v]
        out['variants'][v] = {'config': asdict(replace(RegimeConfig(), **VARIANTS[v])), 'pooled': summarize(vr),
                              'by_symbol': {r['symbol']: summarize([r]) for r in vr},
                              'seconds': {r['symbol']: r['seconds'] for r in vr}}
    out['wall_s'] = round(time.perf_counter() - t0, 1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--ticks-dir', required=True)
    ap.add_argument('--start', required=True)
    ap.add_argument('--days', type=int, default=14)
    ap.add_argument('--variants', nargs='+', default=list(VARIANTS))
    ap.add_argument('--symbols', nargs='+', default=list(SYMBOLS))
    ap.add_argument('--workers', type=int, default=8)
    ap.add_argument('--out', required=True)
    a = ap.parse_args(argv)
    rep = run(a.ticks_dir, a.start, a.days, a.variants, a.symbols, a.workers)
    for v, r in rep['variants'].items():
        p = r['pooled']
        print(f"{v:8s} JUMPY share {p['jumpy_share']:.3f} FA {p['jumpy_false_alarm']:.3f} P {p['jumpy_precision'] or 0:.3f} "
              f"R {p['jumpy_recall'] or 0:.3f} | prot {p['protection_recall']:.3f} falseR {p['false_restriction']:.3f} | "
              f"acc {p['accuracy']:.3f} base {p['majority_baseline']:.3f} | crash {p['crash']}")
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(rep, indent=1, default=float) + '\n', encoding='utf-8')
    print(f'JSON: {a.out} ({rep["wall_s"]} s)')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
