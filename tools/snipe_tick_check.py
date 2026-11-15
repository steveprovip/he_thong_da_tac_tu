"""CCX-1 addendum A1 (2026-11-14, after the frozen run): how fast does the price move against the funding receiver
after a settlement? Public REST, no keys, no orders.

    python tools/snipe_tick_check.py [--refresh-funding] [--out data/snipe_tick_check.json]

/fapi/v1/aggTrades serves a time window only for the most recent 2 days (-4166 otherwise), so the sample is the
settlements of the last ~46 hours whose PREVIOUS print was >= 15 bps (signal P of the plan). For each one: the last
trade before T, the first trade after T, and the last trade at or before T + L for a ladder of delays L. Moves are
in bps in favour of the side that receives the funding. Not pre-registered: it measures the one assumption the
frozen X0 cell rests on (a taker exit at the first print after T).
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research import carry_convex_study as g  # noqa: E402

BASE = 'https://fapi.binance.com'
DELAYS_MS = (0, 50, 100, 150, 250, 500, 1000, 2000, 5000, 10000, 30000, 60000)
WINDOW_MS = 46 * 3_600_000                               # inside the venue's 2-day limit with a margin


def get(url: str):
    for k in range(5):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={'User-Agent': 'research'}), timeout=30) as r:
                return json.load(r), int(r.headers.get('X-MBX-USED-WEIGHT-1M') or 0)
        except urllib.error.HTTPError as e:
            if e.code in (418, 429):
                time.sleep(float(e.headers.get('Retry-After') or 30) + 5 * k)
            elif e.code == 400:
                return None, 0
            elif k == 4:
                raise
            else:
                time.sleep(2 + 3 * k)
        except Exception:
            if k == 4:
                raise
            time.sleep(2 + 3 * k)
    return None, 0


def ladder(trades: list, t_settle: int, side: int, delays=DELAYS_MS) -> dict | None:
    """trades: [(time ms, price)] sorted. -> moves in bps in favour of `side`, measured from the last trade before T.
    Delay 0 = the first trade at or after T. None when there is no trade before T or none after it."""
    pre = [p for t, p in trades if t < t_settle]
    post = [(t, p) for t, p in trades if t >= t_settle]
    if not pre or not post:
        return None
    p0 = pre[-1]
    out = {'first_delay_ms': post[0][0] - t_settle, 'n_100ms': sum(t - t_settle <= 100 for t, _ in post),
           'n_1s': sum(t - t_settle <= 1000 for t, _ in post), 'moves': {}}
    for d in delays:
        if d == 0:
            px = post[0][1]
        else:
            seen = [p for t, p in post if t - t_settle <= d]
            px = seen[-1] if seen else p0                # no print yet: the last traded price still stands
        out['moves'][str(d)] = side * (px / p0 - 1.0) * 1e4
    return out


def refresh(universe: Path, symbols: list, now_ms: int) -> None:
    """Append the prints after each cached file's last row (one request per symbol, 0.65 s apart)."""
    for sym in symbols:
        p = universe / f'fr_{sym}.json'
        rows = json.loads(p.read_text())
        start = int(rows[-1][0]) + 60_000 if rows else now_ms - WINDOW_MS
        page, _ = get(f'{BASE}/fapi/v1/fundingRate?symbol={quote(sym)}&startTime={start}&endTime={now_ms}&limit=1000')
        time.sleep(0.65)
        if page:
            rows += [[int(round(int(x['fundingTime']) / 60000)) * 60000, float(x['fundingRate']) * 1e4] for x in page]
            p.write_text(json.dumps(rows))


def recent_events(universe: Path, now_ms: int) -> list:
    out = []
    for p in sorted(universe.glob('fr_*.json')):
        rows = json.loads(p.read_text())
        if len(rows) < 2:
            continue
        a = np.array(sorted(set(map(tuple, rows))), dtype=np.float64)
        for t, prev, rate in g.find_events(a[:, 0].astype(np.int64), a[:, 1]):
            if prev is not None and abs(prev) >= g.THETA_BPS and now_ms - WINDOW_MS <= t <= now_ms - 120_000:
                out.append((p.stem[3:], t, prev, rate))
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('--refresh-funding', action='store_true')
    ap.add_argument('--out', type=Path, default=ROOT / 'data' / 'snipe_tick_check.json')
    a = ap.parse_args(argv)
    srv, _ = get(f'{BASE}/fapi/v1/time')
    now = int(srv['serverTime'])
    if a.refresh_funding:
        hot = sorted({s for s, es in g.load_events().items() if any(e[0] >= g.ms('2026-10-01') for e in es)})
        refresh(g.UNIVERSE, hot, now)
    rows = []
    for sym, t, prev, rate in recent_events(g.UNIVERSE, now):
        page, weight = get(f'{BASE}/fapi/v1/aggTrades?symbol={quote(sym)}&startTime={t - 10_000}&endTime={t + 60_000}&limit=1000')
        if weight > 1500:
            time.sleep(20)
        if not page:
            continue
        trades = [(int(x['T']), float(x['p'])) for x in page]
        if len(page) == 1000 and trades[-1][0] < t + 60_000:            # truncated before T + 60 s: read on from T
            more, _ = get(f'{BASE}/fapi/v1/aggTrades?symbol={quote(sym)}&startTime={t}&endTime={t + 60_000}&limit=1000')
            trades = sorted(set(trades + [(int(x['T']), float(x['p'])) for x in (more or [])]))
        side = 1 if prev < 0 else -1
        lad = ladder(trades, t, side)
        if lad is not None:
            rows.append({'sym': sym, 't': t, 'prev': prev, 'rate': rate, 'side': side, 'funding': -side * rate,
                         'truncated': len(trades) >= 1000 and trades[-1][0] < t + 60_000, **lad})
    res = {'server_ms': now, 'window_h': WINDOW_MS / 3_600_000, 'events': len(rows), 'rows': rows, 'summary': summarize(rows)}
    a.out.write_text(json.dumps(res, indent=1))
    print(json.dumps(res['summary'], indent=1))
    return 0


def summarize(rows: list) -> dict:
    if not rows:
        return {'n': 0}
    f = np.array([r['funding'] for r in rows])
    out = {'n': len(rows), 'symbols': len({r['sym'] for r in rows}), 'funding_mean': float(f.mean()),
           'funding_median': float(np.median(f)), 'first_delay_ms_median': float(np.median([r['first_delay_ms'] for r in rows])),
           'trades_first_100ms_median': float(np.median([r['n_100ms'] for r in rows])),
           'trades_first_1s_median': float(np.median([r['n_1s'] for r in rows])), 'moves': {}}
    for d in DELAYS_MS:
        x = np.array([r['moves'][str(d)] for r in rows])
        out['moves'][str(d)] = {'mean': float(x.mean()), 'median': float(np.median(x)),
                                'se': float(x.std(ddof=1) / np.sqrt(x.size)) if x.size > 1 else None,
                                'share_of_funding': float(x.mean() / f.mean()) if f.mean() else None}
    return out


if __name__ == '__main__':
    sys.exit(main())
