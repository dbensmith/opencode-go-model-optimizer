"""Tier allocator mapping models to industry-standard capability tiers."""

import math
from dataclasses import dataclass, replace

from opencode_go_model_optimizer.engine.allowance import (
    rank_free_models,
    rank_paid_models,
    tie_break_key,
)
from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.pareto import (
    compute_quota_burn_pct,
    effective_quality,
    evaluate_pareto,
    fisher_jenks_3_clusters,
)
from opencode_go_model_optimizer.ingest.models import NormalizedModel

STANDARD_TIERS = ["heavy", "balanced", "light", "free"]
_PAID_TIERS = ("heavy", "balanced", "light")


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
    """Apply context, output, output-cost, and quota-burn constraints."""
    if c.min_context > 0:
        models = [m for m in models if m.limit.context >= c.min_context]
    if c.min_output > 0:
        models = [m for m in models if m.limit.output >= c.min_output]
    if c.max_output_cost is not None:
        models = [m for m in models if m.is_zero_cost or m.cost.output <= c.max_output_cost]
    if c.max_burn is not None:
        models = [
            m
            for m in models
            if compute_quota_burn_pct(m, profile=c.profile, token_mix=c.token_mix) <= c.max_burn
        ]
    return models


@dataclass(frozen=True)
class TierSelection:
    """Primary pick and ordered candidates per tier, plus the models the picks came from.

    ``models`` is the filtered pool the selection ran on; in pareto mode these are evaluated
    copies carrying ``bpe``/``is_pareto``/``composite_burn_pct``.
    """

    primary: dict[str, str]
    candidates: dict[str, list[str]]
    models: list[NormalizedModel]


def _is_unused(m: NormalizedModel, used: set[str]) -> bool:
    return m.id not in used and m.full_id not in used


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
                (m for m in models if (m.full_id == pref or m.id == pref) and m.status == "active"),
                None,
            )
            if hit:
                _claim(selected, used, tier, hit)
    if "free" not in selected:
        free_pick = next((m for m in free_ranked if _is_unused(m, used)), None)
        if free_pick:
            _claim(selected, used, "free", free_pick)
        elif free_ranked:
            selected["free"] = free_ranked[0].full_id
    return selected, used


def _pick_paid_tiers(
    selected: dict[str, str], used: set[str], clusters: dict[str, list[NormalizedModel]]
) -> None:
    for tier in _PAID_TIERS:
        if tier not in selected and clusters[tier]:
            m = _pick_unused(clusters[tier], used)
            if m:
                _claim(selected, used, tier, m)


def _benchmarked(models: list[NormalizedModel], skips: frozenset[str]) -> list[NormalizedModel]:
    """Active, benchmarked, non-free, non-preview models eligible for Pareto clustering."""
    return [
        m
        for m in models
        if m.status == "active"
        and (m.aa_iq is not None or m.aa_coding is not None)
        and not m.is_zero_cost
        and not m.is_contributor
        and not m.is_preview
        and m.id not in skips
        and m.full_id not in skips
    ]


def _pareto_clusters(
    evaluated: list[NormalizedModel], c: SelectionCriteria
) -> dict[str, list[NormalizedModel]]:
    """Cluster the competitive benchmarked pool by quality into ranked per-tier lists."""
    empty: dict[str, list[NormalizedModel]] = {t: [] for t in _PAID_TIERS}
    benchmarked = _benchmarked(evaluated, c.skip_models)
    if not benchmarked:
        return empty

    def quality(m: NormalizedModel) -> float:
        return effective_quality(m, profile=c.profile, weights=c.weights)

    min_pareto_q = min((quality(m) for m in benchmarked if m.is_pareto), default=0.0)
    competitive = [
        m for m in benchmarked if (m.bpe or 0.0) >= 0.05 and quality(m) >= min_pareto_q * 0.8
    ]
    pool = competitive if len(competitive) >= 3 else benchmarked
    return {
        tier: sorted(
            cluster,
            key=lambda m: (
                not bool(m.is_pareto),
                -(m.bpe or 0.0),
                m.composite_burn_pct if m.composite_burn_pct is not None else float("inf"),
                -(m.speed_tps or 0.0),
                tie_break_key(m),
            ),
        )
        for tier, cluster in zip(
            _PAID_TIERS, fisher_jenks_3_clusters(pool, key=quality), strict=True
        )
    }


def _allowance_clusters(paid: list[NormalizedModel]) -> dict[str, list[NormalizedModel]]:
    """Cluster paid models by output cost into ranked per-tier lists."""
    if not paid:
        return {t: [] for t in _PAID_TIERS}
    clusters = fisher_jenks_3_clusters(paid, key=lambda m: math.log1p(m.cost.output))
    return {tier: rank_paid_models(c) for tier, c in zip(_PAID_TIERS, clusters, strict=True)}


