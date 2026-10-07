"""P1/P2 tren L2 that: adverse selection sau fill maker + fill rate, baseline vs guard.

    python phan_tich/markout_l2.py data/l2/SOLUSDT/20260908.jsonl [...] [--out artifacts/markout_l2]

Chi doc file local, khong mang, khong lenh. Moi `--gap` giay dat 1 cap quote
passive (BUY o bid, SELL o ask) song TTL `--ttl` giay. Fill bao thu (trade-through):
BUY @P khop khi bid(t') < P hoac ask(t') <= P trong TTL; SELL doi xung.
Markout h giay sau fill: side*(mid(t_f+h) - P)/P*1e4. Net = markout_mid(30s) - COST_ALL_IN.

Hai chinh sach so sanh tren CUNG luong book:
  touch  — quote o best bid/ask, khong dieu kien (hanh vi runtime hien tai, inventory=0).
  guard  — core.barrier_planner.microstructure_quote: reservation = microprice, bo quote
           khi imbalance chong lai phia minh (|I| > toxicity) — hanh vi moi.
"""
import argparse
import bisect
import json
import math
import re
import statistics as st
import sys
from array import array
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from core.barrier_planner import microstructure_quote  # noqa: E402
from core.early_stop import COST_ALL_IN_BPS  # noqa: E402

HORIZONS = (1, 5, 30)
PAT = re.compile(r'"lt":([0-9.]+).*?"b":"([0-9.]+)","B":"([0-9.]+)","a":"([0-9.]+)","A":"([0-9.]+)"')


def load(path):
    t, b, bq, a, aq = (array('d') for _ in range(5))
    with open(path, encoding='utf-8', errors='ignore') as fh:
        for line in fh:
            if '"bookTicker"' not in line:
                continue
            m = PAT.search(line)
            if not m:
                continue
            lt, bid, bqty, ask, aqty = map(float, m.groups())
            if bid <= 0 or ask <= bid:
                continue
            if t and lt < t[-1]:
                continue
            t.append(lt); b.append(bid); bq.append(bqty); a.append(ask); aq.append(aqty)
    return t, b, bq, a, aq


class _Top:
    __slots__ = ('bid', 'ask', 'bid_qty', 'ask_qty', 'mid')

    def __init__(self, bid, ask, bq, aq):
        self.bid, self.ask, self.bid_qty, self.ask_qty = bid, ask, bq, aq
        self.mid = (bid + ask) / 2


def simulate(series, tick, gap=20.0, ttl=5.0, toxicity=None, policy='touch'):
    t, b, bq, a, aq = series
    n = len(t)
    out = {'quotes': 0, 'skipped': 0, 'fills': 0, 'mk': {h: [] for h in HORIZONS}, 'imb': []}
    next_t = t[0] + 300.0
    i = 0
    while True:
        i = bisect.bisect_left(t, next_t, i)
        if i >= n:
            break
        next_t = t[i] + gap
        top = _Top(b[i], a[i], bq[i], aq[i])
        for side in ('BUY', 'SELL'):
            if policy == 'touch':
                price = top.bid if side == 'BUY' else top.ask
            else:
                price = microstructure_quote(top, side, tick, toxicity=toxicity)
                if price is None:
                    out['skipped'] += 1
                    continue
                price = float(price)
            out['quotes'] += 1
            j, end = i + 1, t[i] + ttl
            fill_j = None
            while j < n and t[j] <= end:
                if (side == 'BUY' and (b[j] < price or a[j] <= price)) or \
                   (side == 'SELL' and (a[j] > price or b[j] >= price)):
                    fill_j = j
                    break
                j += 1
            if fill_j is None:
                continue
            ok = True
            marks = {}
            for h in HORIZONS:
                k = bisect.bisect_left(t, t[fill_j] + h, fill_j)
                if k >= n:
                    ok = False
                    break
                mid = (b[k] + a[k]) / 2
                marks[h] = (1 if side == 'BUY' else -1) * (mid - price) / price * 1e4
            if not ok:
                continue
            out['fills'] += 1
            for h in HORIZONS:
                out['mk'][h].append(marks[h])
            out['imb'].append((bq[i] - aq[i]) / (bq[i] + aq[i]) * (1 if side == 'BUY' else -1))
    return out


def summarize(res, hours):
    fr = res['fills'] / res['quotes'] if res['quotes'] else 0.0
    row = {'quotes': res['quotes'], 'skipped': res['skipped'], 'fills': res['fills'],
           'fill_rate_pct': round(100 * fr, 2), 'hours': round(hours, 2)}
    for h in HORIZONS:
        v = res['mk'][h]
        if len(v) > 1:
            row[f'markout_{h}s_bps'] = round(st.fmean(v), 3)
            row[f'markout_{h}s_se'] = round(st.stdev(v) / math.sqrt(len(v)), 3)
    v = res['mk'][30]
    if v:
        row['net_30s_after_cost_bps'] = round(st.fmean(v) - COST_ALL_IN_BPS, 3)
        row['gross_ge_9bps_share_pct'] = round(100 * sum(x > 9 for x in v) / len(v), 2)
    return row


def infer_tick(series):
    b = series[1]
    diffs = sorted({round(abs(b[i + 1] - b[i]), 8) for i in range(min(len(b) - 1, 20000)) if b[i + 1] != b[i]})
    return diffs[0] if diffs else 1e-8


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument('files', nargs='+')
    ap.add_argument('--gap', type=float, default=20.0)
    ap.add_argument('--ttl', type=float, default=5.0)
    ap.add_argument('--toxicity', type=float, default=0.30)
    ap.add_argument('--out', default=str(ROOT / 'artifacts' / 'markout_l2'))
    args = ap.parse_args(argv)
    Path(args.out).mkdir(parents=True, exist_ok=True)
    for f in args.files:
        p = Path(f)
        series = load(p)
        if len(series[0]) < 1000:
            print(f'SKIP {p}: {len(series[0])} dong')
            continue
        hours = (series[0][-1] - series[0][0]) / 3600
        tick = infer_tick(series)
        result = {'file': f'{p.parent.name}/{p.name}', 'tick': tick, 'events': len(series[0]),
                  'cost_all_in_bps': COST_ALL_IN_BPS, 'toxicity': args.toxicity,
                  'touch': summarize(simulate(series, tick, args.gap, args.ttl, policy='touch'), hours),
                  'guard': summarize(simulate(series, tick, args.gap, args.ttl, args.toxicity, 'guard'), hours)}
        dest = Path(args.out) / f'{p.parent.name}_{p.stem}.json'
        dest.write_text(json.dumps(result, indent=1), encoding='utf-8')
        print(json.dumps(result))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
