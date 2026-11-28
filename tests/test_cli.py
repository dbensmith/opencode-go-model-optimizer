"""Tests for opencode-go-model-optimizer CLI."""

import argparse
import json
import math
import runpy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from opencode_go_model_optimizer.cli import (
    LoadedSnapshot,
    SnapshotUnavailableError,
    _age,
    _load_or_fetch_models,
    _load_or_fetch_snapshot,
    _parse_preferred_tiers,
    _parse_token_mix,
    _parse_weights,
    main,
    parse_args,
)
from opencode_go_model_optimizer.engine.tiers import resolve_mode
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_http
from opencode_go_model_optimizer.ingest.models import (
    NormalizedModel,
    Snapshot,
    SnapshotMetadata,
)
from opencode_go_model_optimizer.ingest.snapshot import (
    get_cache_snapshot_path,
    load_snapshot,
    save_cached_snapshot,
    save_snapshot,
)
from tests.conftest import CATALOGUE_HTTP_DATA, FIXTURES_DIR, create_temp_snapshot
from tests.factories import make_model


@pytest.fixture
def go_only_snapshot(tmp_path: Path) -> Path:
    snap = Snapshot(
        fetched_at="2026-01-01T00:00:00Z",
        plan="go",
        models=[
            NormalizedModel(
                id="paid",
                cost={"input": 1.0, "output": 2.0},
                limit={"context": 128000, "output": 8192},
                plan_allowances={"go": 15.0},
            )
        ],
    )
    path = tmp_path / "go_only.json"
    save_snapshot(snap, path)
    return path


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
            "heavy=zeta-pro",
            "--min-context",
            "128000",
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
    assert args.prefer == ["heavy=zeta-pro"]
    assert args.min_context == 128000
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
            "--profile",
            "fast",
        ]
    )
    assert args.command == "rank"
    assert args.cache_ttl == 600
    assert args.skip == ["model-c"]
    assert args.min_context == 200000
    assert args.profile == "fast"


def test_parse_preferred_tiers_helper():
    assert _parse_preferred_tiers(None) == {}
    assert _parse_preferred_tiers([]) == {}

    parsed = _parse_preferred_tiers(
        [
            "heavy=zeta-pro",
            "balanced = beta-flash ",
            "invalid_format",
            "=no_key",
            "no_val=",
        ]
    )
    assert parsed == {
        "heavy": "zeta-pro",
        "balanced": "beta-flash",
    }


def test_main_no_args(capsys):
    rc = main([])
    assert rc == 0
    captured = capsys.readouterr()
    assert "use --help" in captured.err


