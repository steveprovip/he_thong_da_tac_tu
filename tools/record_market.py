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

--liq (2026-11-13, liquidation study, docs/research_upgrade/plan_flash_liquidation_sniping_2026_11.md):
    /market  !forceOrder@arr (every symbol) + <sym>@aggTrade      /public  <sym>@bookTicker      (no depth, no REST)
    <out>/liquidations_<YYYYMMDD>.jsonl   one line per liquidation: {"r", "s": "forceOrder", "d": <payload>,
                                          "book": latest bookTicker of that symbol + its age_ms | null}
    <out>/<SYMBOL>/<YYYYMMDD-HH>.jsonl.gz the touch and the prints, same layout as above
The venue pushes at most ONE liquidation per symbol per 1000 ms (the latest): sums are lower bounds.
The event loop only slices each frame (no JSON round trip; liquidations are parsed to attach the touch) and hands
the line to a writer thread (queue.SimpleQueue): gzip and disk never block the loop.  --stats-every prints the
per-frame loop cost and the receive -> written lag.
"""
from __future__ import annotations

import argparse
import asyncio
import gzip
import json
import os
import queue
import shutil
import threading
import time
import urllib.request
import zlib
from collections import Counter, deque
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


LIGHT_KINDS = ('depth@100ms', 'aggTrade')       # --light: the book diffs rebuild best bid / ask, so bookTicker (36 % of the bytes) and forceOrder go
LIQ_KINDS = ('bookTicker', 'aggTrade')          # --liq: the touch and the prints of the chosen symbols ...
ALL_LIQ = '!forceOrder@arr'                     # ... plus every liquidation of the market (/market route)


def stream_url(symbols, route='public', light=False, liq=False):
    base, kinds = WS_ROUTES[route]
    keep = LIQ_KINDS if liq else (LIGHT_KINDS if light else kinds)
    parts = [f'{s.lower()}@{k}' for s in symbols for k in kinds if k in keep]
    if liq and route == 'market':
        parts.append(ALL_LIQ)
    return base + '/'.join(parts)


def hour_key(ns: int) -> str:
    return datetime.fromtimestamp(ns / 1e9, timezone.utc).strftime('%Y%m%d-%H')


def symbol_of(stream: str) -> str:
    return stream.split('@', 1)[0].upper()


FLUSH_EVERY_NS = 5_000_000_000                  # a killed run loses at most this much of each hourly file


def gzip_complete(path: Path) -> bool:
    """True when every gzip member of the file is whole (the file was closed, not cut off by a kill)."""
    d, fed = zlib.decompressobj(31), False
    with open(path, 'rb') as f:
        chunk = f.read(1 << 20)
        while chunk:
            try:
                d.decompress(chunk)
            except zlib.error:
                return False
            fed = True
            if d.eof:
                chunk, d, fed = d.unused_data, zlib.decompressobj(31), False
            else:
                chunk = b''
            chunk = chunk or f.read(1 << 20)
    return not fed


def repair_gzip(path: Path) -> bool:
    """An hourly file left by a killed run ends inside a gzip member; a member appended behind it would be unreadable
    (2026-11-14: one hard kill + restart broke all three hour files at the join). Rewrite every whole line that can
    still be decompressed as one complete member. -> True if the file was rewritten."""
    if gzip_complete(path):
        return False
    tmp = path.with_name(path.name + '.repair')
    d, tail = zlib.decompressobj(31), b''
    with open(path, 'rb') as src, gzip.open(tmp, 'wb') as dst:
        chunk = src.read(1 << 20)
        while chunk:
            try:
                text = tail + d.decompress(chunk)
            except zlib.error:
                break
            cut = text.rfind(b'\n') + 1
            dst.write(text[:cut])
            tail = text[cut:]
            chunk = d.unused_data if d.eof else b''
            if d.eof:
                d = zlib.decompressobj(31)
            chunk = chunk or src.read(1 << 20)
    os.replace(tmp, path)
    return True


class HourlyWriter:
    """One gzip file per (symbol, UTC hour); rotates on the first message of a new hour. A file that already exists
    is made whole before it is appended to, and every open file is sync-flushed every FLUSH_EVERY_NS."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.open = {}
        self.counts = {}
        self.flushed = {}
        self.repaired = 0

    def path(self, sym: str, hk: str) -> Path:
        return self.root / sym / f'{hk}.jsonl.gz'

    def write(self, sym: str, ns: int, obj: dict):
        self.write_line(sym, ns, json.dumps(obj, separators=(',', ':')) + '\n')

    def write_line(self, sym: str, ns: int, line: str):
        hk = hour_key(ns)
        cur = self.open.get(sym)
        if cur is None or cur[0] != hk:
            if cur is not None:
                cur[1].close()
            p = self.path(sym, hk)
            p.parent.mkdir(parents=True, exist_ok=True)
            if p.exists() and p.stat().st_size and repair_gzip(p):
                self.repaired += 1
            self.open[sym] = (hk, gzip.open(p, 'at', encoding='utf-8'))
            cur = self.open[sym]
        cur[1].write(line)
        if ns - self.flushed.get(sym, 0) >= FLUSH_EVERY_NS:
            cur[1].flush()                               # Z_SYNC_FLUSH: what is on disk decompresses up to here
            self.flushed[sym] = ns
        self.counts[sym] = self.counts.get(sym, 0) + 1

    def close(self):
        for _, f in self.open.values():
            f.close()
        self.open.clear()


