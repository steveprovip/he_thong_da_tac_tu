#!/usr/bin/env python3
"""Replay of the logged chop-harvester entries under three exit rules on Binance USD-M public aggTrades.  Read-only apart from --fetch.

    python -I tools/replay_overnight_exits.py fetch  --ledger data/engine_trades.jsonl --since 2026-11-09T23:00:00+07:00 --out data/replay_overnight_20261110
    python -I tools/replay_overnight_exits.py replay --ledger data/engine_trades.jsonl --since 2026-11-09T23:00:00+07:00 --data data/replay_overnight_20261110

The ENTRIES are the real fills of the Demo run (symbol, side, price, quantity, time = ledger t - hold_s); only the EXIT differs:
  legacy    TP 0.15 % resting maker + exchange stop 0.15 % (taker 5 bps + 1 bp slip).  At 45 s the TP is cancelled and a maker exit works at
            max(touch, BE floor); at --escalate_s (215 s: the ledger dumps left at 203-227 s) the floor is dropped and it leaves at the touch.
  patient   TP + stop stay; from 180 s, and only once MFE >= 0.09 %, a scratch works at the floor + 1 tick; no time escalation.
            (The engine also escalates on VPIN >= 0.80 / adverse flow / regime flip: aggTrades carry no VPIN or engine flow, so that
            branch is NOT simulated - a limit of this replay.)
  barriers  TP + stop only.
A position still open after --horizon_s (1 200 s) leaves at the last print as a maker (counted, flagged HORIZON).
BASIS: the Demo venue book is independent of mainnet.  Each ledger row carries the venue-vs-signal basis at the fill (+-10 bps here, 0.2 -> -18.6 bps
between fill and exit on PEPE, against a 15 bps TP), so by default the entry is moved to its mainnet equivalent  entry / (1 + basis_bps / 1e4)  before
the barriers are laid (--no-rebase = the first version of this tool, which compared Demo prices with mainnet prints).  A constant basis is still an
approximation: the Demo book drifts against mainnet while the trade is open.
Fill models (as tools/replay_exit_policies.py): opt = a resting closing order fills on an opposite-side print AT or through its price;
cons = only THROUGH it.  No queue position, no market impact, touch = last print.  Fees: maker 2 bps, taker 5 bps + 1 bp slip.
`legacy` is also compared with the ledger's own net per trade: that error is the honesty check of the whole replay.

Everything fetched is untrusted data: parsed with json only, cast to float / int, written to its own new directory.  The module is
stdlib only so it runs under `python -I`.  With 13 trades, no result here is statistical evidence; it is a check of the mechanism."""
import argparse
import datetime as dt
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

API = 'https://fapi.binance.com/fapi/v1/aggTrades'
TICK = {'XRPUSDT': 1e-4, 'DOGEUSDT': 1e-5, '1000PEPEUSDT': 1e-7}
CFG = {'tp': 0.15, 'sl': 0.15, 'maker_bps': 2.0, 'taker_bps': 5.0, 'slip_bps': 1.0, 'be_s': 45.0, 'escalate_s': 215.0,
       'repeg_s': 5.0, 'patient_be_s': 180.0, 'patient_mfe': 0.09, 'horizon_s': 1200.0}
POLICIES = ('legacy', 'patient', 'barriers')


# ============================================================================= ledger -> trades
def load_trades(ledger: Path, since: float) -> list:
    """Harvest-bracket, real-size, AUTO rows of the ledger at or after `since` (epoch s) -> trade dicts."""
    out = []
    for line in Path(ledger).read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r.get('t', 0) < since or r.get('bracket_src') != 'HARVEST' or r.get('tag') != 'AUTO':
            continue
        qty, entry = float(r['qty']), float(r['entry'])
        if qty * entry < 1.0:
            continue                                            # dust remainder of a partial fill
        out.append({'symbol': r['symbol'], 'side': r['side'], 'entry': entry, 'qty': qty, 't_exit': float(r['t']),
                    't0': float(r['t']) - float(r.get('hold_s') or 0.0), 'net': float(r['pnl']), 'reason': r['reason'],
                    'hold_s': float(r.get('hold_s') or 0.0), 'basis_bps': float(r.get('basis_bps') or 0.0)})
    return out


# ============================================================================= simulation
def _fees(tr: dict, exit_px: float, taker: bool, cfg: dict) -> float:
    bps_out = (cfg['taker_bps'] if taker else cfg['maker_bps'])
    return tr['qty'] * (tr['entry'] * cfg['maker_bps'] + exit_px * bps_out) / 1e4


