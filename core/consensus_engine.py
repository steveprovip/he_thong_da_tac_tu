#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
consensus_engine.py - the 76.8% gate between an idea and an order.

Eight agents, one vote each from the six that are allowed to vote, and one
arithmetic rule that decides whether money is permitted to move. Nothing in this
module places an order; it only decides whether an order may be placed, and mints
the single-use token that says so.

The rule
--------
Six ANALYST agents (TIDAL, NORO, LUMEN, ZEPHR, OKAPI, RUNE) each return a stance
and a confidence. Weights are normalised over the analysts, so they sum to 1.0
regardless of what the config file says in absolute terms. The directive's

    Consensus = sum(w_i * confidence_i)

is a weighted mean of confidence, which is exactly right when everyone agrees on
direction and meaningless when they do not - six agents at 0.9 confidence,
three long and three short, would score 0.9 under a literal reading and that
score would authorise a trade nobody agreed on. So the sum is taken *signed*
along the proposed direction:

    support(D) = sum_i  w_i * confidence_i * align(stance_i, D)
    align      = +1 if stance_i == D, -1 if stance_i is the opposite, 0 if HOLD

When every analyst agrees this reduces to the directive's formula term for term.
When they disagree, dissent subtracts. A HOLD is neither support nor dissent: it
withholds weight, which lowers the score without arguing the other way.

    score = 100 * max(0, support(winning direction))

Three bands, from the directive:

    score >= 76.8   EXECUTE   - mint a token, VESKA may place the order
    70.0 <= score < 76.8   HOLD      - keep watching, no order
    score <  70.0   VETO      - the proposal is cancelled

and one override: RUNE's `veto` flag cancels the proposal at any score. That is
what "quyen phu quyet tuyet doi" means - a risk objection that a high score can
outvote is not a veto.

Fail-closed, everywhere
-----------------------
A missing analyst vote, a duplicate vote, a vote from an agent that is not
allowed to vote, a confidence outside [0, 1], a NaN - each of these cancels the
proposal rather than being dropped, defaulted or rounded. A consensus engine
that quietly computes a score from five of six votes is worse than one that
refuses, because the number it produces still looks like a number.

The token
---------
EXECUTE mints an `AuthToken`: an HMAC over the proposal digest, single-use and
time-limited. `consume()` is the only way to spend one and it is atomic under a
lock, so two execution paths racing on the same token cannot both win. The token
is bound to the proposal *digest*, not just its id, so VESKA cannot be handed an
authorisation for 10 units of BTC-USD and fill 100 - the digest would not match.

CLI:
    python core/consensus_engine.py --selftest
    python core/consensus_engine.py --demo
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import math
import os
import secrets
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence

__all__ = [
    "VoteStance",
    "ConsensusDecision",
    "AgentTier",
    "AgentSpec",
    "AgentRegistry",
    "Vote",
    "Proposal",
    "ConsensusResult",
    "AuthToken",
    "ConsensusEngine",
    "ConsensusError",
    "QuorumError",
    "TokenError",
    "EXECUTE_THRESHOLD_PCT",
    "HOLD_THRESHOLD_PCT",
    "MarketRegime",
    "RegimeGate",
    "AdaptiveGateConfig",
]

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_AGENT_CONFIG = REPO_ROOT / "config" / "groktopus_agents.json"

#: Directive constants. Kept as module-level names so a test can assert on the
#: number itself rather than on a value read back out of the same config file it
#: is meant to be checking.
EXECUTE_THRESHOLD_PCT = 76.8
HOLD_THRESHOLD_PCT = 70.0


class MarketRegime(str, Enum):
    """What kind of market the proposal is being judged in."""

    TRENDING = "TRENDING"
    CHOP = "CHOP"
    VOLATILE = "VOLATILE"
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True)
class RegimeGate:
    """The bar a proposal is actually held to, and why."""

    regime: MarketRegime
    execute_pct: float
    hold_pct: float
    size_multiplier: float = 1.0
    rationale: str = ""

    def __post_init__(self) -> None:
        if not 0.0 <= self.hold_pct <= self.execute_pct <= 100.0:
            raise ConsensusError(
                f"{self.regime.value}: need 0 <= hold ({self.hold_pct}) <= "
                f"execute ({self.execute_pct}) <= 100"
            )
        if not 0.0 < self.size_multiplier <= 1.0:
            raise ConsensusError("size_multiplier must lie in (0, 1]")

    def as_dict(self) -> dict[str, Any]:
        return {
            "regime": self.regime.value,
            "execute_pct": self.execute_pct,
            "hold_pct": self.hold_pct,
            "size_multiplier": self.size_multiplier,
            "rationale": self.rationale,
        }


