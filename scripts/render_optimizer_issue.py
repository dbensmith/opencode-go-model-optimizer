#!/usr/bin/env python3
"""Render GitHub-pretty markdown from opencode-go-model-optimizer JSON and script output."""

from __future__ import annotations

import argparse
import html
import json
import re
import sys
from pathlib import Path
from typing import Any

TIER_ORDER = ("heavy", "balanced", "light", "free")


def _fmt_allowance(val: Any) -> str:
    if val is None:
        return "-"
    if val == "unlimited":
        return "Unlimited"
    if isinstance(val, (int, float)):
        return f"${val:.2f}"
    return str(val)


def _fmt_output_cost(val: Any) -> str:
    if val is None:
        return "-"
    if isinstance(val, (int, float)):
        return "Free" if val == 0 else f"${val:.2f}/1M"
    return str(val)


def _fmt_int(val: Any) -> str:
    if val is None:
        return "-"
    if val == "unlimited":
        return "Unlimited"
    if isinstance(val, (int, float)):
        return f"{int(val):,}" if float(val).is_integer() else f"{val:,.1f}"
    return str(val)


def _fmt_float(val: Any, decimals: int = 1) -> str:
    if val is None:
        return "-"
    if isinstance(val, (int, float)):
        return f"{val:.{decimals}f}"
    return str(val)


def _fmt_pareto(val: Any) -> str:
    if val is None:
        return "-"
    return "Yes" if val else "No"


def _fmt_multiplier(ex: dict[str, Any]) -> str:
    mult = ex.get("price_multiplier")
    if mult is None:
        return "-"
    cell = f"x{mult:.3g}"
    change = ex.get("next_price_change")
    if isinstance(change, dict) and change.get("at") and change.get("multiplier") is not None:
        cell += f" (-> x{change['multiplier']:.3g} @ {change['at']})"
    return cell


def _fmt_privacy(privacy: dict[str, Any] | None) -> str:
    if not privacy:
        return "No privacy metadata"
    parts: list[str] = []
    if privacy.get("zdr"):
        parts.append("ZDR (0d retention)")
    elif privacy.get("retention_days") is not None:
        parts.append(f"{privacy['retention_days']}d retention")
    else:
        parts.append("Retention unspecified")
    parts.append("trains on data" if privacy.get("trains_on_data") else "no training")
    if privacy.get("requires_global_region"):
        parts.append("requires Global region")
    if privacy.get("agreement_valid_until"):
        parts.append(f"agreement valid through {privacy['agreement_valid_until']}")
    if privacy.get("note"):
        parts.append(str(privacy["note"]))
    return "; ".join(parts)


def _fence_for(text: str) -> str:
    """Return a backtick fence longer than any backtick run in text."""
    longest = 0
    current = 0
    for ch in text:
        if ch == "`":
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    return "`" * max(3, longest + 1)


_STDERR_SEPARATOR = "--- stderr ---"
_ASCII_TABLE_ROW_RE = re.compile(r"^\s*\|.+\|\s*$")
_ASCII_TABLE_BORDER_RE = re.compile(r"^\s*\+[-+]+\+\s*$")


def _split_stderr_section(text: str) -> tuple[str, str]:
    """Split combined optimizer output at the workflow's stderr separator line."""
    lines = text.splitlines()
    for idx, line in enumerate(lines):
        if line.strip() == _STDERR_SEPARATOR:
            return "\n".join(lines[:idx]), "\n".join(lines[idx + 1 :])
    return text, ""


def _split_ascii_table_row(line: str) -> list[str]:
    """Split one ``| a | b |`` ASCII table row into stripped cells."""
    inner = line.strip()[1:-1]
    return [cell.strip() for cell in inner.split("|")]


def _parse_ascii_output_blocks(text: str) -> list[dict[str, Any]]:
    """Split optimizer stdout into ASCII-table and plain-line blocks, in order.

    Border lines (``+---+``) are treated as table glue and never surface as
    plain lines, so a bordered ASCII table always parses as a single block
    whose first row is the header.
    """
    blocks: list[dict[str, Any]] = []
    pending: list[str] = []
    table_rows: list[list[str]] | None = None

    def flush_pending() -> None:
        nonlocal pending
        if pending:
            blocks.append({"kind": "lines", "lines": list(pending)})
            pending.clear()

    def flush_table() -> None:
        nonlocal table_rows
        if table_rows is not None:
            blocks.append({"kind": "table", "headers": table_rows[0], "rows": table_rows[1:]})
            table_rows = None

    for raw in text.splitlines():
        if _ASCII_TABLE_BORDER_RE.match(raw):
            continue
        if _ASCII_TABLE_ROW_RE.match(raw):
            if table_rows is None:
                flush_pending()
                table_rows = []
            table_rows.append(_split_ascii_table_row(raw))
        else:
            flush_table()
            if raw.strip():
                pending.append(raw.rstrip())
    flush_table()
    flush_pending()
    return blocks


def _format_table_cell(header: str, cell: str) -> str:
    """Escape an ASCII table cell for GitHub markdown, styling known columns.

    Model IDs get code spans and tier names get bold, matching the tables in
    the issue comment.
    """
    escaped = cell.replace("|", "\\|")
    if not escaped or escaped == "-":
        return escaped
    key = header.strip().lower()
    if key == "model id":
        return f"`{escaped}`"
    if key == "tier":
        return f"**{escaped}**"
    return escaped


def _render_markdown_table(headers: list[str], rows: list[list[str]]) -> list[str]:
    """Render a parsed ASCII table as GitHub-flavored markdown table lines."""
    norm_headers = [h.strip() for h in headers]
    lines = [
        "| " + " | ".join(norm_headers) + " |",
        "| " + " | ".join("---" for _ in norm_headers) + " |",
    ]
    for row in rows:
        cells = [cell.strip() for cell in row[: len(norm_headers)]]
        cells += ["-"] * (len(norm_headers) - len(cells))
        styled = [_format_table_cell(h, c) for h, c in zip(norm_headers, cells, strict=True)]
        lines.append("| " + " | ".join(styled) + " |")
    return lines


