#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
test_no_lookahead.py - the proof that no decision reads a future price.

    pytest tests/test_no_lookahead.py -v

Why this file exists
--------------------
An earlier revision of `groktopus_swarm.py` computed

    true_bias = sign(paths[symbol][step + horizon] - price_now)

and used it twice: `heuristic_brain` made TIDAL, NORO and LUMEN vote that
direction about 82% of the time, and `cycle()` chose the trade's side from it
outright. Its only guard was `symbol not in live_symbols`, so it was active in
every simulation and every backtest. The "74% win rate" that came out of that
branch was a measurement of the lookahead, not of a strategy.

Once removed, the obvious question is how anyone would know if it - or something
like it - came back. Reading the code is not an answer that survives six months
and three contributors.

The technique
-------------
Run two identical swarms. In the second, **rewrite the price path beyond the last
step the run will reach** into something wildly different: alternating triples and
thirds of the boundary price. If any decision reads a price at `step + k`, the
two runs diverge. If none does, every cycle report is identical, byte for byte.

This is a property test with an adversarial mutation rather than an assertion
about code shape, so it catches a reintroduction by any route - a new indicator
that peeks, a helper that slices the wrong end of the series, a "temporary"
debugging line that ships.

`test_the_probe_detects_a_planted_oracle` is the control: it plants a lookahead
on purpose and asserts the probe catches it. Without that, a mutation test that
always passes proves nothing.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

import pytest

from groktopus_swarm import SwarmConfig, build_swarm

#: How far past the run's last step the corruption starts. One cycle advances
#: one step, so a run of N cycles from step 0 ends at step N.
GUARD_STEPS = 2


def corrupt_future(swarm: Any, boundary: int) -> None:
    """Replace every price after `boundary` with a wildly different series."""
    for path in swarm.market.paths.values():
        anchor = path[min(boundary, len(path) - 1)]
        for i in range(boundary + 1, len(path)):
            path[i] = max(1, int(anchor * (3.0 if i % 2 else 0.33)))


def decisions(reports: list[Any]) -> list[dict[str, Any]]:
    """Cycle reports with wall-clock timing stripped, which differs by run."""
    out = []
    for r in reports:
        d = r.as_dict()
        d.pop("timing", None)
        out.append(d)
    return out


def run_pair(seed: int, cycles: int, **cfg: Any) -> tuple[list[dict], list[dict]]:
    a = build_swarm(seed=seed, n_steps=cycles * 3, **cfg)
    b = build_swarm(seed=seed, n_steps=cycles * 3, **cfg)
    corrupt_future(b, cycles + GUARD_STEPS)
    ra = asyncio.run(a.run(cycles))
    rb = asyncio.run(b.run(cycles))
    return decisions(ra), decisions(rb)


class TestNoLookahead:
    def test_rewriting_the_future_changes_nothing(self) -> None:
        a, b = run_pair(seed=4242, cycles=400)
        assert a and b
        assert a == b, "a decision changed when only future prices changed"

    @pytest.mark.parametrize("seed", [11, 907, 20260904])
    def test_holds_across_seeds(self, seed: int) -> None:
        a, b = run_pair(seed=seed, cycles=250)
        assert a == b

    def test_holds_in_batched_mode(self) -> None:
        a, b = run_pair(seed=515, cycles=250, mode="batched", budget_name="TIGHT")
        assert a == b

    def test_the_run_actually_traded(self) -> None:
        # A mutation test over a swarm that never opened a position would pass
        # trivially. This asserts the probe had something to detect.
        s = build_swarm(seed=4242, n_steps=1_200)
        asyncio.run(s.run(400))
        assert s.market.closed, "no trades were made; the mutation test proves nothing"
        assert s.inference_count > 0

    def test_the_probe_detects_a_planted_oracle(self) -> None:
        """The control. Plant a lookahead and confirm the probe catches it."""
        import groktopus_swarm as gs  # noqa: PLC0415

        original = gs.heuristic_brain

        def peeking_brain(spec, prompt, context, rng):
            out = original(spec, prompt, context, rng)
            if spec.name == "TIDAL":
                # Exactly the shape of the bug that was removed.
                m = context.get("_market")
                out["confidence"] = 0.99
            return out

        def make(seed: int):
            s = build_swarm(seed=seed, n_steps=1_200)
            market = s.market

            def oracle_brain(spec, prompt, context, rng):
                out = original(spec, prompt, context, rng)
                if spec.name == "TIDAL":
                    sym, step = context["symbol"], context["step"]
                    fut = market.paths[sym][min(step + 4, len(market.paths[sym]) - 1)]
                    out["stance"] = "BUY" if fut > context["price_cents"] else "SELL"
                    out["confidence"] = 0.95
                return out

            s.brain = oracle_brain
            return s

        a, b = make(4242), make(4242)
        corrupt_future(b, 250 + GUARD_STEPS)
        ra = decisions(asyncio.run(a.run(250)))
        rb = decisions(asyncio.run(b.run(250)))
        assert ra != rb, "the probe failed to detect a deliberately planted oracle"

    def test_context_carries_no_oracle_key(self) -> None:
        s = build_swarm(seed=77, n_steps=1_200)
        s.market.advance(200)
        scan = s.scan()
        from consensus_engine import VoteStance  # noqa: PLC0415

        ctx = s.build_context(scan, VoteStance.BUY, 1)
        for banned in ("true_bias", "future_price", "ground_truth", "lookahead"):
            assert banned not in ctx, f"context still carries {banned}"

    def test_source_has_no_future_indexing_in_the_decision_path(self) -> None:
        from pathlib import Path  # noqa: PLC0415

        src = Path(__import__("groktopus_swarm").__file__).read_text(encoding="utf-8")
        body = "\n".join(
            line for line in src.splitlines() if not line.strip().startswith("#")
        )
        for pattern in ("step + h]", "m.step + self.config.horizon_steps]", "true_bias ="):
            assert pattern not in body, f"decision path contains {pattern!r}"


class TestHonestPerformance:
    """What the strategy actually does, now that it is not being told the answer."""

    def test_win_rate_is_near_a_coin_flip(self) -> None:
        s = build_swarm(seed=20260904, n_steps=6_000)
        asyncio.run(s.run(1_200))
        wr = s.market.report()["win_rate"]
        assert 0.30 < wr < 0.60, (
            f"win rate {wr:.1%} is outside the range a momentum rule on a driftless "
            f"path should produce. Above 60% is the signature of a lookahead."
        )

    def test_no_seed_produces_a_suspicious_win_rate(self) -> None:
        for seed in (11, 22, 33):
            s = build_swarm(seed=seed, n_steps=4_000)
            asyncio.run(s.run(800))
            r = s.market.report()
            if r["closed_trades"] >= 20:
                assert r["win_rate"] < 0.65, (
                    f"seed {seed} won {r['win_rate']:.1%} of {r['closed_trades']} trades"
                )

    def test_money_is_still_conserved(self) -> None:
        s = build_swarm(seed=8, n_steps=4_000)
        asyncio.run(s.run(800))
        m = s.market
        assert m.cash_cents >= 0
        assert m.equity_cents() == m.config.start_equity_cents + m.realised_cents()
