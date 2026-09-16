"""Lay OI + spread hien tai cho coin uu tien."""
import json
import time
import urllib.request

WATCH = ['VTHOUSDT', 'IOSTUSDT', 'NEWTUSDT', 'CXMTUSDT', 'LAUSDT',
         'URNMUSDT', 'TEAMUSDT', 'TBTUSDT']

with urllib.request.urlopen('https://fapi.binance.com/fapi/v1/premiumIndex', timeout=10) as r:
    prem = {x['symbol']: x for x in json.loads(r.read())}
with urllib.request.urlopen('https://fapi.binance.com/futures/data/openInterestHist?symbol=VTHOUSDT&period=5m&limit=1', timeout=10) as r:
    print('VTHO OI sample:', json.loads(r.read()))
with urllib.request.urlopen('https://fapi.binance.com/fapi/v1/ticker/bookTicker', timeout=10) as r:
    books = {x['symbol']: x for x in json.loads(r.read())}

print(f'\n{"symbol":12}{"rate/8h":>10}{"bid":>12}{"ask":>12}{"spread_bps":>11}{"mark vs idx":>12}')
for sym in WATCH:
    if sym not in prem or sym not in books:
        continue
    p, b = prem[sym], books[sym]
    rate = float(p['lastFundingRate'])*1e4
    bid, ask = float(b['bidPrice']), float(b['askPrice'])
    if bid > 0 and ask > 0:
        spread = (ask-bid)/((ask+bid)/2)*1e4
        gap = (float(p['markPrice'])/float(p['indexPrice'])-1)*100
        print(f'{sym:12}{rate:+10.1f}{bid:12.5g}{ask:12.5g}{spread:11.2f}{gap:+11.2f}%')
