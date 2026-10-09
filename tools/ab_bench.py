"""Paired A/B benchmark: run variant A and variant B on the SAME historical events, then test B - A.

Micro engine (real core.engine through tools/engine_replay.py, one subprocess per chunk, --jobs in parallel):
    python tools/ab_bench.py micro --source agg --data <aggTrades dir> --symbols SOLUSDT,DOGEUSDT \
        --chunks 2026-10-01T00:00/2026-10-01T06:00,2026-10-03T12:00/2026-10-03T18:00 [--warmup-min 60] \
        --a-set cvd_shield='"on"' --b-set cvd_shield='"off"'  [--a-code <worktree>] [--b-code <worktree>]
    (--days 2026-10-01..2026-10-07 --chunk-h 6  builds the chunk list for you)

Trend lane (core/trend_lane.py on 1h klines + funding; years in seconds):
    python tools/ab_bench.py trend --data <klines dir> --a-cfg '{}' --b-cfg '{"bar":"4h"}' [--cost TAKER]

Pairing unit: one hour of net PnL (micro, U, zeros included) or one day of net return (trend); both arms see the
same tape, so Var(B - A) = Var(A) + Var(B) - 2 cov -> far fewer units than an unpaired live A/B.
Reported: mean diff, bootstrap CI95 (circular blocks), t, rho, efficiency vs unpaired, pairs needed for the
observed sigma, a verdict, and a Deflated-Sharpe style penalty from the number of logged trials.
--register appends the trial to docs/research_upgrade/trial_log.jsonl and a row to experiment_register.md.
"""
from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import math
import subprocess
import sys
import tempfile
import time
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

from core import fast_eval as fe  # noqa: E402

TRIAL_LOG = ROOT / 'docs' / 'research_upgrade' / 'trial_log.jsonl'
REGISTER = ROOT / 'docs' / 'research_upgrade' / 'experiment_register.md'


# ------------------------------------------------------------------ chunks
def build_chunks(days: str, chunk_h: float):
    a, b = days.split('..')
    d0 = datetime.strptime(a, '%Y-%m-%d').replace(tzinfo=timezone.utc)
    d1 = datetime.strptime(b, '%Y-%m-%d').replace(tzinfo=timezone.utc) + timedelta(days=1)
    out, t = [], d0
    while t < d1:
        e = min(d1, t + timedelta(hours=chunk_h))
        out.append((t.strftime('%Y-%m-%dT%H:%M'), e.strftime('%Y-%m-%dT%H:%M')))
        t = e
    return out


def parse_chunks(s: str):
    return [tuple(x.split('/')) for x in s.split(',') if x]


def _iso_minus(s: str, minutes: float) -> str:
    t = datetime.strptime(s, '%Y-%m-%dT%H:%M').replace(tzinfo=timezone.utc) - timedelta(minutes=minutes)
    return t.strftime('%Y-%m-%dT%H:%M')


def run_chunk(args) -> dict:
    """One variant on one chunk in its own process (the replay patches the process clock)."""
    (arm, chunk, source, data, symbols, warmup_min, sets, code_root, l2_dir, rec_dir, balance) = args
    start, end = chunk
    with tempfile.TemporaryDirectory(prefix='ab_') as td:
        out = Path(td) / 'r.json'
        cmd = [sys.executable, str(ROOT / 'tools' / 'engine_replay.py'), '--source', source, '--data', str(data),
               '--symbols', symbols, '--start', _iso_minus(start, warmup_min), '--end', end,
               '--warmup-min', str(warmup_min), '--balance', str(balance), '--out', str(out), '--quiet']
        if l2_dir:
            cmd += ['--l2-dir', str(l2_dir)]
        if rec_dir:
            cmd += ['--rec-dir', str(rec_dir)]
        for s in sets:
            cmd += ['--set', s]
        if code_root:
            cmd += ['--code-root', str(code_root)]
        p = subprocess.run(cmd, capture_output=True, text=True)
        if p.returncode != 0 or not out.exists():
            raise RuntimeError(f'{arm} {chunk}: replay failed: {p.stderr[-800:]}')
        res = json.loads(out.read_text())
    n_warm = int(math.ceil(warmup_min / 60))
    res['hourly_trading'] = res['hourly_pnl_u'][n_warm:]          # hours before the chunk start are warm-up
    res['arm'], res['chunk'] = arm, chunk
    return res


