"""Tests for ingestion modules."""

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from opencode_go_model_optimizer.ingest.artificial_analysis import fetch_aa_benchmarks
from opencode_go_model_optimizer.ingest.catalogue import (
    _extract_model_limit,
    _extract_token_cost,
    estimate_requests,
    fetch_catalogue,
    parse_catalogue_cli,
    parse_catalogue_http,
)
from opencode_go_model_optimizer.ingest.console import (
    RequestProfiles,
    fetch_console_profiles,
    parse_console_profiles,
)
from opencode_go_model_optimizer.ingest.docs import (
    fetch_docs_mdx,
    norm_name,
    parse_privacy,
    parse_table_rows,
)
from opencode_go_model_optimizer.ingest.models import (
    ModelLimit,
    NormalizedModel,
    Privacy,
    RequestEstimate,
    Snapshot,
    SnapshotMetadata,
    TokenCost,
)
from opencode_go_model_optimizer.ingest.models_dev import (
    _extract_metadata,
    fetch_models_dev,
    parse_models_dev,
)
from opencode_go_model_optimizer.ingest.snapshot import (
    apply_plan,
    create_snapshot,
    fetch_and_build_snapshot,
    get_cache_snapshot_path,
    load_cached_snapshot,
    load_snapshot,
    merge_catalogue_metadata,
    save_cached_snapshot,
    save_snapshot,
)
from tests.conftest import FIXTURES_DIR, TEST_CONSOLE_PROFILES
from tests.factories import make_model


def test_norm_name():
    assert norm_name("Beta Flash (Off-Peak)") == "betaflash"
    assert norm_name("Theta Pro (50% off)") == "thetapro"
    assert norm_name("Alpha Plus (≤ 256K tokens)") == "alphaplus"
    assert norm_name("Gamma-Flash") == "gammaflash"
    assert norm_name("<b>Zeta Pro</b>") == "zetapro"
    assert norm_name("Delta Contributor") == "deltacontributor"


def test_parse_catalogue_cli():
    cli_text = (FIXTURES_DIR / "catalogue_cli.txt").read_text(encoding="utf-8")
    models = parse_catalogue_cli(cli_text)

    assert len(models) == 14
    ids = {m.id for m in models}
    assert "alpha-max" in ids
    assert "beta-flash" in ids
    assert "delta-contributor" in ids
    assert "deprecated-old-model" in ids

    alpha = next(m for m in models if m.id == "alpha-max")
    assert alpha.cost.input == 3.0
    assert alpha.cost.output == 9.0
    assert alpha.cost.cache_read == 0.4
    assert alpha.cost.cache_write == 3.5
    assert alpha.limit.context == 512000
    assert alpha.plan_allowances == {"go": 12.0, "go-plus": 120.0, "go-teams": 24.0}
    assert alpha.privacy is not None
    assert alpha.privacy.trains_on_data is False
    assert alpha.privacy.zdr is True

    delta = next(m for m in models if m.id == "delta-contributor")
    assert delta.is_zero_cost is True
    assert delta.is_contributor is True


def test_parse_catalogue_http():

    http_text = (FIXTURES_DIR / "catalogue_http.json").read_text(encoding="utf-8")
    data = json.loads(http_text)
    models = parse_catalogue_http(data)

    assert len(models) == 14
    alpha = models[0]
    assert alpha.id == "alpha-max"
    assert alpha.provider_id == "opencode-go"  # owned_by ("opencode") is not a provider
    assert alpha.cost.input == 3.0
    assert alpha.cost.output == 9.0
    assert alpha.cost.cache_read == 0.4
    assert alpha.cost.cache_write == 3.5
    assert alpha.multipliers == []
    beta = next(m for m in models if m.id == "beta-flash")
    assert beta.name == "Beta Flash 1101"
    (peak,) = beta.multipliers
    assert peak.factor == 2 and set(peak.when) == {"utc_days", "any"}
    theta = next(m for m in models if m.id == "theta-pro")
    assert theta.multipliers[0].when == {"date": {"gte": "2026-12-01"}}
    assert next(m for m in models if m.id == "deprecated-old-model").status == "deprecated"


def test_parse_catalogue_http_uses_opencode_go_provider_not_owned_by():
    # Live endpoint sets owned_by to the vendor ("opencode") and no providerID.
    live_item = {
        "id": "theta-pro",
        "object": "model",
        "owned_by": "opencode",
        "status": "active",
    }
    models = parse_catalogue_http({"object": "list", "data": [live_item]})

    assert len(models) == 1
    assert models[0].provider_id == "opencode-go"
    assert models[0].full_id == "opencode-go/theta-pro"


def test_snapshot_lifecycle(tmp_path):
    mdx_text = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
    data = json.loads((FIXTURES_DIR / "catalogue_http.json").read_text(encoding="utf-8"))

    models = parse_catalogue_http(data)
    snap = create_snapshot(models, privacy=parse_privacy(mdx_text))
    assert snap.schema_version == 1
    assert snap.plan == "go"
    assert len(snap.models) == 14

    beta = next(m for m in snap.models if m.id == "beta-flash")
    assert beta.monthly_allowance == 80.0
    assert beta.privacy is not None and beta.privacy.retention_days == 0

    out_file = tmp_path / "test_snapshot.json"
    save_snapshot(snap, out_file)
    assert out_file.exists()

    loaded = load_snapshot(out_file)
    assert loaded.schema_version == snap.schema_version
    assert len(loaded.models) == len(snap.models)
    assert loaded.models[0].id == snap.models[0].id


def test_extract_token_cost_and_model_limit_edge_cases():
    # Non-dict inputs
    assert _extract_token_cost(None) == TokenCost()
    assert _extract_token_cost("string-cost") == TokenCost()
    assert _extract_token_cost([1, 2, 3]) == TokenCost()

    # Nested HTTP format with null values
    null_nested = {
        "text": {
            "input": {"price": None, "cache": {"read": None, "write": None}},
            "output": {"price": None},
        }
    }
    cost = _extract_token_cost(null_nested)
    assert cost.input == 0.0
    assert cost.output == 0.0
    assert cost.cache_read == 0.0
    assert cost.cache_write == 0.0

    # Nested HTTP format with primitive input/output (non-dict)
    prim_nested = {
        "text": {
            "input": 1.5,
            "output": 3.0,
        }
    }
    cost_prim = _extract_token_cost(prim_nested)
    assert cost_prim.input == 1.5
    assert cost_prim.output == 3.0

    # Nested HTTP format with dict cache_write (e.g. {'30m': 0.25, '12h': 0.5})
    nested_dict_cache = {
        "text": {
            "input": {"price": 0.15, "cache": {"read": 0.02, "write": {"30m": 0.25, "12h": 0.5}}},
            "output": {"price": 0.60},
        }
    }
    cost_dict = _extract_token_cost(nested_dict_cache)
    assert cost_dict.input == 0.15
    assert cost_dict.cache_write == 0.25

    # Flat CLI format with cache dictionary
    flat = {
        "input": 0.5,
        "output": 1.5,
        "cache": {"read": 0.05, "write": 0.25},
    }
    cost_flat = _extract_token_cost(flat)
    assert cost_flat.input == 0.5
    assert cost_flat.output == 1.5
    assert cost_flat.cache_read == 0.05
    assert cost_flat.cache_write == 0.25

    # Flat CLI with non-dict cache
    flat_no_cache_dict = {"input": 1.0, "output": 2.0, "cache": None}
    cost_no_cache = _extract_token_cost(flat_no_cache_dict)
    assert cost_no_cache.cache_read == 0.0
    assert cost_no_cache.cache_write == 0.0

    # _extract_model_limit edge cases
    assert _extract_model_limit(None) == ModelLimit()
    assert _extract_model_limit("invalid") == ModelLimit()
    assert _extract_model_limit({"context": None, "output": None}) == ModelLimit(
        context=0, output=0
    )
    assert _extract_model_limit({"context": 200000, "output": 8192}) == ModelLimit(
        context=200000, output=8192
    )


def test_parse_catalogue_cli_edge_cases():
    # Empty body, invalid JSON lines, non-matching header lines
    raw_text = """
    Preamble text not matching header pattern
    Some other text
    opencode-go/empty-model
    opencode-go/corrupt-json
    { invalid: json not quoted }
    opencode-go/valid-model
    {
      "id": "valid-model",
      "name": "Valid Model",
      "status": "active"
    }
    opencode-go/empty-at-end
    """
    models = parse_catalogue_cli(raw_text)
    assert len(models) == 1
    assert models[0].id == "valid-model"
    assert models[0].name == "Valid Model"


