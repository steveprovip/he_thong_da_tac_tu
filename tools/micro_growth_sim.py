"""Growth of a 5 U micro account (2026-11-13): what +10 U / hour requires, and what measured trades give.

    python tools/micro_growth_sim.py                         # ledger + backtest + requirement table
    python tools/micro_growth_sim.py --p 0.55 --r 2.0 --n-hour 6 --risk-frac 0.01

Per trade a bracket risks `risk` USDT at the stop (fees included, the 0.05 U micro budget by default, or risk_frac x
equity when compounding) and returns +R x risk with probability p, else -risk. Rules of the micro profile: daily kill
0.15 U (fixed-risk mode), 2 net losses in a row stop the trading day, no trade once equity < `ruin` (default 1 U:
the 5.05 U minimum notional at 20x leverage needs >= 0.25 U margin, plus the stop). Outcomes can also be bootstrapped
from measured NET bps per trade expressed in R (`--ledger`, `--backtest`). No network, no orders.
"""
from __future__ import annotations

import argparse
import json
import math
import random
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def required_per_hour(target_usdt: float, risk_usdt: float, r_net: float, p: float) -> float:
    """Trades per hour needed for an EXPECTED +target_usdt / hour at fixed risk (inf when the edge is <= 0)."""
    ev = risk_usdt * (p * r_net - (1.0 - p))
    return math.inf if ev <= 0 else target_usdt / ev


def log_growth(p: float, r_net: float, f: float) -> float:
    """Expected log growth per trade when risking a fraction f of equity (Kelly criterion objective)."""
    if not 0 <= f < 1:
        raise ValueError('f must be in [0, 1)')
    return p * math.log1p(f * r_net) + (1.0 - p) * math.log1p(-f)


def kelly_f(p: float, r_net: float) -> float:
    return max(0.0, p - (1.0 - p) / r_net)


def simulate(outcomes_r, n_hour: float, hours: float, rng: random.Random, equity: float = 5.0,
             risk_usdt: float | None = 0.05, risk_frac: float | None = None, daily_kill: float | None = 0.15,
             session_losses: int = 2, ruin: float = 1.0) -> dict:
    """One path: trades arrive every 1 / n_hour hours; outcome in R drawn from `outcomes_r` (callable or list)."""
    draw = outcomes_r if callable(outcomes_r) else (lambda: rng.choice(outcomes_r))
    n = int(round(n_hour * hours))
    per_day = max(1, int(round(n_hour * 24)))
    eq, day_pnl, streak, peak, mdd, taken = equity, 0.0, 0, equity, 0.0, 0
    for i in range(n):
        if i % per_day == 0:
            day_pnl, streak = 0.0, 0
        if eq < ruin:
            break
        if session_losses and streak >= session_losses:
            continue
        if daily_kill is not None and risk_frac is None and day_pnl <= -daily_kill:
            continue
        risk = risk_frac * eq if risk_frac is not None else risk_usdt
        pnl = risk * draw()
        eq += pnl
        day_pnl += pnl
        streak = streak + 1 if pnl < 0 else 0
        taken += 1
        peak = max(peak, eq)
        mdd = max(mdd, peak - eq)
    return {'equity': eq, 'mdd': mdd, 'trades': taken, 'ruined': eq < ruin}


def monte_carlo(outcomes_r, n_hour: float, hours: float, paths: int = 2000, seed: int = 20261113, **kw) -> dict:
    rng = random.Random(seed)
    res = [simulate(outcomes_r, n_hour, hours, rng, **kw) for _ in range(paths)]
    eqs = sorted(r['equity'] for r in res)
    q = lambda a: round(eqs[min(len(eqs) - 1, int(a * len(eqs)))], 4)
    start = kw.get('equity', 5.0)
    return {'hours': hours, 'paths': paths, 'p05': q(0.05), 'median': q(0.5), 'p95': q(0.95),
            'mean_net_per_hour': round((sum(eqs) / len(eqs) - start) / hours, 6),
            'p_ruin': round(sum(r['ruined'] for r in res) / paths, 4),
            'p_plus_10u': round(sum(r['equity'] >= start + 10.0 for r in res) / paths, 4),
            'mean_mdd': round(sum(r['mdd'] for r in res) / paths, 4)}


