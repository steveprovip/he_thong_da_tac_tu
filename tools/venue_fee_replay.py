"""Re-price the intraday (1m klines) and burst (aggTrades) replays under other venues' fee schedules (10-10 d request).

    python tools/venue_fee_replay.py --k1m <dir of SYMBOL-1m-YYYY-MM.zip> --agg <dir of SYMBOL-aggTrades-DAY.zip>
        [--json out.json]

Fees never change WHICH trades a strategy takes (no rule here reads a fee), only their net, so each replay runs ONCE
and every trade is re-priced exactly from its gross and its taker share:

    net = gross - maker (entry: always post-only) - (1 - taker_frac) * maker - taker_frac * (taker + slip)

slip = 1 bp on intraday taker legs (the replay's own assumption); 0 on burst legs, whose taker exits already fill at
the next print after the latency.  Averaging over trades gives the closed form used in the report:

    E[net] = E[gross] - (2 - tau) * maker - tau * (taker + slip),   tau = E[taker_frac]

Caveats: the fills are Binance USD-M fills (Binance prices, Binance queue).  Another venue has its own book, depth,
queue and basis; this measures the fee effect only, NOT a replay on that venue.  Selection is pre-registered: per
scenario, per strategy group, the DEV-best variant (mean net) is reported on HOLDOUT; rule as before (n >= 100,
CI95 lower > 0, win > 50 %).
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools import burst_scalper_replay as br  # noqa: E402
from tools import intraday_replay as ir  # noqa: E402

NOTIONAL, EQUITY = 150.0, 5000.0
# (maker bps, taker bps) - per symbol where the venue prices symbols differently.  'src' says where it comes from.
SCENARIOS = {
    'A_BINANCE_VIP0': {'fees': (2.0, 5.0), 'src': 'Binance USD-M VIP0 (the replays\' baseline)'},
    'A2_BINANCE_BNB': {'fees': (1.8, 4.5), 'src': 'Binance VIP0 with the 10 % BNB futures discount'},
    'B_MEXC_WEB': {'fees': (0.0, 2.0), 'src': 'form = MEXC web/app rate (contract/detail 0 % / 0.02 %, 2026-10-10)'},
    'B2_MEXC_API_ORDERS': {'fees': (6.0, 8.0), 'src': 'MEXC API-order schedule from 2026-06-01 (notice of 2026-05-28)'},
    'C1_FORM_REBATE': {'fees': (-0.2, 2.5), 'src': 'form: maker -0.2 bps rebate / taker 2.5'},
    'C2_FORM_1BP': {'fees': (1.0, 2.5), 'src': 'form: maker 1.0 / taker 2.5'},
    'D_HYPERLIQUID_T0': {'fees': (1.5, 4.5), 'src': 'Hyperliquid perps base tier 0.015 % / 0.045 %'},
    'E_BYBIT_VIP0': {'fees': (2.0, 5.5), 'src': 'Bybit linear perps VIP0 0.02 % / 0.055 %'},
    'Z_GROSS': {'fees': (0.0, 0.0), 'slip0': True, 'src': 'no fee, no slip: the gross edge itself'},
}
INTRA_SLIP = 1.0
GROUPS = {'S1': ('S1',), 'S2': ('S2',), 'R1': ('R1',), 'P': ('P5', 'P15')}
MIN_N = 100


def fees_for(sc: dict, symbol: str) -> tuple:
    f = sc['fees']
    return f[symbol] if isinstance(f, dict) else f


def reprice(gross: float, tf: float, maker: float, taker: float, slip: float) -> float:
    return gross - maker - (1.0 - tf) * maker - tf * (taker + slip)


def summarize(rows: list, lo_day: int, hi_day: int, cluster_s: int) -> dict:
    """rows: {t, net, gross, tf}.  CI: bootstrap over `cluster_s` clusters (day for intraday, hour for burst)."""
    tr = [r for r in rows if lo_day <= int(r['t'] // 86400) <= hi_day]
    days = hi_day - lo_day + 1
    if not tr:
        return {'n': 0}
    x = np.array([r['net'] for r in tr])
    cl = np.array([int(r['t'] // cluster_s) for r in tr])
    uc, inv = np.unique(cl, return_inverse=True)
    sums, cnts = np.bincount(inv, x), np.bincount(inv)
    rng = np.random.default_rng(7)
    pick = rng.integers(0, len(uc), size=(2000, len(uc)))
    boots = sums[pick].sum(1) / np.maximum(1, cnts[pick].sum(1))
    daily = np.zeros(days)
    np.add.at(daily, np.array([int(r['t'] // 86400) - lo_day for r in tr]), x / 1e4 * NOTIONAL)
    sd = daily.std(ddof=1)
    return {'n': len(x), 'per_day': len(x) / days, 'win_rate': float((x > 0).mean()), 'mean_net_bps': float(x.mean()),
            'ci95': [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))],
            'gross_bps': float(np.mean([r['gross'] for r in tr])), 'tau': float(np.mean([r['tf'] for r in tr])),
            'usd_per_month': float(daily.mean() * 30), 'sharpe': float(daily.mean() / sd * math.sqrt(365)) if sd > 0 else 0.0,
            'max_dd_usd': float((np.maximum.accumulate(np.r_[0, np.cumsum(daily)]) - np.r_[0, np.cumsum(daily)]).max())}


def intraday_trades(k1m: Path, symbols) -> dict:
    """-> {variant: [{t, symbol, gross, tf}]} from ONE run of tools/intraday_replay.py (lane engine included)."""
    with ProcessPoolExecutor(min(3, len(symbols))) as ex:
        res = dict(ex.map(ir.run_one, [(str(k1m), s) for s in symbols]))
    return {v: [{'t': r['t'], 'symbol': s, 'gross': r['gross_bps'], 'tf': r['taker_frac']}
                for s in symbols for r in res[s][v]] for v in ir.VARIANTS}


def burst_trades(agg: Path, symbols, workers: int) -> dict:
    """-> {variant|model: [{t, symbol, gross, tf, day}]} from ONE run of tools/burst_scalper_replay.py."""
    jobs = [(p, s, p.name.split('-aggTrades-')[1][:10]) for s in symbols
            for p in sorted(agg.glob(f'{s}-aggTrades-*.zip'))]
    with ProcessPoolExecutor(workers) as ex:
        days = list(ex.map(br.run_day, jobs))
    out = {}
    for d in days:
        for k, rows in d['res'].items():
            out.setdefault(k, []).extend({'t': r['t'], 'symbol': d['symbol'], 'gross': r['gross_bps'],
                                          'tf': r['taker_frac']} for r in rows)
    return out


def priced(rows: list, sc: dict, slip: float) -> list:
    out = []
    for r in rows:
        m, t = fees_for(sc, r['symbol'])
        out.append({**r, 'net': reprice(r['gross'], r['tf'], m, t, 0.0 if sc.get('slip0') else slip)})
    return out


def evaluate(trades: dict, dev: tuple, hold: tuple, cluster_s: int, slip: float, groups: dict) -> dict:
    """Per scenario: every variant's DEV / HOLDOUT (pooled and per symbol), DEV-best per group, verdict."""
    lo_d, hi_d, lo_h, hi_h = (ir.day_of(dev[0]), ir.day_of(dev[1]), ir.day_of(hold[0]), ir.day_of(hold[1]))
    res = {}
    for name, sc in SCENARIOS.items():
        r = res[name] = {'variants': {}, 'best': {}}
        for v, rows in trades.items():
            pr = priced(rows, sc, slip)
            syms = sorted({x['symbol'] for x in pr})
            r['variants'][v] = {'DEV': summarize(pr, lo_d, hi_d, cluster_s), 'HOLDOUT': summarize(pr, lo_h, hi_h, cluster_s),
                                'per_symbol': {s: summarize([x for x in pr if x['symbol'] == s], lo_h, hi_h, cluster_s)
                                               for s in syms}}
        for g, pick in groups.items():
            c = [v for v in r['variants'] if pick(v) and r['variants'][v]['DEV'].get('n', 0) >= 30]
            if not c:
                continue
            b = max(c, key=lambda v: r['variants'][v]['DEV']['mean_net_bps'])
            h = r['variants'][b]['HOLDOUT']
            ok = h.get('n', 0) >= MIN_N and h['ci95'][0] > 0 and h['win_rate'] > 0.5
            r['best'][g] = {'variant': b, 'verdict': 'ACTIVE-ELIGIBLE' if ok else 'SHADOW',
                            'per_symbol': {s: ('ACTIVE-ELIGIBLE' if x.get('n', 0) >= MIN_N and x['ci95'][0] > 0
                                               and x['win_rate'] > 0.5 else 'SHADOW')
                                           for s, x in r['variants'][b]['per_symbol'].items()}}
    return res


