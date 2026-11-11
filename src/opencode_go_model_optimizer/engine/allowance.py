"""Allowance ranking engine using deterministic allowance and cost heuristics."""

import functools
import math

from opencode_go_model_optimizer.ingest.models import NormalizedModel


@functools.lru_cache(maxsize=256)
def _desc_str_key(s: str) -> tuple[int, ...]:
    """Produce a sort key that reverses lexicographical string order in a single pass."""
    return (*(-ord(c) for c in s), 0)


def tie_break_key(m: NormalizedModel) -> tuple[tuple[int, ...], int, str]:
    """Deterministic tie-breaking key: release_date desc, context desc, id asc."""
    return (_desc_str_key(m.release_date or ""), -m.limit.context, m.id)


def is_eligible_free(m: NormalizedModel, allow_contributor: bool = True) -> bool:
    """Determine if a model is eligible for the free tier."""
    if m.status != "active" or m.is_preview:
        return False
    if not allow_contributor and m.is_contributor:
        return False
    if m.is_zero_cost:
        return True
    return bool(
        allow_contributor
        and m.is_contributor
        and (m.monthly_allowance == float("inf") or m.cost.output == 0)
    )


def is_eligible_paid(m: NormalizedModel) -> bool:
    """Determine if a model is eligible for paid tiers under allowance mode."""
    if m.status != "active" or m.is_preview or m.is_zero_cost or m.is_contributor:
        return False
    allowance = m.monthly_allowance
    return allowance is not None and allowance > 0 and not math.isinf(allowance)


def rank_free_models(
    models: list[NormalizedModel],
    skip_models: set[str] | None = None,
    allow_contributor: bool = True,
) -> list[NormalizedModel]:
    """Rank eligible free models by release_date DESC, context DESC, id ASC."""
    skips = skip_models or set()
    eligible = [
        m
        for m in models
        if is_eligible_free(m, allow_contributor=allow_contributor)
        and m.id not in skips
        and m.full_id not in skips
    ]

    return sorted(
        eligible,
        key=lambda m: (_desc_str_key(m.release_date or ""), -m.limit.context, m.id),
    )


def rank_paid_models(
    models: list[NormalizedModel],
    skip_models: set[str] | None = None,
) -> list[NormalizedModel]:
    """Rank eligible paid models by allowance DESC, output cost ASC, release_date DESC, context DESC, id ASC."""
    skips = skip_models or set()
    eligible = [
        m for m in models if is_eligible_paid(m) and m.id not in skips and m.full_id not in skips
    ]

    return sorted(
        eligible,
        key=lambda m: (
            -(m.monthly_allowance or 0.0),
            m.cost.output,
            _desc_str_key(m.release_date or ""),
            -m.limit.context,
            m.id,
        ),
    )
