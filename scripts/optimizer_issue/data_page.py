"""Markdown renderer for the Docsify data index page (data.md)."""

from __future__ import annotations

from opencode_go_model_optimizer.formatters.markdown import render_markdown_table

from .common import _normalize_site_url, _site_file


def render_data_markdown(
    site_url: str | None = None,
    evaluated_at: str | None = None,
    run_url: str | None = None,
) -> str:
    """Render the data index page linking every artifact published to GitHub Pages."""
    site_base = _normalize_site_url(site_url)
    lines = [
        "# OpenCode Go Model Optimizer — All Data & Other Choices",
        "",
        "This page indexes every artifact published by the latest optimizer run. Use it to",
        "inspect the models that were **not** selected, the fallback chains for each plan, and",
        "the normalized data collected from every ingest source.",
        "",
    ]
    if evaluated_at:
        lines.append(f"- **Updated**: `{evaluated_at}`")
    if run_url:
        lines.append(f"- **Workflow Run**: {run_url}")
    if site_base:
        lines.append(f"- **GitHub Pages**: {site_base}")

    pages_rows = [
        [
            "[Latest tier selections](README.md)",
            "Final `heavy`, `balanced`, `light` and `free` picks",
        ],
        [
            "[Live Pareto analysis & plans](analysis.md)",
            "Frontier, quality/value bands, free-tier matrix, multi-plan top-3 fallbacks, and the full ranked catalog",
        ],
        ["[Documentation & CLI usage](usage.md)", "How to run the optimizer yourself"],
    ]
    artifact_rows = [
        [
            "[select.json](select.json ':ignore')",
            "Raw `select --format json --explain` payload for the configured plan",
        ],
        [
            "[rank.json](rank.json ':ignore')",
            "Every model ranked by the optimizer, including models that did not win a tier",
        ],
        [
            "[plans/go.json](plans/go.json ':ignore')",
            "`go` plan top-3 choices per tier with explanations",
        ],
        [
            "[plans/go-plus.json](plans/go-plus.json ':ignore')",
            "`go-plus` plan top-3 choices per tier with explanations",
        ],
        [
            "[plans/go-teams.json](plans/go-teams.json ':ignore')",
            "`go-teams` plan top-3 choices per tier with explanations",
        ],
        [
            "[free-matrix.json](free-matrix.json ':ignore')",
            "Free-tier picks under every privacy/region flag combination",
        ],
        [
            "[snapshot.json](snapshot.json ':ignore')",
            "Normalized ingest snapshot: catalogue, docs pricing/privacy/request estimates, models.dev metadata, and Artificial Analysis benchmarks when enabled",
        ],
        [
            "[full-output.txt](full-output.txt ':ignore')",
            "Full stdout/stderr from the optimizer script",
        ],
        [
            "[table.txt](table.txt ':ignore')",
            "Human-readable table output from `scripts/run-optimizer.sh`",
        ],
        [
            "[fetch-log.txt](fetch-log.txt ':ignore')",
            "Ingest warnings and provenance messages from the snapshot fetch",
        ],
    ]
    chart_rows = [
        [
            "[pareto-frontier.svg](charts/pareto-frontier.svg ':ignore')",
            "Effective quality vs quota burn with frontier and value bands",
        ],
        [
            "[catalog-ranking.svg](charts/catalog-ranking.svg ':ignore')",
            "Ranked model catalog with benchmark scores and burn",
        ],
        [
            "[multi-plan-comparison.svg](charts/multi-plan-comparison.svg ':ignore')",
            "Monthly allowance vs quota burn across plans and paid tiers",
        ],
    ]
    source_rows = [
        [
            "OpenCode catalogue (API/CLI)",
            "Model IDs, providers, canonical IDs, status, token prices, multipliers",
            "Fetched live each run",
        ],
        [
            "OpenCode docs",
            "Monthly plan allowances, privacy/training rules, request estimates",
            "Fetched live each run",
        ],
        [
            "models.dev",
            "Release dates, context windows, max output tokens",
            "`opencode-go` provider only",
        ],
        [
            "Artificial Analysis",
            "IQ, engineering capability, output speed",
            "Only when `AA_API_KEY` is configured; always attributed",
        ],
    ]

    lines.extend(
        [
            "- **GitHub Issue**: {{ISSUES_URL}}",
            "- **GitHub Repository**: {{REPO_URL}}",
            "",
            "## Rendered pages",
            "",
            *render_markdown_table(["Page", "Contents"], pages_rows),
            "",
            "## Other choices and raw data",
            "",
            *render_markdown_table(["Artifact", "Contents"], artifact_rows),
            "",
            "## Charts (SVG)",
            "",
            *render_markdown_table(["Chart", "Contents"], chart_rows),
            "",
            "## Data sources",
            "",
            *render_markdown_table(["Source", "Used for", "Notes"], source_rows),
            "",
            "The normalized snapshot records the URL, HTTP status, fetch time, sha256, and byte length",
            "for each source. Raw response bodies are never published.",
            "",
        ]
    )
    if site_base:
        lines.extend(
            [
                "## Quick links",
                "",
                "- **Issue**: {{ISSUES_URL}}",
                f"- **Raw ranked catalog**: {_site_file(site_base, 'rank.json')}",
                f"- **Raw normalized snapshot**: {_site_file(site_base, 'snapshot.json')}",
                "",
            ]
        )
    return "\n".join(lines).rstrip() + "\n"
