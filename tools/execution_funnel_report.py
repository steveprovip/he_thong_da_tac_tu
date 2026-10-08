"""Execution funnel, GTX attempt timeline, latency and P&L slices from a consistent snapshot. Read-only.

    python tools/execution_funnel_report.py [--orders ...] [--ledger ...] [--since EPOCH] [--json out.json]

engine_orders.jsonl rows are one per attempt that got past the slot check (outcome FILLED / PARTIAL / UNFILLED /
REJECTED / TOXIC / ERROR = sent to the venue; REFUSED = stopped before sending; SHADOW_SCALP = logged, never sent).
There is no order id / exchange fill timestamp in the row, so an attempt = a row; a fill seen by polling is not the
exchange fill time.  touch_qty_last is the AGGREGATE displayed size at the touch at expiry and includes our own
order when we are at the touch: it is not the queue ahead of us.

Ledger slices: rows without gross+fee are UNKNOWN cost (counted, not summed as 0); ADOPT rows have no own entry fee.
"""
import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SAIGON = timezone(timedelta(hours=7))
SENT = ('FILLED', 'PARTIAL', 'UNFILLED', 'REJECTED', 'TOXIC', 'ERROR')


def load(p):
    return [json.loads(x) for x in Path(p).read_text(encoding='utf-8').splitlines() if x.strip()]


def q(xs, p):
    xs = sorted(x for x in xs if x is not None)
    if not xs:
        return None
    k = (len(xs) - 1) * p
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def hhmm(t):
    return datetime.fromtimestamp(t, SAIGON).strftime('%m-%d %H:%M:%S')


def queue_class(r):
    tq, qr = r.get('touch_qty_last'), r.get('qty_req')
    if tq is None or not qr:
        return 'UNKNOWN'
    if r.get('px_last') is not None and r.get('touch_last') is not None and abs(r['px_last'] - r['touch_last']) > 1e-12:
        return 'NOT_AT_TOUCH'
    return 'ALONE' if tq <= qr * 1.05 + 1e-12 else 'BEHIND'


def orders_report(rows, since):
    out = {'rows': len(rows), 'by_outcome': Counter(r['outcome'] for r in rows)}
    key = lambda r: f"{r['symbol']}|{r.get('path')}|{r.get('policy')}|{r.get('mode')}"
    tab = defaultdict(Counter)
    for r in rows:
        tab[key(r)][r['outcome']] += 1
    out['by_symbol_path_policy_mode'] = {k: dict(v) for k, v in sorted(tab.items())}
    sent = [r for r in rows if r['outcome'] in SENT]
    out['sent'] = len(sent)
    out['sent_filled'] = sum(r['outcome'] in ('FILLED', 'PARTIAL') for r in sent)
    out['fill_rate'] = round(out['sent_filled'] / len(sent), 4) if sent else None
    qc = defaultdict(Counter)
    for r in sent:
        qc[r['symbol']][(queue_class(r), r['outcome'])] += 1
    out['queue_class'] = {s: {f'{a}|{b}': n for (a, b), n in c.items()} for s, c in qc.items()}
    lat = {}
    for k in ('decide_to_send_ms', 'ack_ms', 'fill_wait_ms', 'total_ms'):
        xs = [r.get(k) for r in sent]
        lat[k] = {'n': sum(x is not None for x in xs), 'p50': q(xs, .5), 'p95': q(xs, .95), 'p99': q(xs, .99)}
    out['latency_sent'] = lat
    bas = defaultdict(list)
    for r in sent:
        if r.get('basis_bps') is not None:
            bas[r['symbol']].append(r['basis_bps'])
    out['basis_bps'] = {s: {'n': len(v), 'p05': q(v, .05), 'p50': q(v, .5), 'p95': q(v, .95)} for s, v in bas.items()}
    tl = [r for r in sent if r['t'] >= since] if since else sent
    out['timeline'] = [{'t': hhmm(r['t']), 'symbol': r['symbol'], 'side': r.get('side'), 'path': r.get('path'),
                        'outcome': r['outcome'], 'qty': r.get('qty_req'), 'filled': r.get('qty_filled'),
                        'reprices': r.get('reprices'), 'px_first': r.get('px_first'), 'px_last': r.get('px_last'),
                        'touch_last': r.get('touch_last'), 'touch_qty_last': r.get('touch_qty_last'),
                        'queue': queue_class(r), 'ack_ms': r.get('ack_ms'), 'basis_bps': r.get('basis_bps'),
                        'policy': r.get('policy')} for r in tl]
    shadow = [r for r in rows if r['outcome'] == 'SHADOW_SCALP' and (not since or r['t'] >= since)]
    out['since'] = {'epoch': since, 'sent': len(tl), 'shadow': len(shadow),
                    'refused': sum(r['outcome'] == 'REFUSED' and r['t'] >= since for r in rows) if since else None}
    return out


