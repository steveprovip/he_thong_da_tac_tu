"""Fast lane runner: liquidation sniper (shadow) + Polymarket 15m fair-value scanner.

    python -m research.fast_runner                  # until Ctrl+C
    python -m research.fast_runner --seconds 600 --cascade 50000 --no-pm

Publishes data/fast_state.json every second (served at /api/fast on :8765).
Logs: data/liq_sniper_trades.jsonl, data/pm15_signals.jsonl. Public data only:
no API key is read, no order is sent to Binance or Polymarket.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import time
from pathlib import Path

from research import alpha_radar as ar
from research import fast_liquidation_sniper as fls
from research import polymarket_15m_fast_arb as pm

ROOT = Path(__file__).resolve().parents[1]
STATE = ROOT / 'data' / 'fast_state.json'
TRADES = ROOT / 'data' / 'liq_sniper_trades.jsonl'
PM_LOG = ROOT / 'data' / 'pm15_signals.jsonl'
SHUTDOWN = ROOT / 'data' / 'shutdown_request.json'
WS_URL = 'wss://fstream.binance.com/stream?streams=' + fls.WS_STREAMS


def _safe(o):
    if isinstance(o, float):
        return o if math.isfinite(o) else None
    if isinstance(o, dict):
        return {str(k): _safe(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_safe(v) for v in o]
    return o


def write_state(path: Path, state: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix('.json.tmp')
    tmp.write_text(json.dumps(_safe(state), ensure_ascii=False, allow_nan=False), encoding='utf-8')
    os.replace(tmp, path)


def append(path: Path, row: dict) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'a', encoding='utf-8') as fh:
            fh.write(json.dumps(_safe(row)) + '\n')
    except OSError:
        pass


def build_state(sniper: fls.Sniper, pm_res: dict, ledger: pm.SignalLedger, ws: dict, now: float) -> dict:
    return {'updated_at': now, 'ws': dict(ws), 'sniper': sniper.snapshot(now),
            'pm15': {'rows': pm_res.get('rows', []), 'errors': pm_res.get('errors', []),
                     't': pm_res.get('t'), 'score': ledger.score(), 'settled': ledger.settled[-10:][::-1]}}


async def pump(sniper: fls.Sniper, ws_stats: dict, url: str = WS_URL) -> None:
    import websockets
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20, open_timeout=8,
                                          max_size=2 ** 23, max_queue=4096) as ws:
                ws_stats.update(connected=True, error='')
                backoff = 1.0
                async for raw in ws:
                    now = time.time()
                    ws_stats['frames'] = ws_stats.get('frames', 0) + 1
                    try:
                        msg = json.loads(raw)
                    except ValueError:
                        continue
                    cas = sniper.on_message(msg, now)
                    if isinstance(msg, dict) and 'forceOrder' in str(msg.get('stream', '')):
                        ev = fls.parse_force_order(msg, now)
                        if ev and ev['notional'] >= sniper.cfg.big_usd:
                            print(f'[{time.strftime("%H:%M:%S")}] LIQ {ev["symbol"]:<14} {ev["liquidated"]:<5} '
                                  f'${ev["notional"]:>10,.0f} @ {ev["price"]:g}')
                    if cas:
                        print(f'[{time.strftime("%H:%M:%S")}] CASCADE {cas["symbol"]} {cas["liquidated"]} '
                              f'${cas["usd_window"]:,.0f}/{sniper.cfg.window_s:g}s x{cas["count"]} '
                              f'wick {cas["move_pct"] if cas["move_pct"] is None else round(cas["move_pct"], 2)}% '
                              f'-> {cas["action"]}')
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            ws_stats.update(connected=False, error=f'{type(exc).__name__}: {exc}'[:160])
        ws_stats['reconnects'] = ws_stats.get('reconnects', 0) + 1
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 15.0)


async def run(args) -> None:
    cfg = fls.SniperConfig(cascade_usd=args.cascade, tp_pct=args.tp, max_loss=args.max_loss,
                           min_drop_pct=args.min_wick, max_hold_s=args.hold)
    try:
        specs = ar.parse_specs(await asyncio.to_thread(ar._get_json, f'{ar.FAPI}/fapi/v1/exchangeInfo'))
    except Exception as exc:
        print(f'[LOI] exchangeInfo: {type(exc).__name__}: {exc} - sizing disabled, feed still shown')
        specs = {}
    sniper = fls.Sniper(cfg, specs)
    pm_cfg = pm.Pm15Config(min_edge=args.pm_edge)
    ledger = pm.SignalLedger(PM_LOG)
    pm_res: dict = {'rows': [], 'errors': []}
    ws: dict = {'connected': False}
    print('=' * 88)
    print(f'  FAST LANE (shadow, 0 orders) · cascade >= ${cfg.cascade_usd:,.0f}/{cfg.window_s:g}s · wick >= '
          f'{cfg.min_drop_pct}% · TP +{cfg.tp_pct}% · max loss {cfg.max_loss} U · hold {cfg.max_hold_s:g}s')
    print(f'  Polymarket 15m: {"OFF" if args.no_pm else ", ".join(pm_cfg.assets)} · edge >= {pm_cfg.min_edge:.2f} after fee')
    print('  UI http://127.0.0.1:8765 · forceOrder = max 1 liquidation/symbol/second (sizes are lower bounds)')
    print('=' * 88)
    feed = asyncio.create_task(pump(sniper, ws))
    t0, wall0 = time.monotonic(), time.time()
    next_pm = next_pub = 0.0
    pm_task = None
    try:
        while True:
            now = time.time()
            t = sniper.step(now)
            if t:
                append(TRADES, t)
                print(f'[{time.strftime("%H:%M:%S")}] EXIT {t["symbol"]} {t["reason"]} {t["net_pct"]:+.2f}% '
                      f'({t["pnl"]:+.4f} U) {t["hold_s"]:.0f}s · session {sniper.summary()["pnl_usdt"]:+.4f} U')
            if not args.no_pm:
                if pm_task is not None and pm_task.done():
                    try:
                        pm_res = pm_task.result()
                        for r in pm_res['rows']:
                            if ledger.record(r):
                                print(f'[{time.strftime("%H:%M:%S")}] PM15 {r["slug"]} {r["side"]} @ {r["price"]:.2f} '
                                      f'fair {r["p_up"] if r["side"] == "UP" else 1 - r["p_up"]:.2f} '
                                      f'edge {r["edge"] * 100:+.1f}pp tau {r["tau_s"]:.0f}s')
                        for s in await asyncio.to_thread(ledger.settle, now, pm.binance_close()):
                            print(f'[{time.strftime("%H:%M:%S")}] PM15 SETTLED {s["slug"]} {s["side"]} '
                                  f'{"WIN" if s["won"] else "LOSS"} {s["roi"] * 100:+.0f}%')
                    except Exception as exc:
                        pm_res = {'rows': [], 'errors': [f'{type(exc).__name__}: {exc}'[:160]]}
                    pm_task = None
                if pm_task is None and now >= next_pm:
                    pm_task = asyncio.create_task(asyncio.to_thread(pm.scan_once, pm_cfg))
                    next_pm = now + args.pm_every
            if now >= next_pub:
                write_state(STATE, build_state(sniper, pm_res, ledger, ws, now))
                next_pub = now + 1.0
            try:
                if SHUTDOWN.stat().st_mtime >= wall0:
                    print('[STOP] dashboard emergency stop')
                    break
            except OSError:
                pass
            if args.seconds and time.monotonic() - t0 >= args.seconds:
                break
            await asyncio.sleep(0.25)
    finally:
        feed.cancel()
        if pm_task is not None:
            pm_task.cancel()
        write_state(STATE, build_state(sniper, pm_res, ledger, ws, time.time()))
        s = sniper.summary()
        print(f'\n  liquidations {sniper.stats["liquidations"]} (${sniper.stats["liq_usd"]:,.0f}) · cascades '
              f'{sniper.stats["cascades"]} · shadow trades {s["n"]} · pnl {s["pnl_usdt"]:+.4f} U'
              + (f' · mean {s["mean_net_pct"]:+.2f}%' if s['mean_net_pct'] is not None else '')
              + f' · PM15 settled {ledger.score()}')


def main(argv=None):
    ap = argparse.ArgumentParser(description='Liquidation sniper + Polymarket 15m scanner (shadow)')
    ap.add_argument('--seconds', type=float, default=0)
    ap.add_argument('--cascade', type=float, default=50_000.0, help='USD liquidated on one side within 5 s')
    ap.add_argument('--min-wick', type=float, default=0.4, help='% move against the liquidated side in 30 s')
    ap.add_argument('--tp', type=float, default=1.0)
    ap.add_argument('--hold', type=float, default=60.0)
    ap.add_argument('--max-loss', type=float, default=0.05)
    ap.add_argument('--pm-edge', type=float, default=0.05)
    ap.add_argument('--pm-every', type=float, default=3.0)
    ap.add_argument('--no-pm', action='store_true')
    args = ap.parse_args(argv)
    try:
        asyncio.run(run(args))
    except KeyboardInterrupt:
        print('\n[STOP] Ctrl+C. Shadow only: nothing to close on any venue.')


if __name__ == '__main__':
    main()