def fmt(x: dict) -> str:
    if not x.get('n'):
        return 'n 0'
    return (f"n {x['n']:6d} ({x['per_day']:6.1f}/d) win {x['win_rate']:5.1%} net {x['mean_net_bps']:+6.2f} "
            f"[{x['ci95'][0]:+6.2f}, {x['ci95'][1]:+6.2f}] gross {x['gross_bps']:+5.2f} tau {x['tau']:.2f} "
            f"SR {x['sharpe']:+6.2f} ${x['usd_per_month']:+8.2f}/mo DD ${x['max_dd_usd']:.0f}")


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--k1m', type=Path)
    ap.add_argument('--agg', type=Path)
    ap.add_argument('--intraday-symbols', default='BTCUSDT,SOLUSDT,DOGEUSDT')
    ap.add_argument('--burst-symbols', default='SOLUSDT,DOGEUSDT')
    ap.add_argument('--workers', type=int, default=4)
    ap.add_argument('--json', type=Path)
    a = ap.parse_args(argv)
    t0 = time.perf_counter()
    out = {'scenarios': SCENARIOS, 'notional_usd': NOTIONAL, 'equity_usd': EQUITY, 'intraday_slip_bps': INTRA_SLIP}
    if a.k1m:
        tr = intraday_trades(a.k1m, a.intraday_symbols.split(','))
        kind = {v: ir.VARIANTS[v]['kind'] for v in ir.VARIANTS}
        groups = {g: (lambda v, ks=ks: kind[v] in ks) for g, ks in GROUPS.items()}
        out['intraday'] = evaluate(tr, ir.DEV, ir.HOLD, 86400, INTRA_SLIP, groups)
    if a.agg:
        tr = burst_trades(a.agg, a.burst_symbols.split(','), a.workers)
        groups = {'BURST': lambda v: v.endswith('|through') and not v.startswith('MM'),
                  'MM': lambda v: v == 'MM|through'}
        out['burst'] = evaluate(tr, br.DEV, br.HOLD, 3600, 0.0, groups)
    out['seconds'] = time.perf_counter() - t0
    for fam in ('intraday', 'burst'):
        if fam not in out:
            continue
        print(f'\n======== {fam.upper()}  (HOLDOUT, pooled; DEV-best per group marked *)')
        for name, r in out[fam].items():
            m = {b['variant']: g for g, b in r['best'].items()}
            print(f'\n{name}  [{SCENARIOS[name]["src"]}]  best: '
                  + ', '.join(f"{g}={b['variant']}->{b['verdict']} {b['per_symbol']}" for g, b in r['best'].items()))
            for v, x in r['variants'].items():
                if fam == 'burst' and not v.endswith('|through'):
                    continue
                print(f"  {'*' if v in m else ' '} {v:<16} DEV {fmt(x['DEV'])}\n  {'':18} HO  {fmt(x['HOLDOUT'])}")
    print(f'\n{out["seconds"]:.0f}s')
    if a.json:
        a.json.parent.mkdir(parents=True, exist_ok=True)
        a.json.write_text(json.dumps(out, indent=1, default=float))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
