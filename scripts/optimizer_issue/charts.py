"""Self-contained SVG chart generators for live Pareto analysis."""

from __future__ import annotations

import html
from pathlib import Path
from typing import Any

from opencode_go_model_optimizer.engine.tiers import PAID_TIERS

from .common import _model_burn_pct, _model_id, _model_quality

_SVG_CSS_STYLES = """
    :root {
      --bg: #ffffff;
      --card-bg: #f8fafc;
      --text: #0f172a;
      --text-muted: #64748b;
      --border: #e2e8f0;
      --grid: #f1f5f9;
      --c-blue: #2563eb;
      --c-green: #10b981;
      --c-amber: #f59e0b;
      --c-purple: #8b5cf6;
      --c-slate: #94a3b8;
      --band-heavy: rgba(16, 185, 129, 0.12);
      --band-balanced: rgba(37, 99, 235, 0.08);
    }
    @media (prefers-color-scheme: dark) {
      :root {
        --bg: #0d1117;
        --card-bg: #161b22;
        --text: #f0f6fc;
        --text-muted: #8b949e;
        --border: #30363d;
        --grid: #21262d;
        --c-blue: #58a6ff;
        --c-green: #3fb950;
        --c-amber: #d29922;
        --c-purple: #bc8cff;
        --c-slate: #6e7681;
        --band-heavy: rgba(63, 185, 80, 0.16);
        --band-balanced: rgba(88, 166, 255, 0.12);
      }
    }
    text {
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    }
    """


def _svg_preamble(width: int, height: int) -> list[str]:
    """Return opening SVG tags, shared theme CSS, and rounded background rect."""
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%">',
        f"<style>{_SVG_CSS_STYLES}</style>",
        f'<rect width="{width}" height="{height}" rx="8" fill="var(--bg)"/>',
    ]


def _pareto_quality(
    model: dict[str, Any],
    profile: str,
    weights: tuple[float, float] | None,
) -> float:
    raw_quality = model.get("_quality")
    if isinstance(raw_quality, (int, float)) and not isinstance(raw_quality, bool):
        return float(raw_quality)
    return _model_quality(model, profile=profile, weights=weights)


