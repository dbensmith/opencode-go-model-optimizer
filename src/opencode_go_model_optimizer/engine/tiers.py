"""Tier allocator mapping models to industry-standard capability tiers."""

import math
from dataclasses import dataclass, field, replace

from opencode_go_model_optimizer.engine.allowance import (
    rank_free_models,
    rank_paid_models,
    tie_break_key,
)
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria, passes_privacy
from opencode_go_model_optimizer.engine.pareto import (
    compute_burn_pct,
    effective_quality,
    evaluate_pareto,
    filter_benchmarked_models,
    fisher_jenks_3_clusters,
    model_burn,
)
from opencode_go_model_optimizer.engine.pricing import apply_price_basis
from opencode_go_model_optimizer.ingest.models import (
    NormalizedModel,
    is_incomplete_free_model,
    matches_any_id,
)

STANDARD_TIERS = ["heavy", "balanced", "light", "free"]
PAID_TIERS = ("heavy", "balanced", "light")
HEAVY_QUALITY_RATIO = 0.9
BALANCED_QUALITY_RATIO = 0.6


def resolve_mode(mode: str, models: list[NormalizedModel]) -> str:
    """Resolve auto mode to pareto when any active model has benchmark data, else allowance."""
    if mode != "auto":
        return mode
    if any(
        (m.aa_iq is not None or m.aa_coding is not None) for m in models if m.status == "active"
    ):
        return "pareto"
    return "allowance"


def _apply_filters(models: list[NormalizedModel], c: SelectionCriteria) -> list[NormalizedModel]:
    """Apply privacy/region, context, output, output-cost, and quota-burn constraints."""
    skips = c.skip_models
    min_ctx = c.min_context
    min_out = c.min_output
    max_cost = c.max_output_cost
    max_burn = c.max_burn
    prof, mix, basis = c.profile, c.token_mix, c.burn_basis

    return [
        m
        for m in models
        if m.model_type == "chat"
        and (not skips or not matches_any_id(m, skips))
        and passes_privacy(m, c)
        and (min_ctx <= 0 or m.limit.context >= min_ctx)
        and (min_out <= 0 or m.limit.output >= min_out)
        and (max_cost is None or m.is_zero_cost or m.cost.output <= max_cost)
        and (max_burn is None or compute_burn_pct(m, prof, mix, basis) <= max_burn)
    ]


@dataclass(frozen=True)
class TierSelection:
    """Primary pick and ordered candidates per tier, plus the models the picks came from.

    ``models`` is the filtered pool the selection ran on; in pareto mode these are evaluated
    copies carrying ``bpe``/``is_pareto``/``composite_burn_pct``/``effective_quality``.
    """

    primary: dict[str, str]
    candidates: dict[str, list[str]]
    models: list[NormalizedModel]
    clusters: dict[str, list[NormalizedModel]] = field(default_factory=dict)


def _is_unused(m: NormalizedModel, used: set[str]) -> bool:
    return not matches_any_id(m, used)


def _claim(selected: dict[str, str], used: set[str], tier: str, m: NormalizedModel) -> None:
    selected[tier] = m.full_id
    used.add(m.id)
    used.add(m.full_id)


def _pick_unused(ranked: list[NormalizedModel], used: set[str]) -> NormalizedModel | None:
    """First unused model, falling back to the top one (e.g. when a pin consumed the cluster)."""
    return next((m for m in ranked if _is_unused(m, used)), ranked[0] if ranked else None)


def _seed_selection(
    models: list[NormalizedModel],
    preferred: dict[str, str],
    free_ranked: list[NormalizedModel],
) -> tuple[dict[str, str], set[str]]:
    """Apply pinned preferences, then pick the free tier."""
    selected: dict[str, str] = {}
    used: set[str] = set()
    for tier in STANDARD_TIERS:
        pref = preferred.get(tier)
        if pref:
            hit = next(
                (
                    m
                    for m in models
                    if pref in (m.full_id, m.id)
                    and m.status == "active"
                    and not is_incomplete_free_model(m)
                ),
                None,
            )
            if hit:
                _claim(selected, used, tier, hit)
    if "free" not in selected:
        free_pick = _pick_unused(free_ranked, used)
        if free_pick:
            _claim(selected, used, "free", free_pick)
    return selected, used


def _tie_tail(m: NormalizedModel, *, include_speed: bool = False) -> tuple[object, ...]:
    """Return optional negative speed followed by the deterministic tie-break key."""
    if include_speed:
        return (-(m.speed_tps or 0.0), tie_break_key(m))
    return (tie_break_key(m),)


def _pareto_rank_key(
    m: NormalizedModel,
    *,
    include_speed: bool = False,
) -> tuple[object, ...]:
    """Sort key for Pareto-evaluated models: BPE desc, quality desc, burn asc."""
    return (
        -(m.bpe or 0.0),
        -(m.effective_quality or 0.0),
        model_burn(m),
        *_tie_tail(m, include_speed=include_speed),
    )


