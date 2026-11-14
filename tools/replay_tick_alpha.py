"""Offline, maker-only replay of aggregate-trade flow, BTC lead-lag and whale fade."""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.fast_eval import dsr as deflated_sharpe  # noqa: E402

SYMBOLS = ("BTCUSDT", "SOLUSDT", "DOGEUSDT", "BNBUSDT")
TICK_SIZE = {"BTCUSDT": 0.1, "SOLUSDT": 0.01, "DOGEUSDT": 0.00001, "BNBUSDT": 0.01}
START_MS = int(datetime(2026, 9, 1, tzinfo=timezone.utc).timestamp() * 1000)
TRAIN_END_MS = int(datetime(2026, 9, 8, tzinfo=timezone.utc).timestamp() * 1000)
TEST_START_MS = int(datetime(2026, 9, 9, tzinfo=timezone.utc).timestamp() * 1000)
END_MS = int(datetime(2026, 9, 15, tzinfo=timezone.utc).timestamp() * 1000)
ROUND_TRIP_COST_BPS = 8.5
TRADE_DTYPE = np.dtype([("t", "<i8"), ("p", "<f8"), ("n", "<f8"), ("s", "i1")])
IO_WORKERS = 4


@dataclass
class Tape:
    """Compact millisecond trade tape; s=+1 buy aggression, -1 sell aggression."""

    rows: np.ndarray
    symbol: str
    load_seconds: float = 0.0
    _timestamps: np.ndarray | None = field(default=None, init=False, repr=False)
    _quote_indexes: tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray] | None = field(default=None, init=False, repr=False)
    _prefix: tuple[np.ndarray, np.ndarray] | None = field(default=None, init=False, repr=False)
    _block_extrema: tuple[np.ndarray, np.ndarray] | None = field(default=None, init=False, repr=False)

    @property
    def t(self) -> np.ndarray:
        if self._timestamps is None:
            # The structured field has a 25-byte stride. Cache one contiguous
            # copy so repeated binary searches do not materialize it each time.
            self._timestamps = self.rows["t"].copy()
        return self._timestamps

    @property
    def p(self) -> np.ndarray:
        return self.rows["p"]

    @property
    def n(self) -> np.ndarray:
        return self.rows["n"]

    @property
    def s(self) -> np.ndarray:
        return self.rows["s"]

    def prefix(self) -> tuple[np.ndarray, np.ndarray]:
        if self._prefix is None:
            notional = np.empty(len(self.rows) + 1, dtype=np.float64)
            signed = np.empty(len(self.rows) + 1, dtype=np.float64)
            notional[0] = signed[0] = 0.0
            np.cumsum(self.n, out=notional[1:])
            np.cumsum(self.n * self.s, out=signed[1:])
            self._prefix = notional, signed
        return self._prefix

    def quote_indexes(self) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
        """Indexes of prior sell and buy aggressor prints for a tape-derived touch."""
        if self._quote_indexes is None:
            sell_ix, buy_ix = np.flatnonzero(self.s < 0), np.flatnonzero(self.s > 0)
            self._quote_indexes = (sell_ix, buy_ix, self.t[sell_ix], self.t[buy_ix])
        return self._quote_indexes

    def block_extrema(self, block_size: int = 4096) -> tuple[np.ndarray, np.ndarray]:
        """Per-block sell-print minima and buy-print maxima for exact fill queries."""
        if self._block_extrema is None:
            starts = np.arange(0, len(self.rows), block_size, dtype=np.int64)
            sell = np.where(self.s < 0, self.p, np.inf)
            buy = np.where(self.s > 0, self.p, -np.inf)
            self._block_extrema = (np.minimum.reduceat(sell, starts),
                                   np.maximum.reduceat(buy, starts))
        return self._block_extrema


def _to_ms(row: dict) -> int:
    """Use source precision; ts_ns is a derived field and does not add precision."""
    unit = row.get("source_time_unit")
    if unit != "ms":
        raise ValueError(f"expected source_timestamp in ms, got {unit!r}")
    return int(row["source_timestamp"])


