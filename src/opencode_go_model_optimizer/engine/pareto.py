"""Pareto efficiency and Fisher-Jenks natural breaks ranking engine."""

from __future__ import annotations

from collections.abc import Callable, Sequence

from opencode_go_model_optimizer.ingest.models import NormalizedModel

# Uniform agentic turn token mix: 750 in / 60,000 cached read / 250 out
STD_IN = 750
STD_CACHE = 60000
STD_OUT = 250


def compute_turn_cost(m: NormalizedModel) -> float:
    """Compute the cost in USD of a standardized agentic turn."""
    if m.is_zero_cost:
        return 0.0
    return (
        STD_IN * m.cost.input + STD_CACHE * m.cost.cache_read + STD_OUT * m.cost.output
    ) / 1_000_000.0


def compute_quota_burn_pct(m: NormalizedModel) -> float:
    """Compute monthly quota burn % per 1,000 standardized turns."""
    if m.is_zero_cost or m.monthly_allowance == float("inf"):
        return 0.0

    allowance = m.monthly_allowance or 15.0
    turn_cost = compute_turn_cost(m)
    if turn_cost <= 0.0:
        return 0.0

    turns_per_mo = allowance / turn_cost
    if turns_per_mo <= 0.0:
        return float("inf")

    return (1000.0 / turns_per_mo) * 100.0


def ssd(arr: Sequence[float]) -> float:
    """Sum of squared deviations from the mean."""
    if not arr:
        return 0.0
    mean = sum(arr) / len(arr)
    return sum((x - mean) ** 2 for x in arr)


def fisher_jenks_3_clusters(
    models: list[NormalizedModel],
    key: Callable[[NormalizedModel], float] | None = None,
) -> tuple[list[NormalizedModel], list[NormalizedModel], list[NormalizedModel]]:
    """1D Fisher-Jenks Natural Breaks into 3 clusters (WCSS minimization)."""
    val_fn = key or (lambda x: x.aa_iq or 0.0)
    sorted_m = sorted(models, key=val_fn, reverse=True)
    qs = [val_fn(x) for x in sorted_m]
    n = len(qs)

    if n <= 1:
        return sorted_m, [], []
    if n == 2:
        return sorted_m[:1], sorted_m[1:], []
    if n == 3:
        return sorted_m[:1], sorted_m[1:2], sorted_m[2:]

    best_wcss = float("inf")
    best_splits = (1, 2)

    for i in range(1, n - 1):
        for j in range(i + 1, n):
            # Avoid splitting identical values
            if qs[i - 1] == qs[i] or qs[j - 1] == qs[j]:
                continue
            wcss = ssd(qs[:i]) + ssd(qs[i:j]) + ssd(qs[j:])
            if wcss < best_wcss:
                best_wcss = wcss
                best_splits = (i, j)

    i, j = best_splits
    return sorted_m[:i], sorted_m[i:j], sorted_m[j:]


def evaluate_pareto(
    models: list[NormalizedModel],
) -> list[NormalizedModel]:
    """Compute Quota Burn and Bidirectional Pareto Efficiency (BPE) for all models."""
    # 1. Compute burn for each model
    for m in models:
        m.composite_burn_pct = compute_quota_burn_pct(m)

    # Models with benchmark intelligence (excluding contributor promos and previews)
    benchmarked = [
        m
        for m in models
        if m.status == "active"
        and m.aa_iq is not None
        and not m.is_zero_cost
        and not m.is_contributor
        and not m.is_preview
    ]
    if not benchmarked:
        return models

    # 2. Compute BPE over benchmarked pool
    for m in benchmarked:
        q = m.aa_iq or 0.0
        burn = m.composite_burn_pct or 0.0

        # Factor 1: Burn Efficiency against peers with IQ >= q
        peers_ge_q = [p for p in benchmarked if (p.aa_iq or 0.0) >= q]
        min_burn_at_q = min(p.composite_burn_pct or 0.0 for p in peers_ge_q) if peers_ge_q else burn
        burn_eff = 1.0 if burn == 0.0 else min(1.0, min_burn_at_q / burn)

        # Factor 2: Quality Efficiency against peers with burn <= burn
        peers_le_burn = [p for p in benchmarked if (p.composite_burn_pct or 0.0) <= burn + 1e-9]
        max_q_at_burn = max((p.aa_iq or 0.0) for p in peers_le_burn) if peers_le_burn else q
        qual_eff = min(1.0, (q / max_q_at_burn) ** 2) if max_q_at_burn > 0 else 1.0

        bpe = burn_eff * qual_eff
        is_pareto = (burn <= min_burn_at_q + 1e-9) and (q >= max_q_at_burn - 1e-9)

        m.bpe = bpe
        m.is_pareto = is_pareto

    return models
