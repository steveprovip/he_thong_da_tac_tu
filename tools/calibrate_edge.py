"""Turn recorded probes into a verdict about whether the signal has edge.

Reads the signal lab's JSONL and answers, with confidence intervals rather than
assertions:
  * what fraction of signals reach take-profit before stop, against the
    break-even rate the barrier geometry demands;
  * whether realized net edge is distinguishable from zero at all;
  * whether ZEPHR's edge/cost gate actually selects better trades than it rejects;
  * what edge multiple, if any, the data would justify.

If the answer is "not enough data" it says so. That is the expected answer for the
first few hundred probes, and it is more useful than a number.
"""
import argparse
import json
import math
from pathlib import Path
import random
import statistics
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from config.settings import ROOT

MIN_SAMPLES = 200


def load(path):
    rows = []
    with open(path, encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return [r for r in rows if r.get('outcome')]


def bootstrap_ci(values, draws=4000, alpha=.05, seed=17):
    """Percentile bootstrap on the mean; no distributional assumption."""
    if len(values) < 2:
        return (float('nan'), float('nan'))
    rng = random.Random(seed)
    n = len(values)
    means = sorted(statistics.fmean(rng.choices(values, k=n)) for _ in range(draws))
    return means[int(alpha / 2 * draws)], means[int((1 - alpha / 2) * draws) - 1]


def verdict(values, label, width=34):
    if not values:
        print(f'  {label:<{width}} no samples')
        return None
    mean = statistics.fmean(values)
    low, high = bootstrap_ci(values)
    if len(values) < 2:
        mark = 'n<2'
    elif low > 0:
        mark = 'POSITIVE'
    elif high < 0:
        mark = 'NEGATIVE'
    else:
        mark = 'indistinguishable from zero'
    print(f'  {label:<{width}} n={len(values):>5}  mean={mean:+7.2f} bps  '
          f'95% CI [{low:+7.2f}, {high:+7.2f}]  {mark}')
    return mean, low, high


def main(path, tp_atr, sl_atr, min_samples):
    rows = load(path)
    if not rows:
        print(f'No resolved probes in {path}. Run the engine for a while first.')
        return 1
    span = max(r['closed'] for r in rows) - min(r['opened'] for r in rows)
    print(f'SIGNAL LAB CALIBRATION · {len(rows)} resolved probes over {span/3600:.2f} h\n')

    print('1 / BARRIER OUTCOMES')
    counts = {k: sum(r['outcome'] == k for r in rows) for k in ('TP', 'SL', 'TIMEOUT')}
    for key, value in counts.items():
        print(f'  {key:<10} {value:>6}  {value/len(rows)*100:>5.1f}%')
    decided = counts['TP'] + counts['SL']
    breakeven = sl_atr / (tp_atr + sl_atr)
    if decided:
        p = counts['TP'] / decided
        # Wilson interval: honest at small n and near the boundaries.
        z = 1.96
        centre = (p + z*z/(2*decided)) / (1 + z*z/decided)
        margin = z*math.sqrt(p*(1-p)/decided + z*z/(4*decided*decided)) / (1 + z*z/decided)
        print(f'\n  p(TP before SL)  = {p:.3f}  95% CI [{centre-margin:.3f}, {centre+margin:.3f}]  (n={decided})')
        print(f'  break-even p     = {breakeven:.3f}   (TP {tp_atr}·ATR vs SL {sl_atr}·ATR)')
        if centre - margin > breakeven:
            print('  -> hit rate clears break-even before costs')
        elif centre + margin < breakeven:
            print('  -> hit rate is below break-even; the signal loses on geometry alone')
        else:
            print('  -> hit rate is not distinguishable from break-even')

    print('\n2 / REALIZED NET EDGE (costs modelled, entry fills assumed)')
    verdict([r['net_bps'] for r in rows], 'all probes')
    verdict([r['net_bps'] for r in rows if r['gated']], 'ZEPHR/RUNE would have allowed')
    verdict([r['net_bps'] for r in rows if not r['gated']], 'ZEPHR/RUNE blocked')

    print('\n3 / DOES THE GATE SEPARATE?')
    allowed = [r['net_bps'] for r in rows if r['gated']]
    blocked = [r['net_bps'] for r in rows if not r['gated']]
    if min(len(allowed), len(blocked)) < 30:
        print('  not enough of both classes yet (need >=30 each)')
    else:
        gap = statistics.fmean(allowed) - statistics.fmean(blocked)
        low, high = bootstrap_ci(allowed)
        blow, bhigh = bootstrap_ci(blocked)
        print(f'  allowed - blocked = {gap:+.2f} bps')
        print(f'  allowed CI [{low:+.2f}, {high:+.2f}] · blocked CI [{blow:+.2f}, {bhigh:+.2f}]')
        print('  -> gate adds value' if low > bhigh else
              '  -> gate does NOT separate: overlapping intervals, no evidence it selects better trades')

    print('\n4 / CONVICTION DECILES (is confidence informative?)')
    ordered = sorted(rows, key=lambda r: r['conviction'])
    buckets = max(1, min(10, len(ordered) // 30))
    size = len(ordered) // buckets if buckets else len(ordered)
    if size < 20:
        print('  too few probes to bucket meaningfully')
    else:
        print(f"  {'conviction':<22} {'n':>5} {'TP%':>6} {'net bps':>9}")
        for i in range(buckets):
            chunk = ordered[i*size:(i+1)*size] if i < buckets-1 else ordered[i*size:]
            lo, hi = chunk[0]['conviction'], chunk[-1]['conviction']
            tp = sum(c['outcome'] == 'TP' for c in chunk) / len(chunk) * 100
            print(f'  {lo:.3f} - {hi:.3f}{"":<8} {len(chunk):>5} {tp:>5.1f}% '
                  f'{statistics.fmean(c["net_bps"] for c in chunk):>+8.2f}')

    print('\n5 / WHAT EDGE MULTIPLE WOULD THE DATA JUSTIFY?')
    print(f"  {'multiple':>9} {'survivors':>10} {'mean net bps':>13} {'95% CI':>26}")
    for multiple in (0.0, 0.5, 1.0, 1.5, 1.8, 2.5, 4.0):
        kept = [r['net_bps'] for r in rows if r['edge_bps'] >= multiple * r['cost_bps']]
        if len(kept) < 20:
            print(f'  {multiple:>9.1f} {len(kept):>10} {"":>13} too few to judge')
            continue
        low, high = bootstrap_ci(kept)
        print(f'  {multiple:>9.1f} {len(kept):>10} {statistics.fmean(kept):>+12.2f} '
              f'  [{low:+7.2f}, {high:+7.2f}]')

    print('\n6 / READING THIS')
    if len(rows) < min_samples:
        print(f'  {len(rows)} probes is below the {min_samples} needed for a stable read.')
        print('  Treat every number above as provisional and keep collecting.')
    else:
        print('  Sample size is adequate. Loosen a gate only where an interval sits')
        print('  entirely above zero -- a positive mean with a straddling interval is noise.')
    print('  Entry fills are assumed, so these figures flatter the strategy: a gate that')
    print('  looks break-even here is losing money in reality.')
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Calibrate the edge model from recorded probes')
    parser.add_argument('--mode', default='testnet', choices=['paper', 'testnet', 'mainnet'])
    parser.add_argument('--path')
    parser.add_argument('--tp-atr', type=float, default=2.5)
    parser.add_argument('--sl-atr', type=float, default=1.5)
    parser.add_argument('--min-samples', type=int, default=MIN_SAMPLES)
    args = parser.parse_args()
    target = Path(args.path) if args.path else ROOT/'data'/f'{args.mode}-signal-lab.jsonl'
    if not target.exists():
        print(f'No lab file at {target}. Run the engine without --no-lab first.')
        raise SystemExit(1)
    raise SystemExit(main(target, args.tp_atr, args.sl_atr, args.min_samples))
