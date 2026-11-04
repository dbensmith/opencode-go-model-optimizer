"""Tests for ranking engines and capability tier allocator."""

from pathlib import Path

from opencode_go_model_optimizer.engine.allowance import (
    rank_free_models,
    rank_paid_models,
)
from opencode_go_model_optimizer.engine.pareto import (
    compute_quota_burn_pct,
    compute_turn_cost,
    evaluate_pareto,
    fisher_jenks_3_clusters,
)
from opencode_go_model_optimizer.engine.tiers import (
    allocate_tiers_allowance,
    allocate_tiers_pareto,
    rank_catalog,
)
from opencode_go_model_optimizer.ingest.catalogue import parse_catalogue_cli
from opencode_go_model_optimizer.ingest.docs import parse_go_pricing
from opencode_go_model_optimizer.ingest.models import ModelLimit, NormalizedModel, TokenCost
from opencode_go_model_optimizer.ingest.snapshot import merge_docs_pricing

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
    evaluated = evaluate_pareto(models)
    qwen = next(m for m in evaluated if m.id == "qwen3.8-max")
    deepseek = next(m for m in evaluated if m.id == "deepseek-v4.1-flash")

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
    tiers = allocate_tiers_pareto(models)
    assert set(tiers.keys()) == {"heavy", "balanced", "light", "free"}
    assert all(tiers.values())


def test_rank_catalog():
    models = _get_fixture_models()
    ranked = rank_catalog(models, mode="allowance")
    assert len(ranked) > 0
    assert ranked[0].rank == 1
    assert ranked[1].rank == 2