def _http_ok(payload):
    return httpx.Response(200, json=payload, request=httpx.Request("GET", "http://test"))


def test_fetch_catalogue_http_primary_does_not_call_cli():
    with (
        patch("shutil.which", return_value="/usr/local/bin/opencode"),
        patch("subprocess.run") as mock_run,
        patch("httpx.Client.get", return_value=_http_ok({"data": [{"id": "http-m"}]})),
    ):
        models = fetch_catalogue()
    assert [m.id for m in models] == ["http-m"]
    mock_run.assert_not_called()


def test_fetch_catalogue_custom_client_list_payload():
    custom_client = httpx.Client()
    with patch.object(custom_client, "get", return_value=_http_ok([{"id": "list-m"}])):
        models = fetch_catalogue(client=custom_client)
    assert [m.id for m in models] == ["list-m"]


def test_fetch_catalogue_falls_back_to_cli_when_http_fails():
    cli_stdout = 'opencode-go/test-cli-m\n{\n  "id": "test-cli-m",\n  "name": "Test CLI Model"\n}\n'
    with (
        patch("httpx.Client.get") as mock_get,
        patch("shutil.which", return_value="/usr/local/bin/opencode"),
        patch("subprocess.run") as mock_run,
    ):
        mock_get.return_value = httpx.Response(500, request=httpx.Request("GET", "http://test"))
        mock_run.return_value = subprocess.CompletedProcess(
            args=["opencode"], returncode=0, stdout=cli_stdout
        )
        models = fetch_catalogue()
    assert [m.id for m in models] == ["test-cli-m"]


def test_fetch_catalogue_raises_http_error_when_cli_also_unusable():
    for which, run in (
        (None, None),
        ("/usr/bin/opencode", subprocess.CompletedProcess(args=[], returncode=1, stdout="")),
    ):
        with (
            patch("shutil.which", return_value=which),
            patch("subprocess.run", return_value=run),
            patch("httpx.Client.get") as mock_get,
        ):
            mock_get.return_value = httpx.Response(500, request=httpx.Request("GET", "http://t"))
            with pytest.raises(httpx.HTTPStatusError):
                fetch_catalogue()
    with (
        patch("shutil.which", return_value="/usr/bin/opencode"),
        patch("subprocess.run", side_effect=subprocess.SubprocessError("boom")),
        patch("httpx.Client.get", side_effect=httpx.ConnectError("down")),
        pytest.raises(httpx.ConnectError),
    ):
        fetch_catalogue()


def test_parse_table_rows_edge_cases():
    table_text = """
    Line without pipe
    | OnlyOneCell |
    | Model | Pricing | Output |
    | :--- | :--- | :--- |
    | Model A | $1.00 | $2.00 |
    | Model B | $3.00 | $4.00 |
    """
    rows = parse_table_rows(table_text)
    assert len(rows) == 2
    assert rows[0][0] == "Model A"
    assert rows[1][0] == "Model B"


def test_fetch_docs_mdx_retries_and_errors():
    # 1. Success with custom client
    custom_client = httpx.Client()
    with patch.object(custom_client, "get") as mock_get:
        mock_get.return_value = httpx.Response(
            200, text="# Docs MDX", request=httpx.Request("GET", "http://test")
        )
        content = fetch_docs_mdx(client=custom_client)
        assert content == "# Docs MDX"

    # 2. Retry across DOCS_URLS: first URL fails, second succeeds
    call_count = 0

    def mock_retry_get(url):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise httpx.ConnectError("Network unreachable")
        return httpx.Response(200, text="# Recovered MDX", request=httpx.Request("GET", url))

    with patch("httpx.Client.get", side_effect=mock_retry_get):
        content = fetch_docs_mdx()
        assert content == "# Recovered MDX"
        assert call_count == 2

    # 3. All URLs fail -> raises last exception
    with (
        patch(
            "httpx.Client.get",
            side_effect=httpx.ConnectError("All URLs failed"),
        ),
        pytest.raises(httpx.ConnectError),
    ):
        fetch_docs_mdx()

    # 4. Custom single URL fails -> raises directly
    with (
        patch(
            "httpx.Client.get",
            side_effect=httpx.HTTPStatusError(
                "404",
                request=httpx.Request("GET", "http://test"),
                response=httpx.Response(404),
            ),
        ),
        pytest.raises(httpx.HTTPStatusError),
    ):
        fetch_docs_mdx(url="http://custom.url/docs.mdx")

    # 5. Empty DOCS_URLS list raises RequestError (line 172)
    with (
        patch("opencode_go_model_optimizer.ingest.docs.DOCS_URLS", []),
        pytest.raises(httpx.RequestError),
    ):
        fetch_docs_mdx()


def test_fetch_aa_benchmarks():
    api_response = {
        "data": [
            "non-dict item",
            {"missing_slug": True},
            {
                "slug": "model-slug-1",
                "name": "Model Slug 1 Name",
                "evaluations": {
                    "artificial_analysis_intelligence_index": 85.5,
                    "artificial_analysis_engineering_index": 78.0,
                },
                "performance_v3": {"mean_output_tokens_per_second": 45.2},
            },
            {
                "id": "model-id-2",
                "evaluations": {},
                "performance_v3": {"mean_output_tokens_per_second": None},
            },
        ]
    }

    # 1. Success with client=None (creates own client)
    with patch("httpx.Client.get") as mock_get:
        mock_get.return_value = httpx.Response(
            200, json=api_response, request=httpx.Request("GET", "http://test")
        )
        benchmarks = fetch_aa_benchmarks("valid-key")
        assert "model-slug-1" in benchmarks
        assert "modelslug1" in benchmarks
        assert "modelslug1name" in benchmarks
        assert benchmarks["model-slug-1"]["iq"] == 85.5
        assert benchmarks["model-slug-1"]["coding"] == 78.0
        assert benchmarks["model-slug-1"]["tps"] == 45.2
        assert benchmarks["modelslug1name"]["iq"] == 85.5

        assert "model-id-2" in benchmarks
        assert benchmarks["model-id-2"]["iq"] is None
        assert benchmarks["model-id-2"]["tps"] is None

    # 2. HTTP error returns {}
    with patch("httpx.Client.get", side_effect=httpx.HTTPError("API error")):
        benchmarks_err = fetch_aa_benchmarks("key")
        assert benchmarks_err == {}


def test_merge_catalogue_metadata_and_benchmarks():
    m = NormalizedModel(
        id="test-model",
        name="Test Model",
        cost=TokenCost(input=1.25, output=3.5, cache_read=0.15, cache_write=0.4),
        plan_allowances={"go": 80.0},
    )
    assert m.is_zero_cost is False

    benchmarks = {
        "testmodel": {
            "iq": 72.0,
            "coding": 68.0,
            "tps": 50.0,
        }
    }
    merged = merge_catalogue_metadata([m], aa_benchmarks=benchmarks)
    assert merged[0].aa_iq == 72.0
    assert merged[0].aa_coding == 68.0
    assert merged[0].speed_tps == 50.0

    # Benchmark lookup by norm_name(name) and is_zero_cost remains True for 0 cost
    m2 = NormalizedModel(
        id="model-custom-id",
        name="Custom Display Name",
        cost=TokenCost(input=0.0, output=0.0),
    )
    benchmarks2 = {
        "customdisplayname": {
            "iq": 90.0,
            "coding": 85.0,
            "tps": 60.0,
        }
    }
    merged2 = merge_catalogue_metadata([m2], aa_benchmarks=benchmarks2)
    assert merged2[0].is_zero_cost is True
    assert merged2[0].aa_iq == 90.0
    assert merged2[0].aa_coding == 85.0


def test_fetch_and_build_with_aa_key():
    # Test fetch_and_build_snapshot with AA_API_KEY env set
    dummy_model = make_model("snap-m", name="Snap Model", plan_allowances={"go": 50.0})

    with (
        patch.dict(os.environ, {"AA_API_KEY": "dummy-key"}),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=[dummy_model],
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx",
            return_value="",
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_aa_benchmarks",
            return_value={"snapmodel": {"iq": 65.0, "coding": 60.0, "tps": 30.0}},
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev",
            return_value={
                "snap-m": {
                    "release_date": "2026-02-01",
                    "context": 256000,
                    "output": 32000,
                }
            },
        ),
    ):
        snap = fetch_and_build_snapshot(plan="go")
        assert len(snap.models) == 1
        assert snap.models[0].aa_iq == 65.0
        assert snap.models[0].release_date == "2026-02-01"
        assert snap.models[0].limit.context == 256000
        assert snap.models[0].limit.output == 32000


