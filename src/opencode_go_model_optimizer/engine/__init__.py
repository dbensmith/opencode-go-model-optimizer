"""Engines for ranking and capability tier allocation."""

from opencode_go_model_optimizer.engine.allowance import (
    is_eligible_free,
    is_eligible_paid,
    rank_free_models,
    rank_paid_models,
    tie_break_key,
)
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria, passes_privacy
from opencode_go_model_optimizer.engine.diff import (
    ModelChange,
    SnapshotDiff,
    diff_snapshots,
)
from opencode_go_model_optimizer.engine.pareto import (
    BURN_BASES,
    WORKLOAD_PROFILES,
    compute_burn_pct,
    compute_quota_burn_pct,
    compute_turn_cost,
    effective_quality,
    evaluate_pareto,
    filter_benchmarked_models,
    fisher_jenks_3_clusters,
)
from opencode_go_model_optimizer.engine.pricing import (
    PRICE_BASES,
    apply_price_basis,
)
from opencode_go_model_optimizer.engine.tiers import (
    PAID_TIERS,
    STANDARD_TIERS,
    TierSelection,
    rank_catalog,
    resolve_mode,
    select_tiers,
)

__all__ = [
    "BURN_BASES",
    "PAID_TIERS",
    "PRICE_BASES",
    "STANDARD_TIERS",
    "WORKLOAD_PROFILES",
    "ModelChange",
    "SelectionCriteria",
    "SnapshotDiff",
    "TierSelection",
    "apply_price_basis",
    "compute_burn_pct",
    "compute_quota_burn_pct",
    "compute_turn_cost",
    "diff_snapshots",
    "effective_quality",
    "evaluate_pareto",
    "filter_benchmarked_models",
    "fisher_jenks_3_clusters",
    "is_eligible_free",
    "is_eligible_paid",
    "passes_privacy",
    "rank_catalog",
    "rank_free_models",
    "rank_paid_models",
    "resolve_mode",
    "select_tiers",
    "tie_break_key",
]
