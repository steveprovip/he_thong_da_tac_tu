"""Verify xem tai khoan testnet da tung co settlement FUNDING_FEE chua.

Chi doc /fapi/v1/income tren testnet. KHONG in key. KHONG dat lenh.
Ket luan: co/khong mot lan FUNDING_FEE nao tu ghi nhan cua viec giu vi the qua print.
"""
import hashlib
import hmac
import json
import re
import time
import urllib.parse
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / '.env.local'
TTL = 15


def load_keys():
    if not ENV.exists():
        return {}
    out = {}
    for line in ENV.read_text(encoding='utf-8', errors='ignore').splitlines():
        m = re.match(r'^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*?)\s*$', line)
        if m and not line.strip().startswith('#'):
            out[m.group(1)] = m.group(2).strip().strip('"').strip("'")
    return out


def pick(keys):
    cands = ['BINANCE_TESTNET_API_KEY', 'TESTNET_API_KEY', 'BINANCE_TESTNET_SECRET',
             'TESTNET_SECRET', 'BINANCE_TESTNET_API_SECRET', 'TESTNET_SECRET_KEY']
    api = secret = None
    for k, v in keys.items():
        name = k.upper()
        if not api and 'TESTNET' in name and 'KEY' in name and 'SECRET' not in name:
            api = v
        if not secret and 'TESTNET' in name and 'SECRET' in name:
            secret = v
    # fallback: bien co TEST ngam, hoac dung cap 'BINANCE_TESTNET_API_KEY/SECRET'
    if not api or not secret:
        for cand_api, cand_sec in (('BINANCE_TESTNET_API_KEY', 'BINANCE_TESTNET_API_SECRET'),
                                   ('BINANCE_TESTNET_API_KEY', 'BINANCE_TESTNET_SECRET_KEY'),
                                   ('TESTNET_API_KEY', 'TESTNET_SECRET')):
            if cand_api in keys and cand_sec in keys:
                api, secret = keys[cand_api], keys[cand_sec]
                break
    return api, secret


def signed(path, params, secret):
    params.update({'timestamp': int(time.time() * 1000), 'recvWindow': 5000})
    qs = urllib.parse.urlencode(params)
    params['signature'] = hmac.new(secret.encode(), qs.encode(), hashlib.sha256).hexdigest()
    return path + '?' + urllib.parse.urlencode(params)


def main():
    keys = load_keys()
    names = [k for k in keys if 'KEY' in k.upper() or 'SECRET' in k.upper()]
    print('Bien chua key/secret co trong .env.local:', names if names else 'KHONG THAY')
    api, secret = pick(keys)
    if not api or not secret:
        print('=> Khong tim duoc cap testnet key/secret -> STOP, khong verify duoc.')
        return 1
    print(f'Cap testnet: api_len={len(api)} secret_len={len(secret)} (khong in gia tri)')

    url = signed('/fapi/v1/income', {'incomeType': 'FUNDING_FEE'}, secret)
    req = urllib.request.Request('https://testnet.binancefuture.com' + url,
                                 headers={'X-MBX-APIKEY': api})
    try:
        with urllib.request.urlopen(req, timeout=TTL) as r:
            rows = json.loads(r.read())
    except urllib.error.HTTPError as e:
        body = e.read().decode('utf-8', 'ignore')[:200]
        print(f'HTTP {e.code}: {body}')
        return 1
    except Exception as e:
        print('LOI:', type(e).__name__, str(e)[:200])
        return 1

    funding = [x for x in rows if x.get('incomeType') == 'FUNDING_FEE']
    print(f'\nTong dong income tra ve: {len(rows)}')
    print(f'Dong FUNDING_FEE (ket luan keyword): {len(funding)}')
    for x in funding[:3]:
        print(f'  {x.get("symbol")} time={x.get("time")} income={x.get("income")} asset={x.get("asset")}')
    if funding:
        print('\n=> TAI KHOAN TESTNET DA TUNG NHAN settlement FUNDING_FEE THAT (co co che).')
    else:
        print('\n=> CHUA CO settlement FUNDING_FEE nao. Quyet dinh B (dung ngoai) la hop ly.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())