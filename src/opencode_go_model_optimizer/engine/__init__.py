"""Engines for ranking and capability tier allocation."""

from opencode_go_model_optimizer.engine.allowance import (
    is_eligible_free,
    is_eligible_paid,
    rank_free_models,
    rank_paid_models,
    tie_break_key,
)
from opencode_go_model_optimizer.engine.diff import (
    ModelChange,
    SnapshotDiff,
    diff_snapshots,
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
    allocate_tier_fallbacks,
    allocate_tiers,
    allocate_tiers_allowance,
    allocate_tiers_pareto,
    allocate_tiers_with_fallbacks,
    rank_catalog,
    resolve_mode,
)

__all__ = [
    "STANDARD_TIERS",
    "WORKLOAD_PROFILES",
    "ModelChange",
    "SnapshotDiff",
    "allocate_tier_fallbacks",
    "allocate_tiers",
    "allocate_tiers_allowance",
    "allocate_tiers_pareto",
    "allocate_tiers_with_fallbacks",
    "compute_quota_burn_pct",
    "compute_turn_cost",
    "diff_snapshots",
    "effective_quality",
    "evaluate_pareto",
    "fisher_jenks_3_clusters",
    "is_eligible_free",
    "is_eligible_paid",
    "rank_catalog",
    "rank_free_models",
    "rank_paid_models",
    "resolve_mode",
    "tie_break_key",
]
