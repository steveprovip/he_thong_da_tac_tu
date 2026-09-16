"""Event study: funding capture ngan han (Astra T2).

Y tuong: vao 60 phut truoc print, giu qua print, thoat print+2phut.
Thu nhap = funding rate ap dung tai print (rate am -> LONG nhan).
Chi phi = 6 bps roundtrip (MEASURED) + spread + slippage uoc luong.
PnL gia = diem * (exit/entry - 1) * 1e4 bps (dung kline 1m thuc).

GO/NO-GO: mean PnL net > 0 va KTC95 duoi > 0 tren tap print |rate|>=30bps.
Chi doc API public, khong key, khong lenh.
"""
import json
import math
import random
import statistics as st
import sys
import time
import urllib.request

SYMBOL = sys.argv[1] if len(sys.argv) > 1 else 'VTHOUSDT'
DAYS = int(sys.argv[2]) if len(sys.argv) > 2 else 45
MIN_RATE_BPS = 30.0
FEE_BPS = 6.0
ENTRY_LEAD_S = 3600      # vao 60 phut truoc print
EXIT_DELAY_S = 120       # thoat 2 phut sau print
BASE = 'https://fapi.binance.com'


def get(url, retries=3):
    for i in range(retries):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except Exception as exc:
            if i == retries - 1:
                raise
            time.sleep(1.5 * (i + 1))


def klines_1m(symbol, start_ms, end_ms):
    """Download 1m klines, pagination theo startTime."""
    out = []
    cur = start_ms
    while cur < end_ms:
        url = f'{BASE}/fapi/v1/klines?symbol={symbol}&interval=1m&startTime={cur}&endTime={end_ms}&limit=1500'
        batch = get(url)
        if not batch:
            break
        out.extend(batch)
        cur = batch[-1][6] + 1
        if len(batch) < 1500:
            break
        time.sleep(0.15)
    return out


def main():
    now_ms = int(time.time() * 1000)
    start_ms = now_ms - DAYS * 86400 * 1000

    print(f'Tai funding history {SYMBOL} ({DAYS} ngay)...')
    funding = get(f'{BASE}/fapi/v1/fundingRate?symbol={SYMBOL}&startTime={start_ms}&endTime={now_ms}&limit=1000')
    print(f'  {len(funding)} funding prints')

    extreme = [f for f in funding if abs(float(f['fundingRate']) * 1e4) >= MIN_RATE_BPS]
    print(f'  |rate| >= {MIN_RATE_BPS} bps: {len(extreme)} prints (mou thu event-study)')
    if not extreme:
        print('  Khong co print cuc doan trong window -> NO-GO khong du mau. Chon symbol khac.')
        return 0
    t0 = min(int(f['fundingTime']) for f in extreme)
    t1 = max(int(f['fundingTime']) for f in extreme)
    print(f'Tai 1m klines tu {datetime_str(t0)} den {datetime_str(t1)}...')
    kl = klines_1m(SYMBOL, t0 - 2 * 3600 * 1000, t1 + 3600 * 1000)
    closes = {int(k[0]): float(k[4]) for k in kl}
    highs = {int(k[0]): float(k[2]) for k in kl}
    lows = {int(k[0]): float(k[3]) for k in kl}
    print(f'  {len(kl)} nen 1m')

    rows = []
    for f in extreme:
        ft = int(f['fundingTime'])
        rate_bps = float(f['fundingRate']) * 1e4
        side = 1 if rate_bps < 0 else -1          # rate am -> LONG nhan; duong -> SHORT nhan
        entry_t = ft - ENTRY_LEAD_S * 1000
        exit_t = ft + EXIT_DELAY_S * 1000
        e_ts = nearest(closes, entry_t)
        x_ts = nearest(closes, exit_t)
        if e_ts is None or x_ts is None:
            continue
        entry, exit_p = closes[e_ts], closes[x_ts]
        if entry <= 0:
            continue
        pnl_price_bps = side * (exit_p / entry - 1) * 1e4
        funding_income_bps = abs(rate_bps) if side == (1 if rate_bps < 0 else -1) else -abs(rate_bps)
        net = pnl_price_bps + funding_income_bps - FEE_BPS
        mae = side * (min(lows.get(t, entry) for t in [e_ts]) / entry - 1) * 1e4 if side == 1 else 0
        rows.append({'time': datetime_str(ft), 'rate': rate_bps, 'side': 'LONG' if side == 1 else 'SHORT',
                     'pnl_price': round(pnl_price_bps, 2), 'funding': round(funding_income_bps, 2),
                     'net': round(net, 2)})

    if len(rows) < 5:
        print(f'Chi {len(rows)} mau hop le — in chi tiet tung mau de xem thu:')
        for r in rows:
            print(f'  {r["time"]}: side {r["side"]} rate {r["rate"]:+.1f} pnl_gia {r["pnl_price"]:+.1f} '
                  f'funding +{r["funding"]:.1f} NET {r["net"]:+.1f} bps')
        print('-> Qua it mau de ket luan thong ke. Nhung day la bang chung PnL THAT dau tien.')
        return 0
    nets = [r['net'] for r in rows]
    lo, hi = bootstrap_ci(nets)
    wins = sum(1 for n in nets if n > 0)
    print(f'\n{"time":20}{"side":7}{"rate":>9}{"pnl_gia":>10}{"funding":>9}{"NET":>9}')
    for r in rows:
        print(f'{r["time"]:20}{r["side"]:7}{r["rate"]:+9.1f}{r["pnl_price"]:+10.1f}{r["funding"]:+9.1f}{r["net"]:+9.1f}')
    print(f'\nN={len(rows)}  mean net {st.mean(nets):+.2f} bps  KTC95 [{lo:+.2f}, {hi:+.2f}]')
    print(f'Win rate: {wins}/{len(rows)} = {100*wins/len(rows):.0f}%')
    print(f'Verdict: {"GO nghien cuu tiep (KTC duoi >0)" if lo > 0 else "NO-GO (KTC duoi <=0) — huong capture ngan han khong chung minh duoc sau phi"}')
    return 0


def nearest(d, ts):
    if ts in d:
        return ts
    for off in (60_000, 120_000, 180_000):
        if ts + off in d:
            return ts + off
        if ts - off in d:
            return ts - off
    return None


def bootstrap_ci(vals, draws=3000, seed=7):
    if len(vals) < 2:
        return vals[0], vals[0]
    rng = random.Random(seed)
    means = sorted(st.fmean(rng.choices(vals, k=len(vals))) for _ in range(draws))
    return means[int(.025 * draws)], means[int(.975 * draws) - 1]


def datetime_str(ms):
    return time.strftime('%m-%d %H:%M', time.gmtime(ms / 1000))


if __name__ == '__main__':
    raise SystemExit(main())