def _format_settings_lines(payload: dict[str, Any], default_mode: str) -> list[str]:
    """Render Run Settings lines when effective settings are present in payload."""
    settings = payload.get("settings") or {}
    if not settings:
        return []
    lines = [
        "",
        "### Run Settings",
        "",
        f"- **Mode**: `{settings.get('mode', default_mode)}` | **Profile**: `{settings.get('profile', 'general')}` "
        f"| **Price basis**: `{settings.get('price_basis', '-')}` | **Burn basis**: `{settings.get('burn_basis', '-')}` "
        f"| **Top N**: `{settings.get('top_n', 1)}`",
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
        extras = []
        if skip:
            extras.append(f"skip `{', '.join(skip)}`")
        if prefer:
            extras.append("prefer " + ", ".join(f"`{k}={v}`" for k, v in prefer.items()))
        lines.append("- **Overrides**: " + " | ".join(extras))
    return lines


_RANKED_CATALOG_HEADERS = (
    "| Rank | Model ID | Tier | BPE | AA IQ | Engineering | Burn % | Allowance | Output Cost | Context | Release | Privacy |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
)


def _ranked_catalog_row(m: dict[str, Any]) -> str:
    """Render one full-catalog markdown table row from a rank JSON model entry."""
    model_id = m.get("full_id") or m.get("id") or m.get("model_id") or "unknown"
    tier = m.get("tier", "-")
    bpe_s = _fmt_float(m.get("bpe"), 3)
    iq_s = _fmt_float(m.get("aa_iq"), 1)
    eng_s = _fmt_float(m.get("aa_coding"), 1)
    burn = m.get("composite_burn_pct")
    if burn is None:
        burn = m.get("quota_burn_pct")
    burn_s = f"{burn:.1f}%" if isinstance(burn, (int, float)) else "-"
    allowance_s = _fmt_allowance(m.get("monthly_allowance"))

    if m.get("is_zero_cost"):
        output_s = "Free"
    else:
        cost_dict = m.get("cost") or {}
        output_cost = cost_dict.get("output")
        if output_cost is not None:
            output_s = _fmt_output_cost(output_cost)
        else:
            output_s = _fmt_output_cost(m.get("output_cost_per_1m"))

    limit_dict = m.get("limit") or {}
    ctx_val = limit_dict.get("context") if limit_dict else None
    if ctx_val is None:
        ctx_val = m.get("context")
    ctx_s = _fmt_int(ctx_val)

    rank_val = m.get("rank")
    rank_s = str(rank_val) if rank_val is not None else "-"

    release = m.get("release_date") or "-"
    priv = _fmt_privacy(m.get("privacy"))
    return (
        f"| {rank_s} | `{model_id}` | {tier} | {bpe_s} | {iq_s} | {eng_s} | {burn_s} "
        f"| {allowance_s} | {output_s} | {ctx_s} | {release} | {priv} |"
    )


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

    pie_lines = [
        "",
        "```mermaid",
        "pie showData",
        '    title "Catalog Composition by Eligibility & Frontier Status"',
    ]
    slices = [
        ("Pareto Frontier", pareto_count),
        ("Paid (Non-Frontier)", paid_non_frontier_count),
        ("Free / Zero-Cost", free_count),
        ("Unbenchmarked Paid", unbenchmarked_count),
    ]
    for name, count in slices:
        if count > 0:
            pie_lines.append(f'    "{name}" : {count}')
    pie_lines.append("```")
    return pie_lines


def _load_rank_models(path: str | None) -> list[dict[str, Any]]:
    """Load model entries from a `rank --format json` output file."""
    if not path:
        return []
    rank_data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(rank_data, list):
        return rank_data
    if isinstance(rank_data, dict):
        return rank_data.get("models") or rank_data.get("rankings") or []
    return []


def render_issue_markdown(
    payload: dict[str, Any],
    script_output: str = "",
    run_url: str | None = None,
    rank_models: list[dict[str, Any]] | None = None,
    site_url: str | None = None,
) -> str:
    """Render optimizer JSON payload, optional full ranked catalog and full script output."""
    plan = payload.get("plan", "go")
    mode = payload.get("mode", "unknown")
    evaluated_at = payload.get("evaluated_at", "-")
    snapshot = payload.get("snapshot") or {}
    snap_source = snapshot.get("source", "-")
    fetched_at = snapshot.get("fetched_at", "-")
    benchmark_source = snapshot.get("benchmark_source", "none")
    explanations = payload.get("explanations") or {}
    tiers = payload.get("tiers") or {}

    ordered_tiers = [t for t in TIER_ORDER if t in explanations or t in tiers]
    for t in explanations:
        if t not in ordered_tiers:
            ordered_tiers.append(t)

    lines: list[str] = [
        "## OpenCode Go Model Optimizer — Latest Tier Selections",
        "",
        f"- **Updated**: `{evaluated_at}`",
        f"- **Plan**: `{plan}` | **Mode**: `{mode}` | **Snapshot**: `{snap_source}` (`{fetched_at}`) | **Benchmarks**: `{benchmark_source}`",
    ]
    if run_url:
        lines.append(f"- **Workflow Run**: {run_url}")
    if site_url:
        base = site_url.rstrip("/")
        lines.append(
            f"- **Full dataset & analysis**: [GitHub Pages]({base}) — "
            f"[analysis & charts]({base}/analysis.md), raw [rank.json]({base}/rank.json) "
            f"and [select.json]({base}/select.json)"
        )

    lines.extend(_format_settings_lines(payload, mode))

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
        burn = ex.get("quota_burn_pct")
        burn_str = f"{burn:.1f}%" if isinstance(burn, (int, float)) else "-"
        turn_cost = ex.get("turn_cost_usd")
        turn_str = f"${turn_cost:.4f}" if isinstance(turn_cost, (int, float)) else "-"
        lines.append(
            f"| **{tier}** | `{model_id}` | {_fmt_allowance(ex.get('monthly_allowance'))} "
            f"| {_fmt_output_cost(ex.get('output_cost_per_1m'))} | {_fmt_int(ex.get('context'))} "
            f"| {_fmt_float(ex.get('aa_iq'), 1)} | {_fmt_float(ex.get('aa_coding'), 1)} "
            f"| {_fmt_float(ex.get('speed_tps'), 1)} | {_fmt_float(ex.get('bpe'), 3)} "
            f"| {_fmt_pareto(ex.get('is_pareto'))} | {burn_str} | {turn_str} "
            f"| {_fmt_multiplier(ex)} |"
        )

    has_active_model = any(
        (explanations.get(t, {}).get("model_id") or tiers.get(t) or "None") != "None"
        for t in ordered_tiers
    )
    if ordered_tiers and has_active_model:
        lines.extend(
            [
                "",
                "#### Visual Tier Summary",
                "",
                "```mermaid",
                "flowchart LR",
                f'    Root["{plan} ({mode})"]',
            ]
        )
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
                    f"{burn_val:.1f}% burn" if isinstance(burn_val, (int, float)) else "- burn"
                )
                node_label = f"{t}: {clean_id} ({allow_str}, {burn_part})"
            node_label = node_label.replace('"', "'")
            t_node = f"T_{t.replace('-', '_')}"
            lines.append(f'    Root --> {t_node}["{node_label}"]')
        lines.append("```")

        has_benchmarks = any(
            (
                explanations.get(t, {}).get("aa_iq") is not None
                or explanations.get(t, {}).get("aa_coding") is not None
            )
            for t in ordered_tiers
        )
        has_burn_or_allow = any(
            (
                isinstance(explanations.get(t, {}).get("quota_burn_pct"), (int, float))
                or isinstance(explanations.get(t, {}).get("monthly_allowance"), (int, float))
            )
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
                    round(float(explanations.get(t, {}).get("aa_iq") or 0.0), 1)
                    for t in chart_tiers
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

    lines.extend(["", "### Update Explanation", ""])
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
        burn = ex.get("quota_burn_pct")
        burn_str = f"{burn:.2f}%" if isinstance(burn, (int, float)) else "-"
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

    if rank_models:
        lines.extend(
            [
                "",
                f"### Full Comparison Catalog (`rank --mode {mode}`)",
                "",
                f"All {len(rank_models)} models evaluated for this run by `rank --mode {mode}`, "
                "not just the tier winners above.",
                "",
                *_RANKED_CATALOG_HEADERS,
            ]
        )
        for m in rank_models:
            lines.append(_ranked_catalog_row(m))
        lines.extend(_catalog_composition_pie_lines(rank_models))

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


def generate_analysis_svgs(
    charts_dir: Path,
    pareto_models: list[dict[str, Any]],
    rank_models: list[dict[str, Any]],
    plan_payloads: dict[str, dict[str, Any]] | None,
    top_quality: float,
    heavy_cutoff: float,
    balanced_cutoff: float,
) -> list[str]:
    """Generate crisp, self-contained SVG charts for live Pareto analysis."""
    charts_dir.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    css_styles = """
    :root {
      --bg: #ffffff;
      --card-bg: #f8fafc;
      --text: #0f172a;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --grid: #f1f5f9;
      --c-blue: #2563eb;
      --c-green: #10b981;
      --c-amber: #f59e0b;
      --c-purple: #8b5cf6;
      --c-slate: #94a3b8;
      --band-heavy: rgba(16, 185, 129, 0.12);
      --band-balanced: rgba(37, 99, 235, 0.08);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0d1117;
        --card-bg: #161b22;
        --text: #f0f6fc;
        --text-muted: #8b949e;
        --border: #30363d;
        --grid: #21262d;
        --c-blue: #58a6ff;
        --c-green: #3fb950;
        --c-amber: #d29922;
        --c-purple: #bc8cff;
        --c-slate: #6e7681;
        --band-heavy: rgba(63, 185, 80, 0.16);
        --band-balanced: rgba(88, 166, 255, 0.12);
      }
    }
    text {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    """

    # 1. pareto-frontier.svg
    ml, mr, mt, mb = 65, 35, 55, 55
    pw = 800 - ml - mr
    ph = 420 - mt - mb

    all_burns = [
        float(
            m.get("composite_burn_pct")
            if m.get("composite_burn_pct") is not None
            else (m.get("quota_burn_pct") or 0.0)
        )
        for m in rank_models
        if (m.get("composite_burn_pct") is not None or m.get("quota_burn_pct") is not None)
    ]
    max_burn = max([*all_burns, 100.0])
    x_max = max_burn * 1.08
    max_q = max([top_quality, *(float(m.get("_quality", 0.0)) for m in pareto_models), 100.0])
    y_max = max_q * 1.08

    def to_x(b: float) -> float:
        return ml + (b / x_max) * pw

    def to_y(q: float) -> float:
        return mt + ph - (q / y_max) * ph

    frontier_svg = [
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 420" width="100%" height="100%">',
        f"<style>{css_styles}</style>",
        '<rect width="800" height="420" rx="8" fill="var(--bg)"/>',
        '<text x="400" y="30" text-anchor="middle" font-size="15" font-weight="bold" fill="var(--text)">Live Pareto Frontier: Effective Quality vs Quota Burn %</text>',
    ]

    # Shaded bands
    y_top = to_y(y_max)
    y_h = to_y(heavy_cutoff)
    y_b = to_y(balanced_cutoff)
    if heavy_cutoff > 0:
        frontier_svg.append(
            f'<rect x="{ml}" y="{y_top:.1f}" width="{pw}" height="{max(0.0, y_h - y_top):.1f}" fill="var(--band-heavy)"/>'
        )
        frontier_svg.append(
            f'<text x="{ml + 10}" y="{y_top + 16:.1f}" font-size="11" font-weight="600" fill="var(--c-green)">Heavy Band (≥90%: {heavy_cutoff:.1f})</text>'
        )
    if balanced_cutoff > 0:
        frontier_svg.append(
            f'<rect x="{ml}" y="{y_h:.1f}" width="{pw}" height="{max(0.0, y_b - y_h):.1f}" fill="var(--band-balanced)"/>'
        )
        frontier_svg.append(
            f'<text x="{ml + 10}" y="{y_h + 16:.1f}" font-size="11" font-weight="600" fill="var(--c-blue)">Balanced Band (≥60%: {balanced_cutoff:.1f})</text>'
        )

    # Grid lines & ticks
    for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
        gy = to_y(frac * y_max)
        frontier_svg.append(
            f'<line x1="{ml}" y1="{gy:.1f}" x2="{ml + pw}" y2="{gy:.1f}" stroke="var(--grid)" stroke-width="1"/>'
        )
        frontier_svg.append(
            f'<text x="{ml - 8}" y="{gy + 4:.1f}" text-anchor="end" font-size="10" fill="var(--text-muted)">{int(frac * y_max)}</text>'
        )
        gx = to_x(frac * x_max)
        frontier_svg.append(
            f'<line x1="{gx:.1f}" y1="{mt}" x2="{gx:.1f}" y2="{mt + ph}" stroke="var(--grid)" stroke-width="1"/>'
        )
        frontier_svg.append(
            f'<text x="{gx:.1f}" y="{mt + ph + 16}" text-anchor="middle" font-size="10" fill="var(--text-muted)">{int(frac * x_max)}%</text>'
        )

    # Axes
    frontier_svg.append(
        f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="var(--border)" stroke-width="1.5"/>'
    )
    frontier_svg.append(
        f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="var(--border)" stroke-width="1.5"/>'
    )
    frontier_svg.append(
        f'<text x="{ml + pw / 2:.1f}" y="405" text-anchor="middle" font-size="12" fill="var(--text-muted)">Quota Burn %</text>'
    )
    frontier_svg.append(
        f'<text x="18" y="{mt + ph / 2:.1f}" text-anchor="middle" font-size="12" fill="var(--text-muted)" transform="rotate(-90, 18, {mt + ph / 2:.1f})">Effective Quality</text>'
    )

    # Dominated models
    for m in rank_models:
        if m.get("is_pareto"):
            continue
        iq = m.get("aa_iq")
        coding = m.get("aa_coding")
        if iq is None and coding is None:
            continue
        q = 0.7 * float(iq or 0) + 0.3 * float(coding or 0)
        b = m.get("composite_burn_pct")
        if b is None:
            b = m.get("quota_burn_pct")
        if not isinstance(b, (int, float)):
            continue
        cx = to_x(float(b))
        cy = to_y(q)
        frontier_svg.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="4" fill="var(--c-slate)" opacity="0.45"/>'
        )

    # Pareto frontier models
    sorted_frontier = sorted(
        pareto_models,
        key=lambda x: float(
            x.get("composite_burn_pct")
            if x.get("composite_burn_pct") is not None
            else (x.get("quota_burn_pct") or 0.0)
        ),
    )
    if sorted_frontier:
        pts: list[tuple[float, float, float, float, dict[str, Any]]] = []
        for m in sorted_frontier:
            b = float(
                m.get("composite_burn_pct")
                if m.get("composite_burn_pct") is not None
                else (m.get("quota_burn_pct") or 0.0)
            )
            q = float(m.get("_quality", 0.0))
            pts.append((to_x(b), to_y(q), b, q, m))

        path_d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"
        for i in range(1, len(pts)):
            path_d += f" L {pts[i][0]:.1f} {pts[i - 1][1]:.1f} L {pts[i][0]:.1f} {pts[i][1]:.1f}"
        frontier_svg.append(
            f'<path d="{path_d}" fill="none" stroke="var(--c-blue)" stroke-width="2.5" stroke-dasharray="4,3"/>'
        )

        for cx, cy, b, q, m in pts:
            raw_id = m.get("full_id") or m.get("id") or m.get("model_id") or "model"
            short_id = raw_id.removeprefix("opencode-go/")
            frontier_svg.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" fill="var(--bg)" stroke="var(--c-blue)" stroke-width="2.5"/>'
            )
            frontier_svg.append(
                f'<text x="{cx + 8:.1f}" y="{cy - 6:.1f}" font-size="11" font-weight="600" fill="var(--text)">'
                f"{html.escape(short_id)} ({b:.1f}%, {q:.1f})</text>"
            )

    frontier_svg.append("</svg>")
    (charts_dir / "pareto-frontier.svg").write_text("\n".join(frontier_svg), encoding="utf-8")
    generated.append("pareto-frontier.svg")

    # 2. catalog-ranking.svg
    top_models = rank_models[:12]
    row_h = 42
    header_h = 55
    footer_h = 35
    svg_h = header_h + len(top_models) * row_h + footer_h
    c_ml, c_mr = 190, 70
    bar_max_w = 800 - c_ml - c_mr
    max_val = 100.0

    catalog_svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 {svg_h}" width="100%" height="100%">',
        f"<style>{css_styles}</style>",
        f'<rect width="800" height="{svg_h}" rx="8" fill="var(--bg)"/>',
        '<text x="30" y="28" font-size="15" font-weight="bold" fill="var(--text)">Ranked Model Catalog: Benchmark Scores &amp; Quota Burn</text>',
        '<rect x="480" y="18" width="10" height="10" rx="2" fill="var(--c-blue)"/>',
        '<text x="495" y="27" font-size="11" fill="var(--text-muted)">AA IQ</text>',
        '<rect x="560" y="18" width="10" height="10" rx="2" fill="var(--c-green)"/>',
        '<text x="575" y="27" font-size="11" fill="var(--text-muted)">Engineering</text>',
        '<rect x="665" y="18" width="10" height="10" rx="2" fill="var(--c-amber)"/>',
        '<text x="680" y="27" font-size="11" fill="var(--text-muted)">Quota Burn %</text>',
    ]

    for i, m in enumerate(top_models):
        y_base = header_h + i * row_h
        raw_id = m.get("full_id") or m.get("id") or m.get("model_id") or "unknown"
        short_id = raw_id.removeprefix("opencode-go/")
        rank_val = m.get("rank")
        rank_s = f"#{rank_val}" if rank_val is not None else f"#{i + 1}"

        catalog_svg.append(
            f'<text x="{c_ml - 12}" y="{y_base + 22}" text-anchor="end" font-size="12" font-weight="500" fill="var(--text)">'
            f"{rank_s} {html.escape(short_id)}</text>"
        )

        iq = float(m.get("aa_iq") or 0.0)
        coding = float(m.get("aa_coding") or 0.0)
        burn_val = m.get("composite_burn_pct")
        if burn_val is None:
            burn_val = m.get("quota_burn_pct")
        burn = float(burn_val) if isinstance(burn_val, (int, float)) else 0.0

        w_iq = max(0.0, min(bar_max_w, (iq / max_val) * bar_max_w))
        w_cod = max(0.0, min(bar_max_w, (coding / max_val) * bar_max_w))
        w_burn = max(0.0, min(bar_max_w, (min(burn, 100.0) / max_val) * bar_max_w))

        # Bars
        catalog_svg.append(
            f'<rect x="{c_ml}" y="{y_base + 6}" width="{w_iq:.1f}" height="7" rx="3" fill="var(--c-blue)"/>'
        )
        catalog_svg.append(
            f'<text x="{c_ml + w_iq + 6:.1f}" y="{y_base + 13}" font-size="9" fill="var(--text-muted)">{iq:.1f}</text>'
        )

        catalog_svg.append(
            f'<rect x="{c_ml}" y="{y_base + 16}" width="{w_cod:.1f}" height="7" rx="3" fill="var(--c-green)"/>'
        )
        catalog_svg.append(
            f'<text x="{c_ml + w_cod + 6:.1f}" y="{y_base + 23}" font-size="9" fill="var(--text-muted)">{coding:.1f}</text>'
        )

        catalog_svg.append(
            f'<rect x="{c_ml}" y="{y_base + 26}" width="{w_burn:.1f}" height="7" rx="3" fill="var(--c-amber)"/>'
        )
        catalog_svg.append(
            f'<text x="{c_ml + w_burn + 6:.1f}" y="{y_base + 33}" font-size="9" fill="var(--text-muted)">{burn:.1f}%</text>'
        )

    catalog_svg.append("</svg>")
    (charts_dir / "catalog-ranking.svg").write_text("\n".join(catalog_svg), encoding="utf-8")
    generated.append("catalog-ranking.svg")

    # 3. multi-plan-comparison.svg
    if plan_payloads:
        plans = list(plan_payloads.keys())
        paid_tiers = ["heavy", "balanced", "light"]
        p_ml, p_mr, p_mt, p_mb = 60, 30, 60, 55
        p_pw = 800 - p_ml - p_mr
        p_ph = 360 - p_mt - p_mb

        all_vals: list[float] = [100.0]
        for p_name in plans:
            explanations = plan_payloads[p_name].get("explanations") or {}
            for t in paid_tiers:
                ex = explanations.get(t) or {}
                allow = ex.get("monthly_allowance")
                if isinstance(allow, (int, float)):
                    all_vals.append(float(allow))
                burn = ex.get("quota_burn_pct") or ex.get("composite_burn_pct")
                if isinstance(burn, (int, float)):
                    all_vals.append(float(burn))
        p_max_val = max(all_vals) * 1.1

        multi_svg = [
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 800 360" width="100%" height="100%">',
            f"<style>{css_styles}</style>",
            '<rect width="800" height="360" rx="8" fill="var(--bg)"/>',
            '<text x="400" y="28" text-anchor="middle" font-size="15" font-weight="bold" fill="var(--text)">Multi-Plan Comparison: Monthly Allowance ($) vs Quota Burn (%)</text>',
            '<rect x="280" y="40" width="10" height="10" rx="2" fill="var(--c-blue)"/>',
            '<text x="295" y="49" font-size="11" fill="var(--text-muted)">Allowance ($)</text>',
            '<rect x="420" y="40" width="10" height="10" rx="2" fill="var(--c-amber)"/>',
            '<text x="435" y="49" font-size="11" fill="var(--text-muted)">Quota Burn %</text>',
        ]

        tier_w = p_pw / len(paid_tiers)
        for t_idx, tier in enumerate(paid_tiers):
            tier_x = p_ml + t_idx * tier_w
            multi_svg.append(
                f'<text x="{tier_x + tier_w / 2:.1f}" y="345" text-anchor="middle" font-size="13" font-weight="600" fill="var(--text)">{tier}</text>'
            )

            num_p = max(1, len(plans))
            plan_slot_w = (tier_w * 0.88) / num_p
            for p_idx, p_name in enumerate(plans):
                ex = (plan_payloads[p_name].get("explanations") or {}).get(tier) or {}
                allow_val = ex.get("monthly_allowance")
                allow_f = float(allow_val) if isinstance(allow_val, (int, float)) else 0.0
                burn_val = ex.get("quota_burn_pct") or ex.get("composite_burn_pct")
                burn_f = float(burn_val) if isinstance(burn_val, (int, float)) else 0.0

                slot_x = tier_x + (tier_w * 0.06) + p_idx * plan_slot_w
                bw = (plan_slot_w * 0.8) / 2

                h_allow = max(0.0, (allow_f / p_max_val) * p_ph)
                h_burn = max(0.0, (min(burn_f, p_max_val) / p_max_val) * p_ph)

                multi_svg.append(
                    f'<rect x="{slot_x:.1f}" y="{p_mt + p_ph - h_allow:.1f}" width="{bw:.1f}" height="{h_allow:.1f}" rx="2" fill="var(--c-blue)"/>'
                )
                multi_svg.append(
                    f'<rect x="{slot_x + bw + 2:.1f}" y="{p_mt + p_ph - h_burn:.1f}" width="{bw:.1f}" height="{h_burn:.1f}" rx="2" fill="var(--c-amber)"/>'
                )
                multi_svg.append(
                    f'<text x="{slot_x + bw:.1f}" y="{p_mt + p_ph + 14}" text-anchor="middle" font-size="9" fill="var(--text-muted)">{html.escape(p_name)}</text>'
                )

        multi_svg.append("</svg>")
        (charts_dir / "multi-plan-comparison.svg").write_text(
            "\n".join(multi_svg), encoding="utf-8"
        )
        generated.append("multi-plan-comparison.svg")

    return generated


