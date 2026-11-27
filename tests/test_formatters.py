"""Tests for output formatters."""

import json
from datetime import UTC, datetime

import pytest

from opencode_go_model_optimizer import formatters
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.diff import ModelChange, SnapshotDiff
from opencode_go_model_optimizer.formatters import output as compat_output
from opencode_go_model_optimizer.formatters.diff import (
    _format_diff_value,
    format_diff_json,
    format_diff_table,
)
from opencode_go_model_optimizer.formatters.markdown import (
    fence_for,
    is_number,
    render_details_block,
    render_mermaid_flowchart,
    sanitize_mermaid_id,
)
from opencode_go_model_optimizer.formatters.rank import format_rank_json, format_rank_table
from opencode_go_model_optimizer.formatters.select import (
    explain_tiers,
    format_select_compact,
    format_select_env,
    format_select_github_output,
    format_select_json,
    format_select_table,
    settings_payload,
    tier_display_names,
)
from opencode_go_model_optimizer.formatters.table import (
    _append_column,
    _render_table,
    format_multiplier,
    render_table,
)
from opencode_go_model_optimizer.ingest.models import (
    ModelLimit,
    NormalizedModel,
    PriceMultiplier,
    Privacy,
    RequestEstimate,
    TokenCost,
)


def test_format_select_json():
    tiers = {"heavy": "opencode-go/alpha", "free": "opencode-go/delta"}
    res = format_select_json(
        tiers, mode="pareto", plan="go-plus", evaluated_at="2026-11-10T00:00:00Z"
    )
    data = json.loads(res)
    assert data["plan"] == "go-plus"
    assert data["mode"] == "pareto"
    assert data["evaluated_at"] == "2026-11-10T00:00:00Z"
    assert data["tiers"] == tiers
    assert "names" not in data

    # Display names mirror tiers when given
    names = {"heavy": "Alpha", "free": "Delta Free"}
    res_names = format_select_json(tiers, mode="pareto", names=names)
    data_names = json.loads(res_names)
    assert data_names["names"] == names
    assert list(data_names)[:3] == ["plan", "tiers", "names"]

    # Default evaluated_at
    res2 = format_select_json(tiers, mode="allowance")
    data2 = json.loads(res2)
    assert "evaluated_at" in data2

    # With profile and explanations
    expl = {"heavy": {"model_id": "opencode-go/alpha", "turn_cost_usd": 0.005}}
    res3 = format_select_json(
        tiers,
        mode="pareto",
        profile="coding",
        explanations=expl,
    )
    data3 = json.loads(res3)
    assert data3["profile"] == "coding"
    assert data3["explanations"] == expl


def test_tier_display_names():
    named = NormalizedModel(
        id="alpha", provider_id="opencode-go", name="Alpha Max", status="active"
    )
    unnamed = NormalizedModel(id="beta", provider_id="opencode-go")
    models = [named, unnamed]

    tiers = {
        "heavy": "opencode-go/alpha",
        "balanced": "opencode-go/beta",
        "light": "opencode-go/missing",
        "free": "",
    }
    assert tier_display_names(tiers, models) == {
        "heavy": "Alpha Max",
        "balanced": "",
        "light": "",
        "free": "",
    }


def test_format_select_compact():
    tiers = {"heavy": "opencode-go/alpha", "balanced": "", "free": "opencode-go/delta"}
    compact = format_select_compact(tiers)
    lines = compact.splitlines()
    assert len(lines) == 2
    assert "heavy opencode-go/alpha" in lines
    assert "free opencode-go/delta" in lines

    # Display names are appended alongside the model IDs
    named = format_select_compact(tiers, names={"heavy": "Alpha Max", "free": "Delta"})
    named_lines = named.splitlines()
    assert "heavy opencode-go/alpha Alpha Max" in named_lines
    assert "free opencode-go/delta Delta" in named_lines

    # A tier without a name keeps its two-field line
    partial = format_select_compact(tiers, names={"heavy": "Alpha Max"})
    assert "free opencode-go/delta" in partial.splitlines()


