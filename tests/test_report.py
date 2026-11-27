"""Tests for the published results report."""

import json
import math
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from opencode_go_model_optimizer.cli import main
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_http
from opencode_go_model_optimizer.ingest.docs import parse_privacy
from opencode_go_model_optimizer.ingest.models import NormalizedModel, Privacy, TokenCost
from opencode_go_model_optimizer.ingest.snapshot import create_snapshot, load_cached_snapshot
from opencode_go_model_optimizer.report import (
    _bucket_exclusion_reason,
    _format_allowance,
    _format_cluster_range,
    _round_chart_num,
    _strip_aa,
    assert_no_aa,
    build_report,
    exclusion_reasons,
    render_markdown,
    summarize_junit,
    write_report,
)
from tests.conftest import CATALOGUE_HTTP_DATA as OC
from tests.conftest import MDX_TEXT as MDX

NOW = datetime(2026, 11, 10, 12, 0, tzinfo=UTC)
SOURCES = {
    "docs": {
        "url": "https://example.invalid/docs",
        "status": 200,
        "fetched_at": "2026-11-10T12:00:00+00:00",
        "sha256": "ab" * 32,
        "bytes": 1234,
    }
}


@pytest.fixture
def snapshot():
    return create_snapshot(
        parse_catalogue_http(OC),
        privacy=parse_privacy(MDX),
        sources=SOURCES,
    )


def _junit(tmp_path, name, cases):
    body = "".join(cases)
    path = tmp_path / f"{name}.xml"
    path.write_text(f'<testsuites><testsuite name="x">{body}</testsuite></testsuites>')
    return str(path)


def test_report_structure_and_no_benchmark_keys(snapshot, monkeypatch):
    monkeypatch.setenv("GITHUB_SHA", "abc123")
    report = build_report(snapshot=snapshot, now=NOW)
    assert report["schema_version"] == 1
    assert report["generated_at"] == "2026-11-10T12:00:00+00:00"
    assert report["git_sha"] == "abc123"
    assert set(report["plans"]) == {"go", "go-plus", "go-teams"}
    go = report["plans"]["go"]
    assert go["mode"] == "allowance"
    assert go["tiers"]["heavy"] == "opencode-go/alpha-max"
    assert go["tiers"]["free"] == "opencode-go/epsilon-free"
    assert len(go["explanations"]["light"]["candidates"]) == 3
    assert go["fallbacks"]["light"] == [
        c["model_id"] for c in go["explanations"]["light"]["candidates"][1:]
    ]
    heavy = go["explanations"]["heavy"]
    assert heavy["cluster"]["size"] >= 1 and heavy["why"]
    assert heavy["explanation"]["privacy"]["requires_global_region"] is False
    text = json.dumps(report)
    assert "aa_" not in text and "speed_tps" not in text and '"bpe"' not in text
    # provenance carries digests only
    assert report["provenance"]["sources"]["docs"]["sha256"] == "ab" * 32
    assert "Synthetic Go Pricing" not in text
    assert report["tests"] is None
    assert report["warnings"] == []
    md = render_markdown(report)
    assert "## Run settings" in md
    assert "Global regions**: blocked" in md
    assert "Paid training**: blocked" in md
    assert "Free training**: blocked" in md


def test_excluded_models_have_reasons(snapshot):
    excluded = {
        e["model_id"]: e["reasons"]
        for e in build_report(snapshot=snapshot, now=NOW)["plans"]["go"]["excluded"]
    }
    # Preview models are included; Global-region and training models use the Go defaults.
    assert "opencode-go/omega-preview" not in excluded
    assert "Global workspace region" in excluded["opencode-go/kappa-free"][0]
    assert "free model trains" in excluded["opencode-go/delta-contributor"][0]
    assert "paid model trains" in excluded["opencode-go/lambda-contributor"][0]
    assert "not active" in excluded["opencode-go/deprecated-old-model"][0]
    assert "opencode-go/alpha-max" not in excluded


