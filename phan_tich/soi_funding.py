"""soi_funding.py — soi ba nhieu coin dat chuan R1 cross trong UNIVERSE 900.

Chuan cross: |rate| >= 30 bps/8h  i  quoteVol24h >= 50M USDT  i  spread <= 3 bps
             i  tuoi >= 30 ngay.  OI khong co trong premiumIndex -> bao 'OI n/a'.

Thuc te R4 tol: fund testnet xac minh (fapi/v1/income FUNDING_FEE BTCUSDT -0.052 USDT).
Toan lai du lieu tu san public mainnet, khong key, khong lenh.
"""
import json
import time
import urllib.request

TTL = 15


def get(url):
    with urllib.request.urlopen(url, timeout=TTL) as r:
        return json.loads(r.read())


prem = get('https://fapi.binance.com/fapi/v1/premiumIndex')
tick = get('https://fapi.binance.com/fapi/v1/ticker/24hr')
books = get('https://fapi.binance.com/fapi/v1/ticker/bookTicker')
info = get('https://fapi.binance.com/fapi/v1/exchangeInfo')

onboard = {s['symbol']: s.get('onboardDate', 0) / 1000 for s in info['symbols']}
tickmap = {x['symbol']: x for x in tick}
bookmap = {x['symbol']: x for x in books}
now = time.time()

FEE = 6.0
pass_rows = []
r = v = s = a = 0
for p in prem:
    sym = p['symbol']
    rate = float(p.get('lastFundingRate', 0) or 0) * 1e4
    if abs(rate) < 30:
        continue
    r += 1
    t = tickmap.get(sym)
    if not t or float(t.get('quoteVolume', 0)) < 50e6:
        continue
    v += 1
    b = bookmap.get(sym)
    if not b:
        continue
    bid, ask = float(b['bidPrice']), float(b['askPrice'])
    if bid <= 0 or ask <= 0 or (ask - bid) / ((ask + bid) / 2) * 1e4 > 3.0:
        continue
    s += 1
    if (now - onboard.get(sym, now)) / 86400 < 30:
        continue
    a += 1
    pass_rows.append((abs(rate), sym, rate))

print('Cross chan |rate|>=30 & vol>=50M & spread<=3 & tuoi>=30d trong 900:')
print(f'  dau: rate30+ {r}, +vol {v}, +spread {s}, +tuoi {a}')
print(f'Pass all (OI n/a): {len(pass_rows)}')
for _, sym, rate in sorted(pass_rows, reverse=True)[:10]:
    print(f'  {sym:14} rate {rate:+8.1f} bps/8h  net {abs(rate)-FEE:+8.1f} bps')
