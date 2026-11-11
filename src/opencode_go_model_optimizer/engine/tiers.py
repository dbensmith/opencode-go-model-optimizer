"""Tier allocator mapping models to industry-standard capability tiers."""

from __future__ import annotations

import math

from opencode_go_model_optimizer.engine.allowance import (
    rank_free_models,
    rank_paid_models,
    tie_break_key,
)
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


def _apply_filters(
    models: list[NormalizedModel],
    min_context: int = 0,
    min_output: int = 0,
    max_output_cost: float | None = None,
    max_burn: float | None = None,
    profile: str = "general",
    token_mix: tuple[int, int, int] | None = None,
) -> list[NormalizedModel]:
    """Apply context, output, output-cost, and quota-burn constraints."""
    if min_context > 0:
        models = [m for m in models if m.limit.context >= min_context]
    if min_output > 0:
        models = [m for m in models if m.limit.output >= min_output]
    if max_output_cost is not None:
        models = [m for m in models if m.is_zero_cost or m.cost.output <= max_output_cost]
    if max_burn is not None:
        models = [
            m
            for m in models
            if compute_quota_burn_pct(m, profile=profile, token_mix=token_mix) <= max_burn
        ]
    return models


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


def _benchmarked(models: list[NormalizedModel], skips: set[str]) -> list[NormalizedModel]:
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
    evaluated: list[NormalizedModel],
    skips: set[str],
    profile: str,
    weights: tuple[float, float] | None,
) -> dict[str, list[NormalizedModel]]:
    """Cluster the competitive benchmarked pool by quality into ranked per-tier lists."""
    empty: dict[str, list[NormalizedModel]] = {t: [] for t in _PAID_TIERS}
    benchmarked = _benchmarked(evaluated, skips)
    if not benchmarked:
        return empty

    def quality(m: NormalizedModel) -> float:
        return effective_quality(m, profile=profile, weights=weights)

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
        for tier, cluster in zip(_PAID_TIERS, fisher_jenks_3_clusters(pool, key=quality))
    }


def _allowance_clusters(paid: list[NormalizedModel]) -> dict[str, list[NormalizedModel]]:
    """Cluster paid models by output cost into ranked per-tier lists."""
    if not paid:
        return {t: [] for t in _PAID_TIERS}
    clusters = fisher_jenks_3_clusters(paid, key=lambda m: math.log1p(m.cost.output))
    return {tier: rank_paid_models(c) for tier, c in zip(_PAID_TIERS, clusters)}


def _allocate_allowance(
    models: list[NormalizedModel],
    preferred: dict[str, str],
    skips: set[str],
    allow_contributor: bool,
) -> dict[str, str]:
    """Allowance allocation over already-filtered models."""
    free_ranked = rank_free_models(models, skip_models=skips, allow_contributor=allow_contributor)
    paid_ranked = rank_paid_models(models, skip_models=skips)
    selected, used = _seed_selection(models, preferred, free_ranked)

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


def _allocate_pareto(
    models: list[NormalizedModel],
    preferred: dict[str, str],
    skips: set[str],
    allow_contributor: bool,
    profile: str,
    already_evaluated: bool,
    token_mix: tuple[int, int, int] | None,
    weights: tuple[float, float] | None,
) -> tuple[dict[str, str], dict[str, list[NormalizedModel]]]:
    """Pareto allocation over already-filtered models; also returns the ranked clusters."""
    evaluated = (
        models
        if already_evaluated
        else evaluate_pareto(
            models, profile=profile, skip_models=skips, token_mix=token_mix, weights=weights
        )
    )
    free_ranked = rank_free_models(
        evaluated, skip_models=skips, allow_contributor=allow_contributor
    )
    clusters = _pareto_clusters(evaluated, skips, profile, weights)
    selected, used = _seed_selection(models, preferred, free_ranked)
    _pick_paid_tiers(selected, used, clusters)

    # Fall back to allowance allocation if any tier is missing
    if len(selected) < len(STANDARD_TIERS):
        picks = _allocate_allowance(models, {**preferred, **selected}, skips, allow_contributor)
        for t in STANDARD_TIERS:
            selected.setdefault(t, picks.get(t, ""))
    return selected, clusters


