"""Formatters for tier selection outputs."""

import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from opencode_go_model_optimizer.engine.criteria import SelectionCriteria
from opencode_go_model_optimizer.engine.pareto import (
    compute_burn_pct,
    compute_turn_cost,
    resolve_burn_basis,
)
from opencode_go_model_optimizer.engine.tiers import STANDARD_TIERS
from opencode_go_model_optimizer.formatters.markdown import format_multiplier
from opencode_go_model_optimizer.formatters.table import (
    _burn_basis,
    _model_map,
    _model_value_cells,
    _next_change,
    format_method_banner,
    render_table,
)
from opencode_go_model_optimizer.ingest.models import NormalizedModel
from opencode_go_model_optimizer.numbers import json_allowance, json_count, json_multiplier


def format_select_json(
    tiers: dict[str, str],
    mode: str,
    plan: str = "go",
    evaluated_at: str | None = None,
    explanations: dict[str, dict[str, Any]] | None = None,
    profile: str | None = None,
    fallbacks: dict[str, list[str]] | None = None,
    snapshot_info: dict[str, Any] | None = None,
    settings: dict[str, Any] | None = None,
    names: dict[str, str] | None = None,
    *,
    requested_mode: str | None = None,
    fell_back: bool = False,
) -> str:
    """Format tier selection as standard JSON.

    ``names`` mirrors ``tiers`` with each pick's display name and is omitted when None.
    """
    ts = evaluated_at or datetime.now(UTC).isoformat(timespec="seconds")
    payload: dict[str, Any] = {"plan": plan, "tiers": tiers}
    if names is not None:
        payload["names"] = {tier: names.get(tier, "") for tier in tiers}
    payload["evaluated_at"] = ts
    payload["mode"] = mode
    payload["method"] = mode
    payload["fallback"] = bool(fell_back)
    if requested_mode is not None:
        payload["requested_mode"] = requested_mode
    if snapshot_info is not None:
        payload["snapshot"] = snapshot_info
    if settings is not None:
        payload["settings"] = settings
    if profile is not None and profile != "general":
        payload["profile"] = profile
    if fallbacks is not None:
        payload["fallbacks"] = fallbacks
    if explanations is not None:
        payload["explanations"] = explanations
    return json.dumps(payload, indent=2)


def settings_payload(criteria: SelectionCriteria) -> dict[str, Any]:
    """Serialize the effective selection criteria for provenance in outputs."""
    return {
        "mode": criteria.mode,
        "allow_fallback": criteria.allow_fallback,
        "top_n": criteria.top_n,
        "profile": criteria.profile,
        "price_basis": criteria.price_basis,
        "burn_basis": criteria.burn_basis,
        "allow_global_region": criteria.allow_global_region,
        "allow_paid_training": criteria.allow_paid_training,
        "allow_free_training": criteria.allow_free_training,
        "max_retention_days": criteria.max_retention_days,
        "require_zdr": criteria.require_zdr,
        "min_context": criteria.min_context,
        "min_output": criteria.min_output,
        "max_output_cost": criteria.max_output_cost,
        "max_burn": criteria.max_burn,
        "skip": sorted(criteria.skip_models),
        "prefer": dict(sorted(criteria.preferred_tiers.items())),
        "tokens": list(criteria.token_mix) if criteria.token_mix else None,
        "weights": list(criteria.weights) if criteria.weights else None,
        "at": criteria.at.isoformat(timespec="seconds") if criteria.at else None,
    }


def tier_display_names(tiers: dict[str, str], models: list[NormalizedModel]) -> dict[str, str]:
    """Display name of each tier's selected model (empty string when unknown or unnamed)."""
    model_map = _model_map(models)
    names: dict[str, str] = {}
    for tier, full_id in tiers.items():
        m = model_map.get(full_id) if full_id else None
        names[tier] = m.name if m is not None else ""
    return names


def explain_tiers(
    tiers: dict[str, str],
    models: list[NormalizedModel],
    criteria: SelectionCriteria | None = None,
) -> dict[str, dict[str, Any]]:
    """Build detailed per-tier metrics and selection rationale."""
    profile, token_mix, basis = _burn_basis(criteria)
    model_map = _model_map(models)

    explanations: dict[str, dict[str, Any]] = {}
    for tier, full_id in tiers.items():
        m = model_map.get(full_id)
        if m:
            quota_burn = (
                m.composite_burn_pct
                if m.composite_burn_pct is not None
                else compute_burn_pct(m, profile, token_mix, basis)
            )
            explanations[tier] = {
                "model_id": full_id,
                "name": m.name,
                "monthly_allowance": json_allowance(m.monthly_allowance),
                "output_cost_per_1m": m.cost.output,
                "turn_cost_usd": round(
                    compute_turn_cost(m, profile=profile, token_mix=token_mix), 6
                ),
                "quota_burn_pct": round(quota_burn, 2) if quota_burn is not None else 0.0,
                "aa_iq": m.aa_iq,
                "aa_coding": m.aa_coding,
                "speed_tps": m.speed_tps,
                "bpe": round(m.bpe, 4) if m.bpe is not None else None,
                "is_pareto": m.is_pareto,
                "context": m.limit.context,
            }
            est = m.request_estimate
            explanations[tier]["requests_per_5h"] = json_count(est.per_5h) if est else None
            explanations[tier]["requests_per_month"] = json_count(est.per_month) if est else None
            used = resolve_burn_basis(basis, profile, token_mix)
            if used == "estimate" and (est is None or est.per_month <= 0):
                used = "tokens"
            explanations[tier]["burn_basis_used"] = used
            if m.privacy is not None:
                explanations[tier]["privacy"] = m.privacy.model_dump(mode="json")
            if m.price_multiplier is not None:
                explanations[tier]["price_multiplier"] = json_multiplier(m.price_multiplier)
                change = _next_change(m, criteria)
                if change:
                    explanations[tier]["next_price_change"] = change
        else:
            explanations[tier] = {
                "model_id": full_id or "None",
                "name": "",
            }
    return explanations


