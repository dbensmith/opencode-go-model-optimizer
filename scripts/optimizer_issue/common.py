"""Shared formatting, URL, catalog, quality, and JSON loading helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from opencode_go_model_optimizer.engine.pareto import effective_quality
from opencode_go_model_optimizer.engine.tiers import STANDARD_TIERS
from opencode_go_model_optimizer.ingest.models import NormalizedModel

_RANKED_CATALOG_HEADERS = (
    "| Rank | Model ID | Tier | BPE | AA IQ | Engineering | Burn % | Allowance | Output Cost | Context | Release | Privacy |",
    "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |",
)


def _fmt_allowance(val: Any) -> str:
    if val is None:
        return "-"
    if val == "unlimited":
        return "Unlimited"
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return f"${val:.2f}"
    return str(val)


def _fmt_output_cost(val: Any) -> str:
    if val is None:
        return "-"
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return "Free" if val == 0 else f"${val:.2f}/1M"
    return str(val)


def _fmt_int(val: Any) -> str:
    if val is None:
        return "-"
    if val == "unlimited":
        return "Unlimited"
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return f"{int(val):,}" if float(val).is_integer() else f"{val:,.1f}"
    return str(val)


def _fmt_float(val: Any, decimals: int = 1) -> str:
    if val is None:
        return "-"
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return f"{val:.{decimals}f}"
    return str(val)


def _fmt_burn(val: Any, decimals: int = 1) -> str:
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return f"{val:.{decimals}f}%"
    return "-"


def _fmt_pareto(val: Any) -> str:
    if val is None:
        return "-"
    return "Yes" if val else "No"


def _fmt_multiplier(ex: dict[str, Any]) -> str:
    mult = ex.get("price_multiplier")
    if mult is None:
        return "-"
    cell = f"x{mult:.3g}"
    change = ex.get("next_price_change")
    if isinstance(change, dict) and change.get("at") and change.get("multiplier") is not None:
        cell += f" (-> x{change['multiplier']:.3g} @ {change['at']})"
    return cell


def _fmt_privacy(privacy: dict[str, Any] | None) -> str:
    if not privacy:
        return "No privacy metadata"
    parts: list[str] = []
    if privacy.get("zdr"):
        parts.append("ZDR (0d retention)")
    elif privacy.get("retention_days") is not None:
        parts.append(f"{privacy['retention_days']}d retention")
    else:
        parts.append("Retention unspecified")
    parts.append("trains on data" if privacy.get("trains_on_data") else "no training")
    if privacy.get("requires_global_region"):
        parts.append("requires Global region")
    if privacy.get("agreement_valid_until"):
        parts.append(f"agreement valid through {privacy['agreement_valid_until']}")
    if privacy.get("note"):
        parts.append(str(privacy["note"]))
    return "; ".join(parts)


def _normalize_site_url(site_url: str | None) -> str | None:
    """Return a GitHub Pages base URL with exactly one trailing slash, or None."""
    if not site_url or not site_url.strip():
        return None
    return site_url.strip().rstrip("/") + "/"


def _site_file(site_url: str, path: str) -> str:
    """Absolute link to a published file under the GitHub Pages base URL."""
    return f"{site_url}{path.lstrip('/')}"


def _site_page(site_url: str, page: str) -> str:
    """Docsify hash route for a rendered Markdown page on GitHub Pages."""
    return f"{site_url}#/{page.lstrip('/')}"


def _fence_for(text: str) -> str:
    """Return a backtick fence longer than any backtick run in text."""
    longest = 0
    current = 0
    for ch in text:
        if ch == "`":
            current += 1
            if current > longest:
                longest = current
        else:
            current = 0
    return "`" * max(3, longest + 1)


def _model_id(m: dict[str, Any], default: str = "unknown") -> str:
    """Return the preferred model identifier string from a serialized model entry."""
    return str(m.get("full_id") or m.get("id") or m.get("model_id") or default)


def _model_burn_pct(m: dict[str, Any]) -> float | None:
    """Extract composite or quota burn percentage from a serialized model entry."""
    burn = m.get("composite_burn_pct")
    if burn is None:
        burn = m.get("quota_burn_pct")
    if isinstance(burn, (int, float)) and not isinstance(burn, bool):
        return float(burn)
    return None


def _ordered_tiers(explanations: dict[str, Any], tiers: dict[str, Any]) -> list[str]:
    """Order tiers according to STANDARD_TIERS, preserving any extra explanation keys."""
    ordered = [t for t in STANDARD_TIERS if t in explanations or t in tiers]
    for tier in explanations:
        if tier not in ordered:
            ordered.append(tier)
    return ordered


def _extract_quality_params(
    select_payload: dict[str, Any] | None,
) -> tuple[str, tuple[float, float] | None]:
    """Extract ``(profile, weights)`` from a select payload's settings."""
    if not select_payload:
        return "general", None
    settings = select_payload.get("settings") or {}
    profile = str(settings.get("profile") or select_payload.get("profile") or "general")
    raw_weights = settings.get("weights")
    weights: tuple[float, float] | None = None
    if (
        isinstance(raw_weights, (list, tuple))
        and len(raw_weights) == 2
        and not any(isinstance(w, bool) for w in raw_weights)
    ):
        try:
            weights = (float(raw_weights[0]), float(raw_weights[1]))
        except ValueError, TypeError:
            weights = None
    return profile, weights


def _model_quality(
    m: dict[str, Any],
    profile: str = "general",
    weights: tuple[float, float] | None = None,
) -> float:
    """Compute effective quality for a serialized model dict via the engine."""
    model = NormalizedModel(
        id=_model_id(m),
        aa_iq=m.get("aa_iq"),
        aa_coding=m.get("aa_coding"),
        speed_tps=m.get("speed_tps"),
    )
    return effective_quality(model, profile=profile, weights=weights)