def test_cache_snapshot_paths_and_operations(tmp_path):
    # 1. get_cache_snapshot_path with XDG_CACHE_HOME
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path)}):
        p1 = get_cache_snapshot_path("go-plus")
        assert p1 == tmp_path / "opencode-go-model-optimizer" / "snapshot-go-plus.json"

    # 2. get_cache_snapshot_path without XDG_CACHE_HOME
    with patch.dict(os.environ, {}, clear=True):
        p2 = get_cache_snapshot_path("go")
        assert ".cache" in str(p2)

    # 3. load_cached_snapshot with ttl <= 0
    assert load_cached_snapshot("go", ttl_seconds=0) is None
    assert load_cached_snapshot("go", ttl_seconds=-5) is None

    # 4. load_cached_snapshot when file doesn't exist
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path)}):
        assert load_cached_snapshot("go", ttl_seconds=3600) is None

    # 5. save_cached_snapshot and load_cached_snapshot roundtrip
    snap = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="go",
        models=[make_model("cached-m")],
        metadata=SnapshotMetadata(),
    )
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path)}):
        saved_path = save_cached_snapshot(snap)
        assert saved_path is not None
        assert saved_path.exists()

        # Load fresh cache (valid TTL)
        loaded = load_cached_snapshot("go", ttl_seconds=3600)
        assert loaded is not None
        assert loaded.models[0].id == "cached-m"

        # Expired cache (time exceeded)
        with patch("time.time", return_value=time.time() + 10000):
            expired = load_cached_snapshot("go", ttl_seconds=60)
            assert expired is None

        # Corrupt cache file
        saved_path.write_text("{ corrupt json", encoding="utf-8")
        assert load_cached_snapshot("go", ttl_seconds=3600) is None

        # OSError during save_cached_snapshot
        with patch(
            "opencode_go_model_optimizer.ingest.cache.atomic_write_text",
            side_effect=OSError("Disk full"),
        ):
            assert save_cached_snapshot(snap) is None


def test_same_plan_cache_load_applies_request_estimate(tmp_path):
    estimate = RequestEstimate(per_5h=100.0, per_week=250.0, per_month=500.0)
    model = make_model(
        "cached-m",
        plan_allowances={"go": 15.0},
        request_estimates={"go": estimate},
    )
    snap = Snapshot(fetched_at="2026-01-01T00:00:00Z", plan="go", models=[model])
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path)}):
        save_cached_snapshot(snap)
        loaded = load_cached_snapshot("go", ttl_seconds=3600)

    assert loaded is not None
    assert loaded.models[0].request_estimate == estimate


def test_fetch_and_build_snapshot_custom_client():
    # Test fetch_and_build_snapshot with user-provided client (should not close user client)
    custom_client = httpx.Client()
    dummy_model = make_model("custom-m", plan_allowances={"go": 15.0})
    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=[dummy_model],
        ),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=""),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev",
            return_value={},
        ),
    ):
        snap = fetch_and_build_snapshot(plan="go", client=custom_client)
        assert len(snap.models) == 1
        assert not custom_client.is_closed
    custom_client.close()


def test_plan_allowances_and_apply_plan():
    # Validation and serialization
    m = NormalizedModel(
        id="plan-mod",
        plan_allowances={
            "go": 15.0,
            "go-plus": "60.0",
            "go-teams": "unlimited",
            "invalid_num": "not_a_number",
        },
    )
    assert m.plan_allowances["go"] == 15.0
    assert m.plan_allowances["go-plus"] == 60.0
    assert m.plan_allowances["go-teams"] == float("inf")
    assert "invalid_num" not in m.plan_allowances

    dump = m.model_dump(mode="json")
    assert dump["plan_allowances"]["go-teams"] == "unlimited"
    assert dump["plan_allowances"]["go-plus"] == 60.0

    # Non-dict validation falls back to empty dict
    m_bad = NormalizedModel(id="bad-plan", plan_allowances="not_a_dict")
    assert m_bad.plan_allowances == {}

    # apply_plan
    apply_plan([m], "go-plus")
    assert m.monthly_allowance == 60.0

    apply_plan([m], "go-teams")
    assert m.monthly_allowance == float("inf")

    # An absent target plan clears a stale allowance when per-plan data exists.
    apply_plan([m], "unknown-plan")
    assert m.monthly_allowance is None

    go_only = make_model("go-only", monthly_allowance=15.0, plan_allowances={"go": 15.0})
    apply_plan([go_only], "go-plus")
    assert go_only.monthly_allowance is None


def test_apply_plan_matches_plan_names_exactly():
    m = make_model(
        "mod-plan",
        plan_allowances={"go": 15.0, "go-plus": 60.0, "custom-tier": 120.0},
    )

    apply_plan([m], "custom-tier")
    assert m.monthly_allowance == 120.0

    # Short aliases are not expanded: 'plus' is just an unknown plan.
    apply_plan([m], "plus")
    assert m.monthly_allowance is None


def test_fetch_and_build_snapshot_benchmark_matching_by_id_and_name():
    # Benchmarks may match a model by its id or by its normalized display name
    aa = {
        "mcoding": {"iq": None, "coding": 85.0, "tps": 110.0},
        "prettymodelv1": {"iq": 92.0, "coding": None, "tps": None},
    }
    target_mod1 = make_model("m_coding", name="Target 1", plan_allowances={"go": 15.0})
    target_mod2 = make_model("unmatched-id", name="Pretty Model v1", plan_allowances={"go": 15.0})

    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=[target_mod1, target_mod2],
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx",
            return_value="",
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_aa_benchmarks",
            return_value=aa,
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev",
            return_value={},
        ),
        patch.dict("os.environ", {"AA_API_KEY": "k"}, clear=True),
    ):
        snap = fetch_and_build_snapshot(plan="go")
        by_id = {m.id: m for m in snap.models}

        assert by_id["m_coding"].aa_coding == 85.0
        assert by_id["m_coding"].speed_tps == 110.0
        assert by_id["unmatched-id"].aa_iq == 92.0


def test_norm_name_lru_cache():
    norm_name.cache_clear()
    n1 = norm_name("Beta Flash")
    n2 = norm_name("Beta Flash")
    assert n1 == n2
    info = norm_name.cache_info()
    assert info.hits >= 1


def test_cross_plan_cache_sharing(tmp_path):
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path)}):
        # Save a fresh cache for plan="go" with plan_allowances for "go-plus" and "go-teams"
        model = make_model(
            "multi-plan-mod",
            monthly_allowance=15.0,
            plan_allowances={"go": 15.0, "go-plus": 60.0, "go-teams": float("inf")},
        )
        snap_go = Snapshot(
            schema_version=1,
            fetched_at="2026-01-01T00:00:00Z",
            plan="go",
            models=[model],
            metadata=SnapshotMetadata(),
        )
        save_cached_snapshot(snap_go)

        # Loading "go-plus" when snapshot-go-plus.json does not exist hits the shared "go" cache
        shared_plus = load_cached_snapshot("go-plus", ttl_seconds=3600)
        assert shared_plus is not None
        assert shared_plus.plan == "go-plus"
        assert shared_plus.models[0].monthly_allowance == 60.0

        shared_teams = load_cached_snapshot("go-teams", ttl_seconds=3600)
        assert shared_teams is not None
        assert shared_teams.plan == "go-teams"
        assert shared_teams.models[0].monthly_allowance == float("inf")

        # Corrupt other plan cache file triggers exception branch and returns None
        target_file = get_cache_snapshot_path("go")
        target_file.write_text("{ corrupt json", encoding="utf-8")
        assert load_cached_snapshot("go-plus", ttl_seconds=3600) is None


def test_parse_models_dev_payload():
    data = {
        "opencode-go": {
            "models": {
                "model-a": {
                    "name": "Model A",
                    "release_date": "2026-03-01",
                    "limit": {"context": 200000, "output": 64000},
                },
                "model-empty": {"name": "Empty Model"},
                "model-non-dict": "not-a-dict",
            }
        },
        "provider-no-models": {"name": "No models here"},
        "provider-not-dict": "nope",
        "provider-b": {
            "models": {
                "model-a": {"release_date": "1999-01-01"},
                "model-c": {"limit": {"context": "large", "output": 0}},
            }
        },
    }

    parsed = parse_models_dev(data)
    assert parsed["modela"] == {
        "release_date": "2026-03-01",
        "context": 200000,
        "output": 64000,
    }
    assert "emptymodel" not in parsed
    assert "modelc" not in parsed

    assert parse_models_dev(["not", "a", "dict"]) == {}


