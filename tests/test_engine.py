"""Tests for ranking engines and capability tier allocator."""

from pathlib import Path
from unittest.mock import patch

from opencode_go_model_optimizer.engine.allowance import (
    rank_free_models,
    rank_paid_models,
    tie_break_key,
)
from opencode_go_model_optimizer.engine.pareto import (
    WORKLOAD_PROFILES,
    compute_quota_burn_pct,
    compute_turn_cost,
    effective_quality,
    evaluate_pareto,
    fisher_jenks_3_clusters,
    ssd,
)
from opencode_go_model_optimizer.engine.tiers import (
    allocate_tiers,
    allocate_tiers_allowance,
    allocate_tiers_pareto,
    rank_catalog,
)
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_cli
from opencode_go_model_optimizer.ingest.docs import parse_go_pricing
from opencode_go_model_optimizer.ingest.models import ModelLimit, NormalizedModel, TokenCost
from opencode_go_model_optimizer.ingest.snapshot import (
    load_reference_snapshot,
    merge_docs_pricing,
)

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _get_fixture_models() -> list[NormalizedModel]:
    cli_text = (FIXTURES_DIR / "catalogue_cli.txt").read_text(encoding="utf-8")
    mdx_text = (FIXTURES_DIR / "go_docs.mdx").read_text(encoding="utf-8")
    models = parse_catalogue_cli(cli_text)
    pricing = parse_go_pricing(mdx_text)
    return merge_docs_pricing(models, pricing)


def test_allowance_paid_ranking():
    models = _get_fixture_models()
    ranked = rank_paid_models(models)

    # Preview and deprecated models must be excluded
    ids = [m.id for m in ranked]
    assert "step-5-preview" not in ids
    assert "deprecated-old-model" not in ids
    assert "iceman-contributor-free" not in ids
    assert "beam-free" not in ids

    # DeepSeek ($100 cap) should rank above GLM ($60 cap) and Qwen ($15 cap)
    assert ids[0] == "deepseek-v4.1-flash"
    assert ids[1] == "glm-5.3-flash"
    assert ids[2] == "qwen3.8-max"


def test_allowance_free_ranking():
    models = _get_fixture_models()
    ranked = rank_free_models(models)

    # Iceman (release_date: 2026-09-12) vs Beam (release_date: 2026-09-01)
    # Iceman should rank first due to release recency
    assert len(ranked) >= 2
    assert ranked[0].id == "iceman-contributor-free"
    assert ranked[1].id == "beam-free"


def test_deterministic_tie_breaking():
    # Models with identical allowance and output cost
    m1 = NormalizedModel(
        id="model-b",
        name="Model B",
        release_date="2026-09-10",
        monthly_allowance=60.0,
        cost=TokenCost(input=1.0, output=2.0),
        limit=ModelLimit(context=500000),
    )
    m2 = NormalizedModel(
        id="model-a",
        name="Model A",
        release_date="2026-09-10",
        monthly_allowance=60.0,
        cost=TokenCost(input=1.0, output=2.0),
        limit=ModelLimit(context=1000000),  # Larger context should win
    )
    m3 = NormalizedModel(
        id="model-c",
        name="Model C",
        release_date="2026-09-09",  # Older release date should lose
        monthly_allowance=60.0,
        cost=TokenCost(input=1.0, output=2.0),
        limit=ModelLimit(context=1000000),
    )
    ranked = rank_paid_models([m1, m2, m3])
    assert [m.id for m in ranked] == ["model-a", "model-b", "model-c"]

    # When release date and context are also identical, id ASC wins
    m4 = NormalizedModel(
        id="model-z",
        name="Model Z",
        release_date="2026-09-10",
        monthly_allowance=60.0,
        cost=TokenCost(input=1.0, output=2.0),
        limit=ModelLimit(context=500000),
    )
    ranked_alphabetical = rank_paid_models([m4, m1])
    assert [m.id for m in ranked_alphabetical] == ["model-b", "model-z"]


