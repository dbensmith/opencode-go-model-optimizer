"""Offline tests for privacy/region parsing, eligibility filters, CLI flags and diff."""

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from opencode_go_model_optimizer.cli import main
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria, passes_privacy
from opencode_go_model_optimizer.engine.diff import diff_snapshots
from opencode_go_model_optimizer.engine.tiers import select_tiers
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_http
from opencode_go_model_optimizer.ingest.docs import parse_privacy
from opencode_go_model_optimizer.ingest.models import (
    Privacy,
    Snapshot,
    TokenCost,
)
from opencode_go_model_optimizer.ingest.snapshot import (
    create_snapshot,
    merge_privacy,
    save_snapshot,
)
from tests.conftest import CATALOGUE_HTTP_DATA as OC
from tests.conftest import MDX_TEXT as MDX
from tests.factories import make_model


def test_parse_privacy_fixture():
    p = parse_privacy(MDX)
    assert p["Kappa Free"]["requires_global_region"] is True
    assert p["Alpha Max"]["requires_global_region"] is False
    assert p["Lambda Contributor"]["retention_days"] is None
    assert p["Lambda Contributor"]["zdr"] is False
    assert p["Zeta Pro"]["retention_days"] == 30
    assert p["Zeta Pro"]["zdr"] is False
    assert p["Gamma Prime"]["zdr"] is True
    for name in ("Beta Flash", "Beta Pro"):
        assert p[name]["agreement_valid_until"] == "2026-11-30"
        assert p[name]["retention_days"] == 0  # "0 days\*"
    assert p["Gamma Prime"]["agreement_valid_until"] is None
    # slash-separated note keys apply to each named row
    assert "30 days" in (p["Eta Flash"]["note"] or "")
    assert parse_privacy("no privacy here") == {}


def test_global_region_note_tolerates_markers_and_case():
    mdx = "## Privacy\n\n| Model | Data retention |\n| - | - |\n"
    mdx += "| Foo | 0 days |\n\n- **Foo:** REQUIRES Global REGIONS please.\n"
    assert parse_privacy(mdx)["Foo"]["requires_global_region"] is True