def format_select_compact(tiers: dict[str, str], names: dict[str, str] | None = None) -> str:
    """Format tier selection as compact space-delimited lines with optional display names."""
    lines: list[str] = []
    for tier, model_id in tiers.items():
        if not model_id:
            continue
        name = (names or {}).get(tier, "")
        lines.append(f"{tier} {model_id} {name}" if name else f"{tier} {model_id}")
    return "\n".join(lines)


def _format_tier_lines(
    tiers: dict[str, str],
    fallbacks: dict[str, list[str]] | None,
    names: dict[str, str] | None,
    format_entry: Callable[[str, str, str], str],
) -> str:
    """Format per-tier primary, display name, and fallback lines using a line formatter."""
    lines = [format_entry(tier, "", tiers.get(tier, "")) for tier in STANDARD_TIERS]
    if names is not None:
        lines.extend(format_entry(tier, "name", names.get(tier, "")) for tier in STANDARD_TIERS)
    if fallbacks is not None:
        lines.extend(
            format_entry(tier, "fallbacks", ",".join(fallbacks.get(tier, [])))
            for tier in STANDARD_TIERS
        )
    return "\n".join(lines)


def _env_line(tier: str, suffix: str, value: str) -> str:
    key = f"OPENCODE_MODEL_{tier.upper()}"
    if suffix:
        key = f"{key}_{suffix.upper()}"
    return f'{key}="{value}"'


def _github_output_line(tier: str, suffix: str, value: str) -> str:
    key = f"{tier}_{suffix}" if suffix else tier
    return f"{key}={value}"


def format_select_env(
    tiers: dict[str, str],
    fallbacks: dict[str, list[str]] | None = None,
    names: dict[str, str] | None = None,
    *,
    mode: str | None = None,
    fell_back: bool = False,
) -> str:
    """Format tier selection as shell environment variable assignments."""
    out = _format_tier_lines(tiers, fallbacks, names, _env_line)
    if mode is not None:
        fb = "true" if fell_back else "false"
        out += f'\nOPENCODE_MODEL_METHOD="{mode}"\nOPENCODE_MODEL_FALLBACK="{fb}"'
    return out


def format_select_github_output(
    tiers: dict[str, str],
    fallbacks: dict[str, list[str]] | None = None,
    names: dict[str, str] | None = None,
    *,
    mode: str | None = None,
    fell_back: bool = False,
) -> str:
    """Format tier selection as GitHub Actions key=value output lines."""
    out = _format_tier_lines(tiers, fallbacks, names, _github_output_line)
    if mode is not None:
        fb = "true" if fell_back else "false"
        out += f"\nmethod={mode}\nfallback={fb}"
    return out


def format_select_table(
    tiers: dict[str, str],
    models: list[NormalizedModel],
    plan: str = "go",
    explain: bool = False,
    criteria: SelectionCriteria | None = None,
    *,
    mode: str | None = None,
    fell_back: bool = False,
) -> str:
    """Format tier selection as a human-readable ASCII table."""
    actual_mode = mode or (criteria.mode if criteria is not None else "allowance")
    banner = format_method_banner(actual_mode, fell_back=fell_back)
    profile, token_mix, basis = _burn_basis(criteria)
    model_map = _model_map(models)

    headers = [
        "Tier",
        "Model ID",
        "Model Name",
        f"Allowance ({plan})",
        "Output Cost",
        "Context",
    ]
    show_mult = explain and any(
        (m := model_map.get(fid)) is not None and m.price_multiplier is not None
        for fid in tiers.values()
    )
    if explain:
        headers.extend(["IQ", "BPE", "Burn %", "Turn Cost"])
        if show_mult:
            headers.append("Price x")

    rows: list[list[str]] = []
    for tier, full_id in tiers.items():
        m = model_map.get(full_id)
        if not m:
            # render_table pads all remaining base and explain columns with "-".
            rows.append([tier, full_id or "None"])
            continue

        row = [
            tier,
            full_id,
            m.name or "-",
            *_model_value_cells(m),
        ]
        if explain:
            burn_val = (
                m.composite_burn_pct
                if m.composite_burn_pct is not None
                else compute_burn_pct(m, profile, token_mix, basis)
            )
            turn_cost = compute_turn_cost(m, profile=profile, token_mix=token_mix)
            row.extend(
                [
                    f"{m.aa_iq:.1f}" if m.aa_iq is not None else "-",
                    f"{m.bpe:.3f}" if m.bpe is not None else "-",
                    f"{burn_val:.1f}%" if burn_val is not None else "-",
                    f"${turn_cost:.4f}",
                ]
            )
            if show_mult:
                row.append(format_multiplier(m.price_multiplier, _next_change(m, criteria)))
        rows.append(row)

    return f"{banner}\n{render_table(headers, rows)}"
