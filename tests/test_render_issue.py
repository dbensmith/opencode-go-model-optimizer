"""Tests for scripts/render_optimizer_issue.py."""

import importlib
import importlib.util
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "render_optimizer_issue.py"
_SPEC = importlib.util.spec_from_file_location("render_optimizer_issue", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def test_render_pareto_issue_markdown(tmp_path: Path) -> None:
    payload = {
        "plan": "go",
        "mode": "pareto",
        "evaluated_at": "2026-11-15T16:00:00+00:00",
        "snapshot": {
            "source": "live",
            "fetched_at": "2026-11-15T15:59:59+00:00",
            "benchmark_source": "aa_live",
        },
        "settings": {
            "mode": "pareto",
            "top_n": 1,
            "profile": "general",
            "price_basis": "blended",
            "burn_basis": "auto",
            "allow_global_region": True,
            "allow_paid_training": False,
            "allow_free_training": True,
            "max_retention_days": None,
            "require_zdr": False,
            "min_context": 0,
            "min_output": 0,
            "max_output_cost": None,
            "max_burn": None,
            "skip": [],
            "prefer": {},
        },
        "tiers": {
            "heavy": "opencode-go/model-a",
            "balanced": "opencode-go/model-b",
            "light": "opencode-go/model-c",
            "free": "opencode-go/model-free",
        },
        "explanations": {
            "heavy": {
                "model_id": "opencode-go/model-a",
                "monthly_allowance": 30.0,
                "output_cost_per_1m": 10.0,
                "turn_cost_usd": 0.01,
                "quota_burn_pct": 47.17,
                "aa_iq": 88.5,
                "aa_coding": 91.2,
                "speed_tps": 64.0,
                "bpe": 1.425,
                "is_pareto": True,
                "context": 1048576,
                "requests_per_5h": 440,
                "requests_per_month": 2120,
                "burn_basis_used": "estimate",
                "privacy": {
                    "trains_on_data": False,
                    "retention_days": 0,
                    "zdr": True,
                    "requires_global_region": False,
                    "note": "Agreement valid.",
                    "agreement_valid_until": "2026-11-30",
                },
                "price_multiplier": 1.0,
                "next_price_change": {"at": "2026-12-01T00:00+00:00", "multiplier": 2.0},
            },
            "free": {
                "model_id": "opencode-go/model-free",
                "monthly_allowance": "unlimited",
                "output_cost_per_1m": 0.0,
                "turn_cost_usd": 0.0,
                "quota_burn_pct": 0.0,
                "aa_iq": None,
                "aa_coding": None,
                "speed_tps": None,
                "bpe": None,
                "is_pareto": None,
                "context": 262144,
            },
        },
    }
    json_path = tmp_path / "select.json"
    json_path.write_text(json.dumps(payload), encoding="utf-8")
    out_path = tmp_path / "issue.md"

    rc = _MOD.main(
        [
            "--json",
            str(json_path),
            "--run-url",
            "https://github.com/example/repo/actions/runs/42",
            "--output",
            str(out_path),
        ]
    )
    assert rc == 0
    md = out_path.read_text(encoding="utf-8")
    assert (
        "| **heavy** | `opencode-go/model-a` | $30.00 | $10.00/1M | 1,048,576 | 88.5 | 91.2 | 64.0 | 1.425 | Yes | 47.2% | $0.0100 | x1 (-> x2 @ 2026-12-01T00:00+00:00) |"
        in md
    )
    assert "| **free** | `opencode-go/model-free` | Unlimited | Free | 262,144 |" in md
    assert "### Run Settings" in md
    assert "Global regions**: allowed" in md
    assert "Paid training**: blocked" in md
    assert "Free training**: allowed" in md
    assert "Pareto mode" in md
    assert "estimated requests: 440 / 5h, 2,120 / month" in md
    assert "#### Visual Tier Summary" in md
    assert "```mermaid\nflowchart LR" in md
    assert 'title "Tier Comparison: Benchmark Scores vs Quota Burn %"' in md
    assert "Source: Artificial Analysis (artificialanalysis.ai)" in md
    assert ".." not in md
    # no rank JSON was passed, so the issue carries no full comparison catalog
    assert "Full Comparison Catalog" not in md


def test_render_issue_markdown_full_comparison_catalog() -> None:
    payload = {
        "plan": "go",
        "mode": "pareto",
        "evaluated_at": "2026-11-16T00:00:00+00:00",
        "snapshot": {
            "source": "live",
            "fetched_at": "2026-11-16T00:00:00+00:00",
            "benchmark_source": "aa_live",
        },
        "tiers": {"heavy": "opencode-go/model-a", "free": "opencode-go/model-free"},
        "explanations": {
            "heavy": {
                "model_id": "opencode-go/model-a",
                "monthly_allowance": 30.0,
                "output_cost_per_1m": 10.0,
                "quota_burn_pct": 47.2,
            },
            "free": {
                "model_id": "opencode-go/model-free",
                "output_cost_per_1m": 0.0,
                "quota_burn_pct": 0.0,
            },
        },
    }
    rank_models = [
        {
            "full_id": "opencode-go/model-a",
            "tier": "heavy",
            "bpe": 1.0,
            "aa_iq": 90.0,
            "aa_coding": 95.0,
            "composite_burn_pct": 47.2,
            "monthly_allowance": 30.0,
            "cost": {"output": 10.0},
            "limit": {"context": 1048576},
            "release_date": "2026-11-01",
            "privacy": {"zdr": True, "trains_on_data": False},
            "is_pareto": True,
            "rank": 1,
        },
        {
            "full_id": "opencode-go/model-b",
            "tier": "-",
            "bpe": 0.5,
            "aa_iq": 60.0,
            "aa_coding": 65.0,
            "composite_burn_pct": 20.0,
            "cost": {"output": 4.0},
            "limit": {"context": 131072},
            "release_date": "2026-10-15",
            "is_pareto": False,
            "rank": 2,
        },
        {
            "full_id": "opencode-go/model-free",
            "tier": "free",
            "is_zero_cost": True,
            "quota_burn_pct": 0.0,
            "limit": {"context": 262144},
            "rank": 3,
        },
    ]

    md = _MOD.render_issue_markdown(payload, rank_models=rank_models)
    assert "### Full Comparison Catalog (`rank --mode pareto`)" in md
    assert (
        "All 3 models evaluated for this run by `rank --mode pareto`, not just the tier winners above."
        in md
    )
    assert (
        "| 1 | `opencode-go/model-a` | heavy | 1.000 | 90.0 | 95.0 | 47.2% "
        "| $30.00 | $10.00/1M | 1,048,576 | 2026-11-01 | ZDR (0d retention); no training |" in md
    )
    assert (
        "| 2 | `opencode-go/model-b` | - | 0.500 | 60.0 | 65.0 | 20.0% "
        "| - | $4.00/1M | 131,072 | 2026-10-15 | No privacy metadata |" in md
    )
    assert (
        "| 3 | `opencode-go/model-free` | free | - | - | - | 0.0% "
        "| - | Free | 262,144 | - | No privacy metadata |" in md
    )
    assert 'title "Catalog Composition by Eligibility & Frontier Status"' in md
    assert '"Pareto Frontier" : 1' in md
    assert '"Paid (Non-Frontier)" : 1' in md
    assert '"Free / Zero-Cost" : 1' in md
    assert '"Unbenchmarked Paid"' not in md

    # the catalog sits between the per-tier explanations and the full script output
    md_with_script = _MOD.render_issue_markdown(
        payload, script_output="raw table output", rank_models=rank_models
    )
    assert (
        md_with_script.index("#### `heavy`")
        < md_with_script.index("### Full Comparison Catalog")
        < md_with_script.index("### Full Script Output")
    )

    # a published site URL adds the Pages link and the artifact index to the issue
    md_site = _MOD.render_issue_markdown(
        payload, rank_models=rank_models, site_url="https://example.github.io/repo/"
    )
    assert "- **GitHub Pages**: https://example.github.io/repo/" in md_site
    assert "- **All data & other choices**: https://example.github.io/repo/#/data" in md_site
    assert "### Other Choices & Published Data" in md_site
    assert "[rank.json](https://example.github.io/repo/rank.json)" in md_site
    assert "[select.json](https://example.github.io/repo/select.json)" in md_site
    assert "[plans/go.json](https://example.github.io/repo/plans/go.json)" in md_site
    assert "[plans/go-plus.json](https://example.github.io/repo/plans/go-plus.json)" in md_site
    assert "[plans/go-teams.json](https://example.github.io/repo/plans/go-teams.json)" in md_site
    assert "[free-matrix.json](https://example.github.io/repo/free-matrix.json)" in md_site
    assert "[snapshot.json](https://example.github.io/repo/snapshot.json)" in md_site
    assert "[full-output.txt](https://example.github.io/repo/full-output.txt)" in md_site
    assert "[table.txt](https://example.github.io/repo/table.txt)" in md_site
    assert "[fetch-log.txt](https://example.github.io/repo/fetch-log.txt)" in md_site
    assert (
        "[pareto-frontier.svg](https://example.github.io/repo/charts/pareto-frontier.svg)"
        in md_site
    )
    assert "Other Choices & Published Data" not in md  # no site URL passed


def test_render_data_index_links_every_artifact(tmp_path: Path) -> None:
    select_path = tmp_path / "select.json"
    select_path.write_text(
        json.dumps(
            {
                "plan": "go",
                "mode": "pareto",
                "evaluated_at": "2026-11-15T16:00:00+00:00",
                "tiers": {},
            }
        ),
        encoding="utf-8",
    )
    out_path = tmp_path / "data.md"
    res = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_PATH),
            "--json",
            str(select_path),
            "--site-url",
            "https://example.github.io/repo/",
            "--data-output",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    md = out_path.read_text(encoding="utf-8")
    assert "All Data & Other Choices" in md
    assert "https://example.github.io/repo/" in md
    assert "plans/go.json" in md
    assert "plans/go-plus.json" in md
    assert "plans/go-teams.json" in md
    assert "free-matrix.json" in md
    assert "snapshot.json" in md
    assert "full-output.txt" in md
    assert "charts/pareto-frontier.svg" in md


