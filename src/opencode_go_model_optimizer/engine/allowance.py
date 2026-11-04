"""Allowance ranking engine matching the proven dotfiles heuristic."""

from __future__ import annotations

import math

from opencode_go_model_optimizer.ingest.models import NormalizedModel


def tie_break_key(m: NormalizedModel) -> tuple[str, int, str]:
    """Deterministic tie-breaking key: release_date desc, context desc, id asc."""
    # Negate context for descending numerical sort
    # Reverse string for release_date by using raw string in reverse or sort lambda
    return (m.release_date or "", m.limit.context, m.id)


def is_eligible_free(m: NormalizedModel, allow_contributor: bool = True) -> bool:
    """Determine if a model is eligible for the free tier."""
    if m.status != "active" or m.is_preview:
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

    # Stable multi-pass sort: id ASC -> context DESC -> release_date DESC
    pass1 = sorted(eligible, key=lambda m: m.id)
    pass2 = sorted(pass1, key=lambda m: m.limit.context, reverse=True)
    pass3 = sorted(pass2, key=lambda m: m.release_date or "", reverse=True)
    return pass3


def rank_paid_models(
    models: list[NormalizedModel],
    skip_models: set[str] | None = None,
) -> list[NormalizedModel]:
    """Rank eligible paid models by allowance DESC, output cost ASC, release_date DESC, context DESC, id ASC."""
    skips = skip_models or set()
    eligible = [
        m for m in models if is_eligible_paid(m) and m.id not in skips and m.full_id not in skips
    ]

    # Deterministic multi-key sort:
    # 1. allowance DESC -> -allowance
    # 2. cost.output ASC -> cost.output
    # 3. release_date DESC -> inverted via custom tuple comparison or multi-pass stable sort
    def sort_key(m: NormalizedModel) -> tuple[float, float, str, int, str]:
        allowance = m.monthly_allowance or 0.0
        return (
            -allowance,
            m.cost.output,
        )

    # Use stable sort passes from least significant to most significant:
    # 1. id ASC
    pass1 = sorted(eligible, key=lambda m: m.id)
    # 2. context DESC
    pass2 = sorted(pass1, key=lambda m: m.limit.context, reverse=True)
    # 3. release_date DESC
    pass3 = sorted(pass2, key=lambda m: m.release_date or "", reverse=True)
    # 4. output cost ASC
    pass4 = sorted(pass3, key=lambda m: m.cost.output)
    # 5. allowance DESC
    pass5 = sorted(pass4, key=lambda m: m.monthly_allowance or 0.0, reverse=True)

    return pass5
