"""Tests for scripts/render_optimizer_issue.py."""

import importlib
import importlib.util
import json
import subprocess
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest

from tests.factories import make_model

_SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "render_optimizer_issue.py"
_SPEC = importlib.util.spec_from_file_location("render_optimizer_issue", _SCRIPT_PATH)
assert _SPEC and _SPEC.loader
_MOD = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(_MOD)


def _allowance_payload(payload: dict) -> dict:
    """Derive the allowance side of a dual-method fixture from a Pareto payload."""
    allowance = json.loads(json.dumps(payload))
    allowance.update(mode="allowance", method="allowance", fallback=False)
    allowance["snapshot"] = {
        **allowance.get("snapshot", {}),
        "benchmark_source": "none",
    }
    for explanation in allowance.get("explanations", {}).values():
        for key in ("aa_iq", "aa_coding", "speed_tps", "bpe", "is_pareto"):
            explanation.pop(key, None)
    return allowance


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
    pareto_json_path = tmp_path / "pareto_select.json"
    pareto_json_path.write_text(json.dumps(payload), encoding="utf-8")
    allowance_json_path = tmp_path / "allowance_select.json"
    allowance_json_path.write_text(json.dumps(_allowance_payload(payload)), encoding="utf-8")
    out_path = tmp_path / "issue.md"

    rc = _MOD.main(
        [
            "--pareto-json",
            str(pareto_json_path),
            "--allowance-json",
            str(allowance_json_path),
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


def test_render_dual_issue_markdown_full_comparison_catalog() -> None:
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
            "effective_quality": 88.5,
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
            "effective_quality": 61.5,
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
            "effective_quality": 0.0,
            "composite_burn_pct": 0.0,
            "limit": {"context": 262144},
            "rank": 3,
        },
    ]

    allowance_payload = _allowance_payload(payload)
    md = _MOD.render_dual_issue_markdown(
        payload,
        allowance_payload,
        pareto_rank_models=rank_models,
        allowance_rank_models=rank_models,
    )
    assert "#### Pareto Full Comparison Catalog (`rank --mode pareto`)" in md
    assert "All 3 models evaluated by `rank --mode pareto`." in md
    assert "#### Allowance Full Comparison Catalog (`rank --mode allowance`)" in md
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
    md_with_script = _MOD.render_dual_issue_markdown(
        payload,
        allowance_payload,
        pareto_rank_models=rank_models,
        allowance_rank_models=rank_models,
        script_output="raw table output",
    )
    assert (
        md_with_script.index("#### `heavy`")
        < md_with_script.index("#### Pareto Full Comparison Catalog")
        < md_with_script.index("### Full Script Output")
    )

    # a published site URL adds the Pages link and the artifact index to the issue
    md_site = _MOD.render_dual_issue_markdown(
        payload,
        allowance_payload,
        pareto_rank_models=rank_models,
        allowance_rank_models=rank_models,
        site_url="https://example.github.io/repo/",
    )
    assert "- **GitHub Pages**: https://example.github.io/repo/" in md_site
    assert "- **All data & other choices**: https://example.github.io/repo/#/data" in md_site
    assert "### Other Choices & Published Data" in md_site
    assert "[pareto/rank.json](https://example.github.io/repo/pareto/rank.json)" in md_site
    assert "[pareto/select.json](https://example.github.io/repo/pareto/select.json)" in md_site
    assert (
        "[allowance/select.json](https://example.github.io/repo/allowance/select.json)" in md_site
    )
    assert "[allowance/rank.json](https://example.github.io/repo/allowance/rank.json)" in md_site
    assert "[go](https://example.github.io/repo/pareto/plans/go.json)" in md_site
    assert "[go-plus](https://example.github.io/repo/pareto/plans/go-plus.json)" in md_site
    assert "[go-teams](https://example.github.io/repo/pareto/plans/go-teams.json)" in md_site
    assert "[free-matrix.json](https://example.github.io/repo/free-matrix.json)" in md_site
    assert "[snapshot.json](https://example.github.io/repo/snapshot.json)" in md_site
    assert "[full-output.txt](https://example.github.io/repo/full-output.txt)" in md_site
    assert "[fetch-log.txt](https://example.github.io/repo/fetch-log.txt)" in md_site
    assert "Other Choices & Published Data" not in md  # no site URL passed


