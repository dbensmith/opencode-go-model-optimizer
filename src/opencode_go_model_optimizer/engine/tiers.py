"""Tier allocator mapping models to industry-standard capability tiers."""

from __future__ import annotations

from opencode_go_model_optimizer.engine.allowance import rank_free_models, rank_paid_models
from opencode_go_model_optimizer.engine.pareto import evaluate_pareto, fisher_jenks_3_clusters
from opencode_go_model_optimizer.ingest.models import NormalizedModel

STANDARD_TIERS = ["heavy", "balanced", "light", "free"]


def is_heavy_candidate(m: NormalizedModel) -> bool:
    """Identify whether a model qualifies for the heavy reasoning tier."""
    id_lower = m.id.lower()
    name_lower = m.name.lower()

    # Explicit heavy / max / pro reasoning indicators
    if (
        any(
            k in id_lower or k in name_lower
            for k in ["max", "pro", "ultra", "argon", "grok", "kimi-k3"]
        )
        and "flash" not in id_lower
        and "lite" not in id_lower
    ):
        return True

    # Higher pricing profile (output cost >= $2.00 or input cost >= $1.00)
    return bool(m.cost.output >= 2.0 or m.cost.input >= 1.0)


def is_light_candidate(m: NormalizedModel) -> bool:
    """Identify whether a model qualifies for the light/high-throughput tier."""
    id_lower = m.id.lower()
    name_lower = m.name.lower()

    if any(
        k in id_lower or k in name_lower
        for k in ["flash", "mini", "haiku", "luna", "lite", "small"]
    ):
        return True

    # Low token price profile (output <= $0.60 and input <= $0.20)
    return bool(m.cost.output <= 0.60 and m.cost.input <= 0.20)


def allocate_tiers_allowance(
    models: list[NormalizedModel],
    preferred_tiers: dict[str, str] | None = None,
    skip_models: set[str] | None = None,
) -> dict[str, str]:
    """Allocate models to tiers using allowance heuristic."""
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
            # Check if preferred model is in models and active
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

    # Heavy tier assignment
    if "heavy" not in selected:
        heavy_pick = next(
            (
                m
                for m in paid_ranked
                if is_heavy_candidate(m)
                and m.id not in used_model_ids
                and m.full_id not in used_model_ids
            ),
            None,
        )
        if heavy_pick:
            selected["heavy"] = heavy_pick.full_id
            used_model_ids.add(heavy_pick.id)
            used_model_ids.add(heavy_pick.full_id)

    # Balanced tier assignment (daily driver: highest available allowance / workhorse)
    if "balanced" not in selected:
        balanced_pick = next(
            (
                m
                for m in paid_ranked
                if m.id not in used_model_ids and m.full_id not in used_model_ids
            ),
            None,
        )
        if balanced_pick:
            selected["balanced"] = balanced_pick.full_id
            used_model_ids.add(balanced_pick.id)
            used_model_ids.add(balanced_pick.full_id)

    # Light tier assignment (lightweight, high throughput)
    if "light" not in selected:
        light_pick = next(
            (
                m
                for m in paid_ranked
                if is_light_candidate(m)
                and m.id not in used_model_ids
                and m.full_id not in used_model_ids
            ),
            None,
        )
        if light_pick:
            selected["light"] = light_pick.full_id
            used_model_ids.add(light_pick.id)
            used_model_ids.add(light_pick.full_id)
        else:
            # Fallback to next unused paid model
            fallback = next(
                (
                    m
                    for m in paid_ranked
                    if m.id not in used_model_ids and m.full_id not in used_model_ids
                ),
                None,
            )
            if fallback:
                selected["light"] = fallback.full_id
                used_model_ids.add(fallback.id)
                used_model_ids.add(fallback.full_id)

    # Ensure all 4 tiers exist, even if fallback needed
    for tier in STANDARD_TIERS:
        if tier not in selected:
            if tier == "free" and free_ranked:
                selected["free"] = free_ranked[0].full_id
            elif paid_ranked:
                selected[tier] = paid_ranked[0].full_id
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
        selected["free"] = free_ranked[0].full_id
        used_model_ids.add(free_ranked[0].id)
        used_model_ids.add(free_ranked[0].full_id)

    if benchmarked:
        f_heavy, f_balanced, f_light = fisher_jenks_3_clusters(benchmarked)

        def pick_best(cluster: list[NormalizedModel]) -> NormalizedModel | None:
            if not cluster:
                return None
            # Sort by BPE desc, burn asc, release_date desc
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

        if "heavy" not in selected and f_heavy:
            m = pick_best(f_heavy)
            if m:
                selected["heavy"] = m.full_id
                used_model_ids.add(m.id)
                used_model_ids.add(m.full_id)

        if "balanced" not in selected and f_balanced:
            m = pick_best(f_balanced)
            if m:
                selected["balanced"] = m.full_id
                used_model_ids.add(m.id)
                used_model_ids.add(m.full_id)

        if "light" not in selected and f_light:
            m = pick_best(f_light)
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