def test_cli_select_json(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    rc = main(["select", "--format", "json", "--snapshot", str(snap_path)])
    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert "tiers" in data
    assert data["plan"] == "go"
    assert data["tiers"]["heavy"] == "opencode-go/alpha-max"
    assert data["tiers"]["balanced"] == "opencode-go/beta-pro"
    assert data["tiers"]["light"] == "opencode-go/beta-flash"
    assert data["tiers"]["free"] == "opencode-go/epsilon-free"
    assert data["names"]["heavy"] == "Alpha Max"
    assert data["names"]["balanced"] == "Beta Pro"
    assert data["names"]["light"] == "Beta Flash 1101"
    assert data["names"]["free"] == "Epsilon Free"
    assert data["mode"] == "allowance"


def test_cli_select_table(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    rc = main(["select", "--format", "table", "--snapshot", str(snap_path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Tier" in captured.out
    assert "Model Name" in captured.out
    assert "heavy" in captured.out
    assert "opencode-go/alpha-max" in captured.out
    assert "Alpha Max" in captured.out


def test_cli_select_env_and_github_output(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)

    # --format env
    rc = main(["select", "--format", "env", "--snapshot", str(snap_path)])
    assert rc == 0
    env_out = capsys.readouterr().out
    assert 'OPENCODE_MODEL_HEAVY="opencode-go/alpha-max"' in env_out
    assert 'OPENCODE_MODEL_FREE="opencode-go/epsilon-free"' in env_out
    assert 'OPENCODE_MODEL_HEAVY_NAME="Alpha Max"' in env_out
    assert 'OPENCODE_MODEL_FREE_NAME="Epsilon Free"' in env_out

    # --format github-output
    rc = main(["select", "--format", "github-output", "--snapshot", str(snap_path)])
    assert rc == 0
    gh_out = capsys.readouterr().out
    assert "heavy=opencode-go/alpha-max" in gh_out
    assert "free=opencode-go/epsilon-free" in gh_out
    assert "heavy_name=Alpha Max" in gh_out
    assert "free_name=Epsilon Free" in gh_out


def test_cli_select_filters_and_overrides(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)

    # Test --skip, --prefer, --min-context
    rc = main(
        [
            "select",
            "--format",
            "json",
            "--snapshot",
            str(snap_path),
            "--skip",
            "opencode-go/alpha-max",
            "--prefer",
            "heavy=opencode-go/gamma-flash",
            "--min-context",
            "100000",
        ]
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    assert data["tiers"]["heavy"] == "opencode-go/gamma-flash"
    assert data["names"]["heavy"] == "Gamma-Flash"
    assert data["tiers"]["free"] == "opencode-go/epsilon-free"


def test_select_json_includes_settings(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    rc = main(
        [
            "select",
            "--format",
            "json",
            "--snapshot",
            str(snap_path),
            "--allow-global-region",
            "--allow-free-training",
            "--max-retention-days",
            "30",
            "--require-zdr",
            "--min-context",
            "1000",
        ]
    )
    assert rc == 0
    data = json.loads(capsys.readouterr().out)
    settings = data["settings"]
    assert settings["allow_global_region"] is True
    assert settings["allow_paid_training"] is False
    assert settings["allow_free_training"] is True
    assert settings["max_retention_days"] == 30
    assert settings["require_zdr"] is True
    assert settings["min_context"] == 1000
    assert settings["mode"] == "allowance"


def test_cli_rank_filters(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)

    rc = main(
        [
            "rank",
            "--format",
            "json",
            "--snapshot",
            str(snap_path),
            "--skip",
            "alpha-max",
            "--min-context",
            "120000",
        ]
    )
    assert rc == 0
    models = json.loads(capsys.readouterr().out)
    assert all(m["id"] != "alpha-max" for m in models)
    assert all(m["limit"]["context"] >= 120000 for m in models)
    free_models = [m for m in models if m.get("tier") == "free"]
    assert len(free_models) == 1
    assert free_models[0]["id"] == "epsilon-free"


def test_cli_select_plan_switch(tmp_path):
    mdx_text = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
    out_snap_go = tmp_path / "snap_go.json"
    out_snap_plus = tmp_path / "snap_plus.json"

    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=parse_catalogue_http(CATALOGUE_HTTP_DATA),
        ),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=mdx_text),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev", return_value={}),
    ):
        main(["fetch", "--plan", "go", "--out", str(out_snap_go)])
        main(["fetch", "--plan", "go-plus", "--out", str(out_snap_plus)])

    snap_go = json.loads(out_snap_go.read_text(encoding="utf-8"))
    snap_plus = json.loads(out_snap_plus.read_text(encoding="utf-8"))

    assert snap_go["plan"] == "go"
    assert snap_plus["plan"] == "go-plus"

    # Gamma-Flash allowance is $50 under go, but $180 under go-plus in the fixture
    gamma_go = next(m for m in snap_go["models"] if m["id"] == "gamma-flash")
    gamma_plus = next(m for m in snap_plus["models"] if m["id"] == "gamma-flash")
    assert gamma_go["monthly_allowance"] == 50.0
    assert gamma_plus["monthly_allowance"] == 180.0


def test_cli_select_compact(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    rc = main(["select", "--format", "compact", "--snapshot", str(snap_path)])
    assert rc == 0
    captured = capsys.readouterr()
    lines = captured.out.strip().splitlines()
    assert "heavy opencode-go/alpha-max Alpha Max" in lines
    assert "balanced opencode-go/beta-pro Beta Pro" in lines
    assert "light opencode-go/beta-flash Beta Flash 1101" in lines
    assert "free opencode-go/epsilon-free Epsilon Free" in lines


def test_cli_select_profile_and_explain(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)

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
    assert data["explanations"]["heavy"]["model_id"] == "opencode-go/alpha-max"
    assert data["explanations"]["heavy"]["name"] == "Alpha Max"
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
    assert "Model Name" in tbl_out
    assert "Alpha Max" in tbl_out
    assert "IQ" in tbl_out
    assert "Turn Cost" in tbl_out
    assert "Burn %" in tbl_out
    assert "BPE" in tbl_out


def test_cli_multiplan_offline_snapshot(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    snap = Snapshot.model_validate_json(snap_path.read_text(encoding="utf-8"))

    # Add plan_allowances to Gamma
    for m in snap.models:
        if m.id == "gamma-flash":
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
            "heavy=opencode-go/gamma-flash",
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


def test_cli_select_plan_fallback_reapplies_effective_plan(go_only_snapshot, capsys):
    args = [
        "select",
        "--snapshot",
        str(go_only_snapshot),
        "--mode",
        "allowance",
        "--format",
        "json",
    ]
    assert main([*args, "--plan", "go-plus"]) == 0
    fallback = json.loads(capsys.readouterr().out)
    assert main([*args, "--plan", "go"]) == 0
    direct = json.loads(capsys.readouterr().out)

    assert fallback["plan"] == direct["plan"] == "go"
    assert fallback["tiers"] == direct["tiers"]
    assert fallback["tiers"]["heavy"] == "opencode-go/paid"


def test_cli_rank_plan_fallback_reapplies_effective_plan(go_only_snapshot, capsys):
    args = [
        "rank",
        "--snapshot",
        str(go_only_snapshot),
        "--mode",
        "allowance",
        "--format",
        "json",
    ]
    assert main([*args, "--plan", "go-plus"]) == 0
    fallback = json.loads(capsys.readouterr().out)
    assert main([*args, "--plan", "go"]) == 0
    direct = json.loads(capsys.readouterr().out)

    assert fallback == direct
    assert fallback[0]["monthly_allowance"] == 15.0
    assert fallback[0]["tier"] == "heavy"


def test_cli_select_output_file(tmp_path):
    snap_path = create_temp_snapshot(tmp_path)
    out_file = tmp_path / "selected.json"
    rc = main(["select", "--format", "json", "--snapshot", str(snap_path), "-o", str(out_file)])
    assert rc == 0
    assert out_file.exists()
    data = json.loads(out_file.read_text(encoding="utf-8"))
    assert "tiers" in data


def test_cli_rank_table(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    rc = main(["rank", "--format", "table", "--snapshot", str(snap_path)])
    assert rc == 0
    captured = capsys.readouterr()
    assert "Rank" in captured.out
    assert "beta-flash" in captured.out


def test_cli_rank_json(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    rc = main(["rank", "--format", "json", "--snapshot", str(snap_path)])
    assert rc == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert isinstance(data, list)
    assert len(data) > 0


def test_cli_fetch(tmp_path):
    mdx_text = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
    out_snap = tmp_path / "fetched_snapshot.json"

    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=parse_catalogue_http(CATALOGUE_HTTP_DATA),
        ),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=mdx_text),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev", return_value={}),
    ):
        rc = main(["fetch", "--out", str(out_snap)])
        assert rc == 0
        assert out_snap.exists()
        data = json.loads(out_snap.read_text(encoding="utf-8"))
        assert data["schema_version"] == 1
        assert len(data["models"]) == 14


def test_cli_fetch_update_reference_flag_removed():
    with pytest.raises(SystemExit) as exc:
        parse_args(["fetch", "--update-reference"])
    assert exc.value.code == 2


def testresolve_mode():
    m_with_iq = make_model("m1", aa_iq=60.0)
    m_with_coding = make_model("m2", aa_coding=75.0)
    m_no_bench = make_model("m3")
    m_inactive = make_model("m4", aa_iq=80.0, status="deprecated")

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
    snap_path = create_temp_snapshot(tmp_path)
    models, plan = _load_or_fetch_models(str(snap_path))
    assert len(models) == 14
    assert plan == "go"

    # Live fetch succeeds
    dummy_snap = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="go-plus",
        models=[make_model("m1")],
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
        models=[make_model("m1", plan_allowances={"go-plus": 60.0})],
        metadata=SnapshotMetadata(),
    )
    multi_path = tmp_path / "snap_multi.json"
    save_snapshot(snap_multi, multi_path)
    models, plan = _load_or_fetch_models(str(multi_path), plan="go-plus")
    assert plan == "go-plus"

    # Live fetch fails with a network error: the consumer cache (any age) is used
    stale_with_allowances = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="go",
        models=[make_model("cached1", plan_allowances={"go-plus": 100.0})],
        metadata=SnapshotMetadata(),
    )
    with (
        patch(
            "opencode_go_model_optimizer.cli.fetch_and_build_snapshot",
            side_effect=httpx.ConnectError("Connection failed"),
        ),
        patch(
            "opencode_go_model_optimizer.cli.load_cached_snapshot",
            return_value=stale_with_allowances,
        ) as mock_stale,
    ):
        models, plan = _load_or_fetch_models(plan="go-plus")
        assert plan == "go-plus"
        assert models[0].monthly_allowance == 100.0
        mock_stale.assert_called_once_with(plan="go-plus", ttl_seconds=math.inf)

    # Live fetch fails and there is no cache -> clear error pointing at --snapshot / fetch
    with (
        patch(
            "opencode_go_model_optimizer.cli.fetch_and_build_snapshot",
            side_effect=httpx.ConnectError("Connection failed"),
        ),
        patch("opencode_go_model_optimizer.cli.load_cached_snapshot", return_value=None),
        pytest.raises(SnapshotUnavailableError, match=r"--snapshot FILE.*'fetch'"),
    ):
        _load_or_fetch_models()


def test_handle_rank_modes_and_formats(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)

    # --format table is the default and can be explicit
    rc = main(["rank", "--format", "table", "--snapshot", str(snap_path)])
    assert rc == 0
    out = capsys.readouterr().out
    assert "Rank" in out
    assert "Model ID" in out

    # --format table with pareto mode fails without benchmarks unless --allow-fallback is passed
    rc_no_bm = main(
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
    assert rc_no_bm == 1
    assert "--allow-fallback" in capsys.readouterr().err

    # --format table with pareto mode and benchmarked snapshot succeeds
    bench_snap = load_snapshot(snap_path)
    for idx, m in enumerate(bench_snap.models):
        m.aa_iq = 60.0 - idx
    bench_path = tmp_path / "bench_snap.json"
    save_snapshot(bench_snap, bench_path)

    rc = main(
        [
            "rank",
            "--format",
            "table",
            "--mode",
            "pareto",
            "--snapshot",
            str(bench_path),
        ]
    )
    assert rc == 0
    cap_pareto = capsys.readouterr()
    assert "Calculation method: PARETO" in cap_pareto.out
    assert "METHOD=PARETO" in cap_pareto.err
    assert "BPE" in cap_pareto.out
    assert "IQ" in cap_pareto.out

    # --format json with pareto mode
    rc = main(
        [
            "rank",
            "--format",
            "json",
            "--mode",
            "pareto",
            "--snapshot",
            str(bench_path),
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
    snap_path = create_temp_snapshot(tmp_path)

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
    snap_path = create_temp_snapshot(tmp_path)

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
    old_path = create_temp_snapshot(tmp_path)
    old_snap = load_snapshot(old_path)
    new_snap = old_snap.model_copy(deep=True)
    new_snap.fetched_at = "2026-02-01T00:00:00Z"
    new_snap.models[0].status = "deprecated"
    new_snap.models[0].monthly_allowance = 999.0
    new_path = tmp_path / "new_snapshot.json"
    save_snapshot(new_snap, new_path)

    rc_table = main(["diff", "--old", str(old_path), "--new", str(new_path)])
    assert rc_table == 0
    cap_diff = capsys.readouterr()
    assert "Snapshot diff (plan: go, method: ALLOWANCE)" in cap_diff.out
    assert "METHOD=ALLOWANCE" in cap_diff.err
    assert "Status changes:" in cap_diff.out

    rc_json = main(["diff", "--old", str(old_path), "--new", str(new_path), "--format", "json"])
    assert rc_json == 0
    diff_data = json.loads(capsys.readouterr().out)
    assert diff_data["status_changes"]["alpha-max"] == {
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
            "--fail-on-change",
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


def test_cli_diff_default_new_and_required_old(tmp_path, capsys):
    old_path = create_temp_snapshot(tmp_path)
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

    with pytest.raises(SystemExit) as exc:
        main(["diff", "--new", str(old_path)])
    assert exc.value.code == 2
    assert "--old" in capsys.readouterr().err


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
    snap_path = create_temp_snapshot(tmp_path)
    rc = main(["select", "--snapshot", str(snap_path), "--prefer", "heavy=does-not-exist"])
    assert rc == 0
    cap = capsys.readouterr()
    assert (
        "opencode-go-model-optimizer: warning: preferred model 'does-not-exist' "
        "for tier 'heavy' not found; ignoring"
    ) in cap.err
    json.loads(cap.out)


def test_prefer_full_id_is_honored_without_warning(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    rc = main(
        [
            "select",
            "--snapshot",
            str(snap_path),
            "--prefer",
            "heavy=opencode-go/beta-flash",
        ]
    )
    assert rc == 0
    cap = capsys.readouterr()
    assert "warning" not in cap.err
    assert json.loads(cap.out)["tiers"]["heavy"] == "opencode-go/beta-flash"


def test_prefer_bare_id_is_honored_without_warning(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    rc = main(["select", "--snapshot", str(snap_path), "--prefer", "heavy=beta-flash"])
    assert rc == 0
    cap = capsys.readouterr()
    assert "warning" not in cap.err
    assert json.loads(cap.out)["tiers"]["heavy"] == "opencode-go/beta-flash"


def test_prefer_full_id_unknown_or_skipped_warns(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    rc = main(
        [
            "select",
            "--snapshot",
            str(snap_path),
            "--skip",
            "opencode-go/beta-flash",
            "--prefer",
            "heavy=opencode-go/beta-flash",
            "--prefer",
            "light=opencode-go/does-not-exist",
        ]
    )
    assert rc == 0
    cap = capsys.readouterr()
    err = cap.err
    assert "preferred model 'opencode-go/beta-flash' for tier 'heavy'" in err
    assert "preferred model 'opencode-go/does-not-exist' for tier 'light'" in err
    assert json.loads(cap.out)["tiers"]["heavy"] != "opencode-go/beta-flash"


def test_prefer_pin_excluded_by_cost_or_deprecated_warns(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    rc = main(
        [
            "select",
            "--snapshot",
            str(snap_path),
            "--max-output-cost",
            "5.0",
            "--prefer",
            "heavy=alpha-max",
            "--prefer",
            "balanced=deprecated-old-model",
        ]
    )
    assert rc == 0
    cap = capsys.readouterr()
    err = cap.err
    assert "preferred model 'alpha-max' for tier 'heavy' not found; ignoring" in err
    assert "preferred model 'deprecated-old-model' for tier 'balanced' not found; ignoring" in err
    out = json.loads(cap.out)["tiers"]
    assert out["heavy"] != "opencode-go/alpha-max"
    assert out["balanced"] != "opencode-go/deprecated-old-model"


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


def _offline(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("offline")

    monkeypatch.setattr("opencode_go_model_optimizer.cli.fetch_and_build_snapshot", boom)


def test_live_fetch_failure_warns_and_uses_consumer_cache(tmp_path, monkeypatch, capsys):
    save_cached_snapshot(load_snapshot(create_temp_snapshot(tmp_path)))
    _offline(monkeypatch)
    rc = main(["select"])  # no --cache-ttl: the cache is used regardless of age
    cap = capsys.readouterr()
    assert rc == 0
    assert "warning: live fetch failed (ConnectError: offline); using local cache fetched at" in (
        cap.err
    )
    assert " old)" in cap.err
    assert json.loads(cap.out)["snapshot"]["source"] == "stale-cache"


def test_live_fetch_failure_without_cache_suggests_snapshot(monkeypatch, capsys):
    _offline(monkeypatch)
    assert main(["select"]) == 1
    err = capsys.readouterr().err
    assert "live fetch failed (ConnectError: offline) and no local cache exists" in err
    assert "--snapshot FILE" in err and "'fetch'" in err


def test_successful_live_fetch_populates_consumer_cache(tmp_path, monkeypatch):
    snap = load_snapshot(create_temp_snapshot(tmp_path))
    monkeypatch.setattr(
        "opencode_go_model_optimizer.cli.fetch_and_build_snapshot", lambda **k: snap
    )
    assert main(["select", "-o", str(tmp_path / "out.json")]) == 0
    assert get_cache_snapshot_path("go").exists()


def test_age_formatting():
    now = datetime.now(UTC)
    assert _age((now - timedelta(days=3, hours=4)).isoformat()) == "3d 4h"
    assert _age((now - timedelta(minutes=90)).isoformat()) == "1h 30m"
    assert _age((now - timedelta(minutes=5)).replace(tzinfo=None).isoformat()) == "0h 5m"
    assert _age("garbage") == "unknown"


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


def test_invalid_cache_ttl_env_warns_once(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("OPENCODE_GO_CACHE_TTL", "abc")
    ref = str(create_temp_snapshot(tmp_path))
    assert main(["select", "--snapshot", ref, "--format", "compact"]) == 0
    assert capsys.readouterr().err.count("invalid OPENCODE_GO_CACHE_TTL") == 1


def test_select_evaluates_pareto_once(tmp_path, monkeypatch, capsys):
    from opencode_go_model_optimizer.engine import tiers

    calls = []
    real = tiers.evaluate_pareto
    monkeypatch.setattr(tiers, "evaluate_pareto", lambda *a, **k: calls.append(1) or real(*a, **k))
    snap = load_snapshot(create_temp_snapshot(tmp_path))
    for idx, m in enumerate(snap.models):
        m.aa_iq = 40.0 + idx
    ref = tmp_path / "bench.json"
    save_snapshot(snap, ref)
    assert main(["select", "--snapshot", str(ref), "--mode", "pareto", "--top-n", "3"]) == 0
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
    snap_path = create_temp_snapshot(tmp_path)
    assert _load_or_fetch_snapshot(str(snap_path)).source == "file"

    dummy = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="go",
        models=[make_model("m1")],
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
        patch("opencode_go_model_optimizer.cli.load_cached_snapshot", return_value=dummy),
    ):
        assert _load_or_fetch_snapshot().source == "stale-cache"


def test_select_json_snapshot_provenance(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)
    assert main(["select", "--snapshot", str(snap_path), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert list(data)[:9] == [
        "plan",
        "tiers",
        "names",
        "evaluated_at",
        "mode",
        "method",
        "fallback",
        "requested_mode",
        "snapshot",
    ]
    assert data["method"] == "allowance"
    assert data["fallback"] is False
    assert data["requested_mode"] == "auto"
    assert data["snapshot"]["source"] == "file"
    assert data["snapshot"]["fetched_at"] == load_snapshot(snap_path).fetched_at

    assert main(["rank", "--snapshot", str(snap_path), "--format", "json"]) == 0
    assert isinstance(json.loads(capsys.readouterr().out), list)


def test_select_strict_empty_tier(tmp_path, capsys):
    snap = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="go",
        models=[make_model("only", monthly_allowance=10.0)],
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


def test_parse_at_utc_normalization_and_validation():
    naive = parse_args(["select", "--at", "2026-06-01T12:00:00"]).at
    assert naive == datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    offset = parse_args(["select", "--at", "2026-06-01T14:00:00+02:00"]).at
    assert offset == datetime(2026, 6, 1, 12, 0, tzinfo=UTC)

    with pytest.raises(SystemExit):
        parse_args(["select", "--at", "not-a-timestamp"])


def test_cli_pareto_fallback_is_opt_in_and_loudly_announced(tmp_path, capsys):
    snap_path = create_temp_snapshot(tmp_path)

    # Default --mode pareto without benchmarks fails loudly instead of silently falling back.
    assert main(["select", "--snapshot", str(snap_path), "--mode", "pareto"]) == 1
    err = capsys.readouterr().err
    assert "BenchmarksUnavailableError" in err
    assert "--allow-fallback" in err

    # With --allow-fallback, JSON reports the actual method ('allowance') and fallback=True,
    # and stderr loudly announces the fallback.
    assert (
        main(
            [
                "select",
                "--snapshot",
                str(snap_path),
                "--mode",
                "pareto",
                "--allow-fallback",
                "--format",
                "json",
            ]
        )
        == 0
    )
    cap_json = capsys.readouterr()
    payload = json.loads(cap_json.out)
    assert payload["mode"] == "allowance"
    assert payload["method"] == "allowance"
    assert payload["fallback"] is True
    assert payload["requested_mode"] == "pareto"
    assert payload["settings"]["mode"] == "allowance"
    assert payload["settings"]["allow_fallback"] is True
    assert "WARNING: FALLBACK ACTIVE" in cap_json.err
    assert "METHOD=ALLOWANCE" in cap_json.err

    # Table output loudly displays the fallback banner.
    assert (
        main(
            [
                "select",
                "--snapshot",
                str(snap_path),
                "--mode",
                "pareto",
                "--allow-fallback",
                "--format",
                "table",
            ]
        )
        == 0
    )
    cap_tbl = capsys.readouterr()
    assert "Calculation method: ALLOWANCE [FALLBACK from PARETO" in cap_tbl.out
    assert "WARNING: FALLBACK ACTIVE" in cap_tbl.err


def test_cli_auto_mode_fails_on_aa_error_unless_allow_fallback(tmp_path, capsys):
    snap = load_snapshot(create_temp_snapshot(tmp_path))
    snap.metadata.benchmark_error = "Artificial Analysis returned HTTP 429"
    snap_path = tmp_path / "aa_err.json"
    save_snapshot(snap, snap_path)

    # Auto mode must not silently fall back when AA was requested and failed.
    assert main(["select", "--snapshot", str(snap_path), "--mode", "auto"]) == 1
    err = capsys.readouterr().err
    assert "HTTP 429" in err
    assert "--allow-fallback" in err

    # With --allow-fallback, auto mode falls back loudly and embeds the AA error in stderr/JSON.
    assert (
        main(
            [
                "select",
                "--snapshot",
                str(snap_path),
                "--mode",
                "auto",
                "--allow-fallback",
                "--format",
                "json",
            ]
        )
        == 0
    )
    cap = capsys.readouterr()
    data = json.loads(cap.out)
    assert data["mode"] == "allowance"
    assert data["fallback"] is True
    assert data["snapshot"]["benchmark_error"] == "Artificial Analysis returned HTTP 429"
    assert "WARNING: FALLBACK ACTIVE" in cap.err
    assert "HTTP 429" in cap.err


def test_cli_allowance_skips_aa_and_passes_aa_cache_ttl(tmp_path, monkeypatch, capsys):
    snap = load_snapshot(create_temp_snapshot(tmp_path))
    seen: list[dict[str, object]] = []

    def fake_fetch(**kwargs):
        seen.append(kwargs)
        return snap

    monkeypatch.setattr("opencode_go_model_optimizer.cli.fetch_and_build_snapshot", fake_fetch)
    monkeypatch.setenv("OPENCODE_GO_AA_CACHE_TTL", "28800")

    assert main(["select", "--mode", "allowance", "--format", "compact"]) == 0
    cap = capsys.readouterr()
    assert "METHOD=ALLOWANCE" in cap.err
    assert seen[-1].get("use_aa") is False
    assert seen[-1].get("aa_cache_ttl") == 28800

    assert main(["fetch", "--aa-cache-ttl", "600", "--out", str(tmp_path / "out.json")]) == 0
    capsys.readouterr()
    assert seen[-1].get("aa_cache_ttl") == 600
