"""Phan tich gop nhieu phien: P1 (bat cu chon), P2 (p_fill maker-exit), P3 (bo loc F4 tren probe moi).

Chi doc. Khong ghi vao bat cu file data nao. Chay luc bot dang chay cung an toan
(ngoai tru: chay KHI PHIEN DANG CHAY thi P2 chi la so bo phan).

Nguon du lieu:
  data/testnet-settled-v2.json     -- ledger ket qua settled (net_return, closed_at, trade_id, origin)
  data/testnet-journal.sqlite      -- events(kind, timestamp, payload)
  data/testnet-signal-lab-v2.jsonl -- probe tung ban ghi (conviction, atr_bps, spread_bps, gross_bps...)
  data/testnet-mission-report.json -- demer cua phien gan nhat (maker_exits, session_pnl)

Quy uoc gia dinh (ghi ro trong output):
  * fill_gross_bps = net_return*1e4 + 6.0 (phai taker exit; neu maker-exit khop thi thuc te la +4.0,
    sai so toi da 2 bps/mau, huong be P1 ve duong -> bao toan).
  * P2 tu journal la phan loai suy dien (heuristic); counter trong mission-report la ground truth.
"""
from __future__ import annotations
import argparse, json, math, random, sqlite3, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
F4 = {'conv_min': 0.3575, 'atr_lo': 6.0, 'atr_hi': 20.0, 'spread_min': 2.0}
TAKER_ROUNDTRIP_BPS = 6.0


def ktc95(values, seed=7, draws=2000):
    n = len(values)
    if n == 0:
        return None, None
    if n == 1:
        return values[0], values[0]
    rng = random.Random(seed)
    means = []
    for _ in range(draws):
        means.append(sum(values[rng.randrange(n)] for _ in range(n)) / n)
    means.sort()
    return means[int(0.025 * draws)], means[int(0.975 * draws)]


def wilson(k, n, z=1.96):
    if n == 0:
        return None, None
    p = k / n
    d = 1 + z*z/n
    c = p + z*z/(2*n)
    h = z * math.sqrt(p*(1-p)/n + z*z/(4*n*n))
    return (c-h)/d, (c+h)/d


def load_ledger(path):
    d = json.loads(path.read_text(encoding='utf-8'))
    return d['records'] if isinstance(d, dict) else d


def load_events(path):
    con = sqlite3.connect(f'file:{path}?mode=ro', uri=True)
    rows = con.execute('select kind, timestamp, payload from events order by timestamp').fetchall()
    con.close()
    out = []
    for kind, ts, payload in rows:
        try:
            out.append((kind, ts, json.loads(payload)))
        except json.JSONDecodeError:
            pass
    return out


def load_probes(path):
    probes = []
    with path.open(encoding='utf-8') as fh:
        for line in fh:
            line = line.strip()
            if line:
                try:
                    probes.append(json.loads(line))
                except json.JSONDecodeError:
                    pass
    return probes


def entry_side_and_fill(journal, trade_id):
    """side tu ENTRY_INTENT; thoi diem fill som nhat tu ORDER_STATE."""
    side = None
    fill_t = None
    for kind, ts, p in journal:
        if kind == 'ENTRY_INTENT' and p.get('client_id') == trade_id:
            side = p.get('side') or side
        if kind == 'ORDER_STATE' and p.get('client_id') == trade_id:
            try:
                if float(p.get('filled', 0)) > 0:
                    fill_t = ts if fill_t is None else min(fill_t, ts)
            except (TypeError, ValueError):
                pass
    return side, fill_t


def p1_adverse_selection(ledger, journal, probes, since, window=60.0):
    pairs = []
    probes_sorted = sorted((p for p in probes if p.get('outcome')), key=lambda p: p['opened'])
    for rec in ledger:
        if rec.get('origin', 'live') != 'live':
            continue
        if rec['closed_at'] < since:
            continue
        side, fill_t = entry_side_and_fill(journal, rec['trade_id'])
        if side is None or fill_t is None:
            continue
        best, best_gap = None, None
        for pr in probes_sorted:
            if pr['symbol'] != rec['symbol'] or pr.get('side') != side:
                continue
            gap = abs(pr['opened'] - fill_t)
            if gap <= window and (best is None or gap < best_gap):
                best, best_gap = pr, gap
        if best is None:
            continue
        fill_gross = rec['net_return'] * 1e4 + TAKER_ROUNDTRIP_BPS
        pairs.append({'symbol': rec['symbol'], 'trade_id': rec['trade_id'],
                      'fill_gross': round(fill_gross, 2), 'probe_gross': round(best['gross_bps'], 2),
                      'as_bps': round(fill_gross - best['gross_bps'], 2)})
    if not pairs:
        return {'n': 0, 'ghichu': 'chua du cap fill<->probe; can them phien'}
    as_vals = [p['as_bps'] for p in pairs]
    lo, hi = ktc95(as_vals)
    mean = sum(as_vals) / len(as_vals)
    sd = math.sqrt(sum((v-mean)**2 for v in as_vals) / max(1, len(as_vals)-1))
    return {'n': len(pairs), 'mean_as_bps': round(mean, 3), 'sd': round(sd, 3),
            'ktc95': [round(lo, 3), round(hi, 3)], 'pairs': pairs,
            'ghichu': 'AS = fill_gross - probe_gross; KTC duoi <= -2.0 -> loai spread>=2 khoi bo loc'}


