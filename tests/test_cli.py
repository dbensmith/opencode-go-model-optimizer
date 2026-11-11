"""Tests for opencode-go-model-optimizer CLI."""

import json
import runpy
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from opencode_go_model_optimizer.cli import (
    _load_or_fetch_models,
    _parse_preferred_tiers,
    _resolve_mode,
    main,
    parse_args,
)
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_cli
from opencode_go_model_optimizer.ingest.docs import parse_go_pricing
from opencode_go_model_optimizer.ingest.models import (
    NormalizedModel,
    Snapshot,
    SnapshotMetadata,
)
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
    args = parse_args(
        [
            "select",
            "--format",
            "env",
            "--mode",
            "allowance",
            "--cache-ttl",
            "300",
            "--skip",
            "model-a",
            "--skip",
            "model-b",
            "--prefer",
            "heavy=kimi-k3.1-pro",
            "--min-context",
            "128000",
            "--no-contributor",
            "--profile",
            "coding",
            "--explain",
        ]
    )
    assert args.command == "select"
    assert args.format == "env"
    assert args.mode == "allowance"
    assert args.cache_ttl == 300
    assert args.skip == ["model-a", "model-b"]
    assert args.prefer == ["heavy=kimi-k3.1-pro"]
    assert args.min_context == 128000
    assert args.no_contributor is True
    assert args.profile == "coding"
    assert args.explain is True


def test_parse_args_rank():
    args = parse_args(
        [
            "rank",
            "--format",
            "json",
            "--cache-ttl",
            "600",
            "--skip",
            "model-c",
            "--min-context",
            "200000",
            "--no-contributor",
            "--profile",
            "fast",
        ]
    )
    assert args.command == "rank"
    assert args.cache_ttl == 600
    assert args.skip == ["model-c"]
    assert args.min_context == 200000
    assert args.no_contributor is True
    assert args.profile == "fast"


def test_parse_preferred_tiers_helper():
    assert _parse_preferred_tiers(None) == {}
    assert _parse_preferred_tiers([]) == {}

    parsed = _parse_preferred_tiers(
        [
            "heavy=kimi-k3.1-pro",
            "balanced = deepseek-v4.1-flash ",
            "invalid_format",
            "=no_key",
            "no_val=",
        ]
    )
    assert parsed == {
        "heavy": "kimi-k3.1-pro",
        "balanced": "deepseek-v4.1-flash",
    }


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
    assert data["tiers"]["heavy"] == "opencode-go/qwen3.8-max"
    assert data["tiers"]["balanced"] == "opencode-go/deepseek-v4.1-flash"
    assert data["tiers"]["light"] == "opencode-go/glm-5.3-flash"
    assert data["tiers"]["free"] == "opencode-go/iceman-contributor-free"
    assert data["mode"] == "allowance"


