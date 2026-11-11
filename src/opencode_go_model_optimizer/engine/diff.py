"""Snapshot drift diffing for catalog and tier assignment changes."""

from __future__ import annotations

import math
from typing import Any, Literal

from pydantic import BaseModel, Field

from opencode_go_model_optimizer.engine.tiers import STANDARD_TIERS, allocate_tiers, resolve_mode
from opencode_go_model_optimizer.ingest.models import NormalizedModel, Snapshot
from opencode_go_model_optimizer.ingest.snapshot import apply_plan

ChangeType = Literal["added", "removed", "deprecated", "modified"]

PRICING_FIELDS = (
    "monthly_allowance",
    "input_cost",
    "output_cost",
    "cache_read_cost",
    "cache_write_cost",
)

CAPABILITY_FIELDS = ("context", "max_output", "aa_iq", "aa_coding", "speed_tps")


class ModelChange(BaseModel):
    """A single catalog change for one model."""

    id: str
    change_type: ChangeType
    changes: dict[str, tuple[Any, Any]] = Field(default_factory=dict)


class SnapshotDiff(BaseModel):
    """Difference between two catalog snapshots and their tier assignments."""

    old_fetched_at: str = ""
    new_fetched_at: str = ""
    plan: str = "go"
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    status_changes: dict[str, dict[str, str]] = Field(default_factory=dict)
    pricing_changes: dict[str, dict[str, tuple[Any, Any]]] = Field(default_factory=dict)
    capability_changes: dict[str, dict[str, tuple[Any, Any]]] = Field(default_factory=dict)
    tier_changes: dict[str, dict[str, str]] = Field(default_factory=dict)
    changes: list[ModelChange] = Field(default_factory=list)

    def has_changes(self) -> bool:
        """Return True when any catalog or tier change was detected."""
        return bool(
            self.added
            or self.removed
            or self.status_changes
            or self.pricing_changes
            or self.capability_changes
            or self.tier_changes
        )


def _normalize_value(value: Any) -> Any:
    """Convert non-JSON-safe values (infinite allowances) into stable representations."""
    if isinstance(value, float) and math.isinf(value):
        return "unlimited"
    return value


def _pricing(m: NormalizedModel) -> dict[str, Any]:
    """Extract comparable pricing/allowance fields from a model."""
    return {
        "monthly_allowance": _normalize_value(m.monthly_allowance),
        "input_cost": m.cost.input,
        "output_cost": m.cost.output,
        "cache_read_cost": m.cost.cache_read,
        "cache_write_cost": m.cost.cache_write,
    }


def _capabilities(m: NormalizedModel) -> dict[str, Any]:
    """Extract comparable context/output limits and benchmark fields from a model."""
    return {
        "context": m.limit.context,
        "max_output": m.limit.output,
        "aa_iq": m.aa_iq,
        "aa_coding": m.aa_coding,
        "speed_tps": m.speed_tps,
    }


def _prepare_models(snapshot: Snapshot, plan: str) -> list[NormalizedModel]:
    """Return deep-copied models with the requested plan allowances applied."""
    models = [m.model_copy(deep=True) for m in snapshot.models]
    return apply_plan(models, plan)


def diff_snapshots(
    old_snap: Snapshot,
    new_snap: Snapshot,
    mode: str = "auto",
    plan: str = "go",
    profile: str = "general",
) -> SnapshotDiff:
    """Compute catalog, pricing, status, and tier-assignment drift between snapshots."""
    old_models = _prepare_models(old_snap, plan)
    new_models = _prepare_models(new_snap, plan)
    old_by_id = {m.id: m for m in old_models}
    new_by_id = {m.id: m for m in new_models}

    added = sorted(new_by_id.keys() - old_by_id.keys())
    removed = sorted(old_by_id.keys() - new_by_id.keys())
    status_changes: dict[str, dict[str, str]] = {}
    pricing_changes: dict[str, dict[str, tuple[Any, Any]]] = {}
    capability_changes: dict[str, dict[str, tuple[Any, Any]]] = {}
    changes_by_id: dict[str, ModelChange] = {}

    def note(model_id: str, change_type: ChangeType, changes: dict[str, tuple[Any, Any]]) -> None:
        existing = changes_by_id.get(model_id)
        if existing:
            existing.changes.update(changes)
            if change_type == "deprecated":
                existing.change_type = "deprecated"
        else:
            changes_by_id[model_id] = ModelChange(
                id=model_id, change_type=change_type, changes=changes
            )

    for model_id in added:
        note(model_id, "added", {})
    for model_id in removed:
        note(model_id, "removed", {})

    for model_id in sorted(old_by_id.keys() & new_by_id.keys()):
        old_model = old_by_id[model_id]
        new_model = new_by_id[model_id]

        old_pricing = _pricing(old_model)
        new_pricing = _pricing(new_model)
        field_changes = {
            field: (old_pricing[field], new_pricing[field])
            for field in PRICING_FIELDS
            if old_pricing[field] != new_pricing[field]
        }
        if field_changes:
            pricing_changes[model_id] = field_changes
            note(model_id, "modified", field_changes)

        old_caps = _capabilities(old_model)
        new_caps = _capabilities(new_model)
        cap_changes = {
            field: (old_caps[field], new_caps[field])
            for field in CAPABILITY_FIELDS
            if old_caps[field] != new_caps[field]
        }
        if cap_changes:
            capability_changes[model_id] = cap_changes
            note(model_id, "modified", cap_changes)

        if old_model.status != new_model.status:
            status_changes[model_id] = {"old": old_model.status, "new": new_model.status}
            change_type: ChangeType = (
                "deprecated" if new_model.status == "deprecated" else "modified"
            )
            note(model_id, change_type, {"status": (old_model.status, new_model.status)})

    resolved_mode = resolve_mode(mode, old_models + new_models)
    old_tiers = allocate_tiers(old_models, mode=resolved_mode, profile=profile)
    new_tiers = allocate_tiers(new_models, mode=resolved_mode, profile=profile)
    tier_changes = {
        tier: {"old": old_tiers.get(tier, ""), "new": new_tiers.get(tier, "")}
        for tier in STANDARD_TIERS
        if old_tiers.get(tier, "") != new_tiers.get(tier, "")
    }

    return SnapshotDiff(
        old_fetched_at=old_snap.fetched_at,
        new_fetched_at=new_snap.fetched_at,
        plan=plan,
        added=added,
        removed=removed,
        status_changes=status_changes,
        pricing_changes=pricing_changes,
        capability_changes=capability_changes,
        tier_changes=tier_changes,
        changes=[changes_by_id[model_id] for model_id in sorted(changes_by_id)],
    )
