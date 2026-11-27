"""Shared Markdown, Mermaid, and settings rendering helpers."""

from __future__ import annotations

import json
import math
import re
from collections.abc import Callable, Iterable
from typing import Any, TypeGuard


def is_number(val: Any) -> TypeGuard[int | float]:
    """Return True when ``val`` is an int or float (excluding bool)."""
    return isinstance(val, (int, float)) and not isinstance(val, bool)


def fence_for(text: str) -> str:
    """Return a backtick fence longer than any backtick run in ``text``."""
    return "`" * max(3, max(map(len, re.findall(r"`+", text)), default=0) + 1)


def sanitize_mermaid_id(raw: str, prefix: str = "") -> str:
    """Return a Mermaid-safe node identifier (non-alphanumerics become ``_``)."""
    return prefix + re.sub(r"[^a-zA-Z0-9_]", "_", str(raw))


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
    if is_number(val):
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
    if is_number(val):
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


def render_mermaid_flowchart(
    nodes: Iterable[tuple[str, str]],
    edges: Iterable[tuple[str, str] | tuple[str, str, str]] = (),
) -> list[str]:
    """Render a Mermaid ``flowchart LR`` from ``(id, label)`` nodes and edges.

    An edge is ``(src, dst)`` or ``(src, dst, dst_label)``; a label declares the
    target node inline (``src --> dst["label"]``). Returns ``[]`` when empty.
    """

    def label(text: str) -> str:
        safe = str(text).replace('"', "'")
        return f'["{safe}"]'

    body = [f"    {sanitize_mermaid_id(i)}{label(t)}" for i, t in nodes]
    for src, dst, *dst_label in edges:
        target = sanitize_mermaid_id(dst) + (label(dst_label[0]) if dst_label else "")
        body.append(f"    {sanitize_mermaid_id(src)} --> {target}")
    return ["```mermaid", "flowchart LR", *body, "```"] if body else []


def render_details_block(
    summary_html: str,
    body: str,
    *,
    open_by_default: bool = False,
) -> list[str]:
    """Render a collapsible ``<details>`` wrapping a fenced text block.

    Returns ``[]`` if ``body`` is blank.
    """
    text = body.strip()
    if not text:
        return []
    fence = fence_for(text)
    tag = "<details open>" if open_by_default else "<details>"
    return [
        tag,
        f"<summary>{summary_html}</summary>",
        "",
        f"{fence}text",
        text,
        fence,
        "",
        "</details>",
    ]


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