def test_extract_metadata_edge_cases():
    assert _extract_metadata({"limit": "invalid"}) == {}
    assert _extract_metadata({"release_date": "2026-05-01", "limit": {"context": 0}}) == {
        "release_date": "2026-05-01"
    }


def test_fetch_models_dev_success_and_failure():
    payload = {"opencode-go": {"models": {"m": {"release_date": "2026-01-01"}}}}

    with patch("httpx.Client.get") as mock_get:
        mock_get.return_value = httpx.Response(
            200, json=payload, request=httpx.Request("GET", "http://test")
        )
        assert fetch_models_dev() == {"m": {"release_date": "2026-01-01"}}

    with patch("httpx.Client.get", side_effect=httpx.HTTPError("offline")):
        assert fetch_models_dev() == {}

    custom_client = MagicMock()
    custom_client.get.return_value = httpx.Response(
        200, json=payload, request=httpx.Request("GET", "http://test")
    )
    assert fetch_models_dev(client=custom_client) == {"m": {"release_date": "2026-01-01"}}
    custom_client.close.assert_not_called()


def test_merge_catalogue_metadata_models_dev_backfill():
    blank = NormalizedModel(id="blank-model", name="Blank Model")
    merge_catalogue_metadata(
        [blank],
        models_dev_metadata={
            "blank-model": {
                "release_date": "2026-06-01",
                "context": 512000,
                "output": 128000,
            }
        },
    )
    assert blank.release_date == "2026-06-01"
    assert blank.limit.context == 512000
    assert blank.limit.output == 128000

    existing = make_model(
        "existing-model",
        release_date="2025-01-01",
        context=1000,
        max_output=2000,
    )
    merge_catalogue_metadata(
        [existing],
        models_dev_metadata={
            "existing-model": {"release_date": "2026-06-01", "context": 512000, "output": 128000}
        },
    )
    assert existing.release_date == "2025-01-01"
    assert existing.limit.context == 1000
    assert existing.limit.output == 2000


def test_fetch_aa_benchmarks_robust_parsing():
    api_response = {
        "data": [
            {
                "slug": "null-evals",
                "evaluations": None,
                "performance_v3": {"mean_output_tokens_per_second": 10},
            },
            {"slug": "list-evals", "evaluations": ["x"]},
            {
                "slug": "bad-number",
                "evaluations": {
                    "artificial_analysis_intelligence_index": "n/a",
                    "artificial_analysis_engineering_index": "42.5",
                },
                "performance_v3": {"mean_output_tokens_per_second": True},
            },
        ]
    }
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=api_response))
    )
    bench = fetch_aa_benchmarks("k", client=client)
    assert bench["null-evals"] == {"iq": None, "coding": None, "tps": 10.0}
    assert bench["list-evals"]["iq"] is None
    assert bench["bad-number"] == {"iq": None, "coding": 42.5, "tps": None}


def test_fetch_aa_benchmarks_v2_paginates_and_reads_performance_v3():
    pages = {
        1: {
            "tier": "free",
            "pagination": {"page": 1, "page_size": 2, "total_pages": 2, "has_more": True},
            "data": [
                {
                    "id": "id-1",
                    "slug": "model-one",
                    "name": "Model One",
                    "evaluations": {
                        "artificial_analysis_intelligence_index": 50.5,
                    },
                    "performance_v3": {"mean_output_tokens_per_second": 123.4},
                }
            ],
        },
        2: {
            "tier": "free",
            "pagination": {"page": 2, "page_size": 2, "total_pages": 2, "has_more": False},
            "data": [
                {
                    "id": "id-2",
                    "slug": "model-two",
                    "name": "Model Two",
                    "evaluations": {"artificial_analysis_intelligence_index": 60.0},
                    "performance_v3": {"mean_output_tokens_per_second": 55.5},
                }
            ],
        },
    }
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        seen.append(page)
        return httpx.Response(200, json=pages[page])

    client = httpx.Client(transport=httpx.MockTransport(handler))
    bench = fetch_aa_benchmarks("k", client=client)

    assert seen == [1, 2]
    assert bench["model-one"] == {"iq": 50.5, "coding": None, "tps": 123.4}
    assert bench["model-two"] == {"iq": 60.0, "coding": None, "tps": 55.5}


def test_fetch_aa_benchmarks_rejects_non_object_payload():
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json=[])))
    errors: list[str] = []

    assert fetch_aa_benchmarks("k", client=client, errors=errors) == {}
    assert errors == ["Artificial Analysis fetch failed (ValueError)"]


def test_fetch_aa_benchmarks_reports_http_status_without_leaking_key():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "API key is required"})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    errors: list[str] = []
    bench = fetch_aa_benchmarks("super-secret-key", client=client, errors=errors)

    assert bench == {}
    assert errors == ["Artificial Analysis returned HTTP 401 (API key is required)"]
    assert "super-secret-key" not in " ".join(errors)


def test_fetch_aa_benchmarks_429_reports_reset_and_retry_without_leaking_key():
    from opencode_go_model_optimizer.ingest.artificial_analysis import clear_aa_rate_limit

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"error": "Rate limit exceeded for super-secret-key"},
            headers={"x-ratelimit-reset": "1795895369", "retry-after": "70023"},
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    errors: list[str] = []
    assert fetch_aa_benchmarks("super-secret-key", client=client, errors=errors) == {}
    assert errors == ["Artificial Analysis returned HTTP 429 (resets at 2026-11-28T19:49:29+00:00)"]
    assert "super-secret-key" not in errors[0]

    clear_aa_rate_limit()

    def handler_retry_only(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={"message": "Too many requests"},
            headers={"retry-after": "120"},
        )

    client2 = httpx.Client(transport=httpx.MockTransport(handler_retry_only))
    errors2: list[str] = []
    assert fetch_aa_benchmarks("k", client=client2, errors=errors2) == {}
    assert errors2 == [
        "Artificial Analysis returned HTTP 429 (Too many requests; retry after 120s)"
    ]


def test_fetch_aa_benchmarks_effort_dedup_and_alias_matching():
    # AA returns multiple effort rows per model family; canonical/flagship (Max) must not be
    # overwritten by later Low/High/Non-reasoning variants that normalize to the same name.
    # Also covers creator-prefixed slugs (vendor-model-e -> model-e), edition suffixes
    # (-next -> base), engineering_index for coding, and canonical_id lookup.
    items = [
        {
            "slug": "acme-model-a-1017",
            "name": "Acme Model A 1017 (Max)",
            "model_creator": {"name": "Acme"},
            "evaluations": {
                "artificial_analysis_intelligence_index": 32.6,
                "artificial_analysis_engineering_index": 37.6,
            },
            "performance_v3": {"mean_output_tokens_per_second": 174.35},
        },
        {
            "slug": "acme-model-a-1017-high",
            "name": "Acme Model A 1017 (High)",
            "model_creator": {"name": "Acme"},
            "evaluations": {
                "artificial_analysis_intelligence_index": 28.6,
                "artificial_analysis_engineering_index": 34.4,
            },
            "performance_v3": {"mean_output_tokens_per_second": 174.64},
        },
        {
            "slug": "acme-model-a-1017-non-reasoning",
            "name": "Acme Model A 1017 (Non-reasoning)",
            "model_creator": {"name": "Acme"},
            "evaluations": {
                "artificial_analysis_intelligence_index": 18.5,
                "artificial_analysis_engineering_index": 25.0,
            },
            "performance_v3": {"mean_output_tokens_per_second": 152.85},
        },
        {
            "slug": "acme-model-b",
            "name": "Acme Model B (Xhigh)",
            "model_creator": {"name": "Acme"},
            "evaluations": {
                "artificial_analysis_intelligence_index": 35.5,
                "artificial_analysis_engineering_index": 41.8,
            },
        },
        {
            "slug": "acme-model-b-low",
            "name": "Acme Model B (Low)",
            "model_creator": {"name": "Acme"},
            "evaluations": {
                "artificial_analysis_intelligence_index": 31.1,
                "artificial_analysis_engineering_index": 37.7,
            },
        },
        {
            "slug": "acme-model-c",
            "name": "Acme Model C (0902)",
            "model_creator": {"name": "Acme"},
            "evaluations": {
                "artificial_analysis_intelligence_index": 33.7,
                "artificial_analysis_engineering_index": 39.3,
            },
        },
        {
            "slug": "acme-model-d-next",
            "name": "Acme Model D-Next",
            "model_creator": {"name": "Acme"},
            "evaluations": {
                "artificial_analysis_intelligence_index": 28.6,
                "artificial_analysis_engineering_index": 33.0,
            },
        },
        {
            "slug": "vendor-model-e",
            "name": "Vendor Model E",
            "model_creator": {"name": "Vendor"},
            "evaluations": {
                "artificial_analysis_intelligence_index": 34.6,
                "artificial_analysis_engineering_index": 41.1,
            },
        },
        {
            "slug": "maker-model-f",
            "name": "Model F 1.0",
            "model_creator": {"name": "Maker"},
            "evaluations": {
                "artificial_analysis_intelligence_index": 17.7,
                "artificial_analysis_engineering_index": 25.2,
            },
        },
        {
            "slug": "explicit-max",
            "name": "Explicit (Max)",
            "evaluations": {"artificial_analysis_intelligence_index": 40.0},
        },
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": items}))
    )
    bench = fetch_aa_benchmarks("k", client=client)

    models = [
        make_model(
            "model-a",
            name="Acme Model A 1017",
            canonical_id="acme/model-a-1017",
        ),
        make_model("model-b", name="Model B", canonical_id="acme/model-b"),
        make_model("model-c", name="Model C", canonical_id="acme/model-c"),
        make_model("model-d", name="Model D", canonical_id="acme/model-d"),
        make_model("model-e", name="Model E", canonical_id="vendor/model-e"),
        make_model("model-f", name="Model F", canonical_id="maker/model-f-1.0-501b"),
    ]
    merged = merge_catalogue_metadata(
        models,
        aa_benchmarks=bench,
    )
    by_id = {m.id: m for m in merged}
    assert by_id["model-a"].aa_iq == 32.6
    assert by_id["model-a"].aa_coding == 37.6
    assert by_id["model-b"].aa_iq == 35.5
    assert by_id["model-c"].aa_iq == 33.7
    assert by_id["model-d"].aa_iq == 28.6
    assert by_id["model-e"].aa_iq == 34.6
    assert by_id["model-f"].aa_iq == 17.7
    assert bench["explicitmax"]["iq"] == 40.0