def _pick_paid_tiers(
    selected: dict[str, str], used: set[str], clusters: dict[str, list[NormalizedModel]]
) -> None:
    for tier in PAID_TIERS:
        if tier not in selected and clusters[tier]:
            _claim(selected, used, tier, clusters[tier][0])


def _pareto_clusters(
    evaluated: list[NormalizedModel], c: SelectionCriteria
) -> dict[str, list[NormalizedModel]]:
    """Rank the Pareto frontier for each tier's objective.

    ``heavy`` is the value winner among frontier models within 10% of the best quality,
    ``balanced`` the value winner among models at least 60% as capable as the best, and
    ``light`` the lowest-burn frontier model. Only Pareto-optimal models are eligible. Tiers
    may share a model.
    """
    empty: dict[str, list[NormalizedModel]] = {t: [] for t in PAID_TIERS}
    benchmarked = filter_benchmarked_models(evaluated, c.skip_models)
    if not benchmarked:
        return empty

    def quality(m: NormalizedModel) -> float:
        return m.effective_quality or 0.0

    def value_key(m: NormalizedModel, *, include_speed: bool) -> tuple[object, ...]:
        q = quality(m)
        b = model_burn(m)
        value = q / b if b > 0 and q > 0 else 0.0
        return (
            not bool(m.is_pareto),
            -value,
            -q,
            b,
            -(m.bpe or 0.0),
            *_tie_tail(m, include_speed=include_speed),
        )

    def light_key(m: NormalizedModel) -> tuple[object, ...]:
        return (
            not bool(m.is_pareto),
            model_burn(m),
            -quality(m),
            -(m.bpe or 0.0),
            *_tie_tail(m, include_speed=True),
        )

    frontier = [m for m in benchmarked if m.is_pareto] or benchmarked
    heavy_quality = max((quality(m) for m in frontier), default=0.0)
    # Heavy is the value winner among models within 10% of the best quality; balanced is the
    # value winner among models at least 60% as capable as the best. The bands keep a cheap,
    # much weaker model from being promoted just because it has a high quality/burn ratio.
    heavy_pool = [
        m for m in frontier if quality(m) >= heavy_quality * HEAVY_QUALITY_RATIO
    ] or frontier
    balanced_pool = [
        m for m in frontier if quality(m) >= heavy_quality * BALANCED_QUALITY_RATIO
    ] or frontier
    return {
        "heavy": sorted(heavy_pool, key=lambda m: value_key(m, include_speed=True)),
        "balanced": sorted(balanced_pool, key=lambda m: value_key(m, include_speed=False)),
        "light": sorted(frontier, key=light_key),
    }


def _allowance_clusters(paid: list[NormalizedModel]) -> dict[str, list[NormalizedModel]]:
    """Cluster paid models by output cost into ranked per-tier lists."""
    if not paid:
        return {t: [] for t in PAID_TIERS}
    clusters = fisher_jenks_3_clusters(paid, key=lambda m: math.log1p(m.cost.output))
    return {tier: rank_paid_models(c) for tier, c in zip(PAID_TIERS, clusters, strict=True)}


def _allocate_allowance(
    models: list[NormalizedModel],
    c: SelectionCriteria,
    free_ranked: list[NormalizedModel] | None = None,
    paid_ranked: list[NormalizedModel] | None = None,
) -> tuple[
    dict[str, str],
    dict[str, list[NormalizedModel]],
    list[NormalizedModel],
    list[NormalizedModel],
]:
    """Allowance allocation over already-filtered models."""
    skips = c.skip_models
    if free_ranked is None:
        free_ranked = rank_free_models(models, skip_models=skips)
    if paid_ranked is None:
        paid_ranked = rank_paid_models(models, skip_models=skips)
    selected, used = _seed_selection(models, c.preferred_tiers, free_ranked)

    eligible_paid = [m for m in paid_ranked if _is_unused(m, used)]
    clusters = _allowance_clusters(eligible_paid)
    _pick_paid_tiers(selected, used, clusters)

    # Fallback to any remaining unused paid models
    for tier in STANDARD_TIERS:
        if tier in selected:
            continue
        if tier == "free":
            free_pick = _pick_unused(free_ranked, used)
            selected["free"] = free_pick.full_id if free_pick else ""
        else:
            fallback = next((m for m in paid_ranked if _is_unused(m, used)), None)
            if fallback:
                _claim(selected, used, tier, fallback)
            else:
                selected[tier] = ""

    return selected, clusters, free_ranked, paid_ranked


def _evaluate(models: list[NormalizedModel], c: SelectionCriteria) -> list[NormalizedModel]:
    return evaluate_pareto(
        models,
        profile=c.profile,
        skip_models=c.skip_models,
        token_mix=c.token_mix,
        weights=c.weights,
        burn_basis=c.burn_basis,
    )


