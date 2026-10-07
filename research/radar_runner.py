"""Daily Alpha Radar runner: Binance surge-breakout + Polymarket event radar -> UI + report.

    python -m research.radar_runner                 # one pass
    python -m research.radar_runner --loop 300      # refresh every 5 min
    python -m research.radar_runner --no-backtest --no-events

Writes data/alpha_radar.json (served by core/web_ui_server.py at /api/radar) and a
markdown report artifacts/alpha_radar/<YYYYMMDD-HHMM>.md. Read-only: no keys, no orders.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path
from typing import Callable, Optional

from research import alpha_radar as ar
from research import event_radar as er

ROOT = Path(__file__).resolve().parents[1]
STATE_FILE = ROOT / 'data' / 'alpha_radar.json'
BACKTEST_CACHE = ROOT / 'data' / 'alpha_radar_backtest.json'
PREDICTION_LOG = ROOT / 'data' / 'event_predictions.jsonl'
REPORT_DIR = ROOT / 'artifacts' / 'alpha_radar'
BACKTEST_TTL_S = 6 * 3600


def _safe(obj):
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {str(k): _safe(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_safe(v) for v in obj]
    return obj


def write_atomic(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(_safe(data), allow_nan=False, ensure_ascii=False), encoding='utf-8')
    os.replace(tmp, path)


def cached_backtest(radar_cfg: ar.RadarConfig, http: Callable, symbols, specs,
                    cache: Path = BACKTEST_CACHE, ttl: float = BACKTEST_TTL_S) -> Optional[dict]:
    try:
        old = json.loads(cache.read_text(encoding='utf-8'))
        if time.time() - old.get('ts', 0) < ttl and 'split' in old:   # old schema -> recompute
            return old
    except (OSError, ValueError):
        pass
    bt = ar.backtest(symbols, radar_cfg, http, specs=specs)
    write_atomic(cache, bt)
    return bt


def run_once(radar_cfg: ar.RadarConfig = ar.RadarConfig(), event_cfg: er.EventConfig = er.EventConfig(),
             http_binance: Callable = ar._get_json, http_poly: Callable = er._get_json,
             do_events: bool = True, do_backtest: bool = True, backtest_symbols: int = 20,
             state_file: Path = STATE_FILE, report_dir: Optional[Path] = REPORT_DIR,
             prediction_log: Optional[Path] = PREDICTION_LOG, backtest_cache: Path = BACKTEST_CACHE) -> dict:
    out = {'ts': time.time(), 'radar': None, 'backtest': None, 'events': None, 'errors': []}
    try:
        out['radar'] = ar.scan(radar_cfg, http_binance)
    except Exception as exc:
        out['errors'].append(f'binance: {type(exc).__name__}: {exc}'[:200])
    if do_backtest and out['radar'] is not None:
        try:
            specs = ar.parse_specs(http_binance(f'{ar.FAPI}/fapi/v1/exchangeInfo'))
            tick = http_binance(f'{ar.FAPI}/fapi/v1/ticker/24hr')
            liquid = [t['symbol'] for t in sorted(
                (t for t in tick if t.get('symbol') in specs), key=lambda t: -float(t.get('quoteVolume', 0)))]
            out['backtest'] = cached_backtest(radar_cfg, http_binance, liquid[:backtest_symbols], specs,
                                              cache=backtest_cache)
        except Exception as exc:
            out['errors'].append(f'backtest: {type(exc).__name__}: {exc}'[:200])
    if do_events:
        try:
            out['events'] = er.scan(event_cfg, http_poly)
            if prediction_log is not None:
                er.log_predictions(out['events'], prediction_log)
        except Exception as exc:
            out['errors'].append(f'polymarket: {type(exc).__name__}: {exc}'[:200])
    out['updated_at'] = time.time()
    write_atomic(state_file, out)
    if report_dir is not None:
        report_dir.mkdir(parents=True, exist_ok=True)
        (report_dir / time.strftime('%Y%m%d-%H%M.md')).write_text(report_md(out), encoding='utf-8')
    return out


def _lbl(tp) -> str:
    return 'LADDER TP1->BE->TP2' if tp == 'ladder' else f'TP +{tp}%'


def _pct(v, d=2):
    return '—' if v is None else f'{v * 100:.{d}f}%'


def report_md(o: dict) -> str:
    L = [f'# Daily Alpha Radar — {time.strftime("%Y-%m-%d %H:%M", time.localtime(o["ts"]))}', '',
         '> Read-only scan. Không phải lời khuyên đầu tư. Tín hiệu chưa hiệu chuẩn; xem base rate lịch sử.', '']
    r = o.get('radar')
    L += ['## 1. Volume Surge Breakout (Binance USD-M, 5m)', '']
    if not r:
        L.append('_Không lấy được dữ liệu Binance._')
    else:
        L.append(f'Quét {r["scanned"]}/{r["universe"]} cặp · {r["fired"]} tín hiệu đủ điều kiện · {r["elapsed_s"]:.0f}s')
        L.append('')
        if not r['picks']:
            L.append('_Không có cặp nào đạt surge ≥ 3x + breakout + RSI. Không vào lệnh là một kết quả hợp lệ._')
        for p in r['picks']:
            pl = p['plan']
            L.append(f'### {p["symbol"]} · {p["side"]} · score {p["score"]:.1f}')
            L.append(f'- Volume {p["surge"]:.1f}x trong {p["window_min"]} phút · giá {p["ret_pct"]:+.2f}% · '
                     f'RSI {p["rsi"]:.0f} · taker {p["taker"]:.2f} · OBI {p.get("obi") if p.get("obi") is None else round(p["obi"], 2)}')
            L.append(f'- Vào ~{pl["entry"]:.6g} · SL {pl["sl"]:.6g} (-{pl["sl_pct"]:.2f}%, mất {pl["loss_usdt"]:.3f} U ≈ '
                     f'{pl["loss_vnd"]:,.0f} đ) · {pl["qty"]:g} @ {pl["leverage"]}x, notional {pl["notional"]:.2f} U')
            for tp in pl['tps']:
                L.append(f'- TP +{tp["tp_pct"]:g}% @ {tp["price"]:.6g} → +{tp["pnl_usdt"]:.3f} U ≈ {tp["pnl_vnd"]:,.0f} đ '
                         f'(R:R {tp["rr"]:.1f})')
            rt, rp = p.get('retest'), p.get('retest_plan')
            if rt and rp:
                L.append(f'- RETEST (mặc định auto-trader): chờ mua @ {rt["price"]:.6g} (lùi {rt["pullback_pct"]:.2f}%, '
                         f'mốc {rt["anchor"]}) · SL {rp["sl"]:.6g} · TP +2% {rp["tps"][0]["price"]:.6g}')
            if p.get('trend'):
                L.append('- Trend: ' + ' · '.join(f'{k} {v}' for k, v in p['trend'].items()))
            for w in pl['warnings'] + (['CHASE: đã chạy quá xa / RSI cực trị'] if p['chase'] else []):
                L.append(f'- ⚠ {w}')
            L.append('')
    bt = o.get('backtest')
    if bt:
        L += ['### Base rate lịch sử (cùng tín hiệu, cùng SL theo vốn)', '']
        L.append('MARKET (vào giá mở nến sau):')
        for tp, s in bt['by_tp'].items():
            if s.get('n'):
                L.append(f'- {_lbl(tp)}: n={s["n"]} · chạm TP {_pct(s["tp_rate"], 0)} · SL {_pct(s["sl_rate"], 0)} · '
                         f'hết giờ {_pct(s["time_rate"], 0)} · TB net {s["mean_net_pct"]:+.2f}%/lệnh'
                         + (f' · cận dưới 95% {s["lower95_pct"]:+.2f}%' if s.get('lower95_pct') is not None else ''))
            else:
                L.append(f'- {_lbl(tp)}: chưa có mẫu')
        if bt.get('by_tp_retest'):
            L.append('RETEST (limit maker tại vùng hồi, sống 1 nến 5m):')
            for tp, s in bt['by_tp_retest'].items():
                if s.get('n'):
                    L.append(f'- {_lbl(tp)}: khớp {_pct(s["fill_rate"], 0)} của {s["signals"]} tín hiệu · n={s["n"]} · '
                             f'chạm TP {_pct(s["tp_rate"], 0)} · SL {_pct(s["sl_rate"], 0)} · TB net {s["mean_net_pct"]:+.2f}%/lệnh'
                             + (f' · cận dưới 95% {s["lower95_pct"]:+.2f}%' if s.get('lower95_pct') is not None else ''))
                else:
                    L.append(f'- {_lbl(tp)}: chưa có mẫu khớp')
        sp = bt.get('split')
        if sp and sp['oos'].get('n'):
            L.append(f'- Out-of-sample (30% tín hiệu mới nhất, MARKET): n={sp["oos"]["n"]} · TB net '
                     f'{sp["oos"]["mean_net_pct"]:+.2f}%/lệnh · in-sample {sp["is"].get("mean_net_pct", 0):+.2f}%')
        L.append('')
    e = o.get('events')
    L += ['## 2. Polymarket Event Radar', '']
    if not e:
        L.append('_Không lấy được dữ liệu Polymarket._')
    else:
        L.append(f'{e["events"]} sự kiện · {e["markets"]} thị trường')
        L += ['', '### Arbitrage (negRisk)', '']
        L += [f'- {a["type"]} · {a["event"]} · {a["legs"]} chân · lời khoá {a["edge"] * 100:.1f}¢/bộ · '
              + '; '.join(a['warnings']) for a in e['arbs']] or ['_Không có._']
        L += ['', '### Model edge (crypto threshold, lognormal, chưa hiệu chuẩn)', '']
        L += [f'- {x["question"]} → mua {x["side"]} @ {x["price"]:.2f} · p_model {x["p_model"]:.2f} · edge '
              f'{x["edge"] * 100:+.1f}pp · vol thực {x["sigma"] * 100:.0f}% vs vol ngụ ý '
              f'{_pct(x["implied_vol"], 0)} · {x["shares"]} cổ ≈ {x["stake"]:.2f}$'
              for x in e['edges']] or ['_Không có kèo vượt ngưỡng edge ≥ 5 điểm % sau phí._']
        L += ['', '### Macro watch (chỉ xem odds, không có edge)', '']
        L += [f'- {m["question"]} · YES {m["yes"] if m["yes"] is None else round(m["yes"] * 100)}% · '
              f'24h vol ${m["volume24h"]:,.0f}' for m in e['macro']] or ['_Không có._']
    if o.get('errors'):
        L += ['', '## Lỗi', ''] + [f'- {x}' for x in o['errors']]
    return '\n'.join(L) + '\n'


def console(o: dict) -> str:
    lines = [f'[{time.strftime("%H:%M:%S")}] ALPHA RADAR']
    r = o.get('radar')
    if r:
        lines.append(f'  Binance: {r["scanned"]} scanned, {r["fired"]} fired')
        for p in r['picks']:
            pl = p['plan']
            lines.append(f'   ★ {p["symbol"]:<14} {p["side"]:<5} vol {p["surge"]:.1f}x/{p["window_min"]}m '
                         f'{p["ret_pct"]:+.2f}% RSI {p["rsi"]:.0f} | SL -{pl["sl_pct"]:.2f}% '
                         f'TP +2% {pl["tps"][0]["pnl_usdt"]:+.3f}U / +5% {pl["tps"][-1]["pnl_usdt"]:+.3f}U'
                         + (' ⚠' if pl['warnings'] or p['chase'] else '')
                         + (f' | retest @ {p["retest"]["price"]:.6g} (-{p["retest"]["pullback_pct"]:.2f}% {p["retest"]["anchor"]})'
                            if p.get('retest') else ''))
        if not r['picks']:
            lines.append('   (no surge breakout right now; top watch: '
                         + ', '.join(f'{w["symbol"]} {w["surge"]:.1f}x' for w in r['watchlist'][:3]) + ')')
    bt = o.get('backtest')
    if bt:
        for tp, s in bt['by_tp'].items():
            if s.get('n'):
                lines.append(f'  Base rate MARKET {_lbl(tp)}: n={s["n"]} TP {s["tp_rate"]:.0%} SL {s["sl_rate"]:.0%} '
                             f'mean {s["mean_net_pct"]:+.2f}%')
        for tp, s in (bt.get('by_tp_retest') or {}).items():
            if s.get('n'):
                lines.append(f'  Base rate RETEST {_lbl(tp)}: fill {s["fill_rate"]:.0%} n={s["n"]} TP {s["tp_rate"]:.0%} '
                             f'SL {s["sl_rate"]:.0%} mean {s["mean_net_pct"]:+.2f}%')
    e = o.get('events')
    if e:
        lines.append(f'  Polymarket: {e["markets"]} markets | arbs {len(e["arbs"])} | model edges {len(e["edges"])}')
        for x in e['edges'][:3]:
            lines.append(f'   • {x["question"][:70]} → {x["side"]} @ {x["price"]:.2f} edge {x["edge"] * 100:+.1f}pp')
    for err in o.get('errors', []):
        lines.append(f'  ! {err}')
    return '\n'.join(lines)


def main(argv=None):
    ap = argparse.ArgumentParser(description='Daily Alpha Radar (read-only)')
    ap.add_argument('--loop', type=float, default=0, help='seconds between passes (0 = once)')
    ap.add_argument('--no-events', action='store_true')
    ap.add_argument('--no-backtest', action='store_true')
    ap.add_argument('--surge', type=float, default=3.0)
    ap.add_argument('--stake', type=float, default=2.0)
    a = ap.parse_args(argv)
    rc = ar.RadarConfig(surge_min=a.surge)
    ec = er.EventConfig(stake_usdt=a.stake)
    while True:
        o = run_once(rc, ec, do_events=not a.no_events, do_backtest=not a.no_backtest)
        print(console(o))
        print(f'  -> {STATE_FILE}  ·  UI http://127.0.0.1:8765  ·  report {REPORT_DIR}')
        if not a.loop:
            break
        try:
            time.sleep(a.loop)
        except KeyboardInterrupt:
            break


if __name__ == '__main__':
    main()