def test_aa_exact_release_beats_generic_id():
    # A catalogue ID may serve a dated release; AA also lists a generic variant. The exact
    # release-specific name/canonical_id must win over the generic catalogue ID.
    items = [
        {
            "slug": "acme-model-a",
            "name": "Acme Model A (Max)",
            "evaluations": {"artificial_analysis_intelligence_index": 40.0},
        },
        {
            "slug": "acme-model-a-1017",
            "name": "Acme Model A 1017 (Max)",
            "evaluations": {"artificial_analysis_intelligence_index": 32.6},
        },
    ]
    client = httpx.Client(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"data": items}))
    )
    bench = fetch_aa_benchmarks("k", client=client)
    model = make_model(
        "model-a",
        name="Acme Model A 1017",
        canonical_id="acme/model-a-1017",
    )
    merged = merge_catalogue_metadata([model], aa_benchmarks=bench)
    assert merged[0].aa_iq == 32.6


def test_fetch_aa_benchmarks_invalid_payloads_and_error_reporting():
    for bad_payload in ("not-a-container", {"data": "not-a-list"}):
        client = httpx.Client(
            transport=httpx.MockTransport(lambda r, p=bad_payload: httpx.Response(200, json=p))
        )
        errors: list[str] = []
        assert fetch_aa_benchmarks("k", client=client, errors=errors) == {}
        assert errors == ["Artificial Analysis fetch failed (ValueError)"]


def test_save_snapshot_atomic_replace_keeps_old_on_failure(tmp_path, monkeypatch):
    import os

    snap = Snapshot(schema_version=1, fetched_at="t", plan="go", models=[])
    path = tmp_path / "snap.json"
    save_snapshot(snap, path)
    before = path.read_text()
    assert (path.stat().st_mode & 0o777) == (0o666 & ~_umask())

    def boom(src, dst):
        raise OSError("replace failed")

    monkeypatch.setattr(os, "replace", boom)
    snap.plan = "go-plus"
    with pytest.raises(OSError):
        save_snapshot(snap, path)
    assert path.read_text() == before
    assert [p.name for p in tmp_path.iterdir()] == ["snap.json"]


def _umask() -> int:
    import os

    mask = os.umask(0)
    os.umask(mask)
    return mask


def test_lookup_named_row_matches_id_or_name():
    from opencode_go_model_optimizer.ingest.snapshot import _lookup_named_row

    by_norm = {
        norm_name("Alpha Max"): {"retention_days": 0},
        norm_name("beta-flash"): {"retention_days": 30},
    }

    by_name = make_model("custom-id", name="Alpha Max")
    assert _lookup_named_row(by_name, by_norm)[1]["retention_days"] == 0

    by_id = make_model("beta-flash", name="Drifted Display Name")
    assert _lookup_named_row(by_id, by_norm)[1]["retention_days"] == 30

    assert _lookup_named_row(make_model("missing", name="Missing"), by_norm) is None


def test_default_file_mode_and_atomic_write(tmp_path):
    from opencode_go_model_optimizer.ingest.http import _default_file_mode, atomic_write_text

    mode = _default_file_mode()
    assert isinstance(mode, int)
    assert (mode & ~0o777) == 0
    # Write new file to verify default mode applies on FileNotFoundError
    target = tmp_path / "test_file.txt"
    atomic_write_text(target, "hello world")
    assert target.read_text(encoding="utf-8") == "hello world"
    assert (target.stat().st_mode & 0o777) == mode


def test_extract_multipliers_malformed():
    from opencode_go_model_optimizer.ingest.catalogue import _extract_multipliers

    # Non-dict item (line 103)
    assert _extract_multipliers({"multipliers": ["not-a-dict"]}) == []

    # Missing or non-dict "when" (line 103)
    assert _extract_multipliers({"multipliers": [{"factor": 2.0}]}) == []
    assert _extract_multipliers({"multipliers": [{"factor": 2.0, "when": "not-a-dict"}]}) == []

    # factor is bool or non-numeric (line 106)
    assert _extract_multipliers({"multipliers": [{"factor": True, "when": {}}]}) == []
    assert _extract_multipliers({"multipliers": [{"factor": "2.0", "when": {}}]}) == []

    # Valid multiplier parses
    res = _extract_multipliers(
        {"multipliers": [{"factor": 2.5, "when": {"date": {"gte": "2026-12-01"}}}]}
    )
    assert len(res) == 1
    assert res[0].factor == 2.5


def test_fetch_catalogue_http_empty_models(monkeypatch):
    from unittest.mock import MagicMock

    from opencode_go_model_optimizer.ingest.catalogue import fetch_catalogue

    mock_client = MagicMock()
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"object": "list", "data": []}
    mock_resp.raise_for_status = MagicMock()
    mock_client.get.return_value = mock_resp

    monkeypatch.setattr(
        "opencode_go_model_optimizer.ingest.catalogue._fetch_catalogue_cli",
        lambda profiles=None: [],
    )
    with pytest.raises(ValueError, match="catalogue HTTP response contained no models"):
        fetch_catalogue(client=mock_client)


def test_parse_valid_through_invalid_date():
    from opencode_go_model_optimizer.ingest.docs import _parse_valid_through

    # Regex matches words + digits + comma + digits, but strptime fails (lines 239-240)
    assert _parse_valid_through("valid through InvalidMonth 99, 2026") is None


def test_parse_privacy_short_or_empty_name_row():
    from opencode_go_model_optimizer.ingest.docs import parse_privacy

    mdx = """
## Privacy

| Model | Data retention |
| --- | --- |
| OnlyOneCell |
| <br> | 0 days |
| Real Model | 30 days |
"""
    priv = parse_privacy(mdx)
    assert "Real Model" in priv
    assert "" not in priv
    assert priv["Real Model"]["retention_days"] == 30


def test_package_version_not_found(monkeypatch):
    from importlib.metadata import PackageNotFoundError

    from opencode_go_model_optimizer.ingest.http import package_version

    def mock_version(name):
        raise PackageNotFoundError(name)

    monkeypatch.setattr("opencode_go_model_optimizer.ingest.http.version", mock_version)
    assert package_version() == "0+unknown"


def test_default_file_mode_proc_status_fallback(monkeypatch):
    from opencode_go_model_optimizer.ingest.http import _default_file_mode

    orig_read_text = Path.read_text

    def mock_read_text(self, *args, **kwargs):
        if str(self) == "/proc/self/status":
            raise OSError("simulated missing proc")
        return orig_read_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", mock_read_text)
    mode = _default_file_mode()
    assert isinstance(mode, int)