def test_turn_cost_and_burn():
    m = NormalizedModel(
        id="test-model",
        name="Test Model",
        monthly_allowance=80.0,
        cost=TokenCost(input=0.30, output=1.20, cache_read=0.006),
    )
    # std turn: 750 * 0.30 + 60,000 * 0.006 + 250 * 1.20 = 225 + 360 + 300 = 885 / 1e6 = 0.000885
    turn_cost = compute_turn_cost(m)
    assert abs(turn_cost - 0.000885) < 1e-9

    # monthly turns: 80 / 0.000885 = 90395.48
    # burn per 1k turns: (1000 / 90395.48) * 100 = 1.10625%
    burn = compute_quota_burn_pct(m)
    assert abs(burn - 1.10625) < 1e-3


def test_fisher_jenks_clustering():
    models = [
        NormalizedModel(id="m1", aa_iq=55.0),
        NormalizedModel(id="m2", aa_iq=53.0),
        NormalizedModel(id="m3", aa_iq=46.0),
        NormalizedModel(id="m4", aa_iq=45.0),
        NormalizedModel(id="m5", aa_iq=25.0),
        NormalizedModel(id="m6", aa_iq=19.0),
    ]
    c1, c2, c3 = fisher_jenks_3_clusters(models)
    assert [m.id for m in c1] == ["m1", "m2"]
    assert [m.id for m in c2] == ["m3", "m4"]
    assert [m.id for m in c3] == ["m5", "m6"]


def test_pareto_evaluation():
    models = _get_fixture_models()
    qwen = next(m for m in models if m.id == "qwen3.8-max")
    deepseek = next(m for m in models if m.id == "deepseek-v4.1-flash")
    qwen.aa_iq = 45.0
    deepseek.aa_iq = 45.0
    evaluated = evaluate_pareto(models)
    assert len(evaluated) == len(models)

    assert qwen.composite_burn_pct is not None
    assert deepseek.composite_burn_pct is not None
    assert deepseek.bpe is not None


def test_allocate_tiers_allowance():
    models = _get_fixture_models()
    tiers = allocate_tiers_allowance(models)

    assert set(tiers.keys()) == {"heavy", "balanced", "light", "free"}
    assert tiers["heavy"] == "opencode-go/qwen3.8-max"
    assert tiers["balanced"] == "opencode-go/deepseek-v4.1-flash"
    assert tiers["light"] == "opencode-go/glm-5.3-flash"
    assert tiers["free"] == "opencode-go/iceman-contributor-free"


def test_allocate_tiers_with_preferences_and_skips():
    models = _get_fixture_models()
    # Pin preferred model for light
    prefs = {"light": "opencode-go/qwen3.8-max"}
    tiers = allocate_tiers_allowance(models, preferred_tiers=prefs)
    assert tiers["light"] == "opencode-go/qwen3.8-max"

    # Skip deepseek
    skips = {"deepseek-v4.1-flash"}
    tiers_skipped = allocate_tiers_allowance(models, skip_models=skips)
    assert tiers_skipped["balanced"] != "opencode-go/deepseek-v4.1-flash"


def test_allocate_tiers_pareto():
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx
    tiers = allocate_tiers_pareto(models)
    assert set(tiers.keys()) == {"heavy", "balanced", "light", "free"}
    assert all(tiers.values())


def test_rank_catalog():
    models = _get_fixture_models()
    ranked = rank_catalog(models, mode="allowance")
    assert len(ranked) > 0
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2


def test_tie_break_key():
    from opencode_go_model_optimizer.engine.allowance import _desc_str_key

    m1 = NormalizedModel(id="mod1", release_date="2026-05-01", limit=ModelLimit(context=128000))
    assert tie_break_key(m1) == (_desc_str_key("2026-05-01"), -128000, "mod1")
    m2 = NormalizedModel(id="mod2", release_date="", limit=ModelLimit(context=64000))
    assert tie_break_key(m2) == (_desc_str_key(""), -64000, "mod2")
    assert _desc_str_key("2026-05-01") < _desc_str_key("2026-04-30")


def test_compute_turn_cost_zero_cost():
    m = NormalizedModel(id="zero-m", is_zero_cost=True, cost=TokenCost(input=1.0, output=2.0))
    assert compute_turn_cost(m) == 0.0