def load_tape(root: Path, symbol: str, days: Iterable[str] | None = None,
              progress=print) -> Tape:
    try:
        import pyarrow.json as arrow_json
    except ImportError as exc:
        raise RuntimeError("PyArrow is required for full-scale tick replay: install pyarrow in this environment") from exc
    files = sorted((root / symbol).glob("*.jsonl.gz"))
    if days is not None:
        wanted = set(days)
        files = [p for p in files if p.name[:8] in wanted]
    if not files:
        raise FileNotFoundError(f"no .jsonl.gz aggregate trades for {symbol} under {root}")
    pieces: list[np.ndarray] = []
    total = 0
    started = time.perf_counter()
    def read_day(path: Path) -> tuple[Path, np.ndarray]:
        table = arrow_json.read_json(path)
        unit = table.column("source_time_unit")[0].as_py()
        if unit != "ms":
            raise ValueError(f"expected source_timestamp in ms, got {unit!r} in {path}")
        ts = table.column("source_timestamp").combine_chunks().to_numpy(zero_copy_only=False)
        px = table.column("p").combine_chunks().to_numpy(zero_copy_only=False)
        qty = table.column("q").combine_chunks().to_numpy(zero_copy_only=False)
        buyer_maker = table.column("m").combine_chunks().to_numpy(zero_copy_only=False)
        valid = np.isfinite(px) & np.isfinite(qty) & (px > 0) & (qty > 0)
        day_rows = np.empty(int(valid.sum()), dtype=TRADE_DTYPE)
        day_rows["t"] = ts[valid]
        day_rows["p"] = px[valid]
        day_rows["n"] = px[valid] * qty[valid]
        day_rows["s"] = np.where(buyer_maker[valid], -1, 1)
        if len(day_rows) > 1 and np.any(day_rows["t"][1:] < day_rows["t"][:-1]):
            raise ValueError(f"timestamps are not sorted in {path}")
        return path, day_rows

    with ThreadPoolExecutor(max_workers=min(IO_WORKERS, len(files))) as pool:
        for path, day_rows in pool.map(read_day, files):
            pieces.append(day_rows)
            total += len(day_rows)
            progress(f"  loaded {symbol} {path.name}: {len(day_rows):,} ticks")
    rows = np.concatenate(pieces)
    if len(rows) > 1 and np.any(rows["t"][1:] < rows["t"][:-1]):
        raise ValueError(f"timestamps are not sorted across {symbol} daily files")
    load_seconds = time.perf_counter() - started
    progress(f"  {symbol}: {total:,} valid ticks loaded in {load_seconds:.2f}s")
    return Tape(rows, symbol, load_seconds)


def rolling_flow(tape: Tape, query_ms: np.ndarray, window_ms: int) -> tuple[np.ndarray, np.ndarray]:
    """Return causal signed-notional / total-notional flow and total notional."""
    p_total, p_signed = tape.prefix()
    end = np.searchsorted(tape.t, query_ms, side="right")
    begin = np.searchsorted(tape.t, query_ms - window_ms, side="left")
    total = p_total[end] - p_total[begin]
    signed = p_signed[end] - p_signed[begin]
    imbalance = np.divide(signed, total, out=np.zeros_like(signed), where=total > 0)
    return imbalance, total


def quote_at(tape: Tape, time_ms: int, side: int) -> float | None:
    """Infer a passive touch from last aggressor prints; side +1 buys, -1 sells."""
    sell_ix, buy_ix, sell_t, buy_t = tape.quote_indexes()
    si = int(np.searchsorted(sell_t, time_ms, side="left")) - 1
    bi = int(np.searchsorted(buy_t, time_ms, side="left")) - 1
    bid = float(tape.p[sell_ix[si]]) if si >= 0 else None
    ask = float(tape.p[buy_ix[bi]]) if bi >= 0 else None
    tick = TICK_SIZE[tape.symbol]
    if bid is None and ask is None:
        return None
    if bid is None:
        bid = ask - tick
    if ask is None:
        ask = bid + tick
    # Match the existing Touch reconstruction when a print crosses its prior opposite touch.
    if bid >= ask:
        if si >= 0 and bi >= 0 and sell_t[si] >= buy_t[bi]:
            ask = bid + tick
        else:
            bid = ask - tick
    return bid if side > 0 else ask


