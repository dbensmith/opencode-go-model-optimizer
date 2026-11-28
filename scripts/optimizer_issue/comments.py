"""PR comment renderers for CI job results and optimizer ASCII table output."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from opencode_go_model_optimizer.formatters.markdown import (
    render_details_block,
    render_markdown_table,
)

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
    return render_markdown_table(headers, rows, cell_formatter=_format_table_cell)


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


def _parse_output_payload(
    output: str | Path,
) -> tuple[list[str], list[dict[str, Any]], list[dict[str, Any]], str]:
    """Read combined optimizer stdout/stderr and extract method banners, tables, and stray lines."""
    text = Path(output).read_text(encoding="utf-8", errors="replace")
    stdout_text, stderr_text = _split_stderr_section(text)
    blocks = _parse_ascii_output_blocks(stdout_text)
    tables = [b for b in blocks if b["kind"] == "table"]
    method_lines: list[str] = []
    stray_groups: list[dict[str, Any]] = []
    for block in blocks:
        if block["kind"] != "lines":
            continue
        kept: list[str] = []
        for line in block["lines"]:
            if line.startswith("Calculation method:"):
                method_lines.append(line.removeprefix("Calculation method:").strip())
            else:
                kept.append(line)
        if kept:
            stray_groups.append({"kind": "lines", "lines": kept})
    return method_lines, tables, stray_groups, stderr_text


def _render_output_body(
    method_lines: list[str],
    tables: list[dict[str, Any]],
    stray_groups: list[dict[str, Any]],
    stderr_text: str,
    *,
    failed: bool,
    empty_message: str = "The optimizer produced no output.",
    stderr_summary: str = "stderr",
    additional_summary: str = "Additional optimizer output",
) -> list[str]:
    """Render parsed method banner, tables, stray stdout, and stderr into markdown lines."""
    lines: list[str] = []
    for method in method_lines:
        lines.append(f"- **Calculation method**: `{method}`")
    if method_lines:
        lines.append("")

    for i, block in enumerate(tables):
        if i:
            lines.append("")
        lines.extend(_render_markdown_table(block["headers"], block["rows"]))

    if stray_groups:
        if tables:
            lines.append("")
        stray_text = "\n\n".join("\n".join(g["lines"]) for g in stray_groups)
        lines.extend(render_details_block(additional_summary, stray_text))

    if not tables and not stray_groups:
        lines.append(empty_message)

    stderr_details = render_details_block(
        stderr_summary,
        stderr_text,
        open_by_default=(failed or not tables),
    )
    if stderr_details:
        lines.extend(["", *stderr_details])
    return lines


def render_optimizer_output_comment(
    output: str | Path,
    run_url: str | None = None,
    sha: str | None = None,
    command: str | None = None,
    benchmarks: str | None = None,
    status: str | int | None = None,
) -> str:
    """Render a PR comment showing the optimizer's tier selections as a table."""
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
    elif benchmarks == "cache":
        lines.append("- **Benchmarks**: Artificial Analysis cached benchmarks")
    elif benchmarks == "unavailable":
        lines.append(
            "- **Benchmarks**: unavailable on this run (1Password secrets absent); "
            "pareto selection falls back to allowance ranking"
        )
    if len(lines) > 3:
        lines.append("")

    method_lines, tables, stray_groups, stderr_text = _parse_output_payload(output)
    failed = status is not None and str(status) != "0"
    lines.extend(
        _render_output_body(
            method_lines,
            tables,
            stray_groups,
            stderr_text,
            failed=failed,
        )
    )

    used_aa = (
        benchmarks in ("live", "cache")
        and not failed
        and not any("ALLOWANCE" in m for m in method_lines)
    )
    if used_aa:
        lines.extend(["", "*Source: Artificial Analysis (artificialanalysis.ai)*"])
    return "\n".join(lines).strip() + "\n"


def render_dual_optimizer_output_comment(
    *,
    pareto_output: str | Path | None = None,
    allowance_output: str | Path | None = None,
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
    if pareto_output and Path(pareto_output).is_file():
        p_methods, p_tables, p_strays, p_stderr = _parse_output_payload(pareto_output)
        empty_msg = (
            "Pareto calculation unavailable (1Password secrets absent; no fallback used)."
            if secrets_missing and not pareto_ok
            else "Pareto selection failed (no fallback applied)."
            if not pareto_ok
            else "The optimizer produced no output."
        )
        lines.extend(
            _render_output_body(
                p_methods,
                p_tables,
                p_strays,
                p_stderr,
                failed=(not pareto_ok and not secrets_missing),
                empty_message=empty_msg,
                stderr_summary="Pareto stderr",
                additional_summary="Additional Pareto output",
            )
        )
    else:
        lines.append("No Pareto output captured.")

    lines.extend(["", "### Allowance Method (`--mode allowance`)", ""])
    if allowance_output and Path(allowance_output).is_file():
        a_methods, a_tables, a_strays, a_stderr = _parse_output_payload(allowance_output)
        lines.extend(
            _render_output_body(
                a_methods,
                a_tables,
                a_strays,
                a_stderr,
                failed=not allowance_ok,
                empty_message="No allowance output produced.",
                stderr_summary="Allowance stderr",
                additional_summary="Additional allowance output",
            )
        )
    else:
        lines.append("No allowance output captured.")

    if fetch_log and Path(fetch_log).is_file():
        fetch_text = Path(fetch_log).read_text(encoding="utf-8", errors="replace")
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