def allocate_tiers_allowance(
    models: list[NormalizedModel],
    preferred_tiers: dict[str, str] | None = None,
    skip_models: set[str] | None = None,
    allow_contributor: bool = True,
    min_context: int = 0,
    min_output: int = 0,
    max_output_cost: float | None = None,
    profile: str = "general",
    max_burn: float | None = None,
    token_mix: tuple[int, int, int] | None = None,
    weights: tuple[float, float] | None = None,
) -> dict[str, str]:
    """Allocate models to tiers using allowance heuristic and natural cost clustering."""
    models = _apply_filters(
        models, min_context, min_output, max_output_cost, max_burn, profile, token_mix
    )
    return _allocate_allowance(
        models, preferred_tiers or {}, set(skip_models or set()), allow_contributor
    )


def allocate_tiers_pareto(
    models: list[NormalizedModel],
    preferred_tiers: dict[str, str] | None = None,
    skip_models: set[str] | None = None,
    allow_contributor: bool = True,
    min_context: int = 0,
    min_output: int = 0,
    max_output_cost: float | None = None,
    profile: str = "general",
    already_evaluated: bool = False,
    max_burn: float | None = None,
    token_mix: tuple[int, int, int] | None = None,
    weights: tuple[float, float] | None = None,
) -> dict[str, str]:
    """Allocate models to tiers using Pareto efficiency and Fisher-Jenks natural breaks."""
    models = _apply_filters(
        models, min_context, min_output, max_output_cost, max_burn, profile, token_mix
    )
    selected, _ = _allocate_pareto(
        models,
        preferred_tiers or {},
        set(skip_models or set()),
        allow_contributor,
        profile,
        already_evaluated,
        token_mix,
        weights,
    )
    return selected


def allocate_tiers(
    models: list[NormalizedModel],
    mode: str = "allowance",
    preferred_tiers: dict[str, str] | None = None,
    skip_models: set[str] | None = None,
    allow_contributor: bool = True,
    min_context: int = 0,
    min_output: int = 0,
    max_output_cost: float | None = None,
    profile: str = "general",
    already_evaluated: bool = False,
    max_burn: float | None = None,
    token_mix: tuple[int, int, int] | None = None,
    weights: tuple[float, float] | None = None,
) -> dict[str, str]:
    """Unified entrypoint to allocate models into capability tiers."""
    if mode == "pareto":
        res = allocate_tiers_pareto(
            models,
            preferred_tiers=preferred_tiers,
            skip_models=skip_models,
            allow_contributor=allow_contributor,
            min_context=min_context,
            min_output=min_output,
            max_output_cost=max_output_cost,
            profile=profile,
            already_evaluated=already_evaluated,
            max_burn=max_burn,
            token_mix=token_mix,
            weights=weights,
        )
    else:
        res = allocate_tiers_allowance(
            models,
            preferred_tiers=preferred_tiers,
            skip_models=skip_models,
            allow_contributor=allow_contributor,
            min_context=min_context,
            min_output=min_output,
            max_output_cost=max_output_cost,
            profile=profile,
            max_burn=max_burn,
            token_mix=token_mix,
            weights=weights,
        )
    return {k: res.get(k, "") for k in STANDARD_TIERS}


def rank_catalog(
    models: list[NormalizedModel],
    mode: str = "allowance",
    skip_models: set[str] | None = None,
    allow_contributor: bool = True,
    min_context: int = 0,
    min_output: int = 0,
    max_output_cost: float | None = None,
    profile: str = "general",
    max_burn: float | None = None,
    token_mix: tuple[int, int, int] | None = None,
    weights: tuple[float, float] | None = None,
) -> list[NormalizedModel]:
    """Produce a full ranked catalog of active models."""
    skips = set(skip_models or set())
    models = _apply_filters(
        models, min_context, min_output, max_output_cost, max_burn, profile, token_mix
    )

    active_models = [
        m for m in models if m.status == "active" and m.id not in skips and m.full_id not in skips
    ]

    if mode == "pareto":
        evaluated = evaluate_pareto(
            active_models, profile=profile, skip_models=skips, token_mix=token_mix, weights=weights
        )
        # Sort by BPE desc, effective_quality desc, composite_burn asc, tie_break_key
        ranked = sorted(
            evaluated,
            key=lambda m: (
                -(m.bpe or 0.0),
                -effective_quality(m, profile=profile, weights=weights),
                m.composite_burn_pct if m.composite_burn_pct is not None else float("inf"),
                tie_break_key(m),
            ),
        )
    else:
        paid = rank_paid_models(active_models, skip_models=skips)
        free = rank_free_models(
            active_models, skip_models=skips, allow_contributor=allow_contributor
        )
        seen = {m.full_id for m in paid} | {m.full_id for m in free}
        remaining = sorted(
            [m for m in active_models if m.full_id not in seen],
            key=lambda m: m.id,
        )
        ranked = paid + free + remaining

    tiers_map = allocate_tiers(
        active_models,
        mode=mode,
        skip_models=skips,
        allow_contributor=allow_contributor,
        min_context=0,
        min_output=0,
        max_output_cost=None,
        profile=profile,
        already_evaluated=(mode == "pareto"),
        max_burn=max_burn,
        token_mix=token_mix,
        weights=weights,
    )
    tier_by_full_id = {full_id: tier_name for tier_name, full_id in tiers_map.items() if full_id}

    # Assign rank index (1-based) and tier
    for idx, m in enumerate(ranked, start=1):
        m.rank = idx
        m.tier = tier_by_full_id.get(m.full_id)

    return ranked