def test_merge_privacy_warnings_none():
    from opencode_go_model_optimizer.ingest.snapshot import merge_privacy

    m = make_model("test-model", name="Test Model")
    privacy_dict = {"Test Model": {"trains_on_data": False, "zdr": True}}
    merge_privacy([m], privacy_dict, warnings=None)
    assert m.privacy is not None
    assert m.privacy.zdr is True


def test_create_snapshot_warns_when_docs_privacy_table_is_empty():
    paid = TokenCost(input=1.0, output=2.0)
    models = [
        make_model(
            "m-priv",
            cost=paid,
            plan_allowances={"go": 15.0},
            privacy=Privacy(trains_on_data=False, zdr=True),
        ),
        make_model("m-nopriv", cost=paid, plan_allowances={"go": 15.0}),
    ]

    snap = create_snapshot(models, plan="go", privacy={})

    assert any("docs Privacy table is missing or empty" in w for w in snap.warnings)
    assert any("no docs privacy row: m-nopriv" in w for w in snap.warnings)


def test_zero_cost_unlimited_allowance_is_not_a_catalogue_anomaly():
    model = make_model("unlimited-free", plan_allowances={"go": float("inf")})

    snap = create_snapshot([model], plan="go")
    estimates = estimate_requests("unlimited-free", model.cost, model.plan_allowances, None, None)

    assert not any("catalogue anomaly" in w for w in snap.warnings)
    assert estimates["go"].per_5h == float("inf")


def test_estimate_requests_skips_zero_cost_catalogue_anomaly():
    assert estimate_requests("anom", TokenCost(), {"go": 15.0}, None, None) == {}


def test_request_estimate_is_reexported_from_ingest():
    from opencode_go_model_optimizer.ingest import RequestEstimate as ExportedRequestEstimate

    assert ExportedRequestEstimate is RequestEstimate


def test_create_snapshot_unsupported_multiplier_warning():
    from opencode_go_model_optimizer.ingest.models import PriceMultiplier

    bad_mult = PriceMultiplier(factor=1.5, when={"date": {"gte": "not-a-date"}})
    m = make_model("test/model", multipliers=[bad_mult], plan_allowances={"go": 15.0})
    snap = create_snapshot([m])
    assert any("ignoring unsupported price multiplier" in w for w in snap.warnings)


def test_source_recorder_untracked_url():
    import httpx

    from opencode_go_model_optimizer.ingest.snapshot import _SourceRecorder

    recorder = _SourceRecorder()
    req = httpx.Request("GET", "https://untracked.example.com/not-a-source")
    resp = httpx.Response(200, request=req, content=b"ignored")
    recorder(resp)
    assert recorder.sources == {}


def _go_catalogue_item(model_id: str = "alpha", **go: object) -> dict:
    default_go = {
        "monthly_usage": {"go": 15, "go-plus": 60, "go-teams": 20},
        "training": False,
        "zdr": True,
        "limited_regions": False,
    }
    default_go.update(go)
    return {
        "id": model_id,
        "name": model_id.title(),
        "type": "chat",
        "cost": {"text": {"input": {"price": 1.0}, "output": {"price": 2.0}}},
        "limit": {"context": 100000, "output": 10000},
        "go": default_go,
    }


def test_catalogue_http_reads_go_usage_and_privacy_flags():
    (model,) = parse_catalogue_http(
        {
            "object": "list",
            "data": [_go_catalogue_item("alpha", training=True, zdr=False, limited_regions=True)],
        }
    )
    assert model.model_type == "chat"
    assert model.plan_allowances == {"go": 15.0, "go-plus": 60.0, "go-teams": 20.0}
    assert model.privacy is not None
    assert model.privacy.trains_on_data is True
    assert model.privacy.zdr is False
    # Regional availability is advisory; only explicit docs text blocks on Global.
    assert model.privacy.limited_regions is True
    assert model.privacy.requires_global_region is False


def test_catalogue_http_skips_null_go_usage_for_free_models():
    item = _go_catalogue_item("free", monthly_usage=None)
    item["cost"] = {"text": {"input": {"price": 0}, "output": {"price": 0}}}
    (model,) = parse_catalogue_http({"object": "list", "data": [item]})
    assert model.plan_allowances == {}
    assert model.is_zero_cost is True


def test_create_snapshot_uses_catalogue_allowances_without_docs_pricing():
    models = parse_catalogue_http({"object": "list", "data": [_go_catalogue_item("alpha")]})
    snap = create_snapshot(models)

    alpha = next(m for m in snap.models if m.id == "alpha")
    assert alpha.monthly_allowance == 15.0
    assert not any("no per-plan usage" in w for w in snap.warnings)

    apply_plan(snap.models, "go-plus")
    assert alpha.monthly_allowance == 60.0


def test_non_chat_models_are_excluded_from_tiers():
    from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
    from opencode_go_model_optimizer.engine.tiers import select_tiers

    chat = _go_catalogue_item("chat-model")
    embedding = _go_catalogue_item("embedding-model")
    embedding["type"] = "embedding"
    models = parse_catalogue_http({"object": "list", "data": [chat, embedding]})
    apply_plan(models, "go")

    selection = select_tiers(models, SelectionCriteria(mode="allowance"))
    selected_ids = {m.id for m in selection.models}
    assert "chat-model" in selected_ids
    assert "embedding-model" not in selected_ids


def _item_with_prices(model_id: str, inp: float, cache: float, out: float, **go):
    item = _go_catalogue_item(model_id, **go)
    item["cost"] = {
        "text": {
            "input": {"price": inp, "cache": {"read": cache}},
            "output": {"price": out},
        }
    }
    return item


def test_catalogue_http_computes_console_request_estimates():
    (model,) = parse_catalogue_http(
        {
            "object": "list",
            "data": [
                _item_with_prices(
                    "test-model",
                    1.0,
                    0.5,
                    2.0,
                    monthly_usage={"go": 15, "go-plus": 60, "go-teams": 16},
                )
            ],
        },
        profiles=TEST_CONSOLE_PROFILES,
    )
    assert model.request_estimates["go"].per_5h == 114
    assert model.request_estimates["go-plus"].per_5h == 455
    assert model.request_estimates["go"].per_week == 284
    assert model.request_estimates["go"].per_month == 568


def test_catalogue_http_request_estimates_use_default_profile():
    (model,) = parse_catalogue_http(
        {
            "object": "list",
            "data": [
                _item_with_prices(
                    "unknown-model",
                    1.0,
                    0.5,
                    2.0,
                    monthly_usage={"go": 15, "go-plus": 60, "go-teams": 16},
                )
            ],
        },
        profiles=RequestProfiles(default=(800, 65_000, 250), by_model={}),
    )
    assert model.request_estimates["go"].per_5h == 89


def test_catalogue_http_go_requests_field_is_authoritative():
    (model,) = parse_catalogue_http(
        {
            "object": "list",
            "data": [
                _item_with_prices(
                    "test-model",
                    1.0,
                    0.5,
                    2.0,
                    monthly_usage={"go": 15, "go-plus": 60, "go-teams": 16},
                    requests=100,
                )
            ],
        },
        profiles=RequestProfiles(default=(1, 1, 1), by_model={"test-model": (1, 1, 1)}),
    )
    assert model.request_estimates["go"].per_5h == 100
    assert model.request_estimates["go-plus"].per_5h == 400


def test_catalogue_http_request_estimates_are_unlimited_for_free_models():
    item = _go_catalogue_item("free", monthly_usage=None)
    item["cost"] = {"text": {"input": {"price": 0}, "output": {"price": 0}}}
    (model,) = parse_catalogue_http({"object": "list", "data": [item]})
    for plan in ("go", "go-plus", "go-teams"):
        assert model.request_estimates[plan].per_5h == float("inf")


def test_apply_plan_attaches_matching_request_estimate():
    (model,) = parse_catalogue_http(
        {
            "object": "list",
            "data": [
                _item_with_prices(
                    "test-model",
                    1.0,
                    0.5,
                    2.0,
                    monthly_usage={"go": 15, "go-plus": 60, "go-teams": 16},
                )
            ],
        },
        profiles=TEST_CONSOLE_PROFILES,
    )
    apply_plan([model], "go-plus")
    assert model.request_estimate is not None
    assert model.request_estimate.per_5h == 455


def test_parse_console_profiles_extracts_map_and_default():
    js = (
        "var defaultProfile={input:800,cached:65e3,output:250},"
        'profiles={"alpha":{input:1000,cached:50000,output:200},'
        "beta:{input:700,cached:40000,output:150}};"
    )
    profiles = parse_console_profiles(js, source_url="test://console")
    assert profiles is not None
    assert profiles.default == (800, 65_000, 250)
    assert profiles.by_model["alpha"] == (1_000, 50_000, 200)
    assert profiles.by_model["beta"] == (700, 40_000, 150)
    assert profiles.source_url == "test://console"


