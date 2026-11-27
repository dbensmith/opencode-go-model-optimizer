"""Shared Markdown, Mermaid, and settings rendering helpers."""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Iterable
from typing import Any


def format_allowance_value(
    val: Any,
    *,
    compact: bool = False,
    unlimited_label: str = "Unlimited",
) -> str:
    """Format a monthly allowance value for Markdown tables and diagrams."""
    if val is None:
        return "-"
    if val == "unlimited":
        return unlimited_label
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if math.isinf(val) or math.isnan(val):
            return str(val)
        return f"${val:g}" if compact else f"${val:.2f}"
    return str(val)


def format_cost_value(
    val: Any,
    *,
    compact: bool = False,
    suffix: str = "/1M",
    free_label: str = "Free",
) -> str:
    """Format an output token cost value for Markdown tables."""
    if val is None:
        return "-"
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        if compact:
            return f"${val:g}"
        return free_label if val == 0 else f"${val:.2f}{suffix}"
    return str(val)


def render_markdown_table(
    headers: Iterable[str],
    rows: Iterable[Iterable[Any]],
    *,
    cell_formatter: Callable[[str, str], str] | None = None,
) -> list[str]:
    """Render GitHub-flavored Markdown table lines from headers and rows."""
    norm_headers = [str(h).strip() for h in headers]
    num_cols = len(norm_headers)
    lines = [
        "| " + " | ".join(norm_headers) + " |",
        "| " + " | ".join("---" for _ in norm_headers) + " |",
    ]
    for row in rows:
        raw_cells = [str(c).strip() if c is not None else "-" for c in row]
        cells = raw_cells[:num_cols] + ["-"] * max(0, num_cols - len(raw_cells))
        if cell_formatter is not None:
            cells = [cell_formatter(h, c) for h, c in zip(norm_headers, cells, strict=True)]
        lines.append("| " + " | ".join(cells) + " |")
    return lines


def render_mermaid_pie(
    title: str,
    slices: Iterable[tuple[str, int | float]],
    *,
    skip_zero: bool = True,
) -> list[str]:
    """Render a Mermaid ``pie showData`` block, or ``[]`` when all slices are zero."""
    entries = [(label, count) for label, count in slices if not skip_zero or count > 0]
    if not entries:
        return []
    safe_title = title.replace('"', "'")
    lines = [
        "```mermaid",
        "pie showData",
        f'    title "{safe_title}"',
    ]
    for label, count in entries:
        safe_label = label.replace('"', "'")
        lines.append(f'    "{safe_label}" : {count}')
    lines.append("```")
    return lines


def render_mermaid_xychart(
    title: str,
    x_labels: list[str],
    *,
    y_axis: str | None = None,
    bars: Iterable[list[float | int]] = (),
    lines_series: Iterable[list[float | int]] = (),
) -> list[str]:
    """Render a Mermaid ``xychart-beta`` block."""
    safe_title = title.replace('"', "'")
    out = [
        "```mermaid",
        "xychart-beta",
        f'    title "{safe_title}"',
        f"    x-axis {json.dumps(x_labels)}",
    ]
    if y_axis is not None:
        out.append(f"    y-axis {y_axis}")
    for bar in bars:
        out.append(f"    bar {json.dumps(bar)}")
    for series in lines_series:
        out.append(f"    line {json.dumps(series)}")
    out.append("```")
    return out


def render_run_settings_lines(
    settings: dict[str, Any],
    *,
    heading: str = "### Run Settings",
    default_mode: str = "allowance",
    default_top_n: int = 1,
) -> list[str]:
    """Render the Run Settings Markdown section from an effective settings/criteria dict."""
    if not settings:
        return []
    lines = [
        heading,
        "",
        f"- **Mode**: `{settings.get('mode', default_mode)}` "
        f"| **Profile**: `{settings.get('profile', 'general')}` "
        f"| **Price basis**: `{settings.get('price_basis', '-')}` "
        f"| **Burn basis**: `{settings.get('burn_basis', '-')}` "
        f"| **Top N**: `{settings.get('top_n', default_top_n)}`",
        f"- **Global regions**: {'allowed' if settings.get('allow_global_region') else 'blocked'} "
        f"| **Paid training**: {'allowed' if settings.get('allow_paid_training') else 'blocked'} "
        f"| **Free training**: {'allowed' if settings.get('allow_free_training') else 'blocked'}",
        f"- **Filters**: max retention `{settings.get('max_retention_days') or 'none'}` "
        f"| ZDR required `{settings.get('require_zdr', False)}` "
        f"| min context `{settings.get('min_context', 0)}` "
        f"| min output `{settings.get('min_output', 0)}` "
        f"| max output cost `{settings.get('max_output_cost') or 'none'}` "
        f"| max burn `{settings.get('max_burn') or 'none'}`",
    ]
    skip = settings.get("skip") or []
    prefer = settings.get("prefer") or {}
    if skip or prefer:
        extras: list[str] = []
        if skip:
            extras.append(f"skip `{', '.join(skip)}`")
        if prefer:
            extras.append("prefer " + ", ".join(f"`{k}={v}`" for k, v in prefer.items()))
        lines.append("- **Overrides**: " + " | ".join(extras))
    return lines