def render_analysis_markdown(
    select_payload: dict[str, Any],
    rank_models: list[dict[str, Any]],
    plan_payloads: dict[str, dict[str, Any]] | None = None,
    free_matrix: list[dict[str, Any]] | None = None,
    run_url: str | None = None,
    charts_dir: str | Path | None = None,
) -> str:
    """Render comprehensive live Pareto analysis and catalog markdown (analysis.md)."""
    plan = select_payload.get("plan", "go")
    mode = select_payload.get("mode", "pareto")
    evaluated_at = select_payload.get("evaluated_at", "-")
    snapshot = select_payload.get("snapshot") or {}
    snap_source = snapshot.get("source", "-")
    fetched_at = snapshot.get("fetched_at", "-")
    benchmark_source = snapshot.get("benchmark_source", "none")

    lines: list[str] = [
        "## OpenCode Go Model Optimizer — Live Pareto Analysis & Catalog",
        "",
        f"- **Updated**: `{evaluated_at}`",
        f"- **Plan**: `{plan}` | **Mode**: `{mode}` | **Snapshot**: `{snap_source}` (`{fetched_at}`) | **Benchmarks**: `{benchmark_source}`",
    ]
    if run_url:
        lines.append(f"- **Workflow Run**: {run_url}")

    lines.extend(_format_settings_lines(select_payload, mode))

    lines.extend(["", "---", "", "### 1. Live Pareto Frontier & Quality/Value Bands", ""])

    # Filter Pareto models from rank_models
    pareto_models: list[dict[str, Any]] = []
    has_aa = False
    for m in rank_models:
        iq = m.get("aa_iq")
        coding = m.get("aa_coding")
        if iq is not None or coding is not None:
            has_aa = True
        if iq is not None and coding is not None:
            quality = 0.7 * float(iq) + 0.3 * float(coding)
        elif iq is not None:
            quality = float(iq)
        elif coding is not None:
            quality = float(coding)
        else:
            quality = 0.0

        burn = m.get("composite_burn_pct")
        if burn is None:
            burn = m.get("quota_burn_pct")
        computed_value = (
            (quality / float(burn)) if isinstance(burn, (int, float)) and burn > 0 else 0.0
        )

        if m.get("is_pareto"):
            paretop = dict(m)
            paretop["_quality"] = quality
            paretop["_value"] = computed_value
            pareto_models.append(paretop)

    pareto_models.sort(key=lambda x: x["_quality"], reverse=True)

    top_quality = pareto_models[0]["_quality"] if pareto_models else 0.0
    heavy_cutoff = 0.9 * top_quality
    balanced_cutoff = 0.6 * top_quality

    generated_svgs: list[str] = []
    if charts_dir:
        c_path = Path(charts_dir)
        generated_svgs = generate_analysis_svgs(
            charts_dir=c_path,
            pareto_models=pareto_models,
            rank_models=rank_models,
            plan_payloads=plan_payloads,
            top_quality=top_quality,
            heavy_cutoff=heavy_cutoff,
            balanced_cutoff=balanced_cutoff,
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
        bands_str = ", ".join(bands)
        model_id = m.get("full_id") or m.get("id") or m.get("model_id") or "unknown"
        iq_s = _fmt_float(m.get("aa_iq"), 1)
        eng_s = _fmt_float(m.get("aa_coding"), 1)
        burn_pct = m.get("composite_burn_pct")
        if burn_pct is None:
            burn_pct = m.get("quota_burn_pct")
        burn_s = f"{burn_pct:.1f}%" if isinstance(burn_pct, (int, float)) else "-"
        allowance_s = _fmt_allowance(m.get("monthly_allowance"))
        val_s = _fmt_float(m.get("_value"), 2)

        lines.append(
            f"| `{model_id}` | {iq_s} | {eng_s} | `{q:.1f}` | {burn_s} | {allowance_s} | {val_s} | {bands_str} |"
        )

    if pareto_models:
        slice_pareto = pareto_models[:12]
        x_ids = [
            (m.get("full_id") or m.get("id") or m.get("model_id") or "unknown")
            .removeprefix("opencode-go/")
            .replace('"', "")
            for m in slice_pareto
        ]
        q_vals = [round(float(m["_quality"]), 1) for m in slice_pareto]
        b_vals = []
        for m in slice_pareto:
            b = m.get("composite_burn_pct")
            if b is None:
                b = m.get("quota_burn_pct") or 0.0
            b_vals.append(round(float(b), 1))

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

        benchmarked_models: list[tuple[str, float, float]] = []
        for m in rank_models:
            if m.get("is_zero_cost"):
                continue
            iq = m.get("aa_iq")
            coding = m.get("aa_coding")
            if iq is None and coding is None:
                continue
            if iq is not None and coding is not None:
                q = 0.7 * float(iq) + 0.3 * float(coding)
            elif iq is not None:
                q = float(iq)
            else:
                q = float(coding)

            b = m.get("composite_burn_pct")
            if b is None:
                b = m.get("quota_burn_pct")
            if not isinstance(b, (int, float)):
                continue

            raw_id = m.get("full_id") or m.get("id") or m.get("model_id") or "model"
            clean_id = raw_id.removeprefix("opencode-go/")
            safe_label = re.sub(r"[^a-zA-Z0-9-]", "-", clean_id).strip("-")
            if not safe_label:
                safe_label = f"m-{len(benchmarked_models) + 1}"
            benchmarked_models.append((safe_label, float(b), q))

        if not benchmarked_models:
            for m in rank_models:
                iq = m.get("aa_iq")
                coding = m.get("aa_coding")
                if iq is None and coding is None:
                    continue
                q = 0.7 * float(iq or 0) + 0.3 * float(coding or 0)
                b = float(
                    m.get("composite_burn_pct")
                    if m.get("composite_burn_pct") is not None
                    else (m.get("quota_burn_pct") or 0.0)
                )
                raw_id = m.get("full_id") or m.get("id") or m.get("model_id") or "model"
                clean_id = raw_id.removeprefix("opencode-go/")
                safe_label = (
                    re.sub(r"[^a-zA-Z0-9-]", "-", clean_id).strip("-")
                    or f"m-{len(benchmarked_models) + 1}"
                )
                benchmarked_models.append((safe_label, b, q))

        seen_labels: dict[str, int] = {}
        deduped_models: list[tuple[str, float, float]] = []
        for lbl, b, q in benchmarked_models[:12]:
            if lbl in seen_labels:
                seen_labels[lbl] += 1
                lbl = f"{lbl}-{seen_labels[lbl]}"
            else:
                seen_labels[lbl] = 1
            deduped_models.append((lbl, b, q))

        if deduped_models:
            max_burn = max(b for _, b, _ in deduped_models)
            max_quality = max(q for _, _, q in deduped_models)
            if max_burn <= 0:
                max_burn = 100.0
            if max_quality <= 0:
                max_quality = 100.0

            quad_lines = [
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
                quad_lines.append(f"    {lbl}: [{clamped_x:.2f}, {clamped_y:.2f}]")
            quad_lines.append("```")
            lines.extend(quad_lines)

    if "pareto-frontier.svg" in generated_svgs:
        lines.extend(["", "![Live Pareto Frontier Curve](charts/pareto-frontier.svg)"])

    # 3. Live Free-Tier Picks Across Privacy/Region Flags
    if free_matrix:
        lines.extend(
            [
                "",
                "---",
                "",
                "### 2. Live Free-Tier Picks Across Privacy/Region Flags",
                "",
                "| Setting | CLI Flags | Selected Free Model |",
                "| --- | --- | --- |",
            ]
        )
        for row in free_matrix:
            label = row.get("label", "Default")
            flags = row.get("flags", "")
            model_id = row.get("model_id", "None")
            lines.append(f"| {label} | `{flags}` | `{model_id}` |")

        flow_lines = [
            "",
            "```mermaid",
            "flowchart LR",
        ]
        for i, row in enumerate(free_matrix):
            lbl = (row.get("label") or f"Setting {i + 1}").replace('"', "'")
            mid = (row.get("model_id") or "None").removeprefix("opencode-go/").replace('"', "'")
            s_node = f"S{i}"
            m_node = f"M_{re.sub(r'[^a-zA-Z0-9_]', '_', mid)}"
            flow_lines.append(f'    {s_node}["{lbl}"] --> {m_node}["{mid}"]')
        flow_lines.append("```")
        lines.extend(flow_lines)

    # 4. Multi-Plan Tier Selections & Top-3 Fallbacks
    if plan_payloads:
        lines.extend(
            [
                "",
                "---",
                "",
                "### 3. Multi-Plan Tier Selections & Top-3 Fallbacks",
                "",
                "| Plan | Tier | Pick | Fallbacks | Allowance | Output Cost | Burn % |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
        )
        for p_name, p_data in plan_payloads.items():
            tiers_dict = p_data.get("tiers") or {}
            explanations = p_data.get("explanations") or {}
            fallbacks_map = p_data.get("fallbacks") or {}
            for tier in TIER_ORDER:
                if (
                    tier not in tiers_dict
                    and tier not in explanations
                    and tier not in fallbacks_map
                ):
                    continue
                ex = explanations.get(tier) or {}
                pick = ex.get("model_id") or tiers_dict.get(tier) or "None"

                # fallbacks can be list of strings (from format_select_json) or list of dicts (from explanations)
                raw_fallbacks = fallbacks_map.get(tier) or ex.get("fallbacks") or []
                fallback_ids: list[str] = []
                for f in raw_fallbacks:
                    if isinstance(f, str):
                        fallback_ids.append(f)
                    elif isinstance(f, dict) and f.get("model_id"):
                        fallback_ids.append(str(f["model_id"]))
                fallback_str = (
                    ", ".join([f"`{fid}`" for fid in fallback_ids]) if fallback_ids else "-"
                )

                # Allowance / output cost / burn can be in ex or resolved from top pick / metadata
                allowance_val = ex.get("monthly_allowance")
                output_val = ex.get("output_cost_per_1m")
                burn = ex.get("quota_burn_pct") or ex.get("composite_burn_pct")

                allowance_s = _fmt_allowance(allowance_val)
                output_s = _fmt_output_cost(output_val)
                burn_s = f"{burn:.1f}%" if isinstance(burn, (int, float)) else "-"
                lines.append(
                    f"| `{p_name}` | **{tier}** | `{pick}` | {fallback_str} | {allowance_s} | {output_s} | {burn_s} |"
                )

        flow_lines = [
            "",
            "```mermaid",
            "flowchart LR",
        ]
        for p_name, p_data in plan_payloads.items():
            tiers_dict = p_data.get("tiers") or {}
            explanations = p_data.get("explanations") or {}
            fallbacks_map = p_data.get("fallbacks") or {}
            p_node = f"Plan_{p_name.replace('-', '_')}"
            flow_lines.append(f'    {p_node}["{p_name}"]')
            for tier in TIER_ORDER:
                if (
                    tier not in tiers_dict
                    and tier not in explanations
                    and tier not in fallbacks_map
                ):
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
                flow_lines.append(f'    {p_node} --> {t_node}["{tier_label}"]')
        flow_lines.append("```")
        lines.extend(flow_lines)

        burn_items: list[tuple[str, float]] = []
        for p_name, p_data in plan_payloads.items():
            explanations = p_data.get("explanations") or {}
            for tier in TIER_ORDER:
                ex = explanations.get(tier) or {}
                burn = ex.get("quota_burn_pct")
                if burn is None:
                    burn = ex.get("composite_burn_pct")
                if isinstance(burn, (int, float)):
                    burn_items.append((f"{p_name}:{tier}", round(float(burn), 1)))

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

    # 5. Full Ranked Catalog
    lines.extend(
        [
            "",
            "---",
            "",
            "### 4. Full Ranked Catalog (`rank --mode pareto`)",
            "",
            *_RANKED_CATALOG_HEADERS,
        ]
    )
    for m in rank_models:
        lines.append(_ranked_catalog_row(m))
    lines.extend(_catalog_composition_pie_lines(rank_models))

    if "catalog-ranking.svg" in generated_svgs:
        lines.extend(["", "![Catalog Ranking Breakdown](charts/catalog-ranking.svg)"])

    if has_aa:
        lines.extend(["", "---", "*Source: Artificial Analysis (artificialanalysis.ai)*"])

    return "\n".join(lines) + "\n"


def _ci_status_class(outcome: str) -> str:
    low = outcome.strip().lower()
    if low == "success":
        return "success"
    if low in ("failure", "failed", "error"):
        return "failure"
    return "other"


def render_ci_comment_markdown(
    ci_dir: str | Path,
    run_url: str | None = None,
    sha: str | None = None,
    outcomes: dict[str, str] | None = None,
) -> str:
    """Render CI comment markdown using out/ci/ log files and outcomes."""
    ci_path = Path(ci_dir)
    outcomes = outcomes or {}
    format_outcome = outcomes.get("format") or "unknown"
    lint_outcome = outcomes.get("lint") or "unknown"
    pytest_outcome = outcomes.get("pytest") or "unknown"
    build_outcome = outcomes.get("build") or "unknown"

    marker = (
        f"<!-- ci-job-results-comment:commit={sha} -->"
        if sha
        else "<!-- ci-job-results-comment -->"
    )
    lines = [
        marker,
        "## CI Job Results",
        "",
    ]
    if run_url:
        lines.append(f"- **Workflow Run**: {run_url}")
    if sha:
        lines.append(f"- **Commit**: `{sha}`")
    if run_url or sha:
        lines.append("")

    lines.extend(
        [
            "| Step | Status |",
            "| --- | --- |",
            f"| Check code formatting (`ruff format --check`) | `{format_outcome}` |",
            f"| Lint with ruff (`ruff check`) | `{lint_outcome}` |",
            f"| Run test suite with coverage (`pytest`) | `{pytest_outcome}` |",
            f"| Verify package build (`uv build`) | `{build_outcome}` |",
            "",
            "```mermaid",
            "flowchart LR",
            f'    Format["ruff format: {format_outcome}"] --> Lint["ruff check: {lint_outcome}"] --> Pytest["pytest: {pytest_outcome}"] --> Build["uv build: {build_outcome}"]',
            "    classDef success fill:#d1fae5,stroke:#059669,stroke-width:2px,color:#065f46;",
            "    classDef failure fill:#fee2e2,stroke:#dc2626,stroke-width:2px,color:#991b1b;",
            "    classDef other fill:#f3f4f6,stroke:#9ca3af,stroke-width:2px,color:#374151;",
            f"    class Format {_ci_status_class(format_outcome)};",
            f"    class Lint {_ci_status_class(lint_outcome)};",
            f"    class Pytest {_ci_status_class(pytest_outcome)};",
            f"    class Build {_ci_status_class(build_outcome)};",
            "```",
            "",
        ]
    )

    steps_map = [
        ("format", "ruff format --check", format_outcome),
        ("lint", "ruff check", lint_outcome),
        ("pytest", "pytest --cov", pytest_outcome),
        ("build", "uv build", build_outcome),
    ]
    for key, label, outcome in steps_map:
        log_file = ci_path / f"{key}.txt"
        if log_file.is_file():
            text = log_file.read_text(encoding="utf-8", errors="replace")
            if text.strip():
                fence = _fence_for(text)
                details_tag = "<details open>" if outcome != "success" else "<details>"
                lines.extend(
                    [
                        details_tag,
                        f"<summary><code>{label}</code> output</summary>",
                        "",
                        f"{fence}text",
                        text.strip(),
                        fence,
                        "",
                        "</details>",
                        "",
                    ]
                )

    return "\n".join(lines).strip() + "\n"


def render_optimizer_output_comment(
    output: str | Path,
    run_url: str | None = None,
    sha: str | None = None,
    command: str | None = None,
    benchmarks: str | None = None,
    status: str | int | None = None,
) -> str:
    """Render a PR comment showing the optimizer's tier selections as a table.

    ``output`` is the optimizer's stdout with its stderr appended after a
    ``--- stderr ---`` separator (assembled by the workflow). ASCII
    tier-selection tables are parsed out and re-rendered as GitHub markdown
    tables so they render natively on the PR; any other stdout lines are kept
    as fenced text and stderr is folded into a collapsible section. ``benchmarks``
    is ``"live"`` when an Artificial Analysis key was available to the run and
    ``"unavailable"`` when repository secrets were absent (fork PR), where
    pareto selection falls back to allowance ranking.
    """
    text = Path(output).read_text(encoding="utf-8", errors="replace")
    marker = (
        f"<!-- optimizer-output-comment:commit={sha} -->"
        if sha
        else "<!-- optimizer-output-comment -->"
    )
    lines = [marker, "## Optimizer Tier Selections", ""]
    if run_url:
        lines.append(f"- **Workflow Run**: {run_url}")
    if sha:
        lines.append(f"- **Commit**: `{sha}`")
    if command:
        lines.append(f"- **Command**: `{command}`")
    if status is not None and str(status) != "0":
        lines.append(f"- **Exit status**: `{status}` (see output below)")
    if benchmarks == "live":
        lines.append("- **Benchmarks**: Artificial Analysis live fetch (key loaded from 1Password)")
    elif benchmarks == "unavailable":
        lines.append(
            "- **Benchmarks**: unavailable on this run (1Password secrets absent); "
            "pareto selection falls back to allowance ranking"
        )
    if len(lines) > 3:
        lines.append("")

    stdout_text, stderr_text = _split_stderr_section(text)
    blocks = _parse_ascii_output_blocks(stdout_text)
    tables = [b for b in blocks if b["kind"] == "table"]
    stray_groups = [b for b in blocks if b["kind"] == "lines"]

    for i, block in enumerate(tables):
        if i:
            lines.append("")
        lines.extend(_render_markdown_table(block["headers"], block["rows"]))

    if stray_groups:
        if tables:
            lines.append("")
        stray_text = "\n\n".join("\n".join(g["lines"]) for g in stray_groups)
        fence = _fence_for(stray_text)
        lines.extend(
            [
                "<details>",
                "<summary>Additional optimizer output</summary>",
                "",
                f"{fence}text",
                stray_text,
                fence,
                "",
                "</details>",
            ]
        )

    if not tables and not stray_groups:
        lines.append("The optimizer produced no output.")

    stderr_cleaned = stderr_text.strip()
    if stderr_cleaned:
        failed = status is not None and str(status) != "0"
        details_tag = "<details open>" if failed or not tables else "<details>"
        fence = _fence_for(stderr_cleaned)
        lines.extend(
            [
                "",
                details_tag,
                "<summary>stderr</summary>",
                "",
                f"{fence}text",
                stderr_cleaned,
                fence,
                "",
                "</details>",
            ]
        )

    if benchmarks == "live":
        lines.extend(["", "*Source: Artificial Analysis (artificialanalysis.ai)*"])
    return "\n".join(lines).strip() + "\n"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render GitHub-pretty markdown from optimizer JSON and script output, "
        "CI results, or the raw optimizer output."
    )
    parser.add_argument("--json", default=None, help="Path to select --format json --explain file")
    parser.add_argument(
        "--script-output",
        default=None,
        help="Optional path to full script output (stdout/stderr)",
    )
    parser.add_argument("--run-url", default=None, help="Optional GitHub Actions run URL")
    parser.add_argument(
        "--ci-dir", default=None, help="Optional path to CI logs directory (out/ci)"
    )
    parser.add_argument(
        "--optimizer-output",
        default=None,
        help="Path to the actual optimizer output (stdout plus stderr) to post as a PR comment",
    )
    parser.add_argument(
        "--benchmarks",
        default=None,
        choices=["live", "unavailable"],
        help="Benchmark availability shown in the optimizer output comment header",
    )
    parser.add_argument(
        "--command",
        default=None,
        help="Command line shown in the optimizer output comment header",
    )
    parser.add_argument(
        "--status",
        default=None,
        help="Optimizer exit status shown in the optimizer output comment header",
    )
    parser.add_argument("--sha", default=None, help="Optional commit SHA for CI report")
    parser.add_argument("--format-outcome", default="unknown", help="Format step outcome")
    parser.add_argument("--lint-outcome", default="unknown", help="Lint step outcome")
    parser.add_argument("--pytest-outcome", default="unknown", help="Pytest step outcome")
    parser.add_argument("--build-outcome", default="unknown", help="Build step outcome")
    parser.add_argument(
        "--rank-json", default=None, help="Optional path to rank --format json file"
    )
    parser.add_argument(
        "--plan-json",
        action="append",
        default=[],
        help="Repeatable PLAN=PATH for select --format json --explain payloads",
    )
    parser.add_argument(
        "--free-matrix-json", default=None, help="Optional path to free matrix JSON list file"
    )
    parser.add_argument(
        "--site-url",
        default=None,
        help="Optional published GitHub Pages site URL (full dataset location)",
    )
    parser.add_argument(
        "-o", "--output", default=None, help="Output markdown path (default: stdout)"
    )
    parser.add_argument(
        "--analysis-output", default=None, help="Optional path to write analysis.md"
    )
    parser.add_argument(
        "--charts-dir",
        default=None,
        help="Optional directory to output generated SVG analysis charts",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    markdown = ""
    if args.ci_dir:
        markdown = render_ci_comment_markdown(
            ci_dir=args.ci_dir,
            run_url=args.run_url,
            sha=args.sha,
            outcomes={
                "format": args.format_outcome,
                "lint": args.lint_outcome,
                "pytest": args.pytest_outcome,
                "build": args.build_outcome,
            },
        )
    elif args.optimizer_output:
        markdown = render_optimizer_output_comment(
            output=args.optimizer_output,
            run_url=args.run_url or None,
            sha=args.sha or None,
            command=args.command or None,
            benchmarks=args.benchmarks or None,
            status=args.status,
        )
    elif args.json:
        payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
        script_output = (
            Path(args.script_output).read_text(encoding="utf-8") if args.script_output else ""
        )
        markdown = render_issue_markdown(
            payload,
            script_output=script_output,
            run_url=args.run_url or None,
            rank_models=_load_rank_models(args.rank_json),
            site_url=args.site_url or None,
        )
    else:
        sys.stderr.write(
            "Error: one of --json, --ci-dir or --optimizer-output must be specified.\n"
        )
        return 1

    if args.analysis_output and args.json:
        select_payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
        rank_models: list[dict[str, Any]] = _load_rank_models(args.rank_json)

        plan_payloads: dict[str, dict[str, Any]] = {}
        for item in args.plan_json:
            if "=" in item:
                p_name, p_path = item.split("=", 1)
                if Path(p_path).is_file():
                    plan_payloads[p_name] = json.loads(Path(p_path).read_text(encoding="utf-8"))

        free_matrix: list[dict[str, Any]] | None = None
        if args.free_matrix_json and Path(args.free_matrix_json).is_file():
            free_matrix = json.loads(Path(args.free_matrix_json).read_text(encoding="utf-8"))

        analysis_md = render_analysis_markdown(
            select_payload=select_payload,
            rank_models=rank_models,
            plan_payloads=plan_payloads if plan_payloads else None,
            free_matrix=free_matrix,
            run_url=args.run_url or None,
            charts_dir=args.charts_dir,
        )
        out_analysis = Path(args.analysis_output)
        out_analysis.parent.mkdir(parents=True, exist_ok=True)
        out_analysis.write_text(analysis_md, encoding="utf-8")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