class DailyWriter:
    """<root>/<prefix>_<YYYYMMDD>.jsonl per UTC day, line-buffered: a killed recorder loses at most the line in flight
    (the 11-10 hourly gzip of a killed run ended mid-block)."""

    def __init__(self, root: Path, prefix: str = 'liquidations'):
        self.root, self.prefix = Path(root), prefix
        self.day, self.f = None, None
        self.counts = Counter()

    def path(self, day: str) -> Path:
        return self.root / f'{self.prefix}_{day}.jsonl'

    def write_line(self, sym: str, ns: int, line: str):
        day = datetime.fromtimestamp(ns / 1e9, timezone.utc).strftime('%Y%m%d')
        if day != self.day:
            if self.f is not None:
                self.f.close()
            self.root.mkdir(parents=True, exist_ok=True)
            self.day, self.f = day, open(self.path(day), 'a', encoding='utf-8', buffering=1)
        self.f.write(line)
        self.counts[sym] += 1

    def close(self):
        if self.f is not None:
            self.f.close()
            self.f = None


class BookState:
    """Latest bookTicker per symbol as received; parsed only when a liquidation of that symbol needs it."""

    def __init__(self):
        self.last = {}

    def update(self, sym: str, ns: int, data_text: str):
        self.last[sym] = (ns, data_text)

    def snapshot(self, sym: str, ns: int):
        got = self.last.get(sym)
        if got is None:
            return None                                  # symbol not subscribed / no touch yet: UNKNOWN
        t, text = got
        try:
            d = json.loads(text)
        except ValueError:
            return None
        return {'b': d.get('b'), 'B': d.get('B'), 'a': d.get('a'), 'A': d.get('A'), 'u': d.get('u'),
                'T': d.get('T'), 'age_ms': round((ns - t) / 1e6, 3)}


def split_frame(raw: str):
    """'{"stream":"<s>","data":<d>}' -> (stream, data text) by slicing (no JSON parse); None for any other layout
    (subscription acks, errors, a changed key order), which the caller sends through record_line."""
    if not raw.startswith('{"stream":"'):
        return None
    j = raw.find('"', 11)
    if j < 0 or not raw.startswith(',"data":', j + 1) or not raw.endswith('}'):
        return None
    data = raw[j + 9:-1]
    if not data or data[0] not in '{[':
        return None
    return raw[11:j], data


class ThreadedSink:
    """Event loop -> writer thread: put() is one SimpleQueue append; the thread owns every file handle."""

    def __init__(self, hourly: HourlyWriter, daily: DailyWriter | None = None):
        self.hourly, self.daily = hourly, daily
        self.q = queue.SimpleQueue()
        self.lag_ns = deque(maxlen=200_000)              # local receive -> line handed to the file object
        self.errors = 0
        self.t = threading.Thread(target=self._run, name='recorder-writer', daemon=True)
        self.t.start()

    def put(self, dest: str, sym: str, ns: int, line: str):
        self.q.put((dest, sym, ns, line))

    def _run(self):
        while True:
            item = self.q.get()
            if item is None:
                return
            dest, sym, ns, line = item
            try:
                (self.daily if dest == 'liq' else self.hourly).write_line(sym, ns, line)
            except Exception:                            # one bad write must not kill the recording
                self.errors += 1
            self.lag_ns.append(time.time_ns() - ns)

    def close(self):
        self.q.put(None)
        self.t.join()
        self.hourly.close()
        if self.daily is not None:
            self.daily.close()


