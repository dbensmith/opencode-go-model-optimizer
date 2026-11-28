"""Tests for ranking engines and capability tier allocator."""

import json
from dataclasses import FrozenInstanceError
from unittest.mock import patch

import pytest

import opencode_go_model_optimizer.engine as engine_pkg
from opencode_go_model_optimizer.engine import (
    PROFILE_SPECS,
    WorkloadProfile,
    model_burn,
)
from opencode_go_model_optimizer.engine.allowance import (
    _desc_str_key,
    is_eligible_free,
    rank_free_models,
    rank_paid_models,
    tie_break_key,
)
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.pareto import (
    WORKLOAD_PROFILES,
    compute_burn_pct,
    compute_quota_burn_pct,
    compute_turn_cost,
    effective_quality,
    evaluate_pareto,
    fisher_jenks_3_clusters,
    resolve_burn_basis,
    ssd,
)
from opencode_go_model_optimizer.engine.tiers import (
    BenchmarksUnavailableError,
    rank_catalog,
    rank_catalog_with_method,
    resolve_calculation_method,
    select_tiers,
)
from opencode_go_model_optimizer.formatters.rank import format_rank_json
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_http
from opencode_go_model_optimizer.ingest.models import (
    ModelLimit,
    NormalizedModel,
    RequestEstimate,
    TokenCost,
)
from opencode_go_model_optimizer.ingest.snapshot import apply_plan
from tests.conftest import CATALOGUE_HTTP_DATA
from tests.factories import make_model


def _criteria(kw):
    if "skip_models" in kw:
        kw["skip_models"] = frozenset(kw["skip_models"])
    return SelectionCriteria(**kw)


def _tiers(models, **kw):
    return select_tiers(models, _criteria(kw)).primary


def _tiers_pareto(models, **kw):
    return _tiers(models, mode="pareto", **kw)


def _fallbacks(models, **kw):
    return select_tiers(models, _criteria(kw)).candidates


def _rank(models, **kw):
    return rank_catalog(models, _criteria(kw))


def _get_fixture_models() -> list[NormalizedModel]:
    models = parse_catalogue_http(CATALOGUE_HTTP_DATA)
    return apply_plan(models, "go")


def test_allowance_paid_ranking():
    models = _get_fixture_models()
    ranked = rank_paid_models(models)

    # Preview models are included; zero-cost/deprecated models are still excluded.
    # Contributors are included in the raw ranking and filtered later by privacy/flags.
    ids = [m.id for m in ranked]
    assert "deprecated-old-model" not in ids
    assert "delta-contributor" not in ids
    assert "epsilon-free" not in ids
    assert "lambda-contributor" in ids

    # Allowance descending: beta-flash ($80), gamma-flash ($50), then the $40 tier.
    assert ids[:2] == ["beta-flash", "gamma-flash"]
    assert "beta-pro" in ids
    assert ids[-1] == "theta-pro"


def test_allowance_free_ranking():
    models = _get_fixture_models()
    ranked = rank_free_models(models)

    # Newest release first: delta (2026-10-01), epsilon (2026-09-20), kappa (2026-08-25)
    assert [m.id for m in ranked] == ["delta-contributor", "epsilon-free", "kappa-free"]


def test_zero_cost_model_with_allowance_is_not_free():
    """A paid model whose prices are missing is incomplete data, not a free endpoint."""
    plain_free = NormalizedModel(id="plain-free")
    assert is_eligible_free(plain_free) is True

    odd = NormalizedModel(id="odd", plan_allowances={"go": 15.0})
    assert odd.is_zero_cost is True
    assert is_eligible_free(odd) is False
    assert (
        is_eligible_free(
            NormalizedModel(id="old", plan_allowances={"go": 15.0}, status="deprecated")
        )
        is False
    )

    unlimited = NormalizedModel(id="unlimited", plan_allowances={"go": float("inf")})
    assert unlimited.is_zero_cost is True
    assert is_eligible_free(unlimited) is True

    selection = select_tiers(
        [odd, NormalizedModel(id="real-free")], SelectionCriteria(mode="allowance")
    )
    assert selection.primary["free"] == "opencode-go/real-free"


def test_preferred_zero_cost_catalogue_anomaly_is_not_selected():
    anomaly = make_model(
        "anom",
        input=0.0,
        output=0.0,
        plan_allowances={"go": 15.0},
        monthly_allowance=15.0,
    )

    selection = select_tiers(
        [anomaly],
        SelectionCriteria(mode="allowance", preferred_tiers={"free": "anom"}),
    )

    assert selection.primary == {"heavy": "", "balanced": "", "light": "", "free": ""}


def test_deterministic_tie_breaking():
    # Models with identical allowance and output cost
    m1 = make_model(
        "model-b",
        name="Model B",
        release_date="2026-09-10",
        monthly_allowance=60.0,
        input=1.0,
        output=2.0,
        context=500000,
    )
    m2 = make_model(
        "model-a",
        name="Model A",
        release_date="2026-09-10",
        monthly_allowance=60.0,
        input=1.0,
        output=2.0,
        context=1000000,  # Larger context should win
    )
    m3 = make_model(
        "model-c",
        name="Model C",
        release_date="2026-09-09",  # Older release date should lose
        monthly_allowance=60.0,
        input=1.0,
        output=2.0,
        context=1000000,
    )
    ranked = rank_paid_models([m1, m2, m3])
    assert [m.id for m in ranked] == ["model-a", "model-b", "model-c"]

    # When release date and context are also identical, id ASC wins
    m4 = make_model(
        "model-z",
        name="Model Z",
        release_date="2026-09-10",
        monthly_allowance=60.0,
        input=1.0,
        output=2.0,
        context=500000,
    )
    ranked_alphabetical = rank_paid_models([m4, m1])
    assert [m.id for m in ranked_alphabetical] == ["model-b", "model-z"]


def test_turn_cost_and_burn():
    m = make_model(
        "test-model",
        name="Test Model",
        monthly_allowance=80.0,
        input=0.30,
        output=1.20,
        cache_read=0.006,
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
        make_model("m1", aa_iq=55.0),
        make_model("m2", aa_iq=53.0),
        make_model("m3", aa_iq=46.0),
        make_model("m4", aa_iq=45.0),
        make_model("m5", aa_iq=25.0),
        make_model("m6", aa_iq=19.0),
    ]
    c1, c2, c3 = fisher_jenks_3_clusters(models)
    assert [m.id for m in c1] == ["m1", "m2"]
    assert [m.id for m in c2] == ["m3", "m4"]
    assert [m.id for m in c3] == ["m5", "m6"]


def test_pareto_evaluation():
    models = _get_fixture_models()
    alpha = next(m for m in models if m.id == "alpha-max")
    beta = next(m for m in models if m.id == "beta-flash")
    alpha.aa_iq = 45.0
    beta.aa_iq = 45.0
    evaluated = evaluate_pareto(models)
    assert len(evaluated) == len(models)

    by_id = {m.id: m for m in evaluated}
    assert all(m.effective_quality is not None for m in evaluated)
    assert by_id["alpha-max"].effective_quality == effective_quality(alpha)
    assert by_id["beta-flash"].effective_quality == effective_quality(beta)
    assert by_id["alpha-max"].composite_burn_pct is not None
    assert by_id["beta-flash"].composite_burn_pct is not None
    assert by_id["beta-flash"].bpe is not None
    # Inputs are never mutated
    assert alpha.composite_burn_pct is None and beta.bpe is None
    assert alpha.effective_quality is None and beta.effective_quality is None


def test_allocate_tiers_allowance():
    models = _get_fixture_models()
    tiers = _tiers(models)

    assert set(tiers.keys()) == {"heavy", "balanced", "light", "free"}
    assert tiers["heavy"] == "opencode-go/alpha-max"
    assert tiers["balanced"] == "opencode-go/beta-pro"
    assert tiers["light"] == "opencode-go/beta-flash"
    assert tiers["free"] == "opencode-go/epsilon-free"


