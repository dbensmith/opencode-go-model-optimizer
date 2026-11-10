"""Pareto efficiency and Fisher-Jenks natural breaks ranking engine."""

from __future__ import annotations

import bisect
import math
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
        return 100000.0

    return (1000.0 / turns_per_mo) * 100.0


def ssd(arr: Sequence[float]) -> float:
    """Sum of squared deviations from the mean."""
    if not arr:
        return 0.0
    mean = sum(arr) / len(arr)
    return sum((x - mean) ** 2 for x in arr)


def effective_quality(m: NormalizedModel) -> float:
    """Compute effective quality score combining intelligence and coding indices."""
    if m.aa_iq is not None and m.aa_coding is not None:
        return 0.7 * m.aa_iq + 0.3 * m.aa_coding
    if m.aa_iq is not None:
        return float(m.aa_iq)
    if m.aa_coding is not None:
        return float(m.aa_coding)
    return 0.0


def fisher_jenks_3_clusters(
    models: list[NormalizedModel],
    key: Callable[[NormalizedModel], float] | None = None,
) -> tuple[list[NormalizedModel], list[NormalizedModel], list[NormalizedModel]]:
    """1D Fisher-Jenks Natural Breaks into 3 clusters (WCSS minimization)."""
    val_fn = key or effective_quality
    sorted_m = sorted(models, key=val_fn, reverse=True)
    qs = [val_fn(x) for x in sorted_m]
    n = len(qs)

    if n <= 1:
        return sorted_m, [], []
    if n == 2:
        return sorted_m[:1], sorted_m[1:], []
    if n == 3:
        return sorted_m[:1], sorted_m[1:2], sorted_m[2:]

    p1 = [0.0] * (n + 1)
    p2 = [0.0] * (n + 1)
    for idx, val in enumerate(qs, start=1):
        p1[idx] = p1[idx - 1] + val
        p2[idx] = p2[idx - 1] + val * val

    def range_ssd(start: int, end: int) -> float:
        cnt = end - start
        if cnt <= 1:
            return 0.0
        s1 = p1[end] - p1[start]
        s2 = p2[end] - p2[start]
        return max(0.0, s2 - (s1 * s1) / cnt)

    best_wcss = float("inf")
    best_splits = (1, 2)

    for i in range(1, n - 1):
        for j in range(i + 1, n):
            # Avoid splitting identical values
            if qs[i - 1] == qs[i] or qs[j - 1] == qs[j]:
                continue
            wcss = range_ssd(0, i) + range_ssd(i, j) + range_ssd(j, n)
            if wcss < best_wcss:
                best_wcss = wcss
                best_splits = (i, j)

    # Fall back to unconstrained search if identical values prevented valid splits
    if best_wcss == float("inf"):
        for i in range(1, n - 1):
            for j in range(i + 1, n):
                wcss = range_ssd(0, i) + range_ssd(i, j) + range_ssd(j, n)
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
        and (m.aa_iq is not None or m.aa_coding is not None)
        and not m.is_zero_cost
        and not m.is_contributor
        and not m.is_preview
    ]
    if not benchmarked:
        return models

    # 2. Compute BPE over benchmarked pool
    qb_list = [(effective_quality(m), m.composite_burn_pct or 0.0) for m in benchmarked]

    # Precompute min_burn_at_q using unique qualities sorted descending
    unique_qs = sorted({q for q, _ in qb_list}, reverse=True)
    q_to_min_burn: dict[float, float] = {}
    running_min_burn = float("inf")
    for uq in unique_qs:
        burns_at_uq = [b for q, b in qb_list if q == uq]
        running_min_burn = min(running_min_burn, min(burns_at_uq))
        q_to_min_burn[uq] = running_min_burn

    # Precompute max_q_at_burn using models sorted by burn ascending
    sorted_by_burn = sorted(
        [item for item in qb_list if not math.isnan(item[1])],
        key=lambda x: x[1],
    )
    burns_sorted = [x[1] for x in sorted_by_burn]
    prefix_max_q: list[float] = []
    curr_max_q = -float("inf")
    for q_val, _ in sorted_by_burn:
        curr_max_q = max(curr_max_q, q_val)
        prefix_max_q.append(curr_max_q)

    for idx, m in enumerate(benchmarked):
        q, burn = qb_list[idx]

        # Factor 1: Burn Efficiency against peers with Quality >= q
        min_burn_at_q = q_to_min_burn.get(q, burn)
        if math.isinf(burn) or math.isnan(burn) or burn <= 0.0:
            burn_eff = 1.0 if burn == 0.0 else 0.0
        else:
            burn_eff = min(1.0, min_burn_at_q / burn)

        # Factor 2: Quality Efficiency against peers with burn <= burn
        if math.isnan(burn) or not burns_sorted:
            max_q_at_burn = q
        else:
            idx_b = bisect.bisect_right(burns_sorted, burn + 1e-9)
            max_q_at_burn = prefix_max_q[idx_b - 1] if idx_b > 0 else q

        qual_eff = min(1.0, (q / max_q_at_burn) ** 2) if max_q_at_burn > 0 else 1.0

        if math.isnan(burn_eff):
            burn_eff = 0.0
        if math.isnan(qual_eff):
            qual_eff = 0.0

        bpe = burn_eff * qual_eff
        is_pareto = (burn <= min_burn_at_q + 1e-9) and (q >= max_q_at_burn - 1e-9)

        m.bpe = bpe
        m.is_pareto = is_pareto

    return models