@dataclass
class AdaptiveGateConfig:
    """Per-regime thresholds. **Disabled by default, and that default is load-bearing.**

    The motivating complaint is that the swarm sits at 67.5% and never fires, so
    the obvious move is to lower the bar until it does. That reasoning has the
    causality backwards: the gate is not what makes a strategy profitable, it is
    what stops an unprofitable one from trading. As of the last measurement this
    strategy's out-of-sample edge is **-8.03 bps per trade** with t = -1.82, so
    every additional trade the gate lets through is, in expectation, a further
    loss. Lowering it does not unblock profit; it unblocks volume.

    That is a hypothesis, though, not a verdict - so this exists, it is
    configurable, and `benchmarks/edge_report.py` can run with it on and off and
    say which is better. Measure before shipping it, and note that measuring both
    counts as two trials in `--trials`, which raises the significance bar.

    Note the asymmetry in the shipped values: VOLATILE *raises* the bar to 82%.
    A mechanism that only ever loosens is not adaptive, it is a loophole with a
    dial on it.
    """

    enabled: bool = False
    trending_execute_pct: float = 76.8
    trending_hold_pct: float = 70.0
    chop_execute_pct: float = 72.0
    chop_hold_pct: float = 66.0
    chop_size_multiplier: float = 0.5
    volatile_execute_pct: float = 82.0
    volatile_hold_pct: float = 75.0
    volatile_size_multiplier: float = 0.5
    #: ATR as a percentage of spot. Below `chop_atr_max_pct` the market is quiet.
    chop_atr_max_pct: float = 0.08
    volatile_atr_min_pct: float = 0.60
    trend_momentum_sigma: float = 1.0
    #: A wide book means the cost of being wrong is higher, so a chop regime with
    #: a wide spread is not granted the loosened bar.
    chop_max_spread_bps: float = 12.0

    def __post_init__(self) -> None:
        for name in ("trending", "chop", "volatile"):
            ex = getattr(self, f"{name}_execute_pct")
            ho = getattr(self, f"{name}_hold_pct")
            if not 0.0 <= ho <= ex <= 100.0:
                raise ConsensusError(f"{name}: need 0 <= hold <= execute <= 100")
        if self.chop_atr_max_pct >= self.volatile_atr_min_pct:
            raise ConsensusError("chop_atr_max_pct must be below volatile_atr_min_pct")

    def classify(
        self,
        *,
        atr_pct: float,
        momentum_sigma: float,
        spread_bps: float = 0.0,
    ) -> RegimeGate:
        """Pick the regime and the bar that goes with it.

        Order matters: volatility is checked first, because a market that is both
        fast and trending is still a market where a stop is more likely to be
        taken out by noise, and the stricter reading should win.
        """
        if not self.enabled:
            return RegimeGate(
                MarketRegime.UNKNOWN, EXECUTE_THRESHOLD_PCT, HOLD_THRESHOLD_PCT, 1.0,
                "adaptive gate disabled; the fixed 76.8/70.0 bar applies",
            )
        if atr_pct >= self.volatile_atr_min_pct:
            return RegimeGate(
                MarketRegime.VOLATILE, self.volatile_execute_pct, self.volatile_hold_pct,
                self.volatile_size_multiplier,
                f"ATR {atr_pct:.3f}% >= {self.volatile_atr_min_pct:.2f}%: bar RAISED and "
                f"size halved, because a stop is more easily taken out by noise here",
            )
        if abs(momentum_sigma) >= self.trend_momentum_sigma:
            return RegimeGate(
                MarketRegime.TRENDING, self.trending_execute_pct, self.trending_hold_pct, 1.0,
                f"momentum {momentum_sigma:+.2f} sigma: trending, standard bar",
            )
        if atr_pct <= self.chop_atr_max_pct and spread_bps <= self.chop_max_spread_bps:
            return RegimeGate(
                MarketRegime.CHOP, self.chop_execute_pct, self.chop_hold_pct,
                self.chop_size_multiplier,
                f"ATR {atr_pct:.3f}% <= {self.chop_atr_max_pct:.2f}% on a "
                f"{spread_bps:.1f} bps book: bar LOWERED to "
                f"{self.chop_execute_pct:.1f}% at half size - unmeasured, see edge_report",
            )
        return RegimeGate(
            MarketRegime.UNKNOWN, EXECUTE_THRESHOLD_PCT, HOLD_THRESHOLD_PCT, 1.0,
            "no regime matched; the fixed bar applies",
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "trending": [self.trending_execute_pct, self.trending_hold_pct],
            "chop": [self.chop_execute_pct, self.chop_hold_pct, self.chop_size_multiplier],
            "volatile": [self.volatile_execute_pct, self.volatile_hold_pct,
                         self.volatile_size_multiplier],
            "chop_atr_max_pct": self.chop_atr_max_pct,
            "volatile_atr_min_pct": self.volatile_atr_min_pct,
            "trend_momentum_sigma": self.trend_momentum_sigma,
        }


class ConsensusError(RuntimeError):
    """Base class for consensus faults."""


class QuorumError(ConsensusError):
    """The set of votes offered is not a legal quorum."""


class TokenError(ConsensusError):
    """An authorisation token was missing, forged, expired, spent or mismatched."""


class VoteStance(str, Enum):
    """What an analyst thinks should happen."""

    BUY = "BUY"
    SELL = "SELL"
    HOLD = "HOLD"


class ConsensusDecision(str, Enum):
    """What the swarm is permitted to do about it."""

    EXECUTE = "EXECUTE"
    HOLD = "HOLD"
    VETO = "VETO"


class AgentTier(str, Enum):
    """Who may vote, who may execute, who may settle."""

    ANALYST = "ANALYST"
    EXECUTION = "EXECUTION"
    SETTLEMENT = "SETTLEMENT"


_OPPOSITE = {VoteStance.BUY: VoteStance.SELL, VoteStance.SELL: VoteStance.BUY}


# ---------------------------------------------------------------------------
# Agent registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentSpec:
    """One agent as declared in config/groktopus_agents.json."""

    name: str
    order: int
    tier: AgentTier
    role: str
    weight: float
    system_prompt: str
    temperature: float = 0.2
    max_tokens: int = 768
    veto_power: bool = False
    sole_executor: bool = False
    sole_settler: bool = False
    tentacle_hue: int = 200
    kpis: tuple[str, ...] = ()
    role_vi: str = ""

    @property
    def votes(self) -> bool:
        return self.tier is AgentTier.ANALYST

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "order": self.order,
            "tier": self.tier.value,
            "role": self.role,
            "role_vi": self.role_vi,
            "weight": self.weight,
            "votes": self.votes,
            "veto_power": self.veto_power,
            "sole_executor": self.sole_executor,
            "sole_settler": self.sole_settler,
            "tentacle_hue": self.tentacle_hue,
            "kpis": list(self.kpis),
        }


