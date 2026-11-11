"""Offline tests for privacy/region parsing, eligibility filters, CLI flags and diff."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opencode_go_model_optimizer.cli import main
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria, passes_privacy
from opencode_go_model_optimizer.engine.diff import diff_snapshots
from opencode_go_model_optimizer.engine.tiers import rank_catalog, select_tiers
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_http
from opencode_go_model_optimizer.ingest.docs import (
    parse_endpoints_table,
    parse_privacy,
)
from opencode_go_model_optimizer.ingest.models import (
    NormalizedModel,
    Privacy,
    Snapshot,
    TokenCost,
)
from opencode_go_model_optimizer.ingest.snapshot import (
    create_snapshot,
    merge_privacy,
    save_snapshot,
)

FIX = Path(__file__).parent / "fixtures"
MDX = (FIX / "go_docs.mdx").read_text(encoding="utf-8")
OC = json.loads((FIX / "catalogue_http.json").read_text(encoding="utf-8"))


def test_parse_privacy_fixture():
    p = parse_privacy(MDX)
    assert p["Kappa Free"]["requires_global_region"] is True
    assert p["Alpha Max"]["requires_global_region"] is False
    assert p["Lambda Contributor"]["trains_on_data"] is True
    assert p["Lambda Contributor"]["retention_days"] is None
    assert p["Lambda Contributor"]["zdr"] is False
    assert p["Zeta Pro"]["retention_days"] == 30
    assert p["Zeta Pro"]["zdr"] is False
    assert p["Gamma Prime"]["zdr"] is True and p["Gamma Prime"]["trains_on_data"] is False
    for name in ("Beta Flash", "Beta Pro"):
        assert p[name]["agreement_valid_until"] == "2026-11-30"
        assert p[name]["retention_days"] == 0  # "0 days\*"
    assert p["Gamma Prime"]["agreement_valid_until"] is None
    # slash-separated note keys apply to each named row
    assert "30 days" in (p["Eta Flash"]["note"] or "")
    assert parse_privacy("no privacy here") == {}


def test_global_region_note_tolerates_markers_and_case():
    mdx = "## Privacy\n\n| Model | Model training | Data retention |\n| - | - | - |\n"
    mdx += "| Foo | Not used | 0 days |\n\n- **Foo:** REQUIRES Global REGIONS please.\n"
    assert parse_privacy(mdx)["Foo"]["requires_global_region"] is True


def test_merge_privacy_catalogue_and_warnings():
    models = parse_catalogue_http(OC)
    warnings: list[str] = []
    merge_privacy(
        models,
        parse_privacy(MDX),
        parse_endpoints_table(MDX),
        warnings,
        now=datetime(2026, 12, 15, tzinfo=UTC),
    )
    by_id = {m.id: m for m in models}
    assert by_id["kappa-free"].privacy.requires_global_region is True
    assert by_id["beta-pro"].privacy.agreement_valid_until == "2026-11-30"
    assert [m.id for m in models if m.status == "active" and m.privacy is None] == []
    assert any("2026-11-30 is in the past" in w for w in warnings)
    assert not any("no docs privacy row" in w for w in warnings)


def test_warning_for_missing_privacy_row():
    m = NormalizedModel(id="x", name="X")
    warnings: list[str] = []
    merge_privacy([m], parse_privacy(MDX), {}, warnings)
    assert m.privacy is None
    assert any("no docs privacy row: x" in w for w in warnings)


def test_old_snapshot_without_privacy_loads():
    snap = Snapshot.model_validate_json('{"fetched_at": "t", "models": [{"id": "a", "name": "A"}]}')
    assert snap.models[0].privacy is None


def _m(mid, privacy=None, out=0.0, allowance=None):
    return NormalizedModel(
        id=mid,
        name=mid,
        cost=TokenCost(input=out, output=out),
        monthly_allowance=allowance,
        privacy=privacy,
    )


@pytest.mark.parametrize(
    ("kw", "privacy", "ok"),
    [
        ({}, None, True),
        ({}, Privacy(requires_global_region=True), False),
        ({"allow_global_region": True}, Privacy(requires_global_region=True), True),
        ({"allow_training": False}, Privacy(trains_on_data=True), False),
        ({"allow_training": False}, Privacy(trains_on_data=False), True),
        ({}, Privacy(trains_on_data=True), True),
        ({"max_retention_days": 30}, Privacy(retention_days=30), True),
        ({"max_retention_days": 29}, Privacy(retention_days=30), False),
        ({"max_retention_days": 30}, Privacy(retention_days=None), False),
        ({"max_retention_days": 30}, None, False),
        ({"require_zdr": True}, Privacy(zdr=True, retention_days=0), True),
        ({"require_zdr": True}, Privacy(zdr=False), False),
        ({"require_zdr": True}, Privacy(), False),
        ({"require_zdr": True}, None, False),
    ],
)
def test_passes_privacy(kw, privacy, ok):
    assert passes_privacy(_m("a", privacy), SelectionCriteria(**kw)) is ok


def test_free_tier_skips_global_region_model_by_default():
    glob = _m("glob", Privacy(requires_global_region=True))
    local = _m("local", Privacy())
    assert select_tiers([glob, local], SelectionCriteria()).primary["free"] == "opencode-go/local"
    allowed = SelectionCriteria(allow_global_region=True)
    assert select_tiers([glob], allowed).primary["free"] == "opencode-go/glob"
    assert select_tiers([glob], SelectionCriteria()).primary["free"] == ""
    assert [m.id for m in rank_catalog([glob, local], SelectionCriteria())] == ["local"]


def test_select_without_privacy_data_unchanged():
    models = [_m("a"), _m("b", out=2.0, allowance=15.0)]
    base = select_tiers(models, SelectionCriteria()).primary
    assert base == select_tiers(models, SelectionCriteria(allow_global_region=True)).primary


def _snapshot_file(tmp_path: Path) -> Path:
    models = [
        _m("paid", Privacy(retention_days=30, zdr=False), out=2.0, allowance=30.0),
        _m("zdr-paid", Privacy(retention_days=0, zdr=True), out=3.0, allowance=30.0),
        _m("free-global", Privacy(retention_days=0, zdr=True, requires_global_region=True)),
        _m("free-train", Privacy(trains_on_data=True)),
    ]
    snap = create_snapshot(models, {}, plan="go")
    path = tmp_path / "s.json"
    save_snapshot(snap, path)
    return path


def _select(tmp_path, capsys, *flags):
    rc = main(
        ["select", "--snapshot", str(_snapshot_file(tmp_path)), "--mode", "allowance", *flags]
    )
    cap = capsys.readouterr()
    assert rc == 0
    return json.loads(cap.out), cap.err


def test_cli_flags_filter_free_tier(tmp_path, capsys):
    out, _ = _select(tmp_path, capsys)
    assert out["tiers"]["free"] == "opencode-go/free-train"
    out, _ = _select(tmp_path, capsys, "--no-training")
    assert out["tiers"]["free"] == ""
    out, _ = _select(tmp_path, capsys, "--allow-global-region", "--no-training")
    assert out["tiers"]["free"] == "opencode-go/free-global"
    out, _ = _select(tmp_path, capsys, "--require-zdr", "--allow-global-region")
    assert out["tiers"]["free"] == "opencode-go/free-global"
    out, _ = _select(tmp_path, capsys, "--require-zdr")
    assert out["tiers"]["free"] == ""
    out, _ = _select(tmp_path, capsys, "--max-retention-days", "0", "--top-n", "3")
    assert "opencode-go/paid" not in json.dumps(out["fallbacks"])


def test_pinned_model_excluded_by_privacy_warns(tmp_path, capsys):
    out, err = _select(tmp_path, capsys, "--prefer", "free=free-global")
    assert "preferred model 'free-global' for tier 'free' excluded by privacy filters" in err
    assert out["tiers"]["free"] != "opencode-go/free-global"
    out, err = _select(tmp_path, capsys, "--prefer", "free=free-global", "--allow-global-region")
    assert "excluded by privacy" not in err
    assert out["tiers"]["free"] == "opencode-go/free-global"


def test_explain_and_rank_include_privacy(tmp_path, capsys):
    out, _ = _select(tmp_path, capsys, "--explain", "--allow-global-region")
    assert out["explanations"]["free"]["privacy"]["requires_global_region"] is True
    path = _snapshot_file(tmp_path)
    assert main(["rank", "--snapshot", str(path), "--format", "json", "--allow-global-region"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert any(d.get("privacy", {}).get("requires_global_region") for d in data)
    assert (
        main(["rank", "--snapshot", str(path), "--format", "table", "--allow-global-region"]) == 0
    )
    table = capsys.readouterr().out
    assert "Privacy" in table
    assert "ZDR,global" in table
    assert "30d" in table
    assert "train" in table


def test_diff_reports_privacy_changes():
    old = Snapshot(fetched_at="a", models=[_m("a", Privacy(retention_days=0, zdr=True))])
    new = Snapshot(
        fetched_at="b",
        models=[_m("a", Privacy(retention_days=30, zdr=False, requires_global_region=True))],
    )
    d = diff_snapshots(old, new)
    assert d.privacy_changes["a"]["retention_days"] == (0, 30)
    assert d.privacy_changes["a"]["requires_global_region"] == (False, True)
    assert d.has_changes()
    assert "note" not in d.privacy_changes["a"]