def micro(a) -> dict:
    chunks = parse_chunks(a.chunks) if a.chunks else build_chunks(a.days, a.chunk_h)
    jobs = []
    for arm, sets, code in (('A', a.a_set, a.a_code), ('B', a.b_set, a.b_code)):
        for ch in chunks:
            jobs.append((arm, ch, a.source, a.data, a.symbols, a.warmup_min, sets, code, a.l2_dir, a.rec_dir,
                         a.balance))
    t0 = time.perf_counter()
    res = []
    with cf.ThreadPoolExecutor(max_workers=a.jobs) as ex:
        for r in ex.map(run_chunk, jobs):
            res.append(r)
            print(f"  {r['arm']} {r['chunk'][0]} -> {r['chunk'][1]}: n={r['summary'].get('n', 0)} "
                  f"net {r['summary'].get('net_u', 0.0):+.4f} U ({r['wall_s']:.0f}s)", flush=True)
    wall = time.perf_counter() - t0
    A = {tuple(r['chunk']): r for r in res if r['arm'] == 'A'}
    B = {tuple(r['chunk']): r for r in res if r['arm'] == 'B'}
    ha, hb, pa, pb = [], [], [], []
    for ch in chunks:
        ra, rb = A[tuple(ch)], B[tuple(ch)]
        k = min(len(ra['hourly_trading']), len(rb['hourly_trading']))
        ha += ra['hourly_trading'][:k]
        hb += rb['hourly_trading'][:k]
        pa += [p['bps'] for p in ra['positions']]
        pb += [p['bps'] for p in rb['positions']]
    sim_h = sum(len(A[tuple(c)]['hourly_pnl_u']) for c in chunks) * 2 * len(a.symbols.split(','))
    return {'kind': 'micro', 'unit': 'hour net PnL (U)', 'a': ha, 'b': hb, 'chunks': chunks,
            'positions_a': summarize(pa), 'positions_b': summarize(pb), 'wall_s': wall,
            'sim_symbol_hours': sim_h, 'approx_book': a.source == 'agg',
            'a_label': ' '.join(a.a_set) + (f' code={a.a_code}' if a.a_code else ''),
            'b_label': ' '.join(a.b_set) + (f' code={a.b_code}' if a.b_code else '')}


def summarize(bps):
    if not bps:
        return {'n': 0}
    m = sum(bps) / len(bps)
    sd = (sum((x - m) ** 2 for x in bps) / (len(bps) - 1)) ** 0.5 if len(bps) > 1 else float('nan')
    return {'n': len(bps), 'mean_bps': m, 'sd_bps': sd, 'win_rate': sum(1 for x in bps if x > 0) / len(bps)}


def trend(a) -> dict:
    import trend_backtest as tb
    import factor_scan as fs
    from core import trend_lane as tl
    ca = replace(tl.TrendConfig(), **_cfg(a.a_cfg))
    cb = replace(tl.TrendConfig(), **_cfg(a.b_cfg))
    syms = a.symbols.split(',') if a.symbols else ['BTCUSDT', 'SOLUSDT', 'DOGEUSDT']
    t0 = time.perf_counter()
    cache = {}
    out = {}
    for arm, c in (('a', ca), ('b', cb)):
        if c.bar not in cache:
            cache[c.bar] = tb.load_bars(a.data, syms, c.bar)
        bars, fund = cache[c.bar]
        out[arm] = tl.to_daily(tl.backtest(bars, c, fund, cost_bps=fs.COSTS[a.cost])['bar_returns'])
    lo, hi = fs.day_of(a.start), fs.day_of(a.end)
    days = [d for d in sorted(set(out['a']) & set(out['b'])) if lo <= d <= hi]
    return {'kind': 'trend', 'unit': 'day net return', 'a': [out['a'][d] for d in days],
            'b': [out['b'][d] for d in days], 'wall_s': time.perf_counter() - t0, 'n_days': len(days),
            'a_label': json.dumps(_cfg(a.a_cfg)), 'b_label': json.dumps(_cfg(a.b_cfg)), 'cost': a.cost}


