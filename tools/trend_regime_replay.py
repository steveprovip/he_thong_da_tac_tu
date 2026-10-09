"""Regime-gate / profit-lock / 10 % sizing replay for the 4H trend lane (request 10-09 ee).  Offline, seconds.

    python tools/trend_regime_replay.py --data <dir: SYMBOL-1h-YYYY-MM.zip + SYMBOL-fundingRate-YYYY-MM.zip>
                                        [--json out.json] [--register]

Event backtest = core/trend_lane.backtest_brackets (size fixed at entry, signal exits, exchange disaster stop
8 x ATR(14) checked intrabar, taker 6 bps / side), the model the demo executor follows.  4H bars from 1h klines;
volume and CVD per bar from the klines' quote volume and taker-buy quote volume (CVD = 2 x taker buy - total).

PRE-REGISTERED before the first run (thresholds are the request's, windows textbook, nothing tuned):
  V0  BASE        signal exits + disaster 8 ATR, legacy |weight| sizing (the running demo lane)
  G1  trend_only  entries only when KER10 > 0.40 and ADX14 > 25
  G2  no_chop     entries unless KER10 < 0.25 and ADX14 < 20
  G3  confirm     entries only on volume > 1.2 x SMA20 and bar CVD with the entry side
  G4  G1 + G3     (the request's full gate)
  G5  G2 + G3
  G6  G4 + chop_exit (flat on a CHOP close: Cash / Observer)
  G7  G2 + chop_exit
  L1  profit lock: chandelier 3.5 ATR armed at >= +2 % best close, soft (close-based)
  L2  profit lock: Donchian-mid (20 d) armed at >= +2 %, soft
  S10 sizing min(10 % equity, 1 % equity / (8 ATR stop distance))  [changes scale; reported apart]
Selection rule: the gate with the best DEV (2022-24) Sharpe among G1-G7; it replaces BASE on the demo only if its
OOS (2025-01..2026-09) Sharpe >= BASE and the paired daily difference vs BASE is not significantly negative.
The lock is kept only if it does not lower DEV and OOS Sharpe of the selected gate.  OOS numbers of every
variant are reported, not only the winner.  DSR deflates over the 100 earlier trend configs + these variants.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import numpy as np  # noqa: E402

import factor_scan as fs  # noqa: E402
import trend_backtest as tb  # noqa: E402
from core import fast_eval as fe  # noqa: E402
from core import trend_lane as tl  # noqa: E402

G = tl.RegimeGate
GATES = {
    'V0_BASE': None,
    'G1_trend_only': G(mode='trend_only', confirm=False),
    'G2_no_chop': G(mode='no_chop', confirm=False),
    'G3_confirm': G(mode='off', confirm=True),
    'G4_trend+confirm': G(mode='trend_only', confirm=True),
    'G5_nochop+confirm': G(mode='no_chop', confirm=True),
    'G6_G4+chop_exit': G(mode='trend_only', confirm=True, chop_exit=True),
    'G7_nochop+chop_exit': G(mode='no_chop', confirm=False, chop_exit=True),
}
LOCKS = {
    'none': tl.Brackets(sl_atr=8.0),
    'L1_chand3.5@2%': tl.Brackets(sl_atr=8.0, trail='chandelier', trail_atr=3.5, lock_pct=2.0, soft=True),
    'L2_dmid@2%': tl.Brackets(sl_atr=8.0, trail='donchian_mid', donchian_n=20, lock_pct=2.0, soft=True),
}
SIZING = {'legacy': None, 'S10': tl.Sizing(alloc_pct=0.10, risk_pct=0.01, stop_atr=8.0)}


def split_days():
    sp = {k: (fs.day_of(v[0]), fs.day_of(v[1])) for k, v in fs.SPLITS.items()}
    sp['OOS'] = (sp['VAL'][0], sp['HOLDOUT'][1])
    return sp


def trade_stats(trades, lo, hi):
    tr = [t for t in trades if lo <= int(t['entry_t'] // 86400) <= hi and t['bars'] > 0]
    if not tr:
        return {'n': 0}
    r = np.array([t['ret_on_notional_bps'] for t in tr])
    why = {}
    for t in tr:
        why[t.get('why', '?')] = why.get(t.get('why', '?'), 0) + 1
    return {'n': len(tr), 'win_rate': float((r > 0).mean()), 'median_bps': float(np.median(r)),
            'mean_bps': float(r.mean()), 'p25_bps': float(np.percentile(r, 25)), 'p75_bps': float(np.percentile(r, 75)),
            'median_hold_h': float(np.median([t['hold_h'] for t in tr])), 'exits': why,
            'mean_w': float(np.mean([t['w'] for t in tr])),
            'kelly_f': float(r.mean() / 1e4 / (r.std(ddof=1) / 1e4) ** 2) if len(r) > 2 and r.std() else None}


def run_variant(bars, fund, gate, br, sizing):
    cfg = tl.TrendConfig(bar='4h')
    return tl.backtest_brackets(bars, cfg, br, fund, gate=gate, sizing=sizing)


def regime_census(bars, lo, hi):
    """Share of 4H bars per regime, per symbol, inside [lo, hi] days (diagnostic)."""
    out = {}
    for s, b in bars.items():
        info = tl.regime_info(b, G())
        reg = [r for t, r in zip(b['t'], info['regime']) if lo <= int(t // 86400) <= hi and r]
        out[s] = {k: reg.count(k) / len(reg) for k in ('TREND', 'MIXED', 'CHOP')} if reg else {}
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--data', required=True, type=Path)
    ap.add_argument('--symbols', default='BTCUSDT,SOLUSDT,DOGEUSDT')
    ap.add_argument('--json', type=Path)
    ap.add_argument('--register', action='store_true', help='append the A/B rows to docs/research_upgrade/trial_log.jsonl')
    a = ap.parse_args(argv)
    syms = a.symbols.split(',')
    t0 = time.perf_counter()
    bars, fund = tb.load_bars(a.data, syms, '4h')
    sp = split_days()
    runs = {}
    for sz in SIZING:
        for g in GATES:
            for lk in LOCKS:
                runs[(g, lk, sz)] = run_variant(bars, fund, GATES[g], LOCKS[lk], SIZING[sz])
    t_run = time.perf_counter() - t0
    daily = {k: tl.to_daily(v['bar_returns']) for k, v in runs.items()}
    turn = {k: tl.to_daily(v['turnover']) for k, v in runs.items()}

    # deflation: the 96 factor-scan configs + 4 trend specs already tried, + these variants (legacy sizing)
    configs, results = fs.run(a.data, syms, cost_names=('TAKER',))
    cols = {fs.key(c): results[(c, 'TAKER')][0] for c in configs if c != fs.BASELINE}
    for name, cfg in tb.SPECS.items():
        b, f = (bars, fund) if cfg.bar == '4h' else tb.load_bars(a.data, syms, cfg.bar)
        cols[name] = tl.to_daily(tl.backtest(b, cfg, f, cost_bps=6.0)['bar_returns'])
    for k, v in daily.items():
        cols['|'.join(k)] = v
    dev_lo, dev_hi = sp['DEV']
    srs = []
    for v in cols.values():
        x = [v[d] for d in range(dev_lo, dev_hi + 1) if d in v]
        if len(x) > 30 and np.std(x) > 0:
            srs.append(np.mean(x) / np.std(x, ddof=1))
    sr_var, n_trials = float(np.var(srs, ddof=1)), len(cols)

    out = {'symbols': syms, 'splits': fs.SPLITS, 'n_trials_total': n_trials, 'sr_var_dev': sr_var,
           'regime_census_oos': regime_census(bars, *sp['OOS']), 'variants': {}}
    base_key = ('V0_BASE', 'none', 'legacy')
    for k, res in runs.items():
        row = {}
        for split in ('DEV', 'VAL', 'HOLDOUT', 'OOS'):
            st = fs.stats(daily[k], turn[k], *sp[split])
            if st:
                st['dsr'] = fe.dsr(st['sr_daily'], st['days'], st['skew'], st['kurt'], sr_var, n_trials)
            row[split] = st
            row[f'trades_{split}'] = trade_stats(res['trades'], *sp[split])
        ref = ('V0_BASE', 'none', k[2])
        if k != ref:
            for split in ('DEV', 'OOS'):
                lo, hi = sp[split]
                ds = [d for d in range(lo, hi + 1)]
                pa = [daily[ref].get(d, 0.0) for d in ds]
                pb = [daily[k].get(d, 0.0) for d in ds]
                pd_ = fe.paired_diff(pa, pb, n_boot=2000, block=5)
                row[f'vs_base_{split}'] = {'mean_bps_day': pd_['mean'] * 1e4, 'ci95_bps_day':
                                           [pd_['ci95'][0] * 1e4, pd_['ci95'][1] * 1e4], 't': pd_['t']}
        out['variants']['|'.join(k)] = row

    # selection (pre-registered): best DEV Sharpe gate, legacy sizing, no lock
    gates = [g for g in GATES if g != 'V0_BASE']
    best = max(gates, key=lambda g: out['variants'][f'{g}|none|legacy']['DEV']['sharpe'])
    out['selected_gate_by_dev'] = best
    out['seconds_backtests'] = t_run
    out['seconds_total'] = time.perf_counter() - t0

    def fmt(s):
        return (f"SR {s['sharpe']:+5.2f} ann {s['ann_ret_pct']:+6.1f}% DD {s['max_dd_pct']:5.1f}% "
                f"PSR {s['psr0']:.2f} DSR {s['dsr']:.2f}") if s else 'n/a'
    print(f'regime replay {",".join(syms)} | backtests {t_run:.1f}s | trials {n_trials} | DEV-selected gate: {best}')
    print('OOS regime census (share of 4H bars):', {s: {k: round(v, 2) for k, v in c.items()}
                                                   for s, c in out['regime_census_oos'].items()})
    for k, row in out['variants'].items():
        t = row['trades_OOS']
        vs = row.get('vs_base_OOS')
        print(f'{k:<40} DEV {fmt(row["DEV"])}')
        print(f'{"":<40} OOS {fmt(row["OOS"])} | n {t.get("n", 0)} win {t.get("win_rate", 0):.0%} '
              f'med {t.get("median_bps", 0):+.0f} mean {t.get("mean_bps", 0):+.0f} bps'
              + (f' | vs base {vs["mean_bps_day"]:+.2f} bps/d CI [{vs["ci95_bps_day"][0]:+.2f}, {vs["ci95_bps_day"][1]:+.2f}]'
                 if vs else ''))
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(out, indent=1, default=float))
    if a.register:
        log = ROOT / 'docs' / 'research_upgrade' / 'trial_log.jsonl'
        with open(log, 'a', encoding='utf-8') as fh:
            for k, row in out['variants'].items():
                if 'vs_base_OOS' not in row:
                    continue
                fh.write(json.dumps({'t': time.time(), 'name': f'trend 4H {k} vs base (OOS)', 'kind': 'trend',
                                     'unit': 'day net return', 'a': 'V0_BASE|none|' + k.split('|')[2], 'b': k,
                                     'n_units': row['OOS']['days'] if row['OOS'] else 0,
                                     'mean_diff': row['vs_base_OOS']['mean_bps_day'] / 1e4,
                                     'ci95': [x / 1e4 for x in row['vs_base_OOS']['ci95_bps_day']],
                                     'dsr': row['OOS']['dsr'] if row['OOS'] else None}) + '\n')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
