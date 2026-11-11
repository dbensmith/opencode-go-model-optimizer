"""Tests for snapshot drift diffing."""

from opencode_go_model_optimizer.engine.diff import diff_snapshots
from opencode_go_model_optimizer.ingest.models import (
    ModelLimit,
    NormalizedModel,
    Snapshot,
    SnapshotMetadata,
    TokenCost,
)


def _snapshot(fetched_at: str, models: list[NormalizedModel]) -> Snapshot:
    return Snapshot(
        schema_version=1,
        fetched_at=fetched_at,
        plan="go",
        models=models,
        metadata=SnapshotMetadata(),
    )


def _old_models() -> list[NormalizedModel]:
    return [
        NormalizedModel(
            id="changing-model",
            name="Changing Model",
            status="active",
            release_date="2026-01-01",
            monthly_allowance=15.0,
            cost=TokenCost(input=1.0, output=2.0),
            limit=ModelLimit(context=200000),
        ),
        NormalizedModel(
            id="removed-model",
            name="Removed Model",
            monthly_allowance=60.0,
            cost=TokenCost(input=0.5, output=1.0),
        ),
        NormalizedModel(
            id="unlimited-model",
            name="Unlimited Model",
            monthly_allowance=float("inf"),
            is_zero_cost=True,
        ),
    ]


def _new_models() -> list[NormalizedModel]:
    return [
        NormalizedModel(
            id="changing-model",
            name="Changing Model",
            status="deprecated",
            release_date="2026-01-01",
            monthly_allowance=30.0,
            cost=TokenCost(input=1.5, output=3.0),
            limit=ModelLimit(context=200000),
        ),
        NormalizedModel(
            id="added-model",
            name="Added Model",
            monthly_allowance=100.0,
            cost=TokenCost(input=0.2, output=0.4),
        ),
        NormalizedModel(
            id="unlimited-model",
            name="Unlimited Model",
            monthly_allowance=float("inf"),
            is_zero_cost=True,
        ),
    ]


def test_diff_snapshots_detects_all_change_kinds():
    diff = diff_snapshots(
        _snapshot("2026-01-01T00:00:00Z", _old_models()),
        _snapshot("2026-02-01T00:00:00Z", _new_models()),
        mode="allowance",
        plan="go",
    )

    assert diff.old_fetched_at == "2026-01-01T00:00:00Z"
    assert diff.new_fetched_at == "2026-02-01T00:00:00Z"
    assert diff.plan == "go"
    assert diff.added == ["added-model"]
    assert diff.removed == ["removed-model"]
    assert diff.status_changes == {"changing-model": {"old": "active", "new": "deprecated"}}
    assert diff.pricing_changes["changing-model"] == {
        "monthly_allowance": (15.0, 30.0),
        "input_cost": (1.0, 1.5),
        "output_cost": (2.0, 3.0),
    }
    assert diff.tier_changes["heavy"] == {
        "old": "opencode-go/changing-model",
        "new": "opencode-go/added-model",
    }
    assert diff.tier_changes["balanced"] == {
        "old": "opencode-go/removed-model",
        "new": "",
    }
    assert diff.has_changes() is True

    changes = {change.id: change for change in diff.changes}
    assert changes["added-model"].change_type == "added"
    assert changes["removed-model"].change_type == "removed"
    assert changes["changing-model"].change_type == "deprecated"
    assert changes["changing-model"].changes["status"] == ("active", "deprecated")


def test_diff_snapshots_no_changes_and_auto_mode():
    snap = _snapshot("2026-01-01T00:00:00Z", _old_models())
    diff = diff_snapshots(snap, snap, mode="auto", plan="go")

    assert diff.has_changes() is False
    assert diff.added == []
    assert diff.removed == []
    assert diff.status_changes == {}
    assert diff.pricing_changes == {}
    assert diff.tier_changes == {}
    assert diff.changes == []


def test_diff_snapshots_auto_mode_with_benchmarks():
    old_models = _old_models()
    new_models = _new_models()
    old_models[0].aa_iq = 70.0
    old_models[0].aa_coding = 65.0
    new_models[0].aa_iq = 75.0
    new_models[0].aa_coding = 70.0

    diff = diff_snapshots(
        _snapshot("2026-01-01T00:00:00Z", old_models),
        _snapshot("2026-02-01T00:00:00Z", new_models),
        mode="auto",
    )

    assert diff.has_changes() is True
    assert "changing-model" in diff.pricing_changes


def test_capability_changes_detected_and_rendered():
    from opencode_go_model_optimizer.formatters.output import format_diff_table

    old = NormalizedModel(
        id="cap", limit=ModelLimit(context=100, output=10), aa_iq=50.0, speed_tps=100.0
    )
    new = NormalizedModel(
        id="cap", limit=ModelLimit(context=200, output=10), aa_iq=55.0, aa_coding=40.0
    )
    diff = diff_snapshots(_snapshot("a", [old]), _snapshot("b", [new]))

    assert diff.capability_changes == {
        "cap": {
            "context": (100, 200),
            "aa_iq": (50.0, 55.0),
            "aa_coding": (None, 40.0),
            "speed_tps": (100.0, None),
        }
    }
    assert diff.has_changes()
    assert diff.changes[0].change_type == "modified"
    assert "context" in diff.changes[0].changes
    table = format_diff_table(diff)
    assert "Capability / benchmark changes:" in table
    assert "speed_tps" in table

    same = diff_snapshots(_snapshot("a", [old]), _snapshot("b", [old]))
    assert same.capability_changes == {}
