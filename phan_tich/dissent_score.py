"""Dissent score + per-agent equity curves cho GrokTOPUS probes.

Tinh hoa TradingAgents, phiên bản đo được cho HFT (phan_tich/tinh_hoa_ngoai.md).

Hai lớp:
- GATE DISSENT (schema hiện tại, đo được ngay): headline consensus được
  ghép 60% alpha-conviction + 40% gate. Khoảng `dissent = consensus -
  0.615*conviction` phản ánh mức các gate nghi ngờ hướng alpha. Bucket
  dissent -> gross bps + KTC95 bootstrap: nếu các gate "nghi ngờ đúng",
  probe bị gate ghét phải gross thấp hơn.
- ALPHA DISSENT (schema tương lai): nếu probe có trường alpha_votes
  (3 phiếu NORO/LUMEN/TIDAL kèm giá trị [-1,1]), tính entropy chuẩn hóa
  + equity-curve từng agent. Script tự nhận diện; không có thì báo rõ.

Chỉ đọc. Không đụng core/ hay live/. Tham số weights phải khớp
consensus_engine.py:12-13 — nếu code đổi, đổi hằng số dưới đây.
"""
import itertools
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

ALPHA_WEIGHT_SUM = 0.60   # consensus_engine.py:12-13 — NORO+LUMEN+TIDAL
DEFAULT_PATH = Path(r'F:/he_thong_da_tac_tu_2/data/testnet-signal-lab-v2.jsonl')


def load_probes(path):
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                try:
                    yield json.loads(line)
                except json.JSONDecodeError:
                    continue


def bootstrap_ci(values, n=2000, seed=7, alpha=.05):
    """Percentile bootstrap; seed cố định để cùng dữ liệu -> cùng quyền."""
    if len(values) < 2:
        return None
    import random
    rng = random.Random(seed)
    means = []
    for _ in range(n):
        sample = [values[rng.randrange(len(values))] for _ in range(len(values))]
        means.append(sum(sample) / len(sample))
    means.sort()
    lo = means[int(alpha / 2 * n)]
    hi = means[int((1 - alpha / 2) * n)]
    return sum(values) / len(values), lo, hi


def main(path=DEFAULT_PATH):
    rows = [r for r in load_probes(path) if r.get('outcome')]
    if not rows:
        print('Không có probe resolved.')
        return 1
    has_votes = all('alpha_votes' in r for r in rows[:50]) and any(r.get('alpha_votes') for r in rows)
    print(f'probes resolved: {len(rows)} · alpha_votes schema: {has_votes}')

    # --- Lớp 1: gate dissent (đo được ngay) --------------------------------
    buckets = defaultdict(list)
    for r in rows:
        conviction = r.get('conviction') or 0.0
        consensus = r.get('consensus') or 0.0
        dissent = consensus - ALPHA_WEIGHT_SUM * conviction
        # dissent ∈ [0, 0.4] = phần đóng góp của gate vào headline.
        # <0.15 = gate gần như đồng thuận; >0.30 = gate mạnh mẽ giữ lại.
        band = 'thap<.15' if dissent < .15 else 'giua' if dissent <= .30 else 'cao>.30'
        buckets[band].append(r.get('gross_bps') or 0.0)
    print('\nGATE DISSENT (consensus − 0.60·conviction, ∈[0,.4]) vs gross:')
    print(f"{'bucket':<10}{'n':>7}{'gross mean':>12}{'KTC95':>24}")
    for band in ('thap<.15', 'giua', 'cao>.30'):
        vals = buckets.get(band, [])
        ci = bootstrap_ci(vals)
        if ci is None:
            print(f'{band:<10}{len(vals):>7}  (thiếu mẫu)')
        else:
            mean, lo, hi = ci
            print(f'{band:<10}{len(vals):>7}{mean:>12.3f}   [{lo:.3f}, {hi:.3f}]')

    # Tương tác với bộ lọc F4 (conv≥0.3575 + 6≤ATR<20 + spread≥2 — ngưỡng đóng băng v1)
    f4 = [r for r in rows if (r.get('conviction') or 0) >= 0.3575
          and 6 <= (r.get('atr_bps') or -1) < 20 and (r.get('spread_bps') or 0) >= 2]
    f4_gross = [r.get('gross_bps') or 0.0 for r in f4]
    ci = bootstrap_ci(f4_gross)
    print(f'\nF4 (đóng băng v1): n={len(f4)}', end=' ')
    if ci:
        print(f'gross {ci[0]:.3f} bps [{ci[1]:.3f}, {ci[2]:.3f}]')

    # --- Lớp 2: alpha dissent + equity-curve từng agent (schema mới) -------
    if not has_votes:
        print('\nALPHA DISSENT: CHƯA ĐO ĐƯỢC — probe hiện tại không ghi phiếu '
              'từng agent (chỉ conviction tổng). Khi runtime bắt đầu ghi '
              'alpha_votes vào probe (schema 3), script này tự bật phần dưới.')
        return 0
    agent_pnl = defaultdict(list)
    entropies = []
    for r in rows:
        votes = r.get('alpha_votes') or {}
        vals = [float(v) for v in votes.values() if isinstance(v, (int, float))]
        if len(vals) >= 3:
            sides = ['BUY' if v > .05 else 'SELL' if v < -.05 else 'HOLD' for v in vals]
            props = [sides.count(s) / len(sides) for s in ('BUY', 'SELL', 'HOLD')]
            ent = -sum(p * math.log(p) for p in props if p > 0) / math.log(3)
            entropies.append((ent, r.get('gross_bps') or 0.0))
        for name, v in votes.items():
            if isinstance(v, (int, float)) and abs(v) > .05:
                agent_pnl[name].append(math.copysign(r.get('gross_bps') or 0.0, v))
    if entropies:
        entropies.sort()
        third = len(entropies) // 3
        low = [g for _, g in entropies[:third]]
        high = [g for _, g in entropies[-third:]]
        for label, vals in (('entropy THẤP (đồng lòng)', low), ('entropy CAO (bất đồng)', high)):
            ci = bootstrap_ci(vals)
            print(f'{label}: n={len(vals)}', end=' ')
            if ci:
                print(f'gross {ci[0]:.3f} [{ci[1]:.3f}, {ci[2]:.3f}]')
    print('\nEQUITY-CURVE từng agent (sign-corrected gross bps):')
    for name, vals in sorted(agent_pnl.items()):
        ci = bootstrap_ci(vals)
        verdict = 'chưa đủ mẫu' if ci is None or ci[1] <= 0 <= ci[2] else ('DƯƠNG' if ci[1] > 0 else 'ÂM')
        print(f'  {name:<8} n={len(vals):<5} mean={sum(vals)/len(vals):.3f}', end=' ')
        print(f'KTC95=[{ci[1]:.3f}, {ci[2]:.3f}] verdict={verdict}' if ci else '')
    return 0


if __name__ == '__main__':
    path = Path(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_PATH
    sys.exit(main(path))