def ledger_report(rows):
    def slice_(keyf):
        acc = defaultdict(lambda: {'n': 0, 'wins': 0, 'net': 0.0, 'gross': 0.0, 'fee': 0.0, 'cost_unknown': 0})
        for r in rows:
            a = acc[keyf(r)]
            a['n'] += 1
            pnl = r.get('pnl')
            if pnl is not None:
                a['net'] += pnl
                a['wins'] += pnl > 0
            if r.get('gross') is None or r.get('fee') is None or r.get('cost_complete') is False \
                    or r.get('path') == 'ADOPT':
                a['cost_unknown'] += 1
            else:
                a['gross'] += r['gross']
                a['fee'] += r['fee']
        return {k: {kk: (round(vv, 6) if isinstance(vv, float) else vv) for kk, vv in v.items()}
                for k, v in sorted(acc.items(), key=lambda kv: str(kv[0]))}
    lane = lambda r: 'MR' if r.get('path') == 'MR' else ('TREND' if r.get('path') in ('SWARM', 'JEV') else r.get('path'))
    return {'n': len(rows), 'net_recorded': round(sum(r.get('pnl') or 0 for r in rows), 8),
            'by_symbol': slice_(lambda r: r.get('symbol')), 'by_lane': slice_(lane),
            'by_policy': slice_(lambda r: r.get('policy') or 'pre-schema-2'),
            'by_regime': slice_(lambda r: r.get('regime') or 'UNKNOWN'),
            'by_exit': slice_(lambda r: r.get('reason') or 'UNKNOWN'),
            'by_entry_type': slice_(lambda r: r.get('entry_type') or 'UNKNOWN')}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--orders', default='data/engine_orders.jsonl')
    ap.add_argument('--ledger', default='data/engine_trades.jsonl')
    ap.add_argument('--since', type=float, default=0.0)
    ap.add_argument('--json', default='')
    a = ap.parse_args()
    rep = {'orders': orders_report(load(ROOT / a.orders), a.since), 'ledger': ledger_report(load(ROOT / a.ledger)),
           'inputs': {'orders': a.orders, 'ledger': a.ledger}}
    s = json.dumps(rep, indent=1, ensure_ascii=False, default=dict)
    if a.json:
        Path(ROOT / a.json).write_text(s, encoding='utf-8')
    o = rep['orders']
    print('orders', o['rows'], dict(o['by_outcome']), 'sent', o['sent'], 'filled', o['sent_filled'], 'rate', o['fill_rate'])
    print('queue', json.dumps(o['queue_class']))
    print('latency', json.dumps(o['latency_sent']))
    print('basis', json.dumps(o['basis_bps']))
    print('since', o['since'])
    L = rep['ledger']
    print('ledger n', L['n'], 'net', L['net_recorded'])
    for k in ('by_symbol', 'by_lane', 'by_policy', 'by_exit', 'by_entry_type'):
        print(k, json.dumps(L[k]))


if __name__ == '__main__':
    main()
