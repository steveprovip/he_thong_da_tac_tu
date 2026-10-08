"""Mainnet market-data recorder for exact replay (public streams only: no keys, no orders, no engine).

    python tools/record_market.py [--symbols SOLUSDT,DOGEUSDT] [--out data/recordings] [--duration 0]
                                  [--snapshot-every 600]

Why: data.binance.vision stopped publishing USD-M bookTicker after 2024-03-30 and never had diff-depth, so a
maker fill model cannot be replayed on recent days.  This writes, per symbol and UTC hour,
    <out>/<SYMBOL>/<YYYYMMDD-HH>.jsonl.gz
one JSON object per line: {"r": <local receive ns>, "s": <stream>, "d": <exchange payload>} for
    <sym>@depth@100ms   (diff depth: U/u/pu update ids)
    <sym>@aggTrade
    <sym>@bookTicker
    <sym>@forceOrder    (liquidation prints: no longer downloadable from data.binance.vision)
plus a REST depth snapshot ({"s": "snapshot", ...}, limit 1000) at start and every --snapshot-every seconds,
so the book can be rebuilt from any hour file.  --duration 0 = run until Ctrl+C.  Reconnects with back-off.
Raw recordings are local data: keep them out of git (data/recordings/ is ignored).
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import time
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

# Binance split the USD-M market streams by route (same split as core/micro_signals.py): book streams live on
# /public, trade-type streams on /market.  The legacy root /stream silently drops aggTrade.
WS_ROUTES = {
    'public': ('wss://fstream.binance.com/public/stream?streams=', ('depth@100ms', 'bookTicker')),
    'market': ('wss://fstream.binance.com/market/stream?streams=', ('aggTrade', 'forceOrder')),
}
REST_DEPTH = 'https://fapi.binance.com/fapi/v1/depth?symbol={sym}&limit=1000'
STREAMS = tuple(k for _, ks in WS_ROUTES.values() for k in ks)


def stream_url(symbols, route='public'):
    base, kinds = WS_ROUTES[route]
    return base + '/'.join(f'{s.lower()}@{k}' for s in symbols for k in kinds)


def hour_key(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).strftime('%Y%m%d-%H')


def symbol_of(stream: str) -> str:
    return stream.split('@', 1)[0].upper()


class HourlyWriter:
    """One gzip file per (symbol, UTC hour); rotates on the first message of a new hour."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.open = {}
        self.counts = {}

    def path(self, sym: str, hk: str) -> Path:
        return self.root / sym / f'{hk}.jsonl.gz'

    def write(self, sym: str, ns: int, obj: dict):
        hk = hour_key(ns)
        cur = self.open.get(sym)
        if cur is None or cur[0] != hk:
            if cur is not None:
                cur[1].close()
            p = self.path(sym, hk)
            p.parent.mkdir(parents=True, exist_ok=True)
            self.open[sym] = (hk, gzip.open(p, 'at', encoding='utf-8'))
            cur = self.open[sym]
        cur[1].write(json.dumps(obj, separators=(',', ':')) + '\n')
        self.counts[sym] = self.counts.get(sym, 0) + 1

    def close(self):
        for _, f in self.open.values():
            f.close()
        self.open.clear()


def record_line(raw: str, ns: int):
    """Combined-stream frame -> (symbol, record) or None."""
    try:
        m = json.loads(raw)
    except json.JSONDecodeError:
        return None
    s = m.get('stream')
    if not s or 'data' not in m:
        return None
    return symbol_of(s), {'r': ns, 's': s.split('@', 1)[1], 'd': m['data']}


def fetch_snapshot(sym: str, timeout=10):
    with urllib.request.urlopen(REST_DEPTH.format(sym=sym), timeout=timeout) as resp:
        return json.loads(resp.read().decode())


async def snapshot_loop(symbols, writer, every, stop):
    while not stop.is_set():
        for s in symbols:
            try:
                snap = await asyncio.to_thread(fetch_snapshot, s)
                writer.write(s, time.time_ns(), {'r': time.time_ns(), 's': 'snapshot', 'd': snap})
            except Exception as e:  # network hiccup: keep recording the streams
                print(f'snapshot {s} failed: {type(e).__name__}')
        try:
            await asyncio.wait_for(stop.wait(), timeout=every)
        except asyncio.TimeoutError:
            pass


async def stream_loop(symbols, writer, stop, route='public'):
    import websockets
    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(stream_url(symbols, route), ping_interval=20, ping_timeout=20,
                                          max_size=2 ** 22) as ws:
                backoff = 1.0
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    rec = record_line(raw, time.time_ns())
                    if rec:
                        writer.write(rec[0], rec[1]['r'], rec[1])
        except Exception as e:
            if stop.is_set():
                break
            print(f'ws {route} error {type(e).__name__}; reconnect in {backoff:.0f}s')
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)


async def run(symbols, out: Path, duration: float, snap_every: float):
    writer = HourlyWriter(out)
    stop = asyncio.Event()
    tasks = [asyncio.create_task(stream_loop(symbols, writer, stop, r)) for r in WS_ROUTES]
    tasks.append(asyncio.create_task(snapshot_loop(symbols, writer, snap_every, stop)))
    t0 = time.time()
    try:
        while not stop.is_set():
            await asyncio.sleep(5)
            if duration and time.time() - t0 >= duration:
                stop.set()
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    writer.close()
    return writer.counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--symbols', default='SOLUSDT,DOGEUSDT')
    ap.add_argument('--out', type=Path, default=Path('data/recordings'))
    ap.add_argument('--duration', type=float, default=0.0, help='seconds; 0 = until Ctrl+C')
    ap.add_argument('--snapshot-every', type=float, default=600.0)
    a = ap.parse_args(argv)
    syms = [s.strip().upper() for s in a.symbols.split(',') if s.strip()]
    counts = asyncio.run(run(syms, a.out, a.duration, a.snapshot_every))
    print('recorded', counts)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
