"""Formatters for CLI output in JSON, table, and compact formats."""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from opencode_go_model_optimizer.engine.diff import SnapshotDiff
from opencode_go_model_optimizer.engine.pareto import compute_quota_burn_pct, compute_turn_cost
from opencode_go_model_optimizer.engine.tiers import STANDARD_TIERS
from opencode_go_model_optimizer.ingest.models import NormalizedModel


def format_select_json(
    tiers: dict[str, str],
    mode: str,
    plan: str = "go",
    evaluated_at: str | None = None,
    explanations: dict[str, dict[str, Any]] | None = None,
    profile: str | None = None,
    fallbacks: dict[str, list[str]] | None = None,
    snapshot_info: dict[str, Any] | None = None,
) -> str:
    """Format tier selection as standard JSON."""
    ts = evaluated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload: dict[str, Any] = {
        "plan": plan,
        "tiers": tiers,
        "evaluated_at": ts,
        "mode": mode,
    }
    if snapshot_info is not None:
        payload["snapshot"] = snapshot_info
    if profile is not None and profile != "general":
        payload["profile"] = profile
    if fallbacks is not None:
        payload["fallbacks"] = fallbacks
    if explanations is not None:
        payload["explanations"] = explanations
    return json.dumps(payload, indent=2)


def explain_tiers(
    tiers: dict[str, str],
    models: list[NormalizedModel],
    mode: str = "allowance",
    profile: str = "general",
    token_mix: tuple[int, int, int] | None = None,
    weights: tuple[float, float] | None = None,
) -> dict[str, dict[str, Any]]:
    """Build detailed per-tier metrics and selection rationale."""
    model_map = {m.full_id: m for m in models}
    model_map.update({m.id: m for m in models})

    explanations: dict[str, dict[str, Any]] = {}
    for tier, full_id in tiers.items():
        m = model_map.get(full_id)
        if m:
            allow_val: float | str | None = (
                "unlimited"
                if (m.monthly_allowance is not None and math.isinf(m.monthly_allowance))
                else m.monthly_allowance
            )
            quota_burn = (
                m.composite_burn_pct
                if m.composite_burn_pct is not None
                else compute_quota_burn_pct(m, profile=profile, token_mix=token_mix)
            )
            explanations[tier] = {
                "model_id": full_id,
                "monthly_allowance": allow_val,
                "output_cost_per_1m": m.cost.output,
                "turn_cost_usd": round(
                    compute_turn_cost(m, profile=profile, token_mix=token_mix), 6
                ),
                "quota_burn_pct": round(quota_burn, 2) if quota_burn is not None else 0.0,
                "aa_iq": m.aa_iq,
                "aa_coding": m.aa_coding,
                "speed_tps": m.speed_tps,
                "bpe": round(m.bpe, 4) if m.bpe is not None else None,
                "is_pareto": m.is_pareto,
                "context": m.limit.context,
            }
        else:
            explanations[tier] = {
                "model_id": full_id or "None",
            }
    return explanations


def format_select_compact(tiers: dict[str, str]) -> str:
    """Format tier selection as compact space-delimited lines."""
    lines = [f"{tier} {model_id}" for tier, model_id in tiers.items() if model_id]
    return "\n".join(lines)


def format_select_env(
    tiers: dict[str, str],
    fallbacks: dict[str, list[str]] | None = None,
) -> str:
    """Format tier selection as shell environment variable assignments."""
    lines = [f'OPENCODE_MODEL_{tier.upper()}="{tiers.get(tier, "")}"' for tier in STANDARD_TIERS]
    if fallbacks is not None:
        lines.extend(
            [
                f'OPENCODE_MODEL_{tier.upper()}_FALLBACKS="{",".join(fallbacks.get(tier, []))}"'
                for tier in STANDARD_TIERS
            ]
        )
    return "\n".join(lines)