class AgentRegistry:
    """Loads and validates the eight-agent roster.

    Validation is structural rather than advisory: an eight-agent swarm with two
    executors, or with RUNE stripped of its veto, is not a GrokTOPUS swarm with a
    misconfiguration - it is a different and less safe system, and it should fail
    to load rather than run.
    """

    REQUIRED_AGENTS = ("TIDAL", "NORO", "LUMEN", "ZEPHR", "OKAPI", "RUNE", "VESKA", "MARIN")
    REQUIRED_ANALYSTS = ("TIDAL", "NORO", "LUMEN", "ZEPHR", "OKAPI", "RUNE")

    def __init__(self, config_path: Path | str = DEFAULT_AGENT_CONFIG) -> None:
        self.config_path = Path(config_path)
        if not self.config_path.exists():
            raise FileNotFoundError(f"agent config not found: {self.config_path}")
        try:
            raw = json.loads(self.config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ConsensusError(f"{self.config_path} is not valid JSON: {exc}") from exc

        agents_raw = raw.get("agents")
        if not isinstance(agents_raw, dict):
            raise ConsensusError(f"{self.config_path}: 'agents' must be an object")

        self.consensus_cfg: dict[str, Any] = dict(raw.get("consensus") or {})
        self.market_cfg: dict[str, Any] = dict(raw.get("market") or {})
        self.schema_version = int(raw.get("schema_version", 1))

        specs: dict[str, AgentSpec] = {}
        for name, d in agents_raw.items():
            if not isinstance(d, dict):
                raise ConsensusError(f"agent {name!r}: entry must be an object")
            try:
                tier = AgentTier(str(d.get("tier", "")).upper())
            except ValueError as exc:
                raise ConsensusError(
                    f"agent {name!r}: tier must be one of "
                    f"{[t.value for t in AgentTier]}, got {d.get('tier')!r}"
                ) from exc
            weight = float(d.get("weight", 0.0))
            if not math.isfinite(weight) or weight < 0.0:
                raise ConsensusError(f"agent {name!r}: weight must be finite and >= 0")
            prompt = str(d.get("system_prompt", "")).strip()
            if not prompt:
                raise ConsensusError(f"agent {name!r}: system_prompt must be non-empty")
            specs[name] = AgentSpec(
                name=name,
                order=int(d.get("order", 0)),
                tier=tier,
                role=str(d.get("role", "")),
                role_vi=str(d.get("role_vi", "")),
                weight=weight,
                system_prompt=prompt,
                temperature=float(d.get("temperature", 0.2)),
                max_tokens=int(d.get("max_tokens", 768)),
                veto_power=bool(d.get("veto_power", False)),
                sole_executor=bool(d.get("sole_executor", False)),
                sole_settler=bool(d.get("sole_settler", False)),
                tentacle_hue=int(d.get("tentacle_hue", 200)),
                kpis=tuple(d.get("kpis") or ()),
            )

        self.agents: dict[str, AgentSpec] = dict(
            sorted(specs.items(), key=lambda kv: (kv[1].order, kv[0]))
        )
        self._validate()

    # -- validation --------------------------------------------------------

    def _validate(self) -> None:
        missing = [n for n in self.REQUIRED_AGENTS if n not in self.agents]
        if missing:
            raise ConsensusError(f"agent roster is missing: {', '.join(missing)}")
        if len(self.agents) != 8:
            raise ConsensusError(
                f"GrokTOPUS is an eight-agent swarm; config declares {len(self.agents)}"
            )

        analysts = self.analyst_names()
        if analysts != list(self.REQUIRED_ANALYSTS):
            raise ConsensusError(
                f"the six voting analysts must be {list(self.REQUIRED_ANALYSTS)}, got {analysts}"
            )

        for name in analysts:
            if self.agents[name].weight <= 0.0:
                raise ConsensusError(f"analyst {name} has weight 0; it could never affect a vote")

        for name, spec in self.agents.items():
            if spec.tier is not AgentTier.ANALYST and spec.weight != 0.0:
                raise ConsensusError(
                    f"{name} is tier {spec.tier.value} and must have weight 0.0, "
                    f"got {spec.weight}; only analysts vote"
                )

        vetoers = [n for n, s in self.agents.items() if s.veto_power]
        if vetoers != ["RUNE"]:
            raise ConsensusError(f"exactly one agent (RUNE) may hold the veto; found {vetoers}")

        executors = [n for n, s in self.agents.items() if s.sole_executor]
        if executors != ["VESKA"]:
            raise ConsensusError(f"exactly one agent (VESKA) may execute; found {executors}")

        settlers = [n for n, s in self.agents.items() if s.sole_settler]
        if settlers != ["MARIN"]:
            raise ConsensusError(f"exactly one agent (MARIN) may settle; found {settlers}")

    # -- accessors ---------------------------------------------------------

    def analyst_names(self) -> list[str]:
        return [n for n, s in self.agents.items() if s.tier is AgentTier.ANALYST]

    def analysts(self) -> list[AgentSpec]:
        return [self.agents[n] for n in self.analyst_names()]

    def require(self, name: str) -> AgentSpec:
        try:
            return self.agents[name]
        except KeyError as exc:
            raise ConsensusError(
                f"unknown agent {name!r}; roster is {list(self.agents)}"
            ) from exc

    def normalised_weights(self) -> dict[str, float]:
        """Analyst weights scaled to sum to exactly 1.0."""
        total = sum(self.agents[n].weight for n in self.analyst_names())
        if total <= 0.0:
            raise ConsensusError("analyst weights sum to zero; no vote could ever pass")
        return {n: self.agents[n].weight / total for n in self.analyst_names()}

    @property
    def executor(self) -> AgentSpec:
        return self.agents["VESKA"]

    @property
    def settler(self) -> AgentSpec:
        return self.agents["MARIN"]

    @property
    def symbols(self) -> list[str]:
        return list(self.market_cfg.get("symbols") or [])

    def as_dict(self) -> dict[str, Any]:
        return {
            "config_path": str(self.config_path),
            "schema_version": self.schema_version,
            "agents": [s.as_dict() for s in self.agents.values()],
            "normalised_weights": self.normalised_weights(),
            "consensus": self.consensus_cfg,
            "market": self.market_cfg,
        }


# ---------------------------------------------------------------------------
# Votes, proposals, results
# ---------------------------------------------------------------------------


def _check_confidence(value: Any, *, agent: str) -> float:
    """Confidence must be a real number in [0, 1]. NaN is not a low confidence."""
    if isinstance(value, bool):
        raise QuorumError(f"{agent}: confidence must be a number, not a bool")
    if not isinstance(value, (int, float)):
        raise QuorumError(
            f"{agent}: confidence must be a number, got {type(value).__name__}"
        )
    conf = float(value)
    if not math.isfinite(conf):
        raise QuorumError(f"{agent}: confidence must be finite, got {value!r}")
    if not 0.0 <= conf <= 1.0:
        raise QuorumError(f"{agent}: confidence must lie in [0, 1], got {conf}")
    return conf


@dataclass(frozen=True)
class Vote:
    """One analyst's position on one proposal."""

    agent: str
    stance: VoteStance
    confidence: float
    rationale: str = ""
    veto: bool = False

    def __post_init__(self) -> None:
        if not self.agent or not str(self.agent).strip():
            raise QuorumError("vote.agent must be a non-empty agent name")
        if not isinstance(self.stance, VoteStance):
            raise QuorumError(
                f"{self.agent}: stance must be a VoteStance, got {type(self.stance).__name__}"
            )
        object.__setattr__(self, "confidence", _check_confidence(self.confidence, agent=self.agent))

    @property
    def signed_weight_note(self) -> str:
        return f"{self.agent}:{self.stance.value}@{self.confidence:.2f}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "agent": self.agent,
            "stance": self.stance.value,
            "confidence": round(self.confidence, 6),
            "rationale": self.rationale,
            "veto": self.veto,
        }