def test_exclusion_reasons_unpriced_and_unlimited():
    c = SelectionCriteria()
    paid = TokenCost(output=1)
    assert "no monthly allowance" in exclusion_reasons(NormalizedModel(id="a", cost=paid), c)[0]
    zero = NormalizedModel(id="a", cost=paid, monthly_allowance=0.0)
    assert "zero" in exclusion_reasons(zero, c)[0]
    assert exclusion_reasons(NormalizedModel(id="free"), c) == []  # zero-cost: free pool
    unlimited = NormalizedModel(id="a", cost=TokenCost(output=1), monthly_allowance=math.inf)
    assert "unlimited" in exclusion_reasons(unlimited, c)[0]
    ok = NormalizedModel(id="a", cost=TokenCost(output=1), monthly_allowance=5.0)
    assert exclusion_reasons(ok, c) == []
    trains = NormalizedModel(
        id="t",
        privacy=Privacy(trains_on_data=True),
        cost=TokenCost(output=1),
        monthly_allowance=5.0,
    )
    assert "paid model trains" in exclusion_reasons(trains, SelectionCriteria())[0]
    assert exclusion_reasons(trains, SelectionCriteria(allow_paid_training=True)) == []
    consent = NormalizedModel(
        id="consent",
        is_contributor=True,
        privacy=Privacy(trains_on_data=True),
        cost=TokenCost(output=1),
        monthly_allowance=5.0,
    )
    # Contributor status no longer adds a separate consent gate.
    assert exclusion_reasons(consent, SelectionCriteria(allow_paid_training=True)) == []
    contrib_paid = NormalizedModel(
        id="cp", is_contributor=True, cost=TokenCost(output=1), monthly_allowance=10.0
    )
    assert exclusion_reasons(contrib_paid, c) == []
    global_model = NormalizedModel(
        id="g", privacy=Privacy(requires_global_region=True), monthly_allowance=5.0
    )
    assert "Global workspace region" in exclusion_reasons(global_model, SelectionCriteria())[0]
    assert exclusion_reasons(global_model, SelectionCriteria(allow_global_region=True)) == []
    contrib = NormalizedModel(
        id="c1",
        is_contributor=True,
        cost=TokenCost(output=0.0),
        monthly_allowance=10.0,
    )
    assert exclusion_reasons(contrib, c) == []
    no_priv = NormalizedModel(
        id="np",
        privacy=None,
        cost=TokenCost(output=1.0),
        monthly_allowance=10.0,
    )
    assert exclusion_reasons(no_priv, SelectionCriteria(require_zdr=True)) == [
        "excluded by privacy criteria"
    ]


def test_exclusion_reasons_zero_priced_with_allowance():
    c = SelectionCriteria()
    odd = NormalizedModel(id="odd", plan_allowances={"go": 15.0})
    assert odd.is_zero_cost is True
    assert exclusion_reasons(odd, c) == [
        "zero-priced but publishes a monthly allowance (incomplete catalogue data)"
    ]
    assert _bucket_exclusion_reason(exclusion_reasons(odd, c)[0]) == "Incomplete Catalogue Data"
    # Zero-cost without an allowance is still the normal free pool.
    assert exclusion_reasons(NormalizedModel(id="free"), c) == []
    unlimited = NormalizedModel(id="unlimited", plan_allowances={"go": math.inf})
    assert exclusion_reasons(unlimited, c) == []


def test_report_excludes_model_without_target_plan_allowance():
    paid = TokenCost(input=1.0, output=2.0)
    go_only = NormalizedModel(id="go-only", cost=paid, plan_allowances={"go": 15.0})
    both = NormalizedModel(
        id="both",
        cost=paid,
        plan_allowances={"go": 15.0, "go-plus": 60.0, "go-teams": 100.0},
    )
    snap = create_snapshot([go_only, both], plan="go")

    report = build_report(snapshot=snap, now=NOW)
    plus = report["plans"]["go-plus"]

    assert "opencode-go/go-only" in {e["model_id"] for e in plus["excluded"]}
    assert "opencode-go/go-only" not in plus["tiers"].values()
    assert "opencode-go/go-only" not in plus["fallbacks"]["heavy"]


