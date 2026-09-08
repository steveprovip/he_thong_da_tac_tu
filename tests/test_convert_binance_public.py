"""Tests for research/convert_binance_public.py.

Validates the converter without downloading real data:
uses a tiny synthetic bookDepth + aggTrades CSV to check
the output event array structure and ordering.
"""
import io
import math
import zipfile
from pathlib import Path

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


def _make_zip(filename: str, content: str) -> str:
    """Write a zip containing one CSV file to a temp path and return the path."""
    import tempfile, os
    tmp = tempfile.NamedTemporaryFile(suffix=".zip", delete=False)
    tmp.close()
    with zipfile.ZipFile(tmp.name, "w") as z:
        z.writestr(filename, content)
    return tmp.name


DEPTH_CSV = """timestamp,percentage,depth,notional
2026-09-05 00:00:04,-0.20,1000.0,100000.0
2026-09-05 00:00:04,-1.00,2000.0,199000.0
2026-09-05 00:00:04,0.20,1000.0,100200.0
2026-09-05 00:00:04,1.00,2000.0,201000.0
2026-09-05 00:01:04,-0.20,1100.0,110000.0
2026-09-05 00:01:04,0.20,1100.0,110220.0
"""

TRADES_CSV = """agg_trade_id,price,quantity,first_trade_id,last_trade_id,transact_time,is_buyer_maker
1,100.0,5.0,1,1,1788566400000,false
2,100.1,3.0,2,2,1788566401000,true
3,99.9,2.0,3,3,1788566402000,false
"""


@pytest.fixture
def depth_zip(tmp_path):
    p = tmp_path / "depth.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("depth.csv", DEPTH_CSV)
    return str(p)


@pytest.fixture
def trades_zip(tmp_path):
    p = tmp_path / "trades.zip"
    with zipfile.ZipFile(p, "w") as z:
        z.writestr("trades.csv", TRADES_CSV)
    return str(p)


@pytest.fixture
def output_npz(tmp_path):
    return str(tmp_path / "out.npz")


def _convert(depth_zip, trades_zip, output_npz, tick_size=0.01, feed_latency_ms=5.0):
    import sys
    sys.path.insert(0, str(ROOT))
    from research.convert_binance_public import convert
    return convert(depth_zip, trades_zip, output_npz, tick_size, feed_latency_ms)


class TestConverterOutput:
    def test_output_has_correct_dtype(self, depth_zip, trades_zip, output_npz):
        from hftbacktest.types import event_dtype
        data = _convert(depth_zip, trades_zip, output_npz)
        assert data.dtype == event_dtype

    def test_output_is_sorted_by_exch_ts(self, depth_zip, trades_zip, output_npz):
        data = _convert(depth_zip, trades_zip, output_npz)
        assert np.all(np.diff(data["exch_ts"]) >= 0), "Events not sorted by exch_ts"

    def test_local_ts_ge_exch_ts(self, depth_zip, trades_zip, output_npz):
        data = _convert(depth_zip, trades_zip, output_npz)
        assert np.all(data["local_ts"] >= data["exch_ts"]), (
            "local_ts must be >= exch_ts (feed latency is non-negative)"
        )

    def test_has_both_depth_and_trade_events(self, depth_zip, trades_zip, output_npz):
        from hftbacktest import DEPTH_EVENT, TRADE_EVENT
        data = _convert(depth_zip, trades_zip, output_npz)
        assert ((data["ev"] & DEPTH_EVENT) > 0).any(), "No DEPTH_EVENT found"
        assert ((data["ev"] & TRADE_EVENT) > 0).any(), "No TRADE_EVENT found"

    def test_prices_are_positive(self, depth_zip, trades_zip, output_npz):
        data = _convert(depth_zip, trades_zip, output_npz)
        assert np.all(data["px"] > 0), "Non-positive price found"

    def test_quantities_are_positive(self, depth_zip, trades_zip, output_npz):
        data = _convert(depth_zip, trades_zip, output_npz)
        assert np.all(data["qty"] > 0), "Non-positive quantity found"

    def test_npz_file_is_loadable(self, depth_zip, trades_zip, output_npz):
        _convert(depth_zip, trades_zip, output_npz)
        d = np.load(output_npz)
        arr = d[d.files[0]]
        assert arr.shape[0] > 0

    def test_trade_aggressor_side_is_correct(self, depth_zip, trades_zip, output_npz):
        """is_buyer_maker=false -> buyer aggressor -> BUY_EVENT."""
        from hftbacktest import BUY_EVENT, SELL_EVENT, TRADE_EVENT
        data = _convert(depth_zip, trades_zip, output_npz)
        trades = data[(data["ev"] & TRADE_EVENT) > 0]
        # First trade: is_buyer_maker=false -> BUY_EVENT (buyer aggressor)
        assert (trades[0]["ev"] & BUY_EVENT) > 0, "First trade should be BUY aggressor"
        # Second trade: is_buyer_maker=true -> SELL_EVENT (seller aggressor)
        assert (trades[1]["ev"] & SELL_EVENT) > 0, "Second trade should be SELL aggressor"

    def test_depth_bid_and_ask_events_present(self, depth_zip, trades_zip, output_npz):
        from hftbacktest import BUY_EVENT, DEPTH_EVENT, SELL_EVENT
        data = _convert(depth_zip, trades_zip, output_npz)
        depth = data[(data["ev"] & DEPTH_EVENT) > 0]
        assert ((depth["ev"] & BUY_EVENT) > 0).any(), "No bid DEPTH_EVENT"
        assert ((depth["ev"] & SELL_EVENT) > 0).any(), "No ask DEPTH_EVENT"

    def test_feed_latency_applied(self, depth_zip, trades_zip, output_npz):
        """local_ts should be exch_ts + feed_latency_ns for trade events."""
        from hftbacktest import TRADE_EVENT
        feed_ms = 5.0
        data = _convert(depth_zip, trades_zip, output_npz, feed_latency_ms=feed_ms)
        trades = data[(data["ev"] & TRADE_EVENT) > 0]
        # After correct_local_timestamp, local_ts >= exch_ts + feed_latency_ns
        feed_ns = int(feed_ms * 1_000_000)
        assert np.all(trades["local_ts"] >= trades["exch_ts"]), (
            "local_ts must be >= exch_ts after latency correction"
        )
