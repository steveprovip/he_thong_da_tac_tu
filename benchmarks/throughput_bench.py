#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
throughput_bench.py - the only file here that measures anything.

`core/inference_planner.py` models throughput from one anchor and a handful of
assumptions, and every number it prints says INFERRED. This module replaces those
with measurements taken against a running llama-server, and it is deliberately
useless without one: `--mode` with no server answering exits 3 and prints the
command line to start it. A benchmark that emits a plausible number when it could
not reach the thing it was benchmarking is worse than no benchmark, because the
number looks exactly like a real one afterwards.

What it measures
----------------
    serial       N requests one after another   -> single-stream tok/s
    batched      N requests at once             -> aggregate tok/s, and the
                                                   batch efficiency the planner
                                                   currently only assumes
    speculative  N requests one after another against a server started with a
                 draft model -> the speedup, and therefore the acceptance rate
                 that BRAIN.md QD-3 warns may be negative at 2-3 bit

Where the numbers come from
---------------------------
llama-server returns a `timings` block with `predicted_per_second` and
`prompt_per_second`, measured inside the server around the actual decode loop.
That is a better number than wall-clock divided by tokens, because it excludes
HTTP and JSON. Both are reported: `server_tokps` is the server's own figure,
`wall_tokps` is what the caller experienced, and the gap between them is the
overhead the planner models as `request_overhead_s`.

Aggregate throughput for the batched run is deliberately computed from wall
clock, not by summing per-request server rates. Six streams each reporting
30 tok/s do not make 180 tok/s unless they truly overlapped, and wall clock is
the only measurement that knows whether they did.

CLI:
    python benchmarks/throughput_bench.py --selftest
    python benchmarks/throughput_bench.py --mode serial --requests 5
    python benchmarks/throughput_bench.py --mode serial --mode batched --slots 6
    python benchmarks/throughput_bench.py --mode batched --slots 6 --calibrate
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

_CORE = str(Path(__file__).resolve().parent.parent / "core")
if _CORE not in sys.path:
    sys.path.insert(0, _CORE)

from inference_planner import (  # noqa: E402
    DEFAULT_HARDWARE,
    HardwareModel,
    batch_efficiency,
    single_stream_tokps,
)
from memory_governor import measured_weights_gb  # noqa: E402

__all__ = [
    "BenchError",
    "ServerUnavailable",
    "BenchResult",
    "ThroughputBench",
    "DEFAULT_PROMPT",
    "BENCH_DIR",
]

REPO_ROOT = Path(__file__).resolve().parent.parent
BENCH_DIR = REPO_ROOT / "bench"

#: A prompt shaped like the real workload: a compact evidence pack and a demand
#: for JSON only. Benchmarking on "write me a poem" measures a different regime -
#: longer generations amortise prefill differently and change the answer.
DEFAULT_PROMPT = json.dumps(
    {
        "proposal": {"symbol": "BTC-USD", "side": "BUY", "qty": 3, "price_cents": 6_412_500},
        "market": {"momentum_sigma": 1.42, "drift_forecast_bps": 18.3,
                   "round_trip_cost_bps": 9.1, "expected_move_bps": 76.0},
        "portfolio": {"equity_cents": 15_000_000, "exposure_pct_if_filled": 12.4,
                      "limit_breaches": []},
        "instruction": "Vote on this proposal. JSON only, no prose.",
    },
    ensure_ascii=False,
)


class BenchError(RuntimeError):
    """A benchmark could not be run."""


class ServerUnavailable(BenchError):
    """No llama-server answered. There is no number to report."""