def test_format_select_env():
    tiers = {
        "heavy": "opencode-go/zeta-pro",
        "balanced": "opencode-go/beta-flash",
        "light": "opencode-go/eta-flash",
        "free": "opencode-go/theta-flash",
    }
    env_str = format_select_env(tiers)
    assert 'OPENCODE_MODEL_HEAVY="opencode-go/zeta-pro"' in env_str
    assert 'OPENCODE_MODEL_BALANCED="opencode-go/beta-flash"' in env_str
    assert 'OPENCODE_MODEL_LIGHT="opencode-go/eta-flash"' in env_str
    assert 'OPENCODE_MODEL_FREE="opencode-go/theta-flash"' in env_str

    # Display names are exposed as <TIER>_NAME variables alongside the IDs
    named_env = format_select_env(tiers, names={"heavy": "Zeta Pro", "free": "Theta Flash"})
    assert 'OPENCODE_MODEL_HEAVY_NAME="Zeta Pro"' in named_env
    assert 'OPENCODE_MODEL_FREE_NAME="Theta Flash"' in named_env
    assert 'OPENCODE_MODEL_BALANCED_NAME=""' in named_env

    # Missing tier defaults to empty string
    partial = {"heavy": "opencode-go/zeta"}
    partial_env = format_select_env(partial)
    assert 'OPENCODE_MODEL_HEAVY="opencode-go/zeta"' in partial_env
    assert 'OPENCODE_MODEL_BALANCED=""' in partial_env
    assert 'OPENCODE_MODEL_LIGHT=""' in partial_env
    assert 'OPENCODE_MODEL_FREE=""' in partial_env


def test_format_select_github_output():
    tiers = {
        "heavy": "opencode-go/zeta-pro",
        "balanced": "opencode-go/beta-flash",
        "light": "opencode-go/eta-flash",
        "free": "opencode-go/theta-flash",
    }
    gh_str = format_select_github_output(tiers)
    assert "heavy=opencode-go/zeta-pro" in gh_str
    assert "balanced=opencode-go/beta-flash" in gh_str
    assert "light=opencode-go/eta-flash" in gh_str
    assert "free=opencode-go/theta-flash" in gh_str

    # Display names are exposed as <tier>_name outputs alongside the IDs
    named_gh = format_select_github_output(tiers, names={"heavy": "Zeta Pro", "free": "Theta"})
    assert "heavy_name=Zeta Pro" in named_gh
    assert "free_name=Theta" in named_gh
    assert "balanced_name=" in named_gh

    # Missing tier defaults to empty string
    partial = {"heavy": "opencode-go/zeta"}
    partial_gh = format_select_github_output(partial)
    assert "heavy=opencode-go/zeta" in partial_gh
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

    # Env format with fallbacks and display names
    names = {t: f"{t.title()} Model" for t in tiers}
    res_env = format_select_env(tiers, fallbacks=fallbacks, names=names)
    assert 'OPENCODE_MODEL_HEAVY="opencode-go/mod-h1"' in res_env
    assert 'OPENCODE_MODEL_HEAVY_FALLBACKS="opencode-go/mod-h1,opencode-go/mod-h2"' in res_env
    assert 'OPENCODE_MODEL_FREE_FALLBACKS="opencode-go/mod-f1,opencode-go/mod-f2"' in res_env
    assert 'OPENCODE_MODEL_HEAVY_NAME="Heavy Model"' in res_env

    # GitHub Actions output with fallbacks and display names
    res_gh = format_select_github_output(tiers, fallbacks=fallbacks, names=names)
    assert "heavy=opencode-go/mod-h1" in res_gh
    assert "heavy_fallbacks=opencode-go/mod-h1,opencode-go/mod-h2" in res_gh
    assert "free_fallbacks=opencode-go/mod-f1,opencode-go/mod-f2" in res_gh
    assert "heavy_name=Heavy Model" in res_gh