def format_select_github_output(
    tiers: dict[str, str],
    fallbacks: dict[str, list[str]] | None = None,
) -> str:
    """Format tier selection as GitHub Actions key=value output lines."""
    lines = [f"{tier}={tiers.get(tier, '')}" for tier in STANDARD_TIERS]
    if fallbacks is not None:
        lines.extend(
            [f"{tier}_fallbacks={','.join(fallbacks.get(tier, []))}" for tier in STANDARD_TIERS]
        )
    return "\n".join(lines)


def format_select_table(
    tiers: dict[str, str],
    models: list[NormalizedModel],
    plan: str = "go",
    explain: bool = False,
    profile: str = "general",
    token_mix: tuple[int, int, int] | None = None,
    weights: tuple[float, float] | None = None,
) -> str:
    """Format tier selection as a human-readable ASCII table."""
    model_map = {m.full_id: m for m in models}
    model_map.update({m.id: m for m in models})

    if explain:
        headers = [
            "Tier",
            "Model ID",
            f"Allowance ({plan})",
            "Output Cost",
            "Context",
            "IQ",
            "BPE",
            "Burn %",
            "Turn Cost",
        ]
    else:
        headers = ["Tier", "Model ID", f"Allowance ({plan})", "Output Cost", "Context"]
    rows: list[list[str]] = []

    for tier, full_id in tiers.items():
        m = model_map.get(full_id)
        if m:
            allow_str = (
                "Unlimited"
                if m.monthly_allowance == float("inf")
                else f"${m.monthly_allowance:.2f}"
                if m.monthly_allowance is not None
                else "-"
            )
            cost_str = "Free" if m.is_zero_cost else f"${m.cost.output:.2f}/1M"
            ctx_str = f"{m.limit.context:,}" if m.limit.context else "-"
            if explain:
                iq_str = f"{m.aa_iq:.1f}" if m.aa_iq is not None else "-"
                bpe_str = f"{m.bpe:.3f}" if m.bpe is not None else "-"
                burn_val = (
                    m.composite_burn_pct
                    if m.composite_burn_pct is not None
                    else compute_quota_burn_pct(m, profile=profile, token_mix=token_mix)
                )
                burn_str = f"{burn_val:.1f}%" if burn_val is not None else "-"
                turn_cost = compute_turn_cost(m, profile=profile, token_mix=token_mix)
                turn_cost_str = f"${turn_cost:.4f}"
                rows.append(
                    [
                        tier,
                        full_id,
                        allow_str,
                        cost_str,
                        ctx_str,
                        iq_str,
                        bpe_str,
                        burn_str,
                        turn_cost_str,
                    ]
                )
            else:
                rows.append([tier, full_id, allow_str, cost_str, ctx_str])
        else:
            if explain:
                rows.append([tier, full_id or "None", "-", "-", "-", "-", "-", "-", "-"])
            else:
                rows.append([tier, full_id or "None", "-", "-", "-"])

    return _render_table(headers, rows)