def _model_quality_and_value(
    m: dict[str, Any],
    profile: str = "general",
    weights: tuple[float, float] | None = None,
) -> tuple[float, float]:
    """Return ``(effective_quality, quality_per_burn_value)`` for a serialized model dict."""
    quality = _model_quality(m, profile=profile, weights=weights)
    burn = _model_burn_pct(m)
    value = (quality / burn) if burn is not None and burn > 0 and quality > 0 else 0.0
    return quality, value


def _format_settings_lines(payload: dict[str, Any], default_mode: str) -> list[str]:
    """Render Run Settings lines when effective settings are present in payload."""
    settings = payload.get("settings") or {}
    if not settings:
        return []
    lines = [
        "",
        "### Run Settings",
        "",
        f"- **Mode**: `{settings.get('mode', default_mode)}` | **Profile**: `{settings.get('profile', 'general')}` "
        f"| **Price basis**: `{settings.get('price_basis', '-')}` | **Burn basis**: `{settings.get('burn_basis', '-')}` "
        f"| **Top N**: `{settings.get('top_n', 1)}`",
        f"- **Global regions**: {'allowed' if settings.get('allow_global_region') else 'blocked'} "
        f"| **Paid training**: {'allowed' if settings.get('allow_paid_training') else 'blocked'} "
        f"| **Free training**: {'allowed' if settings.get('allow_free_training') else 'blocked'}",
        f"- **Filters**: max retention `{settings.get('max_retention_days') or 'none'}` "
        f"| ZDR required `{settings.get('require_zdr', False)}` "
        f"| min context `{settings.get('min_context', 0)}` "
        f"| min output `{settings.get('min_output', 0)}` "
        f"| max output cost `{settings.get('max_output_cost') or 'none'}` "
        f"| max burn `{settings.get('max_burn') or 'none'}`",
    ]
    skip = settings.get("skip") or []
    prefer = settings.get("prefer") or {}
    if skip or prefer:
        extras = []
        if skip:
            extras.append(f"skip `{', '.join(skip)}`")
        if prefer:
            extras.append("prefer " + ", ".join(f"`{k}={v}`" for k, v in prefer.items()))
        lines.append("- **Overrides**: " + " | ".join(extras))
    return lines


def _ranked_catalog_row(m: dict[str, Any]) -> str:
    """Render one full-catalog markdown table row from a rank JSON model entry."""
    model_id = _model_id(m)
    tier = m.get("tier", "-")
    bpe_s = _fmt_float(m.get("bpe"), 3)
    iq_s = _fmt_float(m.get("aa_iq"), 1)
    eng_s = _fmt_float(m.get("aa_coding"), 1)
    burn_s = _fmt_burn(_model_burn_pct(m))
    allowance_s = _fmt_allowance(m.get("monthly_allowance"))

    if m.get("is_zero_cost"):
        output_s = "Free"
    else:
        cost_dict = m.get("cost") or {}
        output_cost = cost_dict.get("output")
        if output_cost is not None:
            output_s = _fmt_output_cost(output_cost)
        else:
            output_s = _fmt_output_cost(m.get("output_cost_per_1m"))

    limit_dict = m.get("limit") or {}
    ctx_val = limit_dict.get("context") if limit_dict else None
    if ctx_val is None:
        ctx_val = m.get("context")
    ctx_s = _fmt_int(ctx_val)

    rank_val = m.get("rank")
    rank_s = str(rank_val) if rank_val is not None else "-"

    release = m.get("release_date") or "-"
    priv = _fmt_privacy(m.get("privacy"))
    return (
        f"| {rank_s} | `{model_id}` | {tier} | {bpe_s} | {iq_s} | {eng_s} | {burn_s} "
        f"| {allowance_s} | {output_s} | {ctx_s} | {release} | {priv} |"
    )


def _catalog_composition_pie_lines(rank_models: list[dict[str, Any]]) -> list[str]:
    """Mermaid pie block summarizing the catalog by eligibility & frontier status."""
    if not rank_models:
        return []
    pareto_count = 0
    paid_non_frontier_count = 0
    free_count = 0
    unbenchmarked_count = 0

    for m in rank_models:
        is_pareto = bool(m.get("is_pareto"))
        is_zero = bool(m.get("is_zero_cost"))
        has_bm = m.get("aa_iq") is not None or m.get("aa_coding") is not None

        if is_pareto:
            pareto_count += 1
        elif is_zero:
            free_count += 1
        elif has_bm:
            paid_non_frontier_count += 1
        else:
            unbenchmarked_count += 1

    pie_lines = [
        "",
        "```mermaid",
        "pie showData",
        '    title "Catalog Composition by Eligibility & Frontier Status"',
    ]
    slices = [
        ("Pareto Frontier", pareto_count),
        ("Paid (Non-Frontier)", paid_non_frontier_count),
        ("Free / Zero-Cost", free_count),
        ("Unbenchmarked Paid", unbenchmarked_count),
    ]
    for name, count in slices:
        if count > 0:
            pie_lines.append(f'    "{name}" : {count}')
    pie_lines.append("```")
    return pie_lines


def _load_rank_models(path: str | None) -> list[dict[str, Any]]:
    """Load model entries from a ``rank --format json`` output file."""
    if not path:
        return []
    rank_data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(rank_data, list):
        return rank_data
    if isinstance(rank_data, dict):
        return rank_data.get("models") or rank_data.get("rankings") or []
    return []
