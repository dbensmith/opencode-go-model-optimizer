"""Markdown rendering for the publishable results report."""

from __future__ import annotations

import math
from typing import Any

from opencode_go_model_optimizer.engine.tiers import PAID_TIERS, STANDARD_TIERS
from opencode_go_model_optimizer.formatters.markdown import (
    format_allowance_value,
    format_cost_value,
    render_markdown_table,
    render_mermaid_pie,
    render_mermaid_xychart,
    render_run_settings_lines,
)

_EXCLUSION_BUCKETS = (
    "Global Region Required",
    "Non-text Model",
    "Incomplete Catalogue Data",
    "Free Model Trains",
    "Paid Model Trains",
    "Not Active",
    "Unpriced / Other",
)


def _fmt_cost(value: Any) -> str:
    return format_cost_value(value, compact=True)


def _format_allowance(val: Any) -> str:
    return format_allowance_value(val, compact=True, unlimited_label="unlimited")


def _format_cluster_range(cluster: dict[str, Any] | None) -> str:
    if cluster and cluster.get("output_cost_per_1m_range"):
        lo, hi = cluster["output_cost_per_1m_range"]
        return f"${lo:g}-${hi:g}/1M"
    return "N/A"


def _bucket_exclusion_reason(reason: str) -> str:
    r = reason.lower()
    if "global" in r:
        return "Global Region Required"
    if "not a text model" in r:
        return "Non-text Model"
    if "zero-priced" in r:
        return "Incomplete Catalogue Data"
    if "free model trains" in r:
        return "Free Model Trains"
    if "paid model trains" in r:
        return "Paid Model Trains"
    if "not active" in r or "status is" in r:
        return "Not Active"
    return "Unpriced / Other"


def _round_chart_num(val: Any) -> float | int:
    if isinstance(val, (int, float)) and not math.isinf(val) and not math.isnan(val):
        return int(val) if float(val).is_integer() else round(float(val), 4)
    return 0


def _render_cross_plan_flowchart(plans: dict[str, Any]) -> list[str]:
    lines = ["```mermaid", "flowchart LR"]
    for plan, section in plans.items():
        p_id = "plan_" + "".join(c if c.isalnum() else "_" for c in str(plan))
        lines.append(f'    {p_id}["Plan: {plan}"]')
        tiers_data = section.get("explanations") or {}
        tier_keys = [t for t in STANDARD_TIERS if t in tiers_data] or list(tiers_data.keys())
        for tier in tier_keys:
            entry = tiers_data[tier]
            t_id = f"{p_id}_{tier}"
            pick = entry.get("model_id") or "None"
            ex = entry.get("explanation", {})
            allowance_str = _format_allowance(ex.get("monthly_allowance"))
            cluster_str = _format_cluster_range(entry.get("cluster"))
            label = f"{tier}: {pick} (Allowance: {allowance_str}, Cluster: {cluster_str})".replace(
                '"', "'"
            )
            lines.append(f'    {p_id} --> {t_id}["{label}"]')
    lines += ["```", ""]
    return lines