class Router:
    """The only work on the event loop per frame: slice it, keep the touch, hand the line to the sink.
    All-market streams ('!...') go to the daily liquidation file with the symbol's latest touch attached;
    per-symbol streams go to the hourly files exactly as before."""

    def __init__(self, sink: ThreadedSink, book: BookState | None = None):
        self.sink = sink
        self.book = book if book is not None else BookState()
        self.hot_ns = deque(maxlen=200_000)
        self.counts = Counter()

    def __call__(self, raw: str, ns: int) -> None:
        t0 = time.perf_counter_ns()
        sp = split_frame(raw)
        if sp is None:
            try:
                m = json.loads(raw)
            except json.JSONDecodeError:
                return
            if not isinstance(m, dict) or not m.get('stream') or 'data' not in m:
                return
            sp = (m['stream'], json.dumps(m['data'], separators=(',', ':')))
        stream, data = sp
        if stream.startswith('!'):
            try:
                d = json.loads(data)
                sym = d['o']['s']
            except (ValueError, KeyError, TypeError):
                self.counts['bad_liq'] += 1
                return
            book = self.book.snapshot(sym, ns)
            self.sink.put('liq', sym, ns, '{"r":%d,"s":"forceOrder","d":%s,"book":%s}\n'
                          % (ns, data, json.dumps(book, separators=(',', ':'))))
            self.counts['forceOrder'] += 1
        else:
            sym, kind = symbol_of(stream), stream.split('@', 1)[1]
            if kind == 'bookTicker':
                self.book.update(sym, ns, data)
            self.sink.put('h', sym, ns, '{"r":%d,"s":"%s","d":%s}\n' % (ns, kind, data))
            self.counts[kind] += 1
        self.hot_ns.append(time.perf_counter_ns() - t0)


def pct(xs, q: float):
    s = sorted(xs)
    return s[min(len(s) - 1, int(q * len(s)))] if s else None


def _copy(dq) -> list:
    for _ in range(5):                                   # the writer thread appends while we read
        try:
            return list(dq)
        except RuntimeError:
            continue
    return []


def stats_line(router: Router, sink: ThreadedSink) -> str:
    hot, lag = list(router.hot_ns), _copy(sink.lag_ns)
    us = lambda v: '—' if v is None else f'{v / 1e3:.1f}'
    ms_ = lambda v: '—' if v is None else f'{v / 1e6:.2f}'
    return (f'frames {dict(router.counts)} · loop cost p50 {us(pct(hot, 0.5))} / p99 {us(pct(hot, 0.99))} / max '
            f'{us(max(hot) if hot else None)} us · write lag p50 {ms_(pct(lag, 0.5))} / p99 {ms_(pct(lag, 0.99))} ms '
            f'· queue {sink.q.qsize()} · write errors {sink.errors}')


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


async def snapshot_loop(symbols, sink, every, stop):
    while not stop.is_set():
        for s in symbols:
            try:
                snap = await asyncio.to_thread(fetch_snapshot, s)
                ns = time.time_ns()
                line = json.dumps({'r': ns, 's': 'snapshot', 'd': snap}, separators=(',', ':'))
                sink.put('h', s, ns, line + '\n')
            except Exception as e:  # network hiccup: keep recording the streams
                print(f'snapshot {s} failed: {type(e).__name__}')
        try:
            await asyncio.wait_for(stop.wait(), timeout=every)
        except asyncio.TimeoutError:
            pass


