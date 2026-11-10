"""Engines for ranking and capability tier allocation."""

from opencode_go_model_optimizer.engine.allowance import (
    is_eligible_free,
    is_eligible_paid,
    rank_free_models,
    rank_paid_models,
    tie_break_key,
)
from opencode_go_model_optimizer.engine.pareto import (
    WORKLOAD_PROFILES,
    compute_quota_burn_pct,
    compute_turn_cost,
    effective_quality,
    evaluate_pareto,
    fisher_jenks_3_clusters,
)
from opencode_go_model_optimizer.engine.tiers import (
    STANDARD_TIERS,
    allocate_tiers,
    allocate_tiers_allowance,
    allocate_tiers_pareto,
    rank_catalog,
)

__all__ = [
    "STANDARD_TIERS",
    "WORKLOAD_PROFILES",
    "allocate_tiers",
    "allocate_tiers_allowance",
    "allocate_tiers_pareto",
    "compute_quota_burn_pct",
    "compute_turn_cost",
    "effective_quality",
    "evaluate_pareto",
    "fisher_jenks_3_clusters",
    "is_eligible_free",
    "is_eligible_paid",
    "rank_catalog",
    "rank_free_models",
    "rank_paid_models",
    "tie_break_key",
]
