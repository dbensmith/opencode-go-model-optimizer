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
    render_issue_markdown,
    render_optimizer_output_comment,
)
from optimizer_issue.common import _load_rank_models  # noqa: E402

__all__ = [
    "generate_analysis_svgs",
    "main",
    "parse_args",
    "render_analysis_markdown",
    "render_ci_comment_markdown",
    "render_data_markdown",
    "render_issue_markdown",
    "render_optimizer_output_comment",
]

_OPTIONAL_FLAGS: tuple[tuple[str, str], ...] = (
    ("--json", "Path to select --format json --explain file"),
    ("--script-output", "Optional path to full script output (stdout/stderr)"),
    ("--run-url", "Optional GitHub Actions run URL"),
    ("--ci-dir", "Optional path to CI logs directory (out/ci)"),
    (
        "--optimizer-output",
        "Path to the actual optimizer output (stdout plus stderr) to post as a PR comment",
    ),
    ("--command", "Command line shown in the optimizer output comment header"),
    ("--status", "Optimizer exit status shown in the optimizer output comment header"),
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
        "CI results, or the raw optimizer output."
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
        choices=["live", "unavailable"],
        help="Benchmark availability shown in the optimizer output comment header",
    )
    parser.add_argument(
        "--plan-json",
        action="append",
        default=[],
        help="Repeatable PLAN=PATH for select --format json --explain payloads",
    )
    parser.add_argument(
        "-o", "--output", default=None, help="Output markdown path (default: stdout)"
    )
    return parser.parse_args(argv)


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

    if args.optimizer_output:
        md = render_optimizer_output_comment(
            output=args.optimizer_output,
            run_url=args.run_url or None,
            sha=args.sha or None,
            command=args.command or None,
            benchmarks=args.benchmarks or None,
            status=args.status,
        )
        return md, None, None

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
    if args.analysis_output:
        plan_payloads = {
            name: _load_json(path)
            for item in args.plan_json
            if "=" in item
            and Path(path := item.split("=", 1)[1]).is_file()
            and (name := item.split("=", 1)[0]) is not None
        }
        free_matrix = (
            _load_json(args.free_matrix_json)
            if args.free_matrix_json and Path(args.free_matrix_json).is_file()
            else None
        )
        analysis_md = render_analysis_markdown(
            select_payload=select_payload,
            rank_models=rank_models,
            plan_payloads=plan_payloads or None,
            free_matrix=free_matrix,
            run_url=args.run_url or None,
            charts_dir=args.charts_dir,
            site_url=args.site_url or None,
        )
        _write_text(args.analysis_output, analysis_md)

    if args.data_output:
        data_md = render_data_markdown(
            site_url=args.site_url or None,
            evaluated_at=select_payload.get("evaluated_at"),
            run_url=args.run_url or None,
        )
        _write_text(args.data_output, data_md)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    markdown, select_payload, rank_models = _render_primary_markdown(args)
    if markdown is None:
        sys.stderr.write(
            "Error: one of --json, --ci-dir or --optimizer-output must be specified.\n"
        )
        return 1

    if args.json and (args.analysis_output or args.data_output):
        if select_payload is None:
            select_payload = _load_json(args.json)
        if args.analysis_output and rank_models is None:
            rank_models = _load_rank_models(args.rank_json)
        _write_site_pages(args, select_payload, rank_models)

    if args.output:
        _write_text(args.output, markdown)
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
