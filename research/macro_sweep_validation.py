"""MLS-2: one confirmation read of the MLS-1 search final on unseen data, the point-in-time universe's opportunity
frequency, and the profit-funded Kelly trajectory (offline: no orders, no network).

Frozen in docs/research_upgrade/plan_mls2_validation_2026_11.md BEFORE the fresh klines were downloaded.
    python tools/fetch_klines_fresh.py --symbols <11 symbols> --start 2026-10-01      # F: data/klines/fresh
    python tools/universe_months.py --top 40 --end 2026-09 --out data/universe_months_top40.json   # names of ranks 21-40
    python -m research.macro_sweep_validation
Partitions: F = the 11 MLS-1 symbols from 2026-10-01; X = the other symbols of data/universe_months.json in their
universe months (each contiguous run of monthly zips detected separately). A trade still open when its data ends is
closed at the last bar (the simulator's TIME exit) and counted in `cut_at_data_end`.
"""
from __future__ import annotations

import argparse
import json
import math
import random
import time
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from core.position_sizer import kelly_lcb, profit_funded_risk
from research import macro_sweep_study as mls
from research.smc_fibo_backtest import HOLDOUT, KLINES, SYMBOLS as CORE, load_zips, ms, stats, welch_lcb

ROOT = Path(__file__).resolve().parents[1]
PLAN = 'docs/research_upgrade/plan_mls2_validation_2026_11.md'
FRESH = ROOT / 'data' / 'klines' / 'fresh'
UNIVERSE = ROOT / 'data' / 'universe_months.json'
UNIVERSE40 = ROOT / 'data' / 'universe_months_top40.json'
FINAL = mls.Cfg(trigger='ABS', stop='STRUCT', zone_lo=0.6319359610818935, zone_hi=0.8420870616291642,
                hold_h=68.95329952863473, tp_lo=179.67696053016616)
FRESH_START, UNIVERSE_SPAN = '2026-10-01', ('2025-01-01', '2026-10-01')
SEED, N_TRIALS, MIN_N = 20261115, 49, 100
DAY = 86_400_000
BANKROLL, BASE_RISK = 5.0, 0.05


# ------------------------------------------------------------------------------------------------------- data
def month_of(t_ms: int) -> str:
    return time.strftime('%Y-%m', time.gmtime(t_ms / 1000))


def month_runs(files) -> list:
    """Contiguous runs of monthly kline zips named `SYMBOL-1m-YYYY-MM.zip` (a gap starts a new run)."""
    runs, prev = [], None
    for p in sorted(files, key=lambda p: p.name):
        k = int(p.name[-11:-7]) * 12 + int(p.name[-6:-4])
        if prev is not None and k == prev + 1:
            runs[-1].append(p)
        else:
            runs.append([p])
        prev = k
    return runs


def months_by_symbol(universe: dict) -> dict:
    out = defaultdict(set)
    for ym, syms in universe.items():
        for s in syms:
            out[s].add(ym)
    return out


def mark_cut(rows: list, cfg: mls.Cfg) -> list:
    """Flag trades the end of their data closed early (TIME exit before the configured hold)."""
    hold = cfg.hold_h * 3_600_000
    for r in rows:
        r['cut'] = r['why'] == 'TIME' and r['t_exit'] - r['t_fill'] < hold - 120_000
    return rows


def core_trades(rng: random.Random, core=CORE, klines: Path = KLINES, fresh: Path = FRESH, log=print) -> tuple:
    """Frozen configuration on the 11 symbols over archive + fresh files -> (all rows, end of the common data, ms)."""
    rows, ends = [], []
    for s in core:
        files = sorted(klines.glob(f'{s}-1m-????-??.zip')) + sorted(fresh.glob(f'{s}-1m-*.zip'))
        sym = mls.symbol_data(s, load_zips(files))
        ends.append(int(sym['m1']['t'][-1]) + 60_000)
        got = mark_cut(mls.run_cfg(sym, FINAL, rng), FINAL)
        rows += got
        log(f'{s}: {len(got)} trades, {sum(r["t_fill"] >= ms(FRESH_START) for r in got)} fresh')
    return rows, min(ends)


