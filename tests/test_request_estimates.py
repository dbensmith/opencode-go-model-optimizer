"""Offline tests for docs request estimates and the burn basis."""

import json
from pathlib import Path

import pytest

from opencode_go_model_optimizer.cli import main
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.diff import diff_snapshots
from opencode_go_model_optimizer.engine.pareto import (
    compute_burn_pct,
    compute_quota_burn_pct,
    resolve_burn_basis,
)
from opencode_go_model_optimizer.engine.tiers import select_tiers
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_http
from opencode_go_model_optimizer.ingest.docs import (
    parse_endpoints_table,
    parse_request_estimates,
)
from opencode_go_model_optimizer.ingest.models import (
    NormalizedModel,
    RequestEstimate,
    Snapshot,
    TokenCost,
)
from opencode_go_model_optimizer.ingest.snapshot import (
    apply_plan,
    create_snapshot,
    estimate_for,
    merge_request_estimates,
    save_snapshot,
)

FIX = Path(__file__).parent / "fixtures"
MDX = (FIX / "go_docs.mdx").read_text(encoding="utf-8")
OC = json.loads((FIX / "catalogue_http.json").read_text(encoding="utf-8"))


def _m(mid="m", per_month=None, out=10.0, allowance=15.0, plan="go", **kw):
    m = NormalizedModel(
        id=mid,
        name=mid,
        cost=TokenCost(input=1.0, output=out, cache_read=0.1),
        monthly_allowance=allowance,
        **kw,
    )
    if per_month is not None:
        m.request_estimates[plan] = RequestEstimate(
            per_5h=per_month / 5, per_week=per_month / 2, per_month=per_month
        )
    return apply_plan([m], plan)[0]


def test_parse_fixture():
    est = parse_request_estimates(MDX)
    assert set(est) == {"go", "go-plus", "go-teams"}
    assert est["go"]["Gamma-Flash"] == {
        "per_5h": 1372.0,
        "per_week": 3430.0,
        "per_month": 6861.0,
    }
    assert est["go"]["Epsilon Free"]["per_month"] == float("inf")
    assert est["go-plus"]["Gamma-Flash"]["per_month"] == 24671.0
    assert est["go-plus"]["Epsilon Free"]["per_5h"] == float("inf")
    assert parse_request_estimates("nothing") == {}


def test_merge_into_catalogue_and_warning():
    models = parse_catalogue_http(OC)
    warnings: list[str] = []
    merge_request_estimates(
        models, parse_request_estimates(MDX), parse_endpoints_table(MDX), warnings
    )
    by_id = {m.id: m for m in models}
    assert by_id["gamma-flash"].request_estimates["go"].per_month == 6861.0
    assert by_id["gamma-flash"].request_estimates["go-teams"].per_month > 0
    assert not any("no docs request estimate" in w for w in warnings)
    assert estimate_for(by_id["gamma-flash"], "plus").per_month == 24671.0
    apply_plan(models, "go-plus")
    assert by_id["gamma-flash"].request_estimate.per_month == 24671.0

    orphan = NormalizedModel(id="x", name="X", cost=TokenCost(input=1, output=1))
    warnings = []
    merge_request_estimates([orphan], parse_request_estimates(MDX), {}, warnings)
    assert any("no docs request estimate" in w and "x" in w for w in warnings)


def test_create_snapshot_roundtrip_serializes_unlimited(tmp_path):
    models = parse_catalogue_http(OC)
    snap = create_snapshot(
        models,
        {},
        endpoints=parse_endpoints_table(MDX),
        request_estimates=parse_request_estimates(MDX),
    )
    path = tmp_path / "s.json"
    save_snapshot(snap, path)
    raw = path.read_text()
    assert '"unlimited"' in raw
    loaded = Snapshot.model_validate_json(raw)
    epsilon = next(m for m in loaded.models if m.request_estimates)
    assert any(
        e.per_month == float("inf") for m in loaded.models for e in m.request_estimates.values()
    )
    assert epsilon


def test_old_snapshot_without_estimates_loads():
    snap = Snapshot.model_validate_json('{"fetched_at": "t", "models": [{"id": "a"}]}')
    assert snap.models[0].request_estimates == {}
    assert apply_plan(snap.models, "go")[0].request_estimate is None


def test_burn_estimate_vs_tokens():
    m = _m(per_month=10_000)
    assert compute_burn_pct(m, basis="estimate") == pytest.approx(10.0)
    assert compute_burn_pct(m, basis="tokens") == pytest.approx(compute_quota_burn_pct(m))
    assert compute_burn_pct(m, basis="estimate") != compute_burn_pct(m, basis="tokens")


def test_unlimited_estimate_is_zero_burn():
    assert compute_burn_pct(_m(per_month=float("inf")), basis="estimate") == 0.0