def test_report_does_not_exclude_zero_cost_unlimited_allowance():
    model = NormalizedModel(id="unlimited-free", plan_allowances={"go": math.inf})
    report = build_report(snapshot=create_snapshot([model], plan="go"), now=NOW)

    for section in report["plans"].values():
        assert "opencode-go/unlimited-free" not in {e["model_id"] for e in section["excluded"]}


def test_empty_tier_becomes_warning():
    snap = create_snapshot([NormalizedModel(id="only", cost=TokenCost(output=1))])
    report = build_report(snapshot=snap, now=NOW)
    assert any("no model available for tier heavy" in w for w in report["warnings"])
    assert report["plans"]["go"]["explanations"]["heavy"]["why"].startswith("no eligible")
    assert "no eligible free model" in report["plans"]["go"]["explanations"]["free"]["why"]


def test_assert_no_aa_rejects_benchmark_keys():
    assert_no_aa({"a": [{"b": 1}]})
    with pytest.raises(ValueError, match=r"\$\.a\[0\]\.aa_iq"):
        assert_no_aa({"a": [{"aa_iq": 1}]})
    with pytest.raises(ValueError, match="speed_tps"):
        assert_no_aa({"speed_tps": 1})
    with pytest.raises(ValueError, match="effective_quality"):
        assert_no_aa({"effective_quality": 1})


def test_strip_aa_arbitrary_keys():
    data = {
        "name": "test",
        "aa_extra": 42,
        "effective_quality": 84.5,
        "nested": {"aa_custom": "x", "ok": 1},
        "list": [{"aa_sub": True, "keep": 2}],
    }
    stripped = _strip_aa(data)
    assert_no_aa(stripped)
    assert stripped == {
        "name": "test",
        "nested": {"ok": 1},
        "list": [{"keep": 2}],
    }


def test_summarize_junit(tmp_path):
    ok = '<testcase classname="t.A" name="ok"/>'
    bad = '<testcase classname="t.A" name="bad"><failure message="x"/></testcase>'
    err = '<testcase classname="t.B" name="err"><error message="x"/></testcase>'
    skip = '<testcase classname="t.B" name="skip"><skipped/></testcase>'
    offline = _junit(tmp_path, "offline", [ok, bad, err])
    live = _junit(tmp_path, "live", [ok, skip])
    summary = summarize_junit([offline, live])
    assert summary["result"] == "fail"
    assert summary["runs"][0] == {
        "name": "offline",
        "passed": 1,
        "failed": 2,
        "skipped": 0,
        "failing": ["t.A::bad", "t.B::err"],
    }
    assert summary["runs"][1]["skipped"] == 1
    assert summarize_junit([live])["result"] == "pass"
    broken = tmp_path / "broken.xml"
    broken.write_text("<not xml")
    bad_summary = summarize_junit([str(broken), str(tmp_path / "missing.xml")])
    assert bad_summary["result"] == "fail"
    assert all("unreadable" in r["error"] for r in bad_summary["runs"])


def test_markdown_sections(snapshot, tmp_path):
    junit = _junit(
        tmp_path, "offline", ['<testcase classname="t" name="bad"><failure/></testcase>']
    )
    report = build_report(junit_paths=[junit], snapshot=snapshot, now=NOW)
    report["warnings"] = ["something odd"]
    md = render_markdown(report)
    for heading in (
        "## Plan: go",
        "## Plan: go-plus",
        "## Plan: go-teams",
        "## How this was decided",
        "## Warnings",
        "## Provenance",
        "## Test results",
    ):
        assert heading in md
    assert "| heavy | `opencode-go/alpha-max` |" in md
    assert "Excluded models" in md and "something odd" in md
    assert "Overall: **fail**" in md and "`t::bad`" in md
    assert "https://example.invalid/docs" in md

    clean = render_markdown(build_report(snapshot=snapshot, now=NOW))
    assert "No test results supplied." in clean
    assert "## Warnings\n\nNone." in clean


def test_markdown_error_run_and_no_exclusions(snapshot, tmp_path):
    report = build_report(junit_paths=[str(tmp_path / "gone.xml")], snapshot=snapshot, now=NOW)
    report["plans"]["go"]["excluded"] = []
    md = render_markdown(report)
    assert "unreadable" in md and "None." in md