def test_render_allowance_issue_markdown_visuals() -> None:
    payload = {
        "plan": "go",
        "mode": "allowance",
        "evaluated_at": "2026-11-15T16:00:00+00:00",
        "snapshot": {
            "source": "live",
            "fetched_at": "2026-11-15T16:00:00+00:00",
            "benchmark_source": "none",
        },
        "tiers": {
            "heavy": "opencode-go/model-a",
            "balanced": "opencode-go/model-b",
            "light": "opencode-go/model-c",
            "free": "opencode-go/model-free",
        },
        "explanations": {
            "heavy": {
                "model_id": "opencode-go/model-a",
                "monthly_allowance": 30.0,
                "output_cost_per_1m": 10.0,
                "quota_burn_pct": 45.0,
            },
            "balanced": {
                "model_id": "opencode-go/model-b",
                "monthly_allowance": 20.0,
                "output_cost_per_1m": 4.0,
                "quota_burn_pct": 25.0,
            },
            "light": {
                "model_id": "opencode-go/model-c",
                "monthly_allowance": 15.0,
                "output_cost_per_1m": 1.0,
                "quota_burn_pct": 10.0,
            },
        },
    }
    md = _MOD.render_issue_markdown(payload)
    assert "#### Visual Tier Summary" in md
    assert 'title "Paid Tier Allowance vs Output Cost"' in md
    assert "bar [30.0, 20.0, 15.0]" in md
    assert "line [10.0, 4.0, 1.0]" in md
    assert ".." not in md


