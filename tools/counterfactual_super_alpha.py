"""Counterfactual of the REAL SOL/DOGE GTX attempts in engine_orders.jsonl: what would crossing (taker IOC) or a
longer / dynamic TTL have earned, net of fees?  Public aggTrades only (no key, no order).  Read-only.

    python tools/counterfactual_super_alpha.py --orders <snapshot>/engine_orders.jsonl [--json out.json]

For every attempt sent to the venue (FILLED / PARTIAL / UNFILLED / ERROR) on SOLUSDT / DOGEUSDT:
  t_dec  = log time - total_ms (decision), t_send = t_dec + decide_to_send_ms.
  Prices come from aggTrades of BOTH the execution venue (demo-fapi, where demo P&L is realised) and mainnet
  (fapi, the market the signal reads and where real money would trade).  Cached in data/replay_cache/aggtrades/.

Policies (same size, same brackets):
  IOC_NOW   taker at t_send: price = first aggressor trade on our side (BUY: buyer-taker) in [t_send, t_send+2 s],
            accepted only within the 3 bps slip cap of the decision touch; fee 5 bps + 1 bps slip.
  IOC_2S    GTX for 2 s (filled only if a trade prints THROUGH our price), else taker at t_send + 2 s (as IOC_NOW).
  GTX_T3/T10/T20  rest at px_first; filled when a trade prints through it (strictly better) before TTL; fee 2 bps.
  DYN_T15   rest up to 15 s, cancel as soon as the last trade moves >= 5 bps against the trade direction or
            >= 5 bps away (the market left us); filled as GTX_T*.
Outcome of a filled entry: bracket on the trade path of the same venue - SL 0.35 % (SOL) / 0.50 % (DOGE) taker
+1 bps slip, TP 0.88 % / 1.25 % maker, exit at the last price after 300 s (taker).  Plus markouts 30/60/120/300 s
(mid proxy = last trade) net of entry fee + 2 bps maker exit.  Unfilled = 0.
Limits: no book history (touch / depth / queue unknown except at decision), slippage of 0.8 SOL / 500 DOGE on the
thin demo book unknown, engine exits (FLOW / REGIME / soft tiers / EJECT) not replicated, attempts overlap in time.
"""
import argparse
import json
import math
import random
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HOSTS = {'demo': 'https://demo-fapi.binance.com', 'main': 'https://fapi.binance.com'}
BR = {'SOLUSDT': (0.35, 0.88), 'DOGEUSDT': (0.50, 1.25)}
MAKER, TAKER, SLIP, CAP_BPS, HOLD = 2.0, 5.0, 1.0, 3.0, 300.0
CACHE = ROOT / 'data' / 'replay_cache' / 'aggtrades'


def get(url):
    for k in range(4):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())
        except Exception:
            time.sleep(0.5 * 2 ** k)
    raise RuntimeError('fetch failed')


def trades(venue, sym, t0, t1):
    """[(t_s, price, buyer_is_maker)] for [t0, t1] (s), cached."""
    CACHE.mkdir(parents=True, exist_ok=True)
    f = CACHE / f'{venue}_{sym}_{int(t0)}_{int(t1)}.json'
    if f.exists():
        return [tuple(x) for x in json.loads(f.read_text())]
    out, start = [], int(t0 * 1000)
    end = int(t1 * 1000)
    while True:
        rows = get(f'{HOSTS[venue]}/fapi/v1/aggTrades?symbol={sym}&startTime={start}&endTime={end}&limit=1000')
        out += [(x['T'] / 1000.0, float(x['p']), bool(x['m'])) for x in rows]
        if len(rows) < 1000:
            break
        start = rows[-1]['T'] + 1
        time.sleep(0.15)
    f.write_text(json.dumps(out))
    time.sleep(0.1)
    return out


def last_before(tr, t):
    p = None
    for ts, px, _ in tr:
        if ts > t:
            break
        p = px
    return p