def test_write_report(snapshot, tmp_path):
    out = tmp_path / "nested" / "out"
    write_report(build_report(snapshot=snapshot, now=NOW), out)
    assert json.loads((out / "results.json").read_text())["schema_version"] == 1
    assert (out / "README.md").read_text().startswith("# OpenCode Go model tiers")


def test_cli_report_fetches_live_without_aa_and_exit_codes(snapshot, tmp_path, capsys):
    out = tmp_path / "out"
    junit = _junit(tmp_path, "offline", ['<testcase classname="t" name="ok"/>'])
    with patch(
        "opencode_go_model_optimizer.report.fetch_and_build_snapshot", return_value=snapshot
    ) as fetch:
        rc = main(["report", "--out-dir", str(out), "--tests-junit", junit])
        assert rc == 0
        fetch.assert_called_once_with(plan="go", use_aa=False)
        snapshot.warnings.append("odd")
        assert main(["report", "--out-dir", str(out)]) == 0  # warnings alone do not fail
        assert main(["report", "--out-dir", str(out), "--fail-on-warnings"]) == 2
    assert "report contains warnings" in capsys.readouterr().err
    assert json.loads((out / "results.json").read_text())["warnings"] == ["odd"]


def test_stale_cache_loader_accepts_any_age(snapshot, tmp_path):
    import os

    from opencode_go_model_optimizer.ingest.snapshot import get_cache_snapshot_path, save_snapshot

    path = get_cache_snapshot_path("go")
    save_snapshot(snapshot, path)
    os.utime(path, (0, 0))  # 1970
    assert load_cached_snapshot("go", ttl_seconds=3600) is None
    assert load_cached_snapshot("go", ttl_seconds=math.inf) is not None


def test_num_none():
    from opencode_go_model_optimizer.report import _candidate_row, _num

    assert _num(None) is None
    m = NormalizedModel(id="test", monthly_allowance=None)
    row = _candidate_row(m)
    assert row["monthly_allowance"] is None


def test_render_markdown_mermaid_diagrams_and_charts(snapshot, tmp_path):
    junit = _junit(
        tmp_path,
        "offline",
        [
            '<testcase classname="t" name="t1"/>',
            '<testcase classname="t" name="t2"><failure/></testcase>',
            '<testcase classname="t" name="t3"><skipped/></testcase>',
        ],
    )
    report = build_report(junit_paths=[junit], snapshot=snapshot, now=NOW)
    md = render_markdown(report)

    # 1. Cross-Plan Overview Diagram (flowchart LR)
    assert "```mermaid\nflowchart LR" in md
    assert 'plan_go["Plan: go"]' in md
    assert (
        'plan_go --> plan_go_heavy["heavy: opencode-go/alpha-max (Allowance: $12, Cluster: $9-$18/1M)"]'
        in md
    )
    assert (
        'plan_go --> plan_go_balanced["balanced: opencode-go/beta-pro (Allowance: $40, Cluster: $2.5375-$4/1M)"]'
        in md
    )
    assert (
        'plan_go --> plan_go_light["light: opencode-go/beta-flash (Allowance: $80, Cluster: $0.4-$1.2/1M)"]'
        in md
    )
    assert (
        'plan_go --> plan_go_free["free: opencode-go/epsilon-free (Allowance: -, Cluster: N/A)"]'
        in md
    )
    assert 'plan_go_plus["Plan: go-plus"]' in md
    assert (
        'plan_go_plus --> plan_go_plus_heavy["heavy: opencode-go/alpha-max (Allowance: $120, Cluster: $9-$18/1M)"]'
        in md
    )
    assert 'plan_go_teams["Plan: go-teams"]' in md
    assert (
        'plan_go_teams --> plan_go_teams_heavy["heavy: opencode-go/alpha-max (Allowance: $24, Cluster: $9-$18/1M)"]'
        in md
    )

    # 2. Per-Plan xychart-beta
    assert "xychart-beta" in md
    assert 'title "Plan go: Tier Allowances ($) & Output Costs ($/1M)"' in md
    assert 'x-axis ["heavy", "balanced", "light"]' in md
    assert "bar [12, 40, 80]" in md
    assert "line [9, 2.5375, 0.8458]" in md
    assert 'title "Plan go-plus: Tier Allowances ($) & Output Costs ($/1M)"' in md
    assert "bar [120, 400, 800]" in md

    # 3. Per-Plan Excluded Models pie showData
    assert "pie showData" in md
    assert 'title "Plan go: Model Eligibility & Exclusion Breakdown"' in md
    assert '"Eligible" : 10' in md
    assert '"Global Region Required" : 1' in md
    assert '"Free Model Trains" : 1' in md
    assert '"Paid Model Trains" : 1' in md
    assert '"Not Active" : 1' in md

    # 4. Test Results pie showData
    assert 'title "Test Suite Results"' in md
    assert '"Passed" : 1' in md
    assert '"Failed" : 1' in md
    assert '"Skipped" : 1' in md

    # Invariant: NO Artificial Analysis / Benchmark data
    assert_no_aa(report)
    assert "aa_" not in md
    assert "speed_tps" not in md
    assert '"bpe"' not in md