def simulate(ticks: list, tr: dict, policy: str, model: str = 'opt', cfg: dict = CFG) -> dict:
    """ticks: [(t_s, price, buyer_is_maker)] sorted by time.  -> {'reason', 'exit_px', 'exit_t', 'net', 'mfe_pct', 'maker'}."""
    assert policy in POLICIES and model in ('opt', 'cons')
    d = 1 if tr['side'] == 'BUY' else -1
    if cfg.get('rebase', True) and tr.get('basis_bps'):
        tr = {**tr, 'entry': tr['entry'] / (1 + tr['basis_bps'] / 1e4)}       # the Demo fill priced in mainnet terms
    e, t0 = tr['entry'], tr['t0']
    tp, sl = e * (1 + d * cfg['tp'] / 100.0), e * (1 - d * cfg['sl'] / 100.0)
    floor0 = e * (1 + d * 2 * cfg['maker_bps'] / 1e4)           # entry fee + maker exit fee: net ~ 0
    tick = TICK.get(tr['symbol'], 0.0)
    end = t0 + cfg['horizon_s']

    def hit(px_order: float, px: float, buyer_maker: bool) -> bool:
        """Does this print fill our resting CLOSING limit?  Long closes with a SELL (a buyer-initiated print lifts it)."""
        if d > 0:
            return (not buyer_maker) and (px >= px_order if model == 'opt' else px > px_order)
        return buyer_maker and (px <= px_order if model == 'opt' else px < px_order)

    def done(reason, px, t, taker=False):
        gross = tr['qty'] * (px - e) * d
        return {'reason': reason, 'exit_px': px, 'exit_t': t, 'net': gross - _fees(tr, px, taker, cfg), 'maker': not taker,
                'mfe_pct': mfe, 'held_s': t - t0}

    mfe, last, tp_live, order, floor = 0.0, e, True, None, None
    for t, px, bm in ticks:
        if t < t0:
            continue
        if t > end:
            break
        last = px
        mfe = max(mfe, d * (px / e - 1) * 100.0)
        age = t - t0
        if d * (px - sl) <= 0:                                  # exchange stop first (the conservative order of events)
            return done('SL', sl * (1 - d * cfg['slip_bps'] / 1e4), t, taker=True)
        if policy == 'legacy':
            if age >= cfg['be_s'] and tp_live:
                tp_live, floor = False, floor0
            if age >= cfg['escalate_s']:
                floor = None
        elif policy == 'patient' and tp_live and age >= cfg['patient_be_s'] and mfe >= cfg['patient_mfe']:
            tp_live, floor = False, floor0 + d * tick
        if tp_live and hit(tp, px, bm):
            return done('TP', tp, t)
        if not tp_live:
            if order is None or t - order[1] >= cfg['repeg_s'] or order[2] != floor:
                want = last if floor is None else (max(last, floor) if d > 0 else min(last, floor))
                order = (want, t, floor)
            if hit(order[0], px, bm):
                return done('BE' if floor is not None else 'EJECT', order[0], t)
    return done('HORIZON', last, min(end, ticks[-1][0]) if ticks else t0)


def run(trades: list, ticks_by_trade: list, cfg: dict = CFG) -> dict:
    """-> {model: {policy: [result per trade]}} for the trades that have ticks."""
    out = {}
    for model in ('cons', 'opt'):
        out[model] = {p: [simulate(tk, tr, p, model, cfg) for tr, tk in zip(trades, ticks_by_trade)] for p in POLICIES}
    return out


def summarize(trades: list, res: dict) -> dict:
    out = {}
    for model, by_pol in res.items():
        out[model] = {}
        for pol, rs in by_pol.items():
            net = [r['net'] for r in rs]
            reasons = {}
            for r in rs:
                reasons[r['reason']] = reasons.get(r['reason'], 0) + 1
            out[model][pol] = {'n': len(rs), 'net_usdt': round(sum(net), 5), 'mean_usdt': round(sum(net) / len(net), 6) if net else None,
                               'wins': sum(1 for x in net if x > 0), 'reasons': reasons}
    actual = [t['net'] for t in trades]
    out['ledger'] = {'n': len(actual), 'net_usdt': round(sum(actual), 5)}
    return out


# ============================================================================= fetch (the only network code)
def fetch_window(symbol: str, start_ms: int, end_ms: int, opener=urllib.request.urlopen, sleep=time.sleep, log=print) -> list:
    """Public aggTrades of [start_ms, end_ms] -> [(t_ms, price, qty, buyer_is_maker)], paged by fromId, paced by the weight header."""
    rows, from_id = [], None
    while True:
        q = {'symbol': symbol, 'limit': 1000}
        if from_id is None:
            q.update(startTime=int(start_ms), endTime=int(min(end_ms, start_ms + 3_600_000 - 1)))
        else:
            q['fromId'] = from_id
        req = urllib.request.Request(API + '?' + urllib.parse.urlencode(q), headers={'User-Agent': 'replay-overnight-exits/1'})
        with opener(req, timeout=20) as resp:
            data = json.loads(resp.read().decode('utf-8'))
            used = int(resp.headers.get('X-MBX-USED-WEIGHT-1M') or 0)
        if not isinstance(data, list):
            raise RuntimeError(f'unexpected aggTrades answer for {symbol}: {str(data)[:120]}')
        for x in data:
            t = int(x['T'])
            if t <= end_ms:
                rows.append((t, float(x['p']), float(x['q']), bool(x['m'])))
        if len(data) < 1000 or int(data[-1]['T']) > end_ms:
            break
        from_id = int(data[-1]['a']) + 1
        sleep(12.0 if used > 1500 else 1.0)       # half the 1m weight budget at most: the Demo bot shares this IP
    return rows


