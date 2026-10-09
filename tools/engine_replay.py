"""Event-driven replay of the REAL GrokTOPUS engine on historical market data (virtual time, no network).

    python tools/engine_replay.py --source agg --data <dir> --symbols SOLUSDT,DOGEUSDT \
        --start 2026-10-01T00:00 --end 2026-10-02T00:00 [--warmup-min 120] [--set field=value ...] \
        [--out run.json] [--ledger-out positions.jsonl]

What runs is the production code path, unmodified: core.engine.Engine (swarm consensus, churn gates, alpha
concentration / scanner slot, conviction sizing, ATR brackets, MARIN soft/hard exits, ratchet, TP1) on
core.micro_signals.MicroFeed, with the engine's own MockVenue (maker/taker fees from the Profile, trade-through
fills for resting orders, taker + slip for stops).  Only three things are substituted:
  * time      a virtual clock: time.time / time.monotonic / datetime.now and the asyncio loop all read it, and
              the loop jumps straight to the next timer (an 0.2 s decide tick costs CPU, not wall time);
  * the feed  historical messages pushed into MicroFeed.on_message in timestamp order (50 ms batches);
  * the loops the Runner's decide_loop / sync_loop, re-used as is through a small shim.
No key, no REST, no WebSocket, never touches data/engine_trades.jsonl (positions go to a temp ledger).

Sources (--source):
  agg   data.binance.vision aggTrades zips (SYMBOL-aggTrades-YYYY-MM-DD.zip).  The order book is SYNTHETIC
        (touch rebuilt from trade sides, one tick wide, flat sizes): OBI / OFI / WALL / depth features are
        degenerate -> results are APPROXIMATE for book-driven gates.  Good for trade-flow logic and exits.
  l2    data/l2/<SYMBOL>/<YYYYMMDD>.jsonl (recorded bookTicker + diff depth) merged with aggTrades zips:
        real touch and near-touch depth (levels rebuilt from diffs, no snapshot).
  rec   tools/record_market.py output (<dir>/<SYMBOL>/<YYYYMMDD-HH>.jsonl.gz): depth@100ms + snapshots,
        aggTrade, forceOrder -> the most faithful source going forward.
What it cannot reproduce: the demo venue's own book/basis (mock fills on the mainnet touch), queue position,
latency/ack (instant), exchange rejects.  Treat the output as a PAIRED instrument: A vs B on the same events.

Output JSON: summary, per-position rows (net bps on notional), hourly net PnL (U) aligned on the window, wall
time and simulated hours per wall second.  Feed the hourly series or position bps to tools/edge_check.py
--paired or tools/ab_bench.py.
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import gzip
import heapq
import io
import json
import math
import sys
import tempfile
import time as _time
import zipfile
from datetime import datetime as _dt, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

STEP_S = 0.05                 # feed batching granularity (live: depth5@100ms, decide 0.2 s)
BOOK_EMIT_S = 0.1             # synthetic / rebuilt book pushed at most every 100 ms (like depth5@100ms)
LOCAL_TZ_H = 7                # the live engine keys day_pnl on local time (Asia/Saigon)


# ============================================================================ virtual time
class VClock:
    """Virtual clock = base + rel.  The asyncio loop works on `rel` (small floats keep asyncio's 1 ns timer
    resolution exact); time.time / time.monotonic / datetime.now read base + rel."""
    base = 0.0
    rel = 0.0
    t = 0.0                                   # cached base + rel (read by everything else)

    @classmethod
    def set_rel(cls, rel: float) -> None:
        cls.rel = rel
        cls.t = cls.base + rel

    @classmethod
    def set_abs(cls, t: float) -> None:
        if t > cls.t:
            cls.set_rel(t - cls.base)

    def __call__(self) -> float:
        return VClock.t


VCLOCK = VClock()
_REAL = {'time': _time.time, 'monotonic': _time.monotonic}


def install_clock(t0: float) -> None:
    """Patch the process clock.  Call BEFORE building engines (defaults captured at import are overridden
    explicitly in build())."""
    VClock.base = float(t0)
    VClock.set_rel(0.0)
    _time.time = VCLOCK
    _time.monotonic = VCLOCK


def restore_clock() -> None:
    _time.time = _REAL['time']
    _time.monotonic = _REAL['monotonic']


class VirtualLoop(asyncio.SelectorEventLoop):
    """asyncio loop on VClock: when nothing is ready it jumps to the next timer instead of sleeping."""

    def time(self) -> float:
        return VClock.rel

    def _run_once(self):
        sch = self._scheduled
        while sch and sch[0]._cancelled:              # a cancelled head would make select() sleep for real
            heapq.heappop(sch)
            self._timer_cancelled_count -= 1
        if not self._ready and sch and sch[0]._when > VClock.rel:
            VClock.set_rel(sch[0]._when)
        super()._run_once()


class VDatetime(_dt):
    @classmethod
    def now(cls, tz=None):
        if tz is not None:
            return _dt.fromtimestamp(VClock.t, tz)
        return _dt.fromtimestamp(VClock.t, timezone.utc).replace(tzinfo=None) + \
            __import__('datetime').timedelta(hours=LOCAL_TZ_H)


# ============================================================================ data
def parse_ts(s: str) -> float:
    fmt = '%Y-%m-%dT%H:%M' if 'T' in s else '%Y-%m-%d'
    return _dt.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp()


def days_between(t0: float, t1: float):
    d = _dt.fromtimestamp(t0, timezone.utc).date()
    end = _dt.fromtimestamp(t1 - 1e-6, timezone.utc).date()
    while d <= end:
        yield d
        d = d.fromordinal(d.toordinal() + 1)


def load_agg(data: Path, symbol: str, t0: float, t1: float):
    """-> list of (t_s, price, qty, buyer_is_maker) inside [t0, t1)."""
    out = []
    for d in days_between(t0, t1):
        z = data / f'{symbol}-aggTrades-{d.isoformat()}.zip'
        if not z.exists():
            continue
        with zipfile.ZipFile(z) as zf:
            for name in zf.namelist():
                with zf.open(name) as fh:
                    for r in csv.reader(io.TextIOWrapper(fh, encoding='utf-8')):
                        if not r or not r[0].isdigit():
                            continue
                        ts = int(r[5])
                        t = ts / 1000.0 if ts < 10 ** 14 else ts / 1e6
                        if t0 <= t < t1:
                            out.append((t, float(r[1]), float(r[2]), r[6].strip().lower() == 'true'))
    out.sort(key=lambda x: x[0])
    return out


class SyntheticBook:
    """Touch rebuilt from trade sides: a sell aggressor prints on the bid, a buy aggressor on the ask."""

    def __init__(self, tick: float, size: float = 1.0):
        self.tick, self.size = tick, size
        self.bid = self.ask = None

    def on_trade(self, px: float, buyer_maker: bool) -> None:
        if buyer_maker:
            self.bid = px
            if self.ask is None or self.ask <= px:
                self.ask = px + self.tick
        else:
            self.ask = px
            if self.bid is None or self.bid >= px:
                self.bid = px - self.tick

    def depth(self, levels: int = 10):
        if self.bid is None:
            return None
        t = self.tick
        return {'b': [[f'{self.bid - i * t:.8f}', f'{self.size}'] for i in range(levels)],
                'a': [[f'{self.ask + i * t:.8f}', f'{self.size}'] for i in range(levels)]}


class DiffBook:
    """Price-level book from diff-depth updates (+ optional snapshot), touch overridden by bookTicker."""

    def __init__(self):
        self.b, self.a = {}, {}
        self.bt = None

    def snapshot(self, d: dict) -> None:
        self.b = {float(p): float(q) for p, q in d.get('bids', []) if float(q) > 0}
        self.a = {float(p): float(q) for p, q in d.get('asks', []) if float(q) > 0}

    def diff(self, d: dict) -> None:
        for side, book in (('b', self.b), ('a', self.a)):
            for p, q in d.get(side, []):
                p, q = float(p), float(q)
                if q <= 0:
                    book.pop(p, None)
                else:
                    book[p] = q

    def ticker(self, d: dict) -> None:
        self.bt = (float(d['b']), float(d['B']), float(d['a']), float(d['A']))

    def depth(self, levels: int = 10):
        if self.bt:
            bb, bq, ba, aq = self.bt
        elif self.b and self.a:
            bb, ba = max(self.b), min(self.a)
            bq, aq = self.b[bb], self.a[ba]
        else:
            return None
        bids = [[bb, bq]] + [[p, self.b[p]] for p in sorted((p for p in self.b if p < bb), reverse=True)[:levels - 1]]
        asks = [[ba, aq]] + [[p, self.a[p]] for p in sorted(p for p in self.a if p > ba)[:levels - 1]]
        return {'b': [[f'{p:.8f}', f'{q}'] for p, q in bids], 'a': [[f'{p:.8f}', f'{q}'] for p, q in asks]}


def agg_msg(symbol: str, px: float, q: float, m: bool, t: float) -> dict:
    return {'stream': f'{symbol.lower()}@aggTrade',
            'data': {'e': 'aggTrade', 's': symbol, 'p': repr(px), 'q': repr(q), 'm': m, 'T': int(t * 1000)}}


def depth_msg(symbol: str, d: dict) -> dict:
    return {'stream': f'{symbol.lower()}@depth10@100ms', 'data': d}


def events_agg(data: Path, symbols, t0: float, t1: float, ticks: dict):
    """Merged (t, symbol, msg) stream: trades + a synthetic book emitted <= every 100 ms per symbol."""
    per = {s: load_agg(data, s, t0, t1) for s in symbols}
    books = {s: SyntheticBook(ticks[s]) for s in symbols}
    last_emit = {s: -1e18 for s in symbols}
    import heapq
    stream = heapq.merge(*[[(t, s, px, q, m) for (t, px, q, m) in per[s]] for s in symbols])
    for t, s, px, q, m in stream:
        b = books[s]
        b.on_trade(px, m)
        if t - last_emit[s] >= BOOK_EMIT_S:
            last_emit[s] = t
            yield t, s, depth_msg(s, b.depth())
        yield t, s, agg_msg(s, px, q, m, t)
    return per


def events_l2(data: Path, l2_dir: Path, symbols, t0: float, t1: float):
    """Recorded bookTicker + diff depth (data/l2) merged with aggTrades; book emitted <= every 100 ms."""
    import heapq
    streams = []
    for s in symbols:
        recs = []
        for d in days_between(t0, t1):
            f = l2_dir / s / f'{d.strftime("%Y%m%d")}.jsonl'
            if not f.exists():
                continue
            with open(f, encoding='utf-8') as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    t = (r.get('E') or r.get('T') or 0) / 1000.0 or r.get('lt')
                    if t0 <= t < t1:
                        recs.append((t, s, 'tk' if r.get('ch') == 'bookTicker' else 'dd', r))
        trades = [(t, s, 'tr', (px, q, m)) for (t, px, q, m) in load_agg(data, s, t0, t1)]
        recs.sort(key=lambda x: x[0])
        streams.append(heapq.merge(recs, trades, key=lambda x: x[0]))
    books = {s: DiffBook() for s in symbols}
    last_emit = {s: -1e18 for s in symbols}
    for t, s, kind, r in heapq.merge(*streams, key=lambda x: x[0]):
        b = books[s]
        if kind == 'tr':
            yield t, s, agg_msg(s, r[0], r[1], r[2], t)
            continue
        if kind == 'tk':
            b.ticker(r)
        else:
            b.diff(r)
        if t - last_emit[s] >= BOOK_EMIT_S:
            dp = b.depth()
            if dp:
                last_emit[s] = t
                yield t, s, depth_msg(s, dp)


def events_rec(rec_dir: Path, symbols, t0: float, t1: float):
    """tools/record_market.py hourly gz files: snapshot + depth@100ms diffs rebuilt; aggTrade / forceOrder as is."""
    import heapq

    def one(s):
        book = DiffBook()
        last = -1e18
        for f in sorted((rec_dir / s).glob('*.jsonl.gz')):
            with gzip.open(f, 'rt', encoding='utf-8') as fh:
                for line in fh:
                    try:
                        r = json.loads(line)
                    except ValueError:
                        continue
                    t = r['r'] / 1e9
                    if t < t0 - 600 or t >= t1:
                        continue
                    k, d = r['s'], r['d']
                    if k == 'snapshot':
                        book.snapshot(d)
                    elif k.startswith('depth'):
                        book.diff(d)
                    elif k == 'bookTicker':
                        book.ticker(d)
                    elif t >= t0 and k in ('aggTrade', 'forceOrder'):
                        yield t, s, {'stream': f'{s.lower()}@{k}', 'data': d}
                        continue
                    if t >= t0 and t - last >= BOOK_EMIT_S:
                        dp = book.depth()
                        if dp:
                            last = t
                            yield t, s, depth_msg(s, dp)
    yield from heapq.merge(*[one(s) for s in symbols], key=lambda x: x[0])


# ============================================================================ engine harness
def parse_sets(items):
    out = {}
    for it in items or ():
        k, _, v = it.partition('=')
        v = v.strip()
        try:
            out[k.strip()] = json.loads(v)
        except ValueError:
            out[k.strip()] = v
    return out


class _Shim:
    """Just enough of Runner for its decide_loop / sync_loop / cadence to run unchanged."""

    def __init__(self, eng_mod, engines: dict):
        self.engines = engines
        self.engine = next(iter(engines.values()))
        self.decide_loop = eng_mod.Runner.decide_loop.__get__(self)
        self.sync_loop = eng_mod.Runner.sync_loop.__get__(self)
        self.cadence = eng_mod.Runner.cadence.__get__(self)
        self.idle = eng_mod.Runner.idle.__get__(self)
        self.autonomy = None
        if hasattr(eng_mod.Runner, 'autonomy_loop'):
            self.autonomy_loop = eng_mod.Runner.autonomy_loop.__get__(self)


def build(symbols, overrides: dict, ledger: Path, balance: float):
    from dataclasses import replace
    from core import engine as eng
    from core import micro_signals as msig
    eng.datetime = VDatetime
    feeds = {s: msig.MicroFeed(s, msig.config_for(s)) for s in symbols}
    profiles = {}
    for s in symbols:
        p = replace(eng.PROFILES['demo'], symbol=s, qty=eng.SYMBOL_QTY.get(s, 0.0), mock_balance=balance)
        if hasattr(p, 'micro_standalone'):             # 10-09 dd: the replay exists to test the micro lane ->
            p = replace(p, micro_standalone=True, trend_lane='paper')   # ON unless --set says otherwise
        profiles[s] = replace(p, **{k: v for k, v in overrides.items() if hasattr(p, k)})
    unknown = [k for k in overrides if not hasattr(profiles[symbols[0]], k)]
    if unknown:
        raise SystemExit(f'unknown Profile fields: {unknown}')
    first = symbols[0]
    venue = eng.MockVenue(profiles[first], quote=lambda s: eng._touch(feeds.get(s)))

    async def _specs():
        return {s: dict(eng.FALLBACK_SPECS[s]) for s in eng.FALLBACK_SPECS}
    venue.specs = _specs                               # no exchangeInfo REST in replay
    es = {}
    risk = eng.RiskBook()
    for s in symbols:
        e = eng.Engine(profiles[s], venue, feeds[s], None, ledger=ledger, risk=risk, clock=VCLOCK)
        e.consensus.clock = VCLOCK                     # default captured time.monotonic at import
        if hasattr(e.authority, 'clock'):
            e.authority.clock = VCLOCK
        e.funnel.clock = VCLOCK
        es[s] = e
    aut = None
    if any(getattr(e.p, 'autonomy', 'off') != 'off' for e in es.values()):
        from core.autonomy import Autonomy                 # 10-09 (cc): learner/bandit/dreaming in the replay too,
        d = Path(ledger).parent                            # state in the temp dir, never data/ or config/
        aut = Autonomy(d / 'data', d / 'config', Path(ledger), clock=VCLOCK, seed=7)
        aut.attach(es)
    return eng, es, venue, feeds, aut


async def run_async(events, symbols, overrides, t0, t1, warmup_s, ledger: Path, balance: float, progress=False):
    eng, es, venue, feeds, aut = build(symbols, overrides, ledger, balance)
    shim = _Shim(eng, es)
    shim.autonomy = aut
    trade_from = t0 + warmup_s
    for e in es.values():
        await e.start()
        e.halted = 'REPLAY WARMUP'
    tasks = [asyncio.ensure_future(shim.decide_loop(e)) for e in es.values()]
    tasks += [asyncio.ensure_future(shim.sync_loop(e)) for e in es.values()]
    if aut is not None:
        tasks.append(asyncio.ensure_future(shim.autonomy_loop(threaded=False)))
    day_key = None
    n_msgs = 0
    it = iter(events)
    nxt = next(it, None)
    t_wall = _time.perf_counter()
    warm = True
    while nxt is not None or VClock.t < t1:
        horizon = VClock.t + STEP_S
        while nxt is not None and nxt[0] <= horizon:
            t, s, msg = nxt
            feeds[s].on_message(msg, now=max(t, VClock.t))
            n_msgs += 1
            nxt = next(it, None)
        if warm and VClock.t >= trade_from:
            warm = False
            for e in es.values():
                e.halted = ''
        local_day = int((VClock.t + LOCAL_TZ_H * 3600) // 86400)
        if day_key != local_day:                      # a fresh trading day resets the shared daily-loss meter
            rb = next(iter(es.values())).risk         # (the engine does it itself since 10-09; older code not)
            if day_key is not None and not hasattr(rb, 'roll'):
                rb.day_pnl = 0.0
            day_key = local_day
        if nxt is None and VClock.t >= t1:
            break
        await asyncio.sleep(max(0.0, min(horizon, t1) - VClock.t) or STEP_S)
        if VClock.t >= t1 and nxt is None:
            break
    for e in es.values():                              # close what is still open at the end of the window
        if e.pos:
            try:
                await e.close('REPLAY-END')
                await e.sync()
            except Exception:
                pass
    for t in tasks:
        t.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    wall = _time.perf_counter() - t_wall
    return {'messages': n_msgs, 'wall_s': wall, 'events': {s: list(e.events)[:5] for s, e in es.items()},
            'autonomy': aut.view() if aut is not None else None,
            'funnel': {s: getattr(e.funnel, 'view', lambda: {})() for s, e in es.items()}}


def positions_from_ledger(ledger: Path):
    rows = []
    if ledger.exists():
        for line in ledger.read_text(encoding='utf-8').splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            notional = float(r.get('qty') or 0) * float(r.get('entry') or 0)
            rows.append({'t': r['t'], 'symbol': r['symbol'], 'side': r['side'], 'qty': r['qty'],
                         'entry': r['entry'], 'pnl': r['pnl'], 'gross': r['gross'], 'fee': r['fee'],
                         'bps': r['pnl'] / notional * 1e4 if notional else 0.0, 'reason': r['reason'],
                         'hold_s': r.get('hold_s'), 'path': r.get('path'), 'score': r.get('score'),
                         'entry_type': r.get('entry_type')})
    return rows


def hourly(rows, t0: float, t1: float):
    n = max(1, int(math.ceil((t1 - t0) / 3600)))
    out = [0.0] * n
    for r in rows:
        k = min(n - 1, max(0, int((r['t'] - t0) // 3600)))
        out[k] += r['pnl']
    return out


def summarize(rows):
    if not rows:
        return {'n': 0}
    bps = [r['bps'] for r in rows]
    wins = sum(1 for r in rows if r['pnl'] > 0)
    gp = sum(r['pnl'] for r in rows if r['pnl'] > 0)
    gl = -sum(r['pnl'] for r in rows if r['pnl'] < 0)
    m = sum(bps) / len(bps)
    sd = (sum((b - m) ** 2 for b in bps) / (len(bps) - 1)) ** 0.5 if len(bps) > 1 else float('nan')
    return {'n': len(rows), 'net_u': sum(r['pnl'] for r in rows), 'gross_u': sum(r['gross'] for r in rows),
            'fee_u': sum(r['fee'] for r in rows), 'win_rate': wins / len(rows),
            'pf': gp / gl if gl else float('inf'), 'mean_bps': m, 'sd_bps': sd,
            'se_bps': sd / len(bps) ** 0.5 if len(bps) > 1 else float('nan'),
            'by_reason': {k: sum(1 for r in rows if r['reason'] == k) for k in sorted({r['reason'] for r in rows})}}


def replay(source: str, data: Path, symbols, t0: float, t1: float, overrides=None, warmup_s: float = 7200.0,
           l2_dir: Path = None, rec_dir: Path = None, balance: float = 5000.0, ledger_out: Path = None):
    """Run one variant in this process.  Returns the result dict (positions, hourly PnL, summary, speed)."""
    from core import engine as eng
    ticks = {s: eng.FALLBACK_SPECS[s]['tick'] for s in symbols}
    load_from = t0
    if source == 'agg':
        ev = events_agg(data, symbols, load_from, t1, ticks)
    elif source == 'l2':
        ev = events_l2(data, l2_dir or ROOT / 'data' / 'l2', symbols, load_from, t1)
    elif source == 'rec':
        ev = events_rec(rec_dir or ROOT / 'data' / 'recordings', symbols, load_from, t1)
    else:
        raise SystemExit(f'unknown source {source}')
    tmp = Path(tempfile.mkdtemp(prefix='replay_'))
    ledger = tmp / 'trades.jsonl'
    loop = VirtualLoop()
    install_clock(t0)
    dt_saved = eng.datetime
    pol = asyncio.get_event_loop_policy()
    prev_loop = getattr(getattr(pol, '_local', None), '_loop', None)
    try:
        asyncio.set_event_loop(loop)
        info = loop.run_until_complete(run_async(ev, symbols, overrides or {}, t0, t1, warmup_s, ledger, balance))
    finally:
        loop.close()
        if prev_loop is not None and not prev_loop.is_closed():
            asyncio.set_event_loop(prev_loop)          # callers (pytest, scripts) keep their own loop
        else:
            asyncio.set_event_loop(asyncio.new_event_loop())
        restore_clock()
        eng.datetime = dt_saved                      # the module global is shared with every other engine user
    rows = positions_from_ledger(ledger)
    if ledger_out:
        Path(ledger_out).write_text(ledger.read_text(encoding='utf-8') if ledger.exists() else '', encoding='utf-8')
    sim_h = (t1 - t0) / 3600
    return {'source': source, 'approx_book': source == 'agg', 'symbols': list(symbols),
            'start': _dt.fromtimestamp(t0, timezone.utc).isoformat(), 'end': _dt.fromtimestamp(t1, timezone.utc).isoformat(),
            'warmup_s': warmup_s, 'overrides': overrides or {}, 'code': eng.CODE_VERSION,
            'summary': summarize(rows), 'positions': rows, 'hourly_pnl_u': hourly(rows, t0, t1),
            'messages': info['messages'], 'wall_s': info['wall_s'], 'autonomy': info.get('autonomy'),
            'sim_hours_per_wall_s': sim_h / info['wall_s'] if info['wall_s'] else None}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--source', choices=('agg', 'l2', 'rec'), default='agg')
    ap.add_argument('--data', type=Path, default=ROOT / 'data' / 'replay_cache' / 'agg_zips')
    ap.add_argument('--l2-dir', type=Path, default=ROOT / 'data' / 'l2')
    ap.add_argument('--rec-dir', type=Path, default=ROOT / 'data' / 'recordings')
    ap.add_argument('--symbols', default='SOLUSDT,DOGEUSDT')
    ap.add_argument('--start', required=True)
    ap.add_argument('--end', required=True)
    ap.add_argument('--warmup-min', type=float, default=120.0)
    ap.add_argument('--balance', type=float, default=5000.0)
    ap.add_argument('--set', action='append', default=[], metavar='FIELD=VALUE', help='Profile override (JSON value)')
    ap.add_argument('--out', type=Path)
    ap.add_argument('--ledger-out', type=Path)
    ap.add_argument('--code-root', type=Path, help='import core/ from this checkout (e.g. a git worktree of the old '
                                                    'commit) -> A/B of two code versions on the same events')
    ap.add_argument('--quiet', action='store_true', help='silence engine event prints')
    a = ap.parse_args(argv)
    if a.code_root:
        sys.path.insert(0, str(a.code_root.resolve()))
        for m in [m for m in sys.modules if m == 'core' or m.startswith('core.')]:
            del sys.modules[m]
    if a.quiet:
        import builtins
        _p = builtins.print
        builtins.print = lambda *x, **k: None if (x and isinstance(x[0], str) and x[0].startswith('[')) else _p(*x, **k)
    syms = [s.strip().upper() for s in a.symbols.split(',') if s.strip()]
    res = replay(a.source, a.data, syms, parse_ts(a.start), parse_ts(a.end), parse_sets(a.set), a.warmup_min * 60,
                 a.l2_dir, a.rec_dir, a.balance, a.ledger_out)
    s = res['summary']
    print(f"replay {res['source']}{' (APPROX book)' if res['approx_book'] else ''} {','.join(syms)} "
          f"{res['start'][:16]} -> {res['end'][:16]} | msgs {res['messages']:,} | wall {res['wall_s']:.1f}s "
          f"({res['sim_hours_per_wall_s']:.2f} sim-h per s)")
    if s['n']:
        print(f"positions {s['n']} | net {s['net_u']:+.4f} U (gross {s['gross_u']:+.4f}, fee {s['fee_u']:.4f}) | "
              f"WR {s['win_rate']:.1%} PF {s['pf']:.2f} | mean {s['mean_bps']:+.2f} bps se {s['se_bps']:.2f}")
        print('exits', s['by_reason'])
    else:
        print('positions 0')
    if a.out:
        a.out.write_text(json.dumps(res, indent=1, default=str))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
