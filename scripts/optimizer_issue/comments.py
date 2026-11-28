"""PR comment renderers for CI job results and optimizer output."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from opencode_go_model_optimizer.formatters.markdown import (
    render_details_block,
    render_markdown_table,
)

from .common import _render_select_json_table


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
            *render_markdown_table(
                ["Step", "Status"],
                [
                    ["Check code formatting (`ruff format --check`)", f"`{format_outcome}`"],
                    ["Lint with ruff (`ruff check`)", f"`{lint_outcome}`"],
                    ["Run test suite with coverage (`pytest`)", f"`{pytest_outcome}`"],
                    ["Verify package build (`uv build`)", f"`{build_outcome}`"],
                ],
            ),
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
            details = render_details_block(
                f"<code>{label}</code> output",
                text,
                open_by_default=(outcome != "success"),
            )
            if details:
                lines.extend([*details, ""])

    return "\n".join(lines).strip() + "\n"


def _read_optional_text(path: str | Path | None) -> str:
    if not path or not (file_path := Path(path)).is_file():
        return ""
    return file_path.read_text(encoding="utf-8", errors="replace")


def _render_dual_method_body(
    payload: dict[str, Any] | None,
    stderr_text: str,
    *,
    failed: bool,
    empty_message: str,
    stderr_summary: str,
    include_aa: bool,
) -> list[str]:
    lines: list[str] = []
    if payload is None:
        lines.append(empty_message)
    else:
        method = payload.get("method") or payload.get("mode") or "unknown"
        table, _ = _render_select_json_table(payload, include_aa=include_aa)
        lines.extend([f"- **Calculation method**: `{method}`", "", *table])

    if stderr_text.strip():
        lines.extend(
            [
                "",
                *render_details_block(
                    stderr_summary,
                    stderr_text,
                    open_by_default=(failed or payload is None),
                ),
            ]
        )
    return lines


def render_dual_optimizer_output_comment(
    *,
    pareto_payload: dict[str, Any] | None = None,
    allowance_payload: dict[str, Any] | None = None,
    pareto_stderr: str | Path | None = None,
    allowance_stderr: str | Path | None = None,
    pareto_status: str | int | None = None,
    allowance_status: str | int | None = None,
    run_url: str | None = None,
    sha: str | None = None,
    benchmarks: str | None = None,
    fetch_log: str | Path | None = None,
) -> str:
    """Render a PR comment showing both Pareto and Allowance outputs without fallback."""
    marker = (
        f"<!-- optimizer-output-comment:commit={sha} -->"
        if sha
        else "<!-- optimizer-output-comment -->"
    )
    pareto_ok = str(pareto_status) == "0"
    allowance_ok = str(allowance_status) == "0"
    secrets_missing = benchmarks == "unavailable"

    lines = [marker, "## Optimizer Tier Selections", ""]
    if run_url:
        lines.append(f"- **Workflow Run**: {run_url}")
    if sha:
        lines.append(f"- **Commit**: `{sha}`")
    if benchmarks == "live":
        lines.append("- **Benchmarks**: Artificial Analysis live fetch (key loaded from 1Password)")
    elif benchmarks == "cache":
        lines.append("- **Benchmarks**: Artificial Analysis cached benchmarks")
    elif secrets_missing:
        lines.append("- **Benchmarks**: unavailable on this run (1Password secrets absent)")

    if pareto_ok:
        lines.append("- **Pareto method (`--mode pareto`, no fallback)**: PASS")
    elif secrets_missing:
        lines.append(
            "- **Pareto method (`--mode pareto`, no fallback)**: UNAVAILABLE "
            "(1Password secrets absent on fork PR; no fallback used)"
        )
    else:
        lines.append(
            f"- **Pareto method (`--mode pareto`, no fallback)**: **FAILED** "
            f"(exit `{pareto_status}` — no fallback used)"
        )

    if allowance_ok:
        lines.append("- **Allowance method (`--mode allowance`)**: PASS")
    else:
        lines.append(
            f"- **Allowance method (`--mode allowance`)**: **FAILED** (exit `{allowance_status}`)"
        )
    lines.append("")

    if not pareto_ok and not secrets_missing:
        lines.extend(
            [
                "> [!CAUTION]",
                "> **Artificial Analysis / Pareto calculation failed (no fallback used).** "
                "See the Pareto error output below.",
                "",
            ]
        )

    lines.extend(["### Pareto Method (`--mode pareto`, no fallback)", ""])
    empty_msg = (
        "Pareto calculation unavailable (1Password secrets absent; no fallback used)."
        if secrets_missing and not pareto_ok
        else "Pareto selection failed (no fallback applied)."
        if not pareto_ok
        else "The optimizer produced no output."
    )
    lines.extend(
        _render_dual_method_body(
            pareto_payload,
            _read_optional_text(pareto_stderr),
            failed=(not pareto_ok and not secrets_missing),
            empty_message=empty_msg,
            stderr_summary="Pareto stderr",
            include_aa=True,
        )
    )

    lines.extend(["", "### Allowance Method (`--mode allowance`)", ""])
    lines.extend(
        _render_dual_method_body(
            allowance_payload,
            _read_optional_text(allowance_stderr),
            failed=not allowance_ok,
            empty_message="No allowance output produced.",
            stderr_summary="Allowance stderr",
            include_aa=False,
        )
    )

    fetch_text = _read_optional_text(fetch_log)
    if fetch_text:
        fetch_details = render_details_block(
            "Snapshot fetch log",
            fetch_text,
            open_by_default=(not pareto_ok and not secrets_missing),
        )
        if fetch_details:
            lines.extend(["", *fetch_details])

    if pareto_ok and benchmarks in ("live", "cache"):
        lines.extend(["", "*Source: Artificial Analysis (artificialanalysis.ai)*"])
    return "\n".join(lines).strip() + "\n"