def test_cli_select_table(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    rc = main(["select", "--format", "table", "--snapshot", str(snap_path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Tier" in captured.out
    assert "heavy" in captured.out
    assert "opencode-go/qwen3.8-max" in captured.out


def test_cli_select_env_and_github_output(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)

    # --format env
    rc = main(["select", "--format", "env", "--snapshot", str(snap_path)])
    assert rc == 0
    env_out = capsys.readouterr().out
    assert 'OPENCODE_MODEL_HEAVY="opencode-go/qwen3.8-max"' in env_out
    assert 'OPENCODE_MODEL_FREE="opencode-go/iceman-contributor-free"' in env_out

    # --format github-output
    rc = main(["select", "--format", "github-output", "--snapshot", str(snap_path)])
    assert rc == 0
    gh_out = capsys.readouterr().out
    assert "heavy=opencode-go/qwen3.8-max" in gh_out
    assert "free=opencode-go/iceman-contributor-free" in gh_out


def test_cli_select_filters_and_overrides(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)

    # Test --skip, --prefer, --no-contributor, --min-context
    rc = main(
        [
            "select",
            "--format",
            "json",
            "--snapshot",
            str(snap_path),
            "--skip",
            "opencode-go/qwen3.8-max",
            "--prefer",
            "heavy=opencode-go/glm-5.3-flash",
            "--no-contributor",
            "--min-context",
            "200000",
        ]
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["tiers"]["heavy"] == "opencode-go/glm-5.3-flash"
    assert data["tiers"]["free"] == "opencode-go/beam-free"


def test_cli_rank_filters(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)

    rc = main(
        [
            "rank",
            "--format",
            "json",
            "--snapshot",
            str(snap_path),
            "--skip",
            "qwen3.8-max",
            "--min-context",
            "500000",
            "--no-contributor",
        ]
    )
    assert rc == 0
    models = json.loads(capsys.readouterr().out)
    assert all(m["id"] != "qwen3.8-max" for m in models)
    assert all(m["limit"]["context"] >= 500000 for m in models)
    free_models = [m for m in models if m.get("tier") == "free"]
    assert len(free_models) == 1
    assert not free_models[0].get("is_contributor", False)


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
    assert "heavy opencode-go/qwen3.8-max" in lines
    assert "balanced opencode-go/deepseek-v4.1-flash" in lines
    assert "light opencode-go/glm-5.3-flash" in lines


def test_cli_select_profile_and_explain(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)

    # --explain with JSON format
    rc = main(
        [
            "select",
            "--format",
            "json",
            "--snapshot",
            str(snap_path),
            "--profile",
            "reasoning",
            "--explain",
        ]
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["profile"] == "reasoning"
    assert "explanations" in data
    assert "heavy" in data["explanations"]
    assert "turn_cost_usd" in data["explanations"]["heavy"]

    # --explain with table format
    rc = main(
        [
            "select",
            "--format",
            "table",
            "--snapshot",
            str(snap_path),
            "--explain",
        ]
    )
    assert rc == 0
    tbl_out = capsys.readouterr().out
    assert "IQ" in tbl_out
    assert "Turn Cost" in tbl_out
    assert "Burn %" in tbl_out
    assert "BPE" in tbl_out


def test_cli_multiplan_offline_snapshot(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    snap = Snapshot.model_validate_json(snap_path.read_text(encoding="utf-8"))

    # Add plan_allowances to GLM
    for m in snap.models:
        if m.id == "glm-5.3-flash":
            m.plan_allowances = {"go": 60.0, "go-plus": 250.0}
    save_snapshot(snap, snap_path)

    # Run select with --plan go-plus on offline snapshot
    rc = main(
        [
            "select",
            "--format",
            "table",
            "--snapshot",
            str(snap_path),
            "--plan",
            "go-plus",
            "--prefer",
            "heavy=opencode-go/glm-5.3-flash",
        ]
    )
    assert rc == 0
    table_out = capsys.readouterr().out
    assert "$250.00" in table_out

    # JSON format should report effective plan 'go-plus'
    rc_json = main(
        [
            "select",
            "--format",
            "json",
            "--snapshot",
            str(snap_path),
            "--plan",
            "go-plus",
        ]
    )
    assert rc_json == 0
    json_out = json.loads(capsys.readouterr().out)
    assert json_out["plan"] == "go-plus"


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


def test_cli_fetch_update_reference(tmp_path):
    cli_text = (FIXTURES_DIR / "catalogue_cli.txt").read_text(encoding="utf-8")
    mdx_text = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
    fake_ref_path = tmp_path / "reference_snapshot.json"

    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=parse_catalogue_cli(cli_text),
        ),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=mdx_text),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.get_reference_snapshot_path",
            return_value=fake_ref_path,
        ),
    ):
        rc = main(["fetch", "--update-reference"])
        assert rc == 0
        assert fake_ref_path.exists()
        data = json.loads(fake_ref_path.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert len(data["models"]) == 7


def test_resolve_mode():
    m_with_iq = NormalizedModel(id="m1", aa_iq=60.0)
    m_with_coding = NormalizedModel(id="m2", aa_coding=75.0)
    m_no_bench = NormalizedModel(id="m3")
    m_inactive = NormalizedModel(id="m4", aa_iq=80.0, status="deprecated")

    # Explicit modes return as-is
    assert _resolve_mode("allowance", [m_with_iq]) == "allowance"
    assert _resolve_mode("pareto", [m_with_iq]) == "pareto"

    # Auto mode resolves to pareto whenever any active model has benchmark data
    assert _resolve_mode("auto", [m_with_iq]) == "pareto"
    assert _resolve_mode("auto", [m_with_coding]) == "pareto"

    # Auto mode returns allowance if no active models have benchmarks
    assert _resolve_mode("auto", [m_no_bench]) == "allowance"
    assert _resolve_mode("auto", [m_inactive]) == "allowance"


def test_load_or_fetch_models(tmp_path):
    # Snapshot provided
    snap_path = _create_temp_snapshot(tmp_path)
    models, plan = _load_or_fetch_models(str(snap_path))
    assert len(models) == 7
    assert plan == "go"

    # Live fetch succeeds
    dummy_snap = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="go-plus",
        models=[NormalizedModel(id="m1")],
        metadata=SnapshotMetadata(),
    )
    with patch(
        "opencode_go_model_optimizer.cli.fetch_and_build_snapshot",
        return_value=dummy_snap,
    ):
        models, plan = _load_or_fetch_models(plan="go-plus")
        assert len(models) == 1
        assert plan == "go-plus"

    # Cache hit when cache_ttl > 0
    with patch(
        "opencode_go_model_optimizer.cli.load_cached_snapshot",
        return_value=dummy_snap,
    ) as mock_load:
        models, plan = _load_or_fetch_models(plan="go-plus", cache_ttl=3600)
        assert len(models) == 1
        assert plan == "go-plus"
        mock_load.assert_called_once_with(plan="go-plus", ttl_seconds=3600)

    # Cache miss when cache_ttl > 0 -> fetches and saves cache
    with (
        patch("opencode_go_model_optimizer.cli.load_cached_snapshot", return_value=None),
        patch("opencode_go_model_optimizer.cli.fetch_and_build_snapshot", return_value=dummy_snap),
        patch("opencode_go_model_optimizer.cli.save_cached_snapshot") as mock_save,
    ):
        models, plan = _load_or_fetch_models(plan="go-plus", cache_ttl=3600)
        assert len(models) == 1
        mock_save.assert_called_once_with(dummy_snap)

    # Snapshot provided with plan_allowances for requested plan
    snap_multi = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="go",
        models=[NormalizedModel(id="m1", plan_allowances={"go-plus": 60.0})],
        metadata=SnapshotMetadata(),
    )
    multi_path = tmp_path / "snap_multi.json"
    save_snapshot(snap_multi, multi_path)
    models, plan = _load_or_fetch_models(str(multi_path), plan="go-plus")
    assert plan == "go-plus"

    # Live fetch fails with network error, falls back to reference snapshot with plan_allowances
    ref_snap_with_allowances = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="reference",
        models=[NormalizedModel(id="ref1", plan_allowances={"go-plus": 100.0})],
        metadata=SnapshotMetadata(),
    )
    with (
        patch(
            "opencode_go_model_optimizer.cli.fetch_and_build_snapshot",
            side_effect=httpx.ConnectError("Connection failed"),
        ),
        patch(
            "opencode_go_model_optimizer.cli.load_reference_snapshot",
            return_value=ref_snap_with_allowances,
        ),
    ):
        models, plan = _load_or_fetch_models(plan="go-plus")
        assert plan == "go-plus"

    # Live fetch fails with network error, falls back to reference snapshot
    ref_snap = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="reference",
        models=[NormalizedModel(id="ref1"), NormalizedModel(id="ref2")],
        metadata=SnapshotMetadata(),
    )
    with (
        patch(
            "opencode_go_model_optimizer.cli.fetch_and_build_snapshot",
            side_effect=httpx.ConnectError("Connection failed"),
        ),
        patch(
            "opencode_go_model_optimizer.cli.load_reference_snapshot",
            return_value=ref_snap,
        ),
    ):
        models, plan = _load_or_fetch_models()
        assert len(models) == 2
        assert plan == "reference"

    # Live fetch fails and reference snapshot is missing (None) -> raises error
    with (
        patch(
            "opencode_go_model_optimizer.cli.fetch_and_build_snapshot",
            side_effect=httpx.ConnectError("Connection failed"),
        ),
        patch(
            "opencode_go_model_optimizer.cli.load_reference_snapshot",
            return_value=None,
        ),
        pytest.raises(httpx.ConnectError),
    ):
        _load_or_fetch_models()


