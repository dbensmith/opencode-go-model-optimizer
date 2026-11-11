"""Tests for opencode-go-model-optimizer CLI."""

import argparse
import json
import runpy
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from opencode_go_model_optimizer.cli import (
    LoadedSnapshot,
    _load_or_fetch_models,
    _load_or_fetch_snapshot,
    _parse_preferred_tiers,
    _parse_token_mix,
    _parse_weights,
    main,
    parse_args,
)
from opencode_go_model_optimizer.engine.tiers import resolve_mode
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_cli
from opencode_go_model_optimizer.ingest.docs import parse_go_pricing
from opencode_go_model_optimizer.ingest.models import (
    NormalizedModel,
    Snapshot,
    SnapshotMetadata,
)
from opencode_go_model_optimizer.ingest.snapshot import (
    create_snapshot,
    load_snapshot,
    save_snapshot,
)

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


def testresolve_mode():
    m_with_iq = NormalizedModel(id="m1", aa_iq=60.0)
    m_with_coding = NormalizedModel(id="m2", aa_coding=75.0)
    m_no_bench = NormalizedModel(id="m3")
    m_inactive = NormalizedModel(id="m4", aa_iq=80.0, status="deprecated")

    # Explicit modes return as-is
    assert resolve_mode("allowance", [m_with_iq]) == "allowance"
    assert resolve_mode("pareto", [m_with_iq]) == "pareto"

    # Auto mode resolves to pareto whenever any active model has benchmark data
    assert resolve_mode("auto", [m_with_iq]) == "pareto"
    assert resolve_mode("auto", [m_with_coding]) == "pareto"

    # Auto mode returns allowance if no active models have benchmarks
    assert resolve_mode("auto", [m_no_bench]) == "allowance"
    assert resolve_mode("auto", [m_inactive]) == "allowance"


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


def test_cli_top_n_and_output_guardrails(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)

    # select with --top-n 3 --format json
    rc = main(
        [
            "select",
            "--format",
            "json",
            "--snapshot",
            str(snap_path),
            "--top-n",
            "3",
        ]
    )
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert "fallbacks" in out
    assert set(out["fallbacks"].keys()) == {"heavy", "balanced", "light", "free"}

    # select with --top-n 2 --format env
    rc_env = main(
        [
            "select",
            "--format",
            "env",
            "--snapshot",
            str(snap_path),
            "--top-n",
            "2",
        ]
    )
    assert rc_env == 0
    env_out = capsys.readouterr().out
    assert "OPENCODE_MODEL_HEAVY_FALLBACKS=" in env_out

    # select with --top-n 2 --format github-output
    rc_gh = main(
        [
            "select",
            "--format",
            "github-output",
            "--snapshot",
            str(snap_path),
            "--top-n",
            "2",
        ]
    )
    assert rc_gh == 0
    gh_out = capsys.readouterr().out
    assert "heavy_fallbacks=" in gh_out

    # select with --min-output and --max-output-cost
    rc_caps = main(
        [
            "select",
            "--snapshot",
            str(snap_path),
            "--min-output",
            "4000",
            "--max-output-cost",
            "10.0",
        ]
    )
    assert rc_caps == 0

    # rank with --min-output and --max-output-cost
    rc_rank_caps = main(
        [
            "rank",
            "--snapshot",
            str(snap_path),
            "--min-output",
            "4000",
            "--max-output-cost",
            "10.0",
        ]
    )
    assert rc_rank_caps == 0


def test_parse_select_rank_new_options():
    select_args = parse_args(
        [
            "select",
            "--max-burn",
            "2.5",
            "--tokens",
            "1000,20000,500",
            "--weights",
            "0.5,0.5",
        ]
    )
    assert select_args.max_burn == 2.5
    assert select_args.tokens == (1000, 20000, 500)
    assert select_args.weights == (0.5, 0.5)

    rank_args = parse_args(
        [
            "rank",
            "--max-burn",
            "1",
            "--tokens",
            "1,2,3",
            "--weights",
            "1,0",
        ]
    )
    assert rank_args.max_burn == 1.0
    assert rank_args.tokens == (1, 2, 3)
    assert rank_args.weights == (1.0, 0.0)


