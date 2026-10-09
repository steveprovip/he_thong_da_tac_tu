"""Execution-timing replay for the 4H trend lane: taker vs maker vs swarm-timed maker, on real aggTrades.

    python tools/trend_execution_replay.py --klines <1h klines+funding zips> --agg <dir for daily aggTrades zips>
        [--download] [--window-s 300] [--json out.json]

Question (request 10-09 dd, task 3): when the 4H trend lane must trade at a bar close, does letting the micro
swarm time a post-only maker entry beat a plain taker order, after fees, missed fills and adverse selection?

Events = every position change of the 4H LS trend lane (signal exits + entries, disaster stop 8 ATR) at the 4H close,
OOS 2025-01-01..2026-09-30, BTC/SOL/DOGE (same events for every policy -> paired comparison).
Reference price = the 4H kline close.  Cost (bps, + = worse) = side x (fill - ref) / ref x 1e4 + fee.
Fees VIP0 USD-M: maker 2 bps, taker 5 bps.  Book = touch rebuilt from aggressor sides (a sell aggressor prints
on the bid, a buy aggressor on the ask) - no queue model.  Policies:
  TAKER        cross at the touch right after the close
  MAKER        post at our touch at once, re-peg every 30 s, fill only when the market trades THROUGH our price
               (conservative) ['touch' variant: trades AT our price fill]; no fill by window -> taker
  SWARM        the engine's own MicroFeed features (core/micro_signals.py) wait for a pull-back: post only while
               the 15 s CVD runs AGAINST our side (sellers hitting bids when we buy) and VPIN is not toxic toward
               our side; strong 60 s flow WITH our side (price leaving) -> take at once; window end -> taker
Output: per policy mean cost, fill rate, time to fill, paired difference vs TAKER with bootstrap CI, and the
trend lane's OOS net Sharpe with each policy's measured costs replacing the 6 bps/side assumption.
"""
from __future__ import annotations

import argparse
import csv
import io
import json
import math
import sys
import time
import urllib.request
import zipfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import numpy as np  # noqa: E402

MAKER_BPS, TAKER_BPS = 2.0, 5.0
TICK = {'BTCUSDT': 0.1, 'SOLUSDT': 0.01, 'DOGEUSDT': 0.00001}
WARM_S = 900.0
URL = 'https://data.binance.vision/data/futures/um/daily/aggTrades/{s}/{s}-aggTrades-{d}.zip'


# ------------------------------------------------------------------ data
def day_of_ts(t: float) -> str:
    return datetime.fromtimestamp(t, timezone.utc).strftime('%Y-%m-%d')


def fetch_day(agg_dir: Path, symbol: str, day: str, download: bool) -> Path:
    p = agg_dir / f'{symbol}-aggTrades-{day}.zip'
    if not p.exists() and download:
        agg_dir.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix('.part')
        with urllib.request.urlopen(URL.format(s=symbol, d=day), timeout=120) as r, open(tmp, 'wb') as fh:
            fh.write(r.read())
        tmp.rename(p)
    return p


def load_windows(zpath: Path, windows):
    """Trades inside any [lo, hi) window -> np.array (t, px, qty, buyer_maker)."""
    rows = []
    wins = sorted(windows)
    with zipfile.ZipFile(zpath) as zf:
        for name in zf.namelist():
            with zf.open(name) as fh:
                for r in csv.reader(io.TextIOWrapper(fh, encoding='utf-8')):
                    if not r or not r[0].isdigit():
                        continue
                    ts = int(r[5])
                    t = ts / 1000.0 if ts < 10 ** 14 else ts / 1e6
                    for lo, hi in wins:
                        if lo <= t < hi:
                            rows.append((t, float(r[1]), float(r[2]), 1.0 if r[6].strip().lower() == 'true' else 0.0))
                            break
    if not rows:
        return np.zeros((0, 4))
    a = np.array(rows)
    return a[np.argsort(a[:, 0], kind='stable')]


# ------------------------------------------------------------------ simulation
class Touch:
    def __init__(self, tick):
        self.tick, self.bid, self.ask = tick, None, None

    def on(self, px, bm):
        if bm:
            self.bid = px
            if self.ask is None or self.ask <= px:
                self.ask = px + self.tick
        else:
            self.ask = px
            if self.bid is None or self.bid >= px:
                self.bid = px - self.tick


