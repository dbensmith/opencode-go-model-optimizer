"""Tests for scripts/render_optimizer_issue.py."""

import importlib.util
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

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
    assert "## Optimizer Output" in comment
    assert "https://github.com/example/repo/actions/runs/77" in comment
    assert "`deadbeef`" in comment
    assert (
        "- **Command**: `uv run opencode-go-model-optimizer select --format table --explain`"
        in comment
    )
    assert "- **Benchmarks**: Artificial Analysis live fetch" in comment
    assert "- **Exit status**" not in comment
    assert "<summary>Actual optimizer output</summary>" in comment
    assert "| heavy | opencode-go/model-a  |" in comment
    assert "--- stderr ---" in comment
    assert "*Source: Artificial Analysis (artificialanalysis.ai)*" in comment


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
    assert "## Optimizer Output" in content
    assert "- **Exit status**: `1` (see output below)" in content
    assert "- **Benchmarks**: unavailable on this run" in content
    assert "falls back to allowance ranking" in content
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