def _render_pareto_frontier_svg(
    pareto_models: list[dict[str, Any]],
    rank_models: list[dict[str, Any]],
    top_quality: float,
    heavy_cutoff: float,
    balanced_cutoff: float,
    *,
    profile: str = "general",
    weights: tuple[float, float] | None = None,
) -> str:
    """Render the live Pareto frontier scatter & step-curve SVG."""
    ml, mr, mt, mb = 65, 35, 55, 55
    pw = 800 - ml - mr
    ph = 420 - mt - mb

    all_burns = [burn for m in rank_models if (burn := _model_burn_pct(m)) is not None]
    x_max = max([*all_burns, 100.0]) * 1.08
    pareto_qualities = [_pareto_quality(m, profile, weights) for m in pareto_models]
    y_max = max([top_quality, *pareto_qualities, 100.0]) * 1.08

    def to_x(burn: float) -> float:
        return ml + (burn / x_max) * pw

    def to_y(quality: float) -> float:
        return mt + ph - (quality / y_max) * ph

    svg = [
        *_svg_preamble(800, 420),
        '<text x="400" y="30" text-anchor="middle" font-size="15" font-weight="bold" fill="var(--text)">Live Pareto Frontier: Effective Quality vs Quota Burn %</text>',
    ]

    # Shaded bands
    y_top = to_y(y_max)
    y_h = to_y(heavy_cutoff)
    y_b = to_y(balanced_cutoff)
    if heavy_cutoff > 0:
        svg.append(
            f'<rect x="{ml}" y="{y_top:.1f}" width="{pw}" height="{max(0.0, y_h - y_top):.1f}" fill="var(--band-heavy)"/>'
        )
        svg.append(
            f'<text x="{ml + 10}" y="{y_top + 16:.1f}" font-size="11" font-weight="600" fill="var(--c-green)">Heavy Band (≥90%: {heavy_cutoff:.1f})</text>'
        )
    if balanced_cutoff > 0:
        svg.append(
            f'<rect x="{ml}" y="{y_h:.1f}" width="{pw}" height="{max(0.0, y_b - y_h):.1f}" fill="var(--band-balanced)"/>'
        )
        svg.append(
            f'<text x="{ml + 10}" y="{y_h + 16:.1f}" font-size="11" font-weight="600" fill="var(--c-blue)">Balanced Band (≥60%: {balanced_cutoff:.1f})</text>'
        )

    # Grid lines & ticks
    for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
        gy = to_y(frac * y_max)
        svg.append(
            f'<line x1="{ml}" y1="{gy:.1f}" x2="{ml + pw}" y2="{gy:.1f}" stroke="var(--grid)" stroke-width="1"/>'
        )
        svg.append(
            f'<text x="{ml - 8}" y="{gy + 4:.1f}" text-anchor="end" font-size="10" fill="var(--text-muted)">{int(frac * y_max)}</text>'
        )
        gx = to_x(frac * x_max)
        svg.append(
            f'<line x1="{gx:.1f}" y1="{mt}" x2="{gx:.1f}" y2="{mt + ph}" stroke="var(--grid)" stroke-width="1"/>'
        )
        svg.append(
            f'<text x="{gx:.1f}" y="{mt + ph + 16}" text-anchor="middle" font-size="10" fill="var(--text-muted)">{int(frac * x_max)}%</text>'
        )

    # Axes
    svg.extend(
        [
            f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="var(--border)" stroke-width="1.5"/>',
            f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="var(--border)" stroke-width="1.5"/>',
            f'<text x="{ml + pw / 2:.1f}" y="405" text-anchor="middle" font-size="12" fill="var(--text-muted)">Quota Burn %</text>',
            f'<text x="18" y="{mt + ph / 2:.1f}" text-anchor="middle" font-size="12" fill="var(--text-muted)" transform="rotate(-90, 18, {mt + ph / 2:.1f})">Effective Quality</text>',
        ]
    )

    # Dominated models
    for m in rank_models:
        if m.get("is_pareto"):
            continue
        if m.get("aa_iq") is None and m.get("aa_coding") is None:
            continue
        burn = _model_burn_pct(m)
        if burn is None:
            continue
        q = _model_quality(m, profile=profile, weights=weights)
        svg.append(
            f'<circle cx="{to_x(burn):.1f}" cy="{to_y(q):.1f}" r="4" fill="var(--c-slate)" opacity="0.45"/>'
        )

    # Pareto frontier models
    sorted_frontier = sorted(pareto_models, key=lambda x: _model_burn_pct(x) or 0.0)
    if sorted_frontier:
        pts: list[tuple[float, float, float, float, dict[str, Any]]] = []
        for m in sorted_frontier:
            burn = _model_burn_pct(m) or 0.0
            q = _pareto_quality(m, profile, weights)
            pts.append((to_x(burn), to_y(q), burn, q, m))

        path_d = f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"
        for i in range(1, len(pts)):
            path_d += f" L {pts[i][0]:.1f} {pts[i - 1][1]:.1f} L {pts[i][0]:.1f} {pts[i][1]:.1f}"
        svg.append(
            f'<path d="{path_d}" fill="none" stroke="var(--c-blue)" stroke-width="2.5" stroke-dasharray="4,3"/>'
        )

        for cx, cy, burn, q, m in pts:
            short_id = _model_id(m, "model").removeprefix("opencode-go/")
            svg.append(
                f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" fill="var(--bg)" stroke="var(--c-blue)" stroke-width="2.5"/>'
            )
            svg.append(
                f'<text x="{cx + 8:.1f}" y="{cy - 6:.1f}" font-size="11" font-weight="600" fill="var(--text)">'
                f"{html.escape(short_id)} ({burn:.1f}%, {q:.1f})</text>"
            )

    svg.append("</svg>")
    return "\n".join(svg)


