"""Convert Binance public USDM futures data (bookDepth + aggTrades) to hftbacktest .npz format.

Binance public bookDepth schema:
    timestamp, percentage, depth (cumulative base qty), notional (cumulative quote qty)
    - negative percentage = bid side (below mid)
    - positive percentage = ask side (above mid)
    - depth is CUMULATIVE from touch outward

Price reconstruction:
    price_at_level = mid_price * (1 + percentage/100)
    where mid_price is interpolated from the nearest aggTrade before the snapshot.
    Marginal depth = cumulative_depth[level] - cumulative_depth[level-1].

Limitations (documented, not hidden):
- bookDepth timestamps are minute-resolution -> depth events are sparse.
- Mid price is interpolated from aggTrades (nearest trade before snapshot).
- No local timestamp -> feed_latency_ms is added as constant offset.
- Cumulative-to-marginal conversion assumes levels are sorted by percentage.
- Crossed books are impossible by construction (bid < mid < ask by percentage sign).

Usage:
    python research/convert_binance_public.py \\
        --depth data/raw/SOLUSDT-bookDepth-2026-09-05.zip \\
        --trades data/raw/SOLUSDT-aggTrades-2026-09-05.zip \\
        --out data/npz/SOLUSDT-2026-09-05.npz \\
        --tick-size 0.01 \\
        --feed-latency-ms 5.0
"""
from __future__ import annotations

import argparse
import csv
import io
import math
import sys
import zipfile
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hftbacktest import BUY_EVENT, DEPTH_EVENT, EXCH_EVENT, LOCAL_EVENT, SELL_EVENT, TRADE_EVENT
from hftbacktest.data.utils.binancehistmktdata import correct_event_order, correct_local_timestamp
from hftbacktest.types import event_dtype

NS = 1_000_000  # ms -> ns


def _open_csv(path: str) -> io.TextIOWrapper:
    p = Path(path)
    if p.suffix.lower() == ".zip":
        z = zipfile.ZipFile(p)
        name = z.namelist()[0]
        return io.TextIOWrapper(z.open(name), encoding="utf-8", newline="")
    return open(p, encoding="utf-8", newline="")


def _parse_timestamp_ms(ts_str: str) -> int:
    ts_str = ts_str.strip()
    if " " in ts_str or (len(ts_str) > 4 and "-" in ts_str[:10]):
        from datetime import datetime, timezone
        dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
        dt = dt.replace(tzinfo=timezone.utc)
        return int(dt.timestamp() * 1000)
    return int(ts_str)


