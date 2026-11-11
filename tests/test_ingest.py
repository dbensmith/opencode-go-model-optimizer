"""Tests for ingestion modules."""

import json
import os
import subprocess
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from opencode_go_model_optimizer.ingest.catalogue import (
    _extract_model_limit,
    _extract_token_cost,
    _safe_float,
    fetch_catalogue,
    parse_catalogue_cli,
    parse_catalogue_http,
)
from opencode_go_model_optimizer.ingest.docs import (
    extract_plan_section,
    fetch_docs_mdx,
    norm_name,
    parse_go_pricing,
    parse_price_value,
    parse_table_rows,
)
from opencode_go_model_optimizer.ingest.models import (
    ModelLimit,
    NormalizedModel,
    Snapshot,
    SnapshotMetadata,
    TokenCost,
)
from opencode_go_model_optimizer.ingest.snapshot import (
    apply_plan,
    create_snapshot,
    fetch_aa_benchmarks,
    fetch_and_build_snapshot,
    get_cache_snapshot_path,
    get_reference_snapshot_path,
    load_cached_snapshot,
    load_reference_snapshot,
    load_snapshot,
    merge_docs_pricing,
    save_cached_snapshot,
    save_snapshot,
    update_reference_snapshot,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def test_norm_name():
    assert norm_name("DeepSeek V4.1 Flash (Off-Peak)") == "deepseekv41flash"
    assert norm_name("Gemini 4 Argon (50% off)") == "gemini4argon"
    assert norm_name("Qwen3.7 Plus (≤ 256K tokens)") == "qwen37plus"
    assert norm_name("GLM-5.3-Flash") == "glm53flash"
    assert norm_name("<b>Kimi K3.1 Pro</b>") == "kimik31pro"
    assert norm_name("Iceman Contributor Free") == "icemancontributorfree"


def test_parse_price_value():
    assert parse_price_value("$0.50") == 0.50
    assert parse_price_value("~~$20.00~~ $10.00") == 10.00
    assert parse_price_value("Free") == 0.0
    assert parse_price_value("-") == 0.0
    assert parse_price_value("") == 0.0


def test_extract_plan_section_and_parse_table():
    mdx_text = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
    section = extract_plan_section(mdx_text, plan_id="go-pricing", slot="go")
    assert '<div slot="go">' in section
    assert '<div slot="go-plus">' not in section

    rows = parse_table_rows(section)
    assert len(rows) > 0
    # Header should be skipped
    assert rows[0][0] != "Model"


def test_parse_go_pricing():
    mdx_text = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
    pricing = parse_go_pricing(mdx_text)

    # GLM-5.3-Flash should have $60 allowance, not go-plus $200
    assert "glm53flash" in pricing
    assert pricing["glm53flash"]["allowance"] == 60.0
    assert pricing["glm53flash"]["output_cost"] == 0.50

    # DeepSeek should have $100 allowance
    assert "deepseekv41flash" in pricing
    assert pricing["deepseekv41flash"]["allowance"] == 100.0

    # Iceman should have infinite allowance (unlimited)
    assert "icemancontributorfree" in pricing
    assert pricing["icemancontributorfree"]["allowance"] == float("inf")


def test_parse_catalogue_cli():
    cli_text = (FIXTURES_DIR / "catalogue_cli.txt").read_text(encoding="utf-8")
    models = parse_catalogue_cli(cli_text)

    assert len(models) == 7
    ids = {m.id for m in models}
    assert "qwen3.8-max" in ids
    assert "deepseek-v4.1-flash" in ids
    assert "iceman-contributor-free" in ids
    assert "deprecated-old-model" in ids

    qwen = next(m for m in models if m.id == "qwen3.8-max")
    assert qwen.cost.input == 2.0
    assert qwen.cost.output == 6.0
    assert qwen.cost.cache_read == 0.25
    assert qwen.cost.cache_write == 2.5
    assert qwen.limit.context == 1000000

    iceman = next(m for m in models if m.id == "iceman-contributor-free")
    assert iceman.is_zero_cost is True
    assert iceman.is_contributor is True


def test_parse_catalogue_http():

    http_text = (FIXTURES_DIR / "catalogue_http.json").read_text(encoding="utf-8")
    data = json.loads(http_text)
    models = parse_catalogue_http(data)

    assert len(models) == 2
    qwen = models[0]
    assert qwen.id == "qwen3.8-max"
    assert qwen.cost.input == 2.0
    assert qwen.cost.output == 6.0
    assert qwen.cost.cache_read == 0.25


def test_snapshot_lifecycle(tmp_path):
    cli_text = (FIXTURES_DIR / "catalogue_cli.txt").read_text(encoding="utf-8")
    mdx_text = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")

    models = parse_catalogue_cli(cli_text)
    pricing = parse_go_pricing(mdx_text)

    snap = create_snapshot(models, pricing)
    assert snap.schema_version == 1
    assert snap.plan == "go"
    assert len(snap.models) == 7

    deepseek = next(m for m in snap.models if m.id == "deepseek-v4.1-flash")
    assert deepseek.monthly_allowance == 100.0

    out_file = tmp_path / "test_snapshot.json"
    save_snapshot(snap, out_file)
    assert out_file.exists()

    loaded = load_snapshot(out_file)
    assert loaded.schema_version == snap.schema_version
    assert len(loaded.models) == len(snap.models)
    assert loaded.models[0].id == snap.models[0].id


def test_reference_snapshot_loading():
    ref_path = get_reference_snapshot_path()
    assert ref_path.exists()
    snap = load_reference_snapshot()
    assert snap is not None
    assert snap.schema_version == 1
    assert len(snap.models) > 0
    # Confirm AA intelligence index is present on benchmarked models
    benchmarked = [m for m in snap.models if m.aa_iq is not None]
    assert len(benchmarked) > 0


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

    # Direct _safe_float edge cases
    assert _safe_float(None) == 0.0
    assert _safe_float(42) == 42.0
    assert _safe_float("3.14") == 3.14
    assert _safe_float({"default": 1.25}) == 1.25
    assert _safe_float({"standard": 2.50}) == 2.50
    assert _safe_float({"price": 5.0}) == 5.0
    assert _safe_float({"key": "invalid", "another": 7.5}) == 7.5
    assert _safe_float({"all": "invalid"}) == 0.0
    assert _safe_float("not-a-number") == 0.0

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


def test_fetch_catalogue_mocked():
    # 1. opencode CLI available and succeeds
    cli_stdout = """opencode-go/test-cli-m
{
  "id": "test-cli-m",
  "name": "Test CLI Model"
}
"""
    with (
        patch("shutil.which", return_value="/usr/local/bin/opencode"),
        patch("subprocess.run") as mock_run,
    ):
        mock_run.return_value = subprocess.CompletedProcess(
            args=["opencode"], returncode=0, stdout=cli_stdout
        )
        models = fetch_catalogue()
        assert len(models) == 1
        assert models[0].id == "test-cli-m"

    # 2. opencode CLI fails with SubprocessError -> falls back to HTTP
    http_data = {"data": [{"id": "http-m", "name": "HTTP Model"}]}
    with (
        patch("shutil.which", return_value="/usr/local/bin/opencode"),
        patch("subprocess.run", side_effect=subprocess.SubprocessError("CLI crashed")),
        patch("httpx.Client.get") as mock_get,
    ):
        mock_get.return_value = httpx.Response(
            200, json=http_data, request=httpx.Request("GET", "http://test")
        )
        models = fetch_catalogue()
        assert len(models) == 1
        assert models[0].id == "http-m"

    # 3. opencode CLI not installed, custom client provided
    custom_client = httpx.Client()
    with patch("shutil.which", return_value=None), patch.object(custom_client, "get") as mock_get:
        mock_get.return_value = httpx.Response(
            200, json=[{"id": "list-m"}], request=httpx.Request("GET", "http://test")
        )
        models = fetch_catalogue(client=custom_client)
        assert len(models) == 1
        assert models[0].id == "list-m"

    # 4. opencode CLI not installed, HTTP raises error
    with patch("shutil.which", return_value=None), patch("httpx.Client.get") as mock_get:
        mock_get.return_value = httpx.Response(500, request=httpx.Request("GET", "http://test"))
        with pytest.raises(httpx.HTTPStatusError):
            fetch_catalogue()


def test_extract_plan_section_edge_cases():
    # Missing PlanTabs tag
    assert extract_plan_section("No plan tabs here", plan_id="go-pricing", slot="go") == ""

    # Missing slot div tag
    mdx_no_slot = '<PlanTabs id="go-pricing"><div>Some content</div></PlanTabs>'
    assert extract_plan_section(mdx_no_slot, plan_id="go-pricing", slot="go") == ""

    # Missing closing </div> tag
    mdx_unclosed_div = '<PlanTabs id="go-pricing"><div slot="go">Unclosed content</PlanTabs>'
    section = extract_plan_section(mdx_unclosed_div, plan_id="go-pricing", slot="go")
    assert '<div slot="go">' in section

    # Missing closing </PlanTabs> tag
    mdx_unclosed_tabs = '<PlanTabs id="go-pricing"><div slot="go">Content</div>'
    section2 = extract_plan_section(mdx_unclosed_tabs, plan_id="go-pricing", slot="go")
    assert '<div slot="go">' in section2


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


def test_parse_go_pricing_edge_cases():
    # Missing slot falling back to 'go'
    mdx_content = """
    <PlanTabs id="go-pricing">
      <div slot="go">
        | Model | Input | Output | Cache Read | Cache Write | Monthly |
        | :--- | :--- | :--- | :--- | :--- | :--- |
        | Special Model | $1.00 | $2.00 | $0.10 | $0.50 | $75.00 |
        | Model Tied | $1.00 | $4.00 | $0.10 | $0.50 | **$100** |
        | Model Tied | $1.00 | $2.00 | $0.10 | $0.50 | **$100** |
        | | $1.00 | $2.00 | $0.10 | $0.50 | $10.00 |
      </div>
    </PlanTabs>
    """
    # Requesting non-existent slot 'unknown' should fallback to 'go'
    pricing = parse_go_pricing(mdx_content, plan="unknown")
    assert "specialmodel" in pricing
    assert pricing["specialmodel"]["allowance"] == 75.0
    assert pricing["specialmodel"]["cached_write_cost"] == 0.50

    # Model Tied should take the cheaper output ($2.00 instead of $4.00) when allowance is tied
    assert pricing["modeltied"]["output_cost"] == 2.00


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
                    "artificial_analysis_coding_index": 78.0,
                },
                "median_output_tokens_per_second": 45.2,
            },
            {
                "id": "model-id-2",
                "evaluations": {},
                "median_output_tokens_per_second": None,
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


def test_merge_docs_pricing_and_benchmarks():
    m = NormalizedModel(
        id="test-model",
        name="Test Model",
        cost=TokenCost(input=0.0, output=0.0, cache_read=0.0, cache_write=0.0),
    )
    assert m.is_zero_cost is True

    pricing = {
        "testmodel": {
            "allowance": 80.0,
            "input_cost": 1.25,
            "output_cost": 3.50,
            "cached_read_cost": 0.15,
            "cached_write_cost": 0.40,
        }
    }
    benchmarks = {
        "testmodel": {
            "iq": 72.0,
            "coding": 68.0,
            "tps": 50.0,
        }
    }
    merged = merge_docs_pricing([m], pricing, aa_benchmarks=benchmarks)
    assert merged[0].monthly_allowance == 80.0
    assert merged[0].cost.input == 1.25
    assert merged[0].cost.output == 3.50
    assert merged[0].cost.cache_read == 0.15
    assert merged[0].cost.cache_write == 0.40
    assert merged[0].is_zero_cost is False
    assert merged[0].aa_iq == 72.0
    assert merged[0].aa_coding == 68.0
    assert merged[0].speed_tps == 50.0

    # Test benchmark lookup by norm_name(name) and is_zero_cost remains True for 0 cost
    m2 = NormalizedModel(
        id="model-custom-id",
        name="Custom Display Name",
        cost=TokenCost(input=0.0, output=0.0),
    )
    pricing2 = {
        "modelcustomid": {
            "allowance": 100.0,
            "input_cost": 0.0,
            "output_cost": 0.0,
            "cached_read_cost": 0.0,
        }
    }
    benchmarks2 = {
        "customdisplayname": {
            "iq": 90.0,
            "coding": 85.0,
            "tps": 60.0,
        }
    }
    merged2 = merge_docs_pricing([m2], pricing2, aa_benchmarks=benchmarks2)
    assert merged2[0].is_zero_cost is True
    assert merged2[0].aa_iq == 90.0
    assert merged2[0].aa_coding == 85.0


def test_reference_snapshot_paths_and_corrupt_fallback(tmp_path):
    # 1. get_reference_snapshot_path fallback branch (line 143)
    with patch("opencode_go_model_optimizer.ingest.snapshot.files") as mock_files:
        mock_p = MagicMock()
        mock_p.exists.return_value = False
        mock_files.return_value.joinpath.return_value = mock_p
        fallback_p = get_reference_snapshot_path()
        assert "reference_snapshot.json" in str(fallback_p)

    # 2. load_reference_snapshot returns None when corrupt JSON
    corrupt_file = tmp_path / "corrupt_reference.json"
    corrupt_file.write_text("{ corrupt json", encoding="utf-8")
    with patch(
        "opencode_go_model_optimizer.ingest.snapshot.get_reference_snapshot_path",
        return_value=corrupt_file,
    ):
        snap = load_reference_snapshot()
        assert snap is None

    # 3. load_reference_snapshot returns None when file does not exist
    missing_file = tmp_path / "nonexistent.json"
    with patch(
        "opencode_go_model_optimizer.ingest.snapshot.get_reference_snapshot_path",
        return_value=missing_file,
    ):
        snap_missing = load_reference_snapshot()
        assert snap_missing is None


def test_fetch_and_build_and_update_reference(tmp_path):
    # Test fetch_and_build_snapshot with AA_API_KEY env set
    dummy_model = NormalizedModel(id="snap-m", name="Snap Model")
    pricing = {
        "snapmodel": {
            "allowance": 50.0,
            "output_cost": 2.0,
            "input_cost": 1.0,
            "cached_read_cost": 0.0,
        }
    }

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
            "opencode_go_model_optimizer.ingest.snapshot.parse_go_pricing",
            return_value=pricing,
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_aa_benchmarks",
            return_value={"snap-m": {"iq": 65.0, "coding": 60.0, "tps": 30.0}},
        ),
    ):
        snap = fetch_and_build_snapshot(plan="go")
        assert len(snap.models) == 1
        assert snap.models[0].aa_iq == 65.0

    # Test fetch_and_build_snapshot without AA_API_KEY and use_reference_benchmarks=True (lines 171-182)
    dummy_model2 = NormalizedModel(id="snap-m", name="Snap Model")
    ref_with_bench = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="go",
        models=[
            NormalizedModel(
                id="snap-m",
                name="Snap Model",
                aa_iq=88.0,
                aa_coding=80.0,
                speed_tps=40.0,
            )
        ],
        metadata=SnapshotMetadata(),
    )
    with (
        patch.dict(os.environ, {}, clear=True),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=[dummy_model2],
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx",
            return_value="",
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.parse_go_pricing",
            return_value=pricing,
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.load_reference_snapshot",
            return_value=ref_with_bench,
        ),
    ):
        snap_ref = fetch_and_build_snapshot(plan="go", use_reference_benchmarks=True)
        assert len(snap_ref.models) == 1
        assert snap_ref.models[0].aa_iq == 88.0

    # Test update_reference_snapshot
    ref_target = tmp_path / "updated_ref.json"
    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_and_build_snapshot",
            return_value=snap,
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.get_reference_snapshot_path",
            return_value=ref_target,
        ),
    ):
        ret_path = update_reference_snapshot(plan="go")
        assert ret_path == ref_target
        assert ref_target.exists()


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
        models=[NormalizedModel(id="cached-m")],
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
            "opencode_go_model_optimizer.ingest.snapshot.save_snapshot",
            side_effect=OSError("Disk full"),
        ):
            assert save_cached_snapshot(snap) is None