def _render_catalog_ranking_svg(rank_models: list[dict[str, Any]]) -> str:
    """Render the horizontal bar breakdown SVG for top ranked catalog models."""
    top_models = rank_models[:12]
    row_h = 42
    header_h = 55
    footer_h = 35
    svg_h = header_h + len(top_models) * row_h + footer_h
    c_ml, c_mr = 190, 70
    bar_max_w = 800 - c_ml - c_mr
    max_val = 100.0

    svg = [
        *_svg_preamble(800, svg_h),
        '<text x="30" y="28" font-size="15" font-weight="bold" fill="var(--text)">Ranked Model Catalog: Benchmark Scores &amp; Quota Burn</text>',
        '<rect x="480" y="18" width="10" height="10" rx="2" fill="var(--c-blue)"/>',
        '<text x="495" y="27" font-size="11" fill="var(--text-muted)">AA IQ</text>',
        '<rect x="560" y="18" width="10" height="10" rx="2" fill="var(--c-green)"/>',
        '<text x="575" y="27" font-size="11" fill="var(--text-muted)">Engineering</text>',
        '<rect x="665" y="18" width="10" height="10" rx="2" fill="var(--c-amber)"/>',
        '<text x="680" y="27" font-size="11" fill="var(--text-muted)">Quota Burn %</text>',
    ]

    for i, m in enumerate(top_models):
        y_base = header_h + i * row_h
        short_id = _model_id(m).removeprefix("opencode-go/")
        rank_val = m.get("rank")
        rank_s = f"#{rank_val}" if rank_val is not None else f"#{i + 1}"

        svg.append(
            f'<text x="{c_ml - 12}" y="{y_base + 22}" text-anchor="end" font-size="12" font-weight="500" fill="var(--text)">'
            f"{rank_s} {html.escape(short_id)}</text>"
        )

        iq = float(m.get("aa_iq") or 0.0)
        coding = float(m.get("aa_coding") or 0.0)
        burn = _model_burn_pct(m) or 0.0

        bars = (
            (iq, y_base + 6, "var(--c-blue)", f"{iq:.1f}"),
            (coding, y_base + 16, "var(--c-green)", f"{coding:.1f}"),
            (min(burn, 100.0), y_base + 26, "var(--c-amber)", f"{burn:.1f}%"),
        )
        for val, y_pos, fill, label in bars:
            width = max(0.0, min(bar_max_w, (val / max_val) * bar_max_w))
            svg.append(
                f'<rect x="{c_ml}" y="{y_pos}" width="{width:.1f}" height="7" rx="3" fill="{fill}"/>'
            )
            svg.append(
                f'<text x="{c_ml + width + 6:.1f}" y="{y_pos + 7}" font-size="9" fill="var(--text-muted)">{label}</text>'
            )

    svg.append("</svg>")
    return "\n".join(svg)


