"""Selection criteria and shared eligibility rules."""

import math
from dataclasses import dataclass, field
from datetime import datetime

from opencode_go_model_optimizer.ingest.models import (
    NormalizedModel,
    is_incomplete_free_model,
)


@dataclass(frozen=True)
class SelectionCriteria:
    """Immutable bundle of filters and ranking parameters.

    Adding a knob is a one-line change here: engine functions receive the whole object.
    """

    mode: str = "allowance"
    allow_fallback: bool = False
    top_n: int = 1
    preferred_tiers: dict[str, str] = field(default_factory=dict)
    skip_models: frozenset[str] = frozenset()
    # OpenCode Go workspace privacy defaults. Everything is opt-in:
    # - Global-region models are blocked.
    # - Paid endpoints that train on request data are blocked.
    # - Free endpoints that train on request data are blocked (disables those free models).
    allow_global_region: bool = False
    allow_paid_training: bool = False
    allow_free_training: bool = False
    max_retention_days: int | None = None
    require_zdr: bool = False
    min_context: int = 0
    min_output: int = 0
    max_output_cost: float | None = None
    max_burn: float | None = None
    profile: str = "general"
    token_mix: tuple[int, int, int] | None = None
    weights: tuple[float, float] | None = None
    # Price multiplier basis: now | blended (7-day average) | worst (7-day max) | list (ignore).
    price_basis: str = "blended"
    # Quota burn basis: tokens (synthetic mix) | estimate (computed request estimates) |
    # auto (estimate unless --tokens or a non-general profile asks for the synthetic model).
    burn_basis: str = "auto"
    at: datetime | None = None  # reference UTC time; None = current time


def _is_free_candidate(m: NormalizedModel) -> bool:
    """Whether a model is in the free pool for the purpose of the training toggles."""
    return m.is_zero_cost or (
        m.is_contributor and (m.monthly_allowance == float("inf") or m.cost.output == 0)
    )


def catalogue_eligibility_reason(m: NormalizedModel, *, require_chat: bool) -> str | None:
    """Return the first status, model-type, or incomplete-catalogue exclusion reason."""
    if m.status != "active":
        return f"status is {m.status!r} (not active)"
    if require_chat and m.model_type != "chat":
        return f"not a text model (type {m.model_type!r})"
    if is_incomplete_free_model(m):
        return "zero-priced but publishes a monthly allowance (incomplete catalogue data)"
    return None


def privacy_reason(m: NormalizedModel, c: SelectionCriteria) -> str | None:
    """Return why privacy criteria exclude a model."""
    p = m.privacy
    if p is None:
        if c.require_zdr or c.max_retention_days is not None:
            return "excluded by privacy criteria"
        return None
    if p.requires_global_region and not c.allow_global_region:
        return "requires Global workspace region (enable --allow-global-region)"
    if p.trains_on_data:
        if _is_free_candidate(m):
            if not c.allow_free_training:
                return "free model trains on request data (enable --allow-free-training)"
        elif not c.allow_paid_training:
            return "paid model trains on request data (enable --allow-paid-training)"
    if (c.require_zdr and p.zdr is not True) or (
        c.max_retention_days is not None
        and (p.retention_days is None or p.retention_days > c.max_retention_days)
    ):
        return "excluded by privacy criteria"
    return None


def passes_privacy(m: NormalizedModel, c: SelectionCriteria) -> bool:
    """Return True when a model satisfies the privacy and region criteria."""
    return privacy_reason(m, c) is None


def _paid_eligibility_reason(m: NormalizedModel) -> str | None:
    reason = catalogue_eligibility_reason(m, require_chat=False)
    if reason is not None:
        return reason
    if m.is_zero_cost:
        return "zero-priced model is not eligible for paid tiers"
    allowance = m.monthly_allowance
    if allowance is None:
        return "unpriced: no monthly allowance published for this plan"
    if not allowance > 0:
        return "unpriced: allowance is zero"
    if math.isinf(allowance):
        return "unlimited allowance on a priced model (not eligible for paid tiers)"
    return None


def is_eligible_free(m: NormalizedModel) -> bool:
    """Determine if a model is eligible for the free tier."""
    return catalogue_eligibility_reason(m, require_chat=False) is None and _is_free_candidate(m)


def is_eligible_paid(m: NormalizedModel) -> bool:
    """Determine if a model is eligible for paid tiers under allowance mode."""
    return _paid_eligibility_reason(m) is None


def exclusion_reasons(m: NormalizedModel, c: SelectionCriteria) -> list[str]:
    """Why a model cannot take a paid or free tier slot (empty when it is eligible)."""
    reason = catalogue_eligibility_reason(m, require_chat=True)
    if reason is not None:
        return [reason]
    reason = privacy_reason(m, c)
    if reason is not None:
        return [reason]
    if is_eligible_free(m):
        return []
    reason = _paid_eligibility_reason(m)
    return [reason] if reason is not None else []
