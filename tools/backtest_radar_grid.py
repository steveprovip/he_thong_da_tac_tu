"""Side-by-side backtest of Alpha Radar configurations on the SAME real Binance history.

    python tools/backtest_radar_grid.py                  # 30 most liquid USDT perps x ~21 days of 5m
    python tools/backtest_radar_grid.py --symbols 40 --pages 6 --refresh

Every signal is sized by research.alpha_radar.trade_plan() exactly as the live
auto-trader sizes it (stop, TP ladder, fees, maker/taker, slippage). Entry on the
next bar open (MARKET) or a maker limit at the retest zone (RETEST, 1 bar TTL).
The 1h EMA50 trend gate is applied from the 5m series itself (causal); the 4h
gate is live-only.

Read the OOS column, not the best row: picking the winner among N configs on the
same few weeks is itself a fit. A config is only worth demo money when its
out-of-sample mean is > 0 AND the pooled lower-95 % bound is > 0.

Output: console table + artifacts/radar_grid/<stamp>.json/.md. Klines are cached
in data/kline_cache/ (1 h) so re-runs are reproducible and cheap. Public data only.
"""
import argparse
import json
import sys
import time
from dataclasses import replace
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research import alpha_radar as ar  # noqa: E402

CACHE = ROOT / 'data' / 'kline_cache'
OUT = ROOT / 'artifacts' / 'radar_grid'
NOTIONAL = 5.1

V3 = ar.RadarConfig()
V1 = replace(V3, surge_min=3.0, taker_long=0.55, taker_short=0.45, require_taker=False, breakout_lookback=48,
             sl_mode='money', max_loss_usdt=0.05, tp_pcts=(2.0, 5.0), hold_bars=48)
GRID = {
    'v1 (surge3, 4h brk, SL 0.05U money, TP 2/5)': V1,
    'v3 (surge5, taker60, 24h brk, SL 2.0xATR, TP>=2.5R)': V3,
    'v3 SL 1.5xATR': replace(V3, atr_sl_mult=1.5),
    'v3 filters + v1 money stop (isolates the filter)': replace(V3, sl_mode='money', max_loss_usdt=0.05,
                                                                 tp_pcts=(2.0, 5.0), hold_bars=48),
    'v1 filters + v3 ATR stop (isolates the stop)': replace(V1, sl_mode='atr', max_loss_usdt=0.15,
                                                             tp_pcts=(4.5, 8.0), hold_bars=144),
}


def load_series(symbols, pages, refresh, http=ar._get_json):
    CACHE.mkdir(parents=True, exist_ok=True)
    series = {}
    for s in symbols:
        f = CACHE / f'{s}_5m_{pages}p.json'
        try:
            if not refresh and time.time() - f.stat().st_mtime < 3600:
                series[s] = json.loads(f.read_text(encoding='utf-8'))
                continue
        except (OSError, ValueError):
            pass
        try:
            bars = ar.fetch_history(s, http, '5m', 1500, pages)
        except Exception as exc:
            print(f'  ! {s}: {type(exc).__name__}')
            continue
        f.write_text(json.dumps(bars), encoding='utf-8')
        series[s] = bars
        time.sleep(0.2)                                     # stay far below the 2400/min weight cap
    return series


def fmt_row(name, entry, st, oos):
    if not st.get('n'):
        return (f'{name[:52]:<52} {entry:<7} sig {st.get("signals", 0):>4} skip {st.get("skipped_risk", 0):>4} '
                f'n    0  (no trades)')
    l95 = st.get('lower95_pct')
    l95_s = '   n/a' if l95 is None else f'{l95:+6.2f}%'
    oos_s = '  n/a' if not oos.get('n') else '%+5.2f%% n=%d' % (oos['mean_net_pct'], oos['n'])
    return (f'{name[:52]:<52} {entry:<7} sig {st["signals"]:>4} skip {st["skipped_risk"]:>4} n {st["n"]:>4} '
            f'TP1 {st["tp_rate"]:>4.0%} TP2 {st["tp2_rate"]:>4.0%} SL {st["sl_rate"]:>4.0%} '
            f'mean {st["mean_net_pct"]:+6.2f}% ({st["mean_net_pct"] / 100 * NOTIONAL:+.4f}U) '
            f'L95 {l95_s} OOS {oos_s}')


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--symbols', type=int, default=30)
    ap.add_argument('--pages', type=int, default=4, help='x1500 5m bars per symbol (4 ~ 21 days)')
    ap.add_argument('--refresh', action='store_true')
    a = ap.parse_args(argv)
    t0 = time.time()
    try:
        specs = ar.parse_specs(ar._get_json(f'{ar.FAPI}/fapi/v1/exchangeInfo'))
        tick = ar._get_json(f'{ar.FAPI}/fapi/v1/ticker/24hr')
    except Exception as exc:
        print(f'[LOI] khong ket noi duoc fapi.binance.com: {type(exc).__name__}: {exc}')
        return 1
    liquid = [t['symbol'] for t in sorted((t for t in tick if t.get('symbol') in specs),
                                          key=lambda t: -float(t.get('quoteVolume', 0)))][:a.symbols]
    print(f'Loading {len(liquid)} symbols x {a.pages * 1500} bars (5m) ...')
    series = load_series(liquid, a.pages, a.refresh)
    days = max((len(b) for b in series.values()), default=0) * 5 / 1440
    print(f'{len(series)} symbols, ~{days:.1f} days each. Running {len(GRID)} configs x 2 entries ...\n')
    results, lines = {}, []
    for name, cfg in GRID.items():
        r = ar.study_symbols(series, cfg, specs)
        key = next(iter(r['by_tp']))
        results[name] = r
        for entry, st in (('MARKET', r['by_tp'][key]), ('RETEST', r['by_tp_retest'][key])):
            oos = r['split']['oos'] if entry == 'MARKET' else {}
            line = fmt_row(name, entry, st, oos)
            lines.append(line)
            print(line)
    OUT.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime('%Y%m%d-%H%M')
    (OUT / f'{stamp}.json').write_text(json.dumps(results, default=str, indent=1), encoding='utf-8')
    md = ['# Radar grid backtest ' + stamp, '',
          f'{len(series)} symbols · ~{days:.1f} days of 5m · sized by trade_plan() · fees+slippage included', '',
          '```'] + lines + ['```', '',
          'Promote a config to demo only if MARKET OOS mean > 0 and pooled L95 > 0. '
          'The best in-sample row across several configs is expected to look better than it is.']
    (OUT / f'{stamp}.md').write_text('\n'.join(md) + '\n', encoding='utf-8')
    print(f'\n-> {OUT / (stamp + ".md")}  ({time.time() - t0:.0f}s)')
    print('Rule: demo only if OOS mean > 0 AND L95 > 0. Pick-the-best-row on one sample is overfitting.')


if __name__ == '__main__':
    main()