def test_render_with_script_output_and_backticks() -> None:
    payload = {
        "plan": "go",
        "mode": "allowance",
        "evaluated_at": "2026-11-15T16:00:00+00:00",
        "snapshot": {
            "source": "live",
            "fetched_at": "2026-11-15T16:00:00+00:00",
            "benchmark_source": "none",
        },
        "tiers": {"heavy": ""},
        "explanations": {"heavy": {"model_id": "None"}},
    }
    md = _MOD.render_issue_markdown(payload, script_output="line with ``` backticks\n+---+")
    assert "````text" in md
    assert "No eligible model available for this tier." in md
    assert "Artificial Analysis" not in md


def test_render_ci_comment_markdown(tmp_path: Path) -> None:
    ci_dir = tmp_path / "ci"
    ci_dir.mkdir()
    (ci_dir / "format.txt").write_text("All checks passed!", encoding="utf-8")
    (ci_dir / "pytest.txt").write_text(
        "FAILED tests/test_foo.py - assert 1 == 2 ``` backticks inside", encoding="utf-8"
    )

    comment = _MOD.render_ci_comment_markdown(
        ci_dir=ci_dir,
        run_url="https://github.com/example/repo/actions/runs/12345",
        sha="abcdef123456",
        outcomes={"format": "success", "lint": "success", "pytest": "failure", "build": "success"},
    )
    assert "<!-- ci-job-results-comment" in comment
    assert "## CI Job Results" in comment
    assert "https://github.com/example/repo/actions/runs/12345" in comment
    assert "`abcdef123456`" in comment
    assert "| Check code formatting (`ruff format --check`) | `success` |" in comment
    assert "| Run test suite with coverage (`pytest`) | `failure` |" in comment
    assert "```mermaid\nflowchart LR" in comment
    assert "class Format success;" in comment
    assert "class Pytest failure;" in comment
    assert "````text" in comment
    assert "FAILED tests/test_foo.py - assert 1 == 2 ``` backticks inside" in comment


