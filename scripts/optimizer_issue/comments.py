"""PR comment renderers for CI job results and optimizer ASCII table output."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .common import _fence_for

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