@dataclass(frozen=True)
class Proposal:
    """A concrete order the swarm is being asked to authorise.

    `digest()` is what the authorisation token is bound to, so an approved
    proposal cannot be swapped for a different one between the vote and the fill.
    """

    proposal_id: str
    symbol: str
    side: VoteStance
    qty: int
    price_cents: int
    meta: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.proposal_id or not str(self.proposal_id).strip():
            raise ConsensusError("proposal_id must be non-empty")
        if not self.symbol or not str(self.symbol).strip():
            raise ConsensusError("symbol must be non-empty")
        if self.side not in (VoteStance.BUY, VoteStance.SELL):
            raise ConsensusError(f"proposal side must be BUY or SELL, got {self.side}")
        for name, value in (("qty", self.qty), ("price_cents", self.price_cents)):
            if not isinstance(value, int) or isinstance(value, bool):
                raise ConsensusError(f"{name} must be an int, got {type(value).__name__}")
            if value <= 0:
                raise ConsensusError(f"{name} must be > 0, got {value}")

    @property
    def notional_cents(self) -> int:
        return self.qty * self.price_cents

    def digest(self) -> str:
        """A stable fingerprint of the economically meaningful fields."""
        canonical = json.dumps(
            {
                "proposal_id": self.proposal_id,
                "symbol": self.symbol,
                "side": self.side.value,
                "qty": self.qty,
                "price_cents": self.price_cents,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "symbol": self.symbol,
            "side": self.side.value,
            "qty": self.qty,
            "price_cents": self.price_cents,
            "notional_cents": self.notional_cents,
            "digest": self.digest(),
            "meta": dict(self.meta),
        }


@dataclass(frozen=True)
class AuthToken:
    """A single-use permission slip for exactly one proposal."""

    token: str
    proposal_id: str
    digest: str
    direction: VoteStance
    score_pct: float
    issued_at: float
    expires_at: float

    def expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) > self.expires_at

    def as_dict(self) -> dict[str, Any]:
        return {
            "token": self.token,
            "proposal_id": self.proposal_id,
            "digest": self.digest,
            "direction": self.direction.value,
            "score_pct": round(self.score_pct, 4),
            "issued_at": self.issued_at,
            "expires_at": self.expires_at,
        }


@dataclass(frozen=True)
class ConsensusResult:
    """The full arithmetic of one vote, kept so a decision can be re-derived."""

    proposal_id: str
    decision: ConsensusDecision
    score_pct: float
    direction: VoteStance
    reason: str
    votes: tuple[Vote, ...] = ()
    weights: Mapping[str, float] = field(default_factory=dict)
    support_pct: Mapping[str, float] = field(default_factory=dict)
    contributions: Mapping[str, float] = field(default_factory=dict)
    veto_source: Optional[str] = None
    token: Optional[AuthToken] = None
    gate: Optional[RegimeGate] = None

    @property
    def authorised(self) -> bool:
        return self.decision is ConsensusDecision.EXECUTE and self.token is not None

    def __bool__(self) -> bool:
        return self.authorised

    def as_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "decision": self.decision.value,
            "score_pct": round(self.score_pct, 4),
            "direction": self.direction.value,
            "reason": self.reason,
            "authorised": self.authorised,
            "veto_source": self.veto_source,
            "votes": [v.as_dict() for v in self.votes],
            "weights": {k: round(v, 6) for k, v in self.weights.items()},
            "support_pct": {k: round(v, 4) for k, v in self.support_pct.items()},
            "contributions": {k: round(v, 4) for k, v in self.contributions.items()},
            "token": self.token.as_dict() if self.token else None,
            "execute_threshold_pct": (
                self.gate.execute_pct if self.gate else EXECUTE_THRESHOLD_PCT),
            "hold_threshold_pct": (
                self.gate.hold_pct if self.gate else HOLD_THRESHOLD_PCT),
            "fixed_execute_threshold_pct": EXECUTE_THRESHOLD_PCT,
            "gate": self.gate.as_dict() if self.gate else None,
            "points_short": (
                round(max(0.0, (self.gate.execute_pct if self.gate else EXECUTE_THRESHOLD_PCT)
                          - self.score_pct), 2)
                if self.decision is not ConsensusDecision.EXECUTE else 0.0
            ),
        }


