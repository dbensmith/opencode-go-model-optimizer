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


def _load_json(path: str) -> Any:
    return json.loads(Path(path).read_text(encoding="utf-8"))


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
    parser.add_argument(
        "--data-output",
        default=None,
        help="Optional path to write the all-data index page (data.md)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    select_payload: dict[str, Any] | None = None
    rank_models: list[dict[str, Any]] | None = None

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
        select_payload = _load_json(args.json)
        rank_models = _load_rank_models(args.rank_json)
        script_output = (
            Path(args.script_output).read_text(encoding="utf-8") if args.script_output else ""
        )
        markdown = render_issue_markdown(
            select_payload,
            script_output=script_output,
            run_url=args.run_url or None,
            rank_models=rank_models,
            site_url=args.site_url or None,
        )
    else:
        sys.stderr.write(
            "Error: one of --json, --ci-dir or --optimizer-output must be specified.\n"
        )
        return 1

    if args.json and (args.analysis_output or args.data_output):
        if select_payload is None:
            select_payload = _load_json(args.json)

        if args.analysis_output:
            if rank_models is None:
                rank_models = _load_rank_models(args.rank_json)

            plan_payloads: dict[str, dict[str, Any]] = {}
            for item in args.plan_json:
                if "=" in item:
                    p_name, p_path = item.split("=", 1)
                    if Path(p_path).is_file():
                        plan_payloads[p_name] = _load_json(p_path)

            free_matrix: list[dict[str, Any]] | None = None
            if args.free_matrix_json and Path(args.free_matrix_json).is_file():
                free_matrix = _load_json(args.free_matrix_json)

            analysis_md = render_analysis_markdown(
                select_payload=select_payload,
                rank_models=rank_models,
                plan_payloads=plan_payloads or None,
                free_matrix=free_matrix,
                run_url=args.run_url or None,
                charts_dir=args.charts_dir,
                site_url=args.site_url or None,
            )
            out_analysis = Path(args.analysis_output)
            out_analysis.parent.mkdir(parents=True, exist_ok=True)
            out_analysis.write_text(analysis_md, encoding="utf-8")

        if args.data_output:
            data_md = render_data_markdown(
                site_url=args.site_url or None,
                evaluated_at=select_payload.get("evaluated_at"),
                run_url=args.run_url or None,
            )
            out_data = Path(args.data_output)
            out_data.parent.mkdir(parents=True, exist_ok=True)
            out_data.write_text(data_md, encoding="utf-8")

    if args.output:
        out_path = Path(args.output)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(markdown, encoding="utf-8")
    else:
        sys.stdout.write(markdown)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