def xsec_trades(universe: dict, rng: random.Random, core=CORE, klines: Path = KLINES, log=print) -> list:
    """Frozen configuration on every non-core universe symbol; a trade counts in that symbol's universe months only."""
    rows, months = [], months_by_symbol(universe)
    for s in sorted(months):
        if s in core:
            continue
        n = 0
        for run in month_runs(klines.glob(f'{s}-1m-????-??.zip')):
            if not any(p.name[-11:-4] in months[s] for p in run):
                continue
            sym = mls.symbol_data(s, load_zips(run))
            got = [r for r in mark_cut(mls.run_cfg(sym, FINAL, rng), FINAL) if month_of(r['t_fill']) in months[s]]
            rows += got
            n += len(got)
        log(f'{s}: {n} trades in {len(months[s])} universe months')
    return rows


# ------------------------------------------------------------------------------------------------- statistics
def clustered_se(x, groups) -> float | None:
    """Standard error of the mean with trades clustered by group (UTC fill day): sqrt(G/(G-1) sum_g S_g^2) / n."""
    x = np.asarray(x, float)
    if len(x) < 2:
        return None
    sums = defaultdict(float)
    for v, g in zip(x - x.mean(), groups):
        sums[g] += v
    g = len(sums)
    return float(math.sqrt(g / (g - 1) * sum(v * v for v in sums.values())) / len(x)) if g > 1 else None


