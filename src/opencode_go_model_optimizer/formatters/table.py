"""Shared ASCII table rendering and cell formatting helpers."""

import math
from collections.abc import Iterable
from datetime import UTC, datetime
from typing import Any

from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.pricing import next_price_change
from opencode_go_model_optimizer.ingest.models import NormalizedModel


def _burn_basis(
    criteria: SelectionCriteria | None,
) -> tuple[str, tuple[int, int, int] | None, str]:
    c = criteria or SelectionCriteria()
    return c.profile, c.token_mix, c.burn_basis


def _count(value: float) -> float | int | str:
    return "unlimited" if math.isinf(value) else int(value) if value.is_integer() else value


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


def _fmt_allowance(m: NormalizedModel) -> str:
    if m.monthly_allowance is None:
        return "-"
    return "Unlimited" if math.isinf(m.monthly_allowance) else f"${m.monthly_allowance:.2f}"


def _fmt_output_cost(m: NormalizedModel) -> str:
    return "Free" if m.is_zero_cost else f"${m.cost.output:.2f}/1M"


def _fmt_context(m: NormalizedModel) -> str:
    return f"{m.limit.context:,}" if m.limit.context else "-"


def format_multiplier(
    multiplier: float | None,
    next_change: dict[str, Any] | None = None,
) -> str:
    """Format a price multiplier and optional upcoming change."""
    if multiplier is None:
        return "-"
    cell = f"x{multiplier:.3g}"
    if (
        isinstance(next_change, dict)
        and next_change.get("at")
        and next_change.get("multiplier") is not None
    ):
        cell += f" (-> x{next_change['multiplier']:.3g} @ {next_change['at']})"
    return cell


def _multiplier_cell(m: NormalizedModel, change: dict[str, Any] | None = None) -> str:
    return format_multiplier(m.price_multiplier, change)


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


_render_table = render_table