# ---------------------------------------------------------------------------
# The engine
# ---------------------------------------------------------------------------


class ConsensusEngine:
    """Scores a quorum of analyst votes and mints execution authority.

    Thread-safe. Token issue and consumption happen under one lock so two
    execution paths racing on the same token cannot both spend it.

        engine = ConsensusEngine()
        result = engine.evaluate(proposal, votes)
        if result.authorised:
            tok = engine.consume(result.token.token, proposal)   # VESKA only
    """

    def __init__(
        self,
        registry: Optional[AgentRegistry] = None,
        *,
        execute_threshold_pct: Optional[float] = None,
        hold_threshold_pct: Optional[float] = None,
        token_ttl_s: Optional[float] = None,
        secret: Optional[bytes] = None,
        history_size: int = 256,
        adaptive: Optional[AdaptiveGateConfig] = None,
    ) -> None:
        self.registry = registry or AgentRegistry()
        cfg = self.registry.consensus_cfg
        self.execute_threshold_pct = float(
            execute_threshold_pct
            if execute_threshold_pct is not None
            else cfg.get("execute_threshold_pct", EXECUTE_THRESHOLD_PCT)
        )
        self.hold_threshold_pct = float(
            hold_threshold_pct
            if hold_threshold_pct is not None
            else cfg.get("hold_threshold_pct", HOLD_THRESHOLD_PCT)
        )
        if not 0.0 <= self.hold_threshold_pct <= self.execute_threshold_pct <= 100.0:
            raise ConsensusError(
                f"need 0 <= hold ({self.hold_threshold_pct}) <= execute "
                f"({self.execute_threshold_pct}) <= 100"
            )
        self.token_ttl_s = float(
            token_ttl_s if token_ttl_s is not None else cfg.get("token_ttl_s", 30.0)
        )
        if self.token_ttl_s <= 0:
            raise ConsensusError("token_ttl_s must be > 0")

        # A per-process secret by default. Tokens are never persisted or shared
        # across processes, so a fresh random secret each run is exactly right:
        # a restart invalidates every outstanding authorisation.
        self._secret = secret or secrets.token_bytes(32)
        self._lock = threading.RLock()
        self._issued: dict[str, AuthToken] = {}
        self._consumed: set[str] = set()
        self.adaptive = adaptive or AdaptiveGateConfig()
        self._history: list[dict[str, Any]] = []
        self._history_size = int(history_size)
        self.stats: dict[str, int] = {"evaluated": 0, "execute": 0, "hold": 0, "veto": 0}

    # -- quorum ------------------------------------------------------------

    def _quorum_fault(self, votes: Sequence[Vote]) -> Optional[str]:
        """Return a human reason if these votes are not a legal quorum, else None."""
        expected = set(self.registry.analyst_names())
        seen: set[str] = set()
        for v in votes:
            spec = self.registry.agents.get(v.agent)
            if spec is None:
                return f"vote from unknown agent {v.agent!r}"
            if spec.tier is not AgentTier.ANALYST:
                return (
                    f"{v.agent} is tier {spec.tier.value} and is not permitted to vote; "
                    f"only the six analysts vote"
                )
            if v.agent in seen:
                return f"{v.agent} voted more than once"
            if v.veto and not spec.veto_power:
                return f"{v.agent} raised a veto flag but holds no veto power"
            seen.add(v.agent)
        missing = sorted(expected - seen)
        if missing:
            return f"missing votes from {', '.join(missing)}; quorum requires all six analysts"
        return None

    # -- scoring -----------------------------------------------------------

    def score_votes(self, votes: Sequence[Vote]) -> tuple[dict[str, float], dict[str, float]]:
        """Signed weighted support for each direction, in percent, plus per-agent terms.

        Returns ``(support_pct, contributions_pct)`` where ``support_pct`` has
        keys "BUY" and "SELL" (mirror images of each other) and
        ``contributions_pct`` gives each agent's signed term along BUY.
        """
        weights = self.registry.normalised_weights()
        contributions: dict[str, float] = {}
        buy_support = 0.0
        for v in votes:
            w = weights[v.agent]
            if v.stance is VoteStance.BUY:
                align = 1.0
            elif v.stance is VoteStance.SELL:
                align = -1.0
            else:
                align = 0.0
            term = w * v.confidence * align * 100.0
            contributions[v.agent] = term
            buy_support += term
        return ({"BUY": buy_support, "SELL": -buy_support}, contributions)

    # -- the gate ----------------------------------------------------------

    def evaluate(
        self,
        proposal: Proposal,
        votes: Sequence[Vote],
        *,
        now: Optional[float] = None,
        gate: Optional[RegimeGate] = None,
    ) -> ConsensusResult:
        """Score a proposal against its quorum and decide EXECUTE / HOLD / VETO.

        Never raises on a bad quorum: an illegal vote set cancels the proposal,
        which is the fail-closed behaviour the directive asks for. Programming
        faults (a non-Proposal, a non-Vote) still raise.
        """
        if not isinstance(proposal, Proposal):
            raise TypeError(f"proposal must be a Proposal, got {type(proposal).__name__}")
        votes = tuple(votes)
        for v in votes:
            if not isinstance(v, Vote):
                raise TypeError(f"votes must contain Vote objects, got {type(v).__name__}")

        weights = self.registry.normalised_weights()
        # No gate supplied means the fixed bar, which is also what a disabled
        # adaptive config produces. The two paths agree on purpose: a caller that
        # forgets to pass a regime gets the strict threshold, never a loose one.
        if gate is None:
            gate = RegimeGate(
                MarketRegime.UNKNOWN, self.execute_threshold_pct,
                self.hold_threshold_pct, 1.0, "no regime supplied; fixed bar",
            )
        execute_at, hold_at = gate.execute_pct, gate.hold_pct

        fault = self._quorum_fault(votes)
        if fault is not None:
            return self._finish(
                ConsensusResult(
                    proposal_id=proposal.proposal_id,
                    decision=ConsensusDecision.VETO,
                    score_pct=0.0,
                    direction=proposal.side,
                    reason=f"quorum refused: {fault}",
                    votes=votes,
                    weights=weights,
                    support_pct={"BUY": 0.0, "SELL": 0.0},
                    contributions={},
                    veto_source="QUORUM",
                    gate=gate,
                )
            )

        support, contributions = self.score_votes(votes)
        raw = support[proposal.side.value]
        score = max(0.0, raw)

        vetoers = tuple(v.agent for v in votes if v.veto)
        if vetoers:
            names = ", ".join(vetoers)
            return self._finish(
                ConsensusResult(
                    proposal_id=proposal.proposal_id,
                    decision=ConsensusDecision.VETO,
                    score_pct=score,
                    direction=proposal.side,
                    reason=(
                        f"{names} exercised the absolute veto at score {score:.2f}%; "
                        f"a veto that a score can outvote is not a veto"
                    ),
                    votes=votes,
                    weights=weights,
                    support_pct=support,
                    contributions=contributions,
                    veto_source=names,
                    gate=gate,
                )
            )

        if score >= execute_at:
            token = self._mint(proposal, score, now=now)
            return self._finish(
                ConsensusResult(
                    proposal_id=proposal.proposal_id,
                    decision=ConsensusDecision.EXECUTE,
                    score_pct=score,
                    direction=proposal.side,
                    reason=(
                        f"consensus {score:.2f}% >= {execute_at:.1f}%; "
                        f"VESKA authorised for {proposal.side.value} {proposal.qty} "
                        f"{proposal.symbol}"
                    ),
                    votes=votes,
                    weights=weights,
                    support_pct=support,
                    contributions=contributions,
                    token=token,
                    gate=gate,
                )
            )

        if score >= hold_at:
            return self._finish(
                ConsensusResult(
                    proposal_id=proposal.proposal_id,
                    decision=ConsensusDecision.HOLD,
                    score_pct=score,
                    direction=proposal.side,
                    reason=(
                        f"consensus {score:.2f}% is in the hold band "
                        f"[{hold_at:.1f}%, {execute_at:.1f}%); "
                        f"keep observing, place nothing"
                    ),
                    votes=votes,
                    weights=weights,
                    support_pct=support,
                    contributions=contributions,
                    gate=gate,
                )
            )

        detail = (
            "no directional mandate"
            if abs(raw) < 1e-9
            else f"support for {proposal.side.value} is {raw:.2f}%"
        )
        return self._finish(
            ConsensusResult(
                proposal_id=proposal.proposal_id,
                decision=ConsensusDecision.VETO,
                score_pct=score,
                direction=proposal.side,
                reason=(
                    f"consensus {score:.2f}% < {hold_at:.1f}% "
                    f"({detail}); RUNE cancels the proposal"
                ),
                votes=votes,
                weights=weights,
                support_pct=support,
                contributions=contributions,
                veto_source="RUNE_THRESHOLD",
                gate=gate,
            )
        )

    def _finish(self, result: ConsensusResult) -> ConsensusResult:
        with self._lock:
            self.stats["evaluated"] += 1
            self.stats[result.decision.value.lower()] += 1
            self._history.append(result.as_dict())
            if len(self._history) > self._history_size:
                del self._history[: len(self._history) - self._history_size]
        return result

    # -- tokens ------------------------------------------------------------

    def _sign(self, nonce: str, proposal: Proposal, expires_at: float) -> str:
        payload = "|".join(
            [nonce, proposal.proposal_id, proposal.digest(), proposal.side.value, f"{expires_at:.6f}"]
        )
        return hmac.new(self._secret, payload.encode("utf-8"), hashlib.sha256).hexdigest()

    def _mint(self, proposal: Proposal, score: float, *, now: Optional[float] = None) -> AuthToken:
        issued = time.time() if now is None else float(now)
        expires = issued + self.token_ttl_s
        nonce = secrets.token_hex(16)
        sig = self._sign(nonce, proposal, expires)
        tok = AuthToken(
            token=f"{nonce}.{sig}",
            proposal_id=proposal.proposal_id,
            digest=proposal.digest(),
            direction=proposal.side,
            score_pct=score,
            issued_at=issued,
            expires_at=expires,
        )
        with self._lock:
            self._issued[tok.token] = tok
        return tok

    def verify(
        self, token: str, proposal: Proposal, *, now: Optional[float] = None
    ) -> AuthToken:
        """Check a token without spending it. Raises TokenError if it is not good."""
        if not isinstance(token, str) or "." not in token:
            raise TokenError("authorisation token is missing or malformed")
        with self._lock:
            rec = self._issued.get(token)
            if rec is None:
                raise TokenError("authorisation token was not issued by this engine")
            if token in self._consumed:
                raise TokenError(
                    f"authorisation token for {rec.proposal_id} has already been spent; "
                    f"tokens are single-use"
                )
            nonce, sig = token.split(".", 1)
            expect = self._sign(nonce, proposal, rec.expires_at)
            if not hmac.compare_digest(sig, expect):
                raise TokenError("authorisation signature does not match this proposal")
            if rec.digest != proposal.digest():
                raise TokenError(
                    f"authorisation was minted for a different order "
                    f"(digest {rec.digest[:12]}... != {proposal.digest()[:12]}...)"
                )
            if rec.expired(now):
                raise TokenError(
                    f"authorisation for {rec.proposal_id} expired "
                    f"{(time.time() if now is None else now) - rec.expires_at:.1f}s ago; "
                    f"the market has moved since the vote"
                )
            return rec

    def consume(self, token: str, proposal: Proposal, *, now: Optional[float] = None) -> AuthToken:
        """Spend a token. Atomic: a second call for the same token always fails."""
        with self._lock:
            rec = self.verify(token, proposal, now=now)
            self._consumed.add(token)
            return rec

    def outstanding_tokens(self) -> int:
        with self._lock:
            return len(self._issued) - len(self._consumed)

    # -- reporting ---------------------------------------------------------

    def history(self, limit: int = 50) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._history[-int(limit) :])

    def status(self) -> dict[str, Any]:
        with self._lock:
            stats = dict(self.stats)
            outstanding = len(self._issued) - len(self._consumed)
        return {
            "execute_threshold_pct": self.execute_threshold_pct,
            "adaptive": self.adaptive.as_dict(),
            "hold_threshold_pct": self.hold_threshold_pct,
            "token_ttl_s": self.token_ttl_s,
            "analysts": self.registry.analyst_names(),
            "weights": self.registry.normalised_weights(),
            "stats": stats,
            "outstanding_tokens": outstanding,
        }


