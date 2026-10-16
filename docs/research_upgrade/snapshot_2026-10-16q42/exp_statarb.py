"""Stat-arb SOL/DOGE: run the shipped OnlineKalmanPairTracker on 1 s-like vs 1 m bars; count entries + net bps."""
import sys, zipfile, io, numpy as np, pandas as pd
sys.path.insert(0, r'F:\he_thong_da_tac_tu_2')
from core.stat_arb_lane import OnlineKalmanPairTracker
K = r'F:\he_thong_da_tac_tu_2\data\klines_1m'
def load(sym, months):
    out = []
    for m in months:
        with zipfile.ZipFile(f'{K}\{sym}-1m-{m}.zip') as z:
            raw = z.read(z.namelist()[0]).decode()
        df = pd.read_csv(io.StringIO(raw), header=None)
        if not str(df.iloc[0, 0]).isdigit(): df = df.iloc[1:]
        out.append(pd.DataFrame({'t': df[0].astype('int64'), 'c': df[4].astype(float)}))
    return pd.concat(out).drop_duplicates('t').set_index('t')['c']
def run(a, b, cost=12.0, ou=120, z_entry=2.1, gate_mult=3.5):
    tr = OnlineKalmanPairTracker('A', 'B', 1e-5, 1e-4, 1e-3, ou)
    pos, trades, stds = None, [], []
    for pa, pb in zip(a, b):
        sig = tr.evaluate_signal(pa, pb, round_trip_cost_bps=cost * gate_mult / 3.5, z_entry=z_entry)
        stds.append(sig.spread_std_bps)
        if sig.action.startswith('ENTER'):
            pos = (1 if 'LONG' in sig.action else -1, pa, pb, sig.hedge_ratio)
        elif sig.action in ('EXIT_SPREAD', 'STOP_BREAKDOWN') and pos:
            s, a0, b0, be = pos
            g = s * ((pa / a0 - 1) - be * (pb / b0 - 1)) * 1e4
            trades.append((g, g - cost, sig.action)); pos = None
    return trades, np.array(stds)
months = ['2026-07', '2026-08', '2026-09']
sol, doge = load('SOLUSDT', months), load('DOGEUSDT', months)
j = sol.index.intersection(doge.index); sol, doge = sol.loc[j].values, doge.loc[j].values
print('bars', len(j))
for gm in (3.5, 2.0, 1.0):
    tr, st = run(sol, doge, gate_mult=gm)
    g = np.array([x[0] for x in tr]); n = np.array([x[1] for x in tr])
    print(f'1m bars gate {gm}x cost: std_bps p50={np.percentile(st,50):.1f} p90={np.percentile(st,90):.1f} trades={len(tr)}',
          (f'gross/tr={g.mean():.1f} net/tr={n.mean():.1f} win={np.mean(n>0):.2f} stops={sum(x[2]=="STOP_BREAKDOWN" for x in tr)}' if len(tr) else ''))

print('--- grid on process noise (DEV = Jul-Aug, HOLDOUT = Sep), 1 m bars, cost 12 bps')
def run2(a, b, da, db, ou, cost=12.0, z_entry=2.1):
    tr = OnlineKalmanPairTracker('A', 'B', da, db, 1e-6, ou)
    pos, trades, zs, stat = None, [], [], 0
    for pa, pb in zip(a, b):
        sig = tr.evaluate_signal(pa, pb, round_trip_cost_bps=cost, z_entry=z_entry)
        zs.append(abs(sig.z_score)); stat += sig.reason != 'insufficient_cointegration_or_spread_vol'
        if sig.action.startswith('ENTER'):
            pos = (1 if 'LONG' in sig.action else -1, pa, pb, sig.hedge_ratio)
        elif sig.action in ('EXIT_SPREAD', 'STOP_BREAKDOWN') and pos:
            s, a0, b0, be = pos
            g = s * ((pa / a0 - 1) - be * (pb / b0 - 1)) * 1e4
            trades.append(g - cost); pos = None
    return np.array(trades), np.percentile(zs, 99), stat / len(zs)
cut = int(len(sol) * 2 / 3)
for da, db in ((1e-7, 1e-6), (1e-8, 1e-7), (1e-9, 1e-8), (1e-10, 1e-9)):
    for ou in (120, 480):
        out = []
        for nm, sl in (('DEV', slice(0, cut)), ('HOLD', slice(cut, None))):
            n, z99, ok = run2(sol[sl], doge[sl], da, db, ou)
            out.append(f'{nm}: n={len(n)} net/tr={n.mean() if len(n) else float("nan"):6.1f} win={np.mean(n>0) if len(n) else 0:.2f} |z|p99={z99:.2f} gate_ok={ok:.2f}')
        print(f'da={da:g} db={db:g} ou={ou}:', ' | '.join(out))
