"""SW1: 15m/1H swing momentum (RFC-2026-Q4.4 Task 2) replayed on real Binance USD-M 1-minute klines.  Offline.

    python tools/swing_replay_sw1.py [--data data/klines_1m] [--symbols BTCUSDT,SOLUSDT,DOGEUSDT] [--json out.json]

PRE-REGISTERED 2026-10-16 (written before the first run; thresholds = the RFC's, windows = textbook defaults):
  bars      15m and 1H = complete clock-aligned groups of 1m bars (15 / 60 bars); decisions at a 15m close use
            CLOSED bars only (the last 1H bar that closed at or before it) and act from the NEXT minute.
  trend 15m UP  close15 > EMA20(15m) AND EMA20 rising over the last bar;  DOWN mirrored.
  1H        MACD(12, 26, 9) histogram > 0 AND TSMOM 24 h (close1h / close1h 24 bars earlier - 1) > 0 (UP);
            both < 0 (DOWN).
  volume    VR = quote volume of the signal 15m bar / mean of the previous 20 15m bars > 1.5.
  signal    side = +1 / -1 when 15m trend, 1H MACD and TSMOM all agree and VR > 1.5.
  entry     TK  taker at the next minute's open (fee 5 + 1 bp slippage);
            MK  post-only at the 15m close, live 15 minutes, filled only when a later 1m bar trades THROUGH it.
  exits     bracket B80   TP +80 bps (post-only, maker) / SL -30 bps (taker) -> planned R:R 2.67 (RFC);
            bracket B200  TP +200 / SL -80 bps (R:R 2.5, the RFC's "+80 to +250 bps" upper range);
            time barrier 240 min -> taker at that minute's close.  Stop before target inside a minute
            (tools/intraday_replay._exit_scan, the same conservative rule as every intraday replay).
  book      one position per symbol per variant; a new signal only after the previous exit + 1 minute.
  fees      VIP0 maker 2, taker 5 + 1 bp slippage (tools/intraday_replay MAKER / TAKER).
  splits    DEV 2025-01-01..2025-12-31 (pick the variant on pooled BTC/SOL/DOGE, n >= 30),
            HOLDOUT 2026-01-01..2026-09-30 (report).  CI: bootstrap over days (intraday_replay.summarize).
  verdict   tools/intraday_replay.eligible (fixed bracket R:R >= 2: HOLDOUT n >= 100 and CI95 lower > 0).
            A pass only allows a SHADOW lane; it never enables an order.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))
from intraday_replay import DEV, HOLD, MAKER, TAKER, _exit_scan, day_of, eligible, load_1m, summarize  # noqa: E402

VARIANTS = {f'SW1_{e}_{b}': {'entry': e, 'tp_bps': tp, 'sl_bps': sl}
            for e in ('TK', 'MK') for b, (tp, sl) in (('B80', (80.0, 30.0)), ('B200', (200.0, 80.0)))}
VR_MIN, HOLD_MIN = 1.5, 240


def ema(x: np.ndarray, n: int) -> np.ndarray:
    a, out = 2.0 / (n + 1), np.empty(len(x))
    out[0] = x[0]
    for i in range(1, len(x)):
        out[i] = out[i - 1] + a * (x[i] - out[i - 1])
    return out


def bars(m: dict, sec: int) -> dict:
    """Complete clock-aligned groups: close time, close, quote volume, index of the last 1m bar."""
    k = (m['t'] // sec).astype(np.int64)
    edge = np.flatnonzero(np.diff(k)) + 1
    starts, ends = np.r_[0, edge], np.r_[edge, len(k)]
    per = sec // 60
    full = (ends - starts) == per
    s, e = starts[full], ends[full]
    qv = np.add.reduceat(m['qv'], starts)[full]
    return {'t_close': (k[s] + 1) * sec * 1.0, 'c': m['c'][e - 1], 'qv': qv, 'i_last': e - 1}


def signals(m: dict) -> list:
    """-> [(15m bar index, side, i_last of that bar)] per the pre-registered rule."""
    b15, b1h = bars(m, 900), bars(m, 3600)
    e20 = ema(b15['c'], 20)
    macd = ema(b1h['c'], 12) - ema(b1h['c'], 26)
    hist = macd - ema(macd, 9)
    ts = np.full(len(b1h['c']), np.nan)
    ts[24:] = b1h['c'][24:] / b1h['c'][:-24] - 1
    j = np.searchsorted(b1h['t_close'], b15['t_close'], side='right') - 1      # last closed 1H bar
    out = []
    for k in range(40, len(b15['c'])):
        jj = j[k]
        if jj < 35 or not np.isfinite(ts[jj]):
            continue
        base = b15['qv'][k - 20:k].mean()
        if base <= 0 or b15['qv'][k] / base <= VR_MIN:
            continue
        up = b15['c'][k] > e20[k] and e20[k] > e20[k - 1] and hist[jj] > 0 and ts[jj] > 0
        dn = b15['c'][k] < e20[k] and e20[k] < e20[k - 1] and hist[jj] < 0 and ts[jj] < 0
        if up or dn:
            out.append((k, 1 if up else -1, int(b15['i_last'][k])))
    return out


def run_symbol(m: dict, sig: list, var: dict) -> list:
    t, n = m['t'], len(m['t'])
    trades, busy = [], -1.0
    for _, side, il in sig:
        i0 = il + 1
        if i0 >= n or t[il] + 60 <= busy:
            continue
        if var['entry'] == 'TK':
            fill, entry, fee_in = i0, m['o'][i0] * (1 + side * 0.0), TAKER
        else:
            px, fill = m['c'][il], None
            for i in range(i0, min(i0 + 15, n)):
                if (side > 0 and m['l'][i] < px) or (side < 0 and m['h'][i] > px):
                    fill = i
                    break
            if fill is None:
                continue
            entry, fee_in = px, MAKER
        tp = entry * (1 + side * var['tp_bps'] / 1e4)
        sl = entry * (1 - side * var['sl_bps'] / 1e4)
        px, how, ix, mk = _exit_scan(m, fill, side, entry, tp, sl, hold=HOLD_MIN)
        gross = side * (px - entry) / entry * 1e4
        trades.append({'t': float(t[ix]), 't_entry': float(t[fill]), 'side': side, 'gross_bps': gross,
                       'net_bps': gross - fee_in - (MAKER if mk else TAKER), 'exit': how,
                       'hold_min': (t[ix] - t[fill]) / 60 + 1})
        busy = t[ix] + 60
    return trades


def main(argv=None) -> dict:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument('--data', type=Path, default=ROOT / 'data' / 'klines_1m')
    ap.add_argument('--symbols', default='BTCUSDT,SOLUSDT,DOGEUSDT')
    ap.add_argument('--json', type=Path)
    a = ap.parse_args(argv)
    dev, hold = (day_of(DEV[0]), day_of(DEV[1])), (day_of(HOLD[0]), day_of(HOLD[1]))
    per = {v: {} for v in VARIANTS}
    for s in a.symbols.split(','):
        m = load_1m(a.data, s)
        sig = signals(m)
        for v, var in VARIANTS.items():
            per[v][s] = run_symbol(m, sig, var)
    out = {'variants': VARIANTS, 'splits': {'DEV': DEV, 'HOLDOUT': HOLD}, 'pooled': {}, 'per_symbol': {}}
    for v in VARIANTS:
        pooled = [x for s in per[v] for x in per[v][s]]
        out['pooled'][v] = {'DEV': summarize(pooled, *dev), 'HOLDOUT': summarize(pooled, *hold)}
        out['per_symbol'][v] = {s: summarize(per[v][s], *hold) for s in per[v]}
    cands = [v for v in VARIANTS if out['pooled'][v]['DEV'].get('n', 0) >= 30]
    best = max(cands, key=lambda v: out['pooled'][v]['DEV']['mean_net_bps']) if cands else None
    out['dev_best'] = best
    out['verdict'] = {s: ('SHADOW-ELIGIBLE' if best and eligible(out['per_symbol'][best][s], VARIANTS[best])
                          else 'REJECT') for s in a.symbols.split(',')}
    out['pooled_verdict'] = ('SHADOW-ELIGIBLE' if best and eligible(out['pooled'][best]['HOLDOUT'], VARIANTS[best])
                             else 'REJECT')
    if a.json:
        a.json.write_text(json.dumps(out, indent=1, default=float))
    for v in VARIANTS:
        for sp in ('DEV', 'HOLDOUT'):
            h = out['pooled'][v][sp]
            if h.get('n'):
                print(f'{v:14s} {sp:7s} n={h["n"]:5d} net={h["mean_net_bps"]:7.2f} CI95=[{h["ci95"][0]:7.2f},'
                      f'{h["ci95"][1]:7.2f}] gross={h["gross_bps"]:7.2f} win={h["win_rate"]:.2f} '
                      f'per_day={h["per_day"]:.2f} exits={h["exits"]}')
    print('DEV best', best, '| pooled HOLDOUT verdict', out['pooled_verdict'], '| per symbol', out['verdict'])
    return out


if __name__ == '__main__':
    main()