# ---------------------------------------------------------------------------
# Helpers used by the demo, the self-test and the swarm
# ---------------------------------------------------------------------------


def build_quorum(
    registry: AgentRegistry,
    stance: VoteStance,
    confidence: float,
    *,
    overrides: Optional[Mapping[str, tuple[VoteStance, float]]] = None,
    veto: bool = False,
) -> list[Vote]:
    """A full six-analyst quorum, uniform except where `overrides` says otherwise.

    Exists so tests and demos state only what they are varying. `overrides` maps
    an agent name to `(stance, confidence)`.
    """
    over = dict(overrides or {})
    out: list[Vote] = []
    for name in registry.analyst_names():
        s, c = over.get(name, (stance, confidence))
        out.append(
            Vote(
                agent=name,
                stance=s,
                confidence=c,
                rationale=f"{name} synthetic vote",
                veto=bool(veto and registry.agents[name].veto_power),
            )
        )
    return out


def minimum_uniform_confidence(engine: ConsensusEngine, threshold_pct: float) -> float:
    """The uniform confidence at which a unanimous quorum exactly reaches a band.

    With normalised weights summing to 1, a unanimous vote at confidence c scores
    100*c, so this is just threshold/100 - but naming it keeps the arithmetic in
    one place instead of scattered through tests as bare literals.
    """
    return threshold_pct / 100.0


