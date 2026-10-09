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


FF_DOC = """Stage ff (request 10-09 ff), PRE-REGISTERED before its first run, on top of the deployed B0 = G6 + L1 + S10:
  A1  BTC shield 'sign'   no alt (SOL, DOGE) entry against the sign of the BTC 4H trend target
  A2  BTC shield 'trend'  ... only while BTC is also TREND (KER > 0.40, ADX > 25)
  A1E / A2E               + eject: close an alt at a close where BTC is TREND with its signal against it
      exception for A*: alt volume > 3.0 x SMA20 and alt CVD share >= 10 % with its side
  P1  break-even stop (entry +/- 0.3 %) once a close is >= +2 %
  P2  P1 + one add of 0.5 x size at that close, soft stop at that 4H bar's low / high
  C2  1H (TSMOM 24/72/168 h) + 4H + 1D (SPEC_1D_LS) agree -> Sizing(0.20, 0.02) instead of Sizing(0.10, 0.01)
  C3  ... -> Sizing(0.30, 0.02)
  FULL = DEV-best A + P2 + C2;  FULL3 = DEV-best A + P2 + C3
  FINAL = the components the rules adopt (added after the first run: FULL/FULL3 hold rejected parts; 1 more trial)
Rules: A = best DEV Sharpe among A1-A2E, adopted if OOS Sharpe >= B0 and the paired daily difference is not
significantly negative.  P / C adopted only if DEV AND OOS Sharpe >= B0 (more size must not lower risk-adjusted
return).  Leverage frontier: daily returns of the final config x k, k = 1..8 (no compounding, no margin model).
DSR: sr_var of the 148 earlier trials (snapshot 2026-10-09ee) and n_trials = 148 + these."""


def conviction_map(data: Path, syms, bars4, fund):
    """{sym: [bool per 4H bar]} from 1h closes + the 1D spec target + the 4H spec target."""
    cfg4 = tl.TrendConfig(bar='4h')
    tw4, _ = tl.weights_series(bars4, cfg4, fund)
    bars1d, fund1d = tb.load_bars(data, syms, '1d')
    tw1d, _ = tl.weights_series(bars1d, tl.TrendConfig(bar='1d'), fund1d)
    out = {}
    for s in syms:
        ts, arr = fs.load_klines(data, s)
        out[s] = tl.conviction_series(bars4[s], (ts / 1000.0).tolist(), arr[:, 3].tolist(), bars1d[s]['t'], tw1d[s],
                                      tw4[s])
    return out