def test_fetch_and_build_snapshot_custom_client():
    # Test fetch_and_build_snapshot with user-provided client (should not close user client)
    custom_client = httpx.Client()
    dummy_model = NormalizedModel(id="custom-m")
    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=[dummy_model],
        ),
        patch("opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx", return_value=""),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.parse_go_pricing",
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

    # unknown plan leaves monthly_allowance unchanged
    apply_plan([m], "unknown-plan")
    assert m.monthly_allowance == float("inf")


def test_merge_docs_pricing_multi_plan():
    m = NormalizedModel(id="mod-multi", name="Model Multi", monthly_allowance=15.0)
    pricing_go = {
        "mod-multi": {
            "allowance": 15.0,
            "output_cost": 1.0,
            "input_cost": 0.5,
            "cached_read_cost": 0.05,
            "cached_write_cost": 0.2,
        }
    }
    all_plan_pricing = {
        "go": {"mod-multi": {"allowance": 15.0}},
        "go-plus": {"mod-multi": {"allowance": 60.0}},
        "go-teams": {"mod-multi": {"allowance": float("inf")}},
    }

    # With all_plan_pricing
    merge_docs_pricing([m], pricing_go, all_plan_pricing=all_plan_pricing)
    assert m.plan_allowances == {"go": 15.0, "go-plus": 60.0, "go-teams": float("inf")}

    # Without all_plan_pricing, consistent with "go"
    m2 = NormalizedModel(id="mod-single", name="Model Single")
    merge_docs_pricing(
        [m2],
        {
            "mod-single": {
                "allowance": 25.0,
                "output_cost": 0.5,
                "input_cost": 0.2,
                "cached_read_cost": 0.0,
                "cached_write_cost": 0.0,
            }
        },
    )
    assert m2.plan_allowances.get("go") == 25.0


