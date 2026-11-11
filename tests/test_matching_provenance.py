"""Tests for endpoint-ID matching, models.dev scoping, warnings and benchmark provenance."""

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
from opencode_go_model_optimizer.ingest.docs import (
    DOCS_URLS,
    parse_endpoints_table,
    parse_go_pricing,
)
from opencode_go_model_optimizer.ingest.models import NormalizedModel, Snapshot
from opencode_go_model_optimizer.ingest.models_dev import MODELS_DEV_URL, parse_models_dev
from opencode_go_model_optimizer.ingest.snapshot import (
    create_snapshot,
    fetch_and_build_snapshot,
    load_snapshot,
    merge_docs_pricing,
)
from tests.conftest import CATALOGUE_HTTP_DATA as OC
from tests.conftest import FIXTURES_DIR as FIX
from tests.conftest import MDX_TEXT as MDX


def _row(allowance=15.0, out=1.0):
    return {
        "allowance": allowance,
        "output_cost": out,
        "input_cost": 0.5,
        "cached_read_cost": 0.1,
        "raw_name": "x",
    }


def test_parse_endpoints_table_fixture():
    eps = parse_endpoints_table(MDX)
    assert eps["Beta Flash"] == "beta-flash"
    assert eps["Omega Preview"] == "omega-preview"
    assert eps["Epsilon Free"] == "epsilon-free"
    assert len(eps) == 14
    assert parse_endpoints_table("no endpoints here") == {}


def test_catalogue_matches_pricing_by_id_despite_name_drift():
    models = parse_catalogue_http(OC)
    drifted = next(m for m in models if m.id == "beta-flash")
    assert drifted.name != "Beta Flash"  # live name drifted from the docs name
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
    aa = {"beta-flash": {"iq": 60.0, "coding": 55.0, "tps": 100.0}}
    snap = _build({"AA_API_KEY": "k"}, aa)
    assert snap.metadata.benchmark_source == "aa_live"
    assert snap.warnings == []
    snap = _build({}, {})
    assert snap.metadata.benchmark_source == "none"
    assert all(m.aa_iq is None for m in snap.models)


def test_aa_key_set_but_fetch_empty_warns_and_resolves_allowance():
    snap = _build({"AA_API_KEY": "k"}, {})
    assert snap.metadata.benchmark_source == "none"
    assert any("no Artificial Analysis benchmarks" in w for w in snap.warnings)
    assert resolve_mode("auto", snap.models) == "allowance"


def test_fetch_prints_warnings_to_stderr(tmp_path, capsys):
    out = tmp_path / "s.json"
    models = [m for m in parse_catalogue_http(OC) if m.id != "alpha-max"]
    with (
        patch.dict(os.environ, {}, clear=True),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue", return_value=models),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=MDX),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev", return_value={}),
    ):
        assert main(["fetch", "--out", str(out)]) == 0
    err = capsys.readouterr().err
    assert "opencode-go-model-optimizer: warning: 1 docs pricing row(s) matched no catalogue" in err
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
    snap = create_snapshot(
        parse_catalogue_http(OC), parse_go_pricing(MDX), benchmark_source="aa_live"
    )
    path = tmp_path / "s.json"
    path.write_text(snap.model_dump_json(), encoding="utf-8")
    assert main(["select", "--snapshot", str(path), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["snapshot"]["benchmark_source"] == "aa_live"
