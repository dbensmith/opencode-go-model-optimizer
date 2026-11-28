#!/usr/bin/env python3
"""Render GitHub-pretty markdown from opencode-go-model-optimizer JSON and script output."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

# Ensure scripts/ is importable both on direct CLI runs and importlib file loads.
_SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from optimizer_issue import (  # noqa: E402
    generate_analysis_svgs,
    render_analysis_markdown,
    render_ci_comment_markdown,
    render_data_markdown,
    render_dual_issue_markdown,
    render_dual_optimizer_output_comment,
    render_issue_markdown,
)
from optimizer_issue.common import _load_rank_models  # noqa: E402

__all__ = [
    "generate_analysis_svgs",
    "main",
    "parse_args",
    "render_analysis_markdown",
    "render_ci_comment_markdown",
    "render_data_markdown",
    "render_dual_issue_markdown",
    "render_dual_optimizer_output_comment",
    "render_issue_markdown",
]

_OPTIONAL_FLAGS: tuple[tuple[str, str], ...] = (
    ("--json", "Path to select --format json --explain file"),
    ("--pareto-json", "Path to Pareto select --format json --explain file"),
    ("--allowance-json", "Path to Allowance select --format json --explain file"),
    ("--pareto-rank-json", "Path to Pareto rank --format json file"),
    ("--allowance-rank-json", "Path to Allowance rank --format json file"),
    ("--pareto-error", "Path to Pareto failure output file when Pareto failed"),
    ("--snapshot-json", "Optional path to snapshot.json for dual-method provenance"),
    ("--script-output", "Optional path to full script output (stdout/stderr)"),
    ("--run-url", "Optional GitHub Actions run URL"),
    ("--ci-dir", "Optional path to CI logs directory (out/ci)"),
    ("--pareto-stderr", "Path to Pareto stderr for the dual PR comment"),
    ("--allowance-stderr", "Path to Allowance stderr for the dual PR comment"),
    ("--pareto-status", "Pareto exit status for dual PR comment"),
    ("--allowance-status", "Allowance exit status for dual PR comment"),
    ("--fetch-log", "Optional path to fetch.log for dual PR comment"),
    ("--sha", "Optional commit SHA for CI report"),
    ("--rank-json", "Optional path to rank --format json file"),
    ("--free-matrix-json", "Optional path to free matrix JSON list file"),
    ("--site-url", "Optional published GitHub Pages site URL (full dataset location)"),
    ("--analysis-output", "Optional path to write analysis.md"),
    ("--charts-dir", "Optional directory to output generated SVG analysis charts"),
    ("--data-output", "Optional path to write the all-data index page (data.md)"),
)


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _write_text(path: str | Path, content: str) -> None:
    out_path = Path(path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(content, encoding="utf-8")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render GitHub-pretty markdown from optimizer JSON and script output, "
        "CI results, or the dual optimizer PR comment."
    )
    for flag, help_text in _OPTIONAL_FLAGS:
        parser.add_argument(flag, default=None, help=help_text)
    for step in ("format", "lint", "pytest", "build"):
        parser.add_argument(
            f"--{step}-outcome", default="unknown", help=f"{step.capitalize()} step outcome"
        )
    parser.add_argument(
        "--benchmarks",
        default=None,
        choices=["live", "cache", "unavailable"],
        help="Benchmark availability shown in the optimizer output comment header",
    )
    parser.add_argument(
        "--plan-json",
        action="append",
        default=[],
        help="Repeatable PLAN=PATH for select --format json --explain payloads",
    )
    parser.add_argument(
        "--pareto-plan-json",
        action="append",
        default=[],
        help="Repeatable PLAN=PATH for Pareto per-plan payloads",
    )
    parser.add_argument(
        "--allowance-plan-json",
        action="append",
        default=[],
        help="Repeatable PLAN=PATH for Allowance per-plan payloads",
    )
    parser.add_argument(
        "-o", "--output", default=None, help="Output markdown path (default: stdout)"
    )
    return parser.parse_args(argv)


def _load_plan_payloads(items: list[str]) -> dict[str, dict[str, Any]]:
    return {
        name: _load_json(path)
        for item in items
        if "=" in item
        and Path(path := item.split("=", 1)[1]).is_file()
        and (name := item.split("=", 1)[0]) is not None
    }


def _load_optional_json(path: str | None) -> Any:
    if path and (json_path := Path(path)).is_file() and json_path.stat().st_size:
        return _load_json(path)
    return None


def _render_primary_markdown(
    args: argparse.Namespace,
) -> tuple[str | None, dict[str, Any] | None, list[dict[str, Any]] | None]:
    if args.ci_dir:
        outcomes = {s: getattr(args, f"{s}_outcome") for s in ("format", "lint", "pytest", "build")}
        return (
            render_ci_comment_markdown(
                ci_dir=args.ci_dir, run_url=args.run_url, sha=args.sha, outcomes=outcomes
            ),
            None,
            None,
        )

    if (
        args.pareto_status is not None
        or args.allowance_status is not None
        or args.pareto_stderr
        or args.allowance_stderr
    ):
        md = render_dual_optimizer_output_comment(
            pareto_payload=_load_optional_json(args.pareto_json),
            allowance_payload=_load_optional_json(args.allowance_json),
            pareto_stderr=args.pareto_stderr,
            allowance_stderr=args.allowance_stderr,
            pareto_status=args.pareto_status,
            allowance_status=args.allowance_status,
            run_url=args.run_url or None,
            sha=args.sha or None,
            benchmarks=args.benchmarks or None,
            fetch_log=args.fetch_log or None,
        )
        return md, None, None

    if args.allowance_json:
        pareto_payload = _load_optional_json(args.pareto_json)
        allowance_payload = _load_json(args.allowance_json)
        pareto_rank = _load_rank_models(args.pareto_rank_json)
        allowance_rank = _load_rank_models(args.allowance_rank_json)
        snapshot_payload = _load_optional_json(args.snapshot_json)
        pareto_error = (
            Path(args.pareto_error).read_text(encoding="utf-8", errors="replace")
            if args.pareto_error and Path(args.pareto_error).is_file()
            else ""
        )
        script_output = (
            Path(args.script_output).read_text(encoding="utf-8", errors="replace")
            if args.script_output and Path(args.script_output).is_file()
            else ""
        )
        md = render_dual_issue_markdown(
            pareto_payload,
            allowance_payload,
            pareto_rank_models=pareto_rank,
            allowance_rank_models=allowance_rank,
            pareto_error=pareto_error,
            script_output=script_output,
            run_url=args.run_url or None,
            site_url=args.site_url or None,
            snapshot_payload=snapshot_payload,
        )
        return md, pareto_payload or allowance_payload, pareto_rank

    if args.json:
        select_payload = _load_json(args.json)
        rank_models = _load_rank_models(args.rank_json)
        script_output = (
            Path(args.script_output).read_text(encoding="utf-8") if args.script_output else ""
        )
        md = render_issue_markdown(
            select_payload,
            script_output=script_output,
            run_url=args.run_url or None,
            rank_models=rank_models,
            site_url=args.site_url or None,
        )
        return md, select_payload, rank_models

    return None, None, None


def _write_site_pages(
    args: argparse.Namespace,
    select_payload: dict[str, Any],
    rank_models: list[dict[str, Any]] | None,
) -> None:
    dual_mode = bool(args.allowance_json)
    pareto_ok = dual_mode and bool(args.pareto_json and Path(args.pareto_json).is_file())
    if args.analysis_output:
        if dual_mode:
            plan_payloads = _load_plan_payloads(args.pareto_plan_json)
            allowance_plan_payloads = _load_plan_payloads(args.allowance_plan_json)
            allowance_select = _load_json(args.allowance_json)
            allowance_rank = _load_rank_models(args.allowance_rank_json)
            pareto_error = (
                Path(args.pareto_error).read_text(encoding="utf-8", errors="replace")
                if args.pareto_error and Path(args.pareto_error).is_file()
                else ""
            )
        else:
            plan_payloads = _load_plan_payloads(args.plan_json)
            allowance_plan_payloads = {}
            allowance_select = None
            allowance_rank = None
            pareto_error = ""
        free_matrix = _load_optional_json(args.free_matrix_json)
        analysis_md = render_analysis_markdown(
            select_payload=select_payload,
            rank_models=rank_models or [],
            plan_payloads=plan_payloads or None,
            free_matrix=free_matrix,
            run_url=args.run_url or None,
            charts_dir=args.charts_dir,
            site_url=args.site_url or None,
            pareto_error=pareto_error,
            allowance_select_payload=allowance_select,
            allowance_rank_models=allowance_rank,
            allowance_plan_payloads=allowance_plan_payloads or None,
        )
        _write_text(args.analysis_output, analysis_md)

    if args.data_output:
        data_md = render_data_markdown(
            site_url=args.site_url or None,
            evaluated_at=select_payload.get("evaluated_at"),
            run_url=args.run_url or None,
            dual_mode=dual_mode,
            pareto_ok=pareto_ok,
        )
        _write_text(args.data_output, data_md)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    markdown, select_payload, rank_models = _render_primary_markdown(args)
    if markdown is None:
        sys.stderr.write(
            "Error: one of --json, --allowance-json, --ci-dir, or dual PR comment "
            "status/stderr flags with JSON inputs must be specified.\n"
        )
        return 1

    if (args.json or args.allowance_json) and (args.analysis_output or args.data_output):
        if select_payload is None:
            select_payload = _load_json(args.json or args.allowance_json)
        if args.analysis_output and rank_models is None and args.rank_json:
            rank_models = _load_rank_models(args.rank_json)
        _write_site_pages(args, select_payload, rank_models)

    if args.output:
        _write_text(args.output, markdown)
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