def test_allocate_tiers_with_preferences_and_skips():
    models = _get_fixture_models()
    # Pin preferred model for light
    prefs = {"light": "opencode-go/alpha-max"}
    tiers = _tiers(models, preferred_tiers=prefs)
    assert tiers["light"] == "opencode-go/alpha-max"

    # Skip beta
    skips = {"beta-flash"}
    tiers_skipped = _tiers(models, skip_models=skips)
    assert tiers_skipped["balanced"] != "opencode-go/beta-flash"


def test_allocate_tiers_pareto():
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx
    tiers = _tiers_pareto(models)
    assert set(tiers.keys()) == {"heavy", "balanced", "light", "free"}
    assert all(tiers.values())


def test_rank_catalog():
    models = _get_fixture_models()
    ranked = _rank(models, mode="allowance")
    assert len(ranked) > 0
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2


def test_tie_break_key():
    from opencode_go_model_optimizer.engine.allowance import _desc_str_key

    m1 = make_model("mod1", release_date="2026-05-01", context=128000)
    assert tie_break_key(m1) == (_desc_str_key("2026-05-01"), -128000, "mod1")
    m2 = make_model("mod2", release_date="", context=64000)
    assert tie_break_key(m2) == (_desc_str_key(""), -64000, "mod2")
    assert _desc_str_key("2026-05-01") < _desc_str_key("2026-04-30")


def test_compute_turn_cost_zero_cost():
    m = NormalizedModel(id="zero-m", is_zero_cost=True, cost=TokenCost(input=1.0, output=2.0))
    assert compute_turn_cost(m) == 0.0


def test_compute_quota_burn_pct_guards():
    # zero-cost model
    m_zero = make_model("zero")
    assert compute_quota_burn_pct(m_zero) == 0.0

    # infinite allowance
    m_inf = make_model("inf", monthly_allowance=float("inf"), output=1.0)
    assert compute_quota_burn_pct(m_inf) == 0.0

    # turn_cost <= 0.0 (all token costs 0.0 and not marked zero_cost)
    m_free_tokens = make_model(
        "free-tokens", monthly_allowance=50.0, input=0.0, output=0.0, cache_read=0.0
    )
    assert compute_quota_burn_pct(m_free_tokens) == 0.0

    # turns_per_mo <= 0.0 (negative allowance)
    m_neg = make_model("neg", monthly_allowance=-10.0, input=1.0, output=1.0)
    assert compute_quota_burn_pct(m_neg) == 100000.0

    # default allowance (None -> 15.0)
    m_none_allowance = NormalizedModel(
        id="none-allowance", monthly_allowance=None, cost=TokenCost(input=1.0, output=1.0)
    )
    burn_default = compute_quota_burn_pct(m_none_allowance)
    assert burn_default > 0.0


def test_quota_burn_zero_allowance():
    m_zero_allowance = make_model("zero-allowance", monthly_allowance=0.0, input=1.0, output=1.0)
    assert compute_quota_burn_pct(m_zero_allowance) == 100000.0


def test_compute_burn_pct_uses_request_estimate():
    m = make_model(
        "estimated",
        input=1.0,
        output=1.0,
        plan_allowances={"go": 15.0},
        request_estimates={"go": RequestEstimate(per_5h=1000, per_week=2500, per_month=5000)},
    )
    apply_plan([m], "go")
    assert m.request_estimate is not None
    assert compute_burn_pct(m, basis="estimate") == 20.0
    assert compute_burn_pct(m, basis="tokens") == compute_quota_burn_pct(m)
    assert resolve_burn_basis("auto") == "estimate"
    assert resolve_burn_basis("auto", profile="coding") == "tokens"


def test_compute_burn_pct_without_estimate_falls_back_to_tokens():
    m = make_model("no-estimate", input=1.0, output=1.0, monthly_allowance=15.0)
    assert compute_burn_pct(m, basis="estimate") == compute_quota_burn_pct(m)


def test_effective_quality():
    # Both IQ and coding index present: 0.7 * iq + 0.3 * coding
    m_both = make_model("m1", aa_iq=80.0, aa_coding=90.0)
    assert abs(effective_quality(m_both) - (0.7 * 80.0 + 0.3 * 90.0)) < 1e-9

    # Only IQ present
    m_iq = make_model("m2", aa_iq=75.0)
    assert effective_quality(m_iq) == 75.0

    # Only coding present
    m_coding = make_model("m3", aa_coding=85.0)
    assert effective_quality(m_coding) == 85.0

    # Neither present
    m_none = make_model("m4")
    assert effective_quality(m_none) == 0.0


def test_ssd_empty_and_single():
    assert ssd([]) == 0.0
    assert ssd([10.0]) == 0.0
    assert abs(ssd([1.0, 3.0]) - 2.0) < 1e-9


def test_fisher_jenks_edge_cases_and_unconstrained_fallback():
    # 0 items
    assert fisher_jenks_3_clusters([]) == ([], [], [])

    # 1 item
    m1 = make_model("m1", aa_iq=50.0)
    assert fisher_jenks_3_clusters([m1]) == ([m1], [], [])

    # 2 items
    m2 = make_model("m2", aa_iq=40.0)
    assert fisher_jenks_3_clusters([m1, m2]) == ([m1], [m2], [])

    # 3 items
    m3 = make_model("m3", aa_iq=30.0)
    assert fisher_jenks_3_clusters([m1, m2, m3]) == ([m1], [m2], [m3])

    # 4 items with identical values triggers unconstrained fallback
    m_same = [make_model(f"m_same_{i}", aa_iq=50.0) for i in range(4)]
    c1, c2, c3 = fisher_jenks_3_clusters(m_same)
    assert len(c1) + len(c2) + len(c3) == 4
    assert len(c1) > 0 and len(c2) > 0 and len(c3) > 0


def test_evaluate_pareto_edge_cases():
    # No benchmarked models: all preview, deprecated, zero-cost, or without aa_iq
    unbenchmarked = [
        make_model("m-preview", is_preview=True, aa_iq=80.0),
        make_model("m-contributor", is_contributor=True, aa_iq=80.0),
        make_model("m-zero", aa_iq=80.0),
        make_model("m-deprecated", status="deprecated", aa_iq=80.0),
        NormalizedModel(id="m-no-iq", aa_iq=None),
    ]
    res = evaluate_pareto(unbenchmarked)
    assert [m.id for m in res] == [m.id for m in unbenchmarked]
    assert [m.effective_quality for m in res] == [80.0, 80.0, 80.0, 80.0, 0.0]
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
    mB = make_model("mB", aa_iq=50.0, monthly_allowance=50.0, input=1.0, output=2.0)
    mC = make_model("mC", aa_iq=0.0, monthly_allowance=50.0, input=1.0, output=2.0)
    m_coding = make_model("m_coding", aa_coding=70.0, monthly_allowance=50.0, input=1.0, output=2.0)
    models = [mA, mB, mC, m_coding]
    rA, _, rC, r_coding = evaluate_pareto(models)
    assert rA.composite_burn_pct == 0.0
    assert rA.bpe is not None
    assert rC.bpe is not None
    assert r_coding.bpe is not None

    # Test extreme burn (inf/nan) and nan guards for burn_eff and qual_eff
    mD = make_model("mD", aa_iq=70.0, monthly_allowance=50.0, input=1.0, output=2.0)
    mE = make_model("mE", aa_iq=80.0, monthly_allowance=50.0, input=1.0, output=2.0)
    with patch(
        "opencode_go_model_optimizer.engine.pareto.compute_quota_burn_pct",
        side_effect=[float("inf"), float("nan")],
    ):
        rD, rE = evaluate_pareto([mD, mE])
        assert rD.bpe == 0.0
        assert rE.bpe == 0.0

    # Test math.isnan(burn_eff) and math.isnan(qual_eff) fallback to 0.0
    mF = make_model("mF", aa_iq=70.0, monthly_allowance=50.0, input=1.0, output=2.0)
    mF.composite_burn_pct = 1.0
    # If peers have nan burn or nan iq
    with patch("math.isnan", side_effect=lambda val: True):
        # All isnan calls return True
        (rF,) = evaluate_pareto([mF])
        assert rF.bpe == 0.0


