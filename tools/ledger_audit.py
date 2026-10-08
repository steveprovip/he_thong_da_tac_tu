"""Reproducible baseline of the engine ledger (data/engine_trades.jsonl). Read-only.

    python tools/ledger_audit.py [--ledger data/engine_trades.jsonl] [--json out.json]

Rules: a missing field is UNKNOWN (counted, never summed as 0); gross/fee totals come only from rows that carry
both; drawdown and sessions use ledger order/time; the expectancy interval is a moving-block bootstrap (blocks of
~sqrt(n) consecutive trades) because trades inside one session are not independent. Code tests prove software
behaviour; this report is the economic evidence and it says how thin it is.
"""
import argparse
import json
import math
import random
from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAIGON = timezone(timedelta(hours=7))
SESSION_GAP_S = 1800.0


def load(path: Path) -> list:
    return [json.loads(x) for x in path.read_text(encoding='utf-8').splitlines() if x.strip()]


def block_bootstrap_mean(xs: list, n_boot: int = 4000, seed: int = 7) -> tuple:
    """95 % moving-block bootstrap interval of the mean (block = round(sqrt n))."""
    n = len(xs)
    if n < 2:
        return None, None
    b = max(1, round(math.sqrt(n)))
    rng, means = random.Random(seed), []
    starts = list(range(n - b + 1))
    for _ in range(n_boot):
        out = []
        while len(out) < n:
            s = rng.choice(starts)
            out.extend(xs[s:s + b])
        means.append(sum(out[:n]) / n)
    means.sort()
    return means[int(0.025 * n_boot)], means[int(0.975 * n_boot) - 1]


def max_drawdown(pnl: list) -> float:
    peak = cum = dd = 0.0
    for x in pnl:
        cum += x
        peak = max(peak, cum)
        dd = min(dd, cum - peak)
    return dd