def simulate(trades, t0: float, side: str, ref: float, symbol: str, window_s: float, policy: str,
             fill_model: str = 'through', feat_fn=None) -> dict:
    """One execution of one event.  trades = np rows (t, px, qty, bm) covering [t0 - WARM_S, t0 + window_s]."""
    d = 1 if side == 'BUY' else -1
    tk = TICK[symbol]
    touch = Touch(tk)
    k, n = 0, len(trades)
    while k < n and trades[k, 0] < t0:
        touch.on(trades[k, 1], trades[k, 3] > 0)
        k += 1
    if touch.bid is None:
        return {'ok': False}

    def cost(px, fee):
        return d * (px - ref) / ref * 1e4 + fee

    if policy == 'TAKER':
        px = touch.ask if d > 0 else touch.bid
        return {'ok': True, 'cost': cost(px, TAKER_BPS), 'filled_maker': False, 'wait_s': 0.0, 'px': px}
    quote = None
    last_peg = -1e18
    posted_at = None
    sec = t0
    for j in range(k, n):
        t, px, q, bm = trades[j]
        if t > t0 + window_s:
            break
        # policy decision once per second (the engine decides every 0.2 s; 1 s is enough at this horizon)
        while sec <= t:
            if policy == 'MAKER':
                if quote is None or sec - last_peg >= 30.0:
                    quote = touch.bid if d > 0 else touch.ask
                    last_peg = sec
                    posted_at = posted_at or sec
            elif policy == 'SWARM':
                f = feat_fn(sec) if feat_fn else {}
                c15, c60, vp, vside = f.get('cvd_15s'), f.get('cvd_60s'), f.get('vpin'), f.get('vpin_side')
                lim = f.get('_scale', 1.0) * 400_000                   # the engine's CVD regime threshold
                if c60 is not None and d * c60 > lim:                  # the move is leaving with strong flow
                    px_take = touch.ask if d > 0 else touch.bid
                    return {'ok': True, 'cost': cost(px_take, TAKER_BPS), 'filled_maker': False,
                            'wait_s': sec - t0, 'px': px_take, 'why': 'flow-with-us -> take'}
                toxic = vp is not None and vp >= 0.6 and vside == ('SELL' if d > 0 else 'BUY')
                pullback = c15 is not None and d * c15 < 0 and not toxic
                if pullback and (quote is None or sec - last_peg >= 30.0):
                    quote = touch.bid if d > 0 else touch.ask
                    last_peg = sec
                    posted_at = posted_at or sec
                elif not pullback and quote is not None and toxic:
                    quote = None                                     # pull the quote while flow is toxic
            sec += 1.0
        # match the resting quote against this print
        if quote is not None:
            hit = (bm > 0 and (px < quote if fill_model == 'through' else px <= quote)) if d > 0 else \
                  (bm == 0 and (px > quote if fill_model == 'through' else px >= quote))
            if hit:
                return {'ok': True, 'cost': cost(quote, MAKER_BPS), 'filled_maker': True, 'wait_s': t - t0,
                        'px': quote}
        touch.on(px, bm > 0)
    px_take = touch.ask if d > 0 else touch.bid                         # window over -> taker
    return {'ok': True, 'cost': cost(px_take, TAKER_BPS), 'filled_maker': False, 'wait_s': window_s,
            'px': px_take, 'why': 'timeout -> take'}


def feature_fn(trades, t0, symbol):
    """The engine's MicroFeed fed with these prints (+ a touch-only book) -> f(sec) -> features dict."""
    from core import micro_signals as msig
    feed = msig.MicroFeed(symbol, msig.config_for(symbol))
    scale = feed.cfg.usd_scale
    touch = Touch(TICK[symbol])
    state = {'k': 0, 'last_book': -1e18}
    n = len(trades)

    def f(sec):
        k = state['k']
        while k < n and trades[k, 0] <= sec:
            t, px, q, bm = (float(x) for x in trades[k])
            touch.on(px, bm > 0)
            if t - state['last_book'] >= 0.1 and touch.bid is not None:
                state['last_book'] = t
                feed.on_message({'stream': f'{symbol.lower()}@depth10@100ms',
                                 'data': {'b': [[repr(touch.bid), '1']], 'a': [[repr(touch.ask), '1']]}}, now=t)
            feed.on_message({'stream': f'{symbol.lower()}@aggTrade',
                             'data': {'p': repr(px), 'q': repr(q), 'm': bool(bm > 0)}}, now=t)
            k += 1
        state['k'] = k
        out = feed.features(sec)
        out['_scale'] = scale
        return out
    f(t0 - 1.0)
    return f