def test_parse_console_profiles_skips_zeroed_default():
    js = (
        "const z={input:0,cached:0,output:0},"
        "d={input:800,cached:65e3,output:250},"
        'm={"alpha":{input:500,cached:5e4,output:200}};'
    )
    profiles = parse_console_profiles(js)
    assert profiles is not None
    assert profiles.default == (800, 65_000, 250)
    assert profiles.by_model == {"alpha": (500, 50_000, 200)}

    no_valid_default = (
        'const z={input:0,cached:0,output:0},m={"alpha":{input:500,cached:5e4,output:200}};'
    )
    assert parse_console_profiles(no_valid_default) is None


def test_parse_console_profiles_accepts_single_quoted_model_ids():
    js = (
        "d={input:800,cached:65e3,output:250},m={'alpha-max.v1':{input:500,cached:5e4,output:200}};"
    )
    profiles = parse_console_profiles(js)
    assert profiles is not None
    assert profiles.by_model == {"alpha-max.v1": (500, 50_000, 200)}


def test_parse_console_profiles_rejects_zeroed_model_entries():
    js = (
        "d={input:800,cached:65000,output:250},"
        'm={"zeroed":{input:0,cached:0,output:0},'
        '"partial":{input:100,cached:0,output:50},'
        '"valid":{input:500,cached:5e4,output:200}};'
    )
    profiles = parse_console_profiles(js)
    assert profiles is not None
    assert profiles.by_model == {"valid": (500, 50_000, 200)}


def test_parse_console_profiles_rejects_missing_table():
    assert parse_console_profiles("no profiles here") is None
    assert parse_console_profiles("var yr={input:800,cached:65000,output:250};") is None


def test_fetch_console_profiles_reads_the_component_bundle():
    page = '<script src="/console/_build/assets/go-abc.js"></script>'
    js = 'yr={input:800,cached:65e3,output:250};br={"m":{input:1,cached:2,output:3}};'

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/console/go":
            return httpx.Response(200, text=page)
        if request.url.path.endswith("/go-abc.js"):
            return httpx.Response(200, text=js)
        return httpx.Response(500)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    profiles = fetch_console_profiles(client=client)
    assert profiles is not None
    assert profiles.by_model["m"] == (1, 2, 3)


def test_fetch_console_profiles_reports_failure():
    errors: list[str] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(503)))
    assert fetch_console_profiles(client=client, errors=errors) is None
    assert errors and "console profile fetch failed" in errors[0]


def test_apply_request_estimates_without_console_or_go_requests_is_empty():
    from opencode_go_model_optimizer.ingest.catalogue import apply_request_estimates

    model = make_model(
        "m",
        input=1.0,
        output=1.0,
        plan_allowances={"go": 15.0},
    )
    apply_request_estimates([model], None)
    assert model.request_estimates == {}


def test_fetch_and_build_applies_scraped_console_profiles():
    model = make_model(
        "test-model",
        input=1.0,
        output=2.0,
        cache_read=0.5,
        plan_allowances={"go": 15.0},
    )
    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=[model],
        ),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=""),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev", return_value={}),
    ):
        snap = fetch_and_build_snapshot(plan="go")
    assert snap.models[0].request_estimates["go"].per_5h == 114


def test_fetch_and_build_warns_when_console_profiles_are_unavailable():
    model = make_model(
        "test-model",
        input=1.0,
        output=2.0,
        cache_read=0.5,
        plan_allowances={"go": 15.0},
    )
    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=[model],
        ),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=""),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev", return_value={}),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_console_profiles",
            return_value=None,
        ),
    ):
        snap = fetch_and_build_snapshot(plan="go")
    assert snap.models[0].request_estimates == {}
    assert any("console request profiles" in w for w in snap.warnings)


def test_artificial_analysis_module_exports_and_lookup():
    from opencode_go_model_optimizer import ingest
    from opencode_go_model_optimizer.ingest import artificial_analysis

    assert ingest.fetch_aa_benchmarks is artificial_analysis.fetch_aa_benchmarks
    assert ingest.lookup_aa_benchmark is artificial_analysis.lookup_aa_benchmark

    model = make_model("glm-5", name="GLM-5")
    benchmarks = {"glm5": {"iq": 88.0, "coding": 82.0, "tps": 90.0}}
    assert artificial_analysis.lookup_aa_benchmark(model, benchmarks) == benchmarks["glm5"]
    assert artificial_analysis.lookup_aa_benchmark(model, {"glm-5": benchmarks["glm5"]}) is None
    assert artificial_analysis.lookup_aa_benchmark(model, {}) is None


def test_aa_benchmark_cache_lifecycle(tmp_path):
    from datetime import UTC, datetime

    from opencode_go_model_optimizer.ingest.artificial_analysis import (
        get_aa_cache_path,
        load_cached_aa_benchmarks_with_age,
        save_cached_aa_benchmarks,
    )

    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path)}):
        assert save_cached_aa_benchmarks({}) is None
        assert load_cached_aa_benchmarks_with_age(ttl_seconds=3600) is None

        data = {"glm5": {"iq": 88.0, "coding": 82.0, "tps": 90.0}}
        saved = save_cached_aa_benchmarks(data)
        assert saved == get_aa_cache_path()
        assert saved is not None and saved.exists()

        assert load_cached_aa_benchmarks_with_age(ttl_seconds=0) is None
        loaded = load_cached_aa_benchmarks_with_age(ttl_seconds=3600)
        assert loaded is not None and loaded[0] == data

        # Age is computed from payload fetched_at even when mtime is brand new (CI restore).
        old_ts = datetime.fromtimestamp(time.time() - 7200, tz=UTC).isoformat(timespec="seconds")
        saved.write_text(
            json.dumps({"schema_version": 1, "fetched_at": old_ts, "benchmarks": data}),
            encoding="utf-8",
        )
        assert load_cached_aa_benchmarks_with_age(ttl_seconds=3600) is None
        loaded_age = load_cached_aa_benchmarks_with_age(ttl_seconds=86400)
        assert loaded_age is not None
        assert loaded_age[0] == data
        assert loaded_age[1] >= 7199

        # Falls back to mtime when fetched_at is absent or unparseable.
        saved.write_text(
            json.dumps({"schema_version": 1, "fetched_at": "bad-ts", "benchmarks": data}),
            encoding="utf-8",
        )
        loaded = load_cached_aa_benchmarks_with_age(ttl_seconds=3600)
        assert loaded is not None and loaded[0] == data
        with patch("time.time", return_value=time.time() + 4000):
            assert load_cached_aa_benchmarks_with_age(ttl_seconds=3600) is None

        saved.write_text('{"schema_version": 99, "benchmarks": {}}', encoding="utf-8")
        assert load_cached_aa_benchmarks_with_age(ttl_seconds=3600) is None

        saved.write_text("{corrupt", encoding="utf-8")
        assert load_cached_aa_benchmarks_with_age(ttl_seconds=3600) is None

        with patch(
            "opencode_go_model_optimizer.ingest.cache.atomic_write_text",
            side_effect=OSError("disk full"),
        ):
            assert save_cached_aa_benchmarks(data) is None


