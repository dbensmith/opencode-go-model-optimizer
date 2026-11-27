"""Shared formatting, URL, catalog, and JSON loading helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opencode_go_model_optimizer.engine.tiers import STANDARD_TIERS
from opencode_go_model_optimizer.formatters.markdown import (
    format_allowance_value,
    format_burn,
    format_cost_value,
    format_float,
    format_int,
    format_privacy,
    is_number,
    render_markdown_table,
    render_mermaid_pie,
    render_run_settings_lines,
)

_RANKED_CATALOG_COLUMN_NAMES = (
    "Rank",
    "Model ID",
    "Tier",
    "BPE",
    "AA IQ",
    "Engineering",
    "Burn %",
    "Allowance",
    "Output Cost",
    "Context",
    "Release",
    "Privacy",
)


def _normalize_site_url(site_url: str | None) -> str | None:
    """Return a GitHub Pages base URL with exactly one trailing slash, or None."""
    if not site_url or not site_url.strip():
        return None
    return site_url.strip().rstrip("/") + "/"


def _site_file(site_url: str, path: str) -> str:
    """Absolute link to a published file under the GitHub Pages base URL."""
    return f"{site_url}{path.lstrip('/')}"


def _site_page(site_url: str, page: str) -> str:
    """Docsify hash route for a rendered Markdown page on GitHub Pages."""
    return f"{site_url}#/{page.lstrip('/')}"


def _model_burn_pct(m: dict[str, Any]) -> float | None:
    """Extract composite burn percentage from a serialized rank row."""
    burn = m.get("composite_burn_pct")
    return float(burn) if is_number(burn) else None


def _short_id(m: dict[str, Any]) -> str:
    """Return a rank row's model id without the provider prefix."""
    return str(m["full_id"]).removeprefix("opencode-go/")


def _tier_pick_id(tier: str, explanations: dict[str, Any], tiers: dict[str, Any]) -> str:
    """Return a tier's explanation id, selected id, or the empty-tier marker."""
    return str(explanations.get(tier, {}).get("model_id") or tiers.get(tier) or "None")


def _model_quality(m: dict[str, Any]) -> float:
    """Read effective quality from a serialized model entry."""
    return float(m.get("effective_quality") or 0.0)


def _ordered_tiers(explanations: dict[str, Any], tiers: dict[str, Any]) -> list[str]:
    """Order tiers according to STANDARD_TIERS, preserving any extra explanation keys."""
    ordered = [t for t in STANDARD_TIERS if t in explanations or t in tiers]
    for tier in explanations:
        if tier not in ordered:
            ordered.append(tier)
    return ordered


def _format_settings_lines(payload: dict[str, Any], default_mode: str) -> list[str]:
    """Render Run Settings lines when effective settings are present in payload."""
    settings = payload.get("settings") or {}
    if not settings:
        return []
    return [
        "",
        *render_run_settings_lines(
            settings,
            heading="### Run Settings",
            default_mode=default_mode,
            default_top_n=1,
        ),
    ]


def _render_report_header(
    heading: str,
    payload: dict[str, Any],
    *,
    default_mode: str,
    run_url: str | None,
    site_base: str | None,
    include_pages_home: bool,
) -> list[str]:
    """Render the common metadata header shared by issue and analysis pages."""
    plan = payload.get("plan", "go")
    mode = payload.get("mode", default_mode)
    evaluated_at = payload.get("evaluated_at", "-")
    snapshot = payload.get("snapshot") or {}
    snap_source = snapshot.get("source", "-")
    fetched_at = snapshot.get("fetched_at", "-")
    benchmark_source = snapshot.get("benchmark_source", "none")

    lines = [
        heading,
        "",
        f"- **Updated**: `{evaluated_at}`",
        f"- **Plan**: `{plan}` | **Mode**: `{mode}` | **Snapshot**: `{snap_source}` (`{fetched_at}`) | **Benchmarks**: `{benchmark_source}`",
    ]
    if run_url:
        lines.append(f"- **Workflow Run**: {run_url}")
    if site_base:
        if include_pages_home:
            lines.append(f"- **GitHub Pages**: {site_base}")
        lines.append(f"- **All data & other choices**: {_site_page(site_base, 'data')}")
    lines.extend(_format_settings_lines(payload, mode))
    return lines


def _ranked_catalog_cells(m: dict[str, Any]) -> list[str]:
    """Extract cell values for one full-catalog markdown table row."""
    model_id = m["full_id"]
    tier = m.get("tier", "-")
    bpe_s = format_float(m.get("bpe"), 3)
    iq_s = format_float(m.get("aa_iq"), 1)
    eng_s = format_float(m.get("aa_coding"), 1)
    burn_s = format_burn(_model_burn_pct(m))
    allowance_s = format_allowance_value(m.get("monthly_allowance"))

    if m.get("is_zero_cost"):
        output_s = "Free"
    else:
        cost_dict = m.get("cost") or {}
        output_cost = cost_dict.get("output")
        if output_cost is not None:
            output_s = format_cost_value(output_cost)
        else:
            output_s = format_cost_value(m.get("output_cost_per_1m"))

    limit_dict = m.get("limit") or {}
    ctx_val = limit_dict.get("context") if limit_dict else None
    if ctx_val is None:
        ctx_val = m.get("context")
    ctx_s = format_int(ctx_val)

    rank_val = m.get("rank")
    rank_s = str(rank_val) if rank_val is not None else "-"

    release = m.get("release_date") or "-"
    priv = format_privacy(m.get("privacy"))
    return [
        rank_s,
        f"`{model_id}`",
        str(tier),
        bpe_s,
        iq_s,
        eng_s,
        burn_s,
        allowance_s,
        output_s,
        ctx_s,
        str(release),
        priv,
    ]


def _render_ranked_catalog_rows(rank_models: list[dict[str, Any]]) -> list[str]:
    """Render the full ranked catalog Markdown table and composition pie chart."""
    lines = render_markdown_table(
        _RANKED_CATALOG_COLUMN_NAMES,
        [_ranked_catalog_cells(m) for m in rank_models],
    )
    lines.extend(_catalog_composition_pie_lines(rank_models))
    return lines


def _catalog_composition_pie_lines(rank_models: list[dict[str, Any]]) -> list[str]:
    """Mermaid pie block summarizing the catalog by eligibility & frontier status."""
    if not rank_models:
        return []
    pareto_count = 0
    paid_non_frontier_count = 0
    free_count = 0
    unbenchmarked_count = 0

    for m in rank_models:
        is_pareto = bool(m.get("is_pareto"))
        is_zero = bool(m.get("is_zero_cost"))
        has_bm = m.get("aa_iq") is not None or m.get("aa_coding") is not None

        if is_pareto:
            pareto_count += 1
        elif is_zero:
            free_count += 1
        elif has_bm:
            paid_non_frontier_count += 1
        else:
            unbenchmarked_count += 1

    pie_lines = render_mermaid_pie(
        "Catalog Composition by Eligibility & Frontier Status",
        [
            ("Pareto Frontier", pareto_count),
            ("Paid (Non-Frontier)", paid_non_frontier_count),
            ("Free / Zero-Cost", free_count),
            ("Unbenchmarked Paid", unbenchmarked_count),
        ],
    )
    return ["", *pie_lines] if pie_lines else []


def _load_rank_models(path: str | None) -> list[dict[str, Any]]:
    """Load model entries from a ``rank --format json`` output file."""
    if not path:
        return []
    rank_data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(rank_data, list):
        return rank_data
    if isinstance(rank_data, dict):
        return rank_data.get("models") or []
    return []
