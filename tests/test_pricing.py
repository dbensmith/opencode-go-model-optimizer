"""Tests for time-aware pricing from catalogue multipliers (offline)."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opencode_go_model_optimizer.cli import main
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.diff import diff_snapshots
from opencode_go_model_optimizer.engine.pricing import (
    apply_price_basis,
    blended_multiplier,
    multiplier_at,
    next_price_change,
    validate_multiplier,
    worst_multiplier,
)
from opencode_go_model_optimizer.engine.tiers import select_tiers
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_http
from opencode_go_model_optimizer.ingest.models import (
    ModelLimit,
    NormalizedModel,
    PriceMultiplier,
    Snapshot,
    TokenCost,
)

FIX = Path(__file__).parent / "fixtures"
PEAK = {
    "utc_days": {"in": ["mon", "tue", "wed", "thu", "fri"]},
    "any": [
        {"all": [{"utc_hours": {"gte": "01:00"}}, {"utc_hours": {"lt": "04:00"}}]},
        {"all": [{"utc_hours": {"gte": "06:00"}}, {"utc_hours": {"lt": "10:00"}}]},
    ],
}
PROMO = {"date": {"gte": "2026-12-01"}}


def dt(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=UTC)


def model(mid="m", out=10.0, whens=(), **kw) -> NormalizedModel:
    return NormalizedModel(
        id=mid,
        cost=TokenCost(input=out / 4, output=out, cache_read=out / 40),
        limit=ModelLimit(context=200000, output=8000),
        monthly_allowance=15.0,
        multipliers=[PriceMultiplier(factor=f, when=w) for f, w in whens],
        **kw,
    )


PEAKED = model(whens=[(2, PEAK)])
PROMOED = model(whens=[(2, PROMO)])


def test_peak_hour_boundaries():
    # 2026-11-09 is a Monday
    assert multiplier_at(PEAKED, dt("2026-11-09T00:59:59")) == 1
    assert multiplier_at(PEAKED, dt("2026-11-09T01:00")) == 2
    assert multiplier_at(PEAKED, dt("2026-11-09T03:59:59")) == 2
    assert multiplier_at(PEAKED, dt("2026-11-09T04:00")) == 1
    assert multiplier_at(PEAKED, dt("2026-11-09T06:00")) == 2
    assert multiplier_at(PEAKED, dt("2026-11-09T10:00")) == 1


def test_weekend_is_never_peak():
    assert multiplier_at(PEAKED, dt("2026-11-14T02:00")) == 1  # Saturday
    assert multiplier_at(PEAKED, dt("2026-11-15T07:00")) == 1  # Sunday


def test_naive_and_offset_datetimes_are_utc_normalised():
    assert multiplier_at(PEAKED, datetime(2026, 11, 9, 2, 0)) == 2
    assert multiplier_at(PEAKED, datetime.fromisoformat("2026-11-09T12:00:00+10:00")) == 2


def test_date_promo_expiry():
    assert multiplier_at(PROMOED, dt("2026-11-30T23:59")) == 1
    assert multiplier_at(PROMOED, dt("2026-12-01T00:00")) == 2


def test_exclusive_and_inclusive_operators():
    m = model(
        whens=[
            (3, {"utc_hours": {"gt": "04:00", "lte": "05:00"}}),
            (5, {"date": {"lt": "2026-01-02", "gt": "2025-12-31"}}),
        ]
    )
    assert multiplier_at(m, dt("2026-06-01T04:00")) == 1
    assert multiplier_at(m, dt("2026-06-01T04:01")) == 3
    assert multiplier_at(m, dt("2026-06-01T05:00")) == 3
    assert multiplier_at(m, dt("2026-06-01T05:01")) == 1
    assert multiplier_at(m, dt("2026-01-01T12:00")) == 5 * 1


def test_multipliers_multiply():
    m = model(whens=[(2, PEAK), (1.5, PROMO)])
    assert multiplier_at(m, dt("2026-12-01T02:00")) == 3.0  # Tuesday in promo, peak hour


def test_unknown_key_is_ignored_and_reported():
    bad = PriceMultiplier(factor=9, when={"region": {"in": ["eu"]}})
    assert "region" in (validate_multiplier(bad) or "")
    m = model(whens=[(2, PEAK)])
    m.multipliers.append(bad)
    assert multiplier_at(m, dt("2026-11-09T02:00")) == 2
    # unknown key nested after a failing sibling is still detected (no short-circuit)
    nested = PriceMultiplier(factor=2, when={"all": [{"date": {"gte": "2999-01-01"}}, {"x": 1}]})
    assert validate_multiplier(nested) is not None
    assert validate_multiplier(PriceMultiplier(factor=2, when={"utc_hours": {"gte": "25:00"}}))
    assert validate_multiplier(PriceMultiplier(factor=2, when={"date": {"between": "x"}}))
    assert validate_multiplier(PriceMultiplier(factor=0, when=PROMO))
    assert validate_multiplier(PriceMultiplier(factor=2, when=PEAK)) is None


def test_blended_full_week_for_peak_model():
    expected = 1 + 35 / 168
    for start in ("2026-11-09T00:00", "2026-11-12T13:37"):  # aligned and unaligned
        assert blended_multiplier(PEAKED, dt(start)) == pytest.approx(expected)


def test_blended_includes_promo_expiry():
    # 2026-11-28T00:00 + 7 days: 3 days at 1x, 4 days at 2x
    assert blended_multiplier(PROMOED, dt("2026-11-28T00:00")) == pytest.approx((3 * 1 + 4 * 2) / 7)
    assert blended_multiplier(PROMOED, dt("2026-10-01T00:00")) == pytest.approx(1.0)
    assert blended_multiplier(PROMOED, dt("2027-01-01T00:00")) == pytest.approx(2.0)


def test_half_hour_boundaries_use_finer_grid():
    m = model(whens=[(2, {"utc_hours": {"gte": "01:30"}}), (1, {})])
    m.multipliers[0].when = {
        "all": [{"utc_hours": {"gte": "01:30"}}, {"utc_hours": {"lt": "02:00"}}]
    }
    m.multipliers.pop()
    assert blended_multiplier(m, dt("2026-11-09T00:00")) == pytest.approx(1 + 0.5 / 24)


def test_worst_multiplier():
    assert worst_multiplier(PEAKED, dt("2026-11-14T05:00")) == 2  # peak reachable Monday
    assert worst_multiplier(PROMOED, dt("2026-11-20T00:00")) == 1
    assert worst_multiplier(PROMOED, dt("2026-11-25T00:00")) == 2


def test_next_price_change():
    assert next_price_change(PROMOED, dt("2026-11-10T08:00")) == (dt("2026-12-01T00:00"), 2.0)
    assert next_price_change(PROMOED, dt("2027-02-01T00:00")) is None
    assert next_price_change(PEAKED, dt("2026-11-09T00:30")) == (dt("2026-11-09T01:00"), 2.0)
    assert next_price_change(PEAKED, dt("2026-11-09T02:00")) == (dt("2026-11-09T04:00"), 1.0)
    assert next_price_change(model(), dt("2026-11-09T02:00")) is None


def test_apply_price_basis_scales_all_costs_and_leaves_plain_models_alone():
    plain = model("plain")
    out = apply_price_basis([PEAKED, plain], "now", dt("2026-11-09T02:00"))
    assert out[0].cost.output == 20.0 and out[0].cost.input == 5.0
    assert out[0].cost.cache_read == pytest.approx(0.5)
    assert out[0].price_multiplier == 2.0
    assert PEAKED.cost.output == 10.0  # original untouched
    assert out[1] is plain
    assert apply_price_basis([PEAKED], "list", dt("2026-11-09T02:00"))[0] is PEAKED
    with pytest.raises(ValueError, match="price basis"):
        apply_price_basis([PEAKED], "bogus")


def test_parse_multipliers_from_fixture():
    models = {
        m.id: m for m in parse_catalogue_http(json.loads((FIX / "catalogue_http.json").read_text()))
    }
    for mid in ("beta-flash", "beta-pro"):
        (mult,) = models[mid].multipliers
        assert mult.factor == 2 and mult.when == PEAK
        assert blended_multiplier(models[mid], dt("2026-11-10T00:00")) == pytest.approx(
            1 + 35 / 168
        )
    (promo,) = models["theta-pro"].multipliers
    assert promo.factor == 2 and promo.when == PROMO
    assert models["gamma-flash"].multipliers == []
    assert all(validate_multiplier(x) is None for m in models.values() for x in m.multipliers)


def test_old_snapshot_without_multipliers_loads():
    snap = Snapshot.model_validate({"fetched_at": "x", "models": [{"id": "a"}]})
    assert snap.models[0].multipliers == []


# --- selection ---------------------------------------------------------------------------


def _pool():
    """'cheap' beats 'steady' in allowance mode only while its 3x peak multiplier is off."""
    cheap = model("cheap", out=2.0, whens=[(10, PEAK)])
    steady = model("steady", out=6.0)
    pricey = model("pricey", out=40.0)
    return [cheap, steady, pricey]


def test_select_flips_between_peak_and_off_peak():
    models = _pool()
    off = select_tiers(
        models, SelectionCriteria(price_basis="now", at=dt("2026-11-14T02:00"), max_output_cost=7)
    )
    peak = select_tiers(
        models, SelectionCriteria(price_basis="now", at=dt("2026-11-09T02:00"), max_output_cost=7)
    )
    all_off = {m.id for m in off.models}
    all_peak = {m.id for m in peak.models}
    assert all_off == {"cheap", "steady"}  # 2.0 <= 7
    assert all_peak == {"steady"}  # cheap now costs 20 > 7
    assert "opencode-go/cheap" in off.primary.values()
    assert "opencode-go/cheap" not in peak.primary.values()


def test_list_basis_ignores_multipliers_and_blended_sits_between():
    models = _pool()
    peak_at = dt("2026-11-09T02:00")
    listed = select_tiers(models, SelectionCriteria(price_basis="list", at=peak_at)).models
    assert next(m for m in listed if m.id == "cheap").cost.output == 2.0
    blended = select_tiers(models, SelectionCriteria(price_basis="blended", at=peak_at)).models
    exp = 2.0 * (1 + 9 * 35 / 168)
    assert next(m for m in blended if m.id == "cheap").cost.output == pytest.approx(exp)
    worst = select_tiers(models, SelectionCriteria(price_basis="worst", at=peak_at)).models
    assert next(m for m in worst if m.id == "cheap").cost.output == pytest.approx(20.0)


# --- diff --------------------------------------------------------------------------------


def test_diff_reports_added_and_removed_promo():
    old = Snapshot(fetched_at="a", models=[model("x")])
    new = Snapshot(fetched_at="b", models=[model("x", whens=[(2, PROMO)])])
    d = diff_snapshots(old, new, mode="allowance")
    assert set(d.pricing_changes["x"]) == {"multipliers"}
    before, after = d.pricing_changes["x"]["multipliers"]
    assert before == [] and after[0]["factor"] == 2
    assert d.has_changes()
    assert "multipliers" in diff_snapshots(new, old, mode="allowance").pricing_changes["x"]
    assert not diff_snapshots(new, new, mode="allowance").has_changes()


# --- CLI ---------------------------------------------------------------------------------


def _snapshot_file(tmp_path):
    snap = Snapshot(fetched_at="t", models=_pool())
    p = tmp_path / "s.json"
    p.write_text(snap.model_dump_json())
    return str(p)


def _run(capsys, *argv):
    assert main(list(argv)) == 0
    return capsys.readouterr().out


def test_cli_explain_json_reports_multiplier_and_next_change(tmp_path, capsys):
    s = _snapshot_file(tmp_path)
    out = json.loads(
        _run(
            capsys, "select", "--snapshot", s, "--mode", "allowance", "--explain",
            "--price-basis", "now", "--at", "2026-11-09T02:00",
        )
    )  # fmt: skip
    ex = out["explanations"]
    cheap = [e for e in ex.values() if e["model_id"].endswith("/cheap")]
    steady = [e for e in ex.values() if e["model_id"].endswith("/steady")]
    assert not cheap or cheap[0]["price_multiplier"] == 10
    assert steady and "price_multiplier" not in steady[0]
    out = json.loads(
        _run(
            capsys, "select", "--snapshot", s, "--mode", "allowance", "--explain",
            "--price-basis", "now", "--at", "2026-11-14T02:00",
        )
    )  # fmt: skip
    (cheap,) = [e for e in out["explanations"].values() if e["model_id"].endswith("/cheap")]
    assert cheap["price_multiplier"] == 1
    assert cheap["next_price_change"] == {"at": "2026-11-16T01:00+00:00", "multiplier": 10.0}


def test_cli_table_explain_and_rank_show_multiplier(tmp_path, capsys):
    s = _snapshot_file(tmp_path)
    args = (
        "--snapshot",
        s,
        "--mode",
        "allowance",
        "--price-basis",
        "now",
        "--at",
        "2026-11-09T02:00",
    )
    table = _run(capsys, "select", *args, "--format", "table", "--explain")
    assert "Price x" in table and "x10" in table
    rank = _run(capsys, "rank", *args)
    assert "Price x" in rank
    ranked = json.loads(_run(capsys, "rank", *args, "--format", "json"))
    by_id = {m["id"]: m for m in ranked}
    assert by_id["cheap"]["price_multiplier"] == 10
    assert by_id["cheap"]["cost"]["output"] == 20.0
    assert "price_multiplier" not in by_id["steady"]


def test_cli_list_basis_reproduces_unadjusted_output(tmp_path, capsys):
    s = _snapshot_file(tmp_path)
    plain = Snapshot(
        fetched_at="t", models=[m.model_copy(update={"multipliers": []}) for m in _pool()]
    )
    p = tmp_path / "plain.json"
    p.write_text(plain.model_dump_json())
    common = ("--mode", "allowance", "--at", "2026-11-09T02:00")

    def sel(path, *extra):
        out = json.loads(_run(capsys, "select", "--snapshot", path, "--explain", *common, *extra))
        out.pop("evaluated_at")
        out.pop("snapshot")
        return out

    assert sel(s, "--price-basis", "list") == sel(str(p))
    assert sel(s, "--price-basis", "now") != sel(str(p))

    def rank(path, *extra):
        data = json.loads(
            _run(capsys, "rank", "--snapshot", path, "--format", "json", *common, *extra)
        )
        for m in data:
            m.pop("multipliers", None)
        return data

    assert rank(s, "--price-basis", "list") == rank(str(p))


def test_cli_bad_at_is_usage_error(tmp_path):
    with pytest.raises(SystemExit):
        main(["select", "--snapshot", _snapshot_file(tmp_path), "--at", "yesterday-ish"])


def test_pricing_cache_and_eval_helpers():
    from opencode_go_model_optimizer.engine.pricing import (
        _eval_multipliers,
        _parse_date_str,
        _parse_hhmm_str,
        _segments,
        _valid_multipliers,
    )

    # 1. Caching
    _parse_hhmm_str.cache_clear()
    _parse_date_str.cache_clear()

    sec1 = _parse_hhmm_str("01:30")
    sec2 = _parse_hhmm_str("01:30")
    assert sec1 == 5400
    assert sec2 == 5400
    assert _parse_hhmm_str.cache_info().hits >= 1

    d1 = _parse_date_str("2026-11-10")
    d2 = _parse_date_str("2026-11-10")
    assert d1 == d2
    assert _parse_date_str.cache_info().hits >= 1

    # 2. _valid_multipliers and _eval_multipliers
    valid_mult = PriceMultiplier(factor=2.0, when={"utc_hours": {"gte": "01:00"}})
    invalid_mult = PriceMultiplier(factor=-1.0, when={})
    m = NormalizedModel(id="test", multipliers=[valid_mult, invalid_mult])
    vm = _valid_multipliers(m)
    assert len(vm) == 1
    assert vm[0] is valid_mult

    when_match = datetime(2026, 11, 10, 2, 0, tzinfo=UTC)
    when_no_match = datetime(2026, 11, 10, 0, 30, tzinfo=UTC)
    assert _eval_multipliers(vm, when_match) == 2.0
    assert _eval_multipliers(vm, when_no_match) == 1.0

    # 3. Model without valid multipliers yields single segment in O(1)
    m_no_mult = NormalizedModel(id="plain")
    start = datetime(2026, 11, 10, 0, 0, tzinfo=UTC)
    end = datetime(2026, 11, 17, 0, 0, tzinfo=UTC)
    segs = list(_segments(m_no_mult, start, end))
    assert len(segs) == 1
    assert segs[0] == (start, end, 1.0)