def _cfg(s: str) -> dict:
    d = json.loads(s or '{}')
    if 'lookbacks' in d:
        d['lookbacks'] = tuple(d['lookbacks'])
    return d


# ------------------------------------------------------------------ verdict + register
def verdict(res: dict, n_trials: int) -> dict:
    a, b = res['a'], res['b']
    if len(a) < 3:
        return {'n': len(a), 'decision': 'TOO FEW UNITS'}
    pd = fe.paired_diff(a, b, n_boot=2000, block=max(1, int(len(a) ** (1 / 3))))
    d = [y - x for x, y in zip(a, b)]
    m, sd, sk, ku = fe.moments(d)
    sr = m / sd if sd else 0.0
    # p-value of the paired mean (normal approx) and a deflation for the number of variants tried so far
    p_two = 2 * (1 - fe.norm_cdf(abs(pd['t']))) if pd['t'] == pd['t'] else 1.0
    psr = fe.psr(sr, len(d), sk, ku)
    exp_max = fe.expected_max_sr(1.0 / max(1, len(d) - 1), max(2, n_trials))   # null SR variance ~ 1/(n-1)
    dsr = fe.psr(sr, len(d), sk, ku, sr0=exp_max)
    need = None
    if sd and abs(m) > 0:
        need = fe.required_n(abs(m), sd, 0.05, 0.80, two_sided=True)
    if pd['ci95'][0] > 0 and dsr > 0.95:
        dec = 'B BETTER (significant after deflation)'
    elif pd['ci95'][1] < 0 and (1 - dsr) > 0.95:
        dec = 'B WORSE (significant after deflation)'
    elif pd['ci95'][0] > 0:
        dec = 'B better, NOT significant after deflation'
    elif pd['ci95'][1] < 0:
        dec = 'B worse, NOT significant after deflation'
    else:
        dec = 'NO DIFFERENCE DETECTED'
    return {**pd, 'p_two_sided': p_two, 'psr_diff_gt0': psr, 'dsr_diff_gt0': dsr, 'n_trials': n_trials,
            'units_needed_80pct_power': need, 'sum_a': sum(a), 'sum_b': sum(b), 'decision': dec}


def logged_trials(path: Path = TRIAL_LOG) -> int:
    try:
        return sum(1 for line in path.read_text(encoding='utf-8').splitlines() if line.strip())
    except OSError:
        return 0


def register_rows(path: Path = REGISTER) -> int:
    """Experiments already on record (rows like '| G2 | ...', '| AA3 | ...'): every one counts as a trial."""
    import re
    try:
        return sum(1 for line in path.read_text(encoding='utf-8').splitlines() if re.match(r'^\| [A-Z]+-?\d', line))
    except OSError:
        return 0