def test_aa_rate_limit_cooldown_lifecycle_and_zero_request_short_circuit(tmp_path):
    from opencode_go_model_optimizer.ingest.artificial_analysis import (
        clear_aa_rate_limit,
        get_aa_ratelimit_path,
        load_aa_rate_limit,
        save_aa_rate_limit,
    )

    secret_key = "aa_super_secret_token"
    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path)}):
        assert load_aa_rate_limit() is None
        assert save_aa_rate_limit(int(time.time()) - 10) is None
        assert save_aa_rate_limit(True) is None
        assert save_aa_rate_limit(0) is None
        assert save_aa_rate_limit(10**20) is None

        future_reset = int(time.time()) + 3600
        calls = 0

        def handler_429(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(
                429,
                headers={
                    "x-ratelimit-limit": "100",
                    "x-ratelimit-remaining": "not-an-int",
                    "x-ratelimit-reset": str(future_reset),
                },
                json={"error": f"Rate limit exceeded {secret_key}"},
            )

        client = httpx.Client(transport=httpx.MockTransport(handler_429))
        errors1: list[str] = []
        stats1: dict[str, object] = {}
        assert fetch_aa_benchmarks(secret_key, client=client, errors=errors1, stats=stats1) == {}
        assert calls == 1
        assert stats1["requests"] == 1
        assert stats1["ratelimit_limit"] == 100
        assert "ratelimit_remaining" not in stats1
        assert get_aa_ratelimit_path().exists()
        assert secret_key not in get_aa_ratelimit_path().read_text(encoding="utf-8")

        # Second call during cooldown makes ZERO HTTP requests.
        errors2: list[str] = []
        stats2: dict[str, object] = {}
        assert fetch_aa_benchmarks(secret_key, client=client, errors=errors2, stats=stats2) == {}
        assert calls == 1
        assert stats2["requests"] == 0
        assert stats2["cooldown_active"] is True
        assert "skipped live request during local cooldown" in errors2[0]

        # Retry-After fallback when x-ratelimit-reset is absent.
        clear_aa_rate_limit()
        assert load_aa_rate_limit() is None

        def handler_retry_after(request: httpx.Request) -> httpx.Response:
            return httpx.Response(429, headers={"retry-after": "120"}, json={"error": "slow down"})

        retry_client = httpx.Client(transport=httpx.MockTransport(handler_retry_after))
        assert fetch_aa_benchmarks("k", client=retry_client, errors=[]) == {}
        loaded_rl = load_aa_rate_limit()
        assert loaded_rl is not None
        assert loaded_rl["reset_at"] > time.time()

        # Successful live fetch clears any leftover expired/stale cooldown file.
        clear_aa_rate_limit()
        ok_client = httpx.Client(
            transport=httpx.MockTransport(
                lambda _: httpx.Response(
                    200,
                    headers={"x-ratelimit-limit": "100", "x-ratelimit-remaining": "99"},
                    json={"data": [{"slug": "m1", "name": "M1", "evaluations": {}}]},
                )
            )
        )
        ok_stats: dict[str, object] = {}
        res = fetch_aa_benchmarks("k", client=ok_client, stats=ok_stats)
        assert "m1" in res
        assert ok_stats["requests"] == 1
        assert ok_stats["pages_fetched"] == 1
        assert ok_stats["ratelimit_remaining"] == 99
        assert not get_aa_ratelimit_path().exists()

        # Corrupt / expired / invalid reset / invalid save paths handled cleanly.
        rl_path = save_aa_rate_limit(int(time.time()) + 60)
        assert rl_path is not None
        rl_path.write_text(
            json.dumps({"schema_version": 1, "reset_at": int(time.time()) - 5}),
            encoding="utf-8",
        )
        assert load_aa_rate_limit() is None
        rl_path.write_text("{bad", encoding="utf-8")
        assert load_aa_rate_limit() is None
        rl_path.write_text('{"schema_version": 2}', encoding="utf-8")
        assert load_aa_rate_limit() is None
        with patch(
            "opencode_go_model_optimizer.ingest.cache.atomic_write_text",
            side_effect=OSError("full"),
        ):
            assert save_aa_rate_limit(int(time.time()) + 60) is None


def test_aa_preflight_quota_guard_aborts_incomplete_pagination(tmp_path):
    from opencode_go_model_optimizer.ingest.artificial_analysis import load_aa_rate_limit

    with patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path)}):
        # remaining=1 but total_pages=4 -> needs 3 more pages -> aborts after page 1.
        pages_hit = 0

        def handler_low_quota(request: httpx.Request) -> httpx.Response:
            nonlocal pages_hit
            pages_hit += 1
            return httpx.Response(
                200,
                headers={
                    "x-ratelimit-limit": "100",
                    "x-ratelimit-remaining": "1",
                    "x-ratelimit-reset": str(int(time.time()) + 1800),
                },
                json={
                    "data": [{"slug": "m1", "name": "M1", "evaluations": {}}],
                    "pagination": {"current_page": 1, "total_pages": 4, "has_more": True},
                },
            )

        client = httpx.Client(transport=httpx.MockTransport(handler_low_quota))
        errors: list[str] = []
        assert fetch_aa_benchmarks("k", client=client, errors=errors) == {}
        assert pages_hit == 1
        assert (
            "quota too low to finish pagination (1 request(s) remaining, 3 more page(s) needed"
            in errors[0]
        )

        # remaining=0 mid-pagination -> saves cooldown file as well.
        zero_pages = 0

        def handler_zero_mid(request: httpx.Request) -> httpx.Response:
            nonlocal zero_pages
            zero_pages += 1
            return httpx.Response(
                200,
                headers={
                    "x-ratelimit-limit": "100",
                    "x-ratelimit-remaining": "0",
                    "x-ratelimit-reset": str(int(time.time()) + 1800),
                },
                json={
                    "data": [{"slug": "m1", "name": "M1", "evaluations": {}}],
                    "pagination": {"has_more": True},
                },
            )

        zero_client = httpx.Client(transport=httpx.MockTransport(handler_zero_mid))
        assert fetch_aa_benchmarks("k", client=zero_client, errors=[]) == {}
        assert zero_pages == 1
        assert load_aa_rate_limit() is not None


def test_fetch_and_build_snapshot_uses_and_populates_aa_cache(tmp_path):
    from opencode_go_model_optimizer.ingest.artificial_analysis import (
        get_aa_cache_path,
        save_aa_rate_limit,
    )

    model = make_model("snap-m", name="Snap Model", output=2.0, plan_allowances={"go": 50.0})
    aa_data = {"snapmodel": {"iq": 70.0, "coding": 65.0, "tps": 80.0}}

    def fake_live(api_key, client=None, errors=None, stats=None):
        if stats is not None:
            stats.update({"requests": 4, "pages_fetched": 4, "ratelimit_remaining": 96})
        return aa_data

    with (
        patch.dict(os.environ, {"XDG_CACHE_HOME": str(tmp_path), "AA_API_KEY": "k"}, clear=True),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue", return_value=[model]),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=""),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_models_dev", return_value={}),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_aa_benchmarks",
            side_effect=fake_live,
        ) as mock_aa,
    ):
        snap_live = fetch_and_build_snapshot(plan="go", aa_cache_ttl=86400)
        assert mock_aa.call_count == 1
        assert snap_live.metadata.benchmark_source == "aa_live"
        assert snap_live.metadata.benchmark_error is None
        assert snap_live.metadata.benchmark_stats == {
            "requests": 4,
            "pages_fetched": 4,
            "ratelimit_remaining": 96,
        }
        assert get_aa_cache_path().exists()

        # Second call with fresh cache reuses aa-benchmarks.json without hitting AA API.
        model2 = make_model("snap-m", name="Snap Model", output=2.0, plan_allowances={"go": 50.0})
        with patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue", return_value=[model2]
        ):
            snap_cached = fetch_and_build_snapshot(plan="go", aa_cache_ttl=86400)
        assert mock_aa.call_count == 1
        assert snap_cached.metadata.benchmark_source == "aa_cache"
        assert snap_cached.metadata.benchmark_stats is not None
        assert snap_cached.metadata.benchmark_stats["cache_hit"] is True
        assert snap_cached.metadata.benchmark_stats["requests"] == 0
        assert snap_cached.models[0].aa_iq == 70.0

        # use_aa=False ignores both live fetch and cached AA benchmarks.
        model3 = make_model("snap-m", name="Snap Model", output=2.0, plan_allowances={"go": 50.0})
        with patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue", return_value=[model3]
        ):
            snap_no_aa = fetch_and_build_snapshot(plan="go", use_aa=False, aa_cache_ttl=86400)
        assert snap_no_aa.metadata.benchmark_source == "none"
        assert snap_no_aa.metadata.benchmark_stats is None
        assert snap_no_aa.models[0].aa_iq is None

        # Cooldown active when cache is bypassed records cooldown telemetry without HTTP calls.
        save_aa_rate_limit(int(time.time()) + 3600, limit=100, remaining=0)
        get_aa_cache_path().unlink()
        model4 = make_model("snap-m", name="Snap Model", output=2.0, plan_allowances={"go": 50.0})
        with (
            patch(
                "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
                return_value=[model4],
            ),
            patch(
                "opencode_go_model_optimizer.ingest.snapshot.fetch_aa_benchmarks",
                side_effect=fetch_aa_benchmarks,
            ),
        ):
            snap_cool = fetch_and_build_snapshot(plan="go", aa_cache_ttl=86400)
        assert snap_cool.metadata.benchmark_source == "none"
        assert snap_cool.metadata.benchmark_stats is not None
        assert snap_cool.metadata.benchmark_stats["cooldown_active"] is True
        assert snap_cool.metadata.benchmark_stats["requests"] == 0
