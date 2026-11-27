"""Self-contained SVG chart generators for live Pareto analysis."""

from __future__ import annotations

import html
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from opencode_go_model_optimizer.engine.tiers import (
    BALANCED_QUALITY_RATIO,
    HEAVY_QUALITY_RATIO,
    PAID_TIERS,
)

from .common import _model_burn_pct, _model_id

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


def _as_float(val: Any) -> float | None:
    """Return float(val) for real numeric values, excluding booleans."""
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    return None


def _svg_preamble(width: int, height: int) -> list[str]:
    """Return opening SVG tags, shared theme CSS, and rounded background rect."""
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%">',
        f"<style>{_SVG_CSS_STYLES}</style>",
        f'<rect width="{width}" height="{height}" rx="8" fill="var(--bg)"/>',
    ]


def _svg_legend(items: Sequence[tuple[float, str, str]], y: float) -> list[str]:
    """Render horizontal color-swatch legend entries."""
    out: list[str] = []
    for x, fill, label in items:
        out.append(f'<rect x="{x}" y="{y}" width="10" height="10" rx="2" fill="{fill}"/>')
        out.append(
            f'<text x="{x + 15}" y="{y + 9}" font-size="11" fill="var(--text-muted)">{html.escape(label)}</text>'
        )
    return out