async def stream_loop(symbols, handler, stop, route='public', light=False, liq=False):
    import websockets
    backoff = 1.0
    while not stop.is_set():
        try:
            async with websockets.connect(stream_url(symbols, route, light, liq), ping_interval=20, ping_timeout=20,
                                          max_size=2 ** 22) as ws:
                backoff = 1.0
                while not stop.is_set():
                    try:
                        raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                    except asyncio.TimeoutError:
                        continue
                    handler(raw, time.time_ns())
        except Exception as e:
            if stop.is_set():
                break
            print(f'ws {route} error {type(e).__name__}; reconnect in {backoff:.0f}s')
            await asyncio.sleep(backoff)
            backoff = min(60.0, backoff * 2)


def free_gb(path: Path) -> float:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return shutil.disk_usage(p).free / 1e9


async def run(symbols, out: Path, duration: float, snap_every: float, light: bool = False, min_free_gb: float = 0.0,
              liq: bool = False, stats_every: float = 60.0, stop_file: Path | None = None):
    hourly = HourlyWriter(out)
    sink = ThreadedSink(hourly, DailyWriter(out) if liq else None)
    router = Router(sink)
    stop = asyncio.Event()
    routes = [r for r in WS_ROUTES if stream_url(symbols, r, light, liq) != WS_ROUTES[r][0]]   # no empty socket
    tasks = [asyncio.create_task(stream_loop(symbols, router, stop, r, light, liq)) for r in routes]
    if not liq:                                       # the REST snapshot only serves the depth@100ms book rebuild
        tasks.append(asyncio.create_task(snapshot_loop(symbols, sink, snap_every, stop)))
    t0 = last = time.time()
    try:
        while not stop.is_set():
            await asyncio.sleep(1.0)
            now = time.time()
            if stats_every and now - last >= stats_every:
                last = now
                print(stats_line(router, sink), flush=True)
            if duration and now - t0 >= duration:
                stop.set()
            elif min_free_gb and free_gb(out) < min_free_gb:      # a days-long run on a nearly full disk stops itself
                print(f'free disk {free_gb(out):.1f} GB < --min-free-gb {min_free_gb:g}: stopping')
                stop.set()
            elif stop_file is not None and Path(stop_file).exists():   # a console-less run cannot get Ctrl+C: the
                print(f'stop file {stop_file}: stopping', flush=True)  # watchdog asks this way, files close cleanly
                stop.set()
    except (KeyboardInterrupt, asyncio.CancelledError):
        stop.set()
    await asyncio.gather(*tasks, return_exceptions=True)
    print(stats_line(router, sink), flush=True)
    sink.close()
    counts = dict(hourly.counts)
    if sink.daily is not None:
        counts['liquidations'] = sum(sink.daily.counts.values())
    return counts


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--symbols', default='SOLUSDT,DOGEUSDT')
    ap.add_argument('--out', type=Path, default=Path('data/recordings'))
    ap.add_argument('--duration', type=float, default=0.0, help='seconds; 0 = until Ctrl+C')
    ap.add_argument('--snapshot-every', type=float, default=600.0)
    ap.add_argument('--light', action='store_true', help='depth@100ms + aggTrade only (no bookTicker / forceOrder): ~0.43 GB/day for 3 coins')
    ap.add_argument('--min-free-gb', type=float, default=0.0, help='stop when the output disk has less free space than this (0 = off)')
    ap.add_argument('--liq', action='store_true',
                    help='liquidation study: !forceOrder@arr (all symbols) -> <out>/liquidations_<YYYYMMDD>.jsonl with the '
                         'symbol touch attached; bookTicker + aggTrade of --symbols -> hourly gz; no depth, no REST')
    ap.add_argument('--stats-every', type=float, default=60.0, help='seconds between loop-cost / write-lag lines (0 = end only)')
    ap.add_argument('--stop-file', type=Path, default=None,
                    help='stop cleanly (gzip members closed) once this file exists; used by tools/watchdog_recorder.py')
    a = ap.parse_args(argv)
    syms = [s.strip().upper() for s in a.symbols.split(',') if s.strip()]
    if a.liq and a.light:
        ap.error('--liq and --light are different stream sets: pick one')
    counts = asyncio.run(run(syms, a.out, a.duration, a.snapshot_every, a.light, a.min_free_gb, a.liq, a.stats_every,
                             a.stop_file))
    print('recorded', counts)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