def test_allocate_tiers_pareto_comprehensive():
    # 1. Preferred tiers pinning
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx
    tiers_pref = _tiers_pareto(models, preferred_tiers={"heavy": "opencode-go/beta-flash"})
    assert tiers_pref["heavy"] == "opencode-go/beta-flash"

    # 2. At least 3 frontier models triggering cutoff calculation:
    m1 = make_model("f-heavy", aa_iq=90.0, monthly_allowance=100.0, output=10.0)
    m2 = make_model("f-bal", aa_iq=60.0, monthly_allowance=100.0, output=5.0)
    m3 = make_model("f-light", aa_iq=30.0, monthly_allowance=100.0, output=2.0)
    m4 = make_model("sub-heavy", aa_iq=85.0, monthly_allowance=100.0, output=20.0)
    m5 = make_model("sub-bal", aa_iq=55.0, monthly_allowance=100.0, output=15.0)
    m6 = make_model("sub-light", aa_iq=25.0, monthly_allowance=100.0, output=5.0)
    m_free = make_model("free-1")
    catalog = [m1, m2, m3, m4, m5, m6, m_free]

    tiers_cutoffs = _tiers_pareto(catalog)
    assert tiers_cutoffs["heavy"] == "opencode-go/f-heavy"
    # f-bal is the best quality-per-burn among models at least 60% as capable as heavy;
    # f-light is below that floor, so light stays f-light.
    assert tiers_cutoffs["balanced"] == "opencode-go/f-bal"
    assert tiers_cutoffs["light"] == "opencode-go/f-light"
    assert tiers_cutoffs["free"] == "opencode-go/free-1"

    # 3. Small benchmarked list: tiers are still populated (sharing when needed)
    t_small = _tiers_pareto([m1, m2, m_free])
    assert set(t_small.keys()) == {"heavy", "balanced", "light", "free"}
    assert all(t_small.values())

    # 4. Single benchmarked model: all paid tiers share it.
    t_single = _tiers_pareto([m1])
    assert t_single["heavy"] == "opencode-go/f-heavy"
    assert t_single["balanced"] == "opencode-go/f-heavy"
    assert t_single["light"] == "opencode-go/f-heavy"

    # 5. Free tier fallback when free_pick is None but free_ranked is not empty
    t_free_pinned = _tiers_pareto(
        [m1, m2, m3, m_free],
        preferred_tiers={"balanced": "opencode-go/free-1"},
    )
    assert t_free_pinned["balanced"] == "opencode-go/free-1"
    assert t_free_pinned["free"] == "opencode-go/free-1"


def test_allocate_tiers_allowance_edge_cases():
    # Only 1 paid model, 0 free models
    m_paid = make_model("paid-only", name="Paid Only", monthly_allowance=50.0, output=1.0)
    tiers = _tiers([m_paid])
    assert tiers["heavy"] == "opencode-go/paid-only"
    assert tiers["balanced"] == ""
    assert tiers["light"] == ""
    assert tiers["free"] == ""

    # Free model already used in preferred_tiers
    m_free = make_model("free-only")
    tiers_free_used = _tiers(
        [m_free, m_paid],
        preferred_tiers={"heavy": "opencode-go/free-only"},
    )
    assert tiers_free_used["heavy"] == "opencode-go/free-only"
    assert tiers_free_used["free"] == "opencode-go/free-only"

    # Test fallback branch when no eligible paid models exist
    tiers_no_paid = _tiers([m_free])
    assert tiers_no_paid["free"] == "opencode-go/free-only"
    assert tiers_no_paid["heavy"] == ""
    assert tiers_no_paid["balanced"] == ""
    assert tiers_no_paid["light"] == ""

    # Test pick_best returning cluster_ranked[0] when all models already in used_model_ids (line 73)
    m_p1 = make_model("p1", monthly_allowance=50.0, output=10.0)
    m_p2 = make_model("p2", monthly_allowance=40.0, output=5.0)
    with patch(
        "opencode_go_model_optimizer.engine.tiers.fisher_jenks_3_clusters",
        return_value=([m_p1], [m_p1], [m_p2]),
    ):
        t_dup = _tiers([m_p1, m_p2])
        assert t_dup["heavy"] == "opencode-go/p1"
        assert t_dup["balanced"] == "opencode-go/p1"


def test_allocate_tiers_pareto_pick_best_sorting():
    # 1. Pareto-optimal model beats non-Pareto even with lower BPE
    m_pareto = make_model(
        "m_pareto",
        aa_iq=90.0,
        effective_quality=90.0,
        monthly_allowance=100.0,
        output=10.0,
        is_pareto=True,
        bpe=0.5,
    )
    m_non_pareto = make_model(
        "m_non_pareto",
        aa_iq=90.0,
        effective_quality=90.0,
        monthly_allowance=100.0,
        output=10.0,
        is_pareto=False,
        bpe=0.9,
    )
    # 2. Lower burn beats higher burn when both Pareto and BPE tied
    m_lower_burn = make_model(
        "m_lower_burn",
        aa_iq=60.0,
        effective_quality=60.0,
        monthly_allowance=100.0,
        output=5.0,
        is_pareto=True,
        bpe=0.8,
        composite_burn_pct=2.0,
    )
    m_higher_burn = make_model(
        "m_higher_burn",
        aa_iq=60.0,
        effective_quality=60.0,
        monthly_allowance=100.0,
        output=5.0,
        is_pareto=True,
        bpe=0.8,
        composite_burn_pct=10.0,
    )
    # 3. Higher speed_tps beats lower speed when burn tied
    m_faster = make_model(
        "m_faster",
        aa_iq=30.0,
        effective_quality=30.0,
        monthly_allowance=100.0,
        output=2.0,
        is_pareto=True,
        bpe=0.7,
        composite_burn_pct=1.0,
        speed_tps=100.0,
        release_date="2026-09-01",
    )
    m_slower = make_model(
        "m_slower",
        aa_iq=30.0,
        effective_quality=30.0,
        monthly_allowance=100.0,
        output=2.0,
        is_pareto=True,
        bpe=0.7,
        composite_burn_pct=1.0,
        speed_tps=50.0,
        release_date="2026-09-01",
    )
    # 4. Newer release date beats older release date when speed tied
    m_newer = make_model(
        "m_newer",
        aa_iq=30.0,
        effective_quality=30.0,
        monthly_allowance=100.0,
        output=2.0,
        is_pareto=True,
        bpe=0.7,
        composite_burn_pct=1.0,
        speed_tps=50.0,
        release_date="2026-10-01",
    )
    m_older = make_model(
        "m_older",
        aa_iq=30.0,
        effective_quality=30.0,
        monthly_allowance=100.0,
        output=2.0,
        is_pareto=True,
        bpe=0.7,
        composite_burn_pct=1.0,
        speed_tps=50.0,
        release_date="2026-09-01",
    )
    m_free = make_model("free-m")

    with (
        patch(
            "opencode_go_model_optimizer.engine.tiers.evaluate_pareto",
            side_effect=lambda models, **kwargs: models,
        ),
        patch(
            "opencode_go_model_optimizer.engine.tiers.fisher_jenks_3_clusters",
            return_value=(
                [m_pareto, m_non_pareto],
                [m_lower_burn, m_higher_burn],
                [m_faster, m_slower],
            ),
        ),
    ):
        res = _tiers_pareto(
            [m_pareto, m_non_pareto, m_lower_burn, m_higher_burn, m_faster, m_slower, m_free]
        )
        assert res["heavy"] == "opencode-go/m_pareto"
        assert res["balanced"] == "opencode-go/m_lower_burn"
        assert res["light"] == "opencode-go/m_faster"

    # Test release date tie breaker
    with (
        patch(
            "opencode_go_model_optimizer.engine.tiers.evaluate_pareto",
            side_effect=lambda models, **kwargs: models,
        ),
        patch(
            "opencode_go_model_optimizer.engine.tiers.fisher_jenks_3_clusters",
            return_value=([m_pareto], [m_lower_burn], [m_newer, m_older]),
        ),
    ):
        res2 = _tiers_pareto([m_pareto, m_lower_burn, m_newer, m_older, m_free])
        assert res2["light"] == "opencode-go/m_newer"