def _allocate_pareto(
    models: list[NormalizedModel], c: SelectionCriteria, evaluated: list[NormalizedModel]
) -> tuple[dict[str, str], dict[str, list[NormalizedModel]], list[NormalizedModel]]:
    """Pareto allocation over already-filtered, already-evaluated models.

    Tiers are allowed to share a model: each tier ranks the same Pareto frontier by its own
    objective, so one model can be the best pick for more than one tier. If there is no
    benchmarked/Pareto model at all, fall back to allowance allocation.
    """
    free_ranked = rank_free_models(evaluated, skip_models=c.skip_models)
    clusters = _pareto_clusters(evaluated, c)
    selected, used = _seed_selection(models, c.preferred_tiers, free_ranked)
    _pick_paid_tiers(selected, used, clusters)

    # No benchmarked/Pareto model at all: fall back to allowance allocation.
    if not any(selected.get(t) for t in PAID_TIERS):
        picks, _, _, _ = _allocate_allowance(
            models,
            replace(c, preferred_tiers={**c.preferred_tiers, **selected}),
            free_ranked=free_ranked,
        )
        for t in STANDARD_TIERS:
            selected.setdefault(t, picks.get(t, ""))
    for t in STANDARD_TIERS:
        selected.setdefault(t, "")
    return selected, clusters, free_ranked


def _prepare_models(
    models: list[NormalizedModel], c: SelectionCriteria, *, active_only: bool = False
) -> list[NormalizedModel]:
    """Apply price basis and selection filters, optionally restricting to active models."""
    filtered = _apply_filters(apply_price_basis(models, c.price_basis, c.at), c)
    return [m for m in filtered if m.status == "active"] if active_only else filtered


def select_tiers(models: list[NormalizedModel], c: SelectionCriteria) -> TierSelection:
    """Pick one model per tier plus up to ``c.top_n`` ordered candidates, evaluating once."""
    filtered = _prepare_models(models, c)
    if c.mode == "pareto":
        pool = _evaluate(filtered, c)
        selected, clusters, free_ranked = _allocate_pareto(filtered, c, pool)
        general_paid = None
    else:
        pool = filtered
        selected, clusters, free_ranked, general_paid = _allocate_allowance(filtered, c)
    primary = {k: selected.get(k, "") for k in STANDARD_TIERS}

    if c.top_n <= 1:
        return TierSelection(
            primary,
            {t: ([primary[t]] if primary[t] else []) for t in STANDARD_TIERS},
            pool,
            clusters,
        )

    if general_paid is None:
        frontier = [m for m in pool if m.is_pareto]
        general_paid = sorted(
            frontier,
            key=lambda m: _pareto_rank_key(m, include_speed=True),
        )

    def fill(tier: str, pools: list[list[NormalizedModel]]) -> list[str]:
        candidates = [primary[tier]] if primary[tier] else []
        for ranked in pools:
            for m in ranked:
                if len(candidates) >= c.top_n:
                    break
                if m.full_id not in candidates:
                    candidates.append(m.full_id)
        return candidates

    fallbacks = {"free": fill("free", [free_ranked])}
    for tier in PAID_TIERS:
        fallbacks[tier] = fill(tier, [clusters[tier], general_paid])
    return TierSelection(primary, {t: fallbacks[t] for t in STANDARD_TIERS}, pool, clusters)


def rank_catalog(models: list[NormalizedModel], c: SelectionCriteria) -> list[NormalizedModel]:
    """Produce a full ranked catalog of active models as copies carrying rank, tier and metrics."""
    active_models = _prepare_models(models, c, active_only=True)

    if c.mode == "pareto":
        evaluated = _evaluate(active_models, c)
        # Sort by BPE desc, effective_quality desc, composite_burn asc, tie_break_key
        ranked = sorted(evaluated, key=lambda m: _pareto_rank_key(m))
        tiers_map, _, _ = _allocate_pareto(evaluated, c, evaluated)
    else:
        tiers_map, _, free, paid = _allocate_allowance(active_models, c)
        seen = {m.full_id for m in paid} | {m.full_id for m in free}
        remaining = sorted(
            [m for m in active_models if m.full_id not in seen],
            key=lambda m: m.id,
        )
        ranked = paid + free + remaining
    tier_by_full_id: dict[str, str] = {}
    for tier_name, full_id in tiers_map.items():
        if full_id and full_id not in tier_by_full_id:
            tier_by_full_id[full_id] = tier_name

    return [
        m.model_copy(
            update={
                "rank": idx,
                "tier": tier_by_full_id.get(m.full_id),
                "effective_quality": (
                    m.effective_quality
                    if m.effective_quality is not None
                    else effective_quality(m, profile=c.profile, weights=c.weights)
                ),
            }
        )
        for idx, m in enumerate(ranked, start=1)
    ]
