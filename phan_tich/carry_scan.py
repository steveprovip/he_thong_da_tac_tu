"""carry_scan.py — scan dinh ky phat hien coin dat chuan R1 (He-Manela-Ross carry).

Quyet dinh 10/09: DUNG NGOAI. Scan nay chi DO tan suat co hoi, khong tu dong mo lenh.
Chay moi 15 phut trong 7 ngay (den 17/09); sau do tam gac neu khong co ung vien.

Chuan R1: |rate|>=30 bps/8h + vol24h>=50M + spread<=3bps + tuoi>=30 ngay.
Journal: data/carry-scan/carry-scan.jsonl (1 dong/luot, snapshot dong bo).
Bao chi khi: ung vien xuat hien 2 luot lien tiep, hoac bat dau/ kết thuc 7 ngay.

Chay:  python phan_tich/carry_scan.py            (1 luot scan)
       python phan_tich/carry_scan.py --loop     (dinh ky 15 phut, 7 ngay)
"""
import argparse
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCAN_DIR = ROOT / 'data' / 'carry-scan'
SCAN_FILE = SCAN_DIR / 'carry-scan.jsonl'
TTL = 15

MIN_RATE_BPS = 30.0
MIN_VOL_USDT = 50e6
MAX_SPREAD_BPS = 3.0
MIN_AGE_DAYS = 30.0
FEE_BPS = 6.0
SCAN_INTERVAL_S = 15 * 60
SCAN_DAYS = 7
# Futility stop: 96 empty scans = 24h with no R1 candidate. The Wilson 95%
# upper bound on P(candidate per scan) is then < 3.9%, i.e. under one hit a
# day -- polling every 15 minutes for 7 days cannot change that answer.
MAX_EMPTY_SCANS = 96


def futility_stop(empty_streak, max_empty=MAX_EMPTY_SCANS):
    """(stop, wilson_ucb) after `empty_streak` consecutive zero-candidate scans."""
    import sys as _sys
    from pathlib import Path as _P
    _sys.path.insert(0, str(_P(__file__).resolve().parents[1]))
    from core.early_stop import wilson_interval
    ucb = wilson_interval(0, empty_streak)[1] if empty_streak else 1.0
    return bool(max_empty) and empty_streak >= max_empty, ucb


def get(url):
    with urllib.request.urlopen(url, timeout=TTL) as r:
        return json.loads(r.read())


def scan_once(now=None):
    now = now if now is not None else time.time()
    prem = get('https://fapi.binance.com/fapi/v1/premiumIndex')
    tick = get('https://fapi.binance.com/fapi/v1/ticker/24hr')
    books = get('https://fapi.binance.com/fapi/v1/ticker/bookTicker')
    info = get('https://fapi.binance.com/fapi/v1/exchangeInfo')
    onboard = {s['symbol']: s.get('onboardDate', 0) / 1000 for s in info['symbols']}
    tickmap = {x['symbol']: x for x in tick}
    bookmap = {x['symbol']: x for x in books}
    counters = {'rate30': 0, 'vol': 0, 'spread': 0, 'age': 0}
    candidates = []
    for p in prem:
        sym = p['symbol']
        rate = float(p.get('lastFundingRate', 0) or 0) * 1e4
        if abs(rate) < MIN_RATE_BPS:
            continue
        counters['rate30'] += 1
        t = tickmap.get(sym)
        vol = float(t.get('quoteVolume', 0)) if t else 0.0
        if vol < MIN_VOL_USDT:
            continue
        counters['vol'] += 1
        b = bookmap.get(sym)
        if not b:
            continue
        bid, ask = float(b['bidPrice']), float(b['askPrice'])
        if bid <= 0 or ask <= 0:
            continue
        spread = (ask - bid) / ((ask + bid) / 2) * 1e4
        if spread > MAX_SPREAD_BPS:
            continue
        counters['spread'] += 1
        age = (now - onboard.get(sym, now)) / 86400
        if age < MIN_AGE_DAYS:
            continue
        counters['age'] += 1
        mark, idx = float(p['markPrice']), float(p['indexPrice'])
        candidates.append({
            'symbol': sym,
            'rate_bps': round(rate, 2),
            'net_bps': round(abs(rate) - FEE_BPS, 2),
            'side': 'SHORT' if rate > 0 else 'LONG',
            'vol24h_usdt': round(vol, 0),
            'spread_bps': round(spread, 3),
            'age_days': round(age, 1),
            'gap_mark_idx_pct': round((mark / idx - 1) * 100, 3) if idx > 0 else None,
            'next_funding_ts': p.get('nextFundingTime'),
            'interval': '8h',
        })
    snapshot = {
        'utc': datetime.now(timezone.utc).isoformat(),
        'ts': now,
        'env': 'mainnet-public',
        'thresholds': {'rate_bps': MIN_RATE_BPS, 'vol_usdt': MIN_VOL_USDT,
                       'spread_bps': MAX_SPREAD_BPS, 'age_days': MIN_AGE_DAYS},
        'counters': counters,
        'n_universe': len(prem),
        'n_candidates': len(candidates),
        'candidates': candidates,
    }
    return snapshot, candidates


