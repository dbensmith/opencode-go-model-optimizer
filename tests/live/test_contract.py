"""Structural contract checks against the real sources. No hardcoded real values."""

import pytest

from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.pricing import validate_multiplier
from opencode_go_model_optimizer.engine.tiers import select_tiers
from opencode_go_model_optimizer.ingest.docs import (
    parse_endpoints_table,
    parse_go_pricing,
    parse_privacy,
    parse_request_estimates,
)
from opencode_go_model_optimizer.ingest.snapshot import apply_plan

pytestmark = pytest.mark.live

PLANS = ("go", "go-plus", "go-teams")
MAX_UNMATCHED = 2  # tolerated active models without a match per source (listed in warnings)


@pytest.mark.parametrize("plan", PLANS)
def test_pricing_table_parses_rows(live_docs, plan):
    assert parse_go_pricing(live_docs, plan=plan, fallback_to_go=False)


@pytest.mark.parametrize("plan", PLANS)
def test_request_estimates_parse_rows(live_docs, plan):
    assert parse_request_estimates(live_docs).get(plan)


def test_endpoints_table_is_non_empty(live_docs):
    assert parse_endpoints_table(live_docs)


def test_privacy_table_is_non_empty(live_docs):
    assert parse_privacy(live_docs)


def test_active_models_get_pricing_privacy_and_estimates(live_snapshot):
    active = [m for m in live_snapshot.models if m.status == "active"]
    assert active
    warnings = "\n".join(live_snapshot.warnings)
    gaps = {
        "pricing": [m.id for m in active if m.monthly_allowance is None],
        "privacy": [m.id for m in active if m.privacy is None],
        "request estimates": [
            m.id for m in active if not m.is_zero_cost and not m.request_estimates
        ],
    }
    for kind, ids in gaps.items():
        assert len(ids) <= MAX_UNMATCHED, f"too many models without {kind}: {ids}"
        for model_id in ids:
            assert model_id in warnings, f"{model_id} lacks {kind} but is not in the warnings"


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