def test_parse_helpers_reject_invalid_values():
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_token_mix("a,2,3")
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_token_mix("1,2")
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_token_mix("-1,2,3")

    with pytest.raises(argparse.ArgumentTypeError):
        _parse_weights("a,2")
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_weights("1")

    with pytest.raises(SystemExit):
        parse_args(["select", "--tokens", "1,2"])


def test_cli_custom_tokens_weights_and_max_burn(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)

    rc_json = main(
        [
            "select",
            "--format",
            "json",
            "--snapshot",
            str(snap_path),
            "--explain",
            "--tokens",
            "2000,120000,1000",
            "--weights",
            "0.5,0.5",
        ]
    )
    assert rc_json == 0
    data = json.loads(capsys.readouterr().out)
    assert "explanations" in data

    rc_table = main(
        [
            "select",
            "--format",
            "table",
            "--snapshot",
            str(snap_path),
            "--explain",
            "--tokens",
            "2000,120000,1000",
            "--weights",
            "0.5,0.5",
        ]
    )
    assert rc_table == 0
    assert "Burn %" in capsys.readouterr().out

    rc_rank = main(
        [
            "rank",
            "--format",
            "json",
            "--snapshot",
            str(snap_path),
            "--max-burn",
            "50",
            "--tokens",
            "2000,120000,1000",
            "--weights",
            "0.5,0.5",
        ]
    )
    assert rc_rank == 0
    assert capsys.readouterr().out

    rc_rank_empty = main(
        [
            "rank",
            "--snapshot",
            str(snap_path),
            "--max-burn",
            "0",
        ]
    )
    assert rc_rank_empty == 0


def test_cli_diff_table_json_and_exit_codes(tmp_path, capsys):
    old_path = _create_temp_snapshot(tmp_path)
    old_snap = load_snapshot(old_path)
    new_snap = old_snap.model_copy(deep=True)
    new_snap.fetched_at = "2026-02-01T00:00:00Z"
    new_snap.models[0].status = "deprecated"
    new_snap.models[0].monthly_allowance = 999.0
    new_path = tmp_path / "new_snapshot.json"
    save_snapshot(new_snap, new_path)

    rc_table = main(["diff", "--old", str(old_path), "--new", str(new_path)])
    assert rc_table == 0
    table_out = capsys.readouterr().out
    assert "Snapshot diff (plan: go)" in table_out
    assert "Status changes:" in table_out

    rc_json = main(["diff", "--old", str(old_path), "--new", str(new_path), "--format", "json"])
    assert rc_json == 0
    diff_data = json.loads(capsys.readouterr().out)
    assert diff_data["status_changes"]["qwen3.8-max"] == {
        "old": "active",
        "new": "deprecated",
    }

    rc_fail = main(["diff", "--old", str(old_path), "--new", str(new_path), "--fail-on-change"])
    assert rc_fail == 2
    capsys.readouterr()

    rc_same = main(
        [
            "diff",
            "--old",
            str(old_path),
            "--new",
            str(old_path),
            "--exit-code",
        ]
    )
    assert rc_same == 0
    assert "No catalog or tier changes detected." in capsys.readouterr().out

    out_path = tmp_path / "diff.txt"
    rc_out = main(
        [
            "diff",
            "--old",
            str(old_path),
            "--new",
            str(new_path),
            "-o",
            str(out_path),
        ]
    )
    assert rc_out == 0
    assert "Snapshot diff" in out_path.read_text(encoding="utf-8")


def test_cli_diff_defaults_and_missing_reference(tmp_path, capsys):
    old_path = _create_temp_snapshot(tmp_path)
    new_snap = load_snapshot(old_path)
    new_snap.fetched_at = "2026-03-01T00:00:00Z"

    with patch(
        "opencode_go_model_optimizer.cli._load_or_fetch_snapshot",
        return_value=LoadedSnapshot(new_snap, "live"),
    ):
        rc = main(
            [
                "diff",
                "--old",
                str(old_path),
                "--plan",
                "go-plus",
                "--mode",
                "allowance",
                "--profile",
                "coding",
            ]
        )
    assert rc == 0
    assert "plan: go-plus" in capsys.readouterr().out

    with patch(
        "opencode_go_model_optimizer.cli.load_reference_snapshot",
        return_value=None,
    ):
        rc_missing = main(["diff", "--new", str(old_path)])
    assert rc_missing == 1
    assert "no reference snapshot available" in capsys.readouterr().err


