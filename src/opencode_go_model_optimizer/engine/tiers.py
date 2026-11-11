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
    preferred = preferred_tiers or {}
    skips = set(skip_models or set())
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

    # 1. Rank free and paid models
    free_ranked = rank_free_models(models, skip_models=skips, allow_contributor=allow_contributor)
    paid_ranked = rank_paid_models(models, skip_models=skips)

    selected: dict[str, str] = {}
    used_model_ids: set[str] = set()

    # Check preferences first
    for tier in STANDARD_TIERS:
        pref = preferred.get(tier)
        if pref:
            hit = next(
                (m for m in models if (m.full_id == pref or m.id == pref) and m.status == "active"),
                None,
            )
            if hit:
                selected[tier] = hit.full_id
                used_model_ids.add(hit.id)
                used_model_ids.add(hit.full_id)

    # Free tier assignment
    if "free" not in selected:
        free_pick = next(
            (
                m
                for m in free_ranked
                if m.id not in used_model_ids and m.full_id not in used_model_ids
            ),
            None,
        )
        if free_pick:
            selected["free"] = free_pick.full_id
            used_model_ids.add(free_pick.id)
            used_model_ids.add(free_pick.full_id)
        elif free_ranked:
            selected["free"] = free_ranked[0].full_id

    # Cluster eligible paid models by output token cost into 3 natural tiers: heavy, balanced, light
    eligible_paid = [
        m for m in paid_ranked if m.id not in used_model_ids and m.full_id not in used_model_ids
    ]

    if eligible_paid:
        c_heavy, c_balanced, c_light = fisher_jenks_3_clusters(
            eligible_paid, key=lambda m: math.log1p(m.cost.output)
        )

        def pick_best(cluster: list[NormalizedModel]) -> NormalizedModel | None:
            cluster_ranked = rank_paid_models(cluster)
            for m in cluster_ranked:
                if m.id not in used_model_ids and m.full_id not in used_model_ids:
                    return m
            return cluster_ranked[0] if cluster_ranked else None

        if "heavy" not in selected and c_heavy:
            m = pick_best(c_heavy)
            if m:
                selected["heavy"] = m.full_id
                used_model_ids.add(m.id)
                used_model_ids.add(m.full_id)

        if "balanced" not in selected and c_balanced:
            m = pick_best(c_balanced)
            if m:
                selected["balanced"] = m.full_id
                used_model_ids.add(m.id)
                used_model_ids.add(m.full_id)

        if "light" not in selected and c_light:
            m = pick_best(c_light)
            if m:
                selected["light"] = m.full_id
                used_model_ids.add(m.id)
                used_model_ids.add(m.full_id)

    # Fallback to any remaining unused paid models
    for tier in STANDARD_TIERS:
        if tier not in selected:
            if tier == "free":
                free_pick = next(
                    (
                        m
                        for m in free_ranked
                        if m.id not in used_model_ids and m.full_id not in used_model_ids
                    ),
                    None,
                )
                selected["free"] = (
                    free_pick.full_id
                    if free_pick
                    else (free_ranked[0].full_id if free_ranked else "")
                )
            elif eligible_paid:
                fallback = next(
                    (
                        m
                        for m in paid_ranked
                        if m.id not in used_model_ids and m.full_id not in used_model_ids
                    ),
                    None,
                )
                if fallback:
                    selected[tier] = fallback.full_id
                    used_model_ids.add(fallback.id)
                    used_model_ids.add(fallback.full_id)
                else:
                    selected[tier] = ""
            else:
                selected[tier] = ""

    return selected


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
    preferred = preferred_tiers or {}
    skips = set(skip_models or set())
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

    # 1. Evaluate BPE on models
    evaluated = (
        models
        if already_evaluated
        else evaluate_pareto(
            models, profile=profile, skip_models=skips, token_mix=token_mix, weights=weights
        )
    )

    # Free tier
    free_ranked = rank_free_models(
        evaluated, skip_models=skips, allow_contributor=allow_contributor
    )

    # Paid benchmarked models
    benchmarked = [
        m
        for m in evaluated
        if m.status == "active"
        and (m.aa_iq is not None or m.aa_coding is not None)
        and not m.is_zero_cost
        and not m.is_contributor
        and not m.is_preview
        and m.id not in skips
        and m.full_id not in skips
    ]

    selected: dict[str, str] = {}
    used_model_ids: set[str] = set()

    # Preferences
    for tier in STANDARD_TIERS:
        pref = preferred.get(tier)
        if pref:
            hit = next(
                (m for m in models if (m.full_id == pref or m.id == pref) and m.status == "active"),
                None,
            )
            if hit:
                selected[tier] = hit.full_id
                used_model_ids.add(hit.id)
                used_model_ids.add(hit.full_id)

    if "free" not in selected and free_ranked:
        free_pick = next(
            (
                m
                for m in free_ranked
                if m.id not in used_model_ids and m.full_id not in used_model_ids
            ),
            None,
        )
        if free_pick:
            selected["free"] = free_pick.full_id
            used_model_ids.add(free_pick.id)
            used_model_ids.add(free_pick.full_id)
        elif free_ranked:
            selected["free"] = free_ranked[0].full_id

    if benchmarked:
        min_pareto_q = min(
            (
                effective_quality(m, profile=profile, weights=weights)
                for m in benchmarked
                if m.is_pareto
            ),
            default=0.0,
        )
        competitive = [
            m
            for m in benchmarked
            if (m.bpe or 0.0) >= 0.05
            and effective_quality(m, profile=profile, weights=weights) >= min_pareto_q * 0.8
        ]
        pool = competitive if len(competitive) >= 3 else benchmarked
        tier_heavy, tier_balanced, tier_light = fisher_jenks_3_clusters(
            pool, key=lambda m: effective_quality(m, profile=profile, weights=weights)
        )

        def pick_best(cluster: list[NormalizedModel]) -> NormalizedModel | None:
            ranked = sorted(
                cluster,
                key=lambda m: (
                    not bool(m.is_pareto),
                    -(m.bpe or 0.0),
                    m.composite_burn_pct if m.composite_burn_pct is not None else float("inf"),
                    -(m.speed_tps or 0.0),
                    tie_break_key(m),
                ),
            )
            for m in ranked:
                if m.id not in used_model_ids and m.full_id not in used_model_ids:
                    return m
            return ranked[0] if ranked else None

        if "heavy" not in selected and tier_heavy:
            m = pick_best(tier_heavy)
            if m:
                selected["heavy"] = m.full_id
                used_model_ids.add(m.id)
                used_model_ids.add(m.full_id)

        if "balanced" not in selected and tier_balanced:
            m = pick_best(tier_balanced)
            if m:
                selected["balanced"] = m.full_id
                used_model_ids.add(m.id)
                used_model_ids.add(m.full_id)

        if "light" not in selected and tier_light:
            m = pick_best(tier_light)
            if m:
                selected["light"] = m.full_id
                used_model_ids.add(m.id)
                used_model_ids.add(m.full_id)

    # Fallback to allowance allocation if any tier is missing
    if len(selected) < len(STANDARD_TIERS):
        allowance_picks = allocate_tiers_allowance(
            models,
            preferred_tiers={**preferred, **selected},
            skip_models=skips,
            allow_contributor=allow_contributor,
            min_context=min_context if not already_evaluated else 0,
            min_output=min_output if not already_evaluated else 0,
            max_output_cost=max_output_cost if not already_evaluated else None,
            profile=profile,
            max_burn=max_burn,
            token_mix=token_mix,
            weights=weights,
        )
        for t in STANDARD_TIERS:
            if t not in selected:
                selected[t] = allowance_picks.get(t, "")

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
    primary = allocate_tiers(
        models,
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
    )
    if top_n <= 1:
        return {tier: ([primary[tier]] if primary.get(tier) else []) for tier in STANDARD_TIERS}

    skips = set(skip_models or set())
    filtered_models = models
    if min_context > 0:
        filtered_models = [m for m in filtered_models if m.limit.context >= min_context]
    if min_output > 0:
        filtered_models = [m for m in filtered_models if m.limit.output >= min_output]
    if max_output_cost is not None:
        filtered_models = [
            m for m in filtered_models if m.is_zero_cost or m.cost.output <= max_output_cost
        ]
    if max_burn is not None:
        filtered_models = [
            m
            for m in filtered_models
            if compute_quota_burn_pct(m, profile=profile, token_mix=token_mix) <= max_burn
        ]

    fallbacks: dict[str, list[str]] = {}

    # Free tier candidates
    free_ranked = rank_free_models(
        filtered_models, skip_models=skips, allow_contributor=allow_contributor
    )
    free_candidates = [primary["free"]] if primary.get("free") else []
    for m in free_ranked:
        if len(free_candidates) >= top_n:
            break
        if m.full_id not in free_candidates:
            free_candidates.append(m.full_id)
    fallbacks["free"] = free_candidates

    # Paid tiers candidates
    general_paid = rank_paid_models(filtered_models, skip_models=skips)

    if mode == "pareto":
        evaluated = evaluate_pareto(
            filtered_models,
            profile=profile,
            skip_models=skips,
            token_mix=token_mix,
            weights=weights,
        )
        benchmarked = [
            m
            for m in evaluated
            if m.status == "active"
            and (m.aa_iq is not None or m.aa_coding is not None)
            and not m.is_zero_cost
            and not m.is_contributor
            and not m.is_preview
            and m.id not in skips
            and m.full_id not in skips
        ]
        if benchmarked:
            min_pareto_q = min(
                (
                    effective_quality(m, profile=profile, weights=weights)
                    for m in benchmarked
                    if m.is_pareto
                ),
                default=0.0,
            )
            competitive = [
                m
                for m in benchmarked
                if (m.bpe or 0.0) >= 0.05
                and effective_quality(m, profile=profile, weights=weights) >= min_pareto_q * 0.8
            ]
            pool = competitive if len(competitive) >= 3 else benchmarked
            tier_heavy, tier_balanced, tier_light = fisher_jenks_3_clusters(
                pool, key=lambda m: effective_quality(m, profile=profile, weights=weights)
            )

            def sort_cluster_pareto(cluster: list[NormalizedModel]) -> list[NormalizedModel]:
                return sorted(
                    cluster,
                    key=lambda m: (
                        not bool(m.is_pareto),
                        -(m.bpe or 0.0),
                        m.composite_burn_pct if m.composite_burn_pct is not None else float("inf"),
                        -(m.speed_tps or 0.0),
                        tie_break_key(m),
                    ),
                )

            paid_clusters = {
                "heavy": sort_cluster_pareto(tier_heavy),
                "balanced": sort_cluster_pareto(tier_balanced),
                "light": sort_cluster_pareto(tier_light),
            }
        else:
            paid_clusters = {"heavy": [], "balanced": [], "light": []}
    else:
        if general_paid:
            c_heavy, c_balanced, c_light = fisher_jenks_3_clusters(
                general_paid, key=lambda m: math.log1p(m.cost.output)
            )
            paid_clusters = {
                "heavy": rank_paid_models(c_heavy),
                "balanced": rank_paid_models(c_balanced),
                "light": rank_paid_models(c_light),
            }
        else:
            paid_clusters = {"heavy": [], "balanced": [], "light": []}

    for tier in ("heavy", "balanced", "light"):
        candidates = [primary[tier]] if primary.get(tier) else []
        for m in paid_clusters.get(tier, []):
            if len(candidates) >= top_n:
                break
            if m.full_id not in candidates:
                candidates.append(m.full_id)
        if len(candidates) < top_n:
            for m in general_paid:
                if len(candidates) >= top_n:
                    break
                if m.full_id not in candidates:
                    candidates.append(m.full_id)
        fallbacks[tier] = candidates

    return {tier: fallbacks.get(tier, []) for tier in STANDARD_TIERS}
