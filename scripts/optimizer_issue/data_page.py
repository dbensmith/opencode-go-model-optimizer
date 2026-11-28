"""Markdown renderer for the Docsify data index page (data.md)."""

from __future__ import annotations

from typing import Any

from opencode_go_model_optimizer.formatters.markdown import render_markdown_table

from .common import _normalize_site_url, _site_file


def render_data_markdown(
    site_url: str | None = None,
    evaluated_at: str | None = None,
    run_url: str | None = None,
    *,
    pareto_ok: bool = True,
    allowance_select_payload: dict[str, Any] | None = None,
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
    artifact_rows: list[list[str]] = []
    if pareto_ok:
        artifact_rows.extend(
            [
                [
                    "[pareto/select.json](pareto/select.json ':ignore')",
                    "Pareto `select --format json --explain` payload (no fallback)",
                ],
                [
                    "[pareto/rank.json](pareto/rank.json ':ignore')",
                    "Every model ranked by Pareto efficiency",
                ],
                [
                    "[pareto/plans/go.json](pareto/plans/go.json ':ignore')",
                    "Pareto `go` plan top-3 choices per tier",
                ],
                [
                    "[pareto/plans/go-plus.json](pareto/plans/go-plus.json ':ignore')",
                    "Pareto `go-plus` plan top-3 choices per tier",
                ],
                [
                    "[pareto/plans/go-teams.json](pareto/plans/go-teams.json ':ignore')",
                    "Pareto `go-teams` plan top-3 choices per tier",
                ],
                [
                    "[pareto/table.txt](pareto/table.txt ':ignore')",
                    "Pareto human-readable ASCII table output",
                ],
            ]
        )
    else:
        artifact_rows.append(
            [
                "[pareto/error.txt](pareto/error.txt ':ignore')",
                "Pareto failure log (Artificial Analysis unavailable; no fallback used)",
            ]
        )
    artifact_rows.extend(
        [
            [
                "[allowance/select.json](allowance/select.json ':ignore')",
                "Allowance `select --format json --explain` payload",
            ],
            [
                "[allowance/rank.json](allowance/rank.json ':ignore')",
                "Every model ranked by allowance and price heuristic",
            ],
            [
                "[allowance/plans/go.json](allowance/plans/go.json ':ignore')",
                "Allowance `go` plan top-3 choices per tier",
            ],
            [
                "[allowance/plans/go-plus.json](allowance/plans/go-plus.json ':ignore')",
                "Allowance `go-plus` plan top-3 choices per tier",
            ],
            [
                "[allowance/plans/go-teams.json](allowance/plans/go-teams.json ':ignore')",
                "Allowance `go-teams` plan top-3 choices per tier",
            ],
            [
                "[allowance/table.txt](allowance/table.txt ':ignore')",
                "Allowance human-readable ASCII table output",
            ],
        ]
    )
    artifact_rows.extend(
        [
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
                "[fetch-log.txt](fetch-log.txt ':ignore')",
                "Ingest warnings and provenance messages from the snapshot fetch",
            ],
        ]
    )
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
    if allowance_select_payload is not None and "excluded" in allowance_select_payload:
        excluded = allowance_select_payload["excluded"] or []
        excluded_rows = [
            [f"`{item['model_id']}`", "; ".join(item["reasons"])] for item in excluded
        ] or [["None", "All models are eligible"]]
        plan = allowance_select_payload.get("plan", "go")
        lines.extend(
            [
                "",
                "## Excluded models",
                "",
                f"Allowance selection for plan `{plan}`.",
                "",
                *render_markdown_table(["Model", "Reason"], excluded_rows),
                "",
            ]
        )
    if site_base:
        quick_links = ["## Quick links", ""]
        if pareto_ok:
            quick_links.append(
                f"- **Pareto ranked catalog**: {_site_file(site_base, 'pareto/rank.json')}"
            )
        quick_links.append(
            f"- **Allowance ranked catalog**: {_site_file(site_base, 'allowance/rank.json')}"
        )
        quick_links.extend(
            [
                f"- **Raw normalized snapshot**: {_site_file(site_base, 'snapshot.json')}",
                "",
            ]
        )
        lines.extend(quick_links)
    return "\n".join(lines).rstrip() + "\n"
