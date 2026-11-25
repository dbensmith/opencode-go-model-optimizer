"""Structural contract checks against the real sources. No hardcoded real values."""

import pytest

from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.pricing import validate_multiplier
from opencode_go_model_optimizer.engine.tiers import select_tiers
from opencode_go_model_optimizer.ingest.docs import parse_privacy
from opencode_go_model_optimizer.ingest.snapshot import apply_plan

pytestmark = pytest.mark.live

PLANS = ("go", "go-plus", "go-teams")
MAX_UNMATCHED = 2  # tolerated active models without a match per source (listed in warnings)


def _active_chat(models):
    return [m for m in models if m.status == "active" and m.model_type == "chat"]


def test_privacy_table_is_non_empty(live_docs):
    assert parse_privacy(live_docs)


def test_catalogue_publishes_per_plan_usage(live_snapshot):
    """Allowances now come from `go.monthly_usage`, not the removed docs pricing table."""
    paid = [m for m in _active_chat(live_snapshot.models) if not m.is_zero_cost]
    assert paid
    for m in paid:
        assert m.plan_allowances, f"{m.id} has no go.monthly_usage"
        for plan in PLANS:
            assert m.plan_allowances.get(plan, 0) > 0, f"{m.id} has no {plan} usage"


def test_active_models_get_allowance_and_privacy(live_snapshot):
    active = _active_chat(live_snapshot.models)
    assert active
    warnings = "\n".join(live_snapshot.warnings)
    gaps = {
        "allowance": [m.id for m in active if not m.is_zero_cost and m.monthly_allowance is None],
        "privacy": [m.id for m in active if m.privacy is None],
        "request estimates": [
            m.id for m in active if not m.is_zero_cost and not m.request_estimates
        ],
    }
    for kind, ids in gaps.items():
        assert len(ids) <= MAX_UNMATCHED, f"too many models without {kind}: {ids}"
        for model_id in ids:
            assert model_id in warnings, f"{model_id} lacks {kind} but is not in the warnings"


@pytest.mark.parametrize("plan", PLANS)
def test_request_estimates_are_positive(live_snapshot, plan):
    models = apply_plan([m.model_copy(deep=True) for m in live_snapshot.models], plan)
    for m in models:
        if m.status != "active" or m.is_zero_cost:
            continue
        assert m.request_estimate is not None, m.id
        assert m.request_estimate.per_5h > 0, m.id
        assert m.request_estimate.per_month >= m.request_estimate.per_5h, m.id


def test_removed_docs_tables_do_not_warn(live_snapshot):
    """The docs no longer publish pricing/requests/endpoints tables; that is not an error."""
    stale = [w for w in live_snapshot.warnings if "no rows parsed" in w]
    assert not stale, stale


def test_multipliers_parse_without_unknown_conditions(live_snapshot):
    assert not [w for w in live_snapshot.warnings if "unsupported price multiplier" in w]
    for m in live_snapshot.models:
        for mult in m.multipliers:
            assert validate_multiplier(mult) is None, (m.id, mult)


@pytest.mark.parametrize("plan", PLANS)
def test_select_yields_paid_tiers(live_snapshot, plan):
    models = apply_plan([m.model_copy(deep=True) for m in live_snapshot.models], plan)
    tiers = select_tiers(models, SelectionCriteria(mode="allowance")).primary
    for tier in ("heavy", "balanced", "light"):
        assert tiers[tier], f"{plan}: empty {tier} tier"


def test_source_provenance_is_recorded(live_snapshot):
    sources = live_snapshot.metadata.sources
    assert {"catalogue", "docs", "models_dev"} <= set(sources)
    for name, src in sources.items():
        assert src["status"] == 200 and len(src["sha256"]) == 64 and src["bytes"] > 0, name


def test_console_profiles_are_fetched_for_api_models(live_snapshot):
    """The console scrape must yield profiles usable by the API models."""
    from opencode_go_model_optimizer.ingest.console import fetch_console_profiles

    profiles = fetch_console_profiles()
    assert profiles is not None
    api_ids = {m.id for m in live_snapshot.models}
    assert set(profiles.by_model) & api_ids