# ------------------------------------------------------------------ driver
def run(events, bars_close, agg_dir: Path, download: bool, window_s: float, log=print):
    by_day = {}
    for e in events:
        by_day.setdefault((e['symbol'], day_of_ts(e['t'] - WARM_S)), []).append(e)
        if day_of_ts(e['t'] + window_s) != day_of_ts(e['t'] - WARM_S):
            by_day.setdefault((e['symbol'], day_of_ts(e['t'] + window_s)), [])
    results = []
    cache = {}
    t_start = time.perf_counter()
    for i, e in enumerate(sorted(events, key=lambda x: x['t'])):
        s, t0 = e['symbol'], e['t']
        days = sorted({day_of_ts(t0 - WARM_S), day_of_ts(t0 + window_s)})
        parts = []
        for dday in days:
            key = (s, dday)
            if key not in cache:
                z = fetch_day(agg_dir, s, dday, download)
                if not z.exists():
                    cache[key] = None
                else:
                    wins = [(x['t'] - WARM_S, x['t'] + window_s + 1) for x in events if x['symbol'] == s and
                            dday in (day_of_ts(x['t'] - WARM_S), day_of_ts(x['t'] + window_s))]
                    cache[key] = load_windows(z, wins)
            if cache[key] is not None:
                parts.append(cache[key])
        if not parts:
            results.append({**e, 'ok': False, 'why': 'no data'})
            continue
        tr = np.concatenate(parts)
        tr = tr[(tr[:, 0] >= t0 - WARM_S) & (tr[:, 0] <= t0 + window_s + 1)]
        tr = tr[np.argsort(tr[:, 0], kind='stable')]
        ref = bars_close.get((s, t0))
        if ref is None or len(tr) == 0:
            results.append({**e, 'ok': False, 'why': 'no ref/trades'})
            continue
        row = {**e, 'ref': ref, 'ok': True}
        for pol, fm in (('TAKER', 'through'), ('MAKER', 'through'), ('MAKER', 'touch'), ('SWARM', 'through'),
                        ('SWARM', 'touch')):
            ff = feature_fn(tr, t0, s) if pol == 'SWARM' else None
            r = simulate(tr, t0, e['side'], ref, s, window_s, pol, fm, ff)
            row[f'{pol}_{fm}'] = r
            row['ok'] = row['ok'] and r.get('ok', False)
        results.append(row)
        if (i + 1) % 20 == 0:
            log(f'  {i + 1}/{len(events)} events ({time.perf_counter() - t_start:.0f}s)')
    return results


def summarize(results, policies):
    ok = [r for r in results if r.get('ok')]
    out = {'n': len(ok), 'n_total': len(results)}
    from core import fast_eval as fe
    base = [r['TAKER_through']['cost'] for r in ok]
    for p in policies:
        c = [r[p]['cost'] for r in ok]
        fills = [r[p]['filled_maker'] for r in ok]
        waits = [r[p]['wait_s'] for r in ok if r[p]['filled_maker']]
        pdif = fe.paired_diff(base, c, n_boot=2000) if p != 'TAKER_through' else None
        out[p] = {'mean_cost_bps': float(np.mean(c)), 'median_cost_bps': float(np.median(c)),
                  'p90_cost_bps': float(np.percentile(c, 90)), 'maker_fill_rate': float(np.mean(fills)),
                  'median_wait_s': float(np.median(waits)) if waits else None,
                  'vs_taker_bps': ({'mean': pdif['mean'], 'ci95': pdif['ci95'], 't': pdif['t']} if pdif else None)}
    return out