def read_trade_prices(trades_path: str) -> list:
    """Read aggTrades and return sorted list of (timestamp_ms, price)."""
    prices = []
    with _open_csv(trades_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_ms = int(row["transact_time"])
            price = float(row["price"])
            prices.append((ts_ms, price))
    prices.sort()
    return prices


def interpolate_mid(trade_prices: list, ts_ms: int) -> float:
    """Return the price of the nearest trade at or before ts_ms."""
    lo, hi = 0, len(trade_prices) - 1
    result = trade_prices[0][1]
    while lo <= hi:
        mid = (lo + hi) // 2
        if trade_prices[mid][0] <= ts_ms:
            result = trade_prices[mid][1]
            lo = mid + 1
        else:
            hi = mid - 1
    return result


def read_depth_events(depth_path: str, trade_prices: list,
                      tick_size: float, feed_latency_ms: float) -> list:
    """Read bookDepth CSV and emit DEPTH_EVENT rows.

    Price at each level = mid_price * (1 + pct/100), snapped to tick grid.
    Marginal depth = cumulative_depth[level] - cumulative_depth[prev_level].
    """
    rows = []
    current_ts = None
    current_bids: list = []   # list of (pct, cum_depth) sorted by pct desc (closest first)
    current_asks: list = []   # list of (pct, cum_depth) sorted by pct asc

    def flush(ts_ms: int, bids: list, asks: list):
        mid = interpolate_mid(trade_prices, ts_ms)
        if mid <= 0:
            return
        exch_ts = ts_ms * NS
        local_ts = exch_ts + int(feed_latency_ms * NS)

        # Bid side: negative percentages, sorted by abs(pct) ascending (closest first)
        sorted_bids = sorted(bids, key=lambda x: -x[0])  # -5,-4,-3,-2,-1,-0.2 -> -0.2,-1,...
        # Actually we want closest to mid first: pct=-0.2 is closest
        sorted_bids = sorted(bids, key=lambda x: x[0], reverse=True)  # -0.2, -1, -2, -3, -4, -5
        prev_cum = 0.0
        for pct, cum_depth in sorted_bids:
            marginal = cum_depth - prev_cum
            if marginal > 1e-9:
                price = mid * (1.0 + pct / 100.0)
                price = round(round(price / tick_size) * tick_size, 10)
                if price > 0:
                    rows.append((
                        EXCH_EVENT | LOCAL_EVENT | DEPTH_EVENT | BUY_EVENT,
                        exch_ts, local_ts, price, marginal, 0, 0, 0.0
                    ))
            prev_cum = cum_depth

        # Ask side: positive percentages, sorted ascending (closest first)
        sorted_asks = sorted(asks, key=lambda x: x[0])  # 0.2, 1, 2, 3, 4, 5
        prev_cum = 0.0
        for pct, cum_depth in sorted_asks:
            marginal = cum_depth - prev_cum
            if marginal > 1e-9:
                price = mid * (1.0 + pct / 100.0)
                price = round(round(price / tick_size) * tick_size, 10)
                if price > 0:
                    rows.append((
                        EXCH_EVENT | LOCAL_EVENT | DEPTH_EVENT | SELL_EVENT,
                        exch_ts, local_ts, price, marginal, 0, 0, 0.0
                    ))
            prev_cum = cum_depth

    with _open_csv(depth_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts_ms = _parse_timestamp_ms(row["timestamp"])
            pct = float(row["percentage"])
            depth = float(row["depth"])

            if current_ts is None:
                current_ts = ts_ms

            if ts_ms != current_ts:
                flush(current_ts, current_bids, current_asks)
                current_ts = ts_ms
                current_bids = []
                current_asks = []

            if pct < 0:
                current_bids.append((pct, depth))
            else:
                current_asks.append((pct, depth))

    if current_ts is not None:
        flush(current_ts, current_bids, current_asks)

    return rows


def read_trade_events(trades_path: str, feed_latency_ms: float) -> list:
    rows = []
    with _open_csv(trades_path) as f:
        reader = csv.DictReader(f)
        for row in reader:
            exch_ts = int(row["transact_time"]) * NS
            local_ts = exch_ts + int(feed_latency_ms * NS)
            price = float(row["price"])
            qty = float(row["quantity"])
            is_buyer_maker = row["is_buyer_maker"].strip().lower() in ("true", "1")
            side = SELL_EVENT if is_buyer_maker else BUY_EVENT
            rows.append((
                EXCH_EVENT | LOCAL_EVENT | TRADE_EVENT | side,
                exch_ts, local_ts, price, qty, 0, 0, 0.0
            ))
    return rows


def convert(depth_path: str, trades_path: str, output_path: str,
            tick_size: float = 0.01, feed_latency_ms: float = 5.0) -> np.ndarray:
    print(f"Reading trade prices for mid interpolation: {trades_path}")
    trade_prices = read_trade_prices(trades_path)
    print(f"  -> {len(trade_prices):,} trade price points")

    print(f"Reading depth: {depth_path}")
    depth_rows = read_depth_events(depth_path, trade_prices, tick_size, feed_latency_ms)
    print(f"  -> {len(depth_rows):,} depth events")

    print(f"Reading trades: {trades_path}")
    trade_rows = read_trade_events(trades_path, feed_latency_ms)
    print(f"  -> {len(trade_rows):,} trade events")

    all_rows = depth_rows + trade_rows
    print(f"Total events: {len(all_rows):,}")

    data = np.array(all_rows, dtype=event_dtype)
    data = data[np.argsort(data["exch_ts"], kind="mergesort")]

    print("Correcting local timestamp order...")
    data = correct_local_timestamp(data, 0)
    data = correct_event_order(
        data,
        np.argsort(data["exch_ts"], kind="mergesort"),
        np.argsort(data["local_ts"], kind="mergesort"),
    )

    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    np.savez_compressed(output_path, data=data)
    size_kb = Path(output_path).stat().st_size // 1024
    print(f"Saved: {output_path}  ({size_kb} KB)")
    return data


def main():
    ap = argparse.ArgumentParser(description="Convert Binance public USDM data to hftbacktest .npz")
    ap.add_argument("--depth", required=True)
    ap.add_argument("--trades", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--tick-size", type=float, default=0.01)
    ap.add_argument("--feed-latency-ms", type=float, default=5.0)
    args = ap.parse_args()
    convert(args.depth, args.trades, args.out, args.tick_size, args.feed_latency_ms)


if __name__ == "__main__":
    sys.exit(main())