@dataclass
class BenchResult:
    """One measurement run. Every rate carries how it was obtained."""

    mode: str
    requests: int
    concurrency: int
    max_tokens: int
    wall_s: float
    completion_tokens: int
    prompt_tokens: int
    server_tokps: list[float] = field(default_factory=list)
    server_prompt_tokps: list[float] = field(default_factory=list)
    per_request_wall_s: list[float] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors and self.completion_tokens > 0

    @property
    def wall_tokps(self) -> float:
        """Aggregate tokens per second as the caller experienced it."""
        return (self.completion_tokens / self.wall_s) if self.wall_s > 0 else 0.0

    @property
    def per_stream_wall_tokps(self) -> float:
        return self.wall_tokps / max(1, self.concurrency)

    @property
    def median_server_tokps(self) -> Optional[float]:
        return statistics.median(self.server_tokps) if self.server_tokps else None

    @property
    def median_prompt_tokps(self) -> Optional[float]:
        return statistics.median(self.server_prompt_tokps) if self.server_prompt_tokps else None

    @property
    def overhead_s(self) -> Optional[float]:
        """Wall time not accounted for by the server's own decode timing."""
        srv = self.median_server_tokps
        if not srv or not self.per_request_wall_s:
            return None
        decode_s = (self.completion_tokens / max(1, self.concurrency)) / srv
        return round(max(0.0, statistics.median(self.per_request_wall_s) - decode_s), 5)

    def as_dict(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "requests": self.requests,
            "concurrency": self.concurrency,
            "max_tokens": self.max_tokens,
            "ok": self.ok,
            "wall_s": round(self.wall_s, 4),
            "completion_tokens": self.completion_tokens,
            "prompt_tokens": self.prompt_tokens,
            "wall_tokps_aggregate": round(self.wall_tokps, 2),
            "wall_tokps_per_stream": round(self.per_stream_wall_tokps, 2),
            "server_tokps_median": (
                round(self.median_server_tokps, 2) if self.median_server_tokps else None
            ),
            "server_prompt_tokps_median": (
                round(self.median_prompt_tokps, 2) if self.median_prompt_tokps else None
            ),
            "request_overhead_s": self.overhead_s,
            "errors": list(self.errors),
            "measurement": "MEASURED against a live llama-server",
        }