def test_render_markdown_mermaid_edge_cases():
    # Unit tests for helpers
    assert _format_allowance(None) == "-"
    assert _format_allowance("unlimited") == "unlimited"
    assert _format_allowance(12.0) == "$12"
    assert _format_allowance(12.5) == "$12.5"

    assert _format_cluster_range(None) == "N/A"
    assert _format_cluster_range({}) == "N/A"
    assert _format_cluster_range({"output_cost_per_1m_range": [1.0, 5.0]}) == "$1-$5/1M"

    assert _bucket_exclusion_reason("requires Global workspace region") == "Global Region Required"
    assert _bucket_exclusion_reason("free model trains on request data") == "Free Model Trains"
    assert _bucket_exclusion_reason("paid model trains on request data") == "Paid Model Trains"
    assert _bucket_exclusion_reason("status is 'deprecated' (not active)") == "Not Active"
    assert _bucket_exclusion_reason("unpriced: allowance is zero") == "Unpriced / Other"
    assert _bucket_exclusion_reason("any generic reason") == "Unpriced / Other"

    assert _round_chart_num(None) == 0
    assert _round_chart_num("invalid") == 0
    assert _round_chart_num(float("inf")) == 0
    assert _round_chart_num(float("nan")) == 0
    assert _round_chart_num(10.0) == 10
    assert _round_chart_num(2.55555) == 2.5556

    # Empty plans dict -> no flowchart
    minimal_report = {
        "generated_at": "2026-11-10T12:00:00+00:00",
        "tool_version": "0.1.0",
        "git_sha": None,
        "plans": {},
        "warnings": [],
        "provenance": {"sources": {}, "note": ""},
        "how_decided": [],
        "tests": None,
    }
    md = render_markdown(minimal_report)
    assert "flowchart LR" not in md
    assert "No test results supplied." in md

    # Plan with no numeric paid tiers and excluded model with empty reasons
    sparse_plan_report = {
        "generated_at": "2026-11-10T12:00:00+00:00",
        "tool_version": "0.1.0",
        "git_sha": None,
        "plans": {
            "custom": {
                "criteria": {},
                "explanations": {
                    "heavy": {
                        "model_id": None,
                        "fallbacks": [],
                        "explanation": {"monthly_allowance": None, "output_cost_per_1m": None},
                        "cluster": None,
                        "why": "none",
                    }
                },
                "excluded": [
                    {"model_id": "m1", "reasons": []},
                    {"model_id": "m2", "reasons": ["unrecognized reason"]},
                ],
            }
        },
        "warnings": [],
        "provenance": {"sources": {}, "note": ""},
        "how_decided": [],
        "tests": {
            "result": "pass",
            "runs": [{"name": "empty", "passed": 0, "failed": 0, "skipped": 0, "failing": []}],
        },
    }
    sparse_md = render_markdown(sparse_plan_report)
    assert 'plan_custom["Plan: custom"]' in sparse_md
    assert "xychart-beta" not in sparse_md
    assert '"Unpriced / Other" : 2' in sparse_md
    assert "Test Suite Results" not in sparse_md