def _render_multi_plan_comparison_svg(plan_payloads: dict[str, dict[str, Any]]) -> str:
    """Render the multi-plan monthly allowance vs quota burn comparison SVG."""
    plans = list(plan_payloads.keys())
    p_ml, p_mr, p_mt, p_mb = 60, 30, 60, 55
    p_pw = 800 - p_ml - p_mr
    p_ph = 360 - p_mt - p_mb

    all_vals: list[float] = [100.0]
    for p_name in plans:
        explanations = plan_payloads[p_name].get("explanations") or {}
        for tier in PAID_TIERS:
            ex = explanations.get(tier) or {}
            allow = ex.get("monthly_allowance")
            if isinstance(allow, (int, float)) and not isinstance(allow, bool):
                all_vals.append(float(allow))
            burn = _model_burn_pct(ex)
            if burn is not None:
                all_vals.append(burn)
    p_max_val = max(all_vals) * 1.1

    svg = [
        *_svg_preamble(800, 360),
        '<text x="400" y="28" text-anchor="middle" font-size="15" font-weight="bold" fill="var(--text)">Multi-Plan Comparison: Monthly Allowance ($) vs Quota Burn (%)</text>',
        '<rect x="280" y="40" width="10" height="10" rx="2" fill="var(--c-blue)"/>',
        '<text x="295" y="49" font-size="11" fill="var(--text-muted)">Allowance ($)</text>',
        '<rect x="420" y="40" width="10" height="10" rx="2" fill="var(--c-amber)"/>',
        '<text x="435" y="49" font-size="11" fill="var(--text-muted)">Quota Burn %</text>',
    ]

    tier_w = p_pw / len(PAID_TIERS)
    num_p = max(1, len(plans))
    for t_idx, tier in enumerate(PAID_TIERS):
        tier_x = p_ml + t_idx * tier_w
        svg.append(
            f'<text x="{tier_x + tier_w / 2:.1f}" y="345" text-anchor="middle" font-size="13" font-weight="600" fill="var(--text)">{tier}</text>'
        )

        plan_slot_w = (tier_w * 0.88) / num_p
        for p_idx, p_name in enumerate(plans):
            ex = (plan_payloads[p_name].get("explanations") or {}).get(tier) or {}
            allow_val = ex.get("monthly_allowance")
            allow_f = (
                float(allow_val)
                if isinstance(allow_val, (int, float)) and not isinstance(allow_val, bool)
                else 0.0
            )
            burn_f = _model_burn_pct(ex) or 0.0

            slot_x = tier_x + (tier_w * 0.06) + p_idx * plan_slot_w
            bw = (plan_slot_w * 0.8) / 2

            h_allow = max(0.0, (allow_f / p_max_val) * p_ph)
            h_burn = max(0.0, (min(burn_f, p_max_val) / p_max_val) * p_ph)

            svg.append(
                f'<rect x="{slot_x:.1f}" y="{p_mt + p_ph - h_allow:.1f}" width="{bw:.1f}" height="{h_allow:.1f}" rx="2" fill="var(--c-blue)"/>'
            )
            svg.append(
                f'<rect x="{slot_x + bw + 2:.1f}" y="{p_mt + p_ph - h_burn:.1f}" width="{bw:.1f}" height="{h_burn:.1f}" rx="2" fill="var(--c-amber)"/>'
            )
            svg.append(
                f'<text x="{slot_x + bw:.1f}" y="{p_mt + p_ph + 14}" text-anchor="middle" font-size="9" fill="var(--text-muted)">{html.escape(p_name)}</text>'
            )

    svg.append("</svg>")
    return "\n".join(svg)


def generate_analysis_svgs(
    charts_dir: Path,
    pareto_models: list[dict[str, Any]],
    rank_models: list[dict[str, Any]],
    plan_payloads: dict[str, dict[str, Any]] | None,
    top_quality: float,
    heavy_cutoff: float,
    balanced_cutoff: float,
    *,
    profile: str = "general",
    weights: tuple[float, float] | None = None,
) -> list[str]:
    """Generate crisp, self-contained SVG charts for live Pareto analysis."""
    charts_dir.mkdir(parents=True, exist_ok=True)
    generated: list[str] = []

    (charts_dir / "pareto-frontier.svg").write_text(
        _render_pareto_frontier_svg(
            pareto_models,
            rank_models,
            top_quality,
            heavy_cutoff,
            balanced_cutoff,
            profile=profile,
            weights=weights,
        ),
        encoding="utf-8",
    )
    generated.append("pareto-frontier.svg")

    (charts_dir / "catalog-ranking.svg").write_text(
        _render_catalog_ranking_svg(rank_models),
        encoding="utf-8",
    )
    generated.append("catalog-ranking.svg")

    if plan_payloads:
        (charts_dir / "multi-plan-comparison.svg").write_text(
            _render_multi_plan_comparison_svg(plan_payloads),
            encoding="utf-8",
        )
        generated.append("multi-plan-comparison.svg")

    return generated
