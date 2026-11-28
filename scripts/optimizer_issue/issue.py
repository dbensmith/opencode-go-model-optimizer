"""Markdown renderer for the tracking issue and Docsify README."""

from __future__ import annotations

from typing import Any

from opencode_go_model_optimizer.formatters.markdown import (
    format_allowance_value,
    format_burn,
    format_cost_value,
    format_float,
    format_int,
    format_multiplier,
    format_pareto,
    format_privacy,
    is_number,
    render_details_block,
    render_markdown_table,
    render_mermaid_flowchart,
    render_mermaid_xychart,
    sanitize_mermaid_id,
)

from .common import (
    _normalize_site_url,
    _ordered_tiers,
    _render_ranked_catalog_rows,
    _render_report_header,
    _site_file,
    _site_page,
    _tier_pick_id,
)


def _is_active_tier(tier: str, explanations: dict[str, Any], tiers: dict[str, Any]) -> bool:
    return _tier_pick_id(tier, explanations, tiers) != "None"


def _tier_flowchart_label(tier: str, explanations: dict[str, Any], tiers: dict[str, Any]) -> str:
    pick = _tier_pick_id(tier, explanations, tiers)
    if pick == "None":
        return f"{tier}: None"
    ex = explanations.get(tier) or {}
    clean_id = pick.removeprefix("opencode-go/")
    allow_str = format_allowance_value(ex.get("monthly_allowance"))
    burn_val = ex.get("quota_burn_pct")
    burn_part = f"{burn_val:.1f}% burn" if is_number(burn_val) else "- burn"
    return f"{tier}: {clean_id} ({allow_str}, {burn_part})"


def _series(explanations: dict[str, Any], tiers: list[str], key: str) -> list[float]:
    return [round(float(explanations.get(t, {}).get(key) or 0.0), 1) for t in tiers]


def _render_benchmark_xychart(
    ordered_tiers: list[str],
    explanations: dict[str, Any],
    tiers: dict[str, Any],
) -> list[str]:
    active = [t for t in ordered_tiers if _is_active_tier(t, explanations, tiers)]
    chart_tiers = [
        t
        for t in active
        if any(explanations.get(t, {}).get(k) is not None for k in ("aa_iq", "aa_coding"))
        or (is_number(explanations.get(t, {}).get("quota_burn_pct")) and t != "free")
    ]
    chart_tiers = chart_tiers or active
    if not chart_tiers:
        return []
    return [
        "",
        *render_mermaid_xychart(
            "Tier Comparison: Benchmark Scores vs Quota Burn %",
            chart_tiers,
            y_axis='"Score / Burn %" 0 --> 100',
            bars=[_series(explanations, chart_tiers, k) for k in ("aa_iq", "aa_coding")],
            lines_series=[_series(explanations, chart_tiers, "quota_burn_pct")],
        ),
    ]


def _render_allowance_xychart(
    ordered_tiers: list[str],
    explanations: dict[str, Any],
    tiers: dict[str, Any],
) -> list[str]:
    paid = [t for t in ordered_tiers if t != "free" and _is_active_tier(t, explanations, tiers)]
    if not paid:
        return []
    return [
        "",
        *render_mermaid_xychart(
            "Paid Tier Allowance vs Output Cost",
            paid,
            y_axis='"Allowance ($) / Cost ($/1M)"',
            bars=[_series(explanations, paid, "monthly_allowance")],
            lines_series=[_series(explanations, paid, "output_cost_per_1m")],
        ),
    ]


