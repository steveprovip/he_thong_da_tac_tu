"""Seconds-not-days verdict on the engine ledger (read-only).

    python tools/edge_check.py [--ledger data/engine_trades.jsonl] [--good 2] [--bad -3] [--by symbol|code|policy|day]
    python tools/edge_check.py --paired a.json b.json     # two equal-length lists of net bps on the SAME events

What it prints, per group:
  * n, mean / std / se of net bps per trade (pnl / entry notional)
  * SPRT  H0: edge = --bad  vs  H1: edge = --good   (Wald, alpha = beta = 5 %)  -> GOOD / BAD / UNDECIDED
  * mSPRT anytime-valid p-value vs 0 (you may look after every trade without inflating false alarms)
  * fixed-n trades needed and SPRT expected n for that separation at the observed sigma
Use --by code (git short hash stamped on every row) to compare before / after a fix without waiting days.
Never writes the ledger.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core import fast_eval as fe  # noqa: E402


def read_rows(path: Path):
    rows = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def group_rows(rows, by: str | None):
    if not by:
        return {'ALL': rows}
    out = {}
    for r in rows:
        out.setdefault(str(r.get(by, '?')), []).append(r)
    return out


def fmt_verdict(name, v):
    if v['n'] < 2:
        return f'{name:<14} n={v["n"]:<4} (too few trades)'
    need = v['fixed_n_needed']
    exp_n = v['sprt_expected_n']
    return (f'{name:<14} n={v["n"]:<4} mean {v["mean_bps"]:+6.2f} bps  sd {v["std_bps"]:5.1f}  '
            f'se {v["se_bps"]:4.1f} | SPRT {v["decision"]:<18} llr {v["sprt"]["llr"]:+6.2f} '
            f'[{v["sprt"]["lower"]:+.2f},{v["sprt"]["upper"]:+.2f}] | mSPRT p {v["msprt"]["p_always_valid"]:.3f} '
            f'| need fixed {need} / SPRT~{exp_n:.0f}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--ledger', type=Path, default=Path('data/engine_trades.jsonl'))
    ap.add_argument('--good', type=float, default=2.0, help='H1 edge, net bps per trade')
    ap.add_argument('--bad', type=float, default=-3.0, help='H0 edge, net bps per trade')
    ap.add_argument('--by', choices=('symbol', 'code', 'policy', 'day', 'mode'), default=None)
    ap.add_argument('--mode', default=None, help='filter: demo / live')
    ap.add_argument('--paired', nargs=2, type=Path, metavar=('A_JSON', 'B_JSON'))
    ap.add_argument('--json', type=Path)
    a = ap.parse_args(argv)

    if a.paired:
        xa = json.loads(a.paired[0].read_text())
        xb = json.loads(a.paired[1].read_text())
        r = fe.paired_diff(xa, xb)
        print(f'paired B-A: n={r["n"]} mean {r["mean"]:+.2f} bps  se {r["se"]:.2f}  t {r["t"]:+.2f}  '
              f'CI95 [{r["ci95"][0]:+.2f}, {r["ci95"][1]:+.2f}]  rho {r["rho"]:.2f}  '
              f'(= {r["efficiency_vs_unpaired"]:.1f}x fewer trades than an unpaired A/B)')
        if a.json:
            a.json.write_text(json.dumps(r, indent=1))
        return 0

    rows = read_rows(a.ledger)
    if a.mode:
        rows = [r for r in rows if r.get('mode') == a.mode]
    out = {}
    print(f'ledger {a.ledger} rows {len(rows)} | H0 {a.bad:+.1f} vs H1 {a.good:+.1f} bps/trade')
    for name, grp in sorted(group_rows(rows, a.by).items()):
        xs = fe.ledger_bps(grp)
        v = fe.verdict(xs, a.good, a.bad) if len(xs) >= 2 else {'n': len(xs)}
        out[name] = v
        print(fmt_verdict(name, v))
    print('\nRule of thumb: sigma 20 bps -> ~99 trades to tell -3 from +2 (fixed n, 80 % power); SPRT ~half that;'
          '\nreplaying A and B on the same events (rho 0.9) -> ~26 pairs.  See tools/factor_scan.py for >= 4h ideas.')
    if a.json:
        a.json.write_text(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