def test_apply_plan_aliases_and_normalization():
    m = NormalizedModel(
        id="mod-plan",
        plan_allowances={
            "go": 15.0,
            "go-plus": 60.0,
            "go-teams": float("inf"),
            "custom-tier": 120.0,
        },
    )

    # Aliases without 'go-' prefix
    apply_plan([m], "plus")
    assert m.monthly_allowance == 60.0

    apply_plan([m], "teams")
    assert m.monthly_allowance == float("inf")

    # Custom plan without 'go-' prefix matching directly
    apply_plan([m], "custom-tier")
    assert m.monthly_allowance == 120.0


def test_fetch_and_build_snapshot_reference_benchmarks_extended_matching():
    # Model 1 has aa_coding and speed_tps, but aa_iq=None
    m_coding_only = NormalizedModel(
        id="m_coding",
        name="Coding Only Model",
        aa_iq=None,
        aa_coding=85.0,
        speed_tps=110.0,
    )
    # Model 2 has aa_iq only, and name matching
    m_named = NormalizedModel(
        id="org/pretty-model-v1",
        name="Pretty Model v1",
        aa_iq=92.0,
    )
    ref_snap = Snapshot(
        schema_version=1,
        fetched_at="2026-01-01T00:00:00Z",
        plan="reference",
        models=[m_coding_only, m_named],
        metadata=SnapshotMetadata(),
    )

    # Target catalog models: one matching m_coding by id, one matching m_named by name
    target_mod1 = NormalizedModel(id="m_coding", name="Target 1")
    target_mod2 = NormalizedModel(id="unmatched-id", name="Pretty Model v1")

    with (
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_catalogue",
            return_value=[target_mod1, target_mod2],
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.fetch_docs_mdx",
            return_value="""# Pricing\n| Model | Monthly Allowance | Input | Output | Context |\n| --- | --- | --- | --- | --- |\n| Target 1 | $15.00 | $1.00 | $2.00 | 128K |\n| Pretty Model v1 | $15.00 | $1.00 | $2.00 | 128K |\n""",
        ),
        patch(
            "opencode_go_model_optimizer.ingest.snapshot.load_reference_snapshot",
            return_value=ref_snap,
        ),
        patch.dict("os.environ", {}, clear=True),
    ):
        snap = fetch_and_build_snapshot(plan="go", use_reference_benchmarks=True)
        by_id = {m.id: m for m in snap.models}

        assert by_id["m_coding"].aa_coding == 85.0
        assert by_id["m_coding"].speed_tps == 110.0
        assert by_id["unmatched-id"].aa_iq == 92.0