def register(res: dict, v: dict, name: str, log: Path = TRIAL_LOG, reg: Path = REGISTER) -> None:
    row = {'t': time.time(), 'name': name, 'kind': res['kind'], 'unit': res['unit'], 'a': res['a_label'],
           'b': res['b_label'], 'n_units': v.get('n'), 'mean_diff': v.get('mean'), 'ci95': v.get('ci95'),
           'p': v.get('p_two_sided'), 'dsr': v.get('dsr_diff_gt0'), 'decision': v.get('decision'),
           'approx_book': res.get('approx_book', False)}
    log.parent.mkdir(parents=True, exist_ok=True)
    with open(log, 'a', encoding='utf-8') as fh:
        fh.write(json.dumps(row, default=float) + '\n')
    if reg.exists():
        with open(reg, 'a', encoding='utf-8') as fh:
            ci = v.get('ci95') or (float('nan'), float('nan'))
            fh.write(f"| AB-{logged_trials(log)} | {res['kind']} | {name}: B={res['b_label']} vs A={res['a_label']} | "
                     f"{v.get('n')} {res['unit']} | Δ {v.get('mean', float('nan')):+.4g} CI95 [{ci[0]:+.4g}, {ci[1]:+.4g}], "
                     f"DSR {v.get('dsr_diff_gt0', float('nan')):.2f} | {v.get('decision')} |\n")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='kind', required=True)
    m = sub.add_parser('micro')
    m.add_argument('--source', choices=('agg', 'l2', 'rec'), default='agg')
    m.add_argument('--data', type=Path, required=True)
    m.add_argument('--l2-dir', type=Path)
    m.add_argument('--rec-dir', type=Path)
    m.add_argument('--symbols', default='SOLUSDT,DOGEUSDT')
    m.add_argument('--chunks', default='')
    m.add_argument('--days', default='')
    m.add_argument('--chunk-h', type=float, default=6.0)
    m.add_argument('--warmup-min', type=float, default=60.0)
    m.add_argument('--balance', type=float, default=5000.0)
    m.add_argument('--a-set', action='append', default=[])
    m.add_argument('--b-set', action='append', default=[])
    m.add_argument('--a-code', type=Path)
    m.add_argument('--b-code', type=Path)
    m.add_argument('--jobs', type=int, default=2)
    t = sub.add_parser('trend')
    t.add_argument('--data', type=Path, required=True)
    t.add_argument('--symbols', default='')
    t.add_argument('--a-cfg', default='{}')
    t.add_argument('--b-cfg', default='{}')
    t.add_argument('--cost', choices=('TAKER', 'MAKER'), default='TAKER')
    t.add_argument('--start', default='2025-01-01')
    t.add_argument('--end', default='2026-09-30')
    for p in (m, t):
        p.add_argument('--name', default='')
        p.add_argument('--register', action='store_true')
        p.add_argument('--json', type=Path)
        p.add_argument('--prior-trials', type=int, default=0, help='variants tried outside the register (e.g. 100 '
                                                                     'for the factor-scan + trend specs)')
    a = ap.parse_args(argv)
    if a.kind == 'micro' and not (a.chunks or a.days):
        ap.error('micro needs --chunks or --days')
    res = micro(a) if a.kind == 'micro' else trend(a)
    n_trials = register_rows() + logged_trials() + a.prior_trials + 1     # every variant ever tried deflates
    v = verdict(res, n_trials)
    print(f"\nA/B {res['kind']} ({res['unit']}): B [{res['b_label']}] - A [{res['a_label']}] | wall {res['wall_s']:.1f}s")
    if v.get('n', 0) >= 3:
        print(f"units {v['n']} | sum A {v['sum_a']:+.4f} B {v['sum_b']:+.4f} | mean diff {v['mean']:+.4g} "
              f"CI95 [{v['ci95'][0]:+.4g}, {v['ci95'][1]:+.4g}] t {v['t']:+.2f} p {v['p_two_sided']:.3f} | "
              f"rho {v['rho']:.2f} ({v['efficiency_vs_unpaired']:.1f}x vs unpaired) | DSR(trials={n_trials}) "
              f"{v['dsr_diff_gt0']:.2f} | units needed {v['units_needed_80pct_power']}")
    print('decision:', v['decision'])
    if res['kind'] == 'micro':
        print('positions A', res['positions_a'], '\npositions B', res['positions_b'])
        if res['approx_book']:
            print('NOTE: --source agg uses a synthetic book (OBI/OFI/WALL degenerate): approximate for book gates.')
    if a.register:
        register(res, v, a.name or f'{res["kind"]} A/B')
    if a.json:
        a.json.write_text(json.dumps({'result': res, 'verdict': v}, indent=1, default=float))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
