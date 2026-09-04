#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
edge_report.py - does this system have an edge, and how sure are we?

Every other measurement in this repository answers "how fast" or "how much
memory". This one answers the only question that decides whether the thing
should be pointed at money, and it is built to be able to answer "no".

Why it exists
-------------
Until now the system's profitability was a parameter. `CampaignConfig.skill_alpha`
set how often the swarm's directional call was right, and the campaign measured
what that assumed accuracy was worth after costs - which is a legitimate what-if
and was always labelled as one. Separately, and not labelled at all, the swarm
itself read `price[step + horizon]` to choose its trade direction, which is where
a reported "74% win rate" came from. Both are gone from the decision path. What
is left is a strategy whose edge nobody has measured, and this module is how that
stops being true.

The method, and why each piece is there
----------------------------------------
**Walk-forward, out-of-sample only.** The price path is cut into K contiguous
folds. Every fold is traded by a swarm that has never seen it - which for this
system means a fresh swarm with fresh state, since nothing here is fitted. The
in-sample number is reported alongside for contrast, never as the result.

**Per-trade returns, not the equity curve.** An equity curve makes a lucky streak
look like a trend. The statistic is the distribution of individual round-trip
returns, which is what a t-test can actually speak to.

**t-statistic on the mean, with the trade count attached.** `t = mean / (sd /
sqrt(n))`. A 2% mean over 12 trades is noise; the same mean over 900 is a
finding. The verdict requires both `t > 2.0` and `n >= 100`, because either alone
is easy to manufacture.

**A multiple-testing haircut.** Try twenty configurations and the best one will
show t ~ 2 by chance alone. `required_t_for_trials` applies the standard
Sidak-style correction, and `trials` is an input the caller is expected to be
honest about - the number reported is only as good as that number.

**Costs are already inside the returns.** The market charges spread, slippage and
fees on every fill, so a "gross" figure is not available here and that is
deliberate: gross edge is the number that talks people into strategies that lose
money net.

Reading the verdict
-------------------
`has_edge: false` is the expected result for a momentum rule on a driftless
random walk, and it is not a bug report. It means the strategy has not been shown
to beat its own costs, which is different from "it loses" and very different from
"it works". `live/live_execution_router.py` refuses MAINNET without a `true`
here, not as a moral position but because a router that cannot state its own
out-of-sample t-statistic has no basis on which to size anything.

CLI:
    python benchmarks/edge_report.py --selftest
    python benchmarks/edge_report.py --folds 6 --cycles 900
    python benchmarks/edge_report.py --folds 6 --cycles 900 --trials 12 --write
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import statistics
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Optional, Sequence

REPO_ROOT = Path(__file__).resolve().parent.parent
for _sub in ("core", "simulation"):
    _p = str(REPO_ROOT / _sub)
    if _p not in sys.path:
        sys.path.insert(0, _p)

from groktopus_swarm import SwarmConfig, build_swarm  # noqa: E402

__all__ = [
    "EdgeError",
    "FoldResult",
    "EdgeReport",
    "measure_edge",
    "required_t_for_trials",
    "report_path",
    "load_report",
    "REPORT_PATH",
    "MIN_TRADES",
    "MIN_T_STAT",
]

REPORT_PATH = REPO_ROOT / "bench" / "edge_report.json"

#: A t-statistic of 2 is roughly p < 0.05 two-sided at these sample sizes, and
#: 100 trades is the floor below which the sample standard deviation is itself
#: too noisy to trust. Both are deliberately unambitious: they are a floor for
#: "worth investigating", not a bar for "deploy this".
MIN_T_STAT = 2.0
MIN_TRADES = 100


class EdgeError(RuntimeError):
    """The measurement could not be made."""


def required_t_for_trials(trials: int, alpha: float = 0.05) -> float:
    """The t-threshold that keeps family-wise error at `alpha` over `trials` tries.

    Sidak: per-test alpha' = 1 - (1 - alpha)^(1/trials), then the two-sided
    normal quantile. Twenty configurations need t ~ 3.02, not 1.96 - which is
    exactly how a strategy search produces a "significant" result from noise.
    """
    if trials < 1:
        raise EdgeError("trials must be >= 1")
    if not 0.0 < alpha < 1.0:
        raise EdgeError("alpha must lie in (0, 1)")
    per_test = 1.0 - (1.0 - alpha) ** (1.0 / trials)
    return _normal_quantile(1.0 - per_test / 2.0)