def test_render_optimizer_output_comment(tmp_path: Path) -> None:
    out_file = tmp_path / "optimizer.txt"
    out_file.write_text(
        "OpenCode Go Model Optimizer --plan go --mode pareto\n"
        "+-------+----------------------+\n"
        "| Tier  | Model ID             |\n"
        "| heavy | opencode-go/model-a  |\n"
        "+-------+----------------------+\n"
        "\n"
        "--- stderr ---\n"
        "opencode-go-model-optimizer: warning: catalogue hiccup\n",
        encoding="utf-8",
    )
    comment = _MOD.render_optimizer_output_comment(
        out_file,
        run_url="https://github.com/example/repo/actions/runs/77",
        sha="deadbeef",
        command="uv run opencode-go-model-optimizer select --format table --explain",
        benchmarks="live",
    )
    assert comment.startswith("<!-- optimizer-output-comment:commit=deadbeef -->\n")
    assert "## Optimizer Tier Selections" in comment
    assert "## Optimizer Output" not in comment
    assert "https://github.com/example/repo/actions/runs/77" in comment
    assert "`deadbeef`" in comment
    assert (
        "- **Command**: `uv run opencode-go-model-optimizer select --format table --explain`"
        in comment
    )
    assert "- **Benchmarks**: Artificial Analysis live fetch" in comment
    assert "- **Exit status**" not in comment
    # the ASCII tier table is re-rendered as a native markdown table
    assert "| Tier | Model ID |" in comment
    assert "| --- | --- |" in comment
    assert "| **heavy** | `opencode-go/model-a` |" in comment
    # the table leads the comment, ahead of any collapsible section
    assert comment.index("| Tier | Model ID |") < comment.index("<details")
    # non-table stdout lines survive in a fenced section
    assert "<summary>Additional optimizer output</summary>" in comment
    assert "OpenCode Go Model Optimizer --plan go --mode pareto" in comment
    # stderr lands in its own collapsed section instead of a raw separator line
    assert "<summary>stderr</summary>" in comment
    assert "opencode-go-model-optimizer: warning: catalogue hiccup" in comment
    assert "--- stderr ---" not in comment
    assert "*Source: Artificial Analysis (artificialanalysis.ai)*" in comment


