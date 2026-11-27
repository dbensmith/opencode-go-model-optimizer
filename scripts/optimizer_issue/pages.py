"""Markdown page renderers for the tracking issue, live analysis page, and data index."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from opencode_go_model_optimizer.engine.tiers import STANDARD_TIERS

from .charts import generate_analysis_svgs
from .common import (
    _RANKED_CATALOG_HEADERS,
    _catalog_composition_pie_lines,
    _extract_quality_params,
    _fence_for,
    _fmt_allowance,
    _fmt_burn,
    _fmt_float,
    _fmt_int,
    _fmt_multiplier,
    _fmt_output_cost,
    _fmt_pareto,
    _fmt_privacy,
    _format_settings_lines,
    _model_burn_pct,
    _model_id,
    _model_quality,
    _model_quality_and_value,
    _normalize_site_url,
    _ordered_tiers,
    _ranked_catalog_row,
    _site_file,
    _site_page,
)


def _render_report_header(
    heading: str,
    payload: dict[str, Any],
    *,
    default_mode: str,
    run_url: str | None,
    site_base: str | None,
    include_pages_home: bool,
) -> list[str]:
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


def _render_visual_tier_summary(
    plan: str,
    mode: str,
    ordered_tiers: list[str],
    explanations: dict[str, Any],
    tiers: dict[str, Any],
) -> list[str]:
    has_active_model = any(
        (explanations.get(t, {}).get("model_id") or tiers.get(t) or "None") != "None"
        for t in ordered_tiers
    )
    if not ordered_tiers or not has_active_model:
        return []

    lines = [
        "",
        "#### Visual Tier Summary",
        "",
        "```mermaid",
        "flowchart LR",
        f'    Root["{plan} ({mode})"]',
    ]
    for t in ordered_tiers:
        ex = explanations.get(t) or {}
        m_id = ex.get("model_id") or tiers.get(t) or "None"
        clean_id = m_id.removeprefix("opencode-go/") if m_id != "None" else "None"
        if clean_id == "None":
            node_label = f"{t}: None"
        else:
            allow_str = _fmt_allowance(ex.get("monthly_allowance"))
            burn_val = ex.get("quota_burn_pct")
            burn_part = (
                f"{burn_val:.1f}% burn"
                if isinstance(burn_val, (int, float)) and not isinstance(burn_val, bool)
                else "- burn"
            )
            node_label = f"{t}: {clean_id} ({allow_str}, {burn_part})"
        node_label = node_label.replace('"', "'")
        t_node = f"T_{t.replace('-', '_')}"
        lines.append(f'    Root --> {t_node}["{node_label}"]')
    lines.append("```")

    has_benchmarks = any(
        explanations.get(t, {}).get("aa_iq") is not None
        or explanations.get(t, {}).get("aa_coding") is not None
        for t in ordered_tiers
    )
    has_burn_or_allow = any(
        isinstance(explanations.get(t, {}).get("quota_burn_pct"), (int, float))
        or isinstance(explanations.get(t, {}).get("monthly_allowance"), (int, float))
        for t in ordered_tiers
    )

    if has_benchmarks:
        chart_tiers = [
            t
            for t in ordered_tiers
            if (
                explanations.get(t, {}).get("aa_iq") is not None
                or explanations.get(t, {}).get("aa_coding") is not None
                or (
                    isinstance(explanations.get(t, {}).get("quota_burn_pct"), (int, float))
                    and t != "free"
                )
            )
            and (explanations.get(t, {}).get("model_id") or tiers.get(t) or "None") != "None"
        ]
        if not chart_tiers:
            chart_tiers = [
                t
                for t in ordered_tiers
                if (explanations.get(t, {}).get("model_id") or tiers.get(t) or "None") != "None"
            ]
        if chart_tiers:
            iq_vals = [
                round(float(explanations.get(t, {}).get("aa_iq") or 0.0), 1) for t in chart_tiers
            ]
            coding_vals = [
                round(float(explanations.get(t, {}).get("aa_coding") or 0.0), 1)
                for t in chart_tiers
            ]
            burn_vals = [
                round(float(explanations.get(t, {}).get("quota_burn_pct") or 0.0), 1)
                for t in chart_tiers
            ]
            x_str = ", ".join(f'"{t}"' for t in chart_tiers)
            lines.extend(
                [
                    "",
                    "```mermaid",
                    "xychart-beta",
                    '    title "Tier Comparison: Benchmark Scores vs Quota Burn %"',
                    f"    x-axis [{x_str}]",
                    '    y-axis "Score / Burn %" 0 --> 100',
                    f"    bar [{', '.join(str(v) for v in iq_vals)}]",
                    f"    bar [{', '.join(str(v) for v in coding_vals)}]",
                    f"    line [{', '.join(str(v) for v in burn_vals)}]",
                    "```",
                ]
            )
    elif has_burn_or_allow:
        paid_tiers = [
            t
            for t in ordered_tiers
            if t != "free"
            and (explanations.get(t, {}).get("model_id") or tiers.get(t) or "None") != "None"
        ]
        if paid_tiers:
            allow_vals = [
                round(float(explanations.get(t, {}).get("monthly_allowance") or 0.0), 1)
                for t in paid_tiers
            ]
            cost_vals = [
                round(float(explanations.get(t, {}).get("output_cost_per_1m") or 0.0), 1)
                for t in paid_tiers
            ]
            x_str = ", ".join(f'"{t}"' for t in paid_tiers)
            lines.extend(
                [
                    "",
                    "```mermaid",
                    "xychart-beta",
                    '    title "Paid Tier Allowance vs Output Cost"',
                    f"    x-axis [{x_str}]",
                    '    y-axis "Allowance ($) / Cost ($/1M)"',
                    f"    bar [{', '.join(str(v) for v in allow_vals)}]",
                    f"    line [{', '.join(str(v) for v in cost_vals)}]",
                    "```",
                ]
            )

    return lines


def _render_tier_explanations(
    mode: str,
    ordered_tiers: list[str],
    explanations: dict[str, Any],
    tiers: dict[str, Any],
) -> list[str]:
    lines = ["", "### Update Explanation", ""]
    if mode == "pareto":
        lines.append(
            "Selected in **Pareto mode** using Bidirectional Pareto Efficiency (BPE) "
            "over Artificial Analysis benchmarks vs. monthly quota burn. Only Pareto-optimal "
            "models are eligible: `heavy` is the value winner among frontier models within "
            "10% of the best quality, `balanced` the value winner among models at least 60% "
            "as capable as the best, and `light` the lowest-burn frontier model; tiers may "
            "share a model. Plus the newest eligible zero-cost `free` model."
        )
    else:
        lines.append(
            f"Selected in **{mode} mode** ranking active eligible models by published plan "
            "allowance, effective output token cost, release recency, and context window."
        )

    for tier in ordered_tiers:
        ex = explanations.get(tier) or {}
        model_id = ex.get("model_id") or tiers.get(tier) or "None"
        lines.extend(["", f"#### `{tier}` — `{model_id}`"])
        if model_id == "None" or "monthly_allowance" not in ex:
            lines.append("- No eligible model available for this tier.")
            continue

        lines.append(
            f"- **Pricing & Allowance**: {_fmt_allowance(ex.get('monthly_allowance'))} monthly allowance, "
            f"{_fmt_output_cost(ex.get('output_cost_per_1m'))} output cost, "
            f"${ex.get('turn_cost_usd', 0.0):.4f}/turn"
            + (
                f", price multiplier `{_fmt_multiplier(ex)}`"
                if ex.get("price_multiplier") is not None
                else ""
            )
            + "."
        )
        burn_str = _fmt_burn(ex.get("quota_burn_pct"), 2)
        req_5h = _fmt_int(ex.get("requests_per_5h"))
        req_mo = _fmt_int(ex.get("requests_per_month"))
        basis = ex.get("burn_basis_used") or "-"
        lines.append(
            f"- **Quota Burn**: {burn_str} per 1k turns (basis: `{basis}`); "
            f"estimated requests: {req_5h} / 5h, {req_mo} / month."
        )
        if any(
            ex.get(k) is not None for k in ("aa_iq", "aa_coding", "speed_tps", "bpe", "is_pareto")
        ):
            lines.append(
                f"- **Benchmarks & Pareto**: IQ `{_fmt_float(ex.get('aa_iq'), 1)}`, "
                f"Coding `{_fmt_float(ex.get('aa_coding'), 1)}`, "
                f"Speed `{_fmt_float(ex.get('speed_tps'), 1)} tok/s`, "
                f"BPE `{_fmt_float(ex.get('bpe'), 3)}`, "
                f"Pareto frontier `{_fmt_pareto(ex.get('is_pareto'))}`."
            )
        if ex.get("privacy") is not None:
            priv_text = _fmt_privacy(ex.get("privacy")).rstrip(".")
            lines.append(f"- **Privacy**: {priv_text}.")
    return lines


def _render_ranked_catalog_rows(rank_models: list[dict[str, Any]]) -> list[str]:
    lines = [*_RANKED_CATALOG_HEADERS]
    for m in rank_models:
        lines.append(_ranked_catalog_row(m))
    lines.extend(_catalog_composition_pie_lines(rank_models))
    return lines


def _render_published_artifacts_section(site_base: str) -> list[str]:
    return [
        "",
        "### Other Choices & Published Data",
        "",
        "Every artifact from this run is published on the GitHub Pages site:",
        "",
        "| Artifact | Link |",
        "| --- | --- |",
        f"| Live analysis, Pareto frontier & multi-plan fallbacks "
        f"| [Open page]({_site_page(site_base, 'analysis')}) |",
        f"| Complete data index | [Open page]({_site_page(site_base, 'data')}) |",
        f"| Full ranked catalog (all models) | [rank.json]({_site_file(site_base, 'rank.json')}) |",
        f"| Raw selection payload | [select.json]({_site_file(site_base, 'select.json')}) |",
        f"| `go` top-3 choices per tier "
        f"| [plans/go.json]({_site_file(site_base, 'plans/go.json')}) |",
        f"| `go-plus` top-3 choices per tier "
        f"| [plans/go-plus.json]({_site_file(site_base, 'plans/go-plus.json')}) |",
        f"| `go-teams` top-3 choices per tier "
        f"| [plans/go-teams.json]({_site_file(site_base, 'plans/go-teams.json')}) |",
        f"| Free-tier choices across privacy/region flags "
        f"| [free-matrix.json]({_site_file(site_base, 'free-matrix.json')}) |",
        f"| Normalized ingest snapshot from every source "
        f"| [snapshot.json]({_site_file(site_base, 'snapshot.json')}) |",
        f"| Full script output (stdout/stderr) "
        f"| [full-output.txt]({_site_file(site_base, 'full-output.txt')}) |",
        f"| Human-readable table output | [table.txt]({_site_file(site_base, 'table.txt')}) |",
        f"| Ingest warnings & provenance log "
        f"| [fetch-log.txt]({_site_file(site_base, 'fetch-log.txt')}) |",
        f"| Pareto frontier chart "
        f"| [pareto-frontier.svg]({_site_file(site_base, 'charts/pareto-frontier.svg')}) |",
        f"| Catalog ranking chart "
        f"| [catalog-ranking.svg]({_site_file(site_base, 'charts/catalog-ranking.svg')}) |",
        f"| Multi-plan comparison chart "
        f"| [multi-plan-comparison.svg]({_site_file(site_base, 'charts/multi-plan-comparison.svg')}) |",
    ]


def render_issue_markdown(
    payload: dict[str, Any],
    script_output: str = "",
    run_url: str | None = None,
    rank_models: list[dict[str, Any]] | None = None,
    site_url: str | None = None,
) -> str:
    """Render optimizer JSON payload, optional full ranked catalog and full script output."""
    site_base = _normalize_site_url(site_url)
    plan = payload.get("plan", "go")
    mode = payload.get("mode", "unknown")
    snapshot = payload.get("snapshot") or {}
    benchmark_source = snapshot.get("benchmark_source", "none")
    explanations = payload.get("explanations") or {}
    tiers = payload.get("tiers") or {}
    ordered_tiers = _ordered_tiers(explanations, tiers)

    lines = _render_report_header(
        "## OpenCode Go Model Optimizer — Latest Tier Selections",
        payload,
        default_mode="unknown",
        run_url=run_url,
        site_base=site_base,
        include_pages_home=True,
    )

    lines.extend(
        [
            "",
            "### Latest Model Choices",
            "",
            "| Tier | Model ID | Allowance | Output Cost | Context | AA IQ | AA Coding | Speed (tok/s) | BPE | Pareto | Burn % | Turn Cost | Price x |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    has_aa_data = benchmark_source == "aa_live"
    for tier in ordered_tiers:
        ex = explanations.get(tier) or {}
        model_id = ex.get("model_id") or tiers.get(tier) or "None"
        if any(ex.get(k) is not None for k in ("aa_iq", "aa_coding", "speed_tps")):
            has_aa_data = True
        burn_str = _fmt_burn(ex.get("quota_burn_pct"))
        turn_cost = ex.get("turn_cost_usd")
        turn_str = (
            f"${turn_cost:.4f}"
            if isinstance(turn_cost, (int, float)) and not isinstance(turn_cost, bool)
            else "-"
        )
        lines.append(
            f"| **{tier}** | `{model_id}` | {_fmt_allowance(ex.get('monthly_allowance'))} "
            f"| {_fmt_output_cost(ex.get('output_cost_per_1m'))} | {_fmt_int(ex.get('context'))} "
            f"| {_fmt_float(ex.get('aa_iq'), 1)} | {_fmt_float(ex.get('aa_coding'), 1)} "
            f"| {_fmt_float(ex.get('speed_tps'), 1)} | {_fmt_float(ex.get('bpe'), 3)} "
            f"| {_fmt_pareto(ex.get('is_pareto'))} | {burn_str} | {turn_str} "
            f"| {_fmt_multiplier(ex)} |"
        )

    lines.extend(_render_visual_tier_summary(plan, mode, ordered_tiers, explanations, tiers))
    lines.extend(_render_tier_explanations(mode, ordered_tiers, explanations, tiers))

    if rank_models:
        lines.extend(
            [
                "",
                f"### Full Comparison Catalog (`rank --mode {mode}`)",
                "",
                f"All {len(rank_models)} models evaluated for this run by `rank --mode {mode}`, "
                "not just the tier winners above.",
                "",
                *_render_ranked_catalog_rows(rank_models),
            ]
        )

    if site_base:
        lines.extend(_render_published_artifacts_section(site_base))

    cleaned_script = script_output.strip()
    if cleaned_script:
        fence = _fence_for(cleaned_script)
        lines.extend(
            [
                "",
                "### Full Script Output",
                "",
                "<details open>",
                "<summary><code>scripts/run-optimizer.sh</code> output</summary>",
                "",
                f"{fence}text",
                cleaned_script,
                fence,
                "",
                "</details>",
            ]
        )

    if has_aa_data:
        lines.extend(["", "---", "*Source: Artificial Analysis (artificialanalysis.ai)*"])

    return "\n".join(lines) + "\n"


def _quadrant_points(
    rank_models: list[dict[str, Any]],
    profile: str,
    weights: tuple[float, float] | None,
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
            quality = _model_quality(m, profile=profile, weights=weights)
            clean_id = _model_id(m, "model").removeprefix("opencode-go/")
            safe_label = re.sub(r"[^a-zA-Z0-9-]", "-", clean_id).strip("-") or f"m-{len(pts) + 1}"
            pts.append((safe_label, burn if burn is not None else 0.0, quality))
        return pts

    return collect(include_zero_cost=False, require_burn=True) or collect(
        include_zero_cost=True, require_burn=False
    )


def _render_quadrant_chart(
    rank_models: list[dict[str, Any]],
    *,
    profile: str,
    weights: tuple[float, float] | None,
) -> list[str]:
    benchmarked_models = _quadrant_points(rank_models, profile=profile, weights=weights)
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
    lines = [
        "",
        "---",
        "",
        "### 2. Live Free-Tier Picks Across Privacy/Region Flags",
        "",
        "| Setting | CLI Flags | Selected Free Model |",
        "| --- | --- | --- |",
    ]
    for row in free_matrix:
        label = row.get("label", "Default")
        flags = row.get("flags", "")
        model_id = row.get("model_id", "None")
        lines.append(f"| {label} | `{flags}` | `{model_id}` |")

    lines.extend(["", "```mermaid", "flowchart LR"])
    for i, row in enumerate(free_matrix):
        lbl = (row.get("label") or f"Setting {i + 1}").replace('"', "'")
        mid = (row.get("model_id") or "None").removeprefix("opencode-go/").replace('"', "'")
        m_node = f"M_{re.sub(r'[^a-zA-Z0-9_]', '_', mid)}"
        lines.append(f'    S{i}["{lbl}"] --> {m_node}["{mid}"]')
    lines.append("```")
    return lines


def _render_multi_plan_section(
    plan_payloads: dict[str, dict[str, Any]],
    generated_svgs: list[str],
) -> list[str]:
    lines = [
        "",
        "---",
        "",
        "### 3. Multi-Plan Tier Selections & Top-3 Fallbacks",
        "",
        "| Plan | Tier | Pick | Fallbacks | Allowance | Output Cost | Burn % |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for p_name, p_data in plan_payloads.items():
        tiers_dict = p_data.get("tiers") or {}
        explanations = p_data.get("explanations") or {}
        fallbacks_map = p_data.get("fallbacks") or {}
        for tier in STANDARD_TIERS:
            if tier not in tiers_dict and tier not in explanations and tier not in fallbacks_map:
                continue
            ex = explanations.get(tier) or {}
            pick = ex.get("model_id") or tiers_dict.get(tier) or "None"

            raw_fallbacks = fallbacks_map.get(tier) or ex.get("fallbacks") or []
            fallback_ids: list[str] = []
            for fb in raw_fallbacks:
                if isinstance(fb, str):
                    fallback_ids.append(fb)
                elif isinstance(fb, dict) and fb.get("model_id"):
                    fallback_ids.append(str(fb["model_id"]))
            fallback_str = ", ".join(f"`{fid}`" for fid in fallback_ids) if fallback_ids else "-"

            allowance_s = _fmt_allowance(ex.get("monthly_allowance"))
            output_s = _fmt_output_cost(ex.get("output_cost_per_1m"))
            burn_s = _fmt_burn(_model_burn_pct(ex))
            lines.append(
                f"| `{p_name}` | **{tier}** | `{pick}` | {fallback_str} | {allowance_s} | {output_s} | {burn_s} |"
            )

    lines.extend(["", "```mermaid", "flowchart LR"])
    for p_name, p_data in plan_payloads.items():
        tiers_dict = p_data.get("tiers") or {}
        explanations = p_data.get("explanations") or {}
        fallbacks_map = p_data.get("fallbacks") or {}
        p_node = f"Plan_{p_name.replace('-', '_')}"
        lines.append(f'    {p_node}["{p_name}"]')
        for tier in STANDARD_TIERS:
            if tier not in tiers_dict and tier not in explanations and tier not in fallbacks_map:
                continue
            ex = explanations.get(tier) or {}
            pick = (
                (ex.get("model_id") or tiers_dict.get(tier) or "None")
                .removeprefix("opencode-go/")
                .replace('"', "'")
            )
            raw_fb = fallbacks_map.get(tier) or ex.get("fallbacks") or []
            fb_id = None
            if raw_fb:
                first = raw_fb[0]
                fb_id = (
                    (first.get("model_id") if isinstance(first, dict) else str(first))
                    .removeprefix("opencode-go/")
                    .replace('"', "'")
                )
            tier_label = f"{tier}: {pick} (fb: {fb_id})" if fb_id else f"{tier}: {pick}"
            t_node = f"T_{p_name.replace('-', '_')}_{tier}"
            lines.append(f'    {p_node} --> {t_node}["{tier_label}"]')
    lines.append("```")

    burn_items: list[tuple[str, float]] = []
    for p_name, p_data in plan_payloads.items():
        explanations = p_data.get("explanations") or {}
        for tier in STANDARD_TIERS:
            burn = _model_burn_pct(explanations.get(tier) or {})
            if burn is not None:
                burn_items.append((f"{p_name}:{tier}", round(burn, 1)))

    if len(burn_items) >= 2:
        x_cats = ", ".join(f'"{item[0]}"' for item in burn_items)
        b_vals = ", ".join(str(item[1]) for item in burn_items)
        lines.extend(
            [
                "",
                "```mermaid",
                "xychart-beta",
                '    title "Quota Burn % Across Plans and Tiers"',
                f"    x-axis [{x_cats}]",
                '    y-axis "Quota Burn %" 0 --> 100',
                f"    bar [{b_vals}]",
                "```",
            ]
        )

    if "multi-plan-comparison.svg" in generated_svgs:
        lines.extend(["", "![Multi-Plan Comparison](charts/multi-plan-comparison.svg)"])
    return lines


def render_analysis_markdown(
    select_payload: dict[str, Any],
    rank_models: list[dict[str, Any]],
    plan_payloads: dict[str, dict[str, Any]] | None = None,
    free_matrix: list[dict[str, Any]] | None = None,
    run_url: str | None = None,
    charts_dir: str | Path | None = None,
    site_url: str | None = None,
) -> str:
    """Render comprehensive live Pareto analysis and catalog markdown (analysis.md)."""
    site_base = _normalize_site_url(site_url)
    profile, weights = _extract_quality_params(select_payload)

    lines = _render_report_header(
        "## OpenCode Go Model Optimizer — Live Pareto Analysis & Catalog",
        select_payload,
        default_mode="pareto",
        run_url=run_url,
        site_base=site_base,
        include_pages_home=False,
    )
    lines.extend(["", "---", "", "### 1. Live Pareto Frontier & Quality/Value Bands", ""])

    pareto_models: list[dict[str, Any]] = []
    has_aa = False
    for m in rank_models:
        if m.get("aa_iq") is not None or m.get("aa_coding") is not None:
            has_aa = True
        if m.get("is_pareto"):
            quality, value = _model_quality_and_value(m, profile=profile, weights=weights)
            pareto_entry = dict(m)
            pareto_entry["_quality"] = quality
            pareto_entry["_value"] = value
            pareto_models.append(pareto_entry)

    pareto_models.sort(key=lambda x: x["_quality"], reverse=True)

    top_quality = pareto_models[0]["_quality"] if pareto_models else 0.0
    heavy_cutoff = 0.9 * top_quality
    balanced_cutoff = 0.6 * top_quality

    generated_svgs: list[str] = []
    if charts_dir:
        generated_svgs = generate_analysis_svgs(
            charts_dir=Path(charts_dir),
            pareto_models=pareto_models,
            rank_models=rank_models,
            plan_payloads=plan_payloads,
            top_quality=top_quality,
            heavy_cutoff=heavy_cutoff,
            balanced_cutoff=balanced_cutoff,
            profile=profile,
            weights=weights,
        )

    lines.extend(
        [
            f"- **Top Frontier Quality**: `{top_quality:.1f}`",
            f"- **Heavy Band Cutoff (≥ 90%)**: `{heavy_cutoff:.1f}`",
            f"- **Balanced Band Cutoff (≥ 60%)**: `{balanced_cutoff:.1f}`",
            "",
            "| Model ID | AA IQ | Engineering | Quality | Burn % | Allowance | Value | Eligible Bands |",
            "| --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    for m in pareto_models:
        q = m["_quality"]
        bands: list[str] = []
        if q >= heavy_cutoff:
            bands.append("heavy")
        if q >= balanced_cutoff:
            bands.append("balanced")
        bands.append("light")
        model_id = _model_id(m)
        iq_s = _fmt_float(m.get("aa_iq"), 1)
        eng_s = _fmt_float(m.get("aa_coding"), 1)
        burn_s = _fmt_burn(_model_burn_pct(m))
        allowance_s = _fmt_allowance(m.get("monthly_allowance"))
        val_s = _fmt_float(m.get("_value"), 2)

        lines.append(
            f"| `{model_id}` | {iq_s} | {eng_s} | `{q:.1f}` | {burn_s} | {allowance_s} | {val_s} | {', '.join(bands)} |"
        )

    if pareto_models:
        slice_pareto = pareto_models[:12]
        x_ids = [_model_id(m).removeprefix("opencode-go/").replace('"', "") for m in slice_pareto]
        q_vals = [round(float(m["_quality"]), 1) for m in slice_pareto]
        b_vals = [round(_model_burn_pct(m) or 0.0, 1) for m in slice_pareto]

        x_axis_str = ", ".join(f'"{x}"' for x in x_ids)
        q_str = ", ".join(str(q) for q in q_vals)
        b_str = ", ".join(str(b) for b in b_vals)

        lines.extend(
            [
                "",
                "```mermaid",
                "xychart-beta",
                '    title "Pareto Frontier: Effective Quality vs Quota Burn %"',
                f"    x-axis [{x_axis_str}]",
                '    y-axis "Quality / Burn %"',
                f"    bar [{q_str}]",
                f"    line [{b_str}]",
                "```",
            ]
        )
        lines.extend(_render_quadrant_chart(rank_models, profile=profile, weights=weights))

    if "pareto-frontier.svg" in generated_svgs:
        lines.extend(["", "![Live Pareto Frontier Curve](charts/pareto-frontier.svg)"])

    if free_matrix:
        lines.extend(_render_free_matrix_section(free_matrix))

    if plan_payloads:
        lines.extend(_render_multi_plan_section(plan_payloads, generated_svgs))

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

    if has_aa:
        lines.extend(["", "---", "*Source: Artificial Analysis (artificialanalysis.ai)*"])

    return "\n".join(lines) + "\n"


def render_data_markdown(
    site_url: str | None = None,
    evaluated_at: str | None = None,
    run_url: str | None = None,
) -> str:
    """Render the data index page linking every artifact published to GitHub Pages."""
    site_base = _normalize_site_url(site_url)
    lines = [
        "# OpenCode Go Model Optimizer — All Data & Other Choices",
        "",
        "This page indexes every artifact published by the latest optimizer run. Use it to",
        "inspect the models that were **not** selected, the fallback chains for each plan, and",
        "the normalized data collected from every ingest source.",
        "",
    ]
    if evaluated_at:
        lines.append(f"- **Updated**: `{evaluated_at}`")
    if run_url:
        lines.append(f"- **Workflow Run**: {run_url}")
    if site_base:
        lines.append(f"- **GitHub Pages**: {site_base}")
    lines.extend(
        [
            "- **GitHub Issue**: {{ISSUES_URL}}",
            "- **GitHub Repository**: {{REPO_URL}}",
            "",
            "## Rendered pages",
            "",
            "| Page | Contents |",
            "| --- | --- |",
            "| [Latest tier selections](README.md) | Final `heavy`, `balanced`, `light` and `free` picks |",
            "| [Live Pareto analysis & plans](analysis.md) | Frontier, quality/value bands, free-tier matrix, multi-plan top-3 fallbacks, and the full ranked catalog |",
            "| [Documentation & CLI usage](usage.md) | How to run the optimizer yourself |",
            "",
            "## Other choices and raw data",
            "",
            "| Artifact | Contents |",
            "| --- | --- |",
            "| [select.json](select.json ':ignore') | Raw `select --format json --explain` payload for the configured plan |",
            "| [rank.json](rank.json ':ignore') | Every model ranked by the optimizer, including models that did not win a tier |",
            "| [plans/go.json](plans/go.json ':ignore') | `go` plan top-3 choices per tier with explanations |",
            "| [plans/go-plus.json](plans/go-plus.json ':ignore') | `go-plus` plan top-3 choices per tier with explanations |",
            "| [plans/go-teams.json](plans/go-teams.json ':ignore') | `go-teams` plan top-3 choices per tier with explanations |",
            "| [free-matrix.json](free-matrix.json ':ignore') | Free-tier picks under every privacy/region flag combination |",
            "| [snapshot.json](snapshot.json ':ignore') | Normalized ingest snapshot: catalogue, docs pricing/privacy/request estimates, models.dev metadata, and Artificial Analysis benchmarks when enabled |",
            "| [full-output.txt](full-output.txt ':ignore') | Full stdout/stderr from the optimizer script |",
            "| [table.txt](table.txt ':ignore') | Human-readable table output from `scripts/run-optimizer.sh` |",
            "| [fetch-log.txt](fetch-log.txt ':ignore') | Ingest warnings and provenance messages from the snapshot fetch |",
            "",
            "## Charts (SVG)",
            "",
            "| Chart | Contents |",
            "| --- | --- |",
            "| [pareto-frontier.svg](charts/pareto-frontier.svg ':ignore') | Effective quality vs quota burn with frontier and value bands |",
            "| [catalog-ranking.svg](charts/catalog-ranking.svg ':ignore') | Ranked model catalog with benchmark scores and burn |",
            "| [multi-plan-comparison.svg](charts/multi-plan-comparison.svg ':ignore') | Monthly allowance vs quota burn across plans and paid tiers |",
            "",
            "## Data sources",
            "",
            "| Source | Used for | Notes |",
            "| --- | --- | --- |",
            "| OpenCode catalogue (API/CLI) | Model IDs, providers, canonical IDs, status, token prices, multipliers | Fetched live each run |",
            "| OpenCode docs | Monthly plan allowances, privacy/training rules, request estimates | Fetched live each run |",
            "| models.dev | Release dates, context windows, max output tokens | `opencode-go` provider only |",
            "| Artificial Analysis | IQ, engineering capability, output speed | Only when `AA_API_KEY` is configured; always attributed |",
            "",
            "The normalized snapshot records the URL, HTTP status, fetch time, sha256, and byte length",
            "for each source. Raw response bodies are never published.",
            "",
        ]
    )
    if site_base:
        lines.extend(
            [
                "## Quick links",
                "",
                "- **Issue**: {{ISSUES_URL}}",
                f"- **Raw ranked catalog**: {_site_file(site_base, 'rank.json')}",
                f"- **Raw normalized snapshot**: {_site_file(site_base, 'snapshot.json')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
