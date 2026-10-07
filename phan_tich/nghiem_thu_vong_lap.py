"""Nghiem thu TRUOC vs SAU khi bat early-stop + permit. Chi doc, khong mang, khong lenh.

    python phan_tich/nghiem_thu_vong_lap.py            (sau phan_tich/markout_l2.py)

TRUOC = hanh vi cu: thu data den Ctrl+C, trade moi tin hieu qua cong / loc noi.
SAU   = core.early_stop.EarlyStopGate (200 tin hieu, 95%) + core.regime_permit.
Chi phi all-in: core.early_stop.COST_ALL_IN_BPS = 8.5 bps.
"""
import json
import statistics as st
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.early_stop import COST_ALL_IN_BPS, GROSS_EDGE_HURDLE_BPS, directory_bytes, replay  # noqa: E402
from core.regime_permit import fast_hands_permit  # noqa: E402
from phan_tich.nghen_edge import load_lab  # noqa: E402

P60 = 0.3575   # conviction p60 dong bang tu v1 (nghen_edge MUC 5)


def lab_section():
    rows = sorted(load_lab(), key=lambda r: r['opened'])
    net = [r['gross_bps'] - COST_ALL_IN_BPS for r in rows]
    idx, verdict = replay(net)
    idx = idx or len(rows)
    oos = [r for r in rows if r['src'] == 'v2']
    f4 = [r for r in oos if r['conviction'] >= P60 and 6 <= r['atr_bps'] < 20 and r['spread_bps'] >= 2]
    gated = [r for r in rows if r['gated']]
    first = rows[:idx]
    g_mean = st.fmean(r['gross_bps'] for r in first)
    permit = fast_hands_permit(4.0, 10.0, oos_gross_bps=g_mean,
                               oos_gross_lcb_bps=verdict.lcb_bps + COST_ALL_IN_BPS,
                               lab_status=verdict.status)
    span_h = (rows[-1]['opened'] - rows[0]['opened']) / 3600
    stop_h = (rows[idx - 1]['opened'] - rows[0]['opened']) / 3600
    return {
        'probes_before': len(rows), 'probes_after': idx,
        'junk_reduction_pct': round(100 * (1 - idx / len(rows)), 1),
        'collection_hours_before': round(span_h, 1), 'collection_hours_after': round(stop_h, 2),
        'verdict': verdict.as_dict(), 'permit': permit.as_dict(),
        'before_trade_gated_net_bps': round(st.fmean(r['gross_bps'] for r in gated) - COST_ALL_IN_BPS, 2),
        'before_trade_gated_n': len(gated),
        'before_loosened_F4_oos_net_bps': round(st.fmean(r['gross_bps'] for r in f4) - COST_ALL_IN_BPS, 2),
        'before_loosened_F4_oos_n': len(f4),
        'after_trades': 0 if not permit.allowed else None, 'after_net_bps': 0.0 if not permit.allowed else None,
    }


def live_section():
    recs = sorted(json.loads((ROOT / 'data' / 'testnet-settled-v2.json').read_text())['records'],
                  key=lambda r: r.get('closed_at', 0))
    net_bps = [r['net_return'] * 1e4 for r in recs]
    usdt = [r['net_pnl_usdt'] for r in recs]
    idx, verdict = replay(net_bps, min_samples=30)
    return {'fills_before': len(recs), 'net_usdt_before': round(sum(usdt), 4),
            'net_bps_before': round(st.fmean(net_bps), 2),
            'early_stop_min30_after_n': idx, 'net_usdt_if_stopped_at_n': round(sum(usdt[:idx]), 4),
            'loss_avoided_pct': round(100 * (1 - sum(usdt[:idx]) / sum(usdt)), 1),
            'verdict': verdict.as_dict(),
            'permit_after': 'STAND_ASIDE (no OOS gross >= 9 bps) -> 0 fills, 0 USDT'}


def l2_section():
    arts = sorted((ROOT / 'artifacts' / 'markout_l2').glob('*.json'))
    agg = {}
    wall = {}
    for p in arts:
        r = json.loads(p.read_text())
        day = r['file'].split('/')[1][:8]
        split = 'TRAIN' if day == '20260908' else 'OOS'
        wall[day] = max(wall.get(day, 0), r['touch']['hours'])
        for pol in ('touch', 'guard'):
            x = r[pol]
            a = agg.setdefault(f'{split}_{pol}', {'quotes': 0, 'fills': 0, 'm1': 0., 'm5': 0., 'm30': 0.})
            a['quotes'] += x['quotes']
            a['fills'] += x['fills']
            for h in (1, 5, 30):
                a[f'm{h}'] += x.get(f'markout_{h}s_bps', 0.) * x['fills']
    out = {}
    for k, a in agg.items():
        f = a['fills'] or 1
        out[k] = {'quotes': a['quotes'], 'fills': a['fills'],
                  'fill_rate_pct': round(100 * a['fills'] / max(a['quotes'], 1), 1),
                  'markout_1s': round(a['m1'] / f, 2), 'markout_5s': round(a['m5'] / f, 2),
                  'markout_30s': round(a['m30'] / f, 2),
                  'net_after_cost_bps': round(a['m30'] / f - COST_ALL_IN_BPS, 2),
                  'passes_9bps_hurdle': a['m30'] / f >= GROSS_EDGE_HURDLE_BPS}
    total_bytes = directory_bytes(str(ROOT / 'data' / 'l2'))
    train = agg.get('TRAIN_touch', {'fills': 0})
    rate_per_h = train['fills'] / wall.get('20260908', 1)
    hours_to_200 = 200 / rate_per_h if rate_per_h else float('nan')
    wall_total = sum(wall.values())
    needed = total_bytes * hours_to_200 / wall_total if wall_total else 0
    out['data'] = {'l2_bytes_on_disk': total_bytes, 'l2_wall_hours': round(wall_total, 2),
                   'fills_per_hour_4_symbols': round(rate_per_h, 1),
                   'hours_to_200_fills': round(hours_to_200, 2),
                   'bytes_needed_for_verdict_est': int(needed),
                   'junk_reduction_pct_est': round(100 * (1 - needed / total_bytes), 1) if total_bytes else None}
    return out


def main():
    report = {'cost_all_in_bps': COST_ALL_IN_BPS, 'gross_hurdle_bps': GROSS_EDGE_HURDLE_BPS,
              'signal_lab': lab_section(), 'live_testnet': live_section(), 'l2_maker': l2_section()}
    dest = ROOT / 'artifacts' / 'nghiem_thu_vong_lap.json'
    dest.write_text(json.dumps(report, indent=1, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, indent=1, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
