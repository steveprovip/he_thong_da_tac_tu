"""Small, deterministic HRP proposal engine for alpha-only shadow portfolios.

The result is a research weight proposal. Safety-gate weights and order sizing
are outside this module and remain fixed by core.consensus_engine.WEIGHTS.

`hrp_allocate` (2026-11-07) is the same algorithm over any named return series
(research sleeves, e.g. research/hrp_alpha_sleeves.py); `hrp_proposal` is the
three-alpha-agent wrapper ported from the factory worktree with identical output.
With two series HRP reduces to inverse-variance weights (no correlation term).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

import numpy as np

from core.consensus_engine import WEIGHTS

ALPHA_AGENTS = ("NORO", "LUMEN", "TIDAL")
ALPHA_BUDGET = sum(WEIGHTS[name] for name in ALPHA_AGENTS)


@dataclass(frozen=True)
class HRPProposal:
    weights: dict[str, float]
    observations: int
    order: tuple[str, ...]
    shrinkage: float


def _cluster_order(correlation: np.ndarray) -> list[int]:
    """Deterministic single-linkage clustering and leaf ordering (no SciPy dependency)."""
    clusters = [[i] for i in range(len(correlation))]
    while len(clusters) > 1:
        best = (float("inf"), 0, 1)
        for left in range(len(clusters)):
            for right in range(left + 1, len(clusters)):
                distance = min(float(1.0 - correlation[i, j])
                               for i in clusters[left] for j in clusters[right])
                candidate = (distance, left, right)
                if candidate < best:
                    best = candidate
        _, left, right = best
        merged = clusters[left] + clusters[right]
        clusters = [c for i, c in enumerate(clusters) if i not in (left, right)]
        clusters.append(merged)
        clusters.sort(key=lambda c: min(c))
    return clusters[0]


def hrp_proposal(daily_returns: Mapping[str, Sequence[float]], *, min_samples: int = 30,
                 shrinkage: float = 0.10, alpha_budget: float = ALPHA_BUDGET,
                 lower_share: float = 0.10, upper_share: float = 0.30) -> HRPProposal | None:
    """Return bounded HRP allocations across the three alpha agents, or None during warmup.

    Inputs must be aligned daily observations. Rows containing a non-finite return
    are dropped jointly to keep the correlation estimate synchronous.
    """
    if set(daily_returns) != set(ALPHA_AGENTS):
        raise ValueError(f"daily_returns must contain exactly {ALPHA_AGENTS}")
    return hrp_allocate(daily_returns, names=ALPHA_AGENTS, min_samples=min_samples, shrinkage=shrinkage,
                        budget=alpha_budget, lower=lower_share, upper=upper_share)


def hrp_allocate(returns: Mapping[str, Sequence[float]], *, names: Sequence[str] | None = None,
                 min_samples: int = 30, shrinkage: float = 0.10, budget: float = 1.0,
                 lower: float = 0.0, upper: float = 1.0) -> HRPProposal | None:
    """Bounded HRP weights over aligned return series (N >= 2), or None while fewer than
    `min_samples` complete rows exist. `names` fixes the column order (default: sorted keys);
    rows with a non-finite value are dropped jointly."""
    names = tuple(sorted(returns) if names is None else names)
    if len(names) < 2 or len(set(names)) != len(names) or set(names) != set(returns):
        raise ValueError("need two or more distinct series, and names must match the returns")
    if (not isinstance(min_samples, int) or min_samples < 3 or
            not 0.0 <= shrinkage <= 1.0 or not 0.0 < budget <= 1.0 or
            not 0.0 <= lower < upper <= 1.0 or
            lower * len(names) > budget or
            upper * len(names) < budget):
        raise ValueError("invalid HRP bounds or sample policy")
    lengths = {len(returns[name]) for name in names}
    if len(lengths) != 1:
        raise ValueError("daily return series must be aligned and equal length")
    alpha_budget, lower_share, upper_share = budget, lower, upper
    matrix = np.column_stack([np.asarray(returns[name], dtype=np.float64)
                              for name in names])
    matrix = matrix[np.isfinite(matrix).all(axis=1)]
    if len(matrix) < min_samples:
        return None

    covariance = np.cov(matrix, rowvar=False, ddof=1)
    if not np.isfinite(covariance).all():
        return None
    diagonal = np.diag(np.diag(covariance))
    covariance = (1.0 - shrinkage) * covariance + shrinkage * diagonal
    variances = np.maximum(np.diag(covariance), 1e-12)
    correlation = covariance / np.sqrt(np.outer(variances, variances))
    correlation = np.clip((correlation + correlation.T) * 0.5, -1.0, 1.0)
    np.fill_diagonal(correlation, 1.0)

    order = _cluster_order(correlation)
    ordered_cov = covariance[np.ix_(order, order)]
    weights = np.ones(len(order), dtype=np.float64)
    clusters = [list(range(len(order)))]
    while clusters:
        next_clusters = []
        for cluster in clusters:
            if len(cluster) <= 1:
                continue
            midpoint = len(cluster) // 2
            left, right = cluster[:midpoint], cluster[midpoint:]

            def cluster_variance(indices):
                cov = ordered_cov[np.ix_(indices, indices)]
                inverse_diag = 1.0 / np.maximum(np.diag(cov), 1e-12)
                local = inverse_diag / inverse_diag.sum()
                return max(float(local @ cov @ local), 1e-12)

            left_var, right_var = cluster_variance(left), cluster_variance(right)
            left_share = 1.0 - left_var / (left_var + right_var)
            weights[left] *= left_share
            weights[right] *= 1.0 - left_share
            next_clusters.extend((left, right))
        clusters = next_clusters

    # Project positive HRP shares onto explicit allocation bounds while preserving
    # the fixed 60% directional-agent budget.
    shares = weights / weights.sum()
    lo, hi = 0.0, alpha_budget / max(float(shares.min()), 1e-12) * 2.0
    lower, upper = lower_share, upper_share
    for _ in range(80):
        scale = (lo + hi) * 0.5
        projected = np.clip(shares * scale, lower, upper)
        if projected.sum() < alpha_budget:
            lo = scale
        else:
            hi = scale
    projected = np.clip(shares * ((lo + hi) * 0.5), lower, upper)
    # Correct floating point residue on the most flexible component.
    residue = alpha_budget - float(projected.sum())
    for index in np.argsort(-(upper - projected)):
        shift = min(max(residue, -(projected[index] - lower)), upper - projected[index])
        projected[index] += shift
        residue -= shift
        if abs(residue) < 1e-12:
            break
    by_name = {names[order[i]]: float(projected[i]) for i in range(len(order))}
    return HRPProposal(by_name, len(matrix), tuple(names[i] for i in order), shrinkage)