# ---------------------------------------------------------------------------
# Self-test. No network, no GPU, no external packages.
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
        except Exception as exc:  # noqa: BLE001 - a self-test reports, never crashes
            failed.append(f"{name}: unexpected {type(exc).__name__}: {exc}")

    reg = AgentRegistry()
    prop = Proposal("p-1", "BTC-USD", VoteStance.BUY, qty=3, price_cents=6_000_000)

    def fresh() -> ConsensusEngine:
        return ConsensusEngine(reg)

    def t_roster_is_eight() -> None:
        assert len(reg.agents) == 8, len(reg.agents)
        assert reg.analyst_names() == list(AgentRegistry.REQUIRED_ANALYSTS)

    def t_weights_normalise_to_one() -> None:
        total = sum(reg.normalised_weights().values())
        assert abs(total - 1.0) < 1e-12, total

    def t_unanimous_high_executes() -> None:
        e = fresh()
        r = e.evaluate(prop, build_quorum(reg, VoteStance.BUY, 0.90))
        assert r.decision is ConsensusDecision.EXECUTE, r.reason
        assert abs(r.score_pct - 90.0) < 1e-9, r.score_pct
        assert r.token is not None

    def t_exact_threshold_executes() -> None:
        e = fresh()
        c = minimum_uniform_confidence(e, EXECUTE_THRESHOLD_PCT)
        r = e.evaluate(prop, build_quorum(reg, VoteStance.BUY, c))
        assert r.decision is ConsensusDecision.EXECUTE, (r.score_pct, r.reason)

    def t_hold_band() -> None:
        e = fresh()
        r = e.evaluate(prop, build_quorum(reg, VoteStance.BUY, 0.73))
        assert r.decision is ConsensusDecision.HOLD, (r.score_pct, r.reason)
        assert r.token is None

    def t_below_hold_is_veto() -> None:
        e = fresh()
        r = e.evaluate(prop, build_quorum(reg, VoteStance.BUY, 0.60))
        assert r.decision is ConsensusDecision.VETO, r.reason
        assert r.veto_source == "RUNE_THRESHOLD", r.veto_source

    def t_rune_veto_beats_high_score() -> None:
        e = fresh()
        votes = build_quorum(reg, VoteStance.BUY, 0.99, veto=True)
        r = e.evaluate(prop, votes)
        assert r.decision is ConsensusDecision.VETO, r.reason
        assert r.score_pct >= 98.0, r.score_pct
        assert r.token is None

    def t_dissent_subtracts() -> None:
        e = fresh()
        votes = build_quorum(
            reg, VoteStance.BUY, 1.0, overrides={"NORO": (VoteStance.SELL, 1.0)}
        )
        r = e.evaluate(prop, votes)
        # NORO carries 0.20 of the weight: 0.80 - 0.20 = 0.60 -> 60%.
        assert abs(r.score_pct - 60.0) < 1e-9, r.score_pct
        assert r.decision is ConsensusDecision.VETO

    def t_hold_withholds_without_opposing() -> None:
        e = fresh()
        votes = build_quorum(reg, VoteStance.BUY, 1.0, overrides={"LUMEN": (VoteStance.HOLD, 1.0)})
        r = e.evaluate(prop, votes)
        assert abs(r.score_pct - 88.0) < 1e-9, r.score_pct

    def t_missing_vote_cancels() -> None:
        e = fresh()
        votes = build_quorum(reg, VoteStance.BUY, 0.99)[:-1]
        r = e.evaluate(prop, votes)
        assert r.decision is ConsensusDecision.VETO
        assert r.veto_source == "QUORUM", r.veto_source

    def t_non_analyst_vote_cancels() -> None:
        e = fresh()
        votes = build_quorum(reg, VoteStance.BUY, 0.99)
        votes.append(Vote("VESKA", VoteStance.BUY, 1.0))
        r = e.evaluate(prop, votes)
        assert r.decision is ConsensusDecision.VETO
        assert "not permitted to vote" in r.reason, r.reason

    def t_token_single_use() -> None:
        e = fresh()
        r = e.evaluate(prop, build_quorum(reg, VoteStance.BUY, 0.9))
        assert r.token is not None
        e.consume(r.token.token, prop)
        try:
            e.consume(r.token.token, prop)
        except TokenError:
            return
        raise AssertionError("a spent token was accepted a second time")

    def t_token_bound_to_order() -> None:
        e = fresh()
        r = e.evaluate(prop, build_quorum(reg, VoteStance.BUY, 0.9))
        bigger = Proposal("p-1", "BTC-USD", VoteStance.BUY, qty=30, price_cents=6_000_000)
        try:
            e.consume(r.token.token, bigger)
        except TokenError:
            return
        raise AssertionError("a token authorised a quantity it was not minted for")

    def t_token_expires() -> None:
        e = ConsensusEngine(reg, token_ttl_s=0.01)
        r = e.evaluate(prop, build_quorum(reg, VoteStance.BUY, 0.9))
        try:
            e.consume(r.token.token, prop, now=time.time() + 5.0)
        except TokenError:
            return
        raise AssertionError("an expired token was accepted")

    def t_forged_token_refused() -> None:
        e = fresh()
        try:
            e.consume("deadbeef.cafe", prop)
        except TokenError:
            return
        raise AssertionError("a forged token was accepted")

    def t_bad_confidence_rejected() -> None:
        for bad in (1.5, -0.1, float("nan"), True, "0.9"):
            try:
                Vote("RUNE", VoteStance.BUY, bad)
            except QuorumError:
                continue
            raise AssertionError(f"confidence {bad!r} was accepted")

    def t_veto_flag_needs_veto_power() -> None:
        e = fresh()
        votes = build_quorum(reg, VoteStance.BUY, 0.99)
        votes[0] = Vote(votes[0].agent, VoteStance.BUY, 0.99, veto=True)
        r = e.evaluate(prop, votes)
        assert r.decision is ConsensusDecision.VETO
        assert "holds no veto power" in r.reason, r.reason

    def t_sell_proposal_scores_on_sell() -> None:
        e = fresh()
        sell = Proposal("p-2", "ETH-USD", VoteStance.SELL, qty=5, price_cents=300_000)
        r = e.evaluate(sell, build_quorum(reg, VoteStance.SELL, 0.9))
        assert r.decision is ConsensusDecision.EXECUTE, r.reason

    def t_opposite_direction_never_executes() -> None:
        e = fresh()
        r = e.evaluate(prop, build_quorum(reg, VoteStance.SELL, 1.0))
        assert r.score_pct == 0.0, r.score_pct
        assert r.decision is ConsensusDecision.VETO

    for name, fn in sorted(locals().items()):
        if name.startswith("t_") and callable(fn):
            check(name[2:], fn)

    print(f"consensus_engine self-test: {passed} passed, {len(failed)} failed")
    for f in failed:
        print(f"  FAIL {f}")
    return 0 if not failed else 1


