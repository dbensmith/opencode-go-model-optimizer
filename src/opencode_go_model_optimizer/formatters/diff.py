"""Formatters for snapshot drift diff outputs."""

import json
import math
from typing import Any

from opencode_go_model_optimizer.engine.diff import SnapshotDiff
from opencode_go_model_optimizer.formatters.table import render_table


def format_diff_json(diff: SnapshotDiff) -> str:
    """Format a snapshot diff as structured JSON."""
    return diff.model_dump_json(indent=2)


def _format_diff_value(value: Any) -> str:
    """Render a diff value for human-readable table output."""
    if value is None:
        return "-"
    if isinstance(value, float):
        if math.isinf(value):
            return "unlimited"
        return f"{value:.4f}".rstrip("0").rstrip(".")
    if isinstance(value, list) and all(isinstance(v, dict) and "factor" in v for v in value):
        if not value:
            return "none"
        return "; ".join(f"x{v['factor']:g} when {json.dumps(v.get('when'))}" for v in value)
    return str(value)


def format_diff_table(diff: SnapshotDiff) -> str:
    """Format a snapshot diff as human-readable ASCII table sections."""
    header = (
        f"Snapshot diff (plan: {diff.plan})\n"
        f"  old: {diff.old_fetched_at or '-'}\n"
        f"  new: {diff.new_fetched_at or '-'}"
    )
    if not diff.has_changes():
        return f"{header}\n\nNo catalog or tier changes detected."

    sections: list[str] = [header]

    for title, model_ids in (("Added models:", diff.added), ("Removed models:", diff.removed)):
        if model_ids:
            sections.append(title)
            sections.append(render_table(["Model ID"], [[model_id] for model_id in model_ids]))

    if diff.status_changes:
        sections.append("Status changes:")
        sections.append(
            render_table(
                ["Model ID", "Old", "New"],
                [
                    [model_id, change["old"], change["new"]]
                    for model_id, change in diff.status_changes.items()
                ],
            )
        )
    for title, changes in (
        ("Pricing / allowance changes:", diff.pricing_changes),
        ("Capability / benchmark changes:", diff.capability_changes),
        ("Privacy / region changes:", diff.privacy_changes),
    ):
        if changes:
            sections.append(title)
            sections.append(
                render_table(
                    ["Model ID", "Field", "Old", "New"],
                    [
                        [model_id, field, _format_diff_value(old), _format_diff_value(new)]
                        for model_id, fields in changes.items()
                        for field, (old, new) in fields.items()
                    ],
                )
            )
    if diff.tier_changes:
        sections.append("Tier changes:")
        sections.append(
            render_table(
                ["Tier", "Old", "New"],
                [
                    [tier, change["old"] or "None", change["new"] or "None"]
                    for tier, change in diff.tier_changes.items()
                ],
            )
        )

    return "\n\n".join(sections)
