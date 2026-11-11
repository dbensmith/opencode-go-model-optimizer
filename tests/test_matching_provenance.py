"""Tests for endpoint-ID matching, models.dev scoping, warnings and benchmark provenance."""

import json
import os
from pathlib import Path
from unittest.mock import patch

from opencode_go_model_optimizer.cli import main
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_http
from opencode_go_model_optimizer.ingest.docs import parse_endpoints_table, parse_go_pricing
from opencode_go_model_optimizer.ingest.models import NormalizedModel, Snapshot, SnapshotMetadata
from opencode_go_model_optimizer.ingest.models_dev import parse_models_dev
from opencode_go_model_optimizer.ingest.snapshot import (
    create_snapshot,
    fetch_and_build_snapshot,
    load_snapshot,
    merge_docs_pricing,
)

FIX = Path(__file__).parent / "fixtures"
MDX = (FIX / "live_go_docs.mdx").read_text(encoding="utf-8")
OC = json.loads((FIX / "live_oc_models.json").read_text(encoding="utf-8"))


def _row(allowance=15.0, out=1.0):
    return {
        "allowance": allowance,
        "output_cost": out,
        "input_cost": 0.5,
        "cached_read_cost": 0.1,
        "raw_name": "x",
    }


def test_parse_endpoints_table_live():
    eps = parse_endpoints_table(MDX)
    assert eps["DeepSeek V4.1 Flash"] == "deepseek-v4.1-flash"
    assert eps["Hy4 preview"] == "hy4-preview"
    assert eps["Beam Free"] == "beam-free"
    assert len(eps) >= 30
    assert parse_endpoints_table("no endpoints here") == {}


def test_live_catalogue_matches_pricing_by_id_despite_name_drift():
    models = parse_catalogue_http(OC)
    drifted = next(m for m in models if m.id == "deepseek-v4.1-flash")
    assert drifted.name != "DeepSeek V4.1 Flash"  # live name drifted from the docs name
    pricing = parse_go_pricing(MDX)
    warnings: list[str] = []
    merge_docs_pricing(models, pricing, endpoints=parse_endpoints_table(MDX), warnings=warnings)
    assert drifted.monthly_allowance is not None
    unpriced = [m.id for m in models if m.status == "active" and m.monthly_allowance is None]
    assert unpriced == []
    assert not any("no docs pricing match" in w for w in warnings)


def test_id_lookup_beats_conflicting_fuzzy_name():
    m = NormalizedModel(id="real-id", name="Other Name")
    pricing = {"othername": _row(99.0), "realmodel": _row(15.0)}
    merge_docs_pricing([m], pricing, endpoints={"Real Model (50% off)": "real-id"})
    assert m.monthly_allowance == 15.0


def test_fuzzy_fallback_when_endpoints_table_lacks_model():
    listed = NormalizedModel(id="listed", name="Listed")
    free = NormalizedModel(id="free-x", name="Free X")
    merge_docs_pricing(
        [listed, free],
        {"listed": _row(15.0), "freex": _row(0.0)},
        endpoints={"Listed": "listed"},
    )
    assert listed.monthly_allowance == 15.0
    assert free.monthly_allowance == 0.0


def test_warnings_unmatched_models_and_rows_and_empty_table():
    models = [
        NormalizedModel(id="a", name="A"),
        NormalizedModel(id="old", name="Old", status="deprecated"),
    ]
    snap = create_snapshot(models, {"a": _row(), "ghost": _row()})
    assert snap.warnings == ["1 docs pricing row(s) matched no catalogue model: x"]
    snap = create_snapshot([NormalizedModel(id="b", name="B")], {"a": _row()})
    assert any("no docs pricing match: b" in w for w in snap.warnings)
    snap = create_snapshot([NormalizedModel(id="b", name="B")], {})
    assert any("no rows parsed from the go-pricing table" in w for w in snap.warnings)


def test_snapshot_backward_compatible_without_new_fields():
    snap = Snapshot.model_validate_json('{"fetched_at": "t", "metadata": {}}')
    assert snap.warnings == []
    assert snap.metadata.benchmark_source == "none"


def test_models_dev_scoped_to_opencode_go():
    data = {
        "other": {"models": {"m": {"name": "Shared", "release_date": "1999-01-01"}}},
        "opencode-go": {"models": {"m": {"name": "Shared", "release_date": "2026-01-01"}}},
    }
    assert parse_models_dev(data)["shared"]["release_date"] == "2026-01-01"
    assert parse_models_dev({"other": data["other"]}) == {}


def test_live_models_dev_fixture_parses():
    data = json.loads((FIX / "live_models_dev.json").read_text(encoding="utf-8"))
    parsed = parse_models_dev(data)
    assert parsed
    assert parsed["deepseekv41flash"]["release_date"]


def _build(env, aa_return, ref):
    models = parse_catalogue_http(OC)
    with (
        patch.dict(os.environ, env, clear=True),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue", return_value=models),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=MDX),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev", return_value={}),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_aa_benchmarks",
            return_value=aa_return,
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.load_reference_snapshot",
            return_value=ref,
        ),
    ):
        return fetch_and_build_snapshot(plan="go")


def _ref():
    return Snapshot(
        fetched_at="2026-01-01T00:00:00+00:00",
        models=[NormalizedModel(id="deepseek-v4.1-flash", aa_iq=50.0)],
        metadata=SnapshotMetadata(),
    )


def test_benchmark_source_reference_and_warning():
    snap = _build({}, {}, _ref())
    assert snap.metadata.benchmark_source == "reference:2026-01-01T00:00:00+00:00"
    assert any("bundled reference snapshot" in w for w in snap.warnings)
    assert not any("no docs pricing match" in w for w in snap.warnings)


def test_benchmark_source_live_and_none():
    aa = {"deepseek-v4.1-flash": {"iq": 60.0, "coding": 55.0, "tps": 100.0}}
    snap = _build({"AA_API_KEY": "k"}, aa, _ref())
    assert snap.metadata.benchmark_source == "aa_live"
    assert not any("bundled reference" in w for w in snap.warnings)
    snap = _build({}, {}, None)
    assert snap.metadata.benchmark_source == "none"


def test_fetch_prints_warnings_to_stderr(tmp_path, capsys):
    out = tmp_path / "s.json"
    models = parse_catalogue_http(OC)
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue", return_value=models),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=MDX),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev", return_value={}),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.load_reference_snapshot",
            return_value=_ref(),
        ),
    ):
        assert main(["fetch", "--out", str(out)]) == 0
    err = capsys.readouterr().err
    assert "opencode-go-model-optimizer: warning: benchmarks" in err
    assert load_snapshot(out).metadata.benchmark_source.startswith("reference:")


def test_select_json_includes_benchmark_source(tmp_path, capsys):
    snap = create_snapshot(
        parse_catalogue_http(OC), parse_go_pricing(MDX), benchmark_source="aa_live"
    )
    path = tmp_path / "s.json"
    path.write_text(snap.model_dump_json(), encoding="utf-8")
    assert main(["select", "--snapshot", str(path), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["snapshot"]["benchmark_source"] == "aa_live"