def _demo() -> int:
    reg = AgentRegistry()
    engine = ConsensusEngine(reg)
    prop = Proposal("demo-1", "BTC-USD", VoteStance.BUY, qty=2, price_cents=6_412_500)

    scenarios = [
        ("unanimous conviction", build_quorum(reg, VoteStance.BUY, 0.92), None),
        ("thin majority", build_quorum(reg, VoteStance.BUY, 0.73), None),
        (
            "quant dissents",
            build_quorum(reg, VoteStance.BUY, 0.95, overrides={"NORO": (VoteStance.SELL, 0.8)}),
            None,
        ),
        ("RUNE vetoes a strong book", build_quorum(reg, VoteStance.BUY, 0.97, veto=True), None),
    ]

    print("=" * 78)
    print(" GrokTOPUS consensus gate - EXECUTE >= 76.8% | HOLD >= 70.0% | else cancelled")
    print("=" * 78)
    print(f" proposal: BUY {prop.qty} {prop.symbol} @ {prop.price_cents/100:,.2f} "
          f"(notional ${prop.notional_cents/100:,.2f})")
    print("-" * 78)
    for label, votes, _ in scenarios:
        r = engine.evaluate(prop, votes)
        mark = {"EXECUTE": "AUTHORISED", "HOLD": "hold", "VETO": "CANCELLED"}[r.decision.value]
        print(f" {label:<28} {r.score_pct:6.2f}%  {mark}")
        print(f"   -> {r.reason}")
    print("-" * 78)
    print(" weights:", ", ".join(f"{k}={v:.2f}" for k, v in reg.normalised_weights().items()))
    print(" stats:  ", engine.stats)
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="GrokTOPUS consensus engine")
    ap.add_argument("--selftest", action="store_true", help="run the built-in checks")
    ap.add_argument("--demo", action="store_true", help="score four illustrative quorums")
    ap.add_argument("--status", action="store_true", help="print engine + roster status as JSON")
    ap.add_argument("--config", default=str(DEFAULT_AGENT_CONFIG), help="agent config path")
    args = ap.parse_args(argv)

    if args.selftest:
        return _selftest()
    if args.status:
        engine = ConsensusEngine(AgentRegistry(args.config))
        print(json.dumps({"engine": engine.status(), "roster": engine.registry.as_dict()},
                         indent=2, ensure_ascii=False))
        return 0
    return _demo()


if __name__ == "__main__":
    raise SystemExit(main())