def _quote_batch(tape: Tape, times: np.ndarray, sides: np.ndarray) -> np.ndarray:
    """Vectorized tape-touch reconstruction for a candidate event set."""
    sell_ix, buy_ix, sell_t, buy_t = tape.quote_indexes()
    si = np.searchsorted(sell_t, times, side="left") - 1
    bi = np.searchsorted(buy_t, times, side="left") - 1
    has_bid, has_ask = si >= 0, bi >= 0
    valid = has_bid | has_ask
    safe_si = np.maximum(si, 0)
    safe_bi = np.maximum(bi, 0)
    bid = np.full(len(times), np.nan, dtype=np.float64)
    ask = np.full(len(times), np.nan, dtype=np.float64)
    bid[has_bid] = tape.p[sell_ix[safe_si[has_bid]]]
    ask[has_ask] = tape.p[buy_ix[safe_bi[has_ask]]]
    tick = TICK_SIZE[tape.symbol]
    bid[~has_bid & has_ask] = ask[~has_bid & has_ask] - tick
    ask[~has_ask & has_bid] = bid[~has_ask & has_bid] + tick
    crossed = valid & (bid >= ask)
    both = crossed & has_bid & has_ask
    newer_sell = np.zeros(len(times), dtype=bool)
    if len(sell_t) and len(buy_t) and np.any(both):
        newer_sell[both] = sell_t[safe_si[both]] >= buy_t[safe_bi[both]]
    ask[newer_sell] = bid[newer_sell] + tick
    repair_bid = crossed & ~newer_sell
    bid[repair_bid] = ask[repair_bid] - tick
    return np.where(sides > 0, bid, ask)


