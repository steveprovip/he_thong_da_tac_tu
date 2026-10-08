"""Longer-horizon factor scan on Binance USD-M public data (data.binance.vision).  Read-only, offline.

    python tools/factor_scan.py --data <dir with SYMBOL-1h-YYYY-MM.zip [+ SYMBOL-fundingRate-YYYY-MM.zip]>
                                [--symbols BTCUSDT,SOLUSDT,DOGEUSDT] [--json out.json]

Question: the seconds-horizon engine has no edge after fees (ledger -3.6 bps/trade, Kelly < 0).  Does ANY
simple directional rule at 1h / 4h / 1d clear realistic costs on BTC/SOL/DOGE perps, out of sample?

Rules (position decided on the close of bar i, held over bar i+1; no look-ahead):
  TSMOM L     sign(close_i / close_{i-L} - 1)
  DONCH N     +1 on a close above the prior N-bar high, -1 below the prior N-bar low, else hold (stateful)
  EMAX L      sign(EMA(L/4) - EMA(L))
  ENS         mean of TSMOM signs over the frequency's whole lookback set (fractional position)
  ENS_VT      ENS scaled by min(2, 50 % ann. vol target / realised 30-bar vol)
  ENS_FF      ENS with longs zeroed while trailing 3-day mean funding > 0.03 %/8h (crowded longs)
each as LS (long/short) and LO (long-only).  Portfolio = equal weight BTC/SOL/DOGE (1/3 notional each).

Costs: |delta position| x cost per side; TAKER = 5 bps fee + 1 bp slip, MAKER = 2 bps (optimistic: assumes
every maker order fills).  Funding: actual Binance funding prints (long pays positive rate).

Splits fixed BEFORE running:  DEV 2022-01-01..2024-12-31 | VAL 2025 | HOLDOUT 2026-01-01..2026-09-30.
Selection = best DEV Sharpe (TAKER).  Honest numbers = (a) VAL/HOLDOUT of the DEV pick, (b) the
pre-registered config ENS 1d LS (literature prior, chosen before looking), (c) a quarterly walk-forward
(pick best of trailing 24 m, trade the next quarter), (d) Deflated Sharpe over every config tried.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import zipfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import fast_eval as fe  # noqa: E402

H = 3_600_000
FREQS = {'1h': 1, '4h': 4, '1d': 24}
LOOKBACKS = {'1h': (24, 72, 168, 336), '4h': (6, 18, 42, 84), '1d': (5, 10, 20, 40, 80)}
COSTS = {'TAKER': 6.0, 'MAKER': 2.0}
SPLITS = {'DEV': ('2022-01-01', '2024-12-31'), 'VAL': ('2025-01-01', '2025-12-31'),
          'HOLDOUT': ('2026-01-01', '2026-09-30')}
PREREG = ('ENS', '1d', 0, 'LS')
BASELINE = ('BH', '1d', 0, 'LO')   # buy & hold benchmark, not a trial
FF_LIMIT = 0.0003
VOL_TARGET = 0.50


# ------------------------------------------------------------------ data
def load_klines(data: Path, symbol: str):
    rows = {}
    for z in sorted(data.glob(f'{symbol}-1h-*.zip')):
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                txt = zf.read(name).decode()
                for r in csv.reader(io.StringIO(txt)):
                    if not r or not r[0].isdigit():
                        continue
                    t = int(r[0])
                    rows[t] = (float(r[1]), float(r[2]), float(r[3]), float(r[4]), float(r[7]), float(r[10]))
    if not rows:
        raise SystemExit(f'no klines for {symbol} in {data}')
    ts = np.array(sorted(rows), dtype=np.int64)
    arr = np.array([rows[t] for t in ts], dtype=float)
    return ts, arr  # columns o h l c quote_vol taker_buy_quote


def load_funding(data: Path, symbol: str):
    out = []
    for z in sorted(data.glob(f'{symbol}-fundingRate-*.zip')):
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                for r in csv.reader(io.StringIO(zf.read(name).decode())):
                    if r and r[0].isdigit():
                        out.append((int(r[0]), float(r[-1])))
    out.sort()
    return out


def resample(ts, arr, hours):
    """Group 1h bars into UTC-aligned `hours` bars (drops incomplete groups)."""
    if hours == 1:
        return ts, arr
    key = ts // (hours * H)
    starts = np.flatnonzero(np.r_[True, key[1:] != key[:-1]])
    ends = np.r_[starts[1:], len(ts)]
    keep = (ends - starts) == hours
    s, e = starts[keep], ends[keep]
    o = arr[s, 0]
    hi = np.maximum.reduceat(arr[:, 1], starts)[keep]
    lo = np.minimum.reduceat(arr[:, 2], starts)[keep]
    c = arr[e - 1, 3]
    qv = np.add.reduceat(arr[:, 4], starts)[keep]
    tb = np.add.reduceat(arr[:, 5], starts)[keep]
    return key[s] * hours * H, np.column_stack([o, hi, lo, c, qv, tb])


def funding_per_bar(bar_ts, hours, funding):
    """Sum of funding rates printed inside each bar [t, t+hours)."""
    f = np.zeros(len(bar_ts))
    if not funding:
        return f
    ft = np.array([x[0] for x in funding], dtype=np.int64)
    fr = np.array([x[1] for x in funding])
    idx = np.searchsorted(bar_ts, ft, side='right') - 1
    ok = (idx >= 0) & (ft < bar_ts[np.clip(idx, 0, None)] + hours * H)
    np.add.at(f, idx[ok], fr[ok])
    return f


def trailing_funding(bar_ts, funding, window_ms=3 * 24 * H):
    """Mean funding rate printed in (t - window, t] for each bar close time."""
    out = np.zeros(len(bar_ts))
    if not funding:
        return out
    ft = np.array([x[0] for x in funding], dtype=np.int64)
    cs = np.r_[0.0, np.cumsum([x[1] for x in funding])]
    return _trail(bar_ts, ft, cs, window_ms, out)


def _trail(close_ts, ft, cs, window, out):
    hi = np.searchsorted(ft, close_ts, side='right')
    lo = np.searchsorted(ft, close_ts - window, side='right')
    cnt = hi - lo
    with np.errstate(invalid='ignore', divide='ignore'):
        out[:] = np.where(cnt > 0, (cs[hi] - cs[lo]) / np.maximum(cnt, 1), 0.0)
    return out


# ------------------------------------------------------------------ signals
def ema(x, span):
    a = 2.0 / (span + 1)
    out = np.empty_like(x)
    acc = x[0]
    for i, v in enumerate(x):
        acc = a * v + (1 - a) * acc
        out[i] = acc
    return out


def sig_tsmom(c, L):
    s = np.zeros(len(c))
    s[L:] = np.sign(c[L:] / c[:-L] - 1)
    return s


def sig_donch(h, l, c, N):
    s = np.zeros(len(c))
    pos = 0.0
    for i in range(N, len(c)):
        if c[i] > h[i - N:i].max():
            pos = 1.0
        elif c[i] < l[i - N:i].min():
            pos = -1.0
        s[i] = pos
    return s


def sig_emax(c, L):
    s = np.sign(ema(c, max(2, L // 4)) - ema(c, L))
    s[:L] = 0
    return s


def realised_vol(c, bars_per_year, n=30):
    r = np.r_[0.0, np.diff(np.log(c))]
    v = np.full(len(c), np.nan)
    for i in range(n, len(c)):
        v[i] = r[i - n + 1:i + 1].std() * math.sqrt(bars_per_year)
    return v


def signal(family, freq, L, bars, fund_trail=None):
    c, h, l = bars[:, 3], bars[:, 1], bars[:, 2]
    if family == 'TSMOM':
        return sig_tsmom(c, L)
    if family == 'DONCH':
        return sig_donch(h, l, c, L)
    if family == 'EMAX':
        return sig_emax(c, L)
    if family == 'BH':
        return np.ones(len(c))
    ens = np.mean([sig_tsmom(c, k) for k in LOOKBACKS[freq]], axis=0)
    if family == 'ENS':
        return ens
    if family == 'ENS_VT':
        bpy = 365 * 24 / FREQS[freq]
        rv = realised_vol(c, bpy)
        scale = np.where(np.isfinite(rv) & (rv > 0), np.minimum(2.0, VOL_TARGET / np.where(rv > 0, rv, 1)), 0.0)
        return ens * scale
    if family == 'ENS_FF':
        s = ens.copy()
        if fund_trail is not None:
            s[(s > 0) & (fund_trail > FF_LIMIT)] = 0.0
        return s
    raise ValueError(family)


# ------------------------------------------------------------------ pnl
def bar_pnl(pos, c, fund, cost_bps):
    """Net return per bar i (position from close i-1 held over bar i)."""
    r = np.r_[0.0, c[1:] / c[:-1] - 1]
    held = np.r_[0.0, pos[:-1]]
    turn = np.abs(np.diff(np.r_[0.0, pos]))
    trade_cost = np.r_[0.0, turn[:-1]] * cost_bps / 1e4   # trade at close i-1, booked in bar i
    return held * r - trade_cost - held * fund, np.r_[0.0, turn[:-1]]


def to_daily(bar_ts, x):
    day = bar_ts // (24 * H)
    out = {}
    for d, v in zip(day.tolist(), x.tolist()):
        out[d] = out.get(d, 0.0) + v
    return out


def day_of(s):
    return int(datetime.strptime(s, '%Y-%m-%d').replace(tzinfo=timezone.utc).timestamp() * 1000) // (24 * H)


def stats(daily: dict, turn: dict, lo: int, hi: int):
    ds = sorted(d for d in daily if lo <= d <= hi)
    x = [daily[d] for d in ds]
    if len(x) < 30:
        return None
    m, sd, sk, ku = fe.moments(x)
    eq = np.cumsum(x)
    dd = float((np.maximum.accumulate(eq) - eq).max())
    tv = sum(turn.get(d, 0.0) for d in ds)
    yrs = len(x) / 365
    sr_d = m / sd if sd else 0.0
    return {'days': len(x), 'ann_ret_pct': m * 365 * 100, 'sharpe': sr_d * math.sqrt(365),
            'sr_daily': sr_d, 'skew': sk, 'kurt': ku, 'max_dd_pct': dd * 100,
            'round_trips_per_year': tv / 2 / yrs, 'net_bps_per_rt': (sum(x) / (tv / 2) * 1e4) if tv else 0.0,
            'psr0': fe.psr(sr_d, len(x), sk, ku)}


def run(data: Path, symbols, cost_names=('TAKER', 'MAKER')):
    configs = []
    for fam in ('TSMOM', 'DONCH', 'EMAX'):
        for fq in FREQS:
            for L in LOOKBACKS[fq]:
                for side in ('LS', 'LO'):
                    configs.append((fam, fq, L, side))
    for fam in ('ENS', 'ENS_VT', 'ENS_FF'):
        for fq in FREQS:
            for side in ('LS', 'LO'):
                configs.append((fam, fq, 0, side))
    configs.append(BASELINE)

    per_sym = {}
    for s in symbols:
        ts, arr = load_klines(data, s)
        fund = load_funding(data, s)
        per_sym[s] = {}
        for fq, hrs in FREQS.items():
            bts, bars = resample(ts, arr, hrs)
            close_ts = bts + hrs * H
            per_sym[s][fq] = (bts, bars, funding_per_bar(bts, hrs, fund), trailing_funding(close_ts, fund))
        per_sym[s]['first_day'] = int(ts[0] // (24 * H))

    results = {}
    for cfg in configs:
        fam, fq, L, side = cfg
        for cname in cost_names:
            port, port_turn = {}, {}
            for s in symbols:
                bts, bars, fbar, ftrail = per_sym[s][fq]
                pos = signal(fam, fq, L, bars, ftrail)
                if side == 'LO':
                    pos = np.clip(pos, 0, None)
                pnl, turn = bar_pnl(pos, bars[:, 3], fbar, COSTS[cname])
                w = 1.0 / len(symbols)
                for d, v in to_daily(bts, pnl * w).items():
                    port[d] = port.get(d, 0.0) + v
                for d, v in to_daily(bts, turn * w).items():
                    port_turn[d] = port_turn.get(d, 0.0) + v
            results[(cfg, cname)] = (port, port_turn)
    return configs, results


def key(cfg):
    fam, fq, L, side = cfg
    return f'{fam}{"" if not L else "_" + str(L)}@{fq}/{side}'


def walk_forward(configs, results, cost='TAKER', train_days=730, step_days=91, start='2024-01-01',
                 end='2026-09-30'):
    """Each quarter: pick the best trailing-24 m Sharpe config, trade the next quarter.  -> daily dict."""
    d0, dend = day_of(start), day_of(end)
    oos, oos_turn, picks = {}, {}, []
    d = d0
    while d <= dend:
        best, bsr = None, -1e9
        for cfg in configs:
            daily, _ = results[(cfg, cost)]
            x = [daily[k] for k in range(d - train_days, d) if k in daily]
            if len(x) < 200:
                continue
            m, sd = sum(x) / len(x), np.std(x, ddof=1)
            sr = m / sd if sd else -1e9
            if sr > bsr:
                best, bsr = cfg, sr
        if best is None:
            break
        daily, turn = results[(best, cost)]
        for k in range(d, min(d + step_days, dend + 1)):
            if k in daily:
                oos[k] = daily[k]
                oos_turn[k] = turn.get(k, 0.0)
        picks.append((datetime.fromtimestamp(d * 86400, timezone.utc).strftime('%Y-%m-%d'), key(best),
                      round(bsr * math.sqrt(365), 2)))
        d += step_days
    return oos, oos_turn, picks


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--data', required=True, type=Path)
    ap.add_argument('--symbols', default='BTCUSDT,SOLUSDT,DOGEUSDT')
    ap.add_argument('--json', type=Path)
    a = ap.parse_args(argv)
    symbols = a.symbols.split(',')
    configs, results = run(a.data, symbols)
    sp = {k: (day_of(v[0]), day_of(v[1])) for k, v in SPLITS.items()}
    sp_oos = (sp['VAL'][0], sp['HOLDOUT'][1])

    table = []
    for cfg in configs:
        row = {'config': key(cfg)}
        for cname in COSTS:
            daily, turn = results[(cfg, cname)]
            for sname, (lo, hi) in sp.items():
                row[f'{cname}_{sname}'] = stats(daily, turn, lo, hi)
            row[f'{cname}_OOS'] = stats(daily, turn, *sp_oos)
        table.append(row)
    bh = next(r for r in table if r['config'] == key(BASELINE))
    table = [r for r in table if r is not bh]
    configs = [c for c in configs if c != BASELINE]

    dev_srs = [r['TAKER_DEV']['sr_daily'] for r in table if r['TAKER_DEV']]
    sr_var = float(np.var(dev_srs, ddof=1))
    n_trials = len(dev_srs)
    ranked = sorted((r for r in table if r['TAKER_DEV']), key=lambda r: -r['TAKER_DEV']['sharpe'])
    for r in ranked:
        dv = r['TAKER_DEV']
        r['DSR_DEV'] = fe.dsr(dv['sr_daily'], dv['days'], dv['skew'], dv['kurt'], sr_var, n_trials)

    wf, wf_turn, picks = walk_forward(configs, results)
    wf_stats = stats(wf, wf_turn, min(wf), max(wf)) if wf else None
    prereg = next(r for r in table if r['config'] == key(PREREG))

    def fmt(s):
        if not s:
            return '      n/a'
        rt = f"{s['net_bps_per_rt']:+7.1f}bps" if s['round_trips_per_year'] >= 1 else '    n/a'
        return (f"SR {s['sharpe']:+5.2f} ann {s['ann_ret_pct']:+6.1f}% DD {s['max_dd_pct']:5.1f}% "
                f"RT/y {s['round_trips_per_year']:6.0f} net/RT {rt}")

    print(f'factor scan {",".join(symbols)} | configs {n_trials} | DEV SR var {sr_var:.2e} | '
          f'E[max SR] daily {fe.expected_max_sr(sr_var, n_trials):.4f}')
    print('\nTop 10 by DEV Sharpe (TAKER 6 bps/side) -> VAL / HOLDOUT')
    for r in ranked[:10]:
        print(f"{r['config']:<22} DSR {r['DSR_DEV']:.2f}  OOS(VAL+HLD) PSR>0 {r['TAKER_OOS']['psr0']:.2f}\n   DEV {fmt(r['TAKER_DEV'])}\n   VAL {fmt(r['TAKER_VAL'])}"
              f"\n   HLD {fmt(r['TAKER_HOLDOUT'])}")
    print('\nBuy & hold equal weight (benchmark, not a trial):')
    for sn in ('DEV', 'VAL', 'HOLDOUT'):
        print(f'   {sn:<7} {fmt(bh[f"TAKER_{sn}"])}')
    print(f"\nPre-registered {prereg['config']}:  OOS(VAL+HLD) PSR>0 {prereg['TAKER_OOS']['psr0']:.2f}")
    for c in COSTS:
        for sn in SPLITS:
            print(f'   {c:<5} {sn:<7} {fmt(prereg[f"{c}_{sn}"])}')
    print('\nWalk-forward (TAKER, 24 m train, quarterly) OOS 2024-01..2026-09:', fmt(wf_stats))
    for p in picks:
        print('   ', *p)
    by_freq = {}
    for r in table:
        fq = r['config'].split('@')[1].split('/')[0]
        for sn in ('VAL', 'HOLDOUT'):
            s = r[f'TAKER_{sn}']
            if s:
                by_freq.setdefault((fq, sn), []).append(s['sharpe'])
    print('\nMedian Sharpe of ALL configs by frequency (TAKER):')
    for (fq, sn), v in sorted(by_freq.items()):
        print(f'   {fq:<3} {sn:<7} median {np.median(v):+.2f}  share>0 {np.mean(np.array(v) > 0):.0%}  n={len(v)}')

    if a.json:
        out = {'symbols': symbols, 'splits': SPLITS, 'costs_bps_per_side': COSTS, 'n_trials': n_trials,
               'dev_sr_var': sr_var, 'table': table, 'ranked': [r['config'] for r in ranked],
               'prereg': prereg, 'buy_hold': bh, 'walk_forward': {'stats': wf_stats, 'picks': picks},
               'median_sharpe_by_freq': {f'{k[0]}_{k[1]}': float(np.median(v)) for k, v in by_freq.items()}}
        a.json.write_text(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
