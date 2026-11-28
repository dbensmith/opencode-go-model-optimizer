"""Markdown renderer for the live Pareto analysis page (analysis.md)."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from opencode_go_model_optimizer.engine.tiers import (
    BALANCED_QUALITY_RATIO,
    HEAVY_QUALITY_RATIO,
    STANDARD_TIERS,
)
from opencode_go_model_optimizer.formatters.markdown import (
    format_allowance_value,
    format_burn,
    format_cost_value,
    format_float,
    is_number,
    render_details_block,
    render_markdown_table,
    render_mermaid_flowchart,
    render_mermaid_xychart,
    sanitize_mermaid_id,
)

from .charts import generate_analysis_svgs
from .common import (
    _model_burn_pct,
    _model_quality,
    _normalize_site_url,
    _render_ranked_catalog_rows,
    _render_report_header,
    _short_id,
    _tier_pick_id,
)


def _quadrant_points(
    rank_models: list[dict[str, Any]],
) -> list[tuple[str, float, float]]:
    def collect(include_zero_cost: bool, require_burn: bool) -> list[tuple[str, float, float]]:
        pts: list[tuple[str, float, float]] = []
        for m in rank_models:
            if not include_zero_cost and m.get("is_zero_cost"):
                continue
            if m.get("aa_iq") is None and m.get("aa_coding") is None:
                continue
            burn = _model_burn_pct(m)
            if require_burn and burn is None:
                continue
            quality = _model_quality(m)
            clean_id = _short_id(m)
            safe_label = re.sub(r"[^a-zA-Z0-9-]", "-", clean_id).strip("-") or f"m-{len(pts) + 1}"
            pts.append((safe_label, burn if burn is not None else 0.0, quality))
        return pts

    return collect(include_zero_cost=False, require_burn=True) or collect(
        include_zero_cost=True, require_burn=False
    )


def _render_quadrant_chart(rank_models: list[dict[str, Any]]) -> list[str]:
    benchmarked_models = _quadrant_points(rank_models)
    seen_labels: dict[str, int] = {}
    deduped_models: list[tuple[str, float, float]] = []
    for lbl, b, q in benchmarked_models[:12]:
        if lbl in seen_labels:
            seen_labels[lbl] += 1
            lbl = f"{lbl}-{seen_labels[lbl]}"
        else:
            seen_labels[lbl] = 1
        deduped_models.append((lbl, b, q))

    if not deduped_models:
        return []

    max_burn = max(b for _, b, _ in deduped_models) or 100.0
    max_quality = max(q for _, _, q in deduped_models) or 100.0
    if max_burn <= 0:
        max_burn = 100.0
    if max_quality <= 0:
        max_quality = 100.0

    lines = [
        "",
        "```mermaid",
        "quadrantChart",
        '    title "Capability vs Quota Efficiency"',
        '    x-axis "Higher Burn" --> "Higher Quota Efficiency"',
        '    y-axis "Lower Capability" --> "Higher Capability"',
        '    quadrant-1 "Sweet Spot (High Quality, Low Burn)"',
        '    quadrant-2 "Heavy Compute (High Quality, High Burn)"',
        '    quadrant-3 "Dominated / High Cost"',
        '    quadrant-4 "Lightweight / Low Burn"',
    ]
    for lbl, b, q in deduped_models:
        norm_x = 1.0 - (b / max_burn)
        norm_y = q / max_quality
        clamped_x = max(0.05, min(0.95, round(norm_x, 2)))
        clamped_y = max(0.05, min(0.95, round(norm_y, 2)))
        lines.append(f"    {lbl}: [{clamped_x:.2f}, {clamped_y:.2f}]")
    lines.append("```")
    return lines


def _render_free_matrix_section(free_matrix: list[dict[str, Any]]) -> list[str]:
    table_rows = [
        [
            row.get("label", "Default"),
            f"`{row.get('flags', '')}`",
            f"`{row.get('model_id', 'None')}`",
        ]
        for row in free_matrix
    ]
    mids = [(r.get("model_id") or "None").removeprefix("opencode-go/") for r in free_matrix]
    nodes = [(f"S{i}", r.get("label") or f"Setting {i + 1}") for i, r in enumerate(free_matrix)]
    edges = [(f"S{i}", sanitize_mermaid_id(mid, "M_"), mid) for i, mid in enumerate(mids)]
    return [
        "",
        "---",
        "",
        "### 2. Live Free-Tier Picks Across Privacy/Region Flags",
        "",
        *render_markdown_table(["Setting", "CLI Flags", "Selected Free Model"], table_rows),
        "",
        *render_mermaid_flowchart(nodes, edges),
    ]


def _render_multi_plan_section(
    plan_payloads: dict[str, dict[str, Any]],
    generated_svgs: list[str],
    *,
    heading: str = "### 3. Multi-Plan Tier Selections & Top-3 Fallbacks",
    include_chart_image: bool = True,
) -> list[str]:
    table_rows: list[list[str]] = []
    nodes: list[tuple[str, str]] = []
    edges: list[tuple[str, str, str]] = []
    burn_items: list[tuple[str, float]] = []

    for p_name, p_data in plan_payloads.items():
        tiers_dict = p_data.get("tiers") or {}
        explanations = p_data.get("explanations") or {}
        fallbacks_map = p_data.get("fallbacks") or {}
        p_node = sanitize_mermaid_id(p_name, "Plan_")
        nodes.append((p_node, p_name))

        for tier in STANDARD_TIERS:
            if tier not in tiers_dict and tier not in explanations and tier not in fallbacks_map:
                continue
            ex = explanations.get(tier) or {}
            pick = _tier_pick_id(tier, explanations, tiers_dict)
            fallback_ids = [str(fb) for fb in fallbacks_map.get(tier) or []]
            fallback_str = ", ".join(f"`{fid}`" for fid in fallback_ids) if fallback_ids else "-"
            burn = ex.get("quota_burn_pct")
            table_rows.append(
                [
                    f"`{p_name}`",
                    f"**{tier}**",
                    f"`{pick}`",
                    fallback_str,
                    format_allowance_value(ex.get("monthly_allowance")),
                    format_cost_value(ex.get("output_cost_per_1m")),
                    format_burn(burn),
                ]
            )

            tier_label = f"{tier}: {pick.removeprefix('opencode-go/')}"
            if fallback_ids:
                tier_label += f" (fb: {fallback_ids[0].removeprefix('opencode-go/')})"
            edges.append((p_node, sanitize_mermaid_id(f"{p_name}_{tier}", "T_"), tier_label))

            if is_number(burn):
                burn_items.append((f"{p_name}:{tier}", round(float(burn), 1)))

    lines = [
        "",
        "---",
        "",
        heading,
        "",
        *render_markdown_table(
            ["Plan", "Tier", "Pick", "Fallbacks", "Allowance", "Output Cost", "Burn %"],
            table_rows,
        ),
        "",
        *render_mermaid_flowchart(nodes, edges),
    ]

    if len(burn_items) >= 2:
        lines.extend(
            [
                "",
                *render_mermaid_xychart(
                    "Quota Burn % Across Plans and Tiers",
                    [item[0] for item in burn_items],
                    y_axis='"Quota Burn %" 0 --> 100',
                    bars=[[item[1] for item in burn_items]],
                ),
            ]
        )

    if include_chart_image and "multi-plan-comparison.svg" in generated_svgs:
        lines.extend(["", "![Multi-Plan Comparison](charts/multi-plan-comparison.svg)"])
    return lines


def _collect_pareto_models(
    rank_models: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], bool]:
    pareto_models = [m for m in rank_models if m.get("is_pareto")]
    pareto_models.sort(key=_model_quality, reverse=True)
    has_aa = any(m.get("aa_iq") is not None or m.get("aa_coding") is not None for m in rank_models)
    return pareto_models, has_aa


def _pareto_rows(
    pareto_models: list[dict[str, Any]], heavy: float, balanced: float
) -> list[list[str]]:
    rows: list[list[str]] = []
    for m in pareto_models:
        q = _model_quality(m)
        burn = _model_burn_pct(m)
        value = q / burn if burn is not None and burn > 0 and q > 0 else 0.0
        bands: list[str] = []
        if q >= heavy:
            bands.append("heavy")
        if q >= balanced:
            bands.append("balanced")
        bands.append("light")
        rows.append(
            [
                f"`{m['full_id']}`",
                format_float(m.get("aa_iq"), 1),
                format_float(m.get("aa_coding"), 1),
                f"`{q:.1f}`",
                format_burn(burn),
                format_allowance_value(m.get("monthly_allowance")),
                format_float(value, 2),
                ", ".join(bands),
            ]
        )
    return rows


def _render_pareto_charts(
    pareto_models: list[dict[str, Any]],
    rank_models: list[dict[str, Any]],
) -> list[str]:
    if not pareto_models:
        return []
    top = pareto_models[:12]
    x_ids = [_short_id(m).replace('"', "") for m in top]
    q_vals = [round(_model_quality(m), 1) for m in top]
    b_vals = [round(_model_burn_pct(m) or 0.0, 1) for m in top]
    return [
        "",
        *render_mermaid_xychart(
            "Pareto Frontier: Effective Quality vs Quota Burn %",
            x_ids,
            y_axis='"Quality / Burn %"',
            bars=[q_vals],
            lines_series=[b_vals],
        ),
        *_render_quadrant_chart(rank_models),
    ]


def render_analysis_markdown(
    select_payload: dict[str, Any],
    rank_models: list[dict[str, Any]],
    plan_payloads: dict[str, dict[str, Any]] | None = None,
    free_matrix: list[dict[str, Any]] | None = None,
    run_url: str | None = None,
    charts_dir: str | Path | None = None,
    site_url: str | None = None,
    *,
    pareto_error: str = "",
    allowance_select_payload: dict[str, Any] | None = None,
    allowance_rank_models: list[dict[str, Any]] | None = None,
    allowance_plan_payloads: dict[str, dict[str, Any]] | None = None,
) -> str:
    """Render comprehensive live Pareto and Allowance analysis markdown (analysis.md)."""
    site_base = _normalize_site_url(site_url)

    lines = _render_report_header(
        "## OpenCode Go Model Optimizer — Live Pareto Analysis & Catalog",
        select_payload,
        default_mode="pareto",
        run_url=run_url,
        site_base=site_base,
        include_pages_home=False,
    )
    lines.extend(["", "---", "", "### 1. Live Pareto Frontier & Quality/Value Bands", ""])

    pareto_models, has_aa = _collect_pareto_models(rank_models)
    pareto_failed = bool(pareto_error) or (not has_aa and allowance_select_payload is not None)
    top_quality = _model_quality(pareto_models[0]) if pareto_models else 0.0
    heavy_cutoff = HEAVY_QUALITY_RATIO * top_quality
    balanced_cutoff = BALANCED_QUALITY_RATIO * top_quality

    generated_svgs: list[str] = []
    if charts_dir and not pareto_failed:
        generated_svgs = generate_analysis_svgs(
            charts_dir=Path(charts_dir),
            pareto_models=pareto_models,
            rank_models=rank_models,
            plan_payloads=plan_payloads,
            top_quality=top_quality,
            heavy_cutoff=heavy_cutoff,
            balanced_cutoff=balanced_cutoff,
        )
    elif charts_dir and allowance_plan_payloads:
        generated_svgs = generate_analysis_svgs(
            charts_dir=Path(charts_dir),
            pareto_models=[],
            rank_models=allowance_rank_models or [],
            plan_payloads=allowance_plan_payloads,
            top_quality=0.0,
            heavy_cutoff=0.0,
            balanced_cutoff=0.0,
        )

    if pareto_failed:
        lines.extend(
            [
                "> [!CAUTION]",
                "> **Artificial Analysis / Pareto frontier analysis failed (no fallback used).**",
                "",
                *render_details_block(
                    "Pareto error output",
                    pareto_error or "Artificial Analysis benchmarks unavailable.",
                    open_by_default=True,
                ),
            ]
        )
    else:
        lines.extend(
            [
                f"- **Top Frontier Quality**: `{top_quality:.1f}`",
                f"- **Heavy Band Cutoff (≥ {HEAVY_QUALITY_RATIO:.0%})**: `{heavy_cutoff:.1f}`",
                f"- **Balanced Band Cutoff (≥ {BALANCED_QUALITY_RATIO:.0%})**: `{balanced_cutoff:.1f}`",
                "",
                *render_markdown_table(
                    [
                        "Model ID",
                        "AA IQ",
                        "Engineering",
                        "Quality",
                        "Burn %",
                        "Allowance",
                        "Value",
                        "Eligible Bands",
                    ],
                    _pareto_rows(pareto_models, heavy_cutoff, balanced_cutoff),
                ),
            ]
        )

        lines.extend(_render_pareto_charts(pareto_models, rank_models))

        if "pareto-frontier.svg" in generated_svgs:
            lines.extend(["", "![Live Pareto Frontier Curve](charts/pareto-frontier.svg)"])

    if free_matrix:
        lines.extend(_render_free_matrix_section(free_matrix))

    if plan_payloads and not pareto_failed:
        lines.extend(_render_multi_plan_section(plan_payloads, generated_svgs))

    if allowance_plan_payloads:
        lines.extend(
            _render_multi_plan_section(
                allowance_plan_payloads,
                generated_svgs,
                heading="### Allowance Method: Multi-Plan Tier Selections & Top-3 Fallbacks",
                include_chart_image=pareto_failed,
            )
        )

    if rank_models and not pareto_failed:
        lines.extend(
            [
                "",
                "---",
                "",
                "### 4. Full Ranked Catalog (`rank --mode pareto`)",
                "",
                *_render_ranked_catalog_rows(rank_models),
            ]
        )
        if "catalog-ranking.svg" in generated_svgs:
            lines.extend(["", "![Catalog Ranking Breakdown](charts/catalog-ranking.svg)"])

    if allowance_rank_models:
        lines.extend(
            [
                "",
                "---",
                "",
                "### Allowance Method: Full Ranked Catalog (`rank --mode allowance`)",
                "",
                *_render_ranked_catalog_rows(allowance_rank_models),
            ]
        )

    if has_aa:
        lines.extend(["", "---", "*Source: Artificial Analysis (artificialanalysis.ai)*"])

    return "\n".join(lines) + "\n"