def _render_plan_markdown(plan: str, section: dict[str, Any]) -> list[str]:
    explanations = section.get("explanations", {})
    tier_rows = [
        [
            tier,
            f"`{entry['model_id'] or '-'}`",
            entry["explanation"].get("monthly_allowance", "-"),
            _fmt_cost(entry["explanation"].get("output_cost_per_1m")),
            entry["explanation"].get("price_multiplier", 1),
            entry["explanation"].get("quota_burn_pct", "-"),
            entry["explanation"].get("burn_basis_used", "-"),
            ", ".join(f"`{f}`" for f in entry["fallbacks"]) or "-",
        ]
        for tier, entry in explanations.items()
    ]
    lines = [
        f"## Plan: {plan}",
        "",
        *render_markdown_table(
            [
                "Tier",
                "Pick",
                "Allowance",
                "Out $/1M",
                "Price x",
                "Burn %/1k turns",
                "Basis",
                "Fallbacks",
            ],
            tier_rows,
        ),
        "",
        "Why:",
        "",
    ]

    for tier, entry in explanations.items():
        lines.append(f"- **{tier}**: {entry['why']}")
        cluster = entry.get("cluster")
        if cluster and cluster["output_cost_per_1m_range"]:
            lo, hi = cluster["output_cost_per_1m_range"]
            lines.append(
                f"  - cluster of {cluster['size']} model(s), effective output ${lo:g}-${hi:g}/1M"
            )
        ex = entry["explanation"]
        if ex.get("privacy"):
            p = ex["privacy"]
            lines.append(
                f"  - privacy: trains_on_data={p.get('trains_on_data')}, "
                f"retention_days={p.get('retention_days')}, zdr={p.get('zdr')}, "
                f"global_region_required={p.get('requires_global_region')}"
            )

    has_numeric = any(
        isinstance(explanations.get(t, {}).get("explanation", {}).get(k), (int, float))
        for t in PAID_TIERS
        for k in ("monthly_allowance", "output_cost_per_1m")
    )
    if has_numeric:
        allowances = [
            _round_chart_num(
                explanations.get(t, {}).get("explanation", {}).get("monthly_allowance")
            )
            for t in PAID_TIERS
        ]
        output_costs = [
            _round_chart_num(
                explanations.get(t, {}).get("explanation", {}).get("output_cost_per_1m")
            )
            for t in PAID_TIERS
        ]
        lines += [
            "",
            *render_mermaid_xychart(
                f"Plan {plan}: Tier Allowances ($) & Output Costs ($/1M)",
                list(PAID_TIERS),
                bars=[allowances],
                lines_series=[output_costs],
            ),
        ]

    lines += ["", "<details><summary>Excluded models</summary>", ""]
    excluded = section.get("excluded", [])
    if excluded:
        eligible_models: set[str] = set()
        for entry in explanations.values():
            if entry.get("model_id"):
                eligible_models.add(entry["model_id"])
            eligible_models.update(entry.get("fallbacks", []))
            for c in entry.get("candidates", []):
                if c.get("model_id"):
                    eligible_models.add(c["model_id"])

        bucket_counts: dict[str, int] = {}
        for item in excluded:
            reasons = item.get("reasons", [])
            bucket = _bucket_exclusion_reason(reasons[0]) if reasons else "Unpriced / Other"
            bucket_counts[bucket] = bucket_counts.get(bucket, 0) + 1

        pie_lines = render_mermaid_pie(
            f"Plan {plan}: Model Eligibility & Exclusion Breakdown",
            [
                ("Eligible", len(eligible_models)),
                *((b, bucket_counts.get(b, 0)) for b in _EXCLUSION_BUCKETS),
            ],
        )
        if pie_lines:
            lines += [*pie_lines, ""]

        lines += render_markdown_table(
            ["Model", "Reason"],
            [[f"`{e['model_id']}`", "; ".join(e["reasons"])] for e in excluded],
        )
    else:
        lines.append("None.")
    lines += ["", "</details>", ""]
    return lines


def render_markdown(report: dict[str, Any], *, default_top_n: int = 3) -> str:
    """Render the report as human-readable Markdown."""
    lines = [
        "# OpenCode Go model tiers",
        "",
        f"Generated {report['generated_at']} by opencode-go-model-optimizer "
        f"{report['tool_version']}"
        + (f" (commit `{report['git_sha']}`)" if report["git_sha"] else "")
        + ". Allowance mode, default criteria, top 3 per tier.",
        "",
    ]
    first_plan = next(iter(report.get("plans", {}).values()), {})
    criteria = first_plan.get("criteria") or {}
    if criteria:
        lines += [
            *render_run_settings_lines(
                criteria,
                heading="## Run settings",
                default_mode="allowance",
                default_top_n=default_top_n,
            ),
            "",
        ]

    plans = report.get("plans") or {}
    if plans:
        lines += _render_cross_plan_flowchart(plans)
    for plan, section in plans.items():
        lines += _render_plan_markdown(plan, section)

    lines += ["## How this was decided", ""]
    lines += [f"- {item}" for item in report["how_decided"]]
    lines += ["", "## Warnings", ""]
    lines += [f"- {w}" for w in report["warnings"]] or ["None."]

    prov_rows = [
        [
            name,
            src["url"],
            src["status"],
            src["fetched_at"],
            f"`{src['sha256'][:16]}...`",
            src["bytes"],
        ]
        for name, src in report["provenance"]["sources"].items()
    ]
    lines += [
        "",
        "## Provenance",
        "",
        *render_markdown_table(["Source", "URL", "HTTP", "Fetched", "sha256", "Bytes"], prov_rows),
        "",
        report["provenance"]["note"] + ". Full digests are in results.json.",
        "",
        "## Test results",
        "",
    ]

    tests = report.get("tests")
    if tests is None:
        lines.append("No test results supplied.")
    else:
        lines += [f"Overall: **{tests['result']}**", ""]
        runs = tests.get("runs") or []
        if runs:
            passed = sum(r.get("passed", 0) for r in runs)
            failed = sum(r.get("failed", 0) for r in runs) + sum(1 for r in runs if "error" in r)
            skipped = sum(r.get("skipped", 0) for r in runs)
            pie_lines = render_mermaid_pie(
                "Test Suite Results",
                [("Passed", passed), ("Failed", failed), ("Skipped", skipped)],
            )
            if pie_lines:
                lines += [*pie_lines, ""]

        run_rows: list[list[Any]] = []
        for run in runs:
            run_rows.append([run["name"], run["passed"], run["failed"], run["skipped"]])
            if run.get("error"):
                run_rows.append([run["name"], run["error"], "", ""])
        lines += render_markdown_table(["Run", "Passed", "Failed", "Skipped"], run_rows)
        failing = [f"`{t}`" for run in runs for t in run["failing"]]
        if failing:
            lines += ["", "Failing tests: " + ", ".join(failing)]
    return "\n".join(lines) + "\n"