def test_compute_quota_burn_pct_guards():
    # zero-cost model
    m_zero = NormalizedModel(id="zero", is_zero_cost=True)
    assert compute_quota_burn_pct(m_zero) == 0.0

    # infinite allowance
    m_inf = NormalizedModel(id="inf", monthly_allowance=float("inf"), cost=TokenCost(output=1.0))
    assert compute_quota_burn_pct(m_inf) == 0.0

    # turn_cost <= 0.0 (all token costs 0.0 and not marked zero_cost)
    m_free_tokens = NormalizedModel(
        id="free-tokens",
        monthly_allowance=50.0,
        cost=TokenCost(input=0.0, output=0.0, cache_read=0.0),
    )
    assert compute_quota_burn_pct(m_free_tokens) == 0.0

    # turns_per_mo <= 0.0 (negative allowance)
    m_neg = NormalizedModel(
        id="neg", monthly_allowance=-10.0, cost=TokenCost(input=1.0, output=1.0)
    )
    assert compute_quota_burn_pct(m_neg) == 100000.0

    # default allowance (None -> 15.0)
    m_none_allowance = NormalizedModel(
        id="none-allowance", monthly_allowance=None, cost=TokenCost(input=1.0, output=1.0)
    )
    burn_default = compute_quota_burn_pct(m_none_allowance)
    assert burn_default > 0.0


def test_effective_quality():
    # Both IQ and coding index present: 0.7 * iq + 0.3 * coding
    m_both = NormalizedModel(id="m1", aa_iq=80.0, aa_coding=90.0)
    assert abs(effective_quality(m_both) - (0.7 * 80.0 + 0.3 * 90.0)) < 1e-9

    # Only IQ present
    m_iq = NormalizedModel(id="m2", aa_iq=75.0)
    assert effective_quality(m_iq) == 75.0

    # Only coding present
    m_coding = NormalizedModel(id="m3", aa_coding=85.0)
    assert effective_quality(m_coding) == 85.0

    # Neither present
    m_none = NormalizedModel(id="m4")
    assert effective_quality(m_none) == 0.0


def test_ssd_empty_and_single():
    assert ssd([]) == 0.0
    assert ssd([10.0]) == 0.0
    assert abs(ssd([1.0, 3.0]) - 2.0) < 1e-9


def test_fisher_jenks_edge_cases_and_unconstrained_fallback():
    # 0 items
    assert fisher_jenks_3_clusters([]) == ([], [], [])

    # 1 item
    m1 = NormalizedModel(id="m1", aa_iq=50.0)
    assert fisher_jenks_3_clusters([m1]) == ([m1], [], [])

    # 2 items
    m2 = NormalizedModel(id="m2", aa_iq=40.0)
    assert fisher_jenks_3_clusters([m1, m2]) == ([m1], [m2], [])

    # 3 items
    m3 = NormalizedModel(id="m3", aa_iq=30.0)
    assert fisher_jenks_3_clusters([m1, m2, m3]) == ([m1], [m2], [m3])

    # 4 items with identical values triggers unconstrained fallback
    m_same = [NormalizedModel(id=f"m_same_{i}", aa_iq=50.0) for i in range(4)]
    c1, c2, c3 = fisher_jenks_3_clusters(m_same)
    assert len(c1) + len(c2) + len(c3) == 4
    assert len(c1) > 0 and len(c2) > 0 and len(c3) > 0


