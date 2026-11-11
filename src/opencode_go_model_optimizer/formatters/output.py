"""Formatters for CLI output in JSON, table, and compact formats."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from opencode_go_model_optimizer.engine.tiers import STANDARD_TIERS
from opencode_go_model_optimizer.ingest.models import NormalizedModel


def format_select_json(
    tiers: dict[str, str],
    mode: str,
    plan: str = "go",
    evaluated_at: str | None = None,
) -> str:
    """Format tier selection as standard JSON."""
    ts = evaluated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    payload = {
        "plan": plan,
        "tiers": tiers,
        "evaluated_at": ts,
        "mode": mode,
    }
    return json.dumps(payload, indent=2)


def format_select_compact(tiers: dict[str, str]) -> str:
    """Format tier selection as compact space-delimited lines."""
    lines = [f"{tier} {model_id}" for tier, model_id in tiers.items() if model_id]
    return "\n".join(lines)


def format_select_env(tiers: dict[str, str]) -> str:
    """Format tier selection as shell environment variable assignments."""
    lines = [f'OPENCODE_MODEL_{tier.upper()}="{tiers.get(tier, "")}"' for tier in STANDARD_TIERS]
    return "\n".join(lines)


def format_select_github_output(tiers: dict[str, str]) -> str:
    """Format tier selection as GitHub Actions key=value output lines."""
    lines = [f"{tier}={tiers.get(tier, '')}" for tier in STANDARD_TIERS]
    return "\n".join(lines)


def format_select_table(
    tiers: dict[str, str],
    models: list[NormalizedModel],
    plan: str = "go",
) -> str:
    """Format tier selection as a human-readable ASCII table."""
    model_map = {m.full_id: m for m in models}
    model_map.update({m.id: m for m in models})

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
            rows.append([tier, full_id, allow_str, cost_str, ctx_str])
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