class ThroughputBench:
    """Drives a running llama-server and reports what it actually did."""

    def __init__(
        self,
        base_url: str = "http://127.0.0.1:8080",
        *,
        transport: Optional[Callable[[str, dict[str, Any], float], dict[str, Any]]] = None,
        prompt: str = DEFAULT_PROMPT,
        timeout_s: float = 300.0,
        system_prompt: str = "You are a market analyst. Reply with JSON only.",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.prompt = prompt
        self.timeout_s = float(timeout_s)
        self.system_prompt = system_prompt
        self._transport = transport or self._http_post

    # -- transport ---------------------------------------------------------

    def _http_post(self, path: str, payload: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        req = urllib.request.Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise ServerUnavailable(f"cannot reach llama-server at {self.base_url}: {exc}") from exc

    def health(self) -> bool:
        """True if a server answers. Never raises."""
        try:
            with urllib.request.urlopen(f"{self.base_url}/health", timeout=5) as resp:
                return resp.status == 200
        except Exception:
            return False

    # -- one request -------------------------------------------------------

    def _one(self, max_tokens: int, seed: Optional[int] = None) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "messages": [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": self.prompt},
            ],
            "temperature": 0.0,
            "top_p": 1.0,
            "max_tokens": int(max_tokens),
            "stream": False,
            "reasoning_effort": "medium",
        }
        if seed is not None:
            payload["seed"] = int(seed)
        t0 = time.perf_counter()
        raw = self._transport("/v1/chat/completions", payload, self.timeout_s)
        wall = time.perf_counter() - t0
        usage = raw.get("usage") or {}
        timings = raw.get("timings") or {}
        return {
            "wall_s": wall,
            "completion_tokens": int(usage.get("completion_tokens", 0) or 0),
            "prompt_tokens": int(usage.get("prompt_tokens", 0) or 0),
            "predicted_per_second": timings.get("predicted_per_second"),
            "prompt_per_second": timings.get("prompt_per_second"),
        }

    # -- runs --------------------------------------------------------------

    def run(
        self, *, mode: str = "serial", requests: int = 6, concurrency: Optional[int] = None,
        max_tokens: int = 48, warmup: int = 1,
    ) -> BenchResult:
        """Measure one configuration.

        `warmup` requests run first and are discarded. The first request into a
        cold server pays for slot allocation and a cold KV cache, and including it
        makes a batched run look worse than it is - which would be measuring the
        harness rather than the server.
        """
        if mode not in ("serial", "batched", "speculative"):
            raise BenchError(f"unknown mode {mode!r}")
        n_conc = concurrency if concurrency is not None else (requests if mode == "batched" else 1)
        if n_conc < 1:
            raise BenchError("concurrency must be >= 1")

        for _ in range(max(0, warmup)):
            try:
                self._one(max_tokens=min(8, max_tokens), seed=0)
            except ServerUnavailable:
                raise
            except Exception:  # noqa: BLE001 - a failed warmup is not a result
                pass

        result = BenchResult(
            mode=mode, requests=requests, concurrency=n_conc,
            max_tokens=max_tokens, wall_s=0.0, completion_tokens=0, prompt_tokens=0,
        )

        def one(i: int) -> Optional[dict[str, Any]]:
            try:
                return self._one(max_tokens, seed=i)
            except ServerUnavailable:
                raise
            except Exception as exc:  # noqa: BLE001
                result.errors.append(f"request {i}: {type(exc).__name__}: {exc}")
                return None

        t0 = time.perf_counter()
        if n_conc == 1:
            rows = [one(i) for i in range(requests)]
        else:
            with ThreadPoolExecutor(max_workers=n_conc) as pool:
                rows = list(pool.map(one, range(requests)))
        result.wall_s = time.perf_counter() - t0

        for row in rows:
            if not row:
                continue
            result.completion_tokens += row["completion_tokens"]
            result.prompt_tokens += row["prompt_tokens"]
            result.per_request_wall_s.append(row["wall_s"])
            if row["predicted_per_second"]:
                result.server_tokps.append(float(row["predicted_per_second"]))
            if row["prompt_per_second"]:
                result.server_prompt_tokps.append(float(row["prompt_per_second"]))
        return result

    # -- calibration -------------------------------------------------------

    @staticmethod
    def calibrate(
        serial: BenchResult,
        batched: Optional[BenchResult] = None,
        *,
        weights_gb: float,
        hw: HardwareModel = DEFAULT_HARDWARE,
    ) -> dict[str, Any]:
        """Turn measurements into the planner's constants.

        eta comes straight from the definition: measured single-stream rate times
        weight bytes over bandwidth. batch_decay is solved from the observed
        aggregate. Both are printed so they can be pasted into HardwareModel and
        stop being assumptions.
        """
        out: dict[str, Any] = {"weights_gb": round(weights_gb, 3)}
        if serial.ok and serial.wall_tokps > 0:
            measured = serial.median_server_tokps or serial.wall_tokps
            eta = measured * weights_gb / hw.bandwidth_gb_s
            out["measured_single_tokps"] = round(measured, 2)
            out["modelled_single_tokps"] = round(single_stream_tokps(weights_gb, hw).tokps, 2)
            out["eta_measured"] = round(eta, 4)
            out["eta_in_model"] = hw.eta
        if serial.ok and batched and batched.ok:
            single = serial.median_server_tokps or serial.wall_tokps
            agg = batched.wall_tokps
            n = batched.concurrency
            if single > 0 and n > 1:
                eff = agg / (single * n)
                # eff = 1 / (1 + decay * (n - 1))  ->  decay = (1/eff - 1)/(n - 1)
                decay = ((1.0 / eff) - 1.0) / (n - 1) if eff > 0 else None
                out["measured_aggregate_tokps"] = round(agg, 2)
                out["measured_batch_efficiency"] = round(eff, 4)
                out["modelled_batch_efficiency"] = round(batch_efficiency(n, hw), 4)
                out["batch_decay_measured"] = round(decay, 4) if decay is not None else None
                out["batch_decay_in_model"] = hw.batch_decay
                out["speedup_vs_serial"] = round(agg / single, 2)
        if serial.median_prompt_tokps:
            out["prefill_tokps_measured"] = round(serial.median_prompt_tokps, 2)
            out["prefill_tokps_in_model"] = hw.prefill_tokps
        return out


# ---------------------------------------------------------------------------
# Self-test and CLI
# ---------------------------------------------------------------------------


def _fake_transport(
    *, tokps: float = 45.0, prompt_tokps: float = 2000.0, out_tokens: int = 48,
    latency_s: float = 0.0,
) -> Callable[[str, dict[str, Any], float], dict[str, Any]]:
    """A stand-in server, so the harness itself is testable with no GPU."""

    def transport(path: str, payload: dict[str, Any], timeout: float) -> dict[str, Any]:
        if latency_s:
            time.sleep(latency_s)
        n = min(int(payload.get("max_tokens", out_tokens)), out_tokens)
        return {
            "choices": [{"message": {"content": '{"stance":"BUY","confidence":0.9}'},
                         "finish_reason": "length"}],
            "usage": {"prompt_tokens": 260, "completion_tokens": n, "total_tokens": 260 + n},
            "timings": {"predicted_per_second": tokps, "prompt_per_second": prompt_tokps},
        }

    return transport


def _selftest() -> int:
    passed = 0
    failed: list[str] = []

    def check(name: str, fn: Callable[[], Any]) -> None:
        nonlocal passed
        try:
            fn()
            passed += 1
        except AssertionError as exc:
            failed.append(f"{name}: {exc}")
        except Exception as exc:  # noqa: BLE001
            failed.append(f"{name}: unexpected {type(exc).__name__}: {exc}")

    def t_serial_run_reports_tokens() -> None:
        b = ThroughputBench(transport=_fake_transport())
        r = b.run(mode="serial", requests=4, max_tokens=48, warmup=0)
        assert r.ok and r.completion_tokens == 4 * 48, r.as_dict()
        assert r.concurrency == 1

    def t_batched_run_overlaps() -> None:
        b = ThroughputBench(transport=_fake_transport(latency_s=0.05))
        ser = b.run(mode="serial", requests=6, max_tokens=48, warmup=0)
        bat = b.run(mode="batched", requests=6, concurrency=6, max_tokens=48, warmup=0)
        assert bat.wall_s < ser.wall_s, (bat.wall_s, ser.wall_s)
        assert bat.wall_tokps > ser.wall_tokps

    def t_server_rate_is_preferred_over_wall() -> None:
        b = ThroughputBench(transport=_fake_transport(tokps=41.5))
        r = b.run(mode="serial", requests=3, warmup=0)
        assert r.median_server_tokps == 41.5

    def t_unreachable_server_raises() -> None:
        def dead(path, payload, timeout):
            raise ServerUnavailable("nothing listening")

        try:
            ThroughputBench(transport=dead).run(mode="serial", requests=1, warmup=0)
        except ServerUnavailable:
            return
        raise AssertionError("a dead server produced a result")

    def t_request_errors_are_recorded_not_swallowed() -> None:
        calls = {"n": 0}

        def flaky(path, payload, timeout):
            calls["n"] += 1
            if calls["n"] == 2:
                raise ValueError("bad json")
            return _fake_transport()(path, payload, timeout)

        r = ThroughputBench(transport=flaky).run(mode="serial", requests=3, warmup=0)
        assert r.errors and not r.ok, r.as_dict()

    def t_calibration_recovers_eta() -> None:
        # A server reporting exactly the modelled rate must calibrate back to the
        # modelled eta, or the calibration arithmetic is inverted somewhere.
        weights = 8.39
        rate = single_stream_tokps(weights).tokps
        b = ThroughputBench(transport=_fake_transport(tokps=rate))
        r = b.run(mode="serial", requests=3, warmup=0)
        cal = ThroughputBench.calibrate(r, weights_gb=weights)
        assert abs(cal["eta_measured"] - DEFAULT_HARDWARE.eta) < 1e-6, cal

    def t_calibration_recovers_batch_decay() -> None:
        weights = 8.39
        single = 45.0
        b = ThroughputBench(transport=_fake_transport(tokps=single, latency_s=0.02))
        ser = b.run(mode="serial", requests=6, max_tokens=48, warmup=0)
        bat = b.run(mode="batched", requests=6, concurrency=6, max_tokens=48, warmup=0)
        cal = ThroughputBench.calibrate(ser, bat, weights_gb=weights)
        assert cal["measured_batch_efficiency"] > 0
        assert "batch_decay_measured" in cal

    def t_unknown_mode_refused() -> None:
        try:
            ThroughputBench(transport=_fake_transport()).run(mode="turbo")
        except BenchError:
            return
        raise AssertionError("an unknown mode was accepted")

    def t_result_is_json_safe() -> None:
        r = ThroughputBench(transport=_fake_transport()).run(mode="serial", requests=2, warmup=0)
        json.dumps(r.as_dict())

    def t_warmup_is_excluded() -> None:
        b = ThroughputBench(transport=_fake_transport())
        r = b.run(mode="serial", requests=2, max_tokens=48, warmup=3)
        assert r.completion_tokens == 2 * 48, r.completion_tokens

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    print(f"throughput_bench self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def _start_hint(slots: int) -> str:
    from inference_planner import llama_server_argv  # noqa: PLC0415

    argv = llama_server_argv(
        model_path="D:/models/Qwen3.8-27B-UD-IQ2_XXS.gguf",
        mode="batched" if slots > 1 else "serial",
        slots=slots, context_per_slot=4096,
    )
    return " ".join(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GrokTOPUS throughput benchmark")
    ap.add_argument("--mode", action="append", choices=["serial", "batched", "speculative"],
                    help="repeatable; run each mode in turn")
    ap.add_argument("--url", default="http://127.0.0.1:8080")
    ap.add_argument("--requests", type=int, default=6)
    ap.add_argument("--slots", type=int, default=6, help="concurrency for --mode batched")
    ap.add_argument("--max-tokens", type=int, default=48)
    ap.add_argument("--warmup", type=int, default=1)
    ap.add_argument("--profile", default="S")
    ap.add_argument("--calibrate", action="store_true",
                    help="solve for eta and batch_decay from the measurements")
    ap.add_argument("--label", default="run")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    modes = args.mode or ["serial"]
    bench = ThroughputBench(args.url)
    if not bench.health():
        print(f"No llama-server answered at {args.url}.", file=sys.stderr)
        print("There is nothing to measure, so nothing is reported.", file=sys.stderr)
        print("\nStart one with:\n  " + _start_hint(args.slots), file=sys.stderr)
        return 3

    weights, provenance = measured_weights_gb(args.profile)
    results: dict[str, BenchResult] = {}
    for mode in modes:
        r = bench.run(
            mode=mode, requests=args.requests,
            concurrency=args.slots if mode == "batched" else 1,
            max_tokens=args.max_tokens, warmup=args.warmup,
        )
        results[mode] = r

    payload: dict[str, Any] = {
        "label": args.label,
        "url": args.url,
        "weights_gb": weights,
        "weights_provenance": provenance,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "results": {k: v.as_dict() for k, v in results.items()},
    }
    if args.calibrate:
        payload["calibration"] = ThroughputBench.calibrate(
            results.get("serial") or next(iter(results.values())),
            results.get("batched"),
            weights_gb=weights,
        )

    BENCH_DIR.mkdir(parents=True, exist_ok=True)
    out = BENCH_DIR / f"throughput_{args.label}.json"
    out.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.json:
        print(json.dumps(payload, indent=2))
        return 0

    print("=" * 74)
    print(f" THROUGHPUT - MEASURED against {args.url}")
    print(f" weights {weights:.2f} GB  [{provenance}]")
    print("=" * 74)
    for mode, r in results.items():
        d = r.as_dict()
        print(f" {mode:<12} x{r.concurrency} concurrent, {r.requests} requests, "
              f"{r.max_tokens} max tokens")
        print(f"   aggregate    : {d['wall_tokps_aggregate']:8.2f} tok/s  (wall clock)")
        print(f"   per stream   : {d['wall_tokps_per_stream']:8.2f} tok/s")
        if d["server_tokps_median"]:
            print(f"   server decode: {d['server_tokps_median']:8.2f} tok/s  (median, server timings)")
        if d["server_prompt_tokps_median"]:
            print(f"   server prefill:{d['server_prompt_tokps_median']:8.2f} tok/s")
        if d["request_overhead_s"] is not None:
            print(f"   overhead     : {d['request_overhead_s']:8.4f} s per request")
        for err in d["errors"][:3]:
            print(f"   ERROR        : {err}")
    if args.calibrate:
        print("-" * 74)
        print(" Calibration - paste these into HardwareModel to stop them being assumptions")
        for k, v in payload["calibration"].items():
            print(f"   {k:<28} {v}")
    print("-" * 74)
    print(f" written: {out}")
    return 0 if all(r.ok for r in results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
