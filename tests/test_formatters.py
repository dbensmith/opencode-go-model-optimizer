"""Tests for output formatters."""

import json

from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.diff import ModelChange, SnapshotDiff
from opencode_go_model_optimizer.formatters.output import (
    _format_diff_value,
    _render_table,
    explain_tiers,
    format_diff_json,
    format_diff_table,
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

    # With profile and explanations
    expl = {"heavy": {"model_id": "opencode-go/qwen", "turn_cost_usd": 0.005}}
    res3 = format_select_json(
        tiers,
        mode="pareto",
        profile="coding",
        explanations=expl,
    )
    data3 = json.loads(res3)
    assert data3["profile"] == "coding"
    assert data3["explanations"] == expl


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


def test_formatters_with_fallbacks():
    tiers = {
        "heavy": "opencode-go/mod-h1",
        "balanced": "opencode-go/mod-b1",
        "light": "opencode-go/mod-l1",
        "free": "opencode-go/mod-f1",
    }
    fallbacks = {
        "heavy": ["opencode-go/mod-h1", "opencode-go/mod-h2"],
        "balanced": ["opencode-go/mod-b1", "opencode-go/mod-b2"],
        "light": ["opencode-go/mod-l1", "opencode-go/mod-l2"],
        "free": ["opencode-go/mod-f1", "opencode-go/mod-f2"],
    }

    # JSON format with fallbacks
    res_json = format_select_json(tiers, mode="allowance", fallbacks=fallbacks)
    data = json.loads(res_json)
    assert data["fallbacks"] == fallbacks

    # Env format with fallbacks
    res_env = format_select_env(tiers, fallbacks=fallbacks)
    assert 'OPENCODE_MODEL_HEAVY="opencode-go/mod-h1"' in res_env
    assert 'OPENCODE_MODEL_HEAVY_FALLBACKS="opencode-go/mod-h1,opencode-go/mod-h2"' in res_env
    assert 'OPENCODE_MODEL_FREE_FALLBACKS="opencode-go/mod-f1,opencode-go/mod-f2"' in res_env

    # GitHub Actions output with fallbacks
    res_gh = format_select_github_output(tiers, fallbacks=fallbacks)
    assert "heavy=opencode-go/mod-h1" in res_gh
    assert "heavy_fallbacks=opencode-go/mod-h1,opencode-go/mod-h2" in res_gh
    assert "free_fallbacks=opencode-go/mod-f1,opencode-go/mod-f2" in res_gh


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


def test_explain_tiers_and_table():
    m1 = NormalizedModel(
        id="mod-1",
        provider_id="opencode-go",
        monthly_allowance=60.0,
        cost=TokenCost(input=1.0, output=2.0, cache_read=0.1),
        limit=ModelLimit(context=200000),
        aa_iq=55.0,
        aa_coding=60.0,
        speed_tps=80.0,
        bpe=0.85,
        is_pareto=True,
        composite_burn_pct=4.2,
    )
    m2 = NormalizedModel(
        id="mod-2",
        provider_id="opencode-go",
        monthly_allowance=float("inf"),
        is_zero_cost=True,
    )
    models = [m1, m2]
    tiers = {
        "heavy": "opencode-go/mod-1",
        "free": "opencode-go/mod-2",
        "light": "opencode-go/mod-missing",
    }

    # Test explain_tiers
    expl = explain_tiers(tiers, models, SelectionCriteria(profile="coding"))
    assert "heavy" in expl
    assert expl["heavy"]["model_id"] == "opencode-go/mod-1"
    assert expl["heavy"]["monthly_allowance"] == 60.0
    assert expl["heavy"]["output_cost_per_1m"] == 2.0
    assert expl["heavy"]["turn_cost_usd"] > 0.0
    assert expl["heavy"]["quota_burn_pct"] == 4.2
    assert expl["heavy"]["aa_iq"] == 55.0
    assert expl["heavy"]["aa_coding"] == 60.0
    assert expl["heavy"]["speed_tps"] == 80.0
    assert expl["heavy"]["bpe"] == 0.85
    assert expl["heavy"]["is_pareto"] is True
    assert expl["heavy"]["context"] == 200000

    assert expl["free"]["monthly_allowance"] == "unlimited"
    assert expl["light"]["model_id"] == "opencode-go/mod-missing"

    # Test format_select_table with explain=True
    tbl_explain = format_select_table(
        tiers, models, plan="go", explain=True, criteria=SelectionCriteria(profile="coding")
    )
    assert "IQ" in tbl_explain
    assert "BPE" in tbl_explain
    assert "Burn %" in tbl_explain
    assert "Turn Cost" in tbl_explain
    assert "55.0" in tbl_explain
    assert "0.850" in tbl_explain
    assert "4.2%" in tbl_explain


def _sample_diff() -> SnapshotDiff:
    return SnapshotDiff(
        old_fetched_at="2026-01-01T00:00:00Z",
        new_fetched_at="2026-02-01T00:00:00Z",
        plan="go",
        added=["new-model"],
        removed=["old-model"],
        status_changes={"old-model": {"old": "active", "new": "deprecated"}},
        pricing_changes={
            "new-model": {
                "monthly_allowance": (None, 15.0),
                "output_cost": (1.0, 2.5),
            }
        },
        tier_changes={"heavy": {"old": "old-model", "new": "new-model"}},
        changes=[ModelChange(id="new-model", change_type="added")],
    )


def test_format_diff_json_and_table():
    diff = _sample_diff()

    parsed = json.loads(format_diff_json(diff))
    assert parsed["added"] == ["new-model"]
    assert parsed["removed"] == ["old-model"]
    assert parsed["tier_changes"]["heavy"]["new"] == "new-model"

    table = format_diff_table(diff)
    assert "Snapshot diff (plan: go)" in table
    assert "old: 2026-01-01T00:00:00Z" in table
    assert "Added models:" in table
    assert "Removed models:" in table
    assert "Status changes:" in table
    assert "Pricing / allowance changes:" in table
    assert "Tier changes:" in table
    assert "new-model" in table
    assert "deprecated" in table
    assert "unlimited" not in table


def test_format_diff_table_no_changes():
    diff = SnapshotDiff(
        old_fetched_at="2026-01-01T00:00:00Z",
        new_fetched_at="2026-01-01T00:00:00Z",
        plan="go",
    )
    assert "No catalog or tier changes detected." in format_diff_table(diff)


def test_format_diff_value():
    assert _format_diff_value(None) == "-"
    assert _format_diff_value(1.0) == "1"
    assert _format_diff_value(2.5) == "2.5"
    assert _format_diff_value("unlimited") == "unlimited"


def test_format_diff_table_infinite_allowance():
    diff = SnapshotDiff(
        plan="go",
        pricing_changes={"mod": {"monthly_allowance": (float("inf"), 15.0)}},
        tier_changes={"free": {"old": "", "new": "opencode-go/mod"}},
    )
    table = format_diff_table(diff)
    assert "None" in table
    assert "unlimited" in table