def bracket(tr, side, t_in, px_in, fee_in, sym):
    sl, tp = BR[sym]
    d = 1 if side == 'BUY' else -1
    slp, tpp = px_in * (1 - d * sl / 100), px_in * (1 + d * tp / 100)
    last = px_in
    for ts, px, _ in tr:
        if ts <= t_in:
            continue
        if ts > t_in + HOLD:
            break
        last = px
        if d * (px - slp) <= 0:
            return d * (slp / px_in - 1) * 1e4 - fee_in - TAKER - SLIP, 'SL'
        if d * (px - tpp) >= 0:
            return d * (tpp / px_in - 1) * 1e4 - fee_in - MAKER, 'TP'
    return d * (last / px_in - 1) * 1e4 - fee_in - TAKER, 'TIME'


def taker_fill(tr, side, t, ref):
    """First aggressor print on our side within 2 s, inside the slip cap of the decision touch."""
    for ts, px, bm in tr:
        if ts < t:
            continue
        if ts > t + 2.0:
            break
        if (side == 'BUY' and not bm) or (side == 'SELL' and bm):
            cap = ref * (1 + (CAP_BPS if side == 'BUY' else -CAP_BPS) / 1e4)
            if (side == 'BUY' and px <= cap) or (side == 'SELL' and px >= cap):
                return ts, px * (1 + (SLIP if side == 'BUY' else -SLIP) / 1e4)
            return None
    return None


def rest_fill(tr, side, px, t0, t1, dyn=False, ref=None):
    """Trade printed strictly through our resting price -> (t, px); dyn: cancel on 5 bps adverse / away."""
    d = 1 if side == 'BUY' else -1
    for ts, p, _ in tr:
        if ts < t0:
            continue
        if ts > t1:
            break
        if (side == 'BUY' and p < px) or (side == 'SELL' and p > px):
            return ts, px
        if dyn and ref:
            mv = d * (p / ref - 1) * 1e4
            if mv <= -5.0 or mv >= 5.0:          # against us (falling knife for a buy) or the market left
                return None
    return None


def evaluate(r, venue):
    sym, side = r['symbol'], r['side']
    d = 1 if side == 'BUY' else -1
    t_dec = r['t'] - (r.get('total_ms') or 0) / 1000.0
    t_send = t_dec + (r.get('decide_to_send_ms') or 0) / 1000.0
    tr = trades(venue, sym, t_dec - 5, t_dec + HOLD + 30)
    if not tr:
        return None
    px_q = r.get('px_first')
    if venue == 'main':                             # mainnet: quote at the signal mid +/- 0.5 tick proxy
        ref = r.get('signal_mid') or last_before(tr, t_dec)
        px_q = ref
    else:
        ref = r.get('venue_mid') or px_q
    touch = px_q if px_q else ref
    res = {}
    m0 = last_before(tr, t_send) or ref
    for name in ('IOC_NOW', 'IOC_2S', 'GTX_T3', 'GTX_T10', 'GTX_T20', 'DYN_T15'):
        fill, fee = None, MAKER
        if name == 'IOC_NOW':
            fill, fee = taker_fill(tr, side, t_send, ref), TAKER + SLIP
            if fill:
                fill = (fill[0], fill[1])
        elif name == 'IOC_2S':
            fill = rest_fill(tr, side, touch, t_send, t_send + 2.0)
            if not fill:
                fill, fee = taker_fill(tr, side, t_send + 2.0, last_before(tr, t_send + 2.0) or ref), TAKER + SLIP
        elif name.startswith('GTX'):
            fill = rest_fill(tr, side, touch, t_send, t_send + float(name.split('_T')[1]))
        else:
            fill = rest_fill(tr, side, touch, t_send, t_send + 15.0, dyn=True, ref=m0)
        if not fill:
            res[name] = {'f': 0, 'net': 0.0}
            continue
        tf, px = fill
        fee_in = fee if name.startswith('IOC') and fee != MAKER else MAKER
        net, why = bracket(tr, side, tf, px, fee_in, sym)
        mk = {}
        for h in (30, 60, 120, 300):
            p = last_before(tr, tf + h)
            mk[f'mk{h}'] = round(d * (p / px - 1) * 1e4 - fee_in - MAKER, 3) if p else None
        res[name] = {'f': 1, 'net': round(net, 3), 'exit': why, 'wait': round(tf - t_send, 2),
                     'is_bps': round(d * (px / m0 - 1) * 1e4, 3) if m0 else None, **mk}
    return res