def test_format_select_table_with_missing_and_varied_models():
    m1 = NormalizedModel(
        id="model-inf",
        provider_id="opencode-go",
        name="Model Inf",
        monthly_allowance=float("inf"),
        is_zero_cost=True,
        limit=ModelLimit(context=1000000),
    )
    m2 = NormalizedModel(
        id="model-dollar",
        provider_id="opencode-go",
        name="Model Dollar",
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
    assert "Model Name" in table
    assert "Model Dollar" in table
    assert "Model Inf" in table
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
        name="Mod One",
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
    assert expl["heavy"]["name"] == "Mod One"
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
    assert expl["light"]["name"] == ""

    # Test format_select_table with explain=True
    tbl_explain = format_select_table(
        tiers, models, plan="go", explain=True, criteria=SelectionCriteria(profile="coding")
    )
    assert "Model Name" in tbl_explain
    assert "Mod One" in tbl_explain
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
    assert _format_diff_value([]) == "none"
    mult_list = [{"factor": 2.0, "when": {"date": {"gte": "2026-12-01"}}}]
    assert _format_diff_value(mult_list) == 'x2 when {"date": {"gte": "2026-12-01"}}'


def test_format_diff_table_infinite_allowance():
    diff = SnapshotDiff(
        plan="go",
        pricing_changes={"mod": {"monthly_allowance": (float("inf"), 15.0)}},
        tier_changes={"free": {"old": "", "new": "opencode-go/mod"}},
    )
    table = format_diff_table(diff)
    assert "None" in table
    assert "unlimited" in table


def test_format_rank_table_requests_and_privacy_columns():
    from opencode_go_model_optimizer.ingest.models import Privacy, RequestEstimate

    m1 = NormalizedModel(id="m1", rank=1, privacy=Privacy(zdr=True))
    m1.request_estimate = RequestEstimate(per_5h=100.0, per_week=500.0, per_month=2000.0)
    m2 = NormalizedModel(id="m2", rank=2, privacy=None)
    table = format_rank_table([m1, m2])
    assert "Req/month" in table
    assert "Privacy" in table
    assert "2,000" in table
    assert "ZDR" in table
    assert "-" in table


def test_explain_tiers_next_change_none():
    from datetime import UTC, datetime

    from opencode_go_model_optimizer.ingest.models import PriceMultiplier

    past_mult = PriceMultiplier(factor=1.5, when={"date": {"lt": "2020-01-01"}})
    m = NormalizedModel(
        id="m1",
        tier="heavy",
        multipliers=[past_mult],
    )
    m.price_multiplier = 1.0
    criteria = SelectionCriteria(at=datetime(2026, 11, 10, tzinfo=UTC))
    expl = explain_tiers({"heavy": "m1"}, [m], criteria=criteria)
    assert "next_price_change" not in expl["heavy"]


def test_formatters_output_reexports_and_package_all():
    expected_reexports = {
        "_format_diff_value": _format_diff_value,
        "_render_table": _render_table,
        "render_table": render_table,
        "settings_payload": settings_payload,
        "explain_tiers": explain_tiers,
        "tier_display_names": tier_display_names,
        "format_select_json": format_select_json,
        "format_select_compact": format_select_compact,
        "format_select_env": format_select_env,
        "format_select_github_output": format_select_github_output,
        "format_select_table": format_select_table,
        "format_rank_json": format_rank_json,
        "format_rank_table": format_rank_table,
        "format_diff_json": format_diff_json,
        "format_diff_table": format_diff_table,
    }
    for name, symbol in expected_reexports.items():
        assert getattr(compat_output, name) is symbol

    for public_name in (
        "explain_tiers",
        "format_diff_json",
        "format_diff_table",
        "format_rank_json",
        "format_rank_table",
        "format_select_compact",
        "format_select_env",
        "format_select_github_output",
        "format_select_json",
        "format_select_table",
        "render_table",
        "settings_payload",
        "tier_display_names",
    ):
        assert public_name in formatters.__all__
        assert getattr(formatters, public_name) is expected_reexports[public_name]

    assert render_table is _render_table
    assert compat_output.format_select_compact({"heavy": "opencode-go/a"}) == "heavy opencode-go/a"


def test_append_column_and_render_table():
    headers = ["A", "B"]
    rows = [["1", "2"], ["3", "4"]]
    _append_column(headers, rows, "C", ["5", "6"])
    assert headers == ["A", "B", "C"]
    assert rows == [["1", "2", "5"], ["3", "4", "6"]]
    rendered = render_table(headers, rows)
    assert "| A | B | C |" in rendered
    assert "| 3 | 4 | 6 |" in rendered

    with pytest.raises(ValueError):
        _append_column(headers, rows, "D", ["only-one"])


def test_format_select_table_explain_multiplier_and_missing_padding():
    future_mult = PriceMultiplier(factor=2.0, when={"date": {"gte": "2026-12-01"}})
    m1 = NormalizedModel(
        id="mod-mult",
        provider_id="opencode-go",
        name="Mod Mult",
        monthly_allowance=60.0,
        cost=TokenCost(input=1.0, output=2.0),
        limit=ModelLimit(context=128000),
        aa_iq=80.0,
        bpe=1.2,
        composite_burn_pct=12.5,
        multipliers=[future_mult],
    )
    m1.price_multiplier = 1.0
    m2 = NormalizedModel(
        id="mod-plain",
        provider_id="opencode-go",
        name="Mod Plain",
        monthly_allowance=30.0,
        cost=TokenCost(input=0.5, output=1.0),
    )
    tiers = {
        "heavy": "opencode-go/mod-mult",
        "balanced": "opencode-go/mod-plain",
        "light": "opencode-go/missing",
        "free": "",
    }
    criteria = SelectionCriteria(at=datetime(2026, 11, 10, tzinfo=UTC))
    table = format_select_table(tiers, [m1, m2], plan="go", explain=True, criteria=criteria)

    data_rows = [line for line in table.splitlines() if line.startswith("| ")]
    # Header + 4 tier rows, every row has 11 pipe-separated columns (12 pipes)
    assert len(data_rows) == 5
    for line in data_rows:
        assert line.count("|") == 12
    assert "Price x" in data_rows[0]
    assert "x1 (-> x2 @ 2026-12-01T00:00+00:00)" in data_rows[1]
    assert "| light" in data_rows[3] and "opencode-go/missing" in data_rows[3]
    assert "| free" in data_rows[4] and "| None " in data_rows[4]


def test_format_rank_table_optional_columns_and_empty():
    m1 = NormalizedModel(
        id="rank-1",
        rank=1,
        tier="heavy",
        bpe=1.1,
        aa_iq=85.0,
        composite_burn_pct=15.0,
        monthly_allowance=60.0,
        cost=TokenCost(output=2.0),
        release_date="2026-10-01",
        limit=ModelLimit(context=200000),
        privacy=Privacy(zdr=True, trains_on_data=False),
    )
    m1.request_estimate = RequestEstimate(per_5h=50.0, per_week=200.0, per_month=900.0)
    m1.price_multiplier = 1.5
    m2 = NormalizedModel(
        id="rank-2",
        rank=2,
        status="preview",
        privacy=Privacy(
            retention_days=30,
            trains_on_data=True,
            requires_global_region=True,
            limited_regions=True,
        ),
    )

    for mode in ("pareto", "allowance"):
        tbl = format_rank_table([m1, m2], mode=mode)
        assert "Req/month" in tbl
        assert "Privacy" in tbl
        assert "Price x" in tbl
        assert "900" in tbl
        assert "ZDR" in tbl
        assert "30d,train,global,limited" in tbl
        assert "x1.5" in tbl

    empty_tbl = format_rank_table([], mode="pareto")
    assert "Rank" in empty_tbl
    assert "BPE" in empty_tbl
    assert "Price x" not in empty_tbl


def test_settings_payload_and_diff_privacy_changes():
    criteria = SelectionCriteria(
        mode="pareto",
        top_n=2,
        profile="coding",
        skip_models={"b", "a"},
        preferred_tiers={"heavy": "m1"},
        token_mix=(1000, 500, 200),
        weights=(0.6, 0.3, 0.1),
        at=datetime(2026, 11, 10, 12, 0, tzinfo=UTC),
    )
    payload = settings_payload(criteria)
    assert payload["mode"] == "pareto"
    assert payload["skip"] == ["a", "b"]
    assert payload["prefer"] == {"heavy": "m1"}
    assert payload["tokens"] == [1000, 500, 200]
    assert payload["weights"] == [0.6, 0.3, 0.1]
    assert payload["at"] == "2026-11-10T12:00:00+00:00"

    diff = SnapshotDiff(
        plan="go",
        privacy_changes={"mod": {"zdr": (False, True)}},
        capability_changes={"mod": {"context": (128000, 256000)}},
    )
    tbl = format_diff_table(diff)
    assert "Capability / benchmark changes:" in tbl
    assert "Privacy / region changes:" in tbl
    assert "256000" in tbl


def test_markdown_shared_helpers():
    assert is_number(1)
    assert is_number(1.5)
    assert not is_number(True)
    assert not is_number("1")
    assert not is_number(None)

    assert fence_for("plain text") == "```"
    assert fence_for("contains ``` backticks") == "````"

    assert sanitize_mermaid_id("go-plus/model.v1", prefix="T_") == "T_go_plus_model_v1"

    assert render_mermaid_flowchart() == []
    flow = render_mermaid_flowchart(
        [("Root", 'Plan "go"')],
        [("Root", "T_heavy", 'heavy: "m1"'), ("T_heavy", "T_fb")],
    )
    assert flow == [
        "```mermaid",
        "flowchart LR",
        "    Root[\"Plan 'go'\"]",
        "    Root --> T_heavy[\"heavy: 'm1'\"]",
        "    T_heavy --> T_fb",
        "```",
    ]

    assert render_details_block("Summary", "   \n  ") == []
    details = render_details_block(
        "<code>cmd</code> output",
        "line 1\n```nested```\nline 2\n",
        open_by_default=True,
    )
    assert details == [
        "<details open>",
        "<summary><code>cmd</code> output</summary>",
        "",
        "````text",
        "line 1\n```nested```\nline 2",
        "````",
        "",
        "</details>",
    ]

    assert format_multiplier(None) == "-"
    assert format_multiplier(1.5) == "x1.5"
    assert (
        format_multiplier(1.0, {"at": "2026-12-01T00:00+00:00", "multiplier": 2.0})
        == "x1 (-> x2 @ 2026-12-01T00:00+00:00)"
    )
