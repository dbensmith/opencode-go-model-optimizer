"""Tests for opencode-go-model-optimizer CLI."""

import json
from pathlib import Path
from unittest.mock import patch

from opencode_go_model_optimizer.cli import main, parse_args
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_cli
from opencode_go_model_optimizer.ingest.docs import parse_go_pricing
from opencode_go_model_optimizer.ingest.snapshot import create_snapshot, save_snapshot

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _create_temp_snapshot(tmp_path: Path) -> Path:
    cli_text = (FIXTURES_DIR / "catalogue_cli.txt").read_text(encoding="utf-8")
    mdx_text = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
    models = parse_catalogue_cli(cli_text)
    pricing = parse_go_pricing(mdx_text)
    snap = create_snapshot(models, pricing)
    snap_path = tmp_path / "snapshot.json"
    save_snapshot(snap, snap_path)
    return snap_path


def test_parse_args_select():
    args = parse_args(["select", "--format", "json", "--mode", "allowance"])
    assert args.command == "select"
    assert args.format == "json"
    assert args.mode == "allowance"


def test_main_no_args(capsys):
    rc = main([])
    assert rc == 0
    captured = capsys.readouterr()
    assert "use --help" in captured.err


def test_cli_select_json(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    rc = main(["select", "--format", "json", "--snapshot", str(snap_path)])
    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "tiers" in data
    assert data["plan"] == "go"
    assert data["tiers"]["flagship"] == "opencode-go/qwen3.8-max"
    assert data["tiers"]["balanced"] == "opencode-go/deepseek-v4.1-flash"
    assert data["tiers"]["fast"] == "opencode-go/glm-5.3-flash"
    assert data["tiers"]["free"] == "opencode-go/iceman-contributor-free"
    assert data["mode"] == "allowance"


def test_cli_select_table(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    rc = main(["select", "--format", "table", "--snapshot", str(snap_path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Tier" in captured.out
    assert "flagship" in captured.out
    assert "opencode-go/qwen3.8-max" in captured.out


def test_cli_select_plan_switch(tmp_path):
    cli_text = (FIXTURES_DIR / "catalogue_cli.txt").read_text(encoding="utf-8")
    mdx_text = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
    out_snap_go = tmp_path / "snap_go.json"
    out_snap_plus = tmp_path / "snap_plus.json"

    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=parse_catalogue_cli(cli_text),
        ),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=mdx_text),
    ):
        main(["fetch", "--plan", "go", "--out", str(out_snap_go)])
        main(["fetch", "--plan", "go-plus", "--out", str(out_snap_plus)])

    snap_go = json.loads(out_snap_go.read_text(encoding="utf-8"))
    snap_plus = json.loads(out_snap_plus.read_text(encoding="utf-8"))

    assert snap_go["plan"] == "go"
    assert snap_plus["plan"] == "go-plus"

    # GLM-5.3-Flash allowance is $60 under go, but $200 under go-plus in the fixture
    glm_go = next(m for m in snap_go["models"] if m["id"] == "glm-5.3-flash")
    glm_plus = next(m for m in snap_plus["models"] if m["id"] == "glm-5.3-flash")
    assert glm_go["monthly_allowance"] == 60.0
    assert glm_plus["monthly_allowance"] == 200.0


def test_cli_select_compact(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    rc = main(["select", "--format", "compact", "--snapshot", str(snap_path)])
    assert rc == 0
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert "flagship opencode-go/qwen3.8-max" in lines
    assert "balanced opencode-go/deepseek-v4.1-flash" in lines


def test_cli_select_output_file(tmp_path):
    snap_path = _create_temp_snapshot(tmp_path)
    out_file = tmp_path / "selected.json"
    rc = main(["select", "--format", "json", "--snapshot", str(snap_path), "-o", str(out_file)])
    assert rc == 0
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert "tiers" in data


def test_cli_rank_table(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    rc = main(["rank", "--table", "--snapshot", str(snap_path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Rank" in captured.out
    assert "deepseek-v4.1-flash" in captured.out


def test_cli_rank_json(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    rc = main(["rank", "--format", "json", "--snapshot", str(snap_path)])
    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) > 0


def test_cli_fetch(tmp_path):
    cli_text = (FIXTURES_DIR / "catalogue_cli.txt").read_text(encoding="utf-8")
    mdx_text = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
    out_snap = tmp_path / "fetched_snapshot.json"

    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=parse_catalogue_cli(cli_text),
        ),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=mdx_text),
    ):
        rc = main(["fetch", "--out", str(out_snap)])
        assert rc == 0
        assert out_snap.exists()
        data = json.loads(out_snap.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert len(data["models"]) == 7