def test_render_optimizer_output_comment_explain_table(tmp_path: Path) -> None:
    """The real `select --format table --explain` output renders as markdown."""
    from opencode_go_model_optimizer.formatters.output import format_select_table
    from opencode_go_model_optimizer.ingest.models import NormalizedModel

    models = [
        NormalizedModel(
            id="model-a",
            provider_id="opencode-go",
            name="Model A",
            monthly_allowance=30.0,
            cost={"output": 10.0},
            limit={"context": 1048576},
            aa_iq=88.5,
            bpe=1.425,
            composite_burn_pct=47.17,
            price_multiplier=1.0,
        ),
        NormalizedModel(
            id="model-free",
            provider_id="opencode-go",
            name="Model Free",
            monthly_allowance=None,
            is_zero_cost=True,
            limit={"context": 262144},
        ),
    ]
    tiers = {"heavy": "opencode-go/model-a", "free": "opencode-go/model-free"}
    ascii_table = format_select_table(tiers, models, plan="go", explain=True)

    out_file = tmp_path / "optimizer.txt"
    out_file.write_text(ascii_table, encoding="utf-8")
    comment = _MOD.render_optimizer_output_comment(out_file, benchmarks="live")
    assert "## Optimizer Tier Selections" in comment
    # every explain column survives the trip through the parser
    assert (
        "| Tier | Model ID | Model Name | Allowance (go) | Output Cost | Context "
        "| IQ | BPE | Burn % | Turn Cost |"
    ) in comment
    assert "| **heavy** | `opencode-go/model-a` | Model A |" in comment
    assert "| $30.00 | $10.00/1M | 1,048,576 | 88.5 | 1.425 | 47.2% |" in comment
    assert "| **free** | `opencode-go/model-free` | Model Free | - | Free | 262,144 |" in comment
    # a pure-table stdout renders no fenced text block at all
    assert "```" not in comment
    # no stderr was appended, so there is no stderr section
    assert "stderr" not in comment


def test_render_optimizer_output_comment_cli_backticks_and_unavailable(tmp_path: Path) -> None:
    out_file = tmp_path / "optimizer.txt"
    out_file.write_text(
        "table with ``` fences inside\nopencode-go-model-optimizer: error: live fetch failed\n",
        encoding="utf-8",
    )
    out_path = tmp_path / "comment.md"

    res = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_PATH),
            "--optimizer-output",
            str(out_file),
            "--run-url",
            "https://github.com/example/repo/actions/runs/78",
            "--sha",
            "1234567890ab",
            "--status",
            "1",
            "--command",
            "uv run opencode-go-model-optimizer select --format table",
            "--benchmarks",
            "unavailable",
            "--output",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    content = out_path.read_text(encoding="utf-8")
    assert "<!-- optimizer-output-comment:commit=1234567890ab -->" in content
    assert "## Optimizer Tier Selections" in content
    assert "## Optimizer Output" not in content
    assert "- **Exit status**: `1` (see output below)" in content
    assert "- **Benchmarks**: unavailable on this run" in content
    assert "falls back to allowance ranking" in content
    # no ASCII table was found, so the output stays a fenced text block
    assert "<summary>Additional optimizer output</summary>" in content
    # backticks inside the output force a longer code fence
    assert "````text" in content
    assert "table with ``` fences inside" in content
    # no AA data was displayed, so no attribution footer
    assert "Artificial Analysis (artificialanalysis.ai)" not in content


