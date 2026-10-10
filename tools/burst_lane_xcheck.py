"""Cross-check: the LIVE lane code (core/burst_lane.BurstLane, shadow) fed with one day of real aggTrades vs the
replay's simulate() on the same prints.  Same policy -> similar trade count and net bps per trade.

    python tools/burst_lane_xcheck.py <SYMBOL-aggTrades-YYYY-MM-DD.zip> [--hours 6]
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import tempfile
from dataclasses import replace
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tools'))

import burst_scalper_replay as br  # noqa: E402
from core import burst_lane as bl  # noqa: E402
from core import engine as eng  # noqa: E402
from core import micro_signals as msig  # noqa: E402


def run(path: Path, hours: float, symbol: str) -> dict:
    day = br.prep(path)
    _, _, qty, _ = br.load_day(path)
    t, px, bm, bid, ask = day['t'], day['px'], day['bm'], day['bid'], day['ask']
    end = t[0] + hours * 3600
    k = int(np.searchsorted(t, end))
    fees = br.FEES['USDC' if symbol.endswith('USDC') else 'USDT']
    rep = [r for r in br.simulate(day, 'MR', 30.0, 'through', fees) if r['t'] < end]
    clk = {'t': t[0]}
    p = replace(eng.PROFILES['demo'], symbol=symbol, burst_lane='shadow', trend_lane='off',
                maker_bps=fees[0], taker_bps=fees[1])
    feed = msig.MicroFeed(symbol, msig.config_for(symbol))
    venue = eng.MockVenue(p, quote=lambda s: eng._touch(feed))
    tmp = Path(tempfile.mkdtemp())
    e = eng.Engine(p, venue, feed, None, ledger=tmp / 'micro.jsonl')
    e.spec, e.started, e.account = dict(eng.FALLBACK_SPECS.get(symbol, eng.FALLBACK_SPECS['SOLUSDT'])), True, {'wallet': 5000.0}
    e.spec['tick'] = day['tick']
    e.event, e.features = (lambda m: None), {}
    lane = bl.BurstLane(e, tmp, clock=lambda: clk['t'])
    stream = f'{symbol.lower()}@aggTrade'

    async def go():
        nxt = t[0] + p.burst_every_s
        for i in range(k):
            while t[i] >= nxt:
                clk['t'] = nxt
                await lane.step()
                nxt += p.burst_every_s
            feed.bid, feed.ask = (bid[i - 1], ask[i - 1]) if i else (px[i] - day['tick'], px[i])
            feed.on_message({'stream': stream, 'data': {'p': str(float(px[i])), 'q': str(float(qty[i])),
                                                        'm': bool(bm[i])}}, now=float(t[i]))
    asyncio.run(go())
    f = tmp / 'micro_trades_shadow.jsonl'
    rows = [json.loads(x) for x in f.read_text().splitlines()] if f.exists() else []
    stat = lambda xs: {'n': len(xs), 'mean_net_bps': float(np.mean(xs)) if xs else None}  # noqa: E731
    return {'replay': stat([r['net_bps'] for r in rep]), 'lane_shadow': stat([r['net_bps'] for r in rows])}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    ap.add_argument('zip', type=Path)
    ap.add_argument('--hours', type=float, default=6.0)
    a = ap.parse_args(argv)
    sym = a.zip.name.split('-')[0]
    print(sym, json.dumps(run(a.zip, a.hours, sym)))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
