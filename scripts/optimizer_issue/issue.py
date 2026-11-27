"""Markdown renderer for the tracking issue and Docsify README."""

from __future__ import annotations

from typing import Any

from opencode_go_model_optimizer.formatters.markdown import (
    render_markdown_table,
    render_mermaid_xychart,
)

from .common import (
    _fence_for,
    _fmt_allowance,
    _fmt_burn,
    _fmt_float,
    _fmt_int,
    _fmt_multiplier,
    _fmt_output_cost,
    _fmt_pareto,
    _fmt_privacy,
    _normalize_site_url,
    _ordered_tiers,
    _render_ranked_catalog_rows,
    _render_report_header,
    _site_file,
    _site_page,
)


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
            lines.extend(
                [
                    "",
                    *render_mermaid_xychart(
                        "Tier Comparison: Benchmark Scores vs Quota Burn %",
                        chart_tiers,
                        y_axis='"Score / Burn %" 0 --> 100',
                        bars=[iq_vals, coding_vals],
                        lines_series=[burn_vals],
                    ),
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
            lines.extend(
                [
                    "",
                    *render_mermaid_xychart(
                        "Paid Tier Allowance vs Output Cost",
                        paid_tiers,
                        y_axis='"Allowance ($) / Cost ($/1M)"',
                        bars=[allow_vals],
                        lines_series=[cost_vals],
                    ),
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


def _render_published_artifacts_section(site_base: str) -> list[str]:
    rows = [
        [
            "Live analysis, Pareto frontier & multi-plan fallbacks",
            f"[Open page]({_site_page(site_base, 'analysis')})",
        ],
        ["Complete data index", f"[Open page]({_site_page(site_base, 'data')})"],
        ["Full ranked catalog (all models)", f"[rank.json]({_site_file(site_base, 'rank.json')})"],
        ["Raw selection payload", f"[select.json]({_site_file(site_base, 'select.json')})"],
        [
            "`go` top-3 choices per tier",
            f"[plans/go.json]({_site_file(site_base, 'plans/go.json')})",
        ],
        [
            "`go-plus` top-3 choices per tier",
            f"[plans/go-plus.json]({_site_file(site_base, 'plans/go-plus.json')})",
        ],
        [
            "`go-teams` top-3 choices per tier",
            f"[plans/go-teams.json]({_site_file(site_base, 'plans/go-teams.json')})",
        ],
        [
            "Free-tier choices across privacy/region flags",
            f"[free-matrix.json]({_site_file(site_base, 'free-matrix.json')})",
        ],
        [
            "Normalized ingest snapshot from every source",
            f"[snapshot.json]({_site_file(site_base, 'snapshot.json')})",
        ],
        [
            "Full script output (stdout/stderr)",
            f"[full-output.txt]({_site_file(site_base, 'full-output.txt')})",
        ],
        ["Human-readable table output", f"[table.txt]({_site_file(site_base, 'table.txt')})"],
        [
            "Ingest warnings & provenance log",
            f"[fetch-log.txt]({_site_file(site_base, 'fetch-log.txt')})",
        ],
        [
            "Pareto frontier chart",
            f"[pareto-frontier.svg]({_site_file(site_base, 'charts/pareto-frontier.svg')})",
        ],
        [
            "Catalog ranking chart",
            f"[catalog-ranking.svg]({_site_file(site_base, 'charts/catalog-ranking.svg')})",
        ],
        [
            "Multi-plan comparison chart",
            f"[multi-plan-comparison.svg]({_site_file(site_base, 'charts/multi-plan-comparison.svg')})",
        ],
    ]
    return [
        "",
        "### Other Choices & Published Data",
        "",
        "Every artifact from this run is published on the GitHub Pages site:",
        "",
        *render_markdown_table(["Artifact", "Link"], rows),
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

    has_aa_data = benchmark_source == "aa_live"
    choice_rows: list[list[str]] = []
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
        choice_rows.append(
            [
                f"**{tier}**",
                f"`{model_id}`",
                _fmt_allowance(ex.get("monthly_allowance")),
                _fmt_output_cost(ex.get("output_cost_per_1m")),
                _fmt_int(ex.get("context")),
                _fmt_float(ex.get("aa_iq"), 1),
                _fmt_float(ex.get("aa_coding"), 1),
                _fmt_float(ex.get("speed_tps"), 1),
                _fmt_float(ex.get("bpe"), 3),
                _fmt_pareto(ex.get("is_pareto")),
                burn_str,
                turn_str,
                _fmt_multiplier(ex),
            ]
        )

    lines.extend(
        [
            "",
            "### Latest Model Choices",
            "",
            *render_markdown_table(
                [
                    "Tier",
                    "Model ID",
                    "Allowance",
                    "Output Cost",
                    "Context",
                    "AA IQ",
                    "AA Coding",
                    "Speed (tok/s)",
                    "BPE",
                    "Pareto",
                    "Burn %",
                    "Turn Cost",
                    "Price x",
                ],
                choice_rows,
            ),
        ]
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