def _render_visual_tier_summary(
    plan: str,
    mode: str,
    ordered_tiers: list[str],
    explanations: dict[str, Any],
    tiers: dict[str, Any],
) -> list[str]:
    if not ordered_tiers or not any(_is_active_tier(t, explanations, tiers) for t in ordered_tiers):
        return []

    edges = [
        ("Root", sanitize_mermaid_id(t, "T_"), _tier_flowchart_label(t, explanations, tiers))
        for t in ordered_tiers
    ]
    lines = [
        "",
        "#### Visual Tier Summary",
        "",
        *render_mermaid_flowchart([("Root", f"{plan} ({mode})")], edges),
    ]

    has_benchmarks = any(
        explanations.get(t, {}).get(k) is not None
        for t in ordered_tiers
        for k in ("aa_iq", "aa_coding")
    )
    has_burn_or_allow = any(
        is_number(explanations.get(t, {}).get(k))
        for t in ordered_tiers
        for k in ("quota_burn_pct", "monthly_allowance")
    )

    if has_benchmarks:
        lines.extend(_render_benchmark_xychart(ordered_tiers, explanations, tiers))
    elif has_burn_or_allow:
        lines.extend(_render_allowance_xychart(ordered_tiers, explanations, tiers))

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
        model_id = _tier_pick_id(tier, explanations, tiers)
        lines.extend(["", f"#### `{tier}` — `{model_id}`"])
        if model_id == "None" or "monthly_allowance" not in ex:
            lines.append("- No eligible model available for this tier.")
            continue

        lines.append(
            f"- **Pricing & Allowance**: {format_allowance_value(ex.get('monthly_allowance'))} monthly allowance, "
            f"{format_cost_value(ex.get('output_cost_per_1m'))} output cost, "
            f"${ex.get('turn_cost_usd', 0.0):.4f}/turn"
            + (
                f", price multiplier `{format_multiplier(ex.get('price_multiplier'), ex.get('next_price_change'))}`"
                if ex.get("price_multiplier") is not None
                else ""
            )
            + "."
        )
        burn_str = format_burn(ex.get("quota_burn_pct"), 2)
        req_5h = format_int(ex.get("requests_per_5h"))
        req_mo = format_int(ex.get("requests_per_month"))
        basis = ex.get("burn_basis_used") or "-"
        lines.append(
            f"- **Quota Burn**: {burn_str} per 1k turns (basis: `{basis}`); "
            f"estimated requests: {req_5h} / 5h, {req_mo} / month."
        )
        if any(
            ex.get(k) is not None for k in ("aa_iq", "aa_coding", "speed_tps", "bpe", "is_pareto")
        ):
            lines.append(
                f"- **Benchmarks & Pareto**: IQ `{format_float(ex.get('aa_iq'), 1)}`, "
                f"Coding `{format_float(ex.get('aa_coding'), 1)}`, "
                f"Speed `{format_float(ex.get('speed_tps'), 1)} tok/s`, "
                f"BPE `{format_float(ex.get('bpe'), 3)}`, "
                f"Pareto frontier `{format_pareto(ex.get('is_pareto'))}`."
            )
        if ex.get("privacy") is not None:
            priv_text = format_privacy(ex.get("privacy")).rstrip(".")
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

    has_aa_data = benchmark_source in ("aa_live", "aa_cache")
    choice_rows: list[list[str]] = []
    for tier in ordered_tiers:
        ex = explanations.get(tier) or {}
        model_id = _tier_pick_id(tier, explanations, tiers)
        if any(ex.get(k) is not None for k in ("aa_iq", "aa_coding", "speed_tps")):
            has_aa_data = True
        burn_str = format_burn(ex.get("quota_burn_pct"))
        turn_cost = ex.get("turn_cost_usd")
        turn_str = f"${turn_cost:.4f}" if is_number(turn_cost) else "-"
        choice_rows.append(
            [
                f"**{tier}**",
                f"`{model_id}`",
                format_allowance_value(ex.get("monthly_allowance")),
                format_cost_value(ex.get("output_cost_per_1m")),
                format_int(ex.get("context")),
                format_float(ex.get("aa_iq"), 1),
                format_float(ex.get("aa_coding"), 1),
                format_float(ex.get("speed_tps"), 1),
                format_float(ex.get("bpe"), 3),
                format_pareto(ex.get("is_pareto")),
                burn_str,
                turn_str,
                format_multiplier(ex.get("price_multiplier"), ex.get("next_price_change")),
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

    script_details = render_details_block(
        "<code>scripts/run-optimizer.sh</code> output",
        script_output,
        open_by_default=True,
    )
    if script_details:
        lines.extend(["", "### Full Script Output", "", *script_details])

    if has_aa_data:
        lines.extend(["", "---", "*Source: Artificial Analysis (artificialanalysis.ai)*"])

    return "\n".join(lines) + "\n"


def _render_method_choices_table(
    ordered_tiers: list[str],
    explanations: dict[str, Any],
    tiers: dict[str, Any],
    *,
    include_aa: bool,
) -> tuple[list[str], bool]:
    """Render a method's choices table, returning ``(markdown_lines, saw_aa_metrics)``."""
    saw_aa = False
    headers = ["Tier", "Model ID", "Allowance", "Output Cost", "Context"]
    if include_aa:
        headers.extend(["AA IQ", "AA Coding", "Speed (tok/s)", "BPE", "Pareto"])
    headers.extend(["Burn %", "Turn Cost", "Price x"])

    rows: list[list[str]] = []
    for tier in ordered_tiers:
        ex = explanations.get(tier) or {}
        model_id = _tier_pick_id(tier, explanations, tiers)
        if any(ex.get(k) is not None for k in ("aa_iq", "aa_coding", "speed_tps")):
            saw_aa = True
        burn_str = format_burn(ex.get("quota_burn_pct"))
        turn_cost = ex.get("turn_cost_usd")
        turn_str = f"${turn_cost:.4f}" if is_number(turn_cost) else "-"
        row = [
            f"**{tier}**",
            f"`{model_id}`",
            format_allowance_value(ex.get("monthly_allowance")),
            format_cost_value(ex.get("output_cost_per_1m")),
            format_int(ex.get("context")),
        ]
        if include_aa:
            row.extend(
                [
                    format_float(ex.get("aa_iq"), 1),
                    format_float(ex.get("aa_coding"), 1),
                    format_float(ex.get("speed_tps"), 1),
                    format_float(ex.get("bpe"), 3),
                    format_pareto(ex.get("is_pareto")),
                ]
            )
        row.extend(
            [
                burn_str,
                turn_str,
                format_multiplier(ex.get("price_multiplier"), ex.get("next_price_change")),
            ]
        )
        rows.append(row)
    return render_markdown_table(headers, rows), saw_aa


def _render_dual_published_artifacts_section(site_base: str, *, pareto_ok: bool) -> list[str]:
    pareto_select_entry = (
        [
            "Pareto selection JSON",
            f"[pareto/select.json]({_site_file(site_base, 'pareto/select.json')})",
        ]
        if pareto_ok
        else [
            "Pareto failure log",
            f"[pareto/error.txt]({_site_file(site_base, 'pareto/error.txt')})",
        ]
    )
    rows = [
        [
            "Live analysis, Pareto frontier & multi-plan fallbacks",
            f"[Open page]({_site_page(site_base, 'analysis')})",
        ],
        ["Complete data index", f"[Open page]({_site_page(site_base, 'data')})"],
        pareto_select_entry,
    ]
    if pareto_ok:
        rows.extend(
            [
                [
                    "Pareto ranked catalog (all models)",
                    f"[pareto/rank.json]({_site_file(site_base, 'pareto/rank.json')})",
                ],
                [
                    "Pareto `go` / `go-plus` / `go-teams` top-3 choices",
                    f"[go]({_site_file(site_base, 'pareto/plans/go.json')}) | "
                    f"[go-plus]({_site_file(site_base, 'pareto/plans/go-plus.json')}) | "
                    f"[go-teams]({_site_file(site_base, 'pareto/plans/go-teams.json')})",
                ],
            ]
        )
    rows.extend(
        [
            [
                "Allowance selection JSON",
                f"[allowance/select.json]({_site_file(site_base, 'allowance/select.json')})",
            ],
            [
                "Allowance ranked catalog (all models)",
                f"[allowance/rank.json]({_site_file(site_base, 'allowance/rank.json')})",
            ],
            [
                "Allowance `go` / `go-plus` / `go-teams` top-3 choices",
                f"[go]({_site_file(site_base, 'allowance/plans/go.json')}) | "
                f"[go-plus]({_site_file(site_base, 'allowance/plans/go-plus.json')}) | "
                f"[go-teams]({_site_file(site_base, 'allowance/plans/go-teams.json')})",
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
            [
                "Ingest warnings & provenance log",
                f"[fetch-log.txt]({_site_file(site_base, 'fetch-log.txt')})",
            ],
        ]
    )
    return [
        "",
        "### Other Choices & Published Data",
        "",
        "Every artifact from this run is published on the GitHub Pages site:",
        "",
        *render_markdown_table(["Artifact", "Link"], rows),
    ]


def render_dual_issue_markdown(
    pareto_payload: dict[str, Any] | None,
    allowance_payload: dict[str, Any],
    *,
    pareto_rank_models: list[dict[str, Any]] | None = None,
    allowance_rank_models: list[dict[str, Any]] | None = None,
    pareto_error: str = "",
    script_output: str = "",
    run_url: str | None = None,
    site_url: str | None = None,
    snapshot_payload: dict[str, Any] | None = None,
) -> str:
    """Render issue and Docsify README publishing both Pareto and Allowance without fallback."""
    from opencode_go_model_optimizer.formatters.markdown import render_run_settings_lines

    site_base = _normalize_site_url(site_url)
    primary = pareto_payload or allowance_payload
    plan = primary.get("plan", "go")
    evaluated_at = primary.get("evaluated_at", "-")
    snap_info = primary.get("snapshot") or {}
    snap_meta = (snapshot_payload or {}).get("metadata") or {}
    snap_source = snap_info.get("source", "file")
    fetched_at = (snapshot_payload or {}).get("fetched_at") or snap_info.get("fetched_at", "-")
    benchmark_source = snap_meta.get("benchmark_source") or snap_info.get(
        "benchmark_source", "none"
    )
    benchmark_error = snap_meta.get("benchmark_error") or snap_info.get("benchmark_error")

    pareto_ok = (
        pareto_payload is not None
        and (pareto_payload.get("method") or pareto_payload.get("mode")) == "pareto"
        and not pareto_payload.get("fallback")
    )

    lines = [
        "## OpenCode Go Model Optimizer — Latest Tier Selections",
        "",
        f"- **Updated**: `{evaluated_at}`",
        f"- **Plan**: `{plan}` | **Snapshot**: `{snap_source}` (`{fetched_at}`) | **Benchmarks**: `{benchmark_source}`",
        f"- **Pareto method (`--mode pareto`, no fallback)**: "
        f"{'**SUCCEEDED**' if pareto_ok else '**FAILED** (no fallback used)'}",
        "- **Allowance method (`--mode allowance`, no fallback)**: **SUCCEEDED**",
    ]
    if run_url:
        lines.append(f"- **Workflow Run**: {run_url}")
    if site_base:
        lines.append(f"- **GitHub Pages**: {site_base}")
        lines.append(f"- **All data & other choices**: {_site_page(site_base, 'data')}")

    if not pareto_ok:
        detail = benchmark_error or (
            pareto_error.strip().splitlines()[-1] if pareto_error.strip() else ""
        )
        suffix = f": `{detail}`" if detail else "."
        lines.extend(
            [
                "",
                "> [!CAUTION]",
                f"> **Artificial Analysis / Pareto calculation FAILED (no fallback used)**{suffix}",
                "> Both methods are published below so the Artificial Analysis failure is immediately visible.",
            ]
        )

    # Side-by-side method comparison table
    a_tiers = allowance_payload.get("tiers") or {}
    a_ex = allowance_payload.get("explanations") or {}
    p_tiers = (pareto_payload or {}).get("tiers") or {}
    p_ex = (pareto_payload or {}).get("explanations") or {}
    comp_tiers = _ordered_tiers(a_ex, a_tiers)
    comp_rows: list[list[str]] = []
    for tier in comp_tiers:
        a_pick = _tier_pick_id(tier, a_ex, a_tiers)
        if pareto_ok:
            p_pick = _tier_pick_id(tier, p_ex, p_tiers)
            match_str = "Same" if p_pick == a_pick else "Different"
            p_cell = f"`{p_pick}`"
        else:
            p_cell = "FAILED (no fallback)"
            match_str = "-"
        comp_rows.append([f"**{tier}**", p_cell, f"`{a_pick}`", match_str])
    lines.extend(
        [
            "",
            "### Method Comparison",
            "",
            *render_markdown_table(
                [
                    "Tier",
                    "Pareto Pick (AA Benchmarks)",
                    "Allowance Pick (Plan Pricing)",
                    "Match",
                ],
                comp_rows,
            ),
        ]
    )

    settings = primary.get("settings") or {}
    if settings:
        display_settings = {
            **settings,
            "mode": "pareto + allowance (no fallback)",
            "allow_fallback": False,
        }
        lines.extend(
            [
                "",
                *render_run_settings_lines(
                    display_settings,
                    heading="### Run Settings",
                    default_mode="pareto + allowance",
                    default_top_n=1,
                ),
            ]
        )

    has_aa_data = False
    lines.extend(["", "### Pareto Method Selections (`--mode pareto`, no fallback)"])
    if pareto_ok and pareto_payload is not None:
        p_ordered = _ordered_tiers(p_ex, p_tiers)
        p_table, saw_aa = _render_method_choices_table(p_ordered, p_ex, p_tiers, include_aa=True)
        has_aa_data = benchmark_source in ("aa_live", "aa_cache") or saw_aa
        lines.extend(["", *p_table])
        lines.extend(_render_visual_tier_summary(plan, "pareto", p_ordered, p_ex, p_tiers))
        lines.extend(_render_tier_explanations("pareto", p_ordered, p_ex, p_tiers))
        if pareto_rank_models:
            lines.extend(
                [
                    "",
                    "#### Pareto Full Comparison Catalog (`rank --mode pareto`)",
                    "",
                    f"All {len(pareto_rank_models)} models evaluated by `rank --mode pareto`.",
                    "",
                    *_render_ranked_catalog_rows(pareto_rank_models),
                ]
            )
    else:
        lines.extend(
            [
                "",
                "Pareto selection failed because Artificial Analysis benchmarks were unavailable and fallback is disabled.",
            ]
        )
        err_block = render_details_block(
            "Pareto error output",
            pareto_error or benchmark_error or "Pareto calculation failed (no stderr captured).",
            open_by_default=True,
        )
        if err_block:
            lines.extend(["", *err_block])

    lines.extend(["", "### Allowance Method Selections (`--mode allowance`, no fallback)"])
    a_ordered = _ordered_tiers(a_ex, a_tiers)
    a_table, _ = _render_method_choices_table(a_ordered, a_ex, a_tiers, include_aa=False)
    lines.extend(["", *a_table])
    lines.extend(_render_visual_tier_summary(plan, "allowance", a_ordered, a_ex, a_tiers))
    lines.extend(_render_tier_explanations("allowance", a_ordered, a_ex, a_tiers))
    if allowance_rank_models:
        lines.extend(
            [
                "",
                "#### Allowance Full Comparison Catalog (`rank --mode allowance`)",
                "",
                f"All {len(allowance_rank_models)} models evaluated by `rank --mode allowance`.",
                "",
                *_render_ranked_catalog_rows(allowance_rank_models),
            ]
        )

    if site_base:
        lines.extend(_render_dual_published_artifacts_section(site_base, pareto_ok=pareto_ok))

    script_details = render_details_block(
        "<code>optimizer</code> full output",
        script_output,
        open_by_default=not pareto_ok,
    )
    if script_details:
        lines.extend(["", "### Full Script Output", "", *script_details])

    if pareto_ok and has_aa_data:
        lines.extend(["", "---", "*Source: Artificial Analysis (artificialanalysis.ai)*"])

    return "\n".join(lines) + "\n"
