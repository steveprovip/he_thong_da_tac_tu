"""Monte Carlo of position sizing on the real ledger (data/engine_trades.jsonl). Read-only.

    python tools/simulate_position_sizing.py [--ledger ...] [--paths 10000] [--trades 200] [--equity 5000] [--json out]

Per closed trade the scale-free result is r = net PnL / entry notional (fees included as recorded).  A sizing
policy that puts a fraction f of equity into notional turns it into an equity return f * r (linear for small f;
liquidity limits are a separate check: tools output docs/research_upgrade/liquidity_*_2026-10-08y.json).

Resampling (10 000 paths x N trades):
  boot   : iid bootstrap of the group's r
  block  : circular block bootstrap (block = round(sqrt n)) - keeps streaks, no edge bias
  null   : r - mean(r) : same dispersion, zero edge (what a lucky streak looks like when there is no edge)
  stress : boot - 2 bps per trade (wider exits / slippage at size)
Policies: notional fractions 0.9 % (now ~ $46), 2 %, 5 %, 10 %, 15 %; 'B10' = 10 % of equity lost AT THE STOP
(notional = 0.10 / (SL 0.35 % + fees 0.08 %) = 23.3 x equity -> needs 23x leverage > the 5x account setting).
Kelly: f* = mean / variance of r (continuous approximation); a CI of the mean that includes 0 means f* is unknown.
The sample is small (SOL 15 trades): every number here is conditional on that sample, not a forecast.
"""
import argparse
import json
import math
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
FRACS = {'now_0.9%': 0.009, '2%': 0.02, '5%': 0.05, '10%': 0.10, '15%': 0.15, 'B10_risk10%': 0.10 / 0.0043}
MODES = ('boot', 'block', 'null', 'stress')


def walk_book(levels, qty):
    """Taker slippage in bps vs the touch for `qty` against depth levels [(price, size), ...] best first;
    None when the shown depth is not enough."""
    rem, cost, top = qty, 0.0, float(levels[0][0]) if levels else None
    for px, sz in levels:
        take = min(rem, float(sz))
        cost += take * float(px)
        rem -= take
        if rem <= 1e-12:
            return abs(cost / qty / top - 1) * 1e4
    return None


def returns(rows, symbol=None):
    out = []
    for r in rows:
        if symbol and r.get('symbol') != symbol:
            continue
        q, e, pnl = r.get('qty'), r.get('entry'), r.get('pnl')
        if not q or not e or pnl is None:
            continue
        out.append(float(pnl) / (float(q) * float(e)))
    return np.array(out)


def sample(r, mode, paths, n, rng):
    if mode == 'block':
        b = max(1, round(math.sqrt(len(r))))
        k = math.ceil(n / b)
        starts = rng.integers(0, len(r), size=(paths, k))         # circular blocks: every trade equally likely
        idx = ((starts[:, :, None] + np.arange(b)[None, None, :]) % len(r)).reshape(paths, -1)[:, :n]
        return r[idx]
    x = r[rng.integers(0, len(r), size=(paths, n))]
    if mode == 'null':
        x = x - r.mean()
    elif mode == 'stress':
        x = x - 2e-4
    return x


def simulate(r, f, mode, paths=10000, n=200, equity=5000.0, seed=7):
    rng = np.random.default_rng(seed)
    x = np.maximum(f * sample(r, mode, paths, n, rng), -0.999999)   # equity return per trade (floor: cannot go < 0)
    eq = equity * np.cumprod(1 + x, axis=1)
    peak = np.maximum.accumulate(np.concatenate([np.full((paths, 1), equity), eq], axis=1), axis=1)[:, 1:]
    dd = 1 - eq / peak
    mdd = dd.max(axis=1)
    term = eq[:, -1]
    lg = np.log1p(x).mean(axis=1)
    pct = lambda a, q: float(np.percentile(a, q))
    return {'terminal_p05': round(pct(term, 5), 2), 'terminal_p50': round(pct(term, 50), 2),
            'terminal_p95': round(pct(term, 95), 2), 'p_loss': round(float((term < equity).mean()), 4),
            'pnl_p50_usdt': round(pct(term, 50) - equity, 3),
            'mdd_p50': round(pct(mdd, 50), 5), 'mdd_p95': round(pct(mdd, 95), 5), 'mdd_p99': round(pct(mdd, 99), 5),
            'p_dd_10': round(float((mdd >= 0.10).mean()), 4), 'p_dd_50_ruin': round(float((mdd >= 0.50).mean()), 4),
            'growth_per_trade_bps': round(float(lg.mean()) * 1e4, 4)}


def stats(r, n_boot=10000, seed=11):
    rng = np.random.default_rng(seed)
    m = r[rng.integers(0, len(r), size=(n_boot, len(r)))].mean(axis=1)
    var = float(r.var(ddof=1)) if len(r) > 1 else float('nan')
    return {'n': int(len(r)), 'mean_bps': round(float(r.mean()) * 1e4, 3),
            'ci95_mean_bps': [round(float(np.percentile(m, 2.5)) * 1e4, 3), round(float(np.percentile(m, 97.5)) * 1e4, 3)],
            'sd_bps': round(math.sqrt(var) * 1e4, 3), 'win_rate': round(float((r > 0).mean()), 3),
            'avg_win_bps': round(float(r[r > 0].mean()) * 1e4, 3) if (r > 0).any() else None,
            'avg_loss_bps': round(float(r[r <= 0].mean()) * 1e4, 3) if (r <= 0).any() else None,
            'sharpe_per_trade': round(float(r.mean()) / math.sqrt(var), 4) if var > 0 else None,
            'kelly_f': round(float(r.mean()) / var, 3) if var > 0 else None,
            'p_mean_le_0': round(float((m <= 0).mean()), 4)}


def run(rows, paths=10000, n=200, equity=5000.0):
    groups = {'ALL': returns(rows), 'SOL': returns(rows, 'SOLUSDT'), 'DOGE': returns(rows, 'DOGEUSDT')}
    rep = {'stats': {g: stats(r) for g, r in groups.items() if len(r) > 1}, 'mc': {}}
    for g in ('SOL', 'ALL'):
        r = groups[g]
        for mode in MODES:
            for name, f in FRACS.items():
                rep['mc'][f'{g}|{mode}|{name}'] = simulate(r, f, mode, paths, n, equity)
    # Option B arithmetic: 5 full stops in a row at 10 % risk each
    rep['option_b_five_stops_left'] = round(0.9 ** 5, 4)
    return rep


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ledger', default='data/engine_trades.jsonl')
    ap.add_argument('--paths', type=int, default=10000)
    ap.add_argument('--trades', type=int, default=200)
    ap.add_argument('--equity', type=float, default=5000.0)
    ap.add_argument('--json', default='')
    a = ap.parse_args()
    rows = [json.loads(x) for x in (ROOT / a.ledger).read_text(encoding='utf-8').splitlines() if x.strip()]
    rep = {'ledger': a.ledger, 'rows': len(rows), 'paths': a.paths, 'trades_per_path': a.trades, 'equity': a.equity,
           **run(rows, a.paths, a.trades, a.equity)}
    if a.json:
        (ROOT / a.json).write_text(json.dumps(rep, indent=1), encoding='utf-8')
    for g, s in rep['stats'].items():
        print(g, s)
    for k, v in rep['mc'].items():
        print(f'{k:28s} {v}')


if __name__ == '__main__':
    main()