def _first_trade_through(tape: Tape, first: int, last: int, side: int,
                         quote: float, block_size: int = 4096) -> int | None:
    """Find the first strict passive fill, skipping full blocks by exact extrema."""
    if first >= last:
        return None
    sell_min, buy_max = tape.block_extrema(block_size)
    extrema = sell_min if side > 0 else buy_max

    def scan(lo: int, hi: int) -> int | None:
        px, aggr = tape.p[lo:hi], tape.s[lo:hi]
        crossed = ((aggr < 0) & (px < quote)) if side > 0 else ((aggr > 0) & (px > quote))
        hits = np.flatnonzero(crossed)
        return lo + int(hits[0]) if len(hits) else None

    # The leading partial block is small and must be checked before any summary.
    aligned = min(last, ((first + block_size - 1) // block_size) * block_size)
    hit = scan(first, aligned)
    if hit is not None:
        return hit
    first = aligned

    # A full block can be discarded only when its directional extreme cannot cross.
    full_end = (last // block_size) * block_size
    if first < full_end:
        block_lo = first // block_size
        block_hi = full_end // block_size
        candidates = np.flatnonzero(extrema[block_lo:block_hi] < quote if side > 0
                                    else extrema[block_lo:block_hi] > quote)
        if len(candidates):
            block_start = (block_lo + int(candidates[0])) * block_size
            hit = scan(block_start, min(last, block_start + block_size))
            if hit is not None:
                return hit
        first = full_end
    return scan(first, last) if first < last else None


def replay_events(tape: Tape, events: Iterable[tuple[int, int]], horizon_ms: int,
                  cost_bps: float = ROUND_TRIP_COST_BPS) -> tuple[list[dict], dict]:
    """One passive entry per event; require a strict trade-through before horizon."""
    if not isinstance(horizon_ms, int) or horizon_ms <= 0 or not math.isfinite(cost_bps) or cost_bps < 0:
        raise ValueError("horizon must be a positive integer and cost must be finite and non-negative")
    event_rows = list(events)
    results = []
    attempted = unfilled = 0
    busy_until = -1
    if event_rows:
        signal_times = np.fromiter((event[0] for event in event_rows), dtype=np.int64, count=len(event_rows))
        sides = np.fromiter((event[1] for event in event_rows), dtype=np.int8, count=len(event_rows))
        if np.all(signal_times[1:] >= signal_times[:-1]):
            # An unfilled attempt is busy for exactly the horizon; a fill can only
            # extend that interval. This lower-bound cooldown safely eliminates
            # dense events that cannot possibly be attempted, before quote/index work.
            eligible = []
            event_index = 0
            while event_index < len(event_rows):
                eligible.append(event_index)
                next_index = int(np.searchsorted(signal_times, signal_times[event_index] + horizon_ms,
                                                 side="left"))
                event_index = max(event_index + 1, next_index)
        else:
            eligible = list(range(len(event_rows)))
        selected_times, selected_sides = signal_times[eligible], sides[eligible]
        quotes = _quote_batch(tape, selected_times, selected_sides)
        first_indexes = np.searchsorted(tape.t, selected_times, side="right")
        last_indexes = np.searchsorted(tape.t, selected_times + horizon_ms, side="right")
    else:
        eligible = []
        quotes = np.empty(0, dtype=np.float64)
        first_indexes = last_indexes = np.empty(0, dtype=np.int64)
    for candidate_index, event_index in enumerate(eligible):
        signal_ms, side = event_rows[event_index]
        if signal_ms < busy_until:
            continue
        attempted += 1
        quote = float(quotes[candidate_index])
        if not math.isfinite(quote):
            busy_until = signal_ms + horizon_ms
            unfilled += 1
            continue
        first = int(first_indexes[candidate_index])
        last = int(last_indexes[candidate_index])
        if first >= last:
            busy_until = signal_ms + horizon_ms
            unfilled += 1
            continue
        fill_i = _first_trade_through(tape, first, last, side, quote)
        if fill_i is None:
            busy_until = signal_ms + horizon_ms
            unfilled += 1
            continue
        fill_ms = int(tape.t[fill_i])
        exit_i = int(np.searchsorted(tape.t, fill_ms + horizon_ms, side="right")) - 1
        if exit_i <= fill_i:
            busy_until = fill_ms + horizon_ms
            unfilled += 1
            continue
        gross = side * (float(tape.p[exit_i]) - quote) / quote * 10_000.0
        results.append({"signal_ms": int(signal_ms), "fill_ms": fill_ms, "side": int(side),
                        "quote": quote, "exit_px": float(tape.p[exit_i]), "gross_bps": gross,
                        "net_bps": gross - cost_bps, "wait_ms": fill_ms - int(signal_ms)})
        busy_until = fill_ms + horizon_ms
    return results, {"signals": len(event_rows), "attempted": attempted, "filled": len(results),
                     "unfilled": unfilled, "skipped_busy": len(event_rows) - attempted,
                     "fill_rate_attempted": len(results) / attempted if attempted else None}


def simulate_events(tape: Tape, events: Iterable[tuple[int, int]], horizon_ms: int,
                    cost_bps: float = ROUND_TRIP_COST_BPS) -> list[dict]:
    """Compatibility helper returning only filled trade rows."""
    return replay_events(tape, events, horizon_ms, cost_bps)[0]


def _daily_stats(trades: list[dict], start_ms: int, end_ms: int) -> tuple[list[float], float, float]:
    n_days = max(1, int(math.ceil((end_ms - start_ms) / 86_400_000)))
    daily = np.zeros(n_days, dtype=np.float64)
    for row in trades:
        day = (row["signal_ms"] - start_ms) // 86_400_000
        if 0 <= day < n_days:
            daily[int(day)] += row["net_bps"]
    mean = float(np.mean(daily))
    std = float(np.std(daily, ddof=1)) if len(daily) > 1 else 0.0
    daily_sr = mean / std if std > 0 else 0.0
    return daily.tolist(), daily_sr, daily_sr * math.sqrt(365.0)


def summarize(trades: list[dict], start_ms: int, end_ms: int,
              sr_var: float = 0.0, n_trials: int = 1) -> dict:
    daily, daily_sr, annual_sr = _daily_stats(trades, start_ms, end_ms)
    if not trades:
        return {"n": 0, "win_rate": None, "gross_bps": None, "net_bps": None,
                "sharpe_daily_annualized": annual_sr, "dsr": None, "daily_net_bps": daily,
                "mean_wait_ms": None, "filled": 0}
    gross = np.array([x["gross_bps"] for x in trades], dtype=np.float64)
    net = np.array([x["net_bps"] for x in trades], dtype=np.float64)
    vals = np.asarray(daily, dtype=np.float64)
    sd = float(np.std(vals, ddof=1)) if len(vals) > 1 else 0.0
    if sd > 0:
        centered = vals - vals.mean()
        skew = float(np.mean(centered ** 3) / (np.mean(centered ** 2) ** 1.5))
        kurt = float(np.mean(centered ** 4) / (np.mean(centered ** 2) ** 2))
        dsr = float(deflated_sharpe(daily_sr, len(vals), skew, kurt, max(0.0, sr_var), max(1, n_trials)))
    else:
        dsr = 0.5 if daily_sr == 0 else (1.0 if daily_sr > 0 else 0.0)
    return {"n": len(trades), "win_rate": float(np.mean(net > 0)),
            "gross_bps": float(np.mean(gross)), "net_bps": float(np.mean(net)),
            "sharpe_daily_annualized": annual_sr, "dsr": dsr, "daily_net_bps": daily,
            "mean_wait_ms": float(np.mean([x["wait_ms"] for x in trades])), "filled": len(trades)}


def _apply_dsr(summary: dict, sr_var: float, n_trials: int) -> dict:
    daily = np.asarray(summary["daily_net_bps"], dtype=np.float64)
    if len(daily) < 2 or np.std(daily, ddof=1) == 0:
        summary["dsr"] = None if not summary["n"] else 0.5
        return summary
    centered = daily - daily.mean()
    skew = float(np.mean(centered ** 3) / np.mean(centered ** 2) ** 1.5)
    kurt = float(np.mean(centered ** 4) / np.mean(centered ** 2) ** 2)
    daily_sr = float(np.mean(daily) / np.std(daily, ddof=1))
    summary["dsr"] = float(deflated_sharpe(daily_sr, len(daily), skew, kurt,
                                            max(0.0, sr_var), max(1, n_trials)))
    return summary


def _crossing_events(times: np.ndarray, value: np.ndarray, threshold: float,
                     start_ms: int, end_ms: int, quality: np.ndarray | None = None,
                     minimum_quality: float = 0.0) -> list[tuple[int, int]]:
    previous = np.r_[0.0, value[:-1]]
    cross = (np.abs(value) >= threshold) & ((np.abs(previous) < threshold) | (np.sign(value) != np.sign(previous)))
    if quality is not None:
        cross &= np.abs(quality) >= minimum_quality
    ix = np.flatnonzero(cross & (times >= start_ms) & (times < end_ms))
    return [(int(times[i]), 1 if value[i] > 0 else -1) for i in ix]


def _pick_model(candidates: list[dict], train_start: int, train_end: int) -> dict:
    usable = [c for c in candidates if c["train"]["n"] >= 30]
    pool = usable or candidates
    if not pool:
        raise ValueError("no candidates available")
    return max(pool, key=lambda c: (float("-inf") if c["train"]["net_bps"] is None
                                   else c["train"]["net_bps"], c["train"]["n"]))


def _candidate(name: str, tape: Tape, train_events: list[tuple[int, int]],
               test_events: list[tuple[int, int]], horizon: int) -> dict:
    train_rows, train_execution = replay_events(tape, train_events, horizon)
    test_rows, test_execution = replay_events(tape, test_events, horizon)
    train_summary = summarize(train_rows, START_MS, TRAIN_END_MS)
    test_summary = summarize(test_rows, TEST_START_MS, END_MS)
    train_summary.update(train_execution)
    test_summary.update(test_execution)
    return {"name": name, "horizon_ms": horizon,
            "train": train_summary, "test": test_summary}


def run_cvd(tape: Tape, progress=print) -> tuple[dict, list[dict]]:
    times = np.arange(START_MS, END_MS, 1000, dtype=np.int64)
    train_mask = times < TRAIN_END_MS
    candidates = []
    for window_s in (5, 15, 60):
        imbalance, total = rolling_flow(tape, times, window_s * 1000)
        signed_flow = imbalance * total
        threshold = max(float(np.quantile(np.abs(signed_flow[train_mask]), 0.99)), 1e-9)
        events = _crossing_events(times, signed_flow, threshold, START_MS, END_MS,
                                  quality=imbalance, minimum_quality=0.60)
        train_events = [e for e in events if e[0] < TRAIN_END_MS]
        test_events = [e for e in events if e[0] >= TEST_START_MS]
        for horizon in (1000, 2500):
            name = f"{tape.symbol}|CVD{window_s}s|net_p99_skew60|h{horizon}ms"
            candidate = _candidate(name, tape, train_events, test_events, horizon)
            candidate["flow_threshold_usd"] = threshold
            candidates.append(candidate)
        progress(f"  {tape.symbol}: CVD {window_s}s threshold sweep complete")
    chosen = _pick_model(candidates, START_MS, TRAIN_END_MS)
    chosen_test = _apply_dsr(dict(chosen["test"]), _sr_variance(candidates), len(candidates))
    return ({"selected": chosen["name"], "selected_threshold_usd": chosen["flow_threshold_usd"],
             "threshold_fit": "training p99 absolute signed notional with abs(skew)>=0.60",
             "train": chosen["train"], "test": chosen_test, "tested_configs": len(candidates)}, candidates)


def _sr_variance(candidates: list[dict]) -> float:
    vals = []
    for c in candidates:
        daily = c["train"]["daily_net_bps"]
        sr = _daily_stats_from_values(daily)[0]
        if len(daily) > 1 and np.std(daily, ddof=1) > 0:
            vals.append(sr)
    return float(np.var(vals, ddof=1)) if len(vals) > 1 else 0.0


def _daily_stats_from_values(values: list[float]) -> tuple[float, float]:
    daily = np.asarray(values, dtype=np.float64)
    std = float(np.std(daily, ddof=1)) if len(daily) > 1 else 0.0
    sr = float(np.mean(daily) / std) if std else 0.0
    return sr, sr * math.sqrt(365.0)


def run_lead_lag(btc: Tape, alt: Tape, progress=print) -> tuple[dict, list[dict]]:
    times = np.arange(START_MS, END_MS, 1000, dtype=np.int64)
    _, total = rolling_flow(btc, times, 1000)
    imbalance, _ = rolling_flow(btc, times, 1000)
    threshold = float(np.quantile(total[times < TRAIN_END_MS], 0.99))
    prev = np.r_[0.0, total[:-1]]
    pulse = (total >= threshold) & (prev < threshold) & (np.abs(imbalance) >= 0.60)
    train_events = [(int(times[i]), 1 if imbalance[i] > 0 else -1)
                    for i in np.flatnonzero(pulse & (times < TRAIN_END_MS))]
    test_events = [(int(times[i]), 1 if imbalance[i] > 0 else -1)
                   for i in np.flatnonzero(pulse & (times >= TEST_START_MS))]
    candidates = [_candidate(f"BTC>{alt.symbol}|pulse_p99|h{h}ms", alt, train_events, test_events, h)
                  for h in (200, 500, 1000, 2500)]
    chosen = _pick_model(candidates, START_MS, TRAIN_END_MS)
    chosen_test = _apply_dsr(dict(chosen["test"]), _sr_variance(candidates), len(candidates))
    progress(f"  BTC>{alt.symbol}: p99 pulse={threshold:,.0f} USD notional/s; {len(test_events)} holdout pulses")
    return ({"selected": chosen["name"], "pulse_threshold_usd_per_s": threshold,
             "train": chosen["train"], "test": chosen_test,
             "test_pulses": len(test_events), "tested_configs": len(candidates)}, candidates)


def run_whale(tape: Tape, progress=print) -> tuple[dict, list[dict]]:
    train_rows = tape.rows[(tape.t >= START_MS) & (tape.t < TRAIN_END_MS)]
    threshold = float(np.quantile(train_rows["n"], 0.99))
    ix = np.flatnonzero((tape.n >= threshold) & (tape.t >= START_MS) & (tape.t < END_MS))
    events = [(int(tape.t[i]), -int(tape.s[i])) for i in ix]
    train_events = [e for e in events if e[0] < TRAIN_END_MS]
    test_events = [e for e in events if e[0] >= TEST_START_MS]
    candidates = [_candidate(f"{tape.symbol}|whale_p99_fade|h{h}ms", tape,
                             train_events, test_events, h) for h in (1000, 2500)]
    chosen = _pick_model(candidates, START_MS, TRAIN_END_MS)
    chosen_test = _apply_dsr(dict(chosen["test"]), _sr_variance(candidates), len(candidates))
    progress(f"  {tape.symbol}: whale p99=${threshold:,.0f}; {len(test_events):,} test prints")
    return ({"selected": chosen["name"], "whale_threshold_usd": threshold,
             "train": chosen["train"], "test": chosen_test,
             "test_whale_prints": len(test_events), "tested_configs": len(candidates)}, candidates)


def run(data_dir: Path, progress=print) -> dict:
    run_started = time.perf_counter()
    load_seconds = 0.0
    days = [(datetime(2026, 9, 1, tzinfo=timezone.utc) + timedelta(days=offset)).strftime("%Y%m%d")
            for offset in range(14)]
    output = {"window_utc": {"data": "2026-09-01..2026-09-14", "train": "2026-09-01..2026-09-07",
                             "embargo": "2026-09-08", "holdout": "2026-09-09..2026-09-14"},
              "timestamp_resolution": "source_timestamp integer milliseconds",
              "execution": {"fill": "strict subsequent trade-through of inferred passive touch",
                            "quote": "last opposite-aggressor print touch proxy; no queue model",
                            "round_trip_cost_bps": ROUND_TRIP_COST_BPS,
                            "unfilled": "no trade; no taker fallback"},
              "symbols": {}, "ledger": []}
    tapes: dict[str, Tape] = {}
    trial_count = 0
    for symbol in SYMBOLS:
        tapes[symbol] = load_tape(data_dir, symbol, days, progress)
        load_seconds += tapes[symbol].load_seconds
        cvd, cvd_candidates = run_cvd(tapes[symbol], progress)
        whale, whale_candidates = run_whale(tapes[symbol], progress)
        output["symbols"][symbol] = {"tick_count": len(tapes[symbol].rows), "cvd": cvd, "whale_fade": whale}
        trial_count += len(cvd_candidates) + len(whale_candidates)
        for family, result in (("cvd", cvd), ("whale_fade", whale)):
            output["ledger"].append({"symbol": symbol, "family": family, **result})
        if symbol != "BTCUSDT":
            tapes[symbol]._prefix = None
    btc = tapes["BTCUSDT"]
    for symbol in ("SOLUSDT", "DOGEUSDT", "BNBUSDT"):
        result, candidates = run_lead_lag(btc, tapes[symbol], progress)
        output["symbols"][symbol]["btc_lead_lag"] = result
        trial_count += len(candidates)
        output["ledger"].append({"symbol": symbol, "family": "btc_lead_lag", **result})
    output["aggregate_tick_count"] = int(sum(x["tick_count"] for x in output["symbols"].values()))
    output["trial_count_for_dsr"] = trial_count
    output["performance"] = {"load_seconds": load_seconds,
                              "analysis_seconds": max(0.0, time.perf_counter() - run_started - load_seconds),
                              "total_seconds": time.perf_counter() - run_started}
    return output


def _print_summary(result: dict) -> None:
    print("\nSelected holdout replay models (all thresholds/horizons selected on train only)")
    print("symbol    family          n    win%   gross bps   net bps   ann daily SR    DSR")
    for row in result["ledger"]:
        s = row["test"]
        def f(value, fmt):
            return "n/a" if value is None else format(value, fmt)
        print(f"{row['symbol']:<9} {row['family']:<14} {s['n']:>5}  {f(s['win_rate'], '.1%'):>6}"
              f" {f(s['gross_bps'], '+.2f'):>10} {f(s['net_bps'], '+.2f'):>9}"
              f" {f(s['sharpe_daily_annualized'], '.2f'):>13} {f(s['dsr'], '.4f'):>8}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=ROOT / "data" / "ticks")
    parser.add_argument("--json", type=Path, default=ROOT / "data" / "tick_alpha_replay.json")
    args = parser.parse_args(argv)
    result = run(args.data_dir)
    args.json.parent.mkdir(parents=True, exist_ok=True)
    args.json.write_text(json.dumps(result, indent=2, allow_nan=False), encoding="utf-8")
    _print_summary(result)
    print(f"\nJSON ledger: {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
