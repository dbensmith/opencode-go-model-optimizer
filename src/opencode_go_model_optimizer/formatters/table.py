"""Shared ASCII table rendering and cell formatting helpers."""

import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.pricing import next_price_change
from opencode_go_model_optimizer.formatters.markdown import (
    format_allowance_value,
    format_cost_value,
    format_int,
)
from opencode_go_model_optimizer.ingest.models import NormalizedModel


def _model_value_cells(m: NormalizedModel) -> list[str]:
    """Format the shared allowance, output-cost, and context table cells."""
    cost = 0.0 if m.is_zero_cost else m.cost.output
    return [
        format_allowance_value(m.monthly_allowance),
        format_cost_value(cost, zero_is_free=m.is_zero_cost),
        format_int(m.limit.context) if m.limit.context else "-",
    ]


def _burn_basis(
    criteria: SelectionCriteria | None,
) -> tuple[str, tuple[int, int, int] | None, str]:
    c = criteria or SelectionCriteria()
    return c.profile, c.token_mix, c.burn_basis


def _requests_cell(m: NormalizedModel) -> str:
    est = m.request_estimate
    if est is None:
        return "-"
    return "Unlimited" if math.isinf(est.per_month) else f"{est.per_month:,.0f}"


def _next_change(m: NormalizedModel, criteria: SelectionCriteria | None) -> dict[str, Any] | None:
    """Next price change for a model with multipliers, relative to the criteria's time."""
    if m.price_multiplier is None:
        return None
    at = (criteria.at if criteria else None) or datetime.now(UTC)
    change = next_price_change(m, at)
    if change is None:
        return None
    when, factor = change
    return {"at": when.isoformat(timespec="minutes"), "multiplier": round(factor, 4)}


def _model_map(models: list[NormalizedModel]) -> dict[str, NormalizedModel]:
    mapping = {m.full_id: m for m in models}
    mapping.update({m.id: m for m in models})
    return mapping


def _privacy_cell(m: NormalizedModel) -> str:
    """Compact privacy summary: ZDR / <N>d retention, plus train, global and limited flags."""
    p = m.privacy
    if p is None:
        return "-"
    parts = ["ZDR" if p.zdr else f"{p.retention_days}d" if p.retention_days is not None else "?"]
    if p.trains_on_data:
        parts.append("train")
    if p.requires_global_region:
        parts.append("global")
    if p.limited_regions:
        parts.append("limited")
    return ",".join(parts)


def _append_column(
    headers: list[str],
    rows: list[list[str]],
    header: str,
    values: Iterable[str],
) -> None:
    """Append an optional column header and one cell value per row."""
    headers.append(header)
    for row, value in zip(rows, values, strict=True):
        row.append(value)


def format_method_banner(mode: str, *, fell_back: bool = False) -> str:
    """Return a prominent header announcing the calculation method used."""
    if fell_back:
        return (
            "Calculation method: ALLOWANCE "
            "[FALLBACK from PARETO — Artificial Analysis benchmarks unavailable]"
        )
    if mode == "pareto":
        return "Calculation method: PARETO (Artificial Analysis benchmarks + quota burn)"
    return "Calculation method: ALLOWANCE (plan allowance & pricing heuristic; no benchmarks)"


def render_table(headers: list[str], rows: list[list[str]]) -> str:
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
