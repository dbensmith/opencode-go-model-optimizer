"""Tier allocator mapping models to industry-standard capability tiers."""

from __future__ import annotations

from opencode_go_model_optimizer.engine.allowance import rank_free_models, rank_paid_models
from opencode_go_model_optimizer.engine.pareto import evaluate_pareto, fisher_jenks_3_clusters
from opencode_go_model_optimizer.ingest.models import NormalizedModel

STANDARD_TIERS = ["heavy", "balanced", "light", "free"]


def allocate_tiers_allowance(
    models: list[NormalizedModel],
    preferred_tiers: dict[str, str] | None = None,
    skip_models: set[str] | None = None,
) -> dict[str, str]:
    """Allocate models to tiers using allowance heuristic and natural cost clustering."""
    preferred = preferred_tiers or {}
    skips = set(skip_models or set())

    # 1. Rank free and paid models
    free_ranked = rank_free_models(models, skip_models=skips)
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
            eligible_paid, key=lambda m: m.cost.output
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
) -> dict[str, str]:
    """Allocate models to tiers using Pareto efficiency and Fisher-Jenks natural breaks."""
    preferred = preferred_tiers or {}
    skips = set(skip_models or set())

    # 1. Evaluate BPE on models
    evaluated = evaluate_pareto(models)

    # Free tier
    free_ranked = rank_free_models(evaluated, skip_models=skips)

    # Paid benchmarked models
    benchmarked = [
        m
        for m in evaluated
        if m.status == "active"
        and m.aa_iq is not None
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
        frontier = [m for m in benchmarked if m.is_pareto]
        if len(frontier) >= 3:
            f_heavy, f_balanced, f_light = fisher_jenks_3_clusters(
                frontier, key=lambda m: m.aa_iq or 0.0
            )
            if f_heavy and f_balanced and f_light:
                heavy_cutoff = (
                    min((m.aa_iq or 0.0) for m in f_heavy)
                    + max((m.aa_iq or 0.0) for m in f_balanced)
                ) / 2.0
                balanced_cutoff = (
                    min((m.aa_iq or 0.0) for m in f_balanced)
                    + max((m.aa_iq or 0.0) for m in f_light)
                ) / 2.0

                tier_heavy = [m for m in benchmarked if (m.aa_iq or 0.0) >= heavy_cutoff]
                tier_balanced = [
                    m for m in benchmarked if heavy_cutoff > (m.aa_iq or 0.0) >= balanced_cutoff
                ]
                tier_light = [m for m in benchmarked if (m.aa_iq or 0.0) < balanced_cutoff]
            else:
                tier_heavy, tier_balanced, tier_light = fisher_jenks_3_clusters(
                    benchmarked, key=lambda m: m.aa_iq or 0.0
                )
        else:
            tier_heavy, tier_balanced, tier_light = fisher_jenks_3_clusters(
                benchmarked, key=lambda m: m.aa_iq or 0.0
            )

        def pick_best(cluster: list[NormalizedModel]) -> NormalizedModel | None:
            if not cluster:
                return None
            ranked = sorted(
                cluster,
                key=lambda m: (
                    m.bpe or 0.0,
                    -(m.composite_burn_pct or 0.0),
                    m.release_date or "",
                ),
                reverse=True,
            )
            for m in ranked:
                if m.id not in used_model_ids and m.full_id not in used_model_ids:
                    return m
            return ranked[0]

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
            models, preferred_tiers=preferred, skip_models=skips
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
) -> dict[str, str]:
    """Unified entrypoint to allocate models into capability tiers."""
    if mode == "pareto":
        res = allocate_tiers_pareto(
            models, preferred_tiers=preferred_tiers, skip_models=skip_models
        )
    else:
        res = allocate_tiers_allowance(
            models, preferred_tiers=preferred_tiers, skip_models=skip_models
        )
    return {k: res.get(k, "") for k in STANDARD_TIERS}


def rank_catalog(
    models: list[NormalizedModel],
    mode: str = "allowance",
    skip_models: set[str] | None = None,
) -> list[NormalizedModel]:
    """Produce a full ranked catalog of active models."""
    skips = set(skip_models or set())
    active_models = [
        m for m in models if m.status == "active" and m.id not in skips and m.full_id not in skips
    ]

    if mode == "pareto":
        evaluated = evaluate_pareto(active_models)
        # Sort by BPE desc, aa_iq desc, composite_burn asc
        ranked = sorted(
            evaluated,
            key=lambda m: (
                m.bpe or 0.0,
                m.aa_iq or 0.0,
                -(m.composite_burn_pct or 0.0),
            ),
            reverse=True,
        )
    else:
        paid = rank_paid_models(active_models, skip_models=skips)
        free = rank_free_models(active_models, skip_models=skips)
        ranked = paid + free

    # Assign rank index (1-based)
    for idx, m in enumerate(ranked, start=1):
        m.rank = idx

    return ranked