def group_stats(rows: list) -> dict:
    pnl = [float(r['pnl']) for r in rows if r.get('pnl') is not None]
    known = [r for r in rows if r.get('gross') is not None and r.get('fee') is not None]
    gross, fee = sum(r['gross'] for r in known), sum(r['fee'] for r in known)
    wins, losses = [x for x in pnl if x > 0], [x for x in pnl if x <= 0]
    mk = sum(r.get('maker_fills') or 0 for r in known)
    tk = sum(r.get('taker_fills') or 0 for r in known)
    lo, hi = block_bootstrap_mean(pnl)
    holds = sorted(r['hold_s'] for r in rows if r.get('hold_s') is not None)
    return OrderedDict(
        n=len(rows), wins=len(wins), net=round(sum(pnl), 8),
        expectancy=round(sum(pnl) / len(pnl), 6) if pnl else None,
        expectancy_ci95_block=[None if lo is None else round(lo, 6), None if hi is None else round(hi, 6)],
        profit_factor=round(sum(wins) / -sum(losses), 4) if losses and sum(losses) < 0 else None,
        max_drawdown=round(max_drawdown(pnl), 6),
        gross_known=round(gross, 8), fee_known=round(fee, 8), rows_unknown_gross_fee=len(rows) - len(known),
        fee_over_gross=round(fee / gross, 4) if gross > 0 else None,
        gross_positive_net_negative=sum(1 for r in known if r['gross'] >= 0 and r['pnl'] < 0),
        maker_fills=mk, taker_fills=tk, taker_share=round(tk / (mk + tk), 4) if mk + tk else None,
        rows_with_taker_exit=sum(1 for r in known if (r.get('taker_fills') or 0) > 0),
        median_hold_s=holds[len(holds) // 2] if holds else None)


def sessions(rows: list) -> list:
    out, cur, last = [], [], None
    for i, r in enumerate(rows, 1):
        t = r.get('t')
        if cur and t is not None and last is not None and t - last > SESSION_GAP_S:
            out.append(cur)
            cur = []
        cur.append((i, r))
        last = t if t is not None else last
    if cur:
        out.append(cur)
    res = []
    for s in out:
        ts = [r['t'] for _, r in s if r.get('t')]
        res.append(OrderedDict(
            rows=f'#{s[0][0]}-#{s[-1][0]}', n=len(s), net=round(sum(r['pnl'] for _, r in s), 6),
            utc=[datetime.fromtimestamp(min(ts), timezone.utc).strftime('%Y-%m-%d %H:%M'),
                 datetime.fromtimestamp(max(ts), timezone.utc).strftime('%H:%M')] if ts else None,
            saigon=[datetime.fromtimestamp(min(ts), SAIGON).strftime('%Y-%m-%d %H:%M'),
                    datetime.fromtimestamp(max(ts), SAIGON).strftime('%H:%M')] if ts else None,
            symbols=sorted({r.get('symbol', 'UNKNOWN') for _, r in s}),
            entry_types=sorted({r.get('entry_type') or 'UNKNOWN' for _, r in s})))
    return res


def entry_fee_suspect(r: dict, maker_bps: float = 2.0) -> bool:
    """ADOPTED rows written before 2026-10-07r may miss the entry fee (fill window started after the entry):
    fee below 1.5 x one maker side means only the exit side was found. cost_complete=False is explicit."""
    if r.get('cost_complete') is False:
        return True
    if r.get('tag') != 'ADOPTED' or r.get('fee') is None or not r.get('qty') or not r.get('entry'):
        return False
    return r['fee'] < 1.5 * r['qty'] * r['entry'] * maker_bps / 1e4


def audit(rows: list) -> dict:
    key = lambda f: (lambda r: r.get(f) if r.get(f) is not None else 'UNKNOWN')
    by = lambda f: OrderedDict((k, group_stats([r for r in rows if key(f)(r) == k]))
                               for k in sorted({key(f)(r) for r in rows}, key=str))
    recon = [i for i, r in enumerate(rows, 1) if r.get('gross') is not None and r.get('fee') is not None
             and abs(r['gross'] - r['fee'] - r['pnl']) > 1e-6]
    lat = sorted(r['latency_ms'] for r in rows if r.get('latency_ms') is not None)
    strategy = [r for r in rows if r.get('tag') == 'AUTO']
    return OrderedDict(
        rows=len(rows), modes=sorted({f"{r.get('mode', 'UNKNOWN')}|{r.get('venue', 'UNKNOWN')}" for r in rows}),
        tags={t: sum(1 for r in rows if (r.get('tag') or 'UNKNOWN') == t) for t in {r.get('tag') or 'UNKNOWN' for r in rows}},
        unknown_fields={f: [i for i, r in enumerate(rows, 1) if r.get(f) is None]
                        for f in ('gross', 'fee', 'entry_type', 'maker_fills', 'latency_ms', 'tag', 'path')},
        reconcile_gross_minus_fee_ne_net=recon,
        all=group_stats(rows), auto_only=group_stats(strategy),
        cost_incomplete_rows=[i for i, r in enumerate(rows, 1) if entry_fee_suspect(r)],
        known_cost_net=round(sum(r['pnl'] for r in rows if r.get('gross') is not None and r.get('fee') is not None
                                 and not entry_fee_suspect(r)), 8),
        adopted_entry_fee_estimate=round(sum(r['qty'] * r['entry'] * 2e-4 for r in rows if entry_fee_suspect(r)), 8),
        by_tag=by('tag'), by_policy=by('policy'),
        by_symbol=by('symbol'), by_entry_type=by('entry_type'), by_path=by('path'), by_reason=by('reason'),
        sessions=sessions(rows),
        latency_ms_ledger={'n': len(lat), 'p50': lat[len(lat) // 2] if lat else None,
                           'p90': lat[int(0.9 * (len(lat) - 1))] if lat else None,
                           'note': 'decision -> fill incl. the GTX queue wait; not network latency'},
        missing_instrumentation=['policy/version per row', 'signal-feed mid vs venue mid at entry (basis)',
                                 'send->ack and ack->fill timestamps', 'post-fill markouts', 'funding',
                                 'cancelled / rejected / unfilled order outcomes (only fills are logged)'])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument('--ledger', default=str(ROOT / 'data' / 'engine_trades.jsonl'))
    ap.add_argument('--json', default=None)
    a = ap.parse_args()
    rep = audit(load(Path(a.ledger)))
    s = json.dumps(rep, indent=1, default=str, ensure_ascii=False)
    if a.json:
        Path(a.json).write_text(s, encoding='utf-8')
    print(s)


if __name__ == '__main__':
    main()