def append_journal(snapshot):
    SCAN_DIR.mkdir(parents=True, exist_ok=True)
    with SCAN_FILE.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(snapshot, ensure_ascii=False, separators=(',', ':')) + '\n')


def read_prev_states():
    if not SCAN_FILE.exists():
        return [], []
    lines = SCAN_FILE.read_text(encoding='utf-8').splitlines()[-2:]
    states = []
    for line in lines:
        try:
            states.append(json.loads(line).get('n_candidates', 0))
        except json.JSONDecodeError:
            pass
    while len(states) < 2:
        states.insert(0, None)
    return states[0], states[1]


def notify_if_needed(snapshot, prev1, prev2):
    n = snapshot['n_candidates']
    messages = []
    if n > 0 and prev1 == 0 and (prev2 in (0, None)):
        names = ', '.join(c['symbol'] + ' ' + str(c['rate_bps']) + 'bps'
                          for c in snapshot['candidates'])
        messages.append(f'UNG VIEN MOI xuat hien {n} coin (2 luot lien tiep): {names}')
    if prev1 is None:
        messages.append('Bat dau scan dinh ky carry 7 ngay. Journal: ' + str(SCAN_FILE))
    return messages


def loop(hours=SCAN_DAYS * 24, max_empty=MAX_EMPTY_SCANS):
    deadline = time.time() + hours * 3600
    print(f'Carry scan moi {SCAN_INTERVAL_S//60} phut, tong {hours}h, '
          f'dung som sau {max_empty} luot rong lien tiep. Ctrl+C de dung.')
    empty = 0
    while time.time() < deadline:
        try:
            snapshot, _ = scan_once()
            empty = 0 if snapshot['n_candidates'] else empty + 1
            stop, ucb = futility_stop(empty, max_empty)
            if stop:
                append_journal(snapshot)
                print(f'  REGIME_UNPROFITABLE: {empty} luot rong lien tiep, Wilson UCB95 '
                      f'P(ung vien) {ucb:.3f}. Dung scan.', flush=True)
                return 0
            append_journal(snapshot)
            prev1, prev2 = read_prev_states()
            for msg in notify_if_needed(snapshot, prev1, prev2):
                print(f'  [{datetime.now().strftime("%H:%M:%S")}] {msg}', flush=True)
        except Exception as exc:
            print(f'  [{datetime.now().strftime("%H:%M:%S")}] LOI scan: '
                  f'{type(exc).__name__}: {str(exc)[:100]}', flush=True)
        time.sleep(SCAN_INTERVAL_S)
    print('Het 7 ngay. Tam gac scan.')


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--loop', action='store_true')
    ap.add_argument('--hours', type=float, default=SCAN_DAYS * 24)
    ap.add_argument('--max-empty-scans', type=int, default=MAX_EMPTY_SCANS)
    args = ap.parse_args()

    if args.loop:
        return loop(args.hours, args.max_empty_scans) or 0

    snapshot, cands = scan_once()
    append_journal(snapshot)
    print(f'UTC: {snapshot["utc"]}')
    print(f'Universe: {snapshot["n_universe"]} perp')
    print('Loc lien tiep: ' + ' -> '.join(f'{k}={v}' for k, v in snapshot['counters'].items()))
    print(f'Ung vien dat chuan R1: {len(cands)}')
    for c in cands:
        print(f"  {c['symbol']:14} rate {c['rate_bps']:+8.1f} net {c['net_bps']:+8.1f} bps "
              f"side {c['side']:5} vol {c['vol24h_usdt']/1e6:7.1f}M spread {c['spread_bps']:.2f} "
              f"age {c['age_days']:.0f}d gap {c['gap_mark_idx_pct']:+.2f}%")
    if not cands:
        print('  Khong co ung vien dat chuan R1. Giu trang thai DUNG NGOAI.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())