def test_evaluate_pareto_edge_cases():
    # No benchmarked models: all preview, deprecated, zero-cost, or without aa_iq
    unbenchmarked = [
        NormalizedModel(id="m-preview", is_preview=True, aa_iq=80.0),
        NormalizedModel(id="m-contributor", is_contributor=True, aa_iq=80.0),
        NormalizedModel(id="m-zero", is_zero_cost=True, aa_iq=80.0),
        NormalizedModel(id="m-deprecated", status="deprecated", aa_iq=80.0),
        NormalizedModel(id="m-no-iq", aa_iq=None),
    ]
    res = evaluate_pareto(unbenchmarked)
    assert res == unbenchmarked
    assert all(m.bpe is None for m in res)

    # Models with zero burn, extreme burn, and NaN guards
    # mA has is_zero_cost manually set to False, so it is evaluated in benchmarked, but turn_cost is 0.0 -> burn is 0.0
    mA = NormalizedModel(
        id="mA",
        aa_iq=60.0,
        monthly_allowance=100.0,
        cost=TokenCost(input=0.0, output=0.0, cache_read=0.0),
    )
    mA.is_zero_cost = False
    mB = NormalizedModel(
        id="mB", aa_iq=50.0, monthly_allowance=50.0, cost=TokenCost(input=1.0, output=2.0)
    )
    mC = NormalizedModel(
        id="mC", aa_iq=0.0, monthly_allowance=50.0, cost=TokenCost(input=1.0, output=2.0)
    )
    m_coding = NormalizedModel(
        id="m_coding", aa_coding=70.0, monthly_allowance=50.0, cost=TokenCost(input=1.0, output=2.0)
    )
    models = [mA, mB, mC, m_coding]
    evaluate_pareto(models)
    assert mA.composite_burn_pct == 0.0
    assert mA.bpe is not None
    assert mC.bpe is not None
    assert m_coding.bpe is not None

    # Test extreme burn (inf/nan) and nan guards for burn_eff and qual_eff
    mD = NormalizedModel(
        id="mD", aa_iq=70.0, monthly_allowance=50.0, cost=TokenCost(input=1.0, output=2.0)
    )
    mE = NormalizedModel(
        id="mE", aa_iq=80.0, monthly_allowance=50.0, cost=TokenCost(input=1.0, output=2.0)
    )
    with patch(
        "opencode_go_model_optimizer.engine.pareto.compute_quota_burn_pct",
        side_effect=[float("inf"), float("nan")],
    ):
        evaluate_pareto([mD, mE])
        assert mD.bpe == 0.0
        assert mE.bpe == 0.0

    # Test math.isnan(burn_eff) and math.isnan(qual_eff) fallback to 0.0
    mF = NormalizedModel(
        id="mF", aa_iq=70.0, monthly_allowance=50.0, cost=TokenCost(input=1.0, output=2.0)
    )
    mF.composite_burn_pct = 1.0
    # If peers have nan burn or nan iq
    with patch("math.isnan", side_effect=lambda val: True):
        # All isnan calls return True
        evaluate_pareto([mF])
        assert mF.bpe == 0.0


def test_allocate_tiers_pareto_comprehensive():
    # 1. Preferred tiers pinning
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx
    tiers_pref = allocate_tiers_pareto(
        models, preferred_tiers={"heavy": "opencode-go/deepseek-v4.1-flash"}
    )
    assert tiers_pref["heavy"] == "opencode-go/deepseek-v4.1-flash"

    # 2. At least 3 frontier models triggering cutoff calculation:
    m1 = NormalizedModel(
        id="f-heavy", aa_iq=90.0, monthly_allowance=100.0, cost=TokenCost(output=10.0)
    )
    m2 = NormalizedModel(
        id="f-bal", aa_iq=60.0, monthly_allowance=100.0, cost=TokenCost(output=5.0)
    )
    m3 = NormalizedModel(
        id="f-light", aa_iq=30.0, monthly_allowance=100.0, cost=TokenCost(output=2.0)
    )
    m4 = NormalizedModel(
        id="sub-heavy", aa_iq=85.0, monthly_allowance=100.0, cost=TokenCost(output=20.0)
    )
    m5 = NormalizedModel(
        id="sub-bal", aa_iq=55.0, monthly_allowance=100.0, cost=TokenCost(output=15.0)
    )
    m6 = NormalizedModel(
        id="sub-light", aa_iq=25.0, monthly_allowance=100.0, cost=TokenCost(output=5.0)
    )
    m_free = NormalizedModel(id="free-1", is_zero_cost=True)
    catalog = [m1, m2, m3, m4, m5, m6, m_free]

    tiers_cutoffs = allocate_tiers_pareto(catalog)
    assert tiers_cutoffs["heavy"] == "opencode-go/f-heavy"
    assert tiers_cutoffs["balanced"] == "opencode-go/f-bal"
    assert tiers_cutoffs["light"] == "opencode-go/f-light"
    assert tiers_cutoffs["free"] == "opencode-go/free-1"

    # 3. Empty cluster missing tier falls back to allowance picks
    with patch(
        "opencode_go_model_optimizer.engine.tiers.fisher_jenks_3_clusters",
        side_effect=[
            ([m1, m2], [m3], []),  # tier_light is empty
            ([m1], [m2], [m3]),  # fallback allocate_tiers_allowance
        ],
    ):
        tiers_fb = allocate_tiers_pareto(catalog)
        assert set(tiers_fb.keys()) == {"heavy", "balanced", "light", "free"}

    # 4. Small benchmarked list (len < 3):
    t_small = allocate_tiers_pareto([m1, m2, m_free])
    assert set(t_small.keys()) == {"heavy", "balanced", "light", "free"}

    # 5. Fallback to allowance allocation when tiers missing:
    t_single = allocate_tiers_pareto([m1])
    assert t_single["heavy"] == "opencode-go/f-heavy"

    # 6. Free tier fallback when free_pick is None but free_ranked is not empty
    t_free_pinned = allocate_tiers_pareto(
        [m1, m2, m3, m_free],
        preferred_tiers={"balanced": "opencode-go/free-1"},
    )
    assert t_free_pinned["balanced"] == "opencode-go/free-1"
    assert t_free_pinned["free"] == "opencode-go/free-1"

    # 7. Cluster returning ranked[0] when all models already in used_model_ids
    with patch(
        "opencode_go_model_optimizer.engine.tiers.fisher_jenks_3_clusters",
        return_value=([m1], [m1], [m2]),
    ):
        t_dup = allocate_tiers_pareto([m1, m2, m_free])
        assert t_dup["heavy"] == "opencode-go/f-heavy"
        assert t_dup["balanced"] == "opencode-go/f-heavy"


def test_allocate_tiers_allowance_edge_cases():
    # Only 1 paid model, 0 free models
    m_paid = NormalizedModel(
        id="paid-only",
        name="Paid Only",
        monthly_allowance=50.0,
        cost=TokenCost(output=1.0),
    )
    tiers = allocate_tiers_allowance([m_paid])
    assert tiers["heavy"] == "opencode-go/paid-only"
    assert tiers["balanced"] == ""
    assert tiers["light"] == ""
    assert tiers["free"] == ""

    # Free model already used in preferred_tiers
    m_free = NormalizedModel(id="free-only", is_zero_cost=True)
    tiers_free_used = allocate_tiers_allowance(
        [m_free, m_paid],
        preferred_tiers={"heavy": "opencode-go/free-only"},
    )
    assert tiers_free_used["heavy"] == "opencode-go/free-only"
    assert tiers_free_used["free"] == "opencode-go/free-only"

    # Test fallback branch when no eligible paid models exist
    tiers_no_paid = allocate_tiers_allowance([m_free])
    assert tiers_no_paid["free"] == "opencode-go/free-only"
    assert tiers_no_paid["heavy"] == ""
    assert tiers_no_paid["balanced"] == ""
    assert tiers_no_paid["light"] == ""

    # Test pick_best returning cluster_ranked[0] when all models already in used_model_ids (line 73)
    m_p1 = NormalizedModel(id="p1", monthly_allowance=50.0, cost=TokenCost(output=10.0))
    m_p2 = NormalizedModel(id="p2", monthly_allowance=40.0, cost=TokenCost(output=5.0))
    with patch(
        "opencode_go_model_optimizer.engine.tiers.fisher_jenks_3_clusters",
        return_value=([m_p1], [m_p1], [m_p2]),
    ):
        t_dup = allocate_tiers_allowance([m_p1, m_p2])
        assert t_dup["heavy"] == "opencode-go/p1"
        assert t_dup["balanced"] == "opencode-go/p1"


def test_allocate_tiers_pareto_pick_best_sorting():
    # 1. Pareto-optimal model beats non-Pareto even with lower BPE
    m_pareto = NormalizedModel(
        id="m_pareto",
        aa_iq=90.0,
        monthly_allowance=100.0,
        cost=TokenCost(output=10.0),
        is_pareto=True,
        bpe=0.5,
    )
    m_non_pareto = NormalizedModel(
        id="m_non_pareto",
        aa_iq=90.0,
        monthly_allowance=100.0,
        cost=TokenCost(output=10.0),
        is_pareto=False,
        bpe=0.9,
    )
    # 2. Lower burn beats higher burn when both Pareto and BPE tied
    m_lower_burn = NormalizedModel(
        id="m_lower_burn",
        aa_iq=60.0,
        monthly_allowance=100.0,
        cost=TokenCost(output=5.0),
        is_pareto=True,
        bpe=0.8,
        composite_burn_pct=2.0,
    )
    m_higher_burn = NormalizedModel(
        id="m_higher_burn",
        aa_iq=60.0,
        monthly_allowance=100.0,
        cost=TokenCost(output=5.0),
        is_pareto=True,
        bpe=0.8,
        composite_burn_pct=10.0,
    )
    # 3. Higher speed_tps beats lower speed when burn tied
    m_faster = NormalizedModel(
        id="m_faster",
        aa_iq=30.0,
        monthly_allowance=100.0,
        cost=TokenCost(output=2.0),
        is_pareto=True,
        bpe=0.7,
        composite_burn_pct=1.0,
        speed_tps=100.0,
        release_date="2026-09-01",
    )
    m_slower = NormalizedModel(
        id="m_slower",
        aa_iq=30.0,
        monthly_allowance=100.0,
        cost=TokenCost(output=2.0),
        is_pareto=True,
        bpe=0.7,
        composite_burn_pct=1.0,
        speed_tps=50.0,
        release_date="2026-09-01",
    )
    # 4. Newer release date beats older release date when speed tied
    m_newer = NormalizedModel(
        id="m_newer",
        aa_iq=30.0,
        monthly_allowance=100.0,
        cost=TokenCost(output=2.0),
        is_pareto=True,
        bpe=0.7,
        composite_burn_pct=1.0,
        speed_tps=50.0,
        release_date="2026-10-01",
    )
    m_older = NormalizedModel(
        id="m_older",
        aa_iq=30.0,
        monthly_allowance=100.0,
        cost=TokenCost(output=2.0),
        is_pareto=True,
        bpe=0.7,
        composite_burn_pct=1.0,
        speed_tps=50.0,
        release_date="2026-09-01",
    )
    m_free = NormalizedModel(id="free-m", is_zero_cost=True)

    with patch(
        "opencode_go_model_optimizer.engine.tiers.fisher_jenks_3_clusters",
        return_value=(
            [m_pareto, m_non_pareto],
            [m_lower_burn, m_higher_burn],
            [m_faster, m_slower],
        ),
    ):
        res = allocate_tiers_pareto(
            [m_pareto, m_non_pareto, m_lower_burn, m_higher_burn, m_faster, m_slower, m_free]
        )
        assert res["heavy"] == "opencode-go/m_pareto"
        assert res["balanced"] == "opencode-go/m_lower_burn"
        assert res["light"] == "opencode-go/m_faster"

    # Test release date tie breaker
    with patch(
        "opencode_go_model_optimizer.engine.tiers.fisher_jenks_3_clusters",
        return_value=([m_pareto], [m_lower_burn], [m_newer, m_older]),
    ):
        res2 = allocate_tiers_pareto([m_pareto, m_lower_burn, m_newer, m_older, m_free])
        assert res2["light"] == "opencode-go/m_newer"


def test_allocate_tiers_pareto_competitive_fallback():
    # If competitive pool has < 3 models, fallback to benchmarked pool
    m1 = NormalizedModel(id="m1", aa_iq=90.0, monthly_allowance=100.0, cost=TokenCost(output=10.0))
    m2 = NormalizedModel(id="m2", aa_iq=80.0, monthly_allowance=100.0, cost=TokenCost(output=5.0))
    m_low = NormalizedModel(
        id="m_low", aa_iq=20.0, monthly_allowance=100.0, cost=TokenCost(output=20.0)
    )
    catalog = [m1, m2, m_low]
    tiers = allocate_tiers_pareto(catalog)
    assert set(tiers.keys()) == {"heavy", "balanced", "light", "free"}


def test_allocate_tiers_pareto_reference_snapshot():
    snap = load_reference_snapshot()
    assert snap is not None
    tiers = allocate_tiers_pareto(snap.models)
    assert tiers["heavy"] in ("opencode-go/gemini-4-argon", "opencode-go/kimi-k3.1-pro")
    assert tiers["balanced"] in ("opencode-go/deepseek-v4.1-flash", "opencode-go/mimo-v2.6-pro")
    assert tiers["light"] == "opencode-go/mimo-v2.6-flash"
    assert tiers["free"] == "opencode-go/longcat-2.5-free"


def test_rank_catalog_both_modes():
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx

    # Test pareto mode without skips (all 4 tiers populated)
    ranked_pareto_all = rank_catalog(models, mode="pareto")
    pareto_tiers = {m.tier for m in ranked_pareto_all if m.tier is not None}
    assert pareto_tiers == {"heavy", "balanced", "light", "free"}
    assert any(m.tier is None for m in ranked_pareto_all)

    # Test pareto mode with skip_models
    ranked_pareto = rank_catalog(models, mode="pareto", skip_models={"deepseek-v4.1-flash"})
    assert all(m.id != "deepseek-v4.1-flash" for m in ranked_pareto)
    assert all(m.rank is not None for m in ranked_pareto)
    assert [m.rank for m in ranked_pareto] == list(range(1, len(ranked_pareto) + 1))

    # Test allowance mode with skips
    ranked_allowance = rank_catalog(models, mode="allowance", skip_models={"qwen3.8-max"})
    assert all(m.id != "qwen3.8-max" for m in ranked_allowance)
    assert all(m.rank is not None for m in ranked_allowance)
    assert [m.rank for m in ranked_allowance] == list(range(1, len(ranked_allowance) + 1))
    allowance_tiers = {m.tier for m in ranked_allowance if m.tier is not None}
    assert "free" in allowance_tiers
    assert any(m.tier is None for m in ranked_allowance)
    # Remaining active models (e.g. preview) must be included and sorted by id
    assert any(m.id == "step-5-preview" for m in ranked_allowance)


def test_allocate_tiers_unified():
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx
    res_p = allocate_tiers(models, mode="pareto")
    assert set(res_p.keys()) == {"heavy", "balanced", "light", "free"}
    res_a = allocate_tiers(models, mode="allowance")
    assert set(res_a.keys()) == {"heavy", "balanced", "light", "free"}


def test_model_roundtripping_and_allowance_normalization():
    # "unlimited", "inf", "+inf", "infinity"
    for s in ["unlimited", "inf", "+inf", "infinity", "UNLIMITED"]:
        m = NormalizedModel(id="m", monthly_allowance=s)
        assert m.monthly_allowance == float("inf")
        dumped = m.model_dump(mode="json")
        assert dumped["monthly_allowance"] == "unlimited"

    # float("inf")
    m_inf = NormalizedModel(id="m", monthly_allowance=float("inf"))
    assert m_inf.monthly_allowance == float("inf")
    assert m_inf.model_dump(mode="json")["monthly_allowance"] == "unlimited"

    # string number
    m_str = NormalizedModel(id="m", monthly_allowance="42.5")
    assert m_str.monthly_allowance == 42.5
    assert m_str.model_dump(mode="json")["monthly_allowance"] == 42.5

    # None
    m_none = NormalizedModel(id="m", monthly_allowance=None)
    assert m_none.monthly_allowance is None

    # invalid string / non-numeric object returns None
    m_invalid = NormalizedModel(id="m", monthly_allowance="not-a-number")
    assert m_invalid.monthly_allowance is None
    m_invalid_obj = NormalizedModel(id="m", monthly_allowance={"key": "val"})
    assert m_invalid_obj.monthly_allowance is None

    # full_id resolution when "/" is already in id
    m_slash = NormalizedModel(id="custom-provider/my-model")
    assert m_slash.full_id == "custom-provider/my-model"

    # full_id resolution when "/" is not in id
    m_noslash = NormalizedModel(id="my-model", provider_id="prov")
    assert m_noslash.full_id == "prov/my-model"


def test_min_context_and_allow_contributor():
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx

    # Test allow_contributor=False in allowance mode
    tiers_no_contrib = allocate_tiers_allowance(models, allow_contributor=False)
    assert tiers_no_contrib["free"] == "opencode-go/beam-free"

    # Test allow_contributor=False in pareto mode
    tiers_pareto_no_contrib = allocate_tiers_pareto(models, allow_contributor=False)
    assert tiers_pareto_no_contrib["free"] != "opencode-go/iceman-contributor-free"

    # Test min_context filter in allowance mode
    tiers_min_ctx = allocate_tiers_allowance(models, min_context=500000)
    for model_id in tiers_min_ctx.values():
        if model_id:
            m = next(x for x in models if x.full_id == model_id)
            assert m.limit.context >= 500000

    # Test min_context filter in pareto mode
    tiers_pareto_min_ctx = allocate_tiers_pareto(models, min_context=500000)
    for model_id in tiers_pareto_min_ctx.values():
        if model_id:
            m = next(x for x in models if x.full_id == model_id)
            assert m.limit.context >= 500000

    # Test min_context filter in rank_catalog
    ranked_min_ctx = rank_catalog(models, min_context=500000)
    assert len(ranked_min_ctx) > 0
    assert all(m.limit.context >= 500000 for m in ranked_min_ctx)

    # Test allocate_tiers unified with min_context and allow_contributor
    unified_p = allocate_tiers(models, mode="pareto", min_context=100000, allow_contributor=False)
    assert unified_p["free"] != "opencode-go/iceman-contributor-free"
    unified_a = allocate_tiers(
        models, mode="allowance", min_context=100000, allow_contributor=False
    )
    assert unified_a["free"] == "opencode-go/beam-free"