def test_merge_privacy_catalogue_and_warnings():
    models = parse_catalogue_http(OC)
    warnings: list[str] = []
    merge_privacy(
        models,
        parse_privacy(MDX),
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
    m = make_model("x", name="X")
    warnings: list[str] = []
    merge_privacy([m], parse_privacy(MDX), warnings)
    assert m.privacy is None
    assert any("no docs privacy row: x" in w for w in warnings)


def test_missing_privacy_warning_only_covers_active_chat_models():
    models = [
        make_model("active-chat"),
        make_model("active-embedding", model_type="embedding"),
        make_model("old-chat", status="deprecated"),
    ]
    warnings: list[str] = []

    merge_privacy(models, {}, warnings)

    assert warnings == [
        "1 active model(s) have no docs privacy row: active-chat (and no Go privacy flags)"
    ]


def test_old_snapshot_without_privacy_loads():
    snap = Snapshot.model_validate_json('{"fetched_at": "t", "models": [{"id": "a", "name": "A"}]}')
    assert snap.models[0].privacy is None


def _m(mid, privacy=None, out=0.0, allowance=None, release_date=""):
    return make_model(
        mid,
        name=mid,
        cost=TokenCost(input=out, output=out),
        monthly_allowance=allowance,
        privacy=privacy,
        release_date=release_date,
    )


@pytest.mark.parametrize(
    ("kw", "privacy", "ok"),
    [
        ({}, None, True),
        ({}, Privacy(requires_global_region=True), False),
        ({"allow_global_region": True}, Privacy(requires_global_region=True), True),
        ({}, Privacy(trains_on_data=True), False),
        ({"allow_free_training": True}, Privacy(trains_on_data=True), True),
        ({}, Privacy(trains_on_data=False), True),
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


def test_passes_privacy_training_controls():
    paid_train = _m("paid", Privacy(trains_on_data=True), out=1.0, allowance=15.0)
    paid_clean = _m("paid-clean", Privacy(trains_on_data=False), out=1.0, allowance=15.0)
    consent_train = make_model(
        "consent",
        is_contributor=True,
        cost=TokenCost(input=1.0, output=1.0),
        monthly_allowance=15.0,
        privacy=Privacy(trains_on_data=True),
    )
    free_train = _m("free", Privacy(trains_on_data=True))

    assert not passes_privacy(paid_train, SelectionCriteria())
    assert passes_privacy(paid_train, SelectionCriteria(allow_paid_training=True))
    assert passes_privacy(paid_clean, SelectionCriteria())
    # Contributor status no longer adds a separate consent gate.
    assert passes_privacy(consent_train, SelectionCriteria(allow_paid_training=True))
    assert not passes_privacy(free_train, SelectionCriteria())
    assert passes_privacy(free_train, SelectionCriteria(allow_free_training=True))


def test_privacy_reason_reports_non_training_failure():
    from opencode_go_model_optimizer.engine.criteria import privacy_reason

    m = _m("free", Privacy(trains_on_data=True, zdr=False))
    c = SelectionCriteria(allow_free_training=True, require_zdr=True)
    assert privacy_reason(m, c) == "excluded by privacy criteria"


def test_free_tier_privacy_defaults():
    glob = _m("glob", Privacy(requires_global_region=True))
    local = _m("local", Privacy())
    # Global-region models are blocked by default.
    assert select_tiers([glob, local], SelectionCriteria()).primary["free"] == "opencode-go/local"
    assert select_tiers([glob], SelectionCriteria()).primary["free"] == ""
    allowed = SelectionCriteria(allow_global_region=True)
    assert select_tiers([glob, local], allowed).primary["free"] == "opencode-go/glob"

    # Free models that train are blocked by default.
    train = _m("train", Privacy(trains_on_data=True))
    assert select_tiers([train], SelectionCriteria()).primary["free"] == ""
    assert (
        select_tiers([train], SelectionCriteria(allow_free_training=True)).primary["free"]
        == "opencode-go/train"
    )


def test_select_without_privacy_data_unchanged():
    models = [_m("a"), _m("b", out=2.0, allowance=15.0)]
    base = select_tiers(models, SelectionCriteria()).primary
    assert base == select_tiers(models, SelectionCriteria(allow_global_region=True)).primary


def _snapshot_file(tmp_path: Path) -> Path:
    models = [
        _m("paid", Privacy(retention_days=30, zdr=False), out=2.0, allowance=30.0),
        _m("zdr-paid", Privacy(retention_days=0, zdr=True), out=3.0, allowance=30.0),
        _m(
            "free-global",
            Privacy(retention_days=0, zdr=True, requires_global_region=True),
            release_date="2026-11-02",
        ),
        _m("free-train", Privacy(trains_on_data=True), release_date="2026-10-24"),
        _m("free-plain", Privacy(retention_days=0, zdr=True), release_date="2026-10-05"),
    ]
    snap = create_snapshot(models, plan="go")
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
    assert out["tiers"]["free"] == "opencode-go/free-plain"
    out, _ = _select(tmp_path, capsys, "--allow-free-training")
    assert out["tiers"]["free"] == "opencode-go/free-train"
    out, _ = _select(tmp_path, capsys, "--allow-global-region")
    assert out["tiers"]["free"] == "opencode-go/free-global"
    out, _ = _select(tmp_path, capsys, "--allow-global-region", "--allow-free-training")
    assert out["tiers"]["free"] == "opencode-go/free-global"
    out, _ = _select(tmp_path, capsys, "--require-zdr")
    assert out["tiers"]["free"] == "opencode-go/free-plain"
    out, _ = _select(tmp_path, capsys, "--require-zdr", "--allow-global-region")
    assert out["tiers"]["free"] == "opencode-go/free-global"
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
    assert (
        main(
            [
                "rank",
                "--snapshot",
                str(path),
                "--format",
                "json",
                "--allow-global-region",
                "--allow-free-training",
            ]
        )
        == 0
    )
    data = json.loads(capsys.readouterr().out)
    assert any(d.get("privacy", {}).get("requires_global_region") for d in data)
    assert (
        main(
            [
                "rank",
                "--snapshot",
                str(path),
                "--format",
                "table",
                "--allow-global-region",
                "--allow-free-training",
            ]
        )
        == 0
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


def test_parse_privacy_two_column_retention_table():
    mdx = (
        "## Privacy\n\n"
        "| Model | Data retention |\n"
        "| ----- | -------------- |\n"
        "| Foo   | 0 days         |\n"
        "| Bar   | 30 days        |\n\n"
        "- **Foo:** Requires Global regions.\n"
    )
    p = parse_privacy(mdx)
    assert p["Foo"].get("trains_on_data") is None  # catalogue supplies the training flag
    assert p["Foo"]["retention_days"] == 0
    assert p["Foo"]["zdr"] is True
    assert p["Foo"]["requires_global_region"] is True
    assert p["Bar"]["retention_days"] == 30
    assert p["Bar"]["zdr"] is False


def test_merge_privacy_keeps_catalogue_flags_and_adds_docs_retention():
    m = make_model(
        "alpha",
        name="Alpha",
        privacy=Privacy(
            trains_on_data=True,
            zdr=False,
            requires_global_region=True,
            limited_regions=True,
        ),
    )
    docs_row = {
        "trains_on_data": None,
        "retention_days": 0,
        "zdr": True,
        "requires_global_region": False,
        "limited_regions": False,
        "note": "from docs",
        "agreement_valid_until": "2026-12-31",
    }
    merge_privacy([m], {"alpha": docs_row}, warnings=None)
    assert m.privacy is not None
    assert m.privacy.trains_on_data is True  # API flag is authoritative
    assert m.privacy.zdr is False  # API flag is authoritative
    assert m.privacy.requires_global_region is True
    assert m.privacy.limited_regions is True  # API flag is authoritative
    assert m.privacy.retention_days == 0  # docs-only field is merged in
    assert m.privacy.note == "from docs"
    assert m.privacy.agreement_valid_until == "2026-12-31"