def main(argv=None):
    import factor_scan as fs
    import trend_backtest as tb
    from core import trend_lane as tl
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--klines', type=Path, required=True)
    ap.add_argument('--agg', type=Path, required=True)
    ap.add_argument('--download', action='store_true')
    ap.add_argument('--window-s', type=float, default=300.0)
    ap.add_argument('--symbols', default='BTCUSDT,SOLUSDT,DOGEUSDT')
    ap.add_argument('--start', default='2025-01-01')
    ap.add_argument('--end', default='2026-09-30')
    ap.add_argument('--max-events', type=int, default=0)
    ap.add_argument('--json', type=Path)
    a = ap.parse_args(argv)
    syms = a.symbols.split(',')
    bars, fund = tb.load_bars(a.klines, syms, '4h')
    cfg = tl.TrendConfig(bar='4h')
    bt = tl.backtest_brackets(bars, cfg, tl.Brackets(sl_atr=8.0), fund)
    lo, hi = fs.day_of(a.start) * 86400, (fs.day_of(a.end) + 1) * 86400
    closes = {(s, b['t'][i] + 4 * 3600): b['c'][i] for s, b in bars.items() for i in range(len(b['t']))}
    ev = []
    for tr in bt['trades']:
        d = 1 if tr['side'] == 'LONG' else -1
        ct = tr['entry_t'] + 4 * 3600
        if lo <= ct < hi:
            ev.append({'symbol': tr['symbol'], 't': ct, 'side': 'BUY' if d > 0 else 'SELL', 'kind': 'ENTRY',
                       'w': tr['w'], 'trade': tr['entry_t']})
        if tr.get('exit_t') and tr.get('why') == 'SIGNAL':
            xt = tr['exit_t'] + 4 * 3600
            if lo <= xt < hi:
                ev.append({'symbol': tr['symbol'], 't': xt, 'side': 'SELL' if d > 0 else 'BUY', 'kind': 'EXIT',
                           'w': tr['w'], 'trade': tr['entry_t']})
    ev.sort(key=lambda x: x['t'])
    if a.max_events:
        ev = ev[:a.max_events]
    print(f'{len(ev)} execution events ({",".join(syms)}), window {a.window_s:.0f}s')
    res = run(ev, closes, a.agg, a.download, a.window_s)
    pols = ['TAKER_through', 'MAKER_through', 'MAKER_touch', 'SWARM_through', 'SWARM_touch']
    summ = summarize(res, pols)
    # strategy impact: replace the 6 bps/side assumption with each policy's measured cost on these events
    base_daily = tl.to_daily(bt['bar_returns'])
    lo_d, hi_d = fs.day_of(a.start), fs.day_of(a.end)
    impact = {}
    for p in pols:
        adj = dict(base_daily)
        for r in res:
            if r.get('ok'):
                dd = int(r['t'] // 86400)
                adj[dd] = adj.get(dd, 0.0) + r['w'] * (6.0 - r[p]['cost']) / 1e4
        st = fs.stats(adj, {}, lo_d, hi_d)
        impact[p] = {'sharpe': st['sharpe'], 'ann_ret_pct': st['ann_ret_pct'], 'max_dd_pct': st['max_dd_pct'],
                     'psr0': st['psr0']}
    st0 = fs.stats(base_daily, {}, lo_d, hi_d)
    impact['ASSUMED_6bps'] = {'sharpe': st0['sharpe'], 'ann_ret_pct': st0['ann_ret_pct'],
                              'max_dd_pct': st0['max_dd_pct'], 'psr0': st0['psr0']}
    print(f"\nevents replayed {summ['n']}/{summ['n_total']}")
    for p in pols:
        x = summ[p]
        vs = x['vs_taker_bps']
        print(f"{p:<15} cost mean {x['mean_cost_bps']:+6.2f} med {x['median_cost_bps']:+6.2f} p90 {x['p90_cost_bps']:+6.2f} bps"
              f" | maker fill {x['maker_fill_rate']:.0%} wait~{x['median_wait_s'] or 0:.0f}s"
              + (f" | vs TAKER {vs['mean']:+.2f} bps CI [{vs['ci95'][0]:+.2f}, {vs['ci95'][1]:+.2f}]" if vs else '')
              + f" | lane OOS SR {impact[p]['sharpe']:+.2f} ann {impact[p]['ann_ret_pct']:+.1f}%")
    print(f"assumed 6 bps/side: lane OOS SR {impact['ASSUMED_6bps']['sharpe']:+.2f} ann {impact['ASSUMED_6bps']['ann_ret_pct']:+.1f}%")
    if a.json:
        a.json.write_text(json.dumps({'summary': summ, 'impact': impact, 'events': res}, default=float, indent=1))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