def test_workload_profiles():
    assert set(WORKLOAD_PROFILES.keys()) == {"general", "coding", "reasoning", "fast"}

    m = NormalizedModel(
        id="test-mod",
        monthly_allowance=30.0,
        cost=TokenCost(input=1.0, output=4.0, cache_read=0.2),
        aa_iq=50.0,
        aa_coding=40.0,
        speed_tps=120.0,
    )

    # Turn costs vary by profile
    cost_gen = compute_turn_cost(m, profile="general")
    cost_coding = compute_turn_cost(m, profile="coding")
    cost_reasoning = compute_turn_cost(m, profile="reasoning")
    cost_fast = compute_turn_cost(m, profile="fast")
    cost_fallback = compute_turn_cost(m, profile="unknown_profile")
    assert cost_fallback == cost_gen
    assert cost_coding > cost_gen
    assert cost_reasoning > cost_gen
    assert cost_fast < cost_gen

    # Quota burns vary by profile
    burn_gen = compute_quota_burn_pct(m, profile="general")
    burn_coding = compute_quota_burn_pct(m, profile="coding")
    assert burn_coding > burn_gen

    # Effective quality formulas
    q_gen = effective_quality(m, profile="general")
    assert abs(q_gen - (0.7 * 50.0 + 0.3 * 40.0)) < 1e-6

    q_coding = effective_quality(m, profile="coding")
    assert abs(q_coding - (0.4 * 50.0 + 0.6 * 40.0)) < 1e-6

    q_reasoning = effective_quality(m, profile="reasoning")
    assert abs(q_reasoning - (0.85 * 50.0 + 0.15 * 40.0)) < 1e-6

    # Fast profile with TPS
    q_fast = effective_quality(m, profile="fast")
    base_fast = 0.7 * 50.0 + 0.3 * 40.0
    expected_fast = base_fast * (0.85 + 0.15 * min(2.0, 120.0 / 100.0))
    assert abs(q_fast - expected_fast) < 1e-6

    # Fast profile capped at 2.0x TPS
    m_hyper = NormalizedModel(id="hyper", aa_iq=50.0, aa_coding=40.0, speed_tps=300.0)
    q_hyper = effective_quality(m_hyper, profile="fast")
    assert abs(q_hyper - (base_fast * (0.85 + 0.15 * 2.0))) < 1e-6

    # Fast profile without TPS or zero TPS
    m_no_tps = NormalizedModel(id="no-tps", aa_iq=50.0, aa_coding=40.0)
    assert effective_quality(m_no_tps, profile="fast") == base_fast
    m_zero_tps = NormalizedModel(id="zero-tps", aa_iq=50.0, aa_coding=40.0, speed_tps=0.0)
    assert effective_quality(m_zero_tps, profile="fast") == base_fast

    # Evaluate pareto and allocate tiers with profiles
    models = _get_fixture_models()
    for idx, mod in enumerate(models):
        mod.aa_iq = 50.0 - idx
        mod.aa_coding = 45.0 - idx

    evaluated_coding = evaluate_pareto(models, profile="coding")
    assert all(x.composite_burn_pct is not None for x in evaluated_coding)

    tiers_coding = allocate_tiers_pareto(models, profile="coding")
    assert set(tiers_coding.keys()) == {"heavy", "balanced", "light", "free"}

    ranked_fast = rank_catalog(models, mode="pareto", profile="fast")
    assert len(ranked_fast) > 0
