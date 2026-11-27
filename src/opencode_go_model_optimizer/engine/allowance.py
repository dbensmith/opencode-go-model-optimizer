"""Allowance ranking engine using deterministic allowance and cost heuristics."""

from collections.abc import Set as AbstractSet

from opencode_go_model_optimizer.engine.criteria import is_eligible_free, is_eligible_paid
from opencode_go_model_optimizer.ingest.models import NormalizedModel, matches_any_id


def _desc_str_key(s: str) -> tuple[int, ...]:
    """Produce a sort key that reverses lexicographical string order in a single pass."""
    return (*(-ord(c) for c in s), 0)


def tie_break_key(m: NormalizedModel) -> tuple[tuple[int, ...], int, str]:
    """Deterministic tie-breaking key: release_date desc, context desc, id asc."""
    return (_desc_str_key(m.release_date or ""), -m.limit.context, m.id)


def rank_free_models(
    models: list[NormalizedModel],
    skip_models: AbstractSet[str] | None = None,
) -> list[NormalizedModel]:
    """Rank eligible free models by release_date DESC, context DESC, id ASC."""
    skips = skip_models or set()
    eligible = [m for m in models if is_eligible_free(m) and not matches_any_id(m, skips)]

    return sorted(eligible, key=tie_break_key)


def rank_paid_models(
    models: list[NormalizedModel],
    skip_models: AbstractSet[str] | None = None,
) -> list[NormalizedModel]:
    """Rank eligible paid models by allowance DESC, output cost ASC, release_date DESC, context DESC, id ASC."""
    skips = skip_models or set()
    eligible = [m for m in models if is_eligible_paid(m) and not matches_any_id(m, skips)]

    return sorted(
        eligible,
        key=lambda m: (-(m.monthly_allowance or 0.0), m.cost.output, *tie_break_key(m)),
    )