def test_render_data_index_links_every_artifact(tmp_path: Path) -> None:
    select_path = tmp_path / "select.json"
    select_path.write_text(
        json.dumps(
            {
                "plan": "go",
                "mode": "allowance",
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
            "--allowance-json",
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
    assert "pareto/error.txt" in md
    assert "allowance/plans/go.json" in md
    assert "allowance/plans/go-plus.json" in md
    assert "allowance/plans/go-teams.json" in md
    assert "free-matrix.json" in md
    assert "snapshot.json" in md
    assert "full-output.txt" in md
    assert "charts/pareto-frontier.svg" in md


def test_render_dual_allowance_issue_markdown_visuals() -> None:
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
    md = _MOD.render_dual_issue_markdown(None, payload)
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
    md = _MOD.render_dual_issue_markdown(
        None, payload, script_output="line with ``` backticks\n+---+"
    )
    assert "````text" in md
    assert "No eligible model available for this tier." in md
    assert "*Source: Artificial Analysis (artificialanalysis.ai)*" not in md


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


def test_render_analysis_markdown_and_cli(tmp_path: Path) -> None:
    from opencode_go_model_optimizer.cli import format_rank_json

    m1 = make_model(
        "model-a",
        full_id="opencode-go/model-a",
        tier="heavy",
        bpe=1.0,
        aa_iq=90.0,
        aa_coding=95.0,
        effective_quality=88.5,
        composite_burn_pct=50.0,
        monthly_allowance=30.0,
        cost={"output": 10.0},
        limit={"context": 1048576},
        release_date="2026-11-01",
        privacy={"zdr": True, "trains_on_data": False},
        is_pareto=True,
        rank=1,
    )
    m2 = make_model(
        "model-b",
        full_id="opencode-go/model-b",
        tier="balanced",
        bpe=0.9,
        aa_iq=60.0,
        aa_coding=65.0,
        effective_quality=61.5,
        composite_burn_pct=20.0,
        monthly_allowance=15.0,
        cost={"output": 0.0},
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
    select_path = tmp_path / "pareto_select.json"
    select_path.write_text(json.dumps(select_data), encoding="utf-8")
    allowance_select_path = tmp_path / "allowance_select.json"
    allowance_select_path.write_text(json.dumps(_allowance_payload(select_data)), encoding="utf-8")

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
            "--pareto-json",
            str(select_path),
            "--pareto-rank-json",
            str(rank_path),
            "--pareto-plan-json",
            f"go={plan_file}",
            "--allowance-json",
            str(allowance_select_path),
            "--allowance-rank-json",
            str(rank_path),
            "--allowance-plan-json",
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
    assert "#### Pareto Full Comparison Catalog (`rank --mode pareto`)" in issue_md
    assert "All 2 models evaluated by `rank --mode pareto`." in issue_md
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
        "comments.py",
    ):
        assert (package_dir / filename).is_file(), f"Missing submodule file: {filename}"

    pkg = importlib.import_module("optimizer_issue")
    for name in ("common", "charts", "issue", "analysis", "data_page", "comments"):
        assert importlib.import_module(f"optimizer_issue.{name}") is not None

    for public_fn in (
        "generate_analysis_svgs",
        "render_analysis_markdown",
        "render_ci_comment_markdown",
        "render_data_markdown",
    ):
        assert getattr(_MOD, public_fn) is getattr(pkg, public_fn)

    from opencode_go_model_optimizer.engine.tiers import STANDARD_TIERS

    assert pkg.common.STANDARD_TIERS is STANDARD_TIERS


def test_render_analysis_markdown_uses_stored_quality(tmp_path: Path) -> None:
    """The renderer consumes effective_quality from rank JSON instead of recomputing it."""
    rank_models = [
        {
            "full_id": "opencode-go/model-a",
            "id": "model-a",
            "aa_iq": 80.0,
            "aa_coding": 95.0,
            "speed_tps": 150.0,
            "effective_quality": 89.0,
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
            "effective_quality": 78.0,
            "composite_burn_pct": 70.0,
            "monthly_allowance": 20.0,
            "is_pareto": False,
            "rank": 2,
        },
    ]

    charts_dir = tmp_path / "charts"
    md = _MOD.render_analysis_markdown(
        select_payload={
            "plan": "go",
            "mode": "pareto",
            "evaluated_at": "2026-11-16T00:00:00+00:00",
            "settings": {"profile": "fast", "weights": [0.2, 0.8]},
        },
        rank_models=rank_models,
        charts_dir=charts_dir,
    )
    assert "- **Top Frontier Quality**: `89.0`" in md
    assert "| `opencode-go/model-a` | 80.0 | 95.0 | `89.0` | 50.0% | $30.00 | 1.78 |" in md
    assert "`84.5`" not in md
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
    allowance_select_path = tmp_path / "allowance_select.json"
    allowance_select_path.write_text(
        json.dumps(_allowance_payload(json.loads(select_path.read_text(encoding="utf-8")))),
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
                    "effective_quality": 89.0,
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
            "--pareto-json",
            str(select_path),
            "--pareto-rank-json",
            str(rank_path),
            "--allowance-json",
            str(allowance_select_path),
            "--allowance-rank-json",
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


def test_generate_analysis_svgs_without_plans_and_boolean_allowance(tmp_path: Path) -> None:
    """generate_analysis_svgs handles empty frontier/plans and ignores bool allowances."""
    charts_dir = tmp_path / "charts"
    generated = _MOD.generate_analysis_svgs(
        charts_dir=charts_dir,
        pareto_models=[],
        rank_models=[
            {
                "full_id": "opencode-go/m1",
                "aa_iq": 80.0,
                "aa_coding": 85.0,
                "effective_quality": 82.5,
                "rank": 1,
            }
        ],
        plan_payloads=None,
        top_quality=85.0,
        heavy_cutoff=76.5,
        balanced_cutoff=51.0,
    )
    assert generated == ["pareto-frontier.svg", "catalog-ranking.svg"]
    for name in generated:
        root = ET.fromstring((charts_dir / name).read_text(encoding="utf-8"))
        assert root.tag.endswith("svg")

    multi_svg = importlib.import_module("optimizer_issue.charts")._render_multi_plan_comparison_svg(
        {"go": {"explanations": {"heavy": {"monthly_allowance": True, "quota_burn_pct": 25.0}}}}
    )
    assert ET.fromstring(multi_svg).tag.endswith("svg")


def test_main_requires_primary_mode(capsys: pytest.CaptureFixture[str]) -> None:
    """main() returns 1 and writes to stderr when no primary input flag is provided."""
    args = _MOD.parse_args([])
    assert args.format_outcome == "unknown"
    assert _MOD.main([]) == 1
    assert "must be specified" in capsys.readouterr().err


def test_render_dual_optimizer_output_comment_success_and_aa_failure(tmp_path: Path) -> None:
    pareto_payload = {"method": "pareto", "tiers": {"heavy": "opencode-go/model-a"}}
    allowance_payload = {"mode": "allowance", "tiers": {"heavy": "opencode-go/model-b"}}
    pareto_json = tmp_path / "pareto.json"
    allowance_json = tmp_path / "allowance.json"
    for path, payload in ((pareto_json, pareto_payload), (allowance_json, allowance_payload)):
        path.write_text(json.dumps(payload), encoding="utf-8")
    pareto_stderr = tmp_path / "pareto.err"
    pareto_stderr.write_text(
        "opencode-go-model-optimizer: calculated with METHOD=PARETO\n", encoding="utf-8"
    )
    allowance_stderr = tmp_path / "allowance.err"
    allowance_stderr.write_text("", encoding="utf-8")
    fetch_log = tmp_path / "fetch.log"
    fetch_log.write_text("saved snapshot\n", encoding="utf-8")

    # 1. Both methods pass with live AA benchmarks
    comment_ok = _MOD.render_dual_optimizer_output_comment(
        pareto_payload=pareto_payload,
        allowance_payload=allowance_payload,
        pareto_stderr=pareto_stderr,
        allowance_stderr=allowance_stderr,
        pareto_status=0,
        allowance_status=0,
        run_url="https://github.com/example/repo/actions/runs/100",
        sha="deadbeef",
        benchmarks="live",
        fetch_log=fetch_log,
    )
    assert "### Pareto Method (`--mode pareto`, no fallback)" in comment_ok
    assert "### Allowance Method (`--mode allowance`)" in comment_ok
    assert "- **Calculation method**: `pareto`" in comment_ok
    assert "- **Calculation method**: `allowance`" in comment_ok
    assert "| Tier | Model ID | Allowance | Output Cost | Context | AA IQ |" in comment_ok
    assert "| **heavy** | `opencode-go/model-a` |" in comment_ok
    assert "| **heavy** | `opencode-go/model-b` |" in comment_ok
    assert comment_ok.count("AA IQ") == 1
    assert "> [!CAUTION]" not in comment_ok
    assert "*Source: Artificial Analysis (artificialanalysis.ai)*" in comment_ok

    # 2. Pareto fails (e.g. HTTP 429) on same-repo PR while Allowance passes -> loud caution & no AA attribution
    pareto_fail_json = tmp_path / "pareto_fail.json"
    pareto_fail_json.write_text("", encoding="utf-8")
    pareto_fail_stderr = tmp_path / "pareto_fail.err"
    pareto_fail_stderr.write_text(
        "opencode-go-model-optimizer: error: BenchmarksUnavailableError: HTTP 429\n",
        encoding="utf-8",
    )
    out_md = tmp_path / "comment.md"
    assert (
        _MOD.main(
            [
                "--pareto-json",
                str(pareto_fail_json),
                "--pareto-stderr",
                str(pareto_fail_stderr),
                "--pareto-status",
                "1",
                "--allowance-json",
                str(allowance_json),
                "--allowance-stderr",
                str(allowance_stderr),
                "--allowance-status",
                "0",
                "--output",
                str(out_md),
            ]
        )
        == 0
    )
    comment_fail = out_md.read_text(encoding="utf-8")
    assert "> [!CAUTION]" in comment_fail
    assert "**FAILED** (exit `1` — no fallback used)" in comment_fail
    assert "<details open>" in comment_fail
    assert "<summary>Pareto stderr</summary>" in comment_fail
    assert "BenchmarksUnavailableError: HTTP 429" in comment_fail
    assert "| **heavy** | `opencode-go/model-b` |" in comment_fail
    assert "*Source: Artificial Analysis (artificialanalysis.ai)*" not in comment_fail

    # 3. Fork PR without secrets marks Pareto UNAVAILABLE without CAUTION
    comment_fork = _MOD.render_dual_optimizer_output_comment(
        pareto_payload=None,
        allowance_payload=allowance_payload,
        pareto_stderr=pareto_fail_stderr,
        pareto_status=1,
        allowance_status=0,
        benchmarks="unavailable",
    )
    assert "UNAVAILABLE (1Password secrets absent on fork PR; no fallback used)" in comment_fork
    assert "> [!CAUTION]" not in comment_fork
    assert "*Source: Artificial Analysis (artificialanalysis.ai)*" not in comment_fork


def test_dual_comment_stderr_flags_select_comment_mode(tmp_path: Path) -> None:
    stderr_path = tmp_path / "optimizer.err"
    stderr_path.touch()
    out_path = tmp_path / "comment.md"

    assert (
        _MOD.main(
            [
                "--pareto-json",
                str(tmp_path / "missing-pareto.json"),
                "--pareto-stderr",
                str(stderr_path),
                "--allowance-json",
                str(tmp_path / "missing-allowance.json"),
                "--allowance-stderr",
                str(stderr_path),
                "--output",
                str(out_path),
            ]
        )
        == 0
    )
    assert "## Optimizer Tier Selections" in out_path.read_text(encoding="utf-8")


def test_render_dual_issue_site_and_pareto_failure(tmp_path: Path) -> None:
    allowance_select = {
        "plan": "go",
        "mode": "allowance",
        "method": "allowance",
        "fallback": False,
        "evaluated_at": "2026-11-28T00:00:00+00:00",
        "snapshot": {
            "source": "file",
            "fetched_at": "2026-11-28T00:00:00+00:00",
            "benchmark_source": "none",
            "benchmark_error": "Artificial Analysis returned HTTP 429",
        },
        "settings": {
            "mode": "allowance",
            "allow_fallback": False,
            "top_n": 1,
            "profile": "general",
            "price_basis": "blended",
            "burn_basis": "auto",
            "allow_global_region": True,
            "allow_paid_training": False,
            "allow_free_training": True,
        },
        "tiers": {
            "heavy": "opencode-go/model-b",
            "balanced": "opencode-go/model-b",
            "light": "opencode-go/model-c",
            "free": "opencode-go/model-free",
        },
        "explanations": {
            "heavy": {
                "model_id": "opencode-go/model-b",
                "monthly_allowance": 60.0,
                "output_cost_per_1m": 10.0,
                "quota_burn_pct": 25.0,
                "context": 128000,
            },
            "free": {
                "model_id": "opencode-go/model-free",
                "output_cost_per_1m": 0.0,
                "quota_burn_pct": 0.0,
                "context": 128000,
            },
        },
    }
    pareto_select = {
        **allowance_select,
        "mode": "pareto",
        "method": "pareto",
        "snapshot": {
            "source": "file",
            "fetched_at": "2026-11-28T00:00:00+00:00",
            "benchmark_source": "aa_live",
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
                "monthly_allowance": 60.0,
                "output_cost_per_1m": 10.0,
                "aa_iq": 85.0,
                "aa_coding": 80.0,
                "speed_tps": 90.0,
                "bpe": 1.0,
                "is_pareto": True,
                "quota_burn_pct": 20.0,
                "context": 128000,
            },
            "free": allowance_select["explanations"]["free"],
        },
    }
    allowance_rank = [
        {
            "full_id": "opencode-go/model-b",
            "tier": "heavy",
            "monthly_allowance": 60.0,
            "cost": {"output": 10.0},
            "limit": {"context": 128000},
            "rank": 1,
        }
    ]
    pareto_rank = [
        {
            **allowance_rank[0],
            "full_id": "opencode-go/model-a",
            "aa_iq": 85.0,
            "aa_coding": 80.0,
            "effective_quality": 83.5,
            "composite_burn_pct": 20.0,
            "is_pareto": True,
        }
    ]

    a_sel_p = tmp_path / "a_select.json"
    a_sel_p.write_text(json.dumps(allowance_select), encoding="utf-8")
    p_sel_p = tmp_path / "p_select.json"
    p_sel_p.write_text(json.dumps(pareto_select), encoding="utf-8")
    a_rank_p = tmp_path / "a_rank.json"
    a_rank_p.write_text(json.dumps(allowance_rank), encoding="utf-8")
    p_rank_p = tmp_path / "p_rank.json"
    p_rank_p.write_text(json.dumps(pareto_rank), encoding="utf-8")
    snap_p = tmp_path / "snapshot.json"
    snap_p.write_text(
        json.dumps(
            {
                "fetched_at": "2026-11-28T00:00:00+00:00",
                "metadata": {"benchmark_source": "none", "benchmark_error": "HTTP 429"},
            }
        ),
        encoding="utf-8",
    )
    err_p = tmp_path / "pareto_err.txt"
    err_p.write_text("BenchmarksUnavailableError: HTTP 429\n", encoding="utf-8")

    # Failure run via CLI
    issue_fail = tmp_path / "issue_fail.md"
    analysis_fail = tmp_path / "analysis_fail.md"
    data_fail = tmp_path / "data_fail.md"
    charts_fail = tmp_path / "charts_fail"
    res_fail = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_PATH),
            "--allowance-json",
            str(a_sel_p),
            "--allowance-rank-json",
            str(a_rank_p),
            "--allowance-plan-json",
            f"go={a_sel_p}",
            "--pareto-error",
            str(err_p),
            "--snapshot-json",
            str(snap_p),
            "--site-url",
            "https://example.github.io/repo/",
            "--charts-dir",
            str(charts_fail),
            "--output",
            str(issue_fail),
            "--analysis-output",
            str(analysis_fail),
            "--data-output",
            str(data_fail),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res_fail.returncode == 0
    md_fail = issue_fail.read_text(encoding="utf-8")
    assert "> [!CAUTION]" in md_fail
    assert "**FAILED** (no fallback used)" in md_fail
    assert "### Method Comparison" in md_fail
    assert "| **heavy** | FAILED (no fallback) | `opencode-go/model-b` | - |" in md_fail
    assert "pareto/error.txt" in md_fail
    assert "allowance/select.json" in md_fail
    assert "*Source: Artificial Analysis (artificialanalysis.ai)*" not in md_fail
    assert "> [!CAUTION]" in analysis_fail.read_text(encoding="utf-8")
    assert "pareto/error.txt" in data_fail.read_text(encoding="utf-8")
    assert "allowance/rank.json" in data_fail.read_text(encoding="utf-8")

    # Success run via CLI
    issue_ok = tmp_path / "issue_ok.md"
    analysis_ok = tmp_path / "analysis_ok.md"
    data_ok = tmp_path / "data_ok.md"
    charts_ok = tmp_path / "charts_ok"
    res_ok = subprocess.run(
        [
            sys.executable,
            str(_SCRIPT_PATH),
            "--pareto-json",
            str(p_sel_p),
            "--pareto-rank-json",
            str(p_rank_p),
            "--pareto-plan-json",
            f"go={p_sel_p}",
            "--allowance-json",
            str(a_sel_p),
            "--allowance-rank-json",
            str(a_rank_p),
            "--allowance-plan-json",
            f"go={a_sel_p}",
            "--site-url",
            "https://example.github.io/repo/",
            "--charts-dir",
            str(charts_ok),
            "--output",
            str(issue_ok),
            "--analysis-output",
            str(analysis_ok),
            "--data-output",
            str(data_ok),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    assert res_ok.returncode == 0
    md_ok = issue_ok.read_text(encoding="utf-8")
    assert "**SUCCEEDED**" in md_ok
    assert "| **heavy** | `opencode-go/model-a` | `opencode-go/model-b` | Different |" in md_ok
    assert "pareto/select.json" in md_ok
    assert "allowance/select.json" in md_ok
    assert "*Source: Artificial Analysis (artificialanalysis.ai)*" in md_ok
    assert "Allowance Method: Full Ranked Catalog" in analysis_ok.read_text(encoding="utf-8")
    assert "pareto/rank.json" in data_ok.read_text(encoding="utf-8")