def test_allocate_tiers_pareto_competitive_fallback():
    # If competitive pool has < 3 models, fallback to benchmarked pool
    m1 = make_model("m1", aa_iq=90.0, monthly_allowance=100.0, output=10.0)
    m2 = make_model("m2", aa_iq=80.0, monthly_allowance=100.0, output=5.0)
    m_low = make_model("m_low", aa_iq=20.0, monthly_allowance=100.0, output=20.0)
    catalog = [m1, m2, m_low]
    tiers = _tiers_pareto(catalog)
    assert set(tiers.keys()) == {"heavy", "balanced", "light", "free"}


def test_rank_catalog_both_modes():
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx

    # Test pareto mode without skips: every tier is populated, but tiers may share a model
    # when the Pareto frontier has fewer models than paid tiers.
    ranked_pareto_all = _rank(models, mode="pareto")
    pareto_tiers = {m.tier for m in ranked_pareto_all if m.tier is not None}
    assert {"heavy", "free"} <= pareto_tiers
    assert any(m.tier is None for m in ranked_pareto_all)
    selection = _tiers_pareto(models)
    assert set(selection) == {"heavy", "balanced", "light", "free"}
    assert all(selection.values())

    # Test pareto mode with skip_models
    ranked_pareto = _rank(models, mode="pareto", skip_models={"beta-flash"})
    assert all(m.id != "beta-flash" for m in ranked_pareto)
    assert all(m.rank is not None for m in ranked_pareto)
    assert [m.rank for m in ranked_pareto] == list(range(1, len(ranked_pareto) + 1))

    # Test allowance mode with skips
    ranked_allowance = _rank(models, mode="allowance", skip_models={"alpha-max"})
    assert all(m.id != "alpha-max" for m in ranked_allowance)
    assert all(m.rank is not None for m in ranked_allowance)
    assert [m.rank for m in ranked_allowance] == list(range(1, len(ranked_allowance) + 1))
    allowance_tiers = {m.tier for m in ranked_allowance if m.tier is not None}
    assert "free" in allowance_tiers
    assert any(m.tier is None for m in ranked_allowance)
    # Remaining active models (e.g. preview) must be included and sorted by id
    assert any(m.id == "omega-preview" for m in ranked_allowance)


def test_allowance_rank_json_includes_effective_quality():
    model = NormalizedModel(
        id="benchmarked",
        aa_iq=80.0,
        aa_coding=90.0,
        monthly_allowance=15.0,
        cost=TokenCost(output=1.0),
    )
    criteria = SelectionCriteria(mode="allowance", profile="coding", weights=(0.2, 0.8))
    (item,) = json.loads(format_rank_json(rank_catalog([model], criteria)))

    assert item["effective_quality"] == 88.0


def test_allocate_tiers_unified():
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx
    res_p = _tiers(models, mode="pareto")
    assert set(res_p.keys()) == {"heavy", "balanced", "light", "free"}
    res_a = _tiers(models, mode="allowance")
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


def test_min_context_filtering():
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx

    # Test min_context filter in allowance mode
    tiers_min_ctx = _tiers(models, min_context=500000)
    for model_id in tiers_min_ctx.values():
        if model_id:
            m = next(x for x in models if x.full_id == model_id)
            assert m.limit.context >= 500000

    # Test min_context filter in pareto mode
    tiers_pareto_min_ctx = _tiers_pareto(models, min_context=500000)
    for model_id in tiers_pareto_min_ctx.values():
        if model_id:
            m = next(x for x in models if x.full_id == model_id)
            assert m.limit.context >= 500000

    # Test min_context filter in rank_catalog
    ranked_min_ctx = _rank(models, min_context=500000)
    assert len(ranked_min_ctx) > 0
    assert all(m.limit.context >= 500000 for m in ranked_min_ctx)


