"""Tests for output formatters."""

import json

from opencode_go_model_optimizer.formatters.output import (
    _render_table,
    format_rank_json,
    format_rank_table,
    format_select_compact,
    format_select_env,
    format_select_github_output,
    format_select_json,
    format_select_table,
)
from opencode_go_model_optimizer.ingest.models import ModelLimit, NormalizedModel, TokenCost


def test_format_select_json():
    tiers = {"heavy": "opencode-go/qwen", "free": "opencode-go/iceman"}
    res = format_select_json(
        tiers, mode="pareto", plan="go-plus", evaluated_at="2026-11-10T00:00:00Z"
    )
    data = json.loads(res)
    assert data["plan"] == "go-plus"
    assert data["mode"] == "pareto"
    assert data["evaluated_at"] == "2026-11-10T00:00:00Z"
    assert data["tiers"] == tiers

    # Default evaluated_at
    res2 = format_select_json(tiers, mode="allowance")
    data2 = json.loads(res2)
    assert "evaluated_at" in data2


def test_format_select_compact():
    tiers = {"heavy": "opencode-go/qwen", "balanced": "", "free": "opencode-go/iceman"}
    compact = format_select_compact(tiers)
    lines = compact.splitlines()
    assert len(lines) == 2
    assert "heavy opencode-go/qwen" in lines
    assert "free opencode-go/iceman" in lines


def test_format_select_env():
    tiers = {
        "heavy": "opencode-go/kimi-k3.1-pro",
        "balanced": "opencode-go/deepseek-v4.1-flash",
        "light": "opencode-go/mimo-v2.6-flash",
        "free": "opencode-go/gemini-4-flash",
    }
    env_str = format_select_env(tiers)
    assert 'OPENCODE_MODEL_HEAVY="opencode-go/kimi-k3.1-pro"' in env_str
    assert 'OPENCODE_MODEL_BALANCED="opencode-go/deepseek-v4.1-flash"' in env_str
    assert 'OPENCODE_MODEL_LIGHT="opencode-go/mimo-v2.6-flash"' in env_str
    assert 'OPENCODE_MODEL_FREE="opencode-go/gemini-4-flash"' in env_str

    # Missing tier defaults to empty string
    partial = {"heavy": "opencode-go/kimi"}
    partial_env = format_select_env(partial)
    assert 'OPENCODE_MODEL_HEAVY="opencode-go/kimi"' in partial_env
    assert 'OPENCODE_MODEL_BALANCED=""' in partial_env
    assert 'OPENCODE_MODEL_LIGHT=""' in partial_env
    assert 'OPENCODE_MODEL_FREE=""' in partial_env


def test_format_select_github_output():
    tiers = {
        "heavy": "opencode-go/kimi-k3.1-pro",
        "balanced": "opencode-go/deepseek-v4.1-flash",
        "light": "opencode-go/mimo-v2.6-flash",
        "free": "opencode-go/gemini-4-flash",
    }
    gh_str = format_select_github_output(tiers)
    assert "heavy=opencode-go/kimi-k3.1-pro" in gh_str
    assert "balanced=opencode-go/deepseek-v4.1-flash" in gh_str
    assert "light=opencode-go/mimo-v2.6-flash" in gh_str
    assert "free=opencode-go/gemini-4-flash" in gh_str

    # Missing tier defaults to empty string
    partial = {"heavy": "opencode-go/kimi"}
    partial_gh = format_select_github_output(partial)
    assert "heavy=opencode-go/kimi" in partial_gh
    assert "balanced=" in partial_gh
    assert "light=" in partial_gh
    assert "free=" in partial_gh


def test_format_select_table_with_missing_and_varied_models():
    m1 = NormalizedModel(
        id="model-inf",
        provider_id="opencode-go",
        monthly_allowance=float("inf"),
        is_zero_cost=True,
        limit=ModelLimit(context=1000000),
    )
    m2 = NormalizedModel(
        id="model-dollar",
        provider_id="opencode-go",
        monthly_allowance=60.0,
        cost=TokenCost(output=0.50),
        limit=ModelLimit(context=0),
    )
    m3 = NormalizedModel(
        id="model-none",
        provider_id="opencode-go",
        monthly_allowance=None,
        cost=TokenCost(output=1.00),
    )
    models = [m1, m2, m3]

    tiers = {
        "heavy": "opencode-go/model-dollar",
        "balanced": "opencode-go/model-inf",
        "light": "opencode-go/model-none",
        "free": "opencode-go/missing-model",
        "other": "",
    }
    table = format_select_table(tiers, models, plan="go")
    assert "Allowance (go)" in table
    assert "Unlimited" in table
    assert "$60.00" in table
    assert "Free" in table
    assert "opencode-go/missing-model" in table
    assert "None" in table


def test_format_rank_table_pareto():
    m1 = NormalizedModel(
        id="pareto-m1",
        rank=1,
        tier="heavy",
        bpe=0.952,
        aa_iq=75.5,
        composite_burn_pct=2.5,
        monthly_allowance=100.0,
        cost=TokenCost(output=2.0),
        release_date="2026-10-01",
    )
    m2 = NormalizedModel(
        id="pareto-m2",
        rank=None,
        tier=None,
        bpe=None,
        aa_iq=None,
        composite_burn_pct=None,
        monthly_allowance=float("inf"),
        is_zero_cost=True,
        release_date="",
    )
    m3 = NormalizedModel(
        id="pareto-m3",
        monthly_allowance=None,
        cost=TokenCost(output=0.0),
    )
    table = format_rank_table([m1, m2, m3], mode="pareto")
    assert "Tier" in table
    assert "heavy" in table
    assert "BPE" in table
    assert "IQ" in table
    assert "Burn %" in table
    assert "0.952" in table
    assert "75.5" in table
    assert "2.5%" in table
    assert "Unlimited" in table
    assert "Free" in table
    assert "-" in table


def test_format_rank_table_allowance():
    m1 = NormalizedModel(
        id="allow-m1",
        rank=1,
        tier="balanced",
        monthly_allowance=50.0,
        cost=TokenCost(output=1.5),
        release_date="2026-08-01",
        limit=ModelLimit(context=500000),
        status="active",
    )
    m2 = NormalizedModel(
        id="allow-m2",
        rank=None,
        tier=None,
        monthly_allowance=float("inf"),
        is_zero_cost=True,
        release_date="",
        limit=ModelLimit(context=0),
        status="deprecated",
    )
    table = format_rank_table([m1, m2], mode="allowance")
    assert "Tier" in table
    assert "balanced" in table
    assert "Context" in table
    assert "Status" in table
    assert "500,000" in table
    assert "deprecated" in table
    assert "Unlimited" in table


def test_format_rank_json():
    m = NormalizedModel(id="json-m", monthly_allowance=40.0)
    data_str = format_rank_json([m])
    data = json.loads(data_str)
    assert isinstance(data, list)
    assert len(data) == 1
    assert data[0]["id"] == "json-m"
    assert data[0]["monthly_allowance"] == 40.0


def test_render_table_column_mismatch():
    headers = ["Col1", "Col2", "Col3"]
    # Row 1 has fewer columns (2 cols) -> padded with "-"
    # Row 2 has more columns (4 cols) -> truncated to 3 cols
    rows = [
        ["val1", "val2"],
        ["valA", "valB", "valC", "extraValD"],
    ]
    rendered = _render_table(headers, rows)
    assert "Col1" in rendered
    assert "Col2" in rendered
    assert "Col3" in rendered
    assert "val1" in rendered
    assert "val2" in rendered
    assert "valC" in rendered
    assert "extraValD" not in rendered