def _allocate_allowance(models: list[NormalizedModel], c: SelectionCriteria) -> dict[str, str]:
    """Allowance allocation over already-filtered models."""
    skips = c.skip_models
    free_ranked = rank_free_models(models, skip_models=skips, allow_contributor=c.allow_contributor)
    paid_ranked = rank_paid_models(models, skip_models=skips)
    selected, used = _seed_selection(models, c.preferred_tiers, free_ranked)

    eligible_paid = [m for m in paid_ranked if _is_unused(m, used)]
    _pick_paid_tiers(selected, used, _allowance_clusters(eligible_paid))

    # Fallback to any remaining unused paid models
    for tier in STANDARD_TIERS:
        if tier in selected:
            continue
        if tier == "free":
            free_pick = _pick_unused(free_ranked, used)
            selected["free"] = free_pick.full_id if free_pick else ""
        else:
            fallback = next((m for m in paid_ranked if _is_unused(m, used)), None)
            if eligible_paid and fallback:
                _claim(selected, used, tier, fallback)
            else:
                selected[tier] = ""
    return selected


def _evaluate(models: list[NormalizedModel], c: SelectionCriteria) -> list[NormalizedModel]:
    return evaluate_pareto(
        models,
        profile=c.profile,
        skip_models=c.skip_models,
        token_mix=c.token_mix,
        weights=c.weights,
    )


def _allocate_pareto(
    models: list[NormalizedModel], c: SelectionCriteria, evaluated: list[NormalizedModel]
) -> tuple[dict[str, str], dict[str, list[NormalizedModel]]]:
    """Pareto allocation over already-filtered, already-evaluated models."""
    free_ranked = rank_free_models(
        evaluated, skip_models=c.skip_models, allow_contributor=c.allow_contributor
    )
    clusters = _pareto_clusters(evaluated, c)
    selected, used = _seed_selection(models, c.preferred_tiers, free_ranked)
    _pick_paid_tiers(selected, used, clusters)

    # Fall back to allowance allocation if any tier is missing
    if len(selected) < len(STANDARD_TIERS):
        picks = _allocate_allowance(
            models, replace(c, preferred_tiers={**c.preferred_tiers, **selected})
        )
        for t in STANDARD_TIERS:
            selected.setdefault(t, picks.get(t, ""))
    return selected, clusters


def select_tiers(models: list[NormalizedModel], c: SelectionCriteria) -> TierSelection:
    """Pick one model per tier plus up to ``c.top_n`` ordered candidates, evaluating once."""
    filtered = _apply_filters(models, c)
    skips = c.skip_models
    clusters: dict[str, list[NormalizedModel]] = {t: [] for t in _PAID_TIERS}
    if c.mode == "pareto":
        pool = _evaluate(filtered, c)
        selected, clusters = _allocate_pareto(filtered, c, pool)
    else:
        pool = filtered
        selected = _allocate_allowance(filtered, c)
    primary = {k: selected.get(k, "") for k in STANDARD_TIERS}

    if c.top_n <= 1:
        return TierSelection(
            primary, {t: ([primary[t]] if primary[t] else []) for t in STANDARD_TIERS}, pool
        )

    general_paid = rank_paid_models(filtered, skip_models=skips)
    if c.mode != "pareto":
        clusters = _allowance_clusters(general_paid)

    def fill(tier: str, pools: list[list[NormalizedModel]]) -> list[str]:
        candidates = [primary[tier]] if primary[tier] else []
        for ranked in pools:
            for m in ranked:
                if len(candidates) >= c.top_n:
                    break
                if m.full_id not in candidates:
                    candidates.append(m.full_id)
        return candidates

    free_ranked = rank_free_models(
        filtered, skip_models=skips, allow_contributor=c.allow_contributor
    )
    fallbacks = {"free": fill("free", [free_ranked])}
    for tier in _PAID_TIERS:
        fallbacks[tier] = fill(tier, [clusters[tier], general_paid])
    return TierSelection(primary, {t: fallbacks[t] for t in STANDARD_TIERS}, pool)


def rank_catalog(models: list[NormalizedModel], c: SelectionCriteria) -> list[NormalizedModel]:
    """Produce a full ranked catalog of active models as copies carrying rank, tier and metrics."""
    skips = c.skip_models
    active_models = [
        m
        for m in _apply_filters(models, c)
        if m.status == "active" and m.id not in skips and m.full_id not in skips
    ]

    if c.mode == "pareto":
        evaluated = _evaluate(active_models, c)
        # Sort by BPE desc, effective_quality desc, composite_burn asc, tie_break_key
        ranked = sorted(
            evaluated,
            key=lambda m: (
                -(m.bpe or 0.0),
                -effective_quality(m, profile=c.profile, weights=c.weights),
                m.composite_burn_pct if m.composite_burn_pct is not None else float("inf"),
                tie_break_key(m),
            ),
        )
        tiers_map, _ = _allocate_pareto(evaluated, c, evaluated)
    else:
        paid = rank_paid_models(active_models, skip_models=skips)
        free = rank_free_models(
            active_models, skip_models=skips, allow_contributor=c.allow_contributor
        )
        seen = {m.full_id for m in paid} | {m.full_id for m in free}
        remaining = sorted(
            [m for m in active_models if m.full_id not in seen],
            key=lambda m: m.id,
        )
        ranked = paid + free + remaining
        tiers_map = _allocate_allowance(active_models, c)
    tier_by_full_id = {full_id: tier_name for tier_name, full_id in tiers_map.items() if full_id}

    return [
        m.model_copy(update={"rank": idx, "tier": tier_by_full_id.get(m.full_id)})
        for idx, m in enumerate(ranked, start=1)
    ]