def test_workload_profiles():
    assert set(WORKLOAD_PROFILES.keys()) == {"general", "coding", "reasoning", "fast"}

    m = make_model(
        "test-mod",
        monthly_allowance=30.0,
        input=1.0,
        output=4.0,
        cache_read=0.2,
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
    m_hyper = make_model("hyper", aa_iq=50.0, aa_coding=40.0, speed_tps=300.0)
    q_hyper = effective_quality(m_hyper, profile="fast")
    assert abs(q_hyper - (base_fast * (0.85 + 0.15 * 2.0))) < 1e-6

    # Fast profile without TPS or zero TPS
    m_no_tps = make_model("no-tps", aa_iq=50.0, aa_coding=40.0)
    assert effective_quality(m_no_tps, profile="fast") == base_fast
    m_zero_tps = make_model("zero-tps", aa_iq=50.0, aa_coding=40.0, speed_tps=0.0)
    assert effective_quality(m_zero_tps, profile="fast") == base_fast

    # Evaluate pareto and allocate tiers with profiles
    models = _get_fixture_models()
    for idx, mod in enumerate(models):
        mod.aa_iq = 50.0 - idx
        mod.aa_coding = 45.0 - idx

    evaluated_coding = evaluate_pareto(models, profile="coding")
    assert all(x.composite_burn_pct is not None for x in evaluated_coding)
    assert all(
        x.effective_quality == effective_quality(m, profile="coding")
        for x, m in zip(evaluated_coding, models, strict=True)
    )

    tiers_coding = _tiers_pareto(models, profile="coding")
    assert set(tiers_coding.keys()) == {"heavy", "balanced", "light", "free"}

    ranked_fast = _rank(models, mode="pareto", profile="fast")
    assert len(ranked_fast) > 0


def test_fisher_jenks_tie_breaking_deterministic():
    # 4 models with identical quality / val_fn
    m_base = make_model("mod-a", aa_iq=50.0, release_date="2026-09-01", context=500000)
    m_newer = make_model(
        "mod-b",
        aa_iq=50.0,
        release_date="2026-09-02",  # Newer release date -> 1st
        context=100000,
    )
    m_larger_ctx = make_model(
        "mod-c",
        aa_iq=50.0,
        release_date="2026-09-01",
        context=1000000,  # Larger context -> 2nd
    )
    m_tied_id = make_model(
        "mod-z",
        aa_iq=50.0,
        release_date="2026-09-01",
        context=500000,  # Same as mod-a, but id 'mod-z' > 'mod-a' -> 4th
    )

    c1, c2, c3 = fisher_jenks_3_clusters([m_base, m_tied_id, m_larger_ctx, m_newer])
    all_clustered = [m.id for m in c1 + c2 + c3]
    assert all_clustered == ["mod-b", "mod-c", "mod-a", "mod-z"]


def test_pick_best_tie_breaking_deterministic():
    # Models tied on pareto, bpe, burn, speed -> break ties via tie_break_key
    m1 = make_model(
        "model-ctx-low",
        aa_iq=90.0,
        effective_quality=90.0,
        monthly_allowance=100.0,
        output=10.0,
        is_pareto=True,
        bpe=0.8,
        composite_burn_pct=5.0,
        speed_tps=50.0,
        release_date="2026-09-01",
        context=128000,
    )
    m2 = make_model(
        "model-ctx-high",
        aa_iq=90.0,
        effective_quality=90.0,
        monthly_allowance=100.0,
        output=10.0,
        is_pareto=True,
        bpe=0.8,
        composite_burn_pct=5.0,
        speed_tps=50.0,
        release_date="2026-09-01",
        context=256000,  # Higher context should win
    )
    m3 = make_model(
        "model-ctx-high-b",
        aa_iq=90.0,
        effective_quality=90.0,
        monthly_allowance=100.0,
        output=10.0,
        is_pareto=True,
        bpe=0.8,
        composite_burn_pct=5.0,
        speed_tps=50.0,
        release_date="2026-09-01",
        context=256000,  # Same context as m2, but 'model-ctx-high' < 'model-ctx-high-b'
    )
    m_no_burn = NormalizedModel(
        id="model-no-burn",
        aa_iq=90.0,
        effective_quality=90.0,
        monthly_allowance=100.0,
        cost=TokenCost(output=10.0),
        is_pareto=True,
        bpe=0.8,
        composite_burn_pct=None,  # None burn should be treated as inf (lose to valid burn)
        speed_tps=50.0,
        release_date="2026-09-01",
        limit=ModelLimit(context=500000),
    )
    m_free = make_model("free-tier-mod")

    with (
        patch(
            "opencode_go_model_optimizer.engine.tiers.evaluate_pareto",
            side_effect=lambda models, **kwargs: models,
        ),
        patch(
            "opencode_go_model_optimizer.engine.tiers.fisher_jenks_3_clusters",
            return_value=([m1, m3, m_no_burn, m2], [], []),
        ),
    ):
        res = _tiers_pareto([m1, m3, m_no_burn, m2, m_free])
        # Higher context wins (m2 beats m1), id ASC wins (m2 beats m3), burn 5.0 beats inf (m2 beats m_no_burn)
        assert res["heavy"] == "opencode-go/model-ctx-high"


def test_rank_catalog_pareto_deterministic_tie_breaking():
    m_burn_none = NormalizedModel(
        id="mod-burn-none",
        aa_iq=80.0,
        effective_quality=80.0,
        cost=TokenCost(output=5.0),
        monthly_allowance=100.0,
        bpe=0.5,
        composite_burn_pct=None,
    )
    m_burn_valid = make_model(
        "mod-burn-valid",
        aa_iq=80.0,
        effective_quality=80.0,
        output=5.0,
        monthly_allowance=100.0,
        bpe=0.5,
        composite_burn_pct=10.0,
    )
    m_tied_burn_a = make_model(
        "mod-tied-a",
        aa_iq=80.0,
        effective_quality=80.0,
        output=5.0,
        monthly_allowance=100.0,
        bpe=0.5,
        composite_burn_pct=10.0,
        context=500000,
    )
    m_tied_burn_b = make_model(
        "mod-tied-b",
        aa_iq=80.0,
        effective_quality=80.0,
        output=5.0,
        monthly_allowance=100.0,
        bpe=0.5,
        composite_burn_pct=10.0,
        context=1000000,  # Larger context beats mod-tied-a
    )

    models = [m_burn_none, m_burn_valid, m_tied_burn_a, m_tied_burn_b]
    with patch(
        "opencode_go_model_optimizer.engine.tiers.evaluate_pareto",
        side_effect=lambda models, **kwargs: models,
    ):
        ranked = _rank(models, mode="pareto")

    ids = [m.id for m in ranked]
    # mod-tied-b (burn 10.0, 1M ctx) > mod-burn-valid/mod-tied-a (burn 10.0, 500k ctx / 0 ctx) > mod-burn-none (burn inf)
    assert ids[0] == "mod-tied-b"
    assert ids[-1] == "mod-burn-none"


def test_evaluate_pareto_clears_stale_metrics_and_respects_skips():
    m1 = make_model(
        "mod-1",
        aa_iq=90.0,
        output=10.0,
        monthly_allowance=100.0,
        bpe=0.99,
        is_pareto=True,
        effective_quality=1.0,
    )
    m2_unbenchmarked = NormalizedModel(
        id="mod-2",
        aa_iq=None,
        aa_coding=None,
        cost=TokenCost(output=5.0),
        monthly_allowance=100.0,
        bpe=0.88,
        is_pareto=True,
        effective_quality=88.0,
    )
    m3_skipped = make_model(
        "mod-3",
        aa_iq=95.0,
        output=1.0,
        monthly_allowance=100.0,
        bpe=0.95,
        is_pareto=True,
        effective_quality=77.0,
    )

    r1, r2, r3 = evaluate_pareto([m1, m2_unbenchmarked, m3_skipped], skip_models={"mod-3"})

    # m1 was benchmarked and evaluated
    assert r1.bpe is not None
    assert r1.is_pareto is True
    assert r1.effective_quality == 90.0

    # m2 was unbenchmarked: stale bpe, is_pareto, and effective_quality must be cleared
    assert r2.bpe is None
    assert r2.is_pareto is None
    assert r2.effective_quality == 0.0

    # m3 was skipped: burn and quality were computed, but stale bpe and is_pareto cleared
    assert r3.composite_burn_pct is not None
    assert r3.bpe is None
    assert r3.is_pareto is None
    assert r3.effective_quality == 95.0

    # The inputs keep their original values
    assert m2_unbenchmarked.bpe == 0.88 and m3_skipped.bpe == 0.95


def test_evaluate_pareto_skipped_model_does_not_dominate():
    # m_dom has higher quality and lower burn: if included, it would dominate m_sub
    m_dom = make_model("dominant-model", aa_iq=95.0, monthly_allowance=100.0, output=1.0)
    m_sub = make_model("sub-model", aa_iq=80.0, monthly_allowance=100.0, output=10.0)

    # Without skip: m_sub BPE is penalized by m_dom
    bpe_with_dom = evaluate_pareto([m_dom, m_sub])[1].bpe
    assert bpe_with_dom is not None and bpe_with_dom < 1.0

    # With m_dom in skip_models: m_sub becomes the sole benchmarked model (BPE 1.0, is_pareto=True)
    r_dom, r_sub = evaluate_pareto([m_dom, m_sub], skip_models={"dominant-model"})
    assert r_sub.bpe == 1.0
    assert r_sub.is_pareto is True
    assert r_dom.bpe is None
    assert r_dom.is_pareto is None


def test_allocate_tiers_pareto_reuses_frontier_when_smaller_than_tiers():
    # Only 1 benchmarked paid model, 2 other active paid models without benchmarks, and 1 free model.
    # Pareto tiers may share a model rather than dropping to an unbenchmarked/dominated one.
    m_heavy = make_model("m-heavy", aa_iq=90.0, monthly_allowance=100.0, output=50.0)
    m_paid_other1 = make_model("m-paid-other1", monthly_allowance=80.0, output=20.0)
    m_paid_other2 = make_model("m-paid-other2", monthly_allowance=60.0, output=10.0)
    m_free = make_model("m-free")

    tiers = _tiers_pareto([m_heavy, m_paid_other1, m_paid_other2, m_free])
    assert set(tiers.keys()) == {"heavy", "balanced", "light", "free"}
    assert tiers["heavy"] == "opencode-go/m-heavy"
    assert tiers["balanced"] == "opencode-go/m-heavy"
    assert tiers["light"] == "opencode-go/m-heavy"
    assert tiers["free"] == "opencode-go/m-free"


def test_pareto_light_tier_ignores_dominated_models():
    # Regression: a non-Pareto model must not win a tier just because the better Pareto
    # model was already claimed by a neighbouring tier. Tiers may share a model instead.
    def model(mid, iq, *, is_pareto, bpe, burn, output):
        return make_model(
            mid,
            aa_iq=iq,
            effective_quality=iq,
            is_pareto=is_pareto,
            bpe=bpe,
            composite_burn_pct=burn,
            monthly_allowance=100.0,
            output=output,
        )

    m_heavy = model("m-heavy", 90.0, is_pareto=True, bpe=1.0, burn=50.0, output=10.0)
    m_bal = model("m-bal", 60.0, is_pareto=True, bpe=1.0, burn=5.0, output=5.0)
    m_light = model("m-light", 59.0, is_pareto=True, bpe=1.0, burn=1.0, output=1.0)
    # Dominated by m_light on both quality and burn, and not on the Pareto frontier.
    m_dominated = model("m-dominated", 20.0, is_pareto=False, bpe=0.2, burn=2.0, output=0.5)
    m_free = make_model("m-free")

    with patch(
        "opencode_go_model_optimizer.engine.tiers.evaluate_pareto",
        side_effect=lambda models, **kwargs: models,
    ):
        tiers = _tiers_pareto([m_heavy, m_bal, m_light, m_dominated, m_free])

    assert tiers["heavy"] == "opencode-go/m-heavy"
    # m-light has nearly the same quality as m-bal at a fifth of the burn, so it is both the
    # best-value balanced pick and the cheapest light pick.
    assert tiers["balanced"] == "opencode-go/m-light"
    assert tiers["light"] == "opencode-go/m-light"
    assert "opencode-go/m-dominated" not in tiers.values()


def test_pareto_heavy_picks_value_winner_within_top_quality_band():
    # Heavy is not simply max IQ: among frontier models within 10% of the best quality,
    # the best quality-per-burn wins. Balanced uses the 60% floor and its own value ranking.
    def model(mid, iq, burn):
        return make_model(
            mid,
            aa_iq=iq,
            effective_quality=iq,
            is_pareto=True,
            bpe=1.0,
            composite_burn_pct=burn,
            monthly_allowance=100.0,
            output=10.0,
        )

    m_max = model("m-max", 100.0, 100.0)
    m_heavy_value = model("m-heavy-value", 92.0, 30.0)
    m_mid = model("m-mid", 70.0, 5.0)
    m_light = model("m-light", 40.0, 1.0)
    m_free = make_model("m-free")

    with patch(
        "opencode_go_model_optimizer.engine.tiers.evaluate_pareto",
        side_effect=lambda models, **kwargs: models,
    ):
        tiers = _tiers_pareto([m_max, m_heavy_value, m_mid, m_light, m_free])

    assert tiers["heavy"] == "opencode-go/m-heavy-value"
    assert tiers["balanced"] == "opencode-go/m-mid"
    assert tiers["light"] == "opencode-go/m-light"


def test_desc_str_key_sorts_strings_descending():
    samples = ["", "2026", "2026-05", "2026-05-01", "2026-04-30", "2025-12-31", "abc", "ab"]
    assert sorted(samples, key=_desc_str_key) == sorted(samples, reverse=True)


def test_evaluate_pareto_nan_burn_handling():
    m_nan = make_model("mod-nan", aa_iq=85.0, output=10.0, monthly_allowance=100.0)
    with patch(
        "opencode_go_model_optimizer.engine.pareto.compute_quota_burn_pct",
        return_value=float("nan"),
    ):
        evaluated = evaluate_pareto([m_nan])
        assert evaluated[0].bpe == 0.0


def test_selection_and_ranking_do_not_mutate_inputs():
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx
    before = [m.model_dump() for m in models]

    selection = select_tiers(models, SelectionCriteria(mode="pareto", top_n=3))
    ranked = _rank(models, mode="pareto")

    assert [m.model_dump() for m in models] == before
    assert any(m.bpe is not None for m in selection.models)
    assert all(m.rank is not None for m in ranked)


def test_rank_catalog_single_evaluate_pareto_call():
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx

    call_count = 0
    real_eval = evaluate_pareto

    def counted_eval(*args, **kwargs):
        nonlocal call_count
        call_count += 1
        return real_eval(*args, **kwargs)

    with patch(
        "opencode_go_model_optimizer.engine.tiers.evaluate_pareto",
        side_effect=counted_eval,
    ):
        ranked = _rank(models, mode="pareto")
        assert len(ranked) > 0
        # Exactly 1 call, not 2
        assert call_count == 1


def test_output_token_and_cost_filtering():
    m_low_out = make_model(
        "mod-low-out",
        monthly_allowance=100.0,
        output=2.0,
        max_output=4000,
        aa_iq=80.0,
    )
    m_high_cost = make_model(
        "mod-high-cost",
        monthly_allowance=100.0,
        output=25.0,
        max_output=16000,
        aa_iq=85.0,
    )
    m_compliant = make_model(
        "mod-compliant",
        monthly_allowance=100.0,
        output=3.0,
        max_output=16000,
        aa_iq=75.0,
    )
    m_free = make_model("mod-free", max_output=16000)

    models = [m_low_out, m_high_cost, m_compliant, m_free]

    # Filtering by min_output=8000 excludes m_low_out
    tiers_out = _tiers(models, min_output=8000)
    assert "opencode-go/mod-low-out" not in tiers_out.values()

    # Filtering by max_output_cost=5.0 excludes m_high_cost
    tiers_cost = _tiers(models, max_output_cost=5.0)
    assert "opencode-go/mod-high-cost" not in tiers_cost.values()

    # In pareto mode
    tiers_pareto = _tiers_pareto(models, min_output=8000, max_output_cost=5.0)
    assert "opencode-go/mod-low-out" not in tiers_pareto.values()
    assert "opencode-go/mod-high-cost" not in tiers_pareto.values()

    # In rank_catalog
    ranked = _rank(models, min_output=8000, max_output_cost=5.0)
    ranked_ids = [m.id for m in ranked]
    assert "mod-low-out" not in ranked_ids
    assert "mod-high-cost" not in ranked_ids
    assert "mod-compliant" in ranked_ids


def test_allocate_tier_fallbacks_top_n_1():
    models = _get_fixture_models()
    # top_n=1 returns single-element lists
    fallbacks_1 = _fallbacks(models, top_n=1, mode="allowance")
    assert set(fallbacks_1.keys()) == {"heavy", "balanced", "light", "free"}
    for tier in ("heavy", "balanced", "light", "free"):
        assert len(fallbacks_1[tier]) <= 1

    # top_n=0 returns single-element lists
    fallbacks_0 = _fallbacks(models, top_n=0, mode="allowance")
    assert set(fallbacks_0.keys()) == {"heavy", "balanced", "light", "free"}


def test_allocate_tier_fallbacks_pareto_mode():
    models = _get_fixture_models()
    for idx, m in enumerate(models):
        m.aa_iq = 50.0 - idx

    fallbacks = _fallbacks(models, top_n=3, mode="pareto")
    assert set(fallbacks.keys()) == {"heavy", "balanced", "light", "free"}
    assert len(fallbacks["heavy"]) <= 3
    assert len(fallbacks["free"]) >= 1

    # When benchmarked pool is empty in pareto mode: fails by default, falls back only with allow_fallback
    unbenchmarked = [
        make_model("p1", monthly_allowance=50.0, output=1.0),
        make_model("p2", monthly_allowance=40.0, output=1.0),
        make_model("f1"),
    ]
    with pytest.raises(BenchmarksUnavailableError, match="--allow-fallback"):
        select_tiers(unbenchmarked, SelectionCriteria(mode="pareto", top_n=2))

    sel_fb = select_tiers(
        unbenchmarked, SelectionCriteria(mode="pareto", top_n=2, allow_fallback=True)
    )
    assert sel_fb.method == "allowance"
    assert sel_fb.fell_back is True
    assert "opencode-go/f1" in sel_fb.candidates["free"]
    assert len(sel_fb.candidates["heavy"]) <= 2


def test_allocate_tier_fallbacks_allowance_mode():
    models = _get_fixture_models()
    fallbacks = _fallbacks(models, top_n=3, mode="allowance")
    assert set(fallbacks.keys()) == {"heavy", "balanced", "light", "free"}
    for tier in ("heavy", "balanced", "light", "free"):
        assert len(fallbacks[tier]) <= 3

    # Empty paid models in allowance mode
    free_only = [make_model("f1")]
    fb_free = _fallbacks(free_only, top_n=2, mode="allowance")
    assert fb_free["heavy"] == []
    assert fb_free["free"] == ["opencode-go/f1"]


def test_allocate_tier_fallbacks_filters():
    m1 = make_model(
        "mod-1",
        monthly_allowance=100.0,
        output=2.0,
        context=100000,
        max_output=16000,
    )
    m2 = make_model(
        "mod-2",
        monthly_allowance=80.0,
        output=30.0,  # High cost
        context=50000,
        max_output=16000,
    )
    m3 = make_model(
        "mod-3",
        monthly_allowance=60.0,
        output=3.0,
        context=100000,
        max_output=2000,  # Low output
    )
    m_free = make_model("mod-f", context=100000, max_output=16000)

    models = [m1, m2, m3, m_free]
    fb = _fallbacks(
        models,
        top_n=3,
        min_context=60000,
        min_output=8000,
        max_output_cost=10.0,
    )
    all_assigned = [mid for candidates in fb.values() for mid in candidates]
    assert "opencode-go/mod-2" not in all_assigned
    assert "opencode-go/mod-3" not in all_assigned
    assert "opencode-go/mod-1" in all_assigned


def test_allocate_tier_fallbacks_top_n_truncation():
    free_models = [make_model(f"free-{i}", release_date=f"2026-09-0{i}") for i in range(1, 6)]
    paid_models = [
        make_model(
            f"paid-{i}",
            monthly_allowance=100.0,
            output=10.0,
            release_date=f"2026-09-0{i}",
            aa_iq=90.0,
        )
        for i in range(1, 10)
    ]
    # In pareto mode with top_n=2
    fb_pareto = _fallbacks(free_models + paid_models, top_n=2, mode="pareto")
    assert len(fb_pareto["free"]) == 2
    assert len(fb_pareto["heavy"]) == 2

    # In allowance mode with top_n=2
    fb_allowance = _fallbacks(free_models + paid_models, top_n=2, mode="allowance")
    assert len(fb_allowance["free"]) == 2
    assert len(fb_allowance["heavy"]) == 2


def test_custom_token_mix_and_quality_weights():
    m = make_model(
        "custom-mix",
        monthly_allowance=100.0,
        input=1.0,
        output=2.0,
        cache_read=0.01,
        aa_iq=60.0,
        aa_coding=40.0,
        speed_tps=100.0,
    )

    assert effective_quality(m, weights=(0.5, 0.5)) == 50.0
    assert effective_quality(m) == 54.0
    assert abs(effective_quality(m, weights=(1.0, 0.0)) - 60.0) < 1e-9
    (evaluated,) = evaluate_pareto([m], weights=(0.5, 0.5))
    assert evaluated.effective_quality == 50.0

    # 1000 input + 0 cached + 100 output = (1000 * 1.0 + 100 * 2.0) / 1e6
    assert abs(compute_turn_cost(m, token_mix=(1000, 0, 100)) - 0.0012) < 1e-12
    burn = compute_quota_burn_pct(m, token_mix=(1000, 0, 100))
    assert abs(burn - 1.2) < 1e-9


def _burn_models() -> list[NormalizedModel]:
    return [
        make_model(
            "low-burn",
            monthly_allowance=100.0,
            input=0.1,
            output=0.1,
            aa_iq=50.0,
            aa_coding=50.0,
        ),
        make_model(
            "high-burn",
            monthly_allowance=10.0,
            input=1.0,
            output=1.0,
            aa_iq=60.0,
            aa_coding=60.0,
        ),
    ]


def test_max_burn_filtering_across_allocators():
    low = _burn_models()[0]
    high = _burn_models()[1]
    assert compute_quota_burn_pct(low) < 1.0
    assert compute_quota_burn_pct(high) > 1.0

    allowance = _tiers(_burn_models(), max_burn=1.0)
    assert high.full_id not in allowance.values()

    pareto = _tiers_pareto(_burn_models(), max_burn=1.0)
    assert high.full_id not in pareto.values()

    ranked = _rank(_burn_models(), max_burn=1.0)
    assert [m.id for m in ranked] == ["low-burn"]

    fallbacks = _fallbacks(_burn_models(), top_n=2, max_burn=1.0)
    flat = [model_id for candidates in fallbacks.values() for model_id in candidates]
    assert high.full_id not in flat
    assert low.full_id in flat


def test_token_mix_and_weights_flow_through_pareto():
    low, high = _burn_models()
    ranked = _rank(
        [low, high],
        mode="pareto",
        token_mix=(1000, 0, 100),
        weights=(0.5, 0.5),
        max_burn=5.0,
    )
    assert [m.id for m in ranked] == ["low-burn"]
    assert ranked[0].bpe is not None
    assert ranked[0].composite_burn_pct is not None
    assert ranked[0].effective_quality == 50.0


def test_tier_selection_clusters_and_allocate_efficiency():
    from opencode_go_model_optimizer.engine.tiers import (
        SelectionCriteria,
        TierSelection,
        _allocate_allowance,
        select_tiers,
    )

    # 1. TierSelection default field
    ts = TierSelection(primary={"free": "f"}, candidates={"free": ["f"]}, models=[])
    assert ts.clusters == {}

    # 2. select_tiers returns clusters populated
    m1 = make_model("p1", output=10.0, monthly_allowance=100.0)
    m2 = make_model("p2", output=5.0, monthly_allowance=50.0)
    m3 = make_model("p3", output=1.0, monthly_allowance=20.0)
    m_free = make_model("free1")
    pool = [m1, m2, m3, m_free]
    sel = select_tiers(pool, SelectionCriteria(mode="allowance"))
    assert "heavy" in sel.clusters
    assert "balanced" in sel.clusters
    assert "light" in sel.clusters

    # 3. _allocate_allowance with precomputed ranked models
    free_ranked = [m_free]
    paid_ranked = [m1, m2, m3]
    selected, full_clusters, ret_free, ret_paid = _allocate_allowance(
        pool,
        SelectionCriteria(mode="allowance"),
        free_ranked=free_ranked,
        paid_ranked=paid_ranked,
    )
    assert "heavy" in full_clusters
    assert ret_free is free_ranked
    assert ret_paid is paid_ranked
    assert selected["free"] == m_free.full_id


def test_profile_specs_sync_and_exports():
    assert set(PROFILE_SPECS.keys()) == {"general", "coding", "reasoning", "fast"}
    assert set(WORKLOAD_PROFILES.keys()) == set(PROFILE_SPECS.keys())

    for name, spec in PROFILE_SPECS.items():
        assert isinstance(spec, WorkloadProfile)
        assert WORKLOAD_PROFILES[name] == spec.token_mix

    assert PROFILE_SPECS["general"] == WorkloadProfile(
        token_mix=(750, 60000, 250), iq_weight=0.7, coding_weight=0.3, speed_scaled=False
    )
    assert PROFILE_SPECS["coding"] == WorkloadProfile(
        token_mix=(1500, 100000, 800), iq_weight=0.4, coding_weight=0.6, speed_scaled=False
    )
    assert PROFILE_SPECS["reasoning"] == WorkloadProfile(
        token_mix=(1000, 40000, 2000), iq_weight=0.85, coding_weight=0.15, speed_scaled=False
    )
    assert PROFILE_SPECS["fast"] == WorkloadProfile(
        token_mix=(500, 20000, 150), iq_weight=0.7, coding_weight=0.3, speed_scaled=True
    )

    with pytest.raises(FrozenInstanceError):
        PROFILE_SPECS["general"].iq_weight = 0.5  # type: ignore[misc]

    for sym in ("WorkloadProfile", "PROFILE_SPECS", "model_burn"):
        assert sym in engine_pkg.__all__
        assert hasattr(engine_pkg, sym)


def test_model_burn_handles_none_and_numeric():
    assert model_burn(NormalizedModel(id="none-burn", composite_burn_pct=None)) == float("inf")
    assert model_burn(NormalizedModel(id="zero-burn", composite_burn_pct=0.0)) == 0.0
    assert model_burn(NormalizedModel(id="num-burn", composite_burn_pct=12.5)) == 12.5


def test_pareto_cluster_and_rank_tie_breaking_preserved():
    def frontier_model(
        mid: str,
        *,
        iq: float,
        burn: float,
        bpe: float,
        speed_tps: float,
        release_date: str,
    ) -> NormalizedModel:
        return make_model(
            mid,
            aa_iq=iq,
            effective_quality=iq,
            is_pareto=True,
            bpe=bpe,
            composite_burn_pct=burn,
            monthly_allowance=100.0,
            output=5.0,
            speed_tps=speed_tps,
            release_date=release_date,
        )

    # Heavy tier tie-break: same quality/burn/bpe -> speed_tps wins over newer release_date
    h_fast = frontier_model(
        "h-fast", iq=100.0, burn=20.0, bpe=1.0, speed_tps=200.0, release_date="2026-01-01"
    )
    h_newer = frontier_model(
        "h-newer", iq=100.0, burn=20.0, bpe=1.0, speed_tps=50.0, release_date="2026-10-01"
    )
    with patch(
        "opencode_go_model_optimizer.engine.tiers.evaluate_pareto",
        side_effect=lambda models, **kwargs: models,
    ):
        heavy_sel = select_tiers([h_newer, h_fast], SelectionCriteria(mode="pareto", top_n=2))
    assert [m.id for m in heavy_sel.clusters["heavy"]] == ["h-fast", "h-newer"]
    assert heavy_sel.primary["heavy"] == "opencode-go/h-fast"

    # Balanced tier tie-break: does NOT include speed_tps, so newer release_date wins over speed
    # Keep a higher-quality model outside the balanced value winner so both tie in balanced_pool.
    b_top = frontier_model(
        "b-top", iq=100.0, burn=50.0, bpe=1.0, speed_tps=100.0, release_date="2026-05-01"
    )
    b_fast = frontier_model(
        "b-fast", iq=70.0, burn=10.0, bpe=0.9, speed_tps=300.0, release_date="2026-01-01"
    )
    b_newer = frontier_model(
        "b-newer", iq=70.0, burn=10.0, bpe=0.9, speed_tps=20.0, release_date="2026-10-01"
    )
    with patch(
        "opencode_go_model_optimizer.engine.tiers.evaluate_pareto",
        side_effect=lambda models, **kwargs: models,
    ):
        bal_sel = select_tiers([b_top, b_fast, b_newer], SelectionCriteria(mode="pareto", top_n=3))
    assert [m.id for m in bal_sel.clusters["balanced"][:2]] == ["b-newer", "b-fast"]
    assert bal_sel.primary["balanced"] == "opencode-go/b-newer"

    # Light tier tie-break: burn asc first, then speed_tps desc before release_date
    l_cheapest = frontier_model(
        "l-cheap", iq=20.0, burn=1.0, bpe=1.0, speed_tps=10.0, release_date="2026-01-01"
    )
    l_tied_fast = frontier_model(
        "l-tied-fast", iq=30.0, burn=2.0, bpe=0.8, speed_tps=200.0, release_date="2026-01-01"
    )
    l_tied_newer = frontier_model(
        "l-tied-newer", iq=30.0, burn=2.0, bpe=0.8, speed_tps=10.0, release_date="2026-10-01"
    )
    with patch(
        "opencode_go_model_optimizer.engine.tiers.evaluate_pareto",
        side_effect=lambda models, **kwargs: models,
    ):
        light_sel = select_tiers(
            [l_tied_newer, l_tied_fast, l_cheapest], SelectionCriteria(mode="pareto")
        )
    assert [m.id for m in light_sel.clusters["light"]] == [
        "l-cheap",
        "l-tied-fast",
        "l-tied-newer",
    ]

    # select_tiers general_paid fallback includes speed_tps while rank_catalog does NOT
    m_anchor = frontier_model(
        "m-anchor", iq=100.0, burn=50.0, bpe=1.0, speed_tps=50.0, release_date="2026-05-01"
    )
    m_sub_fast = frontier_model(
        "m-sub-fast", iq=50.0, burn=5.0, bpe=0.8, speed_tps=250.0, release_date="2026-01-01"
    )
    m_sub_newer = frontier_model(
        "m-sub-newer", iq=50.0, burn=5.0, bpe=0.8, speed_tps=25.0, release_date="2026-11-01"
    )
    pool = [m_anchor, m_sub_newer, m_sub_fast]
    with patch(
        "opencode_go_model_optimizer.engine.tiers.evaluate_pareto",
        side_effect=lambda models, **kwargs: models,
    ):
        sel = select_tiers(pool, SelectionCriteria(mode="pareto", top_n=3))
        ranked = rank_catalog(pool, SelectionCriteria(mode="pareto"))

    # heavy_pool has only m_anchor, so remaining candidates come from general_paid (speed_tps wins)
    assert sel.candidates["heavy"] == [
        "opencode-go/m-anchor",
        "opencode-go/m-sub-fast",
        "opencode-go/m-sub-newer",
    ]
    # rank_catalog omits speed_tps, so release_date breaks the tie in favor of m-sub-newer
    assert [m.id for m in ranked] == ["m-anchor", "m-sub-newer", "m-sub-fast"]


def test_resolve_calculation_method_and_opt_in_fallback():
    paid_no_bm = [make_model("p1", monthly_allowance=50.0, output=1.0)]
    paid_bm = [make_model("p1", monthly_allowance=50.0, output=1.0, aa_iq=80.0)]

    assert resolve_calculation_method(paid_no_bm, SelectionCriteria(mode="allowance")) == (
        "allowance",
        False,
    )
    assert resolve_calculation_method(paid_bm, SelectionCriteria(mode="pareto")) == (
        "pareto",
        False,
    )
    assert resolve_calculation_method(
        paid_no_bm, SelectionCriteria(mode="pareto", allow_fallback=True)
    ) == ("allowance", True)

    with pytest.raises(BenchmarksUnavailableError, match="HTTP 429"):
        resolve_calculation_method(
            paid_no_bm, SelectionCriteria(mode="pareto"), benchmark_error="HTTP 429"
        )
    with pytest.raises(ValueError, match="unknown mode"):
        resolve_calculation_method(paid_no_bm, SelectionCriteria(mode="bogus"))

    with pytest.raises(BenchmarksUnavailableError):
        rank_catalog_with_method(paid_no_bm, SelectionCriteria(mode="pareto"))

    ranked, method, fell_back = rank_catalog_with_method(
        paid_no_bm, SelectionCriteria(mode="pareto", allow_fallback=True)
    )
    assert method == "allowance"
    assert fell_back is True
    assert ranked[0].tier == "heavy"

    sel_pareto = select_tiers(paid_bm, SelectionCriteria(mode="pareto"))
    assert sel_pareto.method == "pareto"
    assert sel_pareto.fell_back is False
