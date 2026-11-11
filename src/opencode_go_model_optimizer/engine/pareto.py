"""Pareto efficiency and Fisher-Jenks natural breaks ranking engine."""

import bisect
import math
from collections.abc import Callable, Sequence
from collections.abc import Set as AbstractSet

from opencode_go_model_optimizer.engine.allowance import tie_break_key
from opencode_go_model_optimizer.ingest.models import NormalizedModel

# Uniform agentic turn token mix: 750 in / 60,000 cached read / 250 out
STD_IN = 750
STD_CACHE = 60000
STD_OUT = 250

WORKLOAD_PROFILES: dict[str, tuple[int, int, int]] = {
    "general": (STD_IN, STD_CACHE, STD_OUT),
    "coding": (1500, 100000, 800),
    "reasoning": (1000, 40000, 2000),
    "fast": (500, 20000, 150),
}


def compute_turn_cost(
    m: NormalizedModel,
    profile: str = "general",
    token_mix: tuple[int, int, int] | None = None,
) -> float:
    """Compute the cost in USD of a standardized agentic turn for a given profile."""
    if m.is_zero_cost:
        return 0.0
    t_in, t_cache, t_out = (
        token_mix
        if token_mix is not None
        else WORKLOAD_PROFILES.get(profile, (STD_IN, STD_CACHE, STD_OUT))
    )
    return (t_in * m.cost.input + t_cache * m.cost.cache_read + t_out * m.cost.output) / 1_000_000.0


def compute_quota_burn_pct(
    m: NormalizedModel,
    profile: str = "general",
    token_mix: tuple[int, int, int] | None = None,
) -> float:
    """Compute monthly quota burn % per 1,000 standardized turns for a given profile."""
    if m.is_zero_cost or m.monthly_allowance == float("inf"):
        return 0.0

    allowance = m.monthly_allowance or 15.0
    turn_cost = compute_turn_cost(m, profile=profile, token_mix=token_mix)
    if turn_cost <= 0.0:
        return 0.0

    turns_per_mo = allowance / turn_cost
    if turns_per_mo <= 0.0:
        return 100000.0

    return (1000.0 / turns_per_mo) * 100.0


BURN_BASES = ("auto", "tokens", "estimate")


def resolve_burn_basis(
    basis: str, profile: str = "general", token_mix: tuple[int, int, int] | None = None
) -> str:
    """Resolve ``auto`` to ``estimate`` (default mix) or ``tokens`` (custom mix or profile)."""
    if basis not in BURN_BASES:
        raise ValueError(f"unknown burn basis {basis!r}; choose from {', '.join(BURN_BASES)}")
    if basis == "auto":
        return "estimate" if token_mix is None and profile == "general" else "tokens"
    return basis


def compute_burn_pct(
    m: NormalizedModel,
    profile: str = "general",
    token_mix: tuple[int, int, int] | None = None,
    basis: str = "auto",
) -> float:
    """Monthly quota burn % per 1,000 requests; the single entry point for every consumer.

    ``tokens`` synthesises it from a token mix. ``estimate`` uses the docs-published
    requests per month for the active plan, scaled by the model's effective price
    multiplier (published estimates are at list price); models without an estimate fall
    back to ``tokens``.
    """
    if resolve_burn_basis(basis, profile, token_mix) == "estimate" and not m.is_zero_cost:
        est = m.request_estimate
        if est is not None and est.per_month > 0:
            if math.isinf(est.per_month):
                return 0.0
            return 1000.0 / est.per_month * 100.0 * (m.price_multiplier or 1.0)
    return compute_quota_burn_pct(m, profile=profile, token_mix=token_mix)


def ssd(arr: Sequence[float]) -> float:
    """Sum of squared deviations from the mean."""
    if not arr:
        return 0.0
    mean = sum(arr) / len(arr)
    return sum((x - mean) ** 2 for x in arr)


def effective_quality(
    m: NormalizedModel,
    profile: str = "general",
    weights: tuple[float, float] | None = None,
) -> float:
    """Compute effective quality score combining intelligence and coding indices."""
    if m.aa_iq is not None and m.aa_coding is not None:
        if weights is not None:
            iq_weight, coding_weight = weights
            base_q = iq_weight * m.aa_iq + coding_weight * m.aa_coding
        elif profile == "coding":
            base_q = 0.4 * m.aa_iq + 0.6 * m.aa_coding
        elif profile == "reasoning":
            base_q = 0.85 * m.aa_iq + 0.15 * m.aa_coding
        else:
            base_q = 0.7 * m.aa_iq + 0.3 * m.aa_coding
    elif m.aa_iq is not None:
        base_q = float(m.aa_iq)
    elif m.aa_coding is not None:
        base_q = float(m.aa_coding)
    else:
        base_q = 0.0

    if profile == "fast" and base_q > 0 and m.speed_tps is not None and m.speed_tps > 0:
        return base_q * (0.85 + 0.15 * min(2.0, m.speed_tps / 100.0))
    return base_q


def fisher_jenks_3_clusters(
    models: list[NormalizedModel],
    key: Callable[[NormalizedModel], float] | None = None,
) -> tuple[list[NormalizedModel], list[NormalizedModel], list[NormalizedModel]]:
    """1D Fisher-Jenks Natural Breaks into 3 clusters (WCSS minimization)."""
    val_fn = key or effective_quality
    decorated = []
    for m in models:
        v = val_fn(m)
        decorated.append((-v, tie_break_key(m), v, m))
    decorated.sort()
    sorted_m = [item[3] for item in decorated]
    qs = [item[2] for item in decorated]
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
    profile: str = "general",
    skip_models: AbstractSet[str] | None = None,
    token_mix: tuple[int, int, int] | None = None,
    weights: tuple[float, float] | None = None,
    burn_basis: str = "auto",
) -> list[NormalizedModel]:
    """Return copies of the models with Quota Burn and Bidirectional Pareto Efficiency (BPE) set.

    The input models are never mutated.
    """
    skips = skip_models or frozenset()

    # 1. Copy, clear stale metrics and compute burn for each model
    models = [m.model_copy() for m in models]
    for m in models:
        m.bpe = None
        m.is_pareto = None
        m.composite_burn_pct = compute_burn_pct(
            m, profile=profile, token_mix=token_mix, basis=burn_basis
        )

    # Models with benchmark intelligence (excluding contributor promos, previews, and skipped models)
    benchmarked = [
        m
        for m in models
        if m.status == "active"
        and (m.aa_iq is not None or m.aa_coding is not None)
        and not m.is_zero_cost
        and not m.is_contributor
        and not m.is_preview
        and m.id not in skips
        and m.full_id not in skips
    ]
    if not benchmarked:
        return models

    # 2. Compute BPE over benchmarked pool
    qb_list = [
        (effective_quality(m, profile=profile, weights=weights), m.composite_burn_pct or 0.0)
        for m in benchmarked
    ]

    # Precompute min_burn_at_q using single-pass min_burn_by_q
    min_burn_by_q: dict[float, float] = {}
    for q, b in qb_list:
        if not math.isnan(b):
            curr = min_burn_by_q.get(q)
            if curr is None or b < curr:
                min_burn_by_q[q] = b
        elif q not in min_burn_by_q:
            min_burn_by_q[q] = float("inf")

    q_to_min_burn: dict[float, float] = {}
    running_min_burn = float("inf")
    for uq in sorted(min_burn_by_q, reverse=True):
        running_min_burn = min(running_min_burn, min_burn_by_q[uq])
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
