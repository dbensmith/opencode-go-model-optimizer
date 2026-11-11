"""Tests for the published results report."""

import json
import math
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from opencode_go_model_optimizer.cli import main
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_http
from opencode_go_model_optimizer.ingest.docs import (
    parse_endpoints_table,
    parse_go_pricing,
    parse_privacy,
    parse_request_estimates,
)
from opencode_go_model_optimizer.ingest.models import NormalizedModel, Privacy, TokenCost
from opencode_go_model_optimizer.ingest.snapshot import create_snapshot, load_cached_snapshot
from opencode_go_model_optimizer.report import (
    _strip_aa,
    assert_no_aa,
    build_report,
    exclusion_reasons,
    render_markdown,
    summarize_junit,
    write_report,
)

FIX = Path(__file__).parent / "fixtures"
MDX = (FIX / "go_docs.mdx").read_text(encoding="utf-8")
OC = json.loads((FIX / "catalogue_http.json").read_text(encoding="utf-8"))
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
        parse_go_pricing(MDX),
        all_plan_pricing={p: parse_go_pricing(MDX, plan=p) for p in ("go", "go-plus", "go-teams")},
        endpoints=parse_endpoints_table(MDX),
        privacy=parse_privacy(MDX),
        request_estimates=parse_request_estimates(MDX),
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
    assert go["tiers"]["free"] == "opencode-go/delta-contributor"
    assert len(go["explanations"]["light"]["candidates"]) == 3
    assert go["fallbacks"]["light"] == [
        c["model_id"] for c in go["explanations"]["light"]["candidates"][1:]
    ]
    heavy = go["explanations"]["heavy"]
    assert heavy["cluster"]["size"] >= 1 and heavy["why"]
    assert heavy["explanation"]["burn_basis_used"] == "estimate"
    assert heavy["explanation"]["privacy"]["requires_global_region"] is False
    text = json.dumps(report)
    assert "aa_" not in text and "speed_tps" not in text and '"bpe"' not in text
    # provenance carries digests only
    assert report["provenance"]["sources"]["docs"]["sha256"] == "ab" * 32
    assert "Synthetic Go Pricing" not in text
    assert report["tests"] is None
    assert report["warnings"] == []


def test_excluded_models_have_reasons(snapshot):
    excluded = {
        e["model_id"]: e["reasons"]
        for e in build_report(snapshot=snapshot, now=NOW)["plans"]["go"]["excluded"]
    }
    assert "preview/experimental model" in excluded["opencode-go/omega-preview"][0]
    assert "not active" in excluded["opencode-go/deprecated-old-model"][0]
    assert "Global workspace region" in excluded["opencode-go/kappa-free"][0]
    assert "contributor" in excluded["opencode-go/lambda-contributor"][0]
    assert "opencode-go/alpha-max" not in excluded


def test_exclusion_reasons_unpriced_and_unlimited():
    c = SelectionCriteria()
    paid = TokenCost(output=1)
    assert "no docs pricing row" in exclusion_reasons(NormalizedModel(id="a", cost=paid), c)[0]
    zero = NormalizedModel(id="a", cost=paid, monthly_allowance=0.0)
    assert "zero" in exclusion_reasons(zero, c)[0]
    assert exclusion_reasons(NormalizedModel(id="free"), c) == []  # zero-cost: free pool
    unlimited = NormalizedModel(id="a", cost=TokenCost(output=1), monthly_allowance=math.inf)
    assert "unlimited" in exclusion_reasons(unlimited, c)[0]
    ok = NormalizedModel(id="a", cost=TokenCost(output=1), monthly_allowance=5.0)
    assert exclusion_reasons(ok, c) == []
    trains = NormalizedModel(id="t", privacy=Privacy(trains_on_data=True), monthly_allowance=5.0)
    assert exclusion_reasons(trains, SelectionCriteria(allow_training=False)) == [
        "excluded by privacy criteria"
    ]
    contrib = NormalizedModel(
        id="c1",
        is_contributor=True,
        cost=TokenCost(output=0.0),
        monthly_allowance=10.0,
    )
    assert exclusion_reasons(contrib, SelectionCriteria(allow_contributor=True)) == []
    no_priv = NormalizedModel(
        id="np",
        privacy=None,
        cost=TokenCost(output=1.0),
        monthly_allowance=10.0,
    )
    assert exclusion_reasons(no_priv, SelectionCriteria(require_zdr=True)) == [
        "excluded by privacy criteria"
    ]


def test_empty_tier_becomes_warning():
    snap = create_snapshot([NormalizedModel(id="only", cost=TokenCost(output=1))], {})
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


def test_strip_aa_arbitrary_keys():
    data = {
        "name": "test",
        "aa_extra": 42,
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