def test_prefer_validation_errors(capsys):
    for bad in ("nope", "bogus=x", "heavy="):
        with pytest.raises(SystemExit) as exc:
            main(["select", "--prefer", bad])
        assert exc.value.code == 2
    err = capsys.readouterr().err
    assert "TIER=MODEL_ID" in err
    assert "unknown tier" in err
    assert "missing a model id" in err


def test_prefer_unknown_model_warns(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    rc = main(["select", "--snapshot", str(snap_path), "--prefer", "heavy=does-not-exist"])
    assert rc == 0
    cap = capsys.readouterr()
    assert (
        "opencode-go-model-optimizer: warning: preferred model 'does-not-exist' "
        "for tier 'heavy' not found; ignoring"
    ) in cap.err
    json.loads(cap.out)


def test_prefer_full_id_is_honored_without_warning(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    rc = main(
        [
            "select",
            "--snapshot",
            str(snap_path),
            "--prefer",
            "heavy=opencode-go/deepseek-v4.1-flash",
        ]
    )
    assert rc == 0
    cap = capsys.readouterr()
    assert "warning" not in cap.err
    assert json.loads(cap.out)["tiers"]["heavy"] == "opencode-go/deepseek-v4.1-flash"


def test_prefer_bare_id_is_honored_without_warning(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    rc = main(["select", "--snapshot", str(snap_path), "--prefer", "heavy=deepseek-v4.1-flash"])
    assert rc == 0
    cap = capsys.readouterr()
    assert "warning" not in cap.err
    assert json.loads(cap.out)["tiers"]["heavy"] == "opencode-go/deepseek-v4.1-flash"


def test_prefer_full_id_unknown_or_skipped_warns(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    rc = main(
        [
            "select",
            "--snapshot",
            str(snap_path),
            "--skip",
            "opencode-go/deepseek-v4.1-flash",
            "--prefer",
            "heavy=opencode-go/deepseek-v4.1-flash",
            "--prefer",
            "light=opencode-go/does-not-exist",
        ]
    )
    assert rc == 0
    err = capsys.readouterr().err
    assert "preferred model 'opencode-go/deepseek-v4.1-flash' for tier 'heavy'" in err
    assert "preferred model 'opencode-go/does-not-exist' for tier 'light'" in err


def test_cache_ttl_env_invalid(monkeypatch, capsys):
    from opencode_go_model_optimizer.cli import _env_cache_ttl

    monkeypatch.setenv("OPENCODE_GO_CACHE_TTL", "abc")
    assert _env_cache_ttl() == 0
    assert "invalid OPENCODE_GO_CACHE_TTL" in capsys.readouterr().err
    monkeypatch.setenv("OPENCODE_GO_CACHE_TTL", "-5")
    assert _env_cache_ttl() == 0
    assert "invalid" in capsys.readouterr().err
    monkeypatch.setenv("OPENCODE_GO_CACHE_TTL", "60")
    assert _env_cache_ttl() == 60
    assert capsys.readouterr().err == ""


def test_live_fetch_failure_warns_and_uses_reference(monkeypatch, capsys):
    def boom(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("opencode_go_model_optimizer.cli.fetch_and_build_snapshot", boom)
    rc = main(["select"])
    cap = capsys.readouterr()
    assert rc == 0
    assert "warning: live fetch failed (ConnectError: offline); using bundled reference" in cap.err
    json.loads(cap.out)


def test_main_error_handling(tmp_path, capsys, monkeypatch):
    rc = main(["select", "--snapshot", str(tmp_path / "missing.json")])
    assert rc == 1
    assert "opencode-go-model-optimizer: error: FileNotFoundError" in capsys.readouterr().err

    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    rc = main(["rank", "--snapshot", str(bad)])
    err = capsys.readouterr().err
    assert rc == 1
    assert err.startswith("opencode-go-model-optimizer: error:")
    assert err.count("\n") == 1

    def http_boom(args):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("opencode_go_model_optimizer.cli.handle_fetch", http_boom)
    assert main(["fetch"]) == 1
    assert "ConnectError: down" in capsys.readouterr().err

    def interrupt(args):
        raise KeyboardInterrupt

    monkeypatch.setattr("opencode_go_model_optimizer.cli.handle_fetch", interrupt)
    assert main(["fetch"]) == 130


def test_invalid_cache_ttl_env_warns_once(monkeypatch, capsys):
    monkeypatch.setenv("OPENCODE_GO_CACHE_TTL", "abc")
    ref = "src/opencode_go_model_optimizer/data/reference_snapshot.json"
    assert main(["select", "--snapshot", ref, "--format", "compact"]) == 0
    assert capsys.readouterr().err.count("invalid OPENCODE_GO_CACHE_TTL") == 1


def test_select_evaluates_pareto_once(monkeypatch, capsys):
    from opencode_go_model_optimizer.engine import tiers

    calls = []
    real = tiers.evaluate_pareto
    monkeypatch.setattr(tiers, "evaluate_pareto", lambda *a, **k: calls.append(1) or real(*a, **k))
    ref = "src/opencode_go_model_optimizer/data/reference_snapshot.json"
    assert main(["select", "--snapshot", ref, "--mode", "pareto", "--top-n", "3"]) == 0
    capsys.readouterr()
    assert len(calls) == 1


def test_write_output_is_atomic(tmp_path, monkeypatch):
    import os

    from opencode_go_model_optimizer.cli import _write_output

    target = tmp_path / "sub" / "out.txt"
    target.parent.mkdir()
    target.write_text("old\n")
    seen = []
    real_replace = os.replace

    def spy(src, dst):
        seen.append((Path(dst).read_text(), Path(src).read_text()))
        real_replace(src, dst)

    monkeypatch.setattr(os, "replace", spy)
    _write_output("new", str(target))
    assert seen == [("old\n", "new\n")]
    assert target.read_text() == "new\n"
    assert [p.name for p in target.parent.iterdir()] == ["out.txt"]


def test_version_flag(capsys):
    with pytest.raises(SystemExit) as exc:
        parse_args(["--version"])
    assert exc.value.code == 0
    assert capsys.readouterr().out.startswith("opencode-go-model-optimizer ")


def test_help_epilog_lists_exit_codes(capsys):
    with pytest.raises(SystemExit):
        parse_args(["--help"])
    out = capsys.readouterr().out
    assert "exit codes:" in out
    assert "130  interrupted" in out


def test_snapshot_source_tracking(tmp_path):
    snap_path = _create_temp_snapshot(tmp_path)
    assert _load_or_fetch_snapshot(str(snap_path)).source == "file"

    dummy = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="go",
        models=[NormalizedModel(id="m1")],
        metadata=SnapshotMetadata(),
    )
    with patch("opencode_go_model_optimizer.cli.fetch_and_build_snapshot", return_value=dummy):
        assert _load_or_fetch_snapshot().source == "live"
    with patch("opencode_go_model_optimizer.cli.load_cached_snapshot", return_value=dummy):
        assert _load_or_fetch_snapshot(cache_ttl=60).source == "cache"
    with (
        patch(
            "opencode_go_model_optimizer.cli.fetch_and_build_snapshot",
            side_effect=httpx.ConnectError("down"),
        ),
        patch("opencode_go_model_optimizer.cli.load_reference_snapshot", return_value=dummy),
    ):
        assert _load_or_fetch_snapshot().source == "reference"


def test_select_json_snapshot_provenance(tmp_path, capsys):
    snap_path = _create_temp_snapshot(tmp_path)
    assert main(["select", "--snapshot", str(snap_path), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert list(data)[:5] == ["plan", "tiers", "evaluated_at", "mode", "snapshot"]
    assert data["snapshot"]["source"] == "file"
    assert data["snapshot"]["fetched_at"] == load_snapshot(snap_path).fetched_at

    assert main(["rank", "--snapshot", str(snap_path), "--format", "json"]) == 0
    assert isinstance(json.loads(capsys.readouterr().out), list)


def test_select_strict_empty_tier(tmp_path, capsys):
    snap = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="go",
        models=[NormalizedModel(id="only", monthly_allowance=10.0)],
        metadata=SnapshotMetadata(),
    )
    path = tmp_path / "one.json"
    save_snapshot(snap, path)
    args = ["select", "--snapshot", str(path), "--skip", "only", "--format", "compact"]
    assert main(args) == 0
    capsys.readouterr()
    assert main([*args, "--strict"]) == 3
    captured = capsys.readouterr()
    assert (
        "opencode-go-model-optimizer: error: no model available for tier(s): "
        "heavy, balanced, light, free"
    ) in captured.err