def format_rank_table(
    ranked_models: list[NormalizedModel],
    mode: str = "allowance",
) -> str:
    """Format full catalog ranking as an ASCII table."""
    if mode == "pareto":
        headers = [
            "Rank",
            "Model ID",
            "Tier",
            "BPE",
            "IQ",
            "Burn %",
            "Allowance",
            "Output Cost",
            "Release",
        ]
        rows: list[list[str]] = []
        for m in ranked_models:
            bpe_str = f"{m.bpe:.3f}" if m.bpe is not None else "-"
            iq_str = f"{m.aa_iq:.1f}" if m.aa_iq is not None else "-"
            burn_str = f"{m.composite_burn_pct:.1f}%" if m.composite_burn_pct is not None else "-"
            allow_str = (
                "Unlimited"
                if m.monthly_allowance == float("inf")
                else f"${m.monthly_allowance:.2f}"
                if m.monthly_allowance is not None
                else "-"
            )
            cost_str = "Free" if m.is_zero_cost else f"${m.cost.output:.2f}/1M"
            rows.append(
                [
                    str(m.rank or "-"),
                    m.id,
                    m.tier or "-",
                    bpe_str,
                    iq_str,
                    burn_str,
                    allow_str,
                    cost_str,
                    m.release_date or "-",
                ]
            )
    else:
        headers = [
            "Rank",
            "Model ID",
            "Tier",
            "Allowance",
            "Output Cost",
            "Release Date",
            "Context",
            "Status",
        ]
        rows = []
        for m in ranked_models:
            allow_str = (
                "Unlimited"
                if m.monthly_allowance == float("inf")
                else f"${m.monthly_allowance:.2f}"
                if m.monthly_allowance is not None
                else "-"
            )
            cost_str = "Free" if m.is_zero_cost else f"${m.cost.output:.2f}/1M"
            ctx_str = f"{m.limit.context:,}" if m.limit.context else "-"
            rows.append(
                [
                    str(m.rank or "-"),
                    m.id,
                    m.tier or "-",
                    allow_str,
                    cost_str,
                    m.release_date or "-",
                    ctx_str,
                    m.status,
                ]
            )

    return _render_table(headers, rows)


def format_rank_json(ranked_models: list[NormalizedModel]) -> str:
    """Format full catalog ranking as JSON array."""
    data = [m.model_dump(mode="json", exclude_none=True) for m in ranked_models]
    return json.dumps(data, indent=2)


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

    if diff.added:
        sections.append("Added models:")
        sections.append(_render_table(["Model ID"], [[model_id] for model_id in diff.added]))
    if diff.removed:
        sections.append("Removed models:")
        sections.append(_render_table(["Model ID"], [[model_id] for model_id in diff.removed]))
    if diff.status_changes:
        sections.append("Status changes:")
        sections.append(
            _render_table(
                ["Model ID", "Old", "New"],
                [
                    [model_id, change["old"], change["new"]]
                    for model_id, change in diff.status_changes.items()
                ],
            )
        )
    if diff.pricing_changes:
        sections.append("Pricing / allowance changes:")
        sections.append(
            _render_table(
                ["Model ID", "Field", "Old", "New"],
                [
                    [model_id, field, _format_diff_value(old), _format_diff_value(new)]
                    for model_id, fields in diff.pricing_changes.items()
                    for field, (old, new) in fields.items()
                ],
            )
        )
    if diff.capability_changes:
        sections.append("Capability / benchmark changes:")
        sections.append(
            _render_table(
                ["Model ID", "Field", "Old", "New"],
                [
                    [model_id, field, _format_diff_value(old), _format_diff_value(new)]
                    for model_id, fields in diff.capability_changes.items()
                    for field, (old, new) in fields.items()
                ],
            )
        )
    if diff.tier_changes:
        sections.append("Tier changes:")
        sections.append(
            _render_table(
                ["Tier", "Old", "New"],
                [
                    [tier, change["old"] or "None", change["new"] or "None"]
                    for tier, change in diff.tier_changes.items()
                ],
            )
        )

    return "\n\n".join(sections)


def _render_table(headers: list[str], rows: list[list[str]]) -> str:
    """Render plain ASCII table with borders."""
    num_cols = len(headers)
    norm_rows: list[list[str]] = []
    for row in rows:
        if len(row) < num_cols:
            norm_rows.append(list(row) + ["-"] * (num_cols - len(row)))
        else:
            norm_rows.append(list(row[:num_cols]))

    col_widths = [len(h) for h in headers]
    for row in norm_rows:
        for idx, cell in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(str(cell)))

    sep = "+-" + "-+-".join("-" * w for w in col_widths) + "-+"
    header_line = "| " + " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers)) + " |"

    out_lines = [sep, header_line, sep]
    for row in norm_rows:
        line = (
            "| " + " | ".join(str(cell).ljust(col_widths[i]) for i, cell in enumerate(row)) + " |"
        )
        out_lines.append(line)
    out_lines.append(sep)

    return "\n".join(out_lines)