def test_missing_estimate_falls_back_to_tokens():
    m = _m()
    assert compute_burn_pct(m, basis="estimate") == compute_quota_burn_pct(m)


def test_active_plan_estimate_used():
    m = _m(per_month=10_000, plan="go-plus")
    m.request_estimates["go"] = RequestEstimate(per_month=1_000)
    apply_plan([m], "go-plus")
    assert compute_burn_pct(m, basis="estimate") == pytest.approx(10.0)
    apply_plan([m], "go")
    assert compute_burn_pct(m, basis="estimate") == pytest.approx(100.0)


def test_auto_rule():
    assert resolve_burn_basis("auto") == "estimate"
    assert resolve_burn_basis("auto", token_mix=(1, 2, 3)) == "tokens"
    assert resolve_burn_basis("auto", profile="coding") == "tokens"
    assert resolve_burn_basis("tokens") == "tokens"
    assert resolve_burn_basis("estimate", profile="coding") == "estimate"
    with pytest.raises(ValueError):
        resolve_burn_basis("bogus")
    m = _m(per_month=10_000)
    assert compute_burn_pct(m) == pytest.approx(10.0)
    assert compute_burn_pct(m, token_mix=(750, 60000, 250)) == compute_quota_burn_pct(m)
    assert compute_burn_pct(m, profile="coding") == compute_quota_burn_pct(m, profile="coding")


def test_price_multiplier_scales_estimate_burn():
    m = _m(per_month=10_000)
    m.price_multiplier = 3.0
    assert compute_burn_pct(m, basis="estimate") == pytest.approx(30.0)


def test_multiplier_still_matters_through_selection():
    from opencode_go_model_optimizer.ingest.models import PriceMultiplier

    peaky = _m("peaky", per_month=10_000, multipliers=[PriceMultiplier(factor=3.0)])
    flat = _m("flat", per_month=10_000)
    c = SelectionCriteria(price_basis="worst", max_burn=15.0, burn_basis="estimate")
    ids = {m.id for m in select_tiers([peaky, flat], c).models}
    assert ids == {"flat"}
    c = SelectionCriteria(price_basis="list", max_burn=15.0, burn_basis="estimate")
    assert {m.id for m in select_tiers([peaky, flat], c).models} == {"peaky", "flat"}


def test_max_burn_filter_uses_basis():
    a = _m("a", per_month=1_000)  # 100% by estimate
    sel = lambda basis: {  # noqa: E731
        m.id for m in select_tiers([a], SelectionCriteria(max_burn=80, burn_basis=basis)).models
    }
    assert sel("estimate") == set()
    assert sel("tokens") == {"a"}


def test_diff_reports_estimate_change():
    def snap(per_month):
        return Snapshot(fetched_at="t", models=[_m("a", per_month=per_month)])

    d = diff_snapshots(snap(1000), snap(2000))
    assert d.pricing_changes["a"]["requests_per_month"] == (1000.0, 2000.0)
    assert not diff_snapshots(snap(1000), snap(1000)).has_changes()


def _snap_file(tmp_path):
    models = [
        _m("heavy", per_month=500, out=40.0, allowance=15.0),
        _m("light", per_month=float("inf"), out=0.5, allowance=15.0),
    ]
    p = tmp_path / "s.json"
    save_snapshot(Snapshot(fetched_at="t", models=models), p)
    return p


def test_cli_explain_and_rank(tmp_path, capsys):
    p = _snap_file(tmp_path)
    argv = ["select", "--snapshot", str(p), "--mode", "allowance", "--format", "json", "--explain"]
    assert main(argv) == 0
    out = json.loads(capsys.readouterr().out)
    first = next(v for v in out["explanations"].values() if v.get("model_id", "").endswith("heavy"))
    assert first["burn_basis_used"] == "estimate"
    assert first["requests_per_month"] == 500
    assert first["quota_burn_pct"] == 200.0
    assert main([*argv, "--burn-basis", "tokens"]) == 0
    out = json.loads(capsys.readouterr().out)
    first = next(v for v in out["explanations"].values() if v.get("model_id", "").endswith("heavy"))
    assert first["burn_basis_used"] == "tokens"
    assert main([*argv, "--tokens", "1,1,1"]) == 0
    out = json.loads(capsys.readouterr().out)
    first = next(v for v in out["explanations"].values() if v.get("model_id", "").endswith("heavy"))
    assert first["burn_basis_used"] == "tokens"

    assert main(["rank", "--snapshot", str(p), "--format", "table"]) == 0
    assert "Req/month" in capsys.readouterr().out
    assert main(["rank", "--snapshot", str(p), "--format", "json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert {d["id"]: d["requests_per_month"] for d in data} == {"heavy": 500, "light": "unlimited"}
    with pytest.raises(SystemExit):
        main(["rank", "--snapshot", str(p), "--burn-basis", "nope"])