def cmd_fetch(a) -> None:
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=False)                      # its own NEW directory: fetched data is untrusted
    trades = load_trades(a.ledger, since_epoch(a.since))
    meta = []
    for i, tr in enumerate(trades):
        start, end = int((tr['t0'] - 2) * 1000), int((tr['t0'] + a.horizon_s) * 1000)
        rows = fetch_window(tr['symbol'], start, end)
        name = f'{i:02d}_{tr["symbol"]}_{int(tr["t0"])}.json'
        (out / name).write_text(json.dumps(rows), encoding='utf-8')
        meta.append({'file': name, 'symbol': tr['symbol'], 't0': tr['t0'], 'prints': len(rows)})
        print(f'{name}: {len(rows)} prints', flush=True)
    (out / 'manifest.json').write_text(json.dumps({'source': API, 'fetched_at': time.time(), 'trades': meta}), encoding='utf-8')


def load_ticks(data_dir: Path, trades: list) -> list:
    man = json.loads((Path(data_dir) / 'manifest.json').read_text(encoding='utf-8'))
    out = []
    for m, tr in zip(man['trades'], trades):
        if m['symbol'] != tr['symbol'] or abs(m['t0'] - tr['t0']) > 1e-6:
            raise SystemExit(f'manifest does not match the ledger at {m["file"]}')
        rows = json.loads((Path(data_dir) / m['file']).read_text(encoding='utf-8'))
        out.append([(float(t) / 1000.0, float(p), bool(bm)) for t, p, _q, bm in rows])
    return out


def since_epoch(text: str) -> float:
    return dt.datetime.fromisoformat(text).timestamp()


def cmd_replay(a) -> None:
    trades = load_trades(a.ledger, since_epoch(a.since))
    ticks = load_ticks(a.data, trades)
    cfg = {**CFG, 'escalate_s': a.escalate_s, 'horizon_s': a.horizon_s, 'tp': a.tp, 'sl': a.sl, 'rebase': not a.no_rebase}   # --tp / --sl: sensitivity only
    res = run(trades, ticks, cfg)
    summ = summarize(trades, res)
    print(f'{len(trades)} harvest entries, horizon {cfg["horizon_s"]:.0f} s, legacy escalation {cfg["escalate_s"]:.0f} s')
    for model in ('cons', 'opt'):
        print(f'\n== fill model {model}')
        print('time   sym  s  ledger(U) reason       | legacy        | patient       | barriers')
        for i, tr in enumerate(trades):
            cells = ' | '.join(f'{res[model][p][i]["net"]:+.5f} {res[model][p][i]["reason"]:<7}' for p in POLICIES)
            print(f'{dt.datetime.fromtimestamp(tr["t_exit"]).strftime("%H:%M")} {tr["symbol"][:4]} {tr["side"][0]} {tr["net"]:+.5f} {tr["reason"]:<12} | {cells}')
        for p in POLICIES:
            s = summ[model][p]
            print(f'{p:<9} net {s["net_usdt"]:+.5f} U  mean {s["mean_usdt"]:+.6f}  wins {s["wins"]}/{s["n"]}  {s["reasons"]}')
        err = [res[model]['legacy'][i]['net'] - tr['net'] for i, tr in enumerate(trades)]
        print(f'legacy replay vs ledger: net {sum(r["net"] for r in res[model]["legacy"]):+.5f} vs {summ["ledger"]["net_usdt"]:+.5f} U, '
              f'mean abs error {sum(abs(x) for x in err) / len(err):.5f} U per trade')
    if a.json:
        Path(a.json).write_text(json.dumps({'config': cfg, 'summary': summ, 'trades': trades}, indent=1), encoding='utf-8')


def main(argv=None) -> None:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = ap.add_subparsers(dest='cmd', required=True)
    for name in ('fetch', 'replay'):
        sp = sub.add_parser(name)
        sp.add_argument('--ledger', required=True)
        sp.add_argument('--since', required=True, help='ISO time with offset, e.g. 2026-11-09T23:00:00+07:00')
        sp.add_argument('--horizon_s', type=float, default=CFG['horizon_s'])
    sub.choices['fetch'].add_argument('--out', required=True)
    sub.choices['replay'].add_argument('--data', required=True)
    sub.choices['replay'].add_argument('--escalate_s', type=float, default=CFG['escalate_s'])
    sub.choices['replay'].add_argument('--json')
    sub.choices['replay'].add_argument('--no-rebase', action='store_true', help='compare Demo prices with mainnet prints as they are')
    sub.choices['replay'].add_argument('--tp', type=float, default=CFG['tp'], help='TP %% (the harvester ships 0.15)')
    sub.choices['replay'].add_argument('--sl', type=float, default=CFG['sl'], help='stop %% (the harvester ships 0.15)')
    a = ap.parse_args(argv)
    {'fetch': cmd_fetch, 'replay': cmd_replay}[a.cmd](a)


if __name__ == '__main__':
    main(sys.argv[1:])