def test_handle_rank_modes_and_formats(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)

    # --table flag overrides format
    rc = main(["rank", "--table", "--snapshot", str(snap_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Rank" in out
    assert "Model ID" in out

    # --format table with pareto mode
    rc = main(
        [
            "rank",
            "--format",
            "table",
            "--mode",
            "pareto",
            "--snapshot",
            str(snap_path),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    assert "BPE" in out
    assert "IQ" in out

    # --format json with pareto mode
    rc = main(
        [
            "rank",
            "--format",
            "json",
            "--mode",
            "pareto",
            "--snapshot",
            str(snap_path),
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    data = json.loads(out)
    assert isinstance(data, list)
    assert len(data) > 0

    # --format json with allowance mode and output file
    out_file = tmp_path / "rank_allowance.json"
    rc = main(
        [
            "rank",
            "--format",
            "json",
            "--mode",
            "allowance",
            "--snapshot",
            str(snap_path),
            "-o",
            str(out_file),
        ]
    )
    assert rc == 0
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert isinstance(data, list)


def test_main_unhandled_command():
    with patch("opencode_go_model_optimizer.cli.parse_args") as mock_parse:
        import argparse

        mock_parse.return_value = argparse.Namespace(command="unhandled_cmd")
        assert main(["unhandled_cmd"]) == 0


def test_main_module_execution():
    with patch("sys.argv", ["opencode-go-model-optimizer"]), patch.dict("sys.modules"):
        import sys

        sys.modules.pop("opencode_go_model_optimizer.cli", None)
        with pytest.raises(SystemExit) as exc_info:
            runpy.run_module("opencode_go_model_optimizer.cli", run_name="__main__")
        assert exc_info.value.code == 0
