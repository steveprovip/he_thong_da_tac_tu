"""Backtest of the 1D / 4H trend lane (core/trend_lane.py) on Binance USD-M public data, plus PBO.  Offline.

    python tools/trend_backtest.py --data <dir: SYMBOL-1h-YYYY-MM.zip + SYMBOL-fundingRate-YYYY-MM.zip>
                                   [--symbols BTCUSDT,SOLUSDT,DOGEUSDT] [--json out.json] [--daily-out dir]

Pre-registered specs (fixed in core/trend_lane.TrendConfig BEFORE this run, from the 10-09 request):
  SPEC_1D_LS   TSMOM 5/20/60 d + Donchian 20 d, vol target 20 %/yr per sleeve, funding overlay, band 5 %
  SPEC_1D_LO   same, long-only
  SPEC_4H_LS   same calendar horizons on 4 h bars (x6), long/short
  SPEC_4H_LO
Costs TAKER 6 bps/side (5 fee + 1 slip) and MAKER 2.  Splits as tools/factor_scan.py: DEV 2022-24 | VAL 2025 |
HOLDOUT 2026-01..09.  Trials counted on top of the 96 factor-scan configs (DSR), and PBO (CSCV, 10 blocks) over
all 100 daily return series.  Speed: the whole 2022-2026 run is a few seconds.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from dataclasses import asdict, replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import numpy as np  # noqa: E402

import factor_scan as fs  # noqa: E402
from core import fast_eval as fe  # noqa: E402
from core import trend_lane as tl  # noqa: E402

SPECS = {
    'SPEC_1D_LS': tl.TrendConfig(bar='1d'),
    'SPEC_1D_LO': tl.TrendConfig(bar='1d', long_only=True),
    'SPEC_4H_LS': tl.TrendConfig(bar='4h'),
    'SPEC_4H_LO': tl.TrendConfig(bar='4h', long_only=True),
}


def load_bars(data: Path, symbols, bar: str):
    out, fund = {}, {}
    hrs = tl.BAR_HOURS[bar]
    for s in symbols:
        ts, arr = fs.load_klines(data, s)
        bts, b = fs.resample(ts, arr, hrs)
        out[s] = {'t': (bts / 1000.0).tolist(), 'o': b[:, 0].tolist(), 'h': b[:, 1].tolist(),
                  'l': b[:, 2].tolist(), 'c': b[:, 3].tolist(), 'v': b[:, 4].tolist(), 'tb': b[:, 5].tolist()}
        fund[s] = [(t / 1000.0, r) for t, r in fs.load_funding(data, s)]
    return out, fund


def daily_dicts(res):
    d = tl.to_daily(res['bar_returns'])
    tv = tl.to_daily(res['turnover'])
    return d, tv


def trade_stats(trades, lo_day, hi_day):
    tr = [t for t in trades if lo_day <= int(t['entry_t'] // 86400) <= hi_day and t['bars'] > 0]
    if not tr:
        return {'n': 0}
    r = sorted(t['ret_on_notional_bps'] for t in tr)
    h = sorted(t['hold_h'] for t in tr)
    return {'n': len(tr), 'win_rate': sum(1 for x in r if x > 0) / len(r), 'median_bps': r[len(r) // 2],
            'mean_bps': sum(r) / len(r), 'median_hold_h': h[len(h) // 2],
            'share_abs_ge_150bps': sum(1 for x in r if abs(x) >= 150) / len(r)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--data', required=True, type=Path)
    ap.add_argument('--symbols', default='BTCUSDT,SOLUSDT,DOGEUSDT')
    ap.add_argument('--json', type=Path)
    ap.add_argument('--daily-out', type=Path, help='write <spec>_<cost>.json daily return lists for ab_bench')
    a = ap.parse_args(argv)
    syms = a.symbols.split(',')
    t0 = time.perf_counter()
    sp = {k: (fs.day_of(v[0]), fs.day_of(v[1])) for k, v in fs.SPLITS.items()}
    sp['OOS'] = (sp['VAL'][0], sp['HOLDOUT'][1])
    bars = {b: load_bars(a.data, syms, b) for b in ('1d', '4h')}
    t_load = time.perf_counter() - t0
    out = {}
    series = {}
    for name, cfg in SPECS.items():
        b, f = bars[cfg.bar]
        out[name] = {'config': asdict(cfg)}
        for cname, cbps in fs.COSTS.items():
            t1 = time.perf_counter()
            res = tl.backtest(b, cfg, f, cost_bps=cbps)
            el = time.perf_counter() - t1
            daily, turn = daily_dicts(res)
            series[(name, cname)] = daily
            out[name][cname] = {k: fs.stats(daily, turn, lo, hi) for k, (lo, hi) in sp.items()}
            out[name][cname]['trades_oos'] = trade_stats(res['trades'], *sp['OOS'])
            out[name][cname]['seconds'] = el
            if a.daily_out:
                a.daily_out.mkdir(parents=True, exist_ok=True)
                days = sorted(daily)
                (a.daily_out / f'{name}_{cname}.json').write_text(json.dumps({'days': days,
                                                                             'ret': [daily[d] for d in days]}))
    # ---- deflation over every config tried so far: 96 factor-scan configs + these 4 specs
    configs, results = fs.run(a.data, syms, cost_names=('TAKER',))
    configs = [c for c in configs if c != fs.BASELINE]
    cols = {fs.key(c): results[(c, 'TAKER')][0] for c in configs}
    for name in SPECS:
        cols[name] = series[(name, 'TAKER')]
    lo, hi = sp['DEV'][0], sp['HOLDOUT'][1]
    days = [d for d in range(lo, hi + 1) if all(d in v for v in cols.values())]
    mat = np.array([[cols[k][d] for k in cols] for d in days])
    pbo = fe.pbo_cscv(mat, n_blocks=10)
    dev_lo, dev_hi = sp['DEV']
    dev_srs = []
    for k, v in cols.items():
        x = [v[d] for d in range(dev_lo, dev_hi + 1) if d in v]
        if len(x) > 30 and np.std(x) > 0:
            dev_srs.append(np.mean(x) / np.std(x, ddof=1))
    sr_var, n_trials = float(np.var(dev_srs, ddof=1)), len(cols)
    for name in SPECS:
        for cname in fs.COSTS:
            for split in ('DEV', 'OOS'):
                st = out[name][cname][split]
                if st:
                    st['dsr'] = fe.dsr(st['sr_daily'], st['days'], st['skew'], st['kurt'], sr_var, n_trials)
    summary = {'symbols': syms, 'splits': fs.SPLITS, 'n_trials_total': n_trials, 'sr_var_dev': sr_var,
               'pbo_all_configs': pbo, 'load_seconds': t_load, 'total_seconds': time.perf_counter() - t0,
               'specs': out}

    def fmt(s):
        if not s:
            return 'n/a'
        return (f"SR {s['sharpe']:+5.2f} ann {s['ann_ret_pct']:+6.1f}% DD {s['max_dd_pct']:5.1f}% "
                f"RT/y {s['round_trips_per_year']:5.0f} net/RT {s['net_bps_per_rt']:+7.1f}bps PSR {s['psr0']:.2f}"
                + (f" DSR {s['dsr']:.2f}" if 'dsr' in s else ''))
    print(f'trend lane backtest {",".join(syms)} | load {t_load:.1f}s | total {summary["total_seconds"]:.1f}s | '
          f'trials {n_trials} | PBO(all) {pbo["pbo"]:.2f}')
    for name in SPECS:
        for cname in fs.COSTS:
            r = out[name][cname]
            print(f'{name} {cname} ({r["seconds"]:.2f}s)')
            for split in ('DEV', 'VAL', 'HOLDOUT', 'OOS'):
                print(f'   {split:<7} {fmt(r[split])}')
            t = r['trades_oos']
            if t.get('n'):
                print(f"   trades OOS n={t['n']} win {t['win_rate']:.0%} median {t['median_bps']:+.0f} bps "
                      f"mean {t['mean_bps']:+.0f} hold~{t['median_hold_h']:.0f}h |ret|>=150bps {t['share_abs_ge_150bps']:.0%}")
    if a.json:
        a.json.write_text(json.dumps(summary, indent=1, default=float))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