def allocate_tiers_with_fallbacks(
    models: list[NormalizedModel],
    top_n: int = 1,
    mode: str = "allowance",
    preferred_tiers: dict[str, str] | None = None,
    skip_models: set[str] | None = None,
    allow_contributor: bool = True,
    min_context: int = 0,
    min_output: int = 0,
    max_output_cost: float | None = None,
    profile: str = "general",
    max_burn: float | None = None,
    token_mix: tuple[int, int, int] | None = None,
    weights: tuple[float, float] | None = None,
) -> tuple[dict[str, str], dict[str, list[str]]]:
    """Return primary tier picks plus ordered per-tier candidate lists in a single evaluation."""
    preferred = preferred_tiers or {}
    skips = set(skip_models or set())
    filtered = _apply_filters(
        models, min_context, min_output, max_output_cost, max_burn, profile, token_mix
    )
    clusters: dict[str, list[NormalizedModel]] = {t: [] for t in _PAID_TIERS}
    if mode == "pareto":
        selected, clusters = _allocate_pareto(
            filtered, preferred, skips, allow_contributor, profile, False, token_mix, weights
        )
    else:
        selected = _allocate_allowance(filtered, preferred, skips, allow_contributor)
    primary = {k: selected.get(k, "") for k in STANDARD_TIERS}

    if top_n <= 1:
        return primary, {t: ([primary[t]] if primary[t] else []) for t in STANDARD_TIERS}

    general_paid = rank_paid_models(filtered, skip_models=skips)
    if mode != "pareto":
        clusters = _allowance_clusters(general_paid)

    def fill(tier: str, pools: list[list[NormalizedModel]]) -> list[str]:
        candidates = [primary[tier]] if primary[tier] else []
        for pool in pools:
            for m in pool:
                if len(candidates) >= top_n:
                    break
                if m.full_id not in candidates:
                    candidates.append(m.full_id)
        return candidates

    free_ranked = rank_free_models(filtered, skip_models=skips, allow_contributor=allow_contributor)
    fallbacks = {"free": fill("free", [free_ranked])}
    for tier in _PAID_TIERS:
        fallbacks[tier] = fill(tier, [clusters[tier], general_paid])
    return primary, {t: fallbacks[t] for t in STANDARD_TIERS}


def allocate_tier_fallbacks(
    models: list[NormalizedModel],
    top_n: int = 1,
    mode: str = "allowance",
    preferred_tiers: dict[str, str] | None = None,
    skip_models: set[str] | None = None,
    allow_contributor: bool = True,
    min_context: int = 0,
    min_output: int = 0,
    max_output_cost: float | None = None,
    profile: str = "general",
    max_burn: float | None = None,
    token_mix: tuple[int, int, int] | None = None,
    weights: tuple[float, float] | None = None,
) -> dict[str, list[str]]:
    """Allocate ordered per-tier candidate lists (primary + fallbacks) up to top_n models."""
    return allocate_tiers_with_fallbacks(
        models,
        top_n=top_n,
        mode=mode,
        preferred_tiers=preferred_tiers,
        skip_models=skip_models,
        allow_contributor=allow_contributor,
        min_context=min_context,
        min_output=min_output,
        max_output_cost=max_output_cost,
        profile=profile,
        max_burn=max_burn,
        token_mix=token_mix,
        weights=weights,
    )[1]
