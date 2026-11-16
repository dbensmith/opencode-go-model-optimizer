"""Filter and ranking knobs shared by every selection entrypoint."""

from dataclasses import dataclass, field
from datetime import datetime

from opencode_go_model_optimizer.ingest.models import NormalizedModel


@dataclass(frozen=True)
class SelectionCriteria:
    """Immutable bundle of filters and ranking parameters.

    Adding a knob is a one-line change here: engine functions receive the whole object.
    """

    mode: str = "allowance"
    top_n: int = 1
    preferred_tiers: dict[str, str] = field(default_factory=dict)
    skip_models: frozenset[str] = frozenset()
    allow_contributor: bool = True
    # OpenCode Go workspace privacy defaults. Everything is opt-in:
    # - Global-region models are blocked.
    # - Paid endpoints that train on request data are blocked.
    # - Go models that require consent to train are disabled.
    # - Free endpoints that train on request data are blocked (disables those free models).
    allow_global_region: bool = False
    allow_paid_training: bool = False
    allow_consent_training: bool = False
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
    # Quota burn basis: tokens (synthetic mix) | estimate (docs request estimates) | auto
    # (estimate unless --tokens or a non-general profile asks for the synthetic model).
    burn_basis: str = "auto"
    at: datetime | None = None  # reference UTC time; None = current time


def _is_free_candidate(m: NormalizedModel) -> bool:
    """Whether a model is in the free pool for the purpose of the training toggles."""
    return m.is_zero_cost or (
        m.is_contributor and (m.monthly_allowance == float("inf") or m.cost.output == 0)
    )


def passes_privacy(m: NormalizedModel, c: SelectionCriteria) -> bool:
    """Return True when a model satisfies the privacy and region criteria.

    Models without privacy data pass unless a retention or ZDR requirement is set
    (unknown is not compliant). Training is controlled separately for free models, paid
    models, and paid models that require consent (contributor models).
    """
    if not c.allow_contributor and m.is_contributor:
        return False
    p = m.privacy
    if p is None:
        return not (c.require_zdr or c.max_retention_days is not None)
    if p.requires_global_region and not c.allow_global_region:
        return False
    if p.trains_on_data:
        if _is_free_candidate(m):
            if not c.allow_free_training:
                return False
        else:
            if not c.allow_paid_training:
                return False
            if m.is_contributor and not c.allow_consent_training:
                return False
    if c.require_zdr and p.zdr is not True:
        return False
    return not (
        c.max_retention_days is not None
        and (p.retention_days is None or p.retention_days > c.max_retention_days)
    )
