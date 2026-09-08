"""So sánh gross distribution backtest vs live (Option 3 — tinh_hoa_ngoai.md).

Bối cảnh blocker A4: bookDepth per-percent của Binance không tái tạo được
L2, nên backtest công khai sai spread/ATR và không đo được fill rate. THAY
ĐỔI CÁCH DÙNG: backtest chỉ được so GROSS DISTRIBUTION (phân vị, theo
F4/non-F4), không được dùng để suy fill haysettled outcome. Script này là
bảng so sánh duy nhất được phép trích từ backtest công khai.

Chạy:
  python phan_tich/so_sanh_gross.py --live data/testnet-signal-lab-v2.jsonl ^
      --backtest data/npz/SOL05-p3-lab.jsonl data/npz/SOL06-p3-lab.jsonl

Ngưỡng F4 đóng băng v1: conv>=0.3575 + 6<=ATR<20 + spread>=2.
KTC95 bootstrap seed cố định — cùng dữ liệu cho cùng khoảng tin cậy.
"""
import argparse
import random
from pathlib import Path


def load(path):
    rows = []
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    rows.append(__import__('json').loads(line))
                except ValueError:
                    continue
    return [r for r in rows if r.get('outcome')]


def f4(r):
    return ((r.get('conviction') or 0) >= 0.3575
            and 6 <= (r.get('atr_bps') if r.get('atr_bps') is not None else -1) < 20
            and (r.get('spread_bps') or 0) >= 2)


def ci(values, n=2000, seed=7):
    if len(values) < 2:
        return None
    rng = random.Random(seed)
    means = sorted(sum(vals := [values[rng.randrange(len(values))] for _ in values]) / len(vals)
                   for _ in range(n))
    return sum(values) / len(values), means[int(.025 * n)], means[int(.975 * n)]


def pct(values, ps=(10, 25, 50, 75, 90)):
    if not values:
        return {}
    s = sorted(values)
    return {p: s[min(len(s) - 1, int(p / 100 * len(s)))] for p in ps}


def describe(label, rows):
    gross = [r.get('gross_bps') or 0.0 for r in rows]
    if not gross:
        print(f'{label:<24} n=0 (bỏ qua)')
        return
    interval = ci(gross)
    box = ' '.join(f'p{p}={v:+.2f}' for p, v in pct(gross).items())
    line = f'{label:<24} n={len(gross):<6} mean={sum(gross)/len(gross):+.3f}'
    line += f'  KTC95=[{interval[1]:.2f},{interval[2]:.2f}]' if interval else ''
    print(f'{line}\n{"":24} {box}')


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--live', default=r'F:/he_thong_da_tac_tu_2/data/testnet-signal-lab-v2.jsonl')
    parser.add_argument('--backtest', nargs='+', required=True)
    args = parser.parse_args()

    live = load(args.live)
    back = [r for path in args.backtest for r in load(path)]
    print(f'LIVE: {len(live)} probe resolved · BACKTEST: {len(back)} probe resolved\n')
    print('GROSS DISTRIBUTION — nhóm F4 (ngưỡng đóng băng v1):')
    for label, rows in (('live F4', [r for r in live if f4(r)]),
                        ('backtest F4', [r for r in back if f4(r)]),
                        ('live non-F4', [r for r in live if not f4(r)]),
                        ('backtest non-F4', [r for r in back if not f4(r)])):
        describe(label, rows)
    print('\nGROSS DISTRIBUTION — theo outcome:')
    for label, rows in (('live TP/SL/TIMEOUT', live), ('backtest TP/SL/TIMEOUT', back)):
        by = {}
        for r in rows:
            by.setdefault(r.get('outcome'), []).append(r.get('gross_bps') or 0.0)
        parts = ' · '.join(f'{k}: n={len(v)} mean={sum(v)/len(v):+.2f}' for k, v in sorted(by.items()))
        print(f'{label:<24} {parts}')
    print('\nLƯU Ý: backtest spread/ATR sai lệch do bookDepth per-percent — chỉ so')
    print('hình dạng phân phối gross, KHÔNG so tuyệt đối và KHÔNG suy fill/size.')


if __name__ == '__main__':
    main()