def ledger_r(path: Path, micro_only: bool = False, loss_cost_bps: float = 8.0) -> list:
    """Measured NET bps per AUTO trade -> R units, R = the trade's net / its stop distance is unknown in old rows, so
    one stop = the median |loss| in bps (documented approximation)."""
    rows = [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]
    rows = [r for r in rows if r.get('tag') == 'AUTO' and r.get('pnl') is not None and (r.get('micro') or not micro_only)]
    bps = [float(r['pnl']) / (float(r['qty']) * float(r['entry'])) * 1e4 for r in rows if r.get('qty') and r.get('entry')]
    losses = sorted(-b for b in bps if b < 0)
    unit = losses[len(losses) // 2] if losses else loss_cost_bps
    return [b / unit for b in bps]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--p', type=float, default=0.55)
    ap.add_argument('--r', type=float, default=1.9, help='net reward:risk of a winner after fees')
    ap.add_argument('--n-hour', type=float, default=6.0, help='trades per hour (live profile cap: 6)')
    ap.add_argument('--risk-frac', type=float, default=None)
    ap.add_argument('--paths', type=int, default=2000)
    ap.add_argument('--out', default=str(ROOT / 'data' / 'micro_growth_sim.json'))
    a = ap.parse_args(argv)
    out = {'requirement': {}, 'param': {}, 'ledger': {}, 'backtest': {}}
    for p in (0.5, 0.55, 0.6, 0.7, 1.0):
        fk = min(kelly_f(p, 1.9), 0.99)                     # full Kelly; f = 1 (p = 1) is ruin on any loss
        out['requirement'][f'p={p}'] = {
            'trades_per_hour_for_10u_at_0.05u_risk_R1.9': round(required_per_hour(10.0, 0.05, 1.9, p), 1),
            'kelly_f': round(fk, 4),
            'hours_5u_to_15u_at_full_kelly_6_per_h': (round(math.log(3) / (6 * log_growth(p, 1.9, fk)), 2)
                                                      if fk > 0 else None),
            'hours_5u_to_15u_at_3pct_risk_6_per_h': (round(math.log(3) / (6 * log_growth(p, 1.9, 0.03)), 1)
                                                     if log_growth(p, 1.9, 0.03) > 0 else None)}
    win = lambda rng: (lambda: a.r if rng.random() < a.p else -1.0)
    for hrs in (1, 24, 24 * 7, 24 * 30):
        rng = random.Random(1)
        out['param'][f'{hrs}h'] = monte_carlo(win(rng), a.n_hour, hrs, a.paths, risk_frac=a.risk_frac)
    lp = ROOT / 'data' / 'engine_trades.jsonl'
    if lp.exists():
        r_all = ledger_r(lp)
        for hrs in (1, 24, 24 * 7):
            out['ledger'][f'{hrs}h'] = monte_carlo(r_all, a.n_hour, hrs, a.paths)
        out['ledger']['mean_R'] = round(sum(r_all) / len(r_all), 4)
        out['ledger']['n'] = len(r_all)
    bp = ROOT / 'data' / 'smc_fibo_backtest.json'
    if bp.exists():
        s = json.loads(bp.read_text(encoding='utf-8'))['summary'].get('holdout:R2_BEoff', {})
        r_bt = s.get('net_R_list')
        if r_bt:
            for hrs in (1, 24 * 30):
                out['backtest'][f'{hrs}h'] = monte_carlo(r_bt, a.n_hour, hrs, a.paths)
            out['backtest']['mean_R'] = round(sum(r_bt) / len(r_bt), 4)
    Path(a.out).write_text(json.dumps(out, indent=1), encoding='utf-8')
    print(json.dumps(out, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