def test_render_analysis_markdown_and_cli(tmp_path: Path) -> None:
    from opencode_go_model_optimizer.cli import format_rank_json
    from opencode_go_model_optimizer.ingest.models import NormalizedModel

    m1 = NormalizedModel(
        id="model-a",
        full_id="opencode-go/model-a",
        tier="heavy",
        bpe=1.0,
        aa_iq=90.0,
        aa_coding=95.0,
        composite_burn_pct=50.0,
        monthly_allowance=30.0,
        cost={"output": 10.0},
        limit={"context": 1048576},
        release_date="2026-11-01",
        privacy={"zdr": True, "trains_on_data": False},
        is_pareto=True,
        rank=1,
    )
    m2 = NormalizedModel(
        id="model-b",
        full_id="opencode-go/model-b",
        tier="balanced",
        bpe=0.9,
        aa_iq=60.0,
        aa_coding=65.0,
        composite_burn_pct=20.0,
        monthly_allowance=15.0,
        cost={"output": 0.0},
        is_zero_cost=True,
        limit={"context": 131072},
        release_date="2026-10-15",
        privacy={"zdr": False, "trains_on_data": True},
        is_pareto=True,
        rank=2,
    )

    rank_dict = json.loads(format_rank_json([m1, m2]))

    select_data = {
        "plan": "go",
        "mode": "pareto",
        "evaluated_at": "2026-11-16T00:00:00+00:00",
        "snapshot": {
            "source": "live",
            "fetched_at": "2026-11-16T00:00:00+00:00",
            "benchmark_source": "aa_live",
        },
        "tiers": {"heavy": "opencode-go/model-a", "balanced": "opencode-go/model-b"},
        "fallbacks": {"heavy": ["opencode-go/model-fallback"]},
        "explanations": {
            "heavy": {
                "model_id": "opencode-go/model-a",
                "monthly_allowance": 30.0,
                "output_cost_per_1m": 10.0,
                "quota_burn_pct": 50.0,
            }
        },
    }
    select_path = tmp_path / "select.json"
    select_path.write_text(json.dumps(select_data), encoding="utf-8")

    rank_path = tmp_path / "rank.json"
    rank_path.write_text(json.dumps(rank_dict), encoding="utf-8")

    plan_file = tmp_path / "go.json"
    plan_file.write_text(json.dumps(select_data), encoding="utf-8")

    free_matrix_file = tmp_path / "free_matrix.json"
    free_matrix_file.write_text(
        json.dumps(
            [
                {"label": "Default", "flags": "", "model_id": "opencode-go/free-default"},
            ]
        ),
        encoding="utf-8",
    )

    analysis_out = tmp_path / "analysis.md"
    charts_dir = tmp_path / "charts"

    res = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_PATH),
            "--json",
            str(select_path),
            "--rank-json",
            str(rank_path),
            "--plan-json",
            f"go={plan_file}",
            "--free-matrix-json",
            str(free_matrix_file),
            "--charts-dir",
            str(charts_dir),
            "--analysis-output",
            str(analysis_out),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    assert analysis_out.is_file()
    content = analysis_out.read_text(encoding="utf-8")
    assert "Live Pareto Analysis & Catalog" in content
    assert "opencode-go/model-a" in content
    assert "opencode-go/model-fallback" in content
    assert "opencode-go/free-default" in content
    assert "xychart-beta" in content
    assert "quadrantChart" in content
    assert "flowchart LR" in content
    assert "pie showData" in content
    assert "![Live Pareto Frontier Curve](charts/pareto-frontier.svg)" in content
    assert "![Multi-Plan Comparison](charts/multi-plan-comparison.svg)" in content
    assert "![Catalog Ranking Breakdown](charts/catalog-ranking.svg)" in content

    # the same rank JSON also feeds the full comparison catalog in the issue markdown
    issue_md = res.stdout
    assert "### Full Comparison Catalog (`rank --mode pareto`)" in issue_md
    assert "All 2 models evaluated for this run" in issue_md
    assert "| 1 | `opencode-go/model-a` | heavy |" in issue_md
    assert "| 2 | `opencode-go/model-b` | balanced |" in issue_md
    assert 'title "Catalog Composition by Eligibility & Frontier Status"' in issue_md

    for svg_name in (
        "pareto-frontier.svg",
        "catalog-ranking.svg",
        "multi-plan-comparison.svg",
    ):
        svg_path = charts_dir / svg_name
        assert svg_path.is_file(), f"Missing generated SVG: {svg_name}"
        svg_text = svg_path.read_text(encoding="utf-8")
        root = ET.fromstring(svg_text)
        assert root.tag.endswith("svg")


def test_render_ci_comment_cli(tmp_path: Path) -> None:
    ci_dir = tmp_path / "ci"
    ci_dir.mkdir()
    (ci_dir / "format.txt").write_text("OK", encoding="utf-8")
    out_path = tmp_path / "comment.md"

    res = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_PATH),
            "--ci-dir",
            str(ci_dir),
            "--run-url",
            "https://github.com/example/repo/actions/runs/99",
            "--sha",
            "1234567890ab",
            "--format-outcome",
            "success",
            "--output",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    content = out_path.read_text(encoding="utf-8")
    assert "<!-- ci-job-results-comment" in content
    assert "`1234567890ab`" in content
    assert "| Check code formatting (`ruff format --check`) | `success` |" in content