def block_ci(xs, n_boot=3000, seed=5):
    n = len(xs)
    if n < 8:
        return None
    b = max(1, round(math.sqrt(n)))
    rng, ms = random.Random(seed), []
    for _ in range(n_boot):
        o = []
        while len(o) < n:
            s = rng.randrange(0, n - b + 1)
            o += xs[s:s + b]
        ms.append(sum(o[:n]) / n)
    ms.sort()
    return [round(ms[int(.025 * n_boot)], 2), round(ms[int(.975 * n_boot) - 1], 2)]


def summarize(rows, venue, key=lambda r: True):
    out = {}
    sel = [r for r in rows if key(r) and r[venue]]
    for name in ('IOC_NOW', 'IOC_2S', 'GTX_T3', 'GTX_T10', 'GTX_T20', 'DYN_T15'):
        xs = [r[venue][name] for r in sel]
        fills = [x for x in xs if x['f']]
        nets = [x['net'] for x in xs]
        avg = lambda k: round(sum(x[k] for x in fills if x.get(k) is not None) /
                              max(1, sum(x.get(k) is not None for x in fills)), 2) if fills else None
        out[name] = {'n': len(xs), 'fills': len(fills), 'net_per_opp_bps': round(sum(nets) / len(xs), 2) if xs else None,
                     'ci95': block_ci(nets), 'net_per_fill_bps': avg('net'), 'mk30': avg('mk30'), 'mk60': avg('mk60'),
                     'mk120': avg('mk120'), 'mk300': avg('mk300'), 'wins': sum(x['net'] > 0 for x in fills),
                     'exits': {e: sum(x.get('exit') == e for x in fills) for e in ('SL', 'TP', 'TIME')}}
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--orders', default='data/engine_orders.jsonl')
    ap.add_argument('--json', default='')
    ap.add_argument('--min-score', type=float, default=0.0)
    a = ap.parse_args()
    rows = [json.loads(x) for x in (ROOT / a.orders).read_text(encoding='utf-8').splitlines() if x.strip()]
    att = [r for r in rows if r['outcome'] in ('FILLED', 'PARTIAL', 'UNFILLED', 'ERROR')
           and r['symbol'] in BR and r.get('side') and r.get('score', 0) >= a.min_score]
    out = []
    for r in att:
        x = {'t': r['t'], 'symbol': r['symbol'], 'side': r['side'], 'outcome': r['outcome'], 'score': r.get('score'),
             'edge_bps': r.get('edge_bps'), 'basis_bps': r.get('basis_bps'), 'policy': r.get('policy')}
        for v in ('demo', 'main'):
            try:
                x[v] = evaluate(r, v)
            except RuntimeError:
                x[v] = None
        out.append(x)
    rep = {'n_attempts': len(out), 'rows': out, 'config': {'brackets': BR, 'maker': MAKER, 'taker': TAKER,
           'slip': SLIP, 'cap_bps': CAP_BPS, 'hold_s': HOLD}}
    for v in ('demo', 'main'):
        rep[f'{v}_all'] = summarize(out, v)
        rep[f'{v}_super'] = summarize(out, v, lambda r: (r['score'] or 0) >= 0.95)
    if a.json:
        (ROOT / a.json).write_text(json.dumps(rep, indent=1), encoding='utf-8')
    for k in ('demo_all', 'demo_super', 'main_all', 'main_super'):
        print('==', k)
        for name, s in rep[k].items():
            print(f'{name:8s} n={s["n"]:3d} fills={s["fills"]:3d} net/opp={s["net_per_opp_bps"]} ci={s["ci95"]} '
                  f'net/fill={s["net_per_fill_bps"]} mk30={s["mk30"]} mk60={s["mk60"]} mk300={s["mk300"]} '
                  f'wins={s["wins"]} exits={s["exits"]}')


if __name__ == '__main__':
    main()