def read(rows: list, n_trials: int | None = None) -> dict:
    n = len(rows)
    if n == 0:
        return {'n': 0}
    x = [r['net_bps'] for r in rows]
    st = stats(x, [r['risk_bps'] + mls.STOP_COST_BPS for r in rows])
    se_cl = clustered_se(x, [r['t_fill'] // DAY for r in rows])
    ses = [v for v in (st['se'], se_cl) if v is not None]
    se, mean = (max(ses) if ses else None), float(np.mean(x))
    per = defaultdict(list)
    for r in rows:
        per[r['symbol']].append(r['net_bps'])
    big = [v for v in per.values() if len(v) >= 5]
    plc = [p for r in rows for p in r.get('placebo_net_bps', ())]
    return {'n': n, 'mean_net_bps': round(mean, 3), 'se_iid': st['se'], 'se_clustered': None if se_cl is None else round(se_cl, 3),
            'se': None if se is None else round(se, 3), 'lcb95': None if se is None else round(mean - 1.645 * se, 3),
            'ucb95': None if se is None else round(mean + 1.645 * se, 3), 'win_rate': st['win_rate'],
            'profit_factor': st['profit_factor'], 'mean_R': st.get('mean_R'),
            'gross_mean_bps': round(float(np.mean([r['gross_bps'] for r in rows])), 3),
            'vs_placebo': welch_lcb(x, plc), 'placebo_mean_bps': round(float(np.mean(plc)), 3) if plc else None,
            'symbols': len(per), 'symbols_ge5': len(big), 'symbols_ge5_positive': sum(1 for v in big if np.mean(v) > 0),
            'exits': dict(Counter(r['why'] for r in rows)), 'cut_at_data_end': sum(1 for r in rows if r.get('cut')),
            'dsr': mls.deflated(x, n_trials) if n_trials else None, 'schedules': mls.reprice(rows)}


def verdict(u: dict) -> dict:
    """Section 3 of the plan, on U = F + X."""
    if not u.get('n'):
        return {'verdict': 'NOT CONFIRMED', 'checks': {}, 'why': 'no trade'}
    checks = {f'n>={MIN_N}': u['n'] >= MIN_N, 'lcb95>0': u['lcb95'] is not None and u['lcb95'] > 0,
              'beats_placebo_lcb95>0': (u['vs_placebo'].get('lcb95') or -1e9) > 0,
              'half_of_symbols_ge5_positive': u['symbols_ge5'] > 0 and 2 * u['symbols_ge5_positive'] >= u['symbols_ge5'],
              'dsr>=0.95': u['dsr'] is not None and u['dsr'] >= 0.95}
    if all(checks.values()):
        v = 'CONFIRMED'
    else:
        v = 'REFUTED' if u['ucb95'] is not None and u['ucb95'] < 0 else 'NOT CONFIRMED'
    return {'verdict': v, 'checks': checks}


# -------------------------------------------------------------------------------------- frequency, concurrency
def frequency(rows: list, start: str, end: str) -> dict:
    """Trades per day and open positions over [start, end) (UTC days), time-weighted."""
    lo, hi = ms(start), ms(end)
    r = [x for x in rows if lo <= x['t_fill'] < hi]
    days = (hi - lo) // DAY
    per_day = np.zeros(days)
    for x in r:
        per_day[(x['t_fill'] - lo) // DAY] += 1
    dur, level, t_prev, top = defaultdict(float), 0, lo, 0
    for t, d in sorted([(x['t_fill'], 1) for x in r] + [(min(x['t_exit'], hi), -1) for x in r]):
        dur[level] += t - t_prev
        t_prev, level = t, level + d
        top = max(top, level)
    dur[level] += hi - t_prev
    acc, p95 = 0.0, 0
    for k in sorted(dur):
        acc += dur[k]
        if acc >= 0.95 * (hi - lo):
            p95 = k
            break
    hold = [(x['t_exit'] - x['t_fill']) / 3_600_000 for x in r]
    london = sum(1 for x in r if (x['t_signal'] // 60_000 - 5) % 1440 < mls.NY_MIN)
    return {'days': int(days), 'trades': len(r), 'trades_per_day': round(len(r) / days, 4),
            'median_per_day': float(np.median(per_day)), 'p95_per_day': float(np.percentile(per_day, 95)),
            'max_per_day': int(per_day.max()), 'days_without_trade': round(float((per_day == 0).mean()), 4),
            'mean_hold_h': round(float(np.mean(hold)), 2) if hold else None,
            'open_mean': round(sum(k * v for k, v in dur.items()) / (hi - lo), 4), 'open_p95': p95, 'open_max': top,
            'time_flat': round(dur[0] / (hi - lo), 4), 'london_share': round(london / len(r), 4) if r else None}


def symbol_days(universe: dict, keep) -> int:
    """Symbol-days of the universe months for the symbols `keep(symbol)` accepts."""
    n = 0
    for ym, syms in universe.items():
        y, m = int(ym[:4]), int(ym[5:])
        days = (ms(f'{y + (m == 12)}-{m % 12 + 1:02d}-01') - ms(f'{ym}-01')) // DAY
        n += days * sum(1 for s in syms if keep(s))
    return n


def expand(measured: dict, rate_x: float, universe20: dict, universe40: dict) -> dict:
    """Top-40 figures EXTRAPOLATED from the measured top-20 run: the symbols a month's top 40 adds trade at the
    measured non-core rate; mean open positions by Little's law (arrival rate x mean hold)."""
    extra = 0
    for ym, syms in universe40.items():
        if ym in universe20:
            y, m = int(ym[:4]), int(ym[5:])
            days = (ms(f'{y + (m == 12)}-{m % 12 + 1:02d}-01') - ms(f'{ym}-01')) // DAY
            extra += days * len(set(syms) - set(universe20[ym]))
    months = [ym for ym in universe40 if ym in universe20]
    per_day = measured['trades_per_day'] + rate_x * extra / measured['days']
    return {'months': len(months), 'mean_symbols': round(float(np.mean([len(universe40[m]) for m in months])), 1),
            'extra_symbol_days': int(extra), 'trades_per_day': round(per_day, 4),
            'open_mean': round(per_day * (measured['mean_hold_h'] or 0.0) / 24.0, 4)}


# ------------------------------------------------------------------------------------------ Kelly, trajectory
def evidence(rows: list) -> dict:
    r = [x['net_R'] for x in rows]
    w, l = [v for v in r if v > 0], [-v for v in r if v <= 0]
    b = (sum(w) / len(w)) / (sum(l) / len(l)) if w and l and sum(l) > 0 else None
    p = len(w) / len(r) if r else None
    lcb = kelly_lcb(len(w), len(l), b)
    return {'wins': len(w), 'losses': len(l), 'sum_win': sum(w), 'sum_loss': sum(l), 'payoff_b': b,
            'kelly_point': None if b is None else p - (1 - p) / b, 'kelly_lcb': lcb, 'f_applied': 0.25 * max(0.0, lcb)}


def trajectory(net_r: list, trades_per_day: float, ev: dict, days=(30, 90, 180, 365), paths: int = 2000,
               seed: int = SEED) -> dict:
    """5 U bankroll under profit_funded_risk, outcomes bootstrapped from `net_r`, evidence updated along the path."""
    rng = random.Random(seed)
    at = {d: int(round(trades_per_day * d)) for d in days}            # trades done by each day mark
    n, eq_at, scaled, risk_end = (at[days[-1]] if net_r else 0), {d: [] for d in days}, 0, []
    for _ in range(paths):
        eq, w, l, sw, sl, risk = BANKROLL, ev['wins'], ev['losses'], ev['sum_win'], ev['sum_loss'], BASE_RISK
        eqs = [eq]
        for _i in range(n):
            b = (sw / w) / (sl / l) if w and l and sl > 0 else None
            risk = min(profit_funded_risk(eq, BANKROLL, w, l, b, BASE_RISK), max(eq, 0.0))
            r = rng.choice(net_r)
            eq += risk * r
            if r > 0:
                w, sw = w + 1, sw + r
            else:
                l, sl = l + 1, sl - r
            eqs.append(eq)
        for d in days:
            eq_at[d].append(eqs[min(at[d], n)])
        risk_end.append(risk)
        scaled += risk > BASE_RISK + 1e-12
    q = lambda v, a: round(float(np.percentile(v, a)), 4)
    return {'trades_per_day': trades_per_day, 'trades_in_365d': n,
            'equity': {d: {'p05': q(eq_at[d], 5), 'median': q(eq_at[d], 50), 'p95': q(eq_at[d], 95)} for d in days},
            'p_below_4u_365d': round(float(np.mean(np.asarray(eq_at[days[-1]]) < 4.0)), 4),
            'p_plus_10u_365d': round(float(np.mean(np.asarray(eq_at[days[-1]]) >= BANKROLL + 10.0)), 4),
            'mean_risk_end': round(float(np.mean(risk_end)), 5), 'paths_scaled_at_end': round(scaled / paths, 4)}


# ------------------------------------------------------------------------------------------------------ driver
def run(seed: int = SEED, core_syms=CORE, klines: Path = KLINES, fresh: Path = FRESH, universe_path: Path = UNIVERSE,
        universe40_path: Path = UNIVERSE40, paths: int = 2000, log=print) -> dict:
    t0 = time.time()
    universe = json.loads(universe_path.read_text())['months']
    months = months_by_symbol(universe)
    rng = random.Random(seed)
    core, end = core_trades(rng, core_syms, klines, fresh, log)
    x = xsec_trades(universe, rng, core_syms, klines, log)
    f = [r for r in core if r['t_fill'] >= ms(FRESH_START)]
    ho = [r for r in core if ms(HOLDOUT[0]) <= r['t_fill'] < ms(HOLDOUT[1])]
    u = read(f + x, N_TRIALS)
    out = {'plan': PLAN, 'cfg': FINAL.__dict__, 'seed': seed, 'fresh_end_ms': end,
           'fresh_days': (end - ms(FRESH_START)) / DAY, 'F': read(f), 'X': read(x), 'U': u, 'verdict': verdict(u),
           'HOLDOUT_completed': read(ho), 'POOLED_HOLDOUT_F_X': read(ho + f + x, N_TRIALS)}
    uni_core = [r for r in core if month_of(r['t_fill']) in months[r['symbol']]]
    top20 = frequency(uni_core + x, *UNIVERSE_SPAN)
    sd_core = symbol_days(universe, lambda s: s in core_syms)
    sd_x = symbol_days(universe, lambda s: s not in core_syms)
    n_core = sum(1 for r in uni_core if ms(UNIVERSE_SPAN[0]) <= r['t_fill'] < ms(UNIVERSE_SPAN[1]))
    rate_x = len(x) / sd_x if sd_x else 0.0
    out['frequency'] = {'core11': frequency(uni_core, *UNIVERSE_SPAN), 'top20_measured': top20,
                        'per_symbol_day': {'core': round(n_core / sd_core, 5) if sd_core else None, 'x': round(rate_x, 5),
                                           'core_symbol_days': sd_core, 'x_symbol_days': sd_x}}
    if universe40_path.exists():
        out['frequency']['top40_extrapolated'] = expand(top20, rate_x, universe,
                                                        json.loads(universe40_path.read_text())['months'])
    pooled = ho + f + x
    ev = evidence(pooled)
    rates = {'core11': out['frequency']['core11']['trades_per_day'], 'top20_measured': top20['trades_per_day']}
    if 'top40_extrapolated' in out['frequency']:
        rates['top40_extrapolated'] = out['frequency']['top40_extrapolated']['trades_per_day']
    out['kelly'] = {k: v for k, v in ev.items() if k not in ('sum_win', 'sum_loss')}
    out['trajectory'] = {k: trajectory([r['net_R'] for r in pooled], v, ev, paths=paths, seed=seed) for k, v in rates.items()}
    out['trades'] = {k: [{c: r[c] for c in ('symbol', 'side', 't_fill', 't_exit', 'why', 'gross_bps', 'net_bps', 'risk_bps',
                                           'tp_bps', 'net_R', 'cut')} for r in v] for k, v in (('F', f), ('X', x))}
    out['seconds'] = round(time.time() - t0, 1)
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--seed', type=int, default=SEED)
    ap.add_argument('--out', default=str(ROOT / 'data' / 'macro_sweep_validation.json'))
    a = ap.parse_args(argv)
    out = run(a.seed)
    Path(a.out).write_text(json.dumps(out, indent=1, default=str), encoding='utf-8')
    for k in ('F', 'X', 'U', 'HOLDOUT_completed', 'POOLED_HOLDOUT_F_X'):
        s = out[k]
        print(f"{k:20s} n={s.get('n', 0):4d} net={s.get('mean_net_bps')}±{s.get('se')} (iid {s.get('se_iid')}, clustered "
              f"{s.get('se_clustered')}) lcb={s.get('lcb95')} ucb={s.get('ucb95')} win={s.get('win_rate')} R={s.get('mean_R')} "
              f"vsPlacebo={s.get('vs_placebo', {}).get('diff')} (lcb {s.get('vs_placebo', {}).get('lcb95')}) "
              f"sym>=5 {s.get('symbols_ge5_positive')}/{s.get('symbols_ge5')} dsr={s.get('dsr')} cut={s.get('cut_at_data_end')}")
    print('VERDICT', json.dumps(out['verdict']))
    print('FREQUENCY', json.dumps(out['frequency']))
    print('KELLY', json.dumps(out['kelly']))
    for k, v in out['trajectory'].items():
        print('TRAJECTORY', k, json.dumps(v))
    print(f"wrote {a.out} ({out['seconds']:.0f} s)")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
