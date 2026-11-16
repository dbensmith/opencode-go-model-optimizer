#!/usr/bin/env python3
"""Render GitHub-pretty markdown from opencode-go-model-optimizer JSON and script output."""

from __future__ import annotations

import argparse
import json
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


def render_issue_markdown(
    payload: dict[str, Any],
    script_output: str = "",
    run_url: str | None = None,
) -> str:
    """Render optimizer JSON payload and optional full script output as GitHub Markdown."""
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


def render_analysis_markdown(
    select_payload: dict[str, Any],
    rank_models: list[dict[str, Any]],
    plan_payloads: dict[str, dict[str, Any]] | None = None,
    free_matrix: list[dict[str, Any]] | None = None,
    run_url: str | None = None,
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

    # 5. Full Ranked Catalog
    lines.extend(
        [
            "",
            "---",
            "",
            "### 4. Full Ranked Catalog (`rank --mode pareto`)",
            "",
            "| Rank | Model ID | Tier | BPE | AA IQ | Engineering | Burn % | Allowance | Output Cost | Context | Release | Privacy |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
    )

    for m in rank_models:
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
        lines.append(
            f"| {rank_s} | `{model_id}` | {tier} | {bpe_s} | {iq_s} | {eng_s} | {burn_s} | {allowance_s} | {output_s} | {ctx_s} | {release} | {priv} |"
        )

    if has_aa:
        lines.extend(["", "---", "*Source: Artificial Analysis (artificialanalysis.ai)*"])

    return "\n".join(lines) + "\n"


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


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render GitHub-pretty markdown from optimizer JSON and script output or CI results."
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
        "-o", "--output", default=None, help="Output markdown path (default: stdout)"
    )
    parser.add_argument(
        "--analysis-output", default=None, help="Optional path to write analysis.md"
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
    elif args.json:
        payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
        script_output = (
            Path(args.script_output).read_text(encoding="utf-8") if args.script_output else ""
        )
        markdown = render_issue_markdown(
            payload,
            script_output=script_output,
            run_url=args.run_url or None,
        )
    else:
        sys.stderr.write("Error: either --json or --ci-dir must be specified.\n")
        return 1

    if args.analysis_output and args.json:
        select_payload = json.loads(Path(args.json).read_text(encoding="utf-8"))
        rank_models: list[dict[str, Any]] = []
        if args.rank_json:
            rank_data = json.loads(Path(args.rank_json).read_text(encoding="utf-8"))
            if isinstance(rank_data, list):
                rank_models = rank_data
            elif isinstance(rank_data, dict):
                rank_models = rank_data.get("models") or rank_data.get("rankings") or []

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
