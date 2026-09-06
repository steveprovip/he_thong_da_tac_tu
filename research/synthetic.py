"""A synthetic Binance-shaped feed, so the whole research tier is testable offline.

This exists to prove the plumbing, never to produce a result. A synthetic tape has
whatever edge its generator was given, so any PnL measured on it is an artefact.
Every report built from this feed is stamped SYNTHETIC for that reason.
"""
import math
import random

import numpy as np
from hftbacktest import BUY_EVENT, DEPTH_EVENT, EXCH_EVENT, LOCAL_EVENT, SELL_EVENT, TRADE_EVENT
from hftbacktest.types import event_dtype

MS = 1_000_000
US = 1_000


def synthetic_feed(seconds=1800, tick_size=0.10, lot_size=0.001, start_price=68000.0,
                   depth_interval_ms=100, feed_latency_us=2500, levels=5,
                   volatility=0.00025, trade_rate=6.0, sweep_run=4.0, seed=7,
                   start_ns=1_700_000_000_000_000_000):
    """Depth snapshots plus heavy-tailed aggTrades arriving in same-side bursts."""
    rng = random.Random(seed)
    rows = []
    price = start_price
    side_left, aggressor = 0, True
    steps = int(seconds * 1000 / depth_interval_ms)
    for step in range(steps):
        timestamp = start_ns + step * depth_interval_ms * MS
        local = timestamp + feed_latency_us * US
        price *= math.exp(rng.gauss(0, volatility))
        bid_tick = math.floor(price / tick_size) - 1
        ask_tick = bid_tick + 2
        for level in range(levels):
            quantity = round(0.4 + rng.random() * 3.0, 3)
            rows.append((EXCH_EVENT | LOCAL_EVENT | DEPTH_EVENT | BUY_EVENT, timestamp, local,
                         (bid_tick - level) * tick_size, quantity, 0, 0, 0.0))
            quantity = round(0.4 + rng.random() * 3.0, 3)
            rows.append((EXCH_EVENT | LOCAL_EVENT | DEPTH_EVENT | SELL_EVENT, timestamp, local,
                         (ask_tick + level) * tick_size, quantity, 0, 0, 0.0))
        for _ in range(int(rng.expovariate(1 / trade_rate)) if trade_rate else 0):
            if side_left <= 0:
                side_left = max(1, int(rng.expovariate(1 / sweep_run)))
                aggressor = rng.random() < .5
            side_left -= 1
            quantity = max(lot_size, round(rng.lognormvariate(math.log(.02) - 1.8, 1.9), 3))
            trade_tick = ask_tick if aggressor else bid_tick
            rows.append((EXCH_EVENT | LOCAL_EVENT | TRADE_EVENT | (BUY_EVENT if aggressor else SELL_EVENT),
                         timestamp + rng.randint(1, depth_interval_ms) * MS,
                         local + rng.randint(1, depth_interval_ms) * MS,
                         trade_tick * tick_size, quantity, 0, 0, 0.0))
    data = np.array(rows, dtype=event_dtype)
    data.sort(order='exch_ts', kind='stable')
    return data