def test_optimizer_issue_submodule_structure() -> None:
    """scripts/render_optimizer_issue.py delegates to focused scripts/optimizer_issue submodules."""
    package_dir = _SCRIPT_PATH.parent / "optimizer_issue"
    for filename in (
        "__init__.py",
        "common.py",
        "charts.py",
        "issue.py",
        "analysis.py",
        "data_page.py",
        "pages.py",
        "comments.py",
    ):
        assert (package_dir / filename).is_file(), f"Missing submodule file: {filename}"

    pkg = importlib.import_module("optimizer_issue")
    for name in ("common", "charts", "issue", "analysis", "data_page", "pages", "comments"):
        assert importlib.import_module(f"optimizer_issue.{name}") is not None

    for public_fn in (
        "generate_analysis_svgs",
        "render_analysis_markdown",
        "render_ci_comment_markdown",
        "render_data_markdown",
        "render_issue_markdown",
        "render_optimizer_output_comment",
    ):
        assert getattr(_MOD, public_fn) is getattr(pkg, public_fn)

    assert pkg.pages.render_issue_markdown is pkg.issue.render_issue_markdown
    assert pkg.pages.render_analysis_markdown is pkg.analysis.render_analysis_markdown
    assert pkg.pages.render_data_markdown is pkg.data_page.render_data_markdown

    from opencode_go_model_optimizer.engine.tiers import STANDARD_TIERS

    assert pkg.pages.STANDARD_TIERS is STANDARD_TIERS
    assert pkg.common.STANDARD_TIERS is STANDARD_TIERS


def test_render_analysis_markdown_honors_profile_and_weights(tmp_path: Path) -> None:
    """render_analysis_markdown computes quality/value via engine.pareto.effective_quality."""
    rank_models = [
        {
            "full_id": "opencode-go/model-a",
            "id": "model-a",
            "aa_iq": 80.0,
            "aa_coding": 95.0,
            "speed_tps": 150.0,
            "composite_burn_pct": 50.0,
            "monthly_allowance": 30.0,
            "is_pareto": True,
            "rank": 1,
        },
        {
            "full_id": "opencode-go/model-dominated",
            "id": "model-dominated",
            "aa_iq": 60.0,
            "aa_coding": 90.0,
            "speed_tps": 120.0,
            "composite_burn_pct": 70.0,
            "monthly_allowance": 20.0,
            "is_pareto": False,
            "rank": 2,
        },
    ]

    # 1. coding profile: 0.4 * 80 + 0.6 * 95 = 89.0 (general would be 84.5), value = 89 / 50 = 1.78
    coding_md = _MOD.render_analysis_markdown(
        select_payload={
            "plan": "go",
            "mode": "pareto",
            "evaluated_at": "2026-11-16T00:00:00+00:00",
            "settings": {"profile": "coding", "weights": None},
        },
        rank_models=rank_models,
    )
    assert "- **Top Frontier Quality**: `89.0`" in coding_md
    assert "| `opencode-go/model-a` | 80.0 | 95.0 | `89.0` | 50.0% | $30.00 | 1.78 |" in coding_md
    assert "`84.5`" not in coding_md

    # 2. explicit weights [0.2, 0.8]: 0.2 * 80 + 0.8 * 95 = 92.0, value = 92 / 50 = 1.84
    weighted_md = _MOD.render_analysis_markdown(
        select_payload={
            "plan": "go",
            "mode": "pareto",
            "evaluated_at": "2026-11-16T00:00:00+00:00",
            "settings": {"profile": "general", "weights": [0.2, 0.8]},
        },
        rank_models=rank_models,
    )
    assert "- **Top Frontier Quality**: `92.0`" in weighted_md
    assert "| `opencode-go/model-a` | 80.0 | 95.0 | `92.0` | 50.0% | $30.00 | 1.84 |" in weighted_md

    # 3. fast profile: base_q = 0.7*80 + 0.3*95 = 84.5; speed 150 -> * (0.85 + 0.15 * 1.5) = 90.8375
    charts_dir = tmp_path / "charts"
    fast_md = _MOD.render_analysis_markdown(
        select_payload={
            "plan": "go",
            "mode": "pareto",
            "evaluated_at": "2026-11-16T00:00:00+00:00",
            "settings": {"profile": "fast", "weights": None},
        },
        rank_models=rank_models,
        charts_dir=charts_dir,
    )
    assert "- **Top Frontier Quality**: `90.8`" in fast_md
    assert "| `opencode-go/model-a` | 80.0 | 95.0 | `90.8` | 50.0% | $30.00 | 1.82 |" in fast_md
    assert (charts_dir / "pareto-frontier.svg").is_file()