def p2_maker_fill(journal, since, grace=8.0, slack=2.0):
    placed = filled = fallback = 0
    for kind, ts, p in journal:
        if kind != 'MAKER_EXIT_INTENT' or ts < since:
            continue
        placed += 1
        sym = p['symbol']
        end = ts + grace + slack
        saw_exit_intent = any(k == 'EXIT_INTENT' and q.get('symbol') == sym and ts < t2 <= end
                              for k, t2, q in journal)
        saw_close = any(k == 'POSITION_CLOSED' and q.get('symbol') == sym and ts < t2 <= end
                        for k, t2, q in journal)
        if saw_exit_intent or not saw_close:
            fallback += 1
        else:
            filled += 1
    lo, hi = wilson(filled, placed)
    return {'placed': placed, 'filled_heuristic': filled, 'fallback_heuristic': fallback,
            'p_fill': round(filled/placed, 3) if placed else None,
            'wilson95': [round(lo, 3), round(hi, 3)] if placed else None,
            'ghichu': 'heuristic tu journal; ground truth la maker_exits trong mission-report tung phien'}


def p3_frozen_filter(probes, since):
    resolved = [p for p in probes if p.get('outcome') and p.get('recorded', 0) >= since]
    f4 = [p for p in resolved if p['conviction'] >= F4['conv_min']
          and F4['atr_lo'] <= p['atr_bps'] < F4['atr_hi']
          and p['spread_bps'] >= F4['spread_min']]
    def stats(group):
        vals = [p['gross_bps'] for p in group]
        lo, hi = ktc95(vals)
        return {'n': len(vals), 'mean_gross': round(sum(vals)/len(vals), 3) if vals else None,
                'ktc95': [round(lo, 3), round(hi, 3)] if vals else None}
    return {'tat_ca_resolved': stats(resolved),
            'F4_dong_bang': stats(f4),
            'ty_le_giu': round(len(f4)/len(resolved), 4) if resolved else None,
            'nguong_F4': F4,
            'ghichu': 'P3 PASS khi KTC duoi cua F4 gross > 0'}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--since', type=float, default=0.0, help='epoch gioi han probe/ledger (0 = tat ca)')
    ap.add_argument('--grace', type=float, default=8.0)
    a = ap.parse_args()

    ledger = load_ledger(ROOT/'data/testnet-settled-v2.json')
    journal = load_events(ROOT/'data/testnet-journal.sqlite')
    probes = load_probes(ROOT/'data/testnet-signal-lab-v2.jsonl')

    net_bps = [r['net_return']*1e4 for r in ledger]
    lo, hi = ktc95(net_bps)
    report_path = ROOT/'data/testnet-mission-report.json'
    mr = json.loads(report_path.read_text(encoding='utf-8')) if report_path.exists() else {}

    out = {
        'ledger': {'n': len(net_bps), 'mean_net_bps': round(sum(net_bps)/len(net_bps), 3),
                   'ktc95': [round(lo, 3), round(hi, 3)],
                   'theo_origin': {o: round(sum(r['net_return'] for r in ledger if r.get('origin', 'live') == o)
                                            / max(1, sum(1 for r in ledger if r.get('origin', 'live') == o))*1e4, 3)
                                   for o in {r.get('origin', 'live') for r in ledger}}},
        'mission_report_gan_nhat': {k: mr.get(k) for k in
                                    ('collected_this_session', 'session_pnl_usdt', 'maker_exits', 'edge_verdict')},
        'P2_p_fill': p2_maker_fill(journal, a.since, a.grace),
        'P1_adverse_selection': p1_adverse_selection(ledger, journal, probes, a.since),
        'P3_F4': p3_frozen_filter(probes, a.since),
    }
    print(json.dumps(out, indent=1, ensure_ascii=False))


if __name__ == '__main__':
    sys.exit(main())
