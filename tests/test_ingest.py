"""Tests for ingestion modules."""

from pathlib import Path

from opencode_go_model_optimizer.ingest.catalogue import (
    parse_catalogue_cli,
    parse_catalogue_http,
)
from opencode_go_model_optimizer.ingest.docs import (
    extract_plan_section,
    norm_name,
    parse_go_pricing,
    parse_price_value,
    parse_table_rows,
)
from opencode_go_model_optimizer.ingest.snapshot import (
    create_snapshot,
    load_snapshot,
    save_snapshot,
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
    import json

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