def test_cli_subprocess_from_different_cwd(tmp_path: Path) -> None:
    """Running scripts/render_optimizer_issue.py from outside repo root works cleanly."""
    select_path = tmp_path / "select.json"
    select_path.write_text(
        json.dumps(
            {
                "plan": "go",
                "mode": "pareto",
                "evaluated_at": "2026-11-16T00:00:00+00:00",
                "snapshot": {
                    "source": "live",
                    "fetched_at": "2026-11-16T00:00:00+00:00",
                    "benchmark_source": "aa_live",
                },
                "settings": {"profile": "coding", "weights": None},
                "tiers": {"heavy": "opencode-go/model-a"},
                "explanations": {
                    "heavy": {
                        "model_id": "opencode-go/model-a",
                        "monthly_allowance": 30.0,
                        "output_cost_per_1m": 10.0,
                        "quota_burn_pct": 50.0,
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    rank_path = tmp_path / "rank.json"
    rank_path.write_text(
        json.dumps(
            [
                {
                    "full_id": "opencode-go/model-a",
                    "aa_iq": 80.0,
                    "aa_coding": 95.0,
                    "composite_burn_pct": 50.0,
                    "monthly_allowance": 30.0,
                    "is_pareto": True,
                    "rank": 1,
                }
            ]
        ),
        encoding="utf-8",
    )
    issue_out = tmp_path / "issue.md"
    analysis_out = tmp_path / "analysis.md"
    data_out = tmp_path / "data.md"
    charts_dir = tmp_path / "charts"

    res = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_PATH),
            "--json",
            str(select_path),
            "--rank-json",
            str(rank_path),
            "--charts-dir",
            str(charts_dir),
            "--output",
            str(issue_out),
            "--analysis-output",
            str(analysis_out),
            "--data-output",
            str(data_out),
        ],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=True,
    )
    assert res.returncode == 0
    assert "Latest Tier Selections" in issue_out.read_text(encoding="utf-8")
    assert "- **Top Frontier Quality**: `89.0`" in analysis_out.read_text(encoding="utf-8")
    assert "All Data & Other Choices" in data_out.read_text(encoding="utf-8")
    assert (charts_dir / "pareto-frontier.svg").is_file()


def test_main_loads_select_and_rank_json_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """main() loads args.json and args.rank_json only once even with --analysis-output."""
    select_path = tmp_path / "select.json"
    select_path.write_text(
        json.dumps(
            {
                "plan": "go",
                "mode": "pareto",
                "evaluated_at": "2026-11-16T00:00:00+00:00",
                "tiers": {"heavy": "opencode-go/model-a"},
                "explanations": {},
            }
        ),
        encoding="utf-8",
    )
    rank_path = tmp_path / "rank.json"
    rank_path.write_text(json.dumps([]), encoding="utf-8")

    load_json_calls = 0
    real_load_json = _MOD._load_json

    def counting_load_json(path: str) -> object:
        nonlocal load_json_calls
        if path == str(select_path):
            load_json_calls += 1
        return real_load_json(path)

    rank_calls = 0
    real_load_rank = _MOD._load_rank_models

    def counting_load_rank(path: str | None) -> list[dict[str, object]]:
        nonlocal rank_calls
        rank_calls += 1
        return real_load_rank(path)

    monkeypatch.setattr(_MOD, "_load_json", counting_load_json)
    monkeypatch.setattr(_MOD, "_load_rank_models", counting_load_rank)

    rc = _MOD.main(
        [
            "--json",
            str(select_path),
            "--rank-json",
            str(rank_path),
            "--output",
            str(tmp_path / "issue.md"),
            "--analysis-output",
            str(tmp_path / "analysis.md"),
            "--data-output",
            str(tmp_path / "data.md"),
        ]
    )
    assert rc == 0
    assert load_json_calls == 1
    assert rank_calls == 1