def _render_pareto_frontier_svg(
    pareto_models: list[dict[str, Any]],
    rank_models: list[dict[str, Any]],
    top_quality: float,
    heavy_cutoff: float,
    balanced_cutoff: float,
) -> str:
    """Render the live Pareto frontier scatter & step-curve SVG."""
    ml, mr, mt, mb = 65, 35, 55, 55
    pw, ph = 800 - ml - mr, 420 - mt - mb

    all_burns = [burn for m in rank_models if (burn := _model_burn_pct(m)) is not None]
    x_max = max([*all_burns, 100.0]) * 1.08
    y_max = (
        max(
            [
                top_quality,
                *(_as_float(m.get("effective_quality")) or 0.0 for m in pareto_models),
                100.0,
            ]
        )
        * 1.08
    )

    def to_x(burn: float) -> float:
        return ml + (burn / x_max) * pw

    def to_y(quality: float) -> float:
        return mt + ph - (quality / y_max) * ph

    svg = [
        *_svg_preamble(800, 420),
        '<text x="400" y="30" text-anchor="middle" font-size="15" font-weight="bold" fill="var(--text)">Live Pareto Frontier: Effective Quality vs Quota Burn %</text>',
    ]

    y_top, y_h, y_b = to_y(y_max), to_y(heavy_cutoff), to_y(balanced_cutoff)
    bands = (
        (
            heavy_cutoff,
            y_top,
            y_h,
            "var(--band-heavy)",
            "var(--c-green)",
            f"Heavy Band (≥{HEAVY_QUALITY_RATIO:.0%}: {heavy_cutoff:.1f})",
        ),
        (
            balanced_cutoff,
            y_h,
            y_b,
            "var(--band-balanced)",
            "var(--c-blue)",
            f"Balanced Band (≥{BALANCED_QUALITY_RATIO:.0%}: {balanced_cutoff:.1f})",
        ),
    )
    for cutoff, top, bottom, fill, text_fill, label in bands:
        if cutoff > 0:
            svg.append(
                f'<rect x="{ml}" y="{top:.1f}" width="{pw}" height="{max(0.0, bottom - top):.1f}" fill="{fill}"/>'
            )
            svg.append(
                f'<text x="{ml + 10}" y="{top + 16:.1f}" font-size="11" font-weight="600" fill="{text_fill}">{label}</text>'
            )

    for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
        gy, gx = to_y(frac * y_max), to_x(frac * x_max)
        svg.extend(
            [
                f'<line x1="{ml}" y1="{gy:.1f}" x2="{ml + pw}" y2="{gy:.1f}" stroke="var(--grid)" stroke-width="1"/>',
                f'<text x="{ml - 8}" y="{gy + 4:.1f}" text-anchor="end" font-size="10" fill="var(--text-muted)">{int(frac * y_max)}</text>',
                f'<line x1="{gx:.1f}" y1="{mt}" x2="{gx:.1f}" y2="{mt + ph}" stroke="var(--grid)" stroke-width="1"/>',
                f'<text x="{gx:.1f}" y="{mt + ph + 16}" text-anchor="middle" font-size="10" fill="var(--text-muted)">{int(frac * x_max)}%</text>',
            ]
        )

    svg.extend(
        [
            f'<line x1="{ml}" y1="{mt}" x2="{ml}" y2="{mt + ph}" stroke="var(--border)" stroke-width="1.5"/>',
            f'<line x1="{ml}" y1="{mt + ph}" x2="{ml + pw}" y2="{mt + ph}" stroke="var(--border)" stroke-width="1.5"/>',
            f'<text x="{ml + pw / 2:.1f}" y="405" text-anchor="middle" font-size="12" fill="var(--text-muted)">Quota Burn %</text>',
            f'<text x="18" y="{mt + ph / 2:.1f}" text-anchor="middle" font-size="12" fill="var(--text-muted)" transform="rotate(-90, 18, {mt + ph / 2:.1f})">Effective Quality</text>',
        ]
    )

    for m in rank_models:
        if m.get("is_pareto") or (m.get("aa_iq") is None and m.get("aa_coding") is None):
            continue
        if (burn := _model_burn_pct(m)) is None:
            continue
        q = _as_float(m.get("effective_quality")) or 0.0
        svg.append(
            f'<circle cx="{to_x(burn):.1f}" cy="{to_y(q):.1f}" r="4" fill="var(--c-slate)" opacity="0.45"/>'
        )

    sorted_frontier = sorted(pareto_models, key=lambda x: _model_burn_pct(x) or 0.0)
    if sorted_frontier:
        pts = [
            (
                to_x(b := (_model_burn_pct(m) or 0.0)),
                to_y(q := (_as_float(m.get("effective_quality")) or 0.0)),
                b,
                q,
                m,
            )
            for m in sorted_frontier
        ]
        segs = [f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"] + [
            f"L {pts[i][0]:.1f} {pts[i - 1][1]:.1f} L {pts[i][0]:.1f} {pts[i][1]:.1f}"
            for i in range(1, len(pts))
        ]
        svg.append(
            f'<path d="{" ".join(segs)}" fill="none" stroke="var(--c-blue)" stroke-width="2.5" stroke-dasharray="4,3"/>'
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
    row_h, header_h, footer_h = 42, 55, 35
    c_ml, c_mr = 190, 70
    bar_max_w = 800 - c_ml - c_mr

    svg = [
        *_svg_preamble(800, header_h + len(top_models) * row_h + footer_h),
        '<text x="30" y="28" font-size="15" font-weight="bold" fill="var(--text)">Ranked Model Catalog: Benchmark Scores &amp; Quota Burn</text>',
        *_svg_legend(
            (
                (480, "var(--c-blue)", "AA IQ"),
                (560, "var(--c-green)", "Engineering"),
                (665, "var(--c-amber)", "Quota Burn %"),
            ),
            18,
        ),
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

        iq, coding, burn = (
            float(m.get("aa_iq") or 0.0),
            float(m.get("aa_coding") or 0.0),
            _model_burn_pct(m) or 0.0,
        )
        for val, y_pos, fill, label in (
            (iq, y_base + 6, "var(--c-blue)", f"{iq:.1f}"),
            (coding, y_base + 16, "var(--c-green)", f"{coding:.1f}"),
            (min(burn, 100.0), y_base + 26, "var(--c-amber)", f"{burn:.1f}%"),
        ):
            width = max(0.0, min(bar_max_w, (val / 100.0) * bar_max_w))
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
    p_pw, p_ph = 800 - p_ml - p_mr, 360 - p_mt - p_mb

    all_vals: list[float] = [100.0]
    for p_name in plans:
        explanations = plan_payloads[p_name].get("explanations") or {}
        for tier in PAID_TIERS:
            ex = explanations.get(tier) or {}
            if (allow := _as_float(ex.get("monthly_allowance"))) is not None:
                all_vals.append(allow)
            if (burn := _model_burn_pct(ex)) is not None:
                all_vals.append(burn)
    p_max_val = max(all_vals) * 1.1

    svg = [
        *_svg_preamble(800, 360),
        '<text x="400" y="28" text-anchor="middle" font-size="15" font-weight="bold" fill="var(--text)">Multi-Plan Comparison: Monthly Allowance ($) vs Quota Burn (%)</text>',
        *_svg_legend(
            ((280, "var(--c-blue)", "Allowance ($)"), (420, "var(--c-amber)", "Quota Burn %")), 40
        ),
    ]

    tier_w = p_pw / len(PAID_TIERS)
    plan_slot_w = (tier_w * 0.88) / max(1, len(plans))
    bw = (plan_slot_w * 0.8) / 2
    for t_idx, tier in enumerate(PAID_TIERS):
        tier_x = p_ml + t_idx * tier_w
        svg.append(
            f'<text x="{tier_x + tier_w / 2:.1f}" y="345" text-anchor="middle" font-size="13" font-weight="600" fill="var(--text)">{tier}</text>'
        )
        for p_idx, p_name in enumerate(plans):
            ex = (plan_payloads[p_name].get("explanations") or {}).get(tier) or {}
            allow_f = _as_float(ex.get("monthly_allowance")) or 0.0
            burn_f = _model_burn_pct(ex) or 0.0
            slot_x = tier_x + (tier_w * 0.06) + p_idx * plan_slot_w
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
) -> list[str]:
    """Generate crisp, self-contained SVG charts for live Pareto analysis."""
    charts_dir.mkdir(parents=True, exist_ok=True)
    specs = [
        (
            "pareto-frontier.svg",
            _render_pareto_frontier_svg(
                pareto_models,
                rank_models,
                top_quality,
                heavy_cutoff,
                balanced_cutoff,
            ),
        ),
        ("catalog-ranking.svg", _render_catalog_ranking_svg(rank_models)),
    ]
    if plan_payloads:
        specs.append(
            ("multi-plan-comparison.svg", _render_multi_plan_comparison_svg(plan_payloads))
        )
    for filename, content in specs:
        (charts_dir / filename).write_text(content, encoding="utf-8")
    return [filename for filename, _ in specs]