def _normal_quantile(p: float) -> float:
    """Inverse standard normal CDF (Acklam's rational approximation, |eps| < 1e-9)."""
    if not 0.0 < p < 1.0:
        raise EdgeError("p must lie in (0, 1)")
    a = [-3.969683028665376e+01, 2.209460984245205e+02, -2.759285104469687e+02,
         1.383577518672690e+02, -3.066479806614716e+01, 2.506628277459239e+00]
    b = [-5.447609879822406e+01, 1.615858368580409e+02, -1.556989798598866e+02,
         6.680131188771972e+01, -1.328068155288572e+01]
    c = [-7.784894002430293e-03, -3.223964580411365e-01, -2.400758277161838e+00,
         -2.549732539343734e+00, 4.374664141464968e+00, 2.938163982698783e+00]
    d = [7.784695709041462e-03, 3.224671290700398e-01, 2.445134137142996e+00,
         3.754408661907416e+00]
    plow, phigh = 0.02425, 1 - 0.02425
    if p < plow:
        q = math.sqrt(-2 * math.log(p))
        return (((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    if p > phigh:
        q = math.sqrt(-2 * math.log(1 - p))
        return -(((((c[0]*q+c[1])*q+c[2])*q+c[3])*q+c[4])*q+c[5]) / ((((d[0]*q+d[1])*q+d[2])*q+d[3])*q+1)
    q = p - 0.5
    r = q * q
    return (((((a[0]*r+a[1])*r+a[2])*r+a[3])*r+a[4])*r+a[5])*q / (((((b[0]*r+b[1])*r+b[2])*r+b[3])*r+b[4])*r+1)


def _normal_sf(z: float) -> float:
    """Two-sided tail probability for a standard normal."""
    return math.erfc(abs(z) / math.sqrt(2.0))


@dataclass(frozen=True)
class FoldResult:
    """One out-of-sample segment."""

    fold: int
    seed: int
    trades: int
    returns: tuple[float, ...]
    start_equity_cents: int
    end_equity_cents: int

    @property
    def return_pct(self) -> float:
        if not self.start_equity_cents:
            return 0.0
        return (self.end_equity_cents - self.start_equity_cents) / self.start_equity_cents * 100.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "fold": self.fold,
            "seed": self.seed,
            "trades": self.trades,
            "return_pct": round(self.return_pct, 4),
            "mean_trade_return_bps": (
                round(statistics.fmean(self.returns) * 10_000, 3) if self.returns else 0.0
            ),
        }


@dataclass
class EdgeReport:
    """The verdict, and everything needed to disagree with it."""

    trades: int
    mean_return: float
    stdev_return: float
    t_stat: float
    p_value: float
    win_rate: float
    profit_factor: float
    max_drawdown_pct: float
    total_return_pct: float
    folds: list[FoldResult] = field(default_factory=list)
    trials: int = 1
    config_digest: str = ""
    generated_at: float = field(default_factory=time.time)
    notes: list[str] = field(default_factory=list)

    @property
    def required_t(self) -> float:
        return max(MIN_T_STAT, required_t_for_trials(self.trials))

    @property
    def has_edge(self) -> bool:
        """The single boolean the router gates on. Conservative on purpose."""
        return (
            self.trades >= MIN_TRADES
            and self.mean_return > 0.0
            and self.t_stat >= self.required_t
        )

    @property
    def verdict(self) -> str:
        if self.trades < MIN_TRADES:
            return f"INCONCLUSIVE: {self.trades} trades, need at least {MIN_TRADES}"
        if self.mean_return <= 0.0:
            return (
                f"NO EDGE: mean out-of-sample trade return is "
                f"{self.mean_return * 10_000:+.2f} bps, net of costs"
            )
        if self.t_stat < self.required_t:
            return (
                f"NOT SIGNIFICANT: t = {self.t_stat:.2f} against {self.required_t:.2f} "
                f"required for {self.trials} trial(s)"
            )
        return (
            f"EDGE FOUND: {self.mean_return * 10_000:+.2f} bps/trade, "
            f"t = {self.t_stat:.2f} over {self.trades} out-of-sample trades"
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "schema": "groktopus.edge_report/1",
            "generated_at": self.generated_at,
            "generated_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.generated_at)),
            "out_of_sample": True,
            "trades": self.trades,
            "mean_trade_return_bps": round(self.mean_return * 10_000, 4),
            "stdev_trade_return_bps": round(self.stdev_return * 10_000, 4),
            "t_stat": round(self.t_stat, 4),
            "p_value": round(self.p_value, 6),
            "trials": self.trials,
            "required_t": round(self.required_t, 4),
            "win_rate": round(self.win_rate, 4),
            "profit_factor": round(self.profit_factor, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 4),
            "total_return_pct": round(self.total_return_pct, 4),
            "has_edge": self.has_edge,
            "verdict": self.verdict,
            "min_trades": MIN_TRADES,
            "min_t_stat": MIN_T_STAT,
            "config_digest": self.config_digest,
            "folds": [f.as_dict() for f in self.folds],
            "notes": list(self.notes),
            "costs": "included - spread, slippage and fees are charged on every fill",
        }


# ---------------------------------------------------------------------------
# Measurement
# ---------------------------------------------------------------------------


def _trade_returns(market: Any) -> list[float]:
    """Per-trade net return as a fraction of the capital that trade committed.

    Normalising by the position's own notional rather than by account equity is
    what makes the trades comparable to each other: a 1% move on a position
    sized at 5% of equity and the same move at 20% are the same signal quality
    and very different equity impacts, and it is signal quality being tested.
    """
    out: list[float] = []
    for t in market.closed:
        committed = t.entry_price_cents * t.qty
        if committed > 0:
            out.append(t.net_pnl_cents / committed)
    return out


def measure_edge(
    *,
    folds: int = 6,
    cycles_per_fold: int = 900,
    base_seed: int = 20260904,
    n_steps: int = 8_000,
    trials: int = 1,
    config: Optional[SwarmConfig] = None,
    swarm_factory: Optional[Callable[[int], Any]] = None,
    progress: Optional[Callable[[int, int], None]] = None,
) -> EdgeReport:
    """Walk forward over `folds` independent segments and test the pooled result.

    Each fold gets its own seed, which means its own price path and its own
    swarm. Nothing is fitted here, so a fold is out-of-sample by construction -
    but the folds are kept separate anyway, because the day someone adds a fitted
    parameter this is the structure that will catch it, and retrofitting a
    walk-forward harness after the fact never happens.
    """
    if folds < 2:
        raise EdgeError("need at least 2 folds for a walk-forward measurement")
    if cycles_per_fold < 50:
        raise EdgeError("cycles_per_fold must be >= 50 to produce any trades")

    results: list[FoldResult] = []
    pooled: list[float] = []
    equity_path: list[float] = []
    running = 1.0

    for i in range(folds):
        seed = base_seed + i * 1_009  # a prime stride, so folds do not share paths
        swarm = (swarm_factory(seed) if swarm_factory
                 else build_swarm(seed=seed, n_steps=n_steps, config=config))
        asyncio.run(swarm.run(cycles_per_fold))
        m = swarm.market
        rets = _trade_returns(m)
        pooled.extend(rets)
        results.append(FoldResult(
            fold=i,
            seed=seed,
            trades=len(m.closed),
            returns=tuple(rets),
            start_equity_cents=m.config.start_equity_cents,
            end_equity_cents=m.equity_cents(),
        ))
        for t in m.closed:
            committed = t.entry_price_cents * t.qty
            if committed > 0:
                running *= 1.0 + (t.net_pnl_cents / committed) * 0.10
                equity_path.append(running)
        if progress is not None:
            progress(i + 1, folds)

    n = len(pooled)
    mean = statistics.fmean(pooled) if pooled else 0.0
    sd = statistics.stdev(pooled) if n > 1 else 0.0
    t_stat = (mean / (sd / math.sqrt(n))) if (n > 1 and sd > 0) else 0.0
    p_value = _normal_sf(t_stat) if t_stat else 1.0

    wins = [r for r in pooled if r > 0]
    losses = [-r for r in pooled if r < 0]
    profit_factor = (sum(wins) / sum(losses)) if losses else (float("inf") if wins else 0.0)

    peak, max_dd = (equity_path[0] if equity_path else 1.0), 0.0
    for v in equity_path:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak * 100.0)

    total_return = (
        statistics.fmean([f.return_pct for f in results]) if results else 0.0
    )

    cfg = config or SwarmConfig()
    digest = hashlib.sha256(
        json.dumps(
            {
                "folds": folds, "cycles_per_fold": cycles_per_fold, "base_seed": base_seed,
                "n_steps": n_steps,
                "risk_fraction": cfg.risk_fraction, "horizon_steps": cfg.horizon_steps,
                "stop_bps": cfg.stop_bps, "target_bps": cfg.target_bps,
                "scan_window": cfg.scan_window, "drift_window": cfg.drift_window,
                "max_exposure_pct": cfg.max_exposure_pct,
            },
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()

    notes = [
        "Out-of-sample: each fold uses a fresh seed, price path and swarm.",
        "Costs (spread, slippage, taker fees) are charged inside every fill.",
        "No lookahead: tests/test_no_lookahead.py rewrites future prices and "
        "asserts decisions are unchanged.",
    ]
    if trials > 1:
        notes.append(
            f"{trials} trials declared; the significance bar is raised to "
            f"t >= {required_t_for_trials(trials):.2f} accordingly."
        )

    return EdgeReport(
        trades=n,
        mean_return=mean,
        stdev_return=sd,
        t_stat=t_stat,
        p_value=p_value,
        win_rate=(len(wins) / n) if n else 0.0,
        profit_factor=profit_factor,
        max_drawdown_pct=max_dd,
        total_return_pct=total_return,
        folds=results,
        trials=trials,
        config_digest=digest,
        notes=notes,
    )


def report_path() -> Path:
    return REPORT_PATH


def load_report(path: Optional[Path] = None) -> Optional[dict[str, Any]]:
    """Read a written report, or None. Never raises on a malformed file.

    The router calls this to decide whether MAINNET may start, so a corrupt or
    truncated report has to read as "no evidence" rather than as an exception
    that some caller might catch and treat as a pass.
    """
    p = Path(path or REPORT_PATH)
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict) or data.get("schema") != "groktopus.edge_report/1":
        return None
    return data


def write_report(report: EdgeReport, path: Optional[Path] = None) -> Path:
    p = Path(path or REPORT_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(report.as_dict(), indent=2), encoding="utf-8")
    return p


# ---------------------------------------------------------------------------
# Self-test and CLI
# ---------------------------------------------------------------------------


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

    def mk(**kw: Any) -> EdgeReport:
        base = dict(trades=500, mean_return=0.001, stdev_return=0.01, t_stat=2.2,
                    p_value=0.03, win_rate=0.55, profit_factor=1.2,
                    max_drawdown_pct=4.0, total_return_pct=3.0)
        base.update(kw)
        return EdgeReport(**base)  # type: ignore[arg-type]

    def t_quantile_matches_known_values() -> None:
        assert abs(_normal_quantile(0.975) - 1.959964) < 1e-4
        assert abs(_normal_quantile(0.5)) < 1e-9

    def t_more_trials_raise_the_bar() -> None:
        assert required_t_for_trials(1) < required_t_for_trials(20)
        assert abs(required_t_for_trials(1) - 1.96) < 0.01

    def t_twenty_trials_need_about_three_sigma() -> None:
        assert 2.9 < required_t_for_trials(20) < 3.2, required_t_for_trials(20)

    def t_edge_requires_enough_trades() -> None:
        assert not mk(trades=40).has_edge
        assert "INCONCLUSIVE" in mk(trades=40).verdict

    def t_edge_requires_positive_mean() -> None:
        assert not mk(mean_return=-0.001).has_edge
        assert "NO EDGE" in mk(mean_return=-0.001).verdict

    def t_edge_requires_significance() -> None:
        assert not mk(t_stat=1.1).has_edge
        assert "NOT SIGNIFICANT" in mk(t_stat=1.1).verdict

    def t_trials_can_revoke_an_edge() -> None:
        assert mk(t_stat=2.4, trials=1).has_edge
        assert not mk(t_stat=2.4, trials=20).has_edge

    def t_a_real_edge_passes() -> None:
        r = mk(t_stat=4.0)
        assert r.has_edge and "EDGE FOUND" in r.verdict

    def t_report_is_json_safe() -> None:
        json.dumps(mk().as_dict())

    def t_load_missing_report_is_none() -> None:
        assert load_report(Path("/nonexistent/edge.json")) is None

    def t_load_corrupt_report_is_none() -> None:
        import tempfile  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as td:
            bad = Path(td) / "edge.json"
            bad.write_text("{ not json", encoding="utf-8")
            assert load_report(bad) is None
            bad.write_text('{"schema": "something.else/9"}', encoding="utf-8")
            assert load_report(bad) is None

    def t_round_trip_write_and_load() -> None:
        import tempfile  # noqa: PLC0415

        with tempfile.TemporaryDirectory() as td:
            p = write_report(mk(t_stat=4.0), Path(td) / "edge.json")
            back = load_report(p)
            assert back is not None and back["has_edge"] is True

    def t_too_few_folds_refused() -> None:
        try:
            measure_edge(folds=1)
        except EdgeError:
            return
        raise AssertionError("a single fold was accepted as walk-forward")

    def t_measurement_runs_end_to_end() -> None:
        r = measure_edge(folds=2, cycles_per_fold=300, n_steps=1_200)
        assert r.trades > 0
        assert isinstance(r.has_edge, bool)
        json.dumps(r.as_dict())

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    print(f"edge_report self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def main(argv: Optional[Sequence[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GrokTOPUS out-of-sample edge measurement")
    ap.add_argument("--folds", type=int, default=6)
    ap.add_argument("--cycles", type=int, default=900, help="cycles per fold")
    ap.add_argument("--steps", type=int, default=8_000, help="price path length per fold")
    ap.add_argument("--seed", type=int, default=20260904)
    ap.add_argument("--trials", type=int, default=1,
                    help="how many configurations you have tried; raises the bar")
    ap.add_argument("--adaptive", action="store_true",
                    help="turn the regime-adaptive consensus gate ON for this measurement")
    ap.add_argument("--write", action="store_true", help=f"write {REPORT_PATH}")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()

    def progress(done: int, total: int) -> None:
        print(f"  fold {done}/{total} complete", file=sys.stderr)

    cfg = SwarmConfig(adaptive_gate=args.adaptive) if args.adaptive else None
    report = measure_edge(
        folds=args.folds, cycles_per_fold=args.cycles, base_seed=args.seed,
        n_steps=args.steps, trials=args.trials, progress=progress, config=cfg,
    )
    if args.adaptive:
        report.notes.append("Regime-adaptive consensus gate was ENABLED for this run.")

    if args.write:
        print(f"written: {write_report(report)}", file=sys.stderr)
    if args.json:
        print(json.dumps(report.as_dict(), indent=2))
        return 0 if report.has_edge else 1

    d = report.as_dict()
    print("=" * 76)
    print(" OUT-OF-SAMPLE EDGE REPORT")
    print("=" * 76)
    print(f" folds                 : {args.folds} independent paths, {args.cycles} cycles each")
    print(f" trades                : {d['trades']:,}")
    print(f" mean return / trade   : {d['mean_trade_return_bps']:+.3f} bps  (net of all costs)")
    print(f" stdev / trade         : {d['stdev_trade_return_bps']:.3f} bps")
    print(f" t-statistic           : {d['t_stat']:+.3f}   (need >= {d['required_t']:.2f})")
    print(f" p-value               : {d['p_value']:.4f}")
    print(f" win rate              : {d['win_rate'] * 100:.2f}%")
    print(f" profit factor         : {d['profit_factor']:.3f}")
    print(f" max drawdown          : {d['max_drawdown_pct']:.2f}%")
    print(f" mean fold return      : {d['total_return_pct']:+.2f}%")
    print("-" * 76)
    for f in d["folds"]:
        print(f"   fold {f['fold']}  seed {f['seed']:<10} {f['trades']:>5} trades  "
              f"{f['return_pct']:>+7.2f}%   {f['mean_trade_return_bps']:>+8.2f} bps/trade")
    print("-" * 76)
    print(f" VERDICT: {d['verdict']}")
    print(f" has_edge = {d['has_edge']}")
    if not d["has_edge"]:
        print()
        print(" This is the expected result for a momentum rule on a driftless path.")
        print(" It is not a failure of the plumbing - the consensus gate, the ledger and")
        print(" the execution path all work. It means no edge has been demonstrated, so")
        print(" live/live_execution_router.py will refuse to start in MAINNET mode.")
    print("=" * 76)
    return 0 if report.has_edge else 1


if __name__ == "__main__":
    raise SystemExit(main())
