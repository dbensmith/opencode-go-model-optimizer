"""Tests for models.dev scoping, warnings and benchmark provenance."""

import hashlib
import json
import os
from unittest.mock import patch

import httpx

from opencode_go_model_optimizer.cli import main
from opencode_go_model_optimizer.engine.tiers import resolve_mode
from opencode_go_model_optimizer.ingest.catalogue import (
    DEFAULT_MODELS_HTTP_URL,
    parse_catalogue_http,
)
from opencode_go_model_optimizer.ingest.docs import DOCS_URLS
from opencode_go_model_optimizer.ingest.models import Snapshot
from opencode_go_model_optimizer.ingest.models_dev import MODELS_DEV_URL, parse_models_dev
from opencode_go_model_optimizer.ingest.snapshot import (
    create_snapshot,
    fetch_and_build_snapshot,
    load_snapshot,
)
from tests.conftest import CATALOGUE_HTTP_DATA as OC
from tests.conftest import FIXTURES_DIR as FIX
from tests.conftest import MDX_TEXT as MDX
from tests.factories import make_model


def test_catalogue_carries_allowances_despite_name_drift():
    models = parse_catalogue_http(OC)
    drifted = next(m for m in models if m.id == "beta-flash")
    assert drifted.name != "Beta Flash"  # live name drifted from the docs name
    assert drifted.plan_allowances["go"] == 80.0

    paid = [m for m in models if m.status == "active" and not m.is_zero_cost]
    assert all(m.plan_allowances for m in paid)


def test_warning_when_catalogue_has_no_plan_usage():
    snap = create_snapshot([make_model("b", name="B")])
    assert any("no per-plan usage from the Go models API" in w for w in snap.warnings)


def test_warning_lists_paid_models_without_usage_for_the_plan():
    priced = make_model("priced", name="Priced", output=1.0, plan_allowances={"go": 15.0})
    missing = make_model("missing-usage", name="Missing", output=1.0)
    partial = make_model(
        "plus-only",
        name="PlusOnly",
        output=1.0,
        plan_allowances={"go-plus": 60.0},
    )
    snap = create_snapshot([priced, missing, partial], plan="go")
    warning = next(w for w in snap.warnings if "no go.monthly_usage for plan go" in w)
    assert "missing-usage" in warning and "plus-only" in warning
    assert "priced" not in warning


def test_warning_for_zero_priced_model_with_allowance():
    odd = make_model("odd", name="Odd", plan_allowances={"go": 15.0})
    snap = create_snapshot([odd], plan="go")
    warning = next(w for w in snap.warnings if "catalogue anomaly" in w)
    assert "odd" in warning
    assert not any("no go.monthly_usage" in w for w in snap.warnings)


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


def test_models_dev_fixture_scopes_to_opencode_go():
    data = json.loads((FIX / "models_dev.json").read_text(encoding="utf-8"))
    parsed = parse_models_dev(data)
    assert parsed
    assert parsed["betaflash"]["release_date"] == "2026-10-17"
    assert parsed["alphamax"]["context"] == 512000  # not the colliding other-provider entry


def _build(env, aa_return, models=None):
    models = parse_catalogue_http(OC) if models is None else models
    with (
        patch.dict(os.environ, env, clear=True),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue", return_value=models),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=MDX),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev", return_value={}),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_aa_benchmarks",
            return_value=aa_return,
        ),
    ):
        return fetch_and_build_snapshot(plan="go")


def test_benchmark_source_live_and_none():
    aa = {"betaflash": {"iq": 60.0, "coding": 55.0, "tps": 100.0}}
    snap = _build({"AA_API_KEY": "k"}, aa)
    assert snap.metadata.benchmark_source == "aa_live"
    assert snap.warnings == []
    snap = _build({}, {})
    assert snap.metadata.benchmark_source == "none"
    assert all(m.aa_iq is None for m in snap.models)


def test_aa_key_set_but_fetch_empty_warns_and_records_error():
    snap = _build({"AA_API_KEY": "k"}, {})
    assert snap.metadata.benchmark_source == "none"
    assert snap.metadata.benchmark_error is not None
    assert any("no Artificial Analysis benchmarks" in w for w in snap.warnings)
    assert all("falling back to allowance" not in w for w in snap.warnings)
    assert resolve_mode("auto", snap.models) == "allowance"


def test_aa_fetch_error_detail_is_included_in_warning():
    def fake_fetch(api_key, client=None, errors=None, stats=None):
        if errors is not None:
            errors.append("Artificial Analysis returned HTTP 401")
        return {}

    models = parse_catalogue_http(OC)
    with (
        patch.dict(os.environ, {"AA_API_KEY": "k"}, clear=True),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue", return_value=models),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=MDX),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev", return_value={}),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_aa_benchmarks",
            side_effect=fake_fetch,
        ),
    ):
        snap = fetch_and_build_snapshot(plan="go")

    assert any("HTTP 401" in w for w in snap.warnings)
    assert snap.metadata.benchmark_error == "Artificial Analysis returned HTTP 401"


def test_fetch_prints_warnings_to_stderr(tmp_path, capsys):
    out = tmp_path / "s.json"
    models = [make_model("no-usage", name="No Usage")]
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue", return_value=models),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=MDX),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev", return_value={}),
    ):
        assert main(["fetch", "--out", str(out)]) == 0
    err = capsys.readouterr().err
    assert "opencode-go-model-optimizer: warning: no per-plan usage" in err
    assert load_snapshot(out).metadata.benchmark_source == "none"


def test_source_provenance_records_digests_not_bodies():
    mdx_bytes = MDX.encode()

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == DEFAULT_MODELS_HTTP_URL:
            return httpx.Response(200, json=OC)
        if url == MODELS_DEV_URL:
            return httpx.Response(200, content=b"{}")
        if url == DOCS_URLS[0]:
            return httpx.Response(404)
        if url == DOCS_URLS[1]:
            return httpx.Response(200, content=mdx_bytes)
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    with patch.dict(os.environ, {"AA_API_KEY": "secret"}, clear=True):
        snap = fetch_and_build_snapshot(plan="go", client=client, use_aa=False)
    assert client.event_hooks["response"] == []  # hook removed again
    sources = snap.metadata.sources
    assert set(sources) == {"catalogue", "docs", "models_dev"}
    assert sources["docs"]["status"] == 200  # the 404 mirror did not overwrite the success
    assert sources["docs"]["url"] == DOCS_URLS[1]
    assert sources["docs"]["sha256"] == hashlib.sha256(mdx_bytes).hexdigest()
    assert sources["docs"]["bytes"] == len(mdx_bytes)
    assert sources["models_dev"]["bytes"] == 2
    dumped = snap.model_dump_json()
    assert "Alpha Max" in dumped  # model data present
    assert MDX[:200] not in dumped  # but never the raw body
    assert snap.metadata.benchmark_source == "none"  # use_aa=False ignores the key


def test_select_json_includes_benchmark_source(tmp_path, capsys):
    snap = create_snapshot(parse_catalogue_http(OC), benchmark_source="aa_live")
    path = tmp_path / "s.json"
    path.write_text(snap.model_dump_json(), encoding="utf-8")
    assert main(["select", "--snapshot", str(path), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["snapshot"]["benchmark_source"] == "aa_live"