def main_ff(a, syms):
    t0 = time.perf_counter()
    bars, fund = tb.load_bars(a.data, syms, '4h')
    sp = split_days()
    sp['2025'], sp['2026'] = sp['VAL'], sp['HOLDOUT']
    conv = conviction_map(a.data, syms, bars, fund)
    g6, l1, s10 = GATES['G6_G4+chop_exit'], LOCKS['L1_chand3.5@2%'], SIZING['S10']
    c2, c3 = tl.Sizing(0.20, 0.02, 8.0), tl.Sizing(0.30, 0.02, 8.0)
    al = {'A1': tl.BtcAlign(mode='sign'), 'A2': tl.BtcAlign(mode='trend'),
          'A1E': tl.BtcAlign(mode='sign', eject=True), 'A2E': tl.BtcAlign(mode='trend', eject=True)}
    p1, p2 = tl.Pyramid(add_frac=0.0), tl.Pyramid(add_frac=0.5)

    def run(**kw):
        return tl.backtest_brackets(bars, tl.TrendConfig(bar='4h'), l1, fund, gate=g6, sizing=s10, **kw)
    runs = {'B0': run()}
    for k, v in al.items():
        runs[k] = run(align=v)
    runs['P1'], runs['P2'] = run(pyramid=p1), run(pyramid=p2)
    runs['C2'], runs['C3'] = run(conv=conv, conv_sizing=c2), run(conv=conv, conv_sizing=c3)
    daily = {k: tl.to_daily(v['bar_returns']) for k, v in runs.items()}
    dev_sr = lambda k: fs.stats(daily[k], {}, *sp['DEV'])['sharpe']
    best_a = max(al, key=dev_sr)
    runs['FULL'] = run(align=al[best_a], pyramid=p2, conv=conv, conv_sizing=c2)
    runs['FULL3'] = run(align=al[best_a], pyramid=p2, conv=conv, conv_sizing=c3)
    for k in ('FULL', 'FULL3'):
        daily[k] = tl.to_daily(runs[k]['bar_returns'])
    turn = {k: tl.to_daily(v['turnover']) for k, v in runs.items()}
    prev = json.loads((ROOT / 'docs/research_upgrade/snapshot_2026-10-09ee/trend_regime_replay.json').read_text())
    sr_var, n_trials = prev['sr_var_dev'], prev['n_trials_total'] + len(runs) - 1
    out = {'doc': FF_DOC, 'symbols': syms, 'n_trials_total': n_trials, 'sr_var_dev': sr_var, 'best_align_by_dev': best_a,
           'conviction_share_oos': {s: float(np.mean([c for t, c in zip(bars[s]['t'], conv[s])
                                                     if sp['OOS'][0] <= int(t // 86400) <= sp['OOS'][1]]))
                                    for s in syms},
           'variants': {}}
    for k, res in runs.items():
        row = {}
        for split in ('DEV', 'OOS', '2025', '2026'):
            st = fs.stats(daily[k], turn[k], *sp[split])
            if st:
                st['dsr'] = fe.dsr(st['sr_daily'], st['days'], st['skew'], st['kurt'], sr_var, n_trials)
            row[split] = st
        row['trades_OOS'] = trade_stats(res['trades'], *sp['OOS'])
        alt = [t for t in res['trades'] if t['symbol'] != 'BTCUSDT' and sp['OOS'][0] <= int(t['entry_t'] // 86400) <= sp['OOS'][1]]
        row['alt_trades_OOS'] = {'n': len(alt), 'pnl_sum_pct_equity': 100 * sum(t['pnl'] for t in alt)}
        row['adds_OOS'] = sum(1 for t in res['trades'] if t.get('added') and sp['OOS'][0] <= int(t['entry_t'] // 86400) <= sp['OOS'][1])
        row['conv_trades_OOS'] = sum(1 for t in res['trades'] if t.get('conv') and sp['OOS'][0] <= int(t['entry_t'] // 86400) <= sp['OOS'][1])
        if k != 'B0':
            for split in ('DEV', 'OOS'):
                ds = list(range(sp[split][0], sp[split][1] + 1))
                pd_ = fe.paired_diff([daily['B0'].get(d, 0.0) for d in ds], [daily[k].get(d, 0.0) for d in ds],
                                     n_boot=2000, block=5)
                row[f'vs_B0_{split}'] = {'mean_bps_day': pd_['mean'] * 1e4,
                                         'ci95_bps_day': [pd_['ci95'][0] * 1e4, pd_['ci95'][1] * 1e4]}
        out['variants'][k] = row
    V = out['variants']
    ok = lambda k: V[k]['DEV']['sharpe'] >= V['B0']['DEV']['sharpe'] and V[k]['OOS']['sharpe'] >= V['B0']['OOS']['sharpe']
    out['decision'] = {
        'align': best_a if (V[best_a]['OOS']['sharpe'] >= V['B0']['OOS']['sharpe'] and
                            V[best_a]['vs_B0_OOS']['ci95_bps_day'][1] > 0) else None,
        'pyramid': 'P2' if ok('P2') else ('P1' if ok('P1') else None),
        'conviction': 'C3' if ok('C3') else ('C2' if ok('C2') else None)}
    dec = out['decision']
    runs['FINAL'] = run(align=al[dec['align']] if dec['align'] else None,
                        pyramid={'P1': p1, 'P2': p2}.get(dec['pyramid']),
                        conv=conv if dec['conviction'] else None,
                        conv_sizing={'C2': c2, 'C3': c3}.get(dec['conviction']))
    daily['FINAL'] = tl.to_daily(runs['FINAL']['bar_returns'])
    turn['FINAL'] = tl.to_daily(runs['FINAL']['turnover'])
    out['n_trials_total'] = n_trials = n_trials + 1
    row = {}
    for split in ('DEV', 'OOS', '2025', '2026'):
        st = fs.stats(daily['FINAL'], turn['FINAL'], *sp[split])
        st['dsr'] = fe.dsr(st['sr_daily'], st['days'], st['skew'], st['kurt'], sr_var, n_trials)
        row[split] = st
    row['trades_OOS'] = trade_stats(runs['FINAL']['trades'], *sp['OOS'])
    alt = [t for t in runs['FINAL']['trades'] if t['symbol'] != 'BTCUSDT' and sp['OOS'][0] <= int(t['entry_t'] // 86400) <= sp['OOS'][1]]
    row['alt_trades_OOS'] = {'n': len(alt), 'pnl_sum_pct_equity': 100 * sum(t['pnl'] for t in alt)}
    row['adds_OOS'] = 0
    row['conv_trades_OOS'] = sum(1 for t in runs['FINAL']['trades'] if t.get('conv') and sp['OOS'][0] <= int(t['entry_t'] // 86400) <= sp['OOS'][1])
    for split in ('DEV', 'OOS'):
        ds = list(range(sp[split][0], sp[split][1] + 1))
        pd_ = fe.paired_diff([daily['B0'].get(d, 0.0) for d in ds], [daily['FINAL'].get(d, 0.0) for d in ds],
                             n_boot=2000, block=5)
        row[f'vs_B0_{split}'] = {'mean_bps_day': pd_['mean'] * 1e4, 'ci95_bps_day': [pd_['ci95'][0] * 1e4, pd_['ci95'][1] * 1e4]}
    V['FINAL'] = row
    w_fin = [t['w'] for t in runs['FINAL']['trades'] if sp['OOS'][0] <= int(t['entry_t'] // 86400) <= sp['OOS'][1]]
    out['final_weight_OOS'] = {'mean': float(np.mean(w_fin)), 'max': float(np.max(w_fin))}
    fin = 'FINAL'
    lo, hi = sp['OOS']
    out['frontier_OOS'] = {}
    for kx in (1, 2, 3, 5, 8):
        st = fs.stats({d: v * kx for d, v in daily['B0'].items()}, {}, lo, hi)
        st2 = fs.stats({d: v * kx for d, v in daily[fin].items()}, {}, lo, hi)
        out['frontier_OOS'][kx] = {'B0': {'ann': st['ann_ret_pct'], 'dd': st['max_dd_pct']},
                                   fin: {'ann': st2['ann_ret_pct'], 'dd': st2['max_dd_pct']}}
    out['seconds'] = time.perf_counter() - t0

    def f(x):
        return (f"SR {x['sharpe']:+5.2f} ann {x['ann_ret_pct']:+6.1f}% DD {x['max_dd_pct']:5.1f}% DSR {x['dsr']:.2f}") if x else 'n/a'
    print(f'stage ff | {out["seconds"]:.0f}s | trials {n_trials} | DEV-best align {best_a} | decision {out["decision"]}')
    print('conviction share of 4H bars OOS', {s: round(v, 2) for s, v in out['conviction_share_oos'].items()})
    for k, r in V.items():
        t = r['trades_OOS']
        vs = r.get('vs_B0_OOS')
        print(f'{k:<6} DEV {f(r["DEV"])} | OOS {f(r["OOS"])} | 2025 SR {r["2025"]["sharpe"]:+.2f} 2026 SR {r["2026"]["sharpe"]:+.2f}')
        print(f'       n {t.get("n", 0)} win {t.get("win_rate", 0):.0%} med {t.get("median_bps", 0):+.0f} mean {t.get("mean_bps", 0):+.0f} '
              f'| alt n {r["alt_trades_OOS"]["n"]} pnl {r["alt_trades_OOS"]["pnl_sum_pct_equity"]:+.2f}% eq | adds {r["adds_OOS"]} '
              f'conv {r["conv_trades_OOS"]}' + (f' | vs B0 {vs["mean_bps_day"]:+.2f} bps/d CI [{vs["ci95_bps_day"][0]:+.2f}, '
                                               f'{vs["ci95_bps_day"][1]:+.2f}]' if vs else ''))
    print('frontier OOS (x k):', {k: v for k, v in out['frontier_OOS'].items()})
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(out, indent=1, default=float))
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--data', required=True, type=Path)
    ap.add_argument('--symbols', default='BTCUSDT,SOLUSDT,DOGEUSDT')
    ap.add_argument('--json', type=Path)
    ap.add_argument('--register', action='store_true', help='append the A/B rows to docs/research_upgrade/trial_log.jsonl')
    ap.add_argument('--stage', default='ee', choices=('ee', 'ff'), help='ee: regime/lock/sizing grid; ff: BTC '
                    'alignment / pyramiding / conviction on top of the deployed G6 + L1 + S10')
    a = ap.parse_args(argv)
    if a.stage == 'ff':
        return main_ff(a, a.symbols.split(','))
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
