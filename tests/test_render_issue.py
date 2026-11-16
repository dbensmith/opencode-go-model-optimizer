"""Tests for scripts/render_optimizer_issue.py."""

import importlib.util
import json
import subprocess
import sys
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
                "requests_per_5h": "unlimited",
                "requests_per_month": "unlimited",
                "burn_basis_used": "estimate",
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
    assert "Pareto mode" in md
    assert "Source: Artificial Analysis (artificialanalysis.ai)" in md
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
    assert "````text" in comment
    assert "FAILED tests/test_foo.py - assert 1 == 2 ``` backticks inside" in comment


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
