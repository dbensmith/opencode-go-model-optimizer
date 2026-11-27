"""Self-contained SVG chart generators for live Pareto analysis."""

from __future__ import annotations

import html
from collections.abc import Callable, Sequence
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


def _as_float(val: Any) -> float | None:
    """Return float(val) for real numeric values, excluding booleans."""
    if isinstance(val, (int, float)) and not isinstance(val, bool):
        return float(val)
    return None


def _fmt_coord(val: float) -> str:
    return f"{val:.1f}"


def _svg_rect(
    x: float | None,
    y: float | None,
    width: float,
    height: float,
    fill: str,
    *,
    rx: int | None = None,
) -> str:
    attrs: list[str] = []
    if x is not None:
        attrs.append(f'x="{_fmt_coord(x)}"')
    if y is not None:
        attrs.append(f'y="{_fmt_coord(y)}"')
    attrs.append(f'width="{_fmt_coord(width)}"')
    attrs.append(f'height="{_fmt_coord(height)}"')
    if rx is not None:
        attrs.append(f'rx="{rx}"')
    attrs.append(f'fill="{fill}"')
    return f"<rect {' '.join(attrs)}/>"


def _svg_line(x1: float, y1: float, x2: float, y2: float, stroke: str, stroke_width: float) -> str:
    return (
        f'<line x1="{_fmt_coord(x1)}" y1="{_fmt_coord(y1)}" '
        f'x2="{_fmt_coord(x2)}" y2="{_fmt_coord(y2)}" '
        f'stroke="{stroke}" stroke-width="{stroke_width}"/>'
    )


def _svg_text(
    x: float,
    y: float,
    content: str,
    *,
    font_size: int,
    fill: str,
    anchor: str | None = None,
    weight: str | None = None,
    transform: str | None = None,
) -> str:
    attrs = [f'x="{_fmt_coord(x)}"', f'y="{_fmt_coord(y)}"']
    if anchor:
        attrs.append(f'text-anchor="{anchor}"')
    attrs.append(f'font-size="{font_size}"')
    if weight:
        attrs.append(f'font-weight="{weight}"')
    attrs.append(f'fill="{fill}"')
    if transform:
        attrs.append(f'transform="{transform}"')
    return f"<text {' '.join(attrs)}>{html.escape(content)}</text>"


def _svg_legend(items: Sequence[tuple[float, str, str]], *, y: float) -> list[str]:
    elements: list[str] = []
    for x, fill, label in items:
        elements.append(_svg_rect(x, y, 10, 10, fill, rx=2))
        elements.append(_svg_text(x + 15, y + 9, label, font_size=11, fill="var(--text-muted)"))
    return elements


def _svg_preamble(width: int, height: int) -> list[str]:
    """Return opening SVG tags, shared theme CSS, and rounded background rect."""
    return [
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" width="100%" height="100%">',
        f"<style>{_SVG_CSS_STYLES}</style>",
        _svg_rect(None, None, width, height, "var(--bg)", rx=8),
    ]


def _pareto_quality(
    model: dict[str, Any],
    profile: str,
    weights: tuple[float, float] | None,
) -> float:
    raw_quality = _as_float(model.get("_quality"))
    if raw_quality is not None:
        return raw_quality
    return _model_quality(model, profile=profile, weights=weights)


def _pareto_bands(
    ml: int,
    pw: int,
    to_y: Callable[[float], float],
    y_max: float,
    heavy_cutoff: float,
    balanced_cutoff: float,
) -> list[str]:
    y_top, y_h, y_b = to_y(y_max), to_y(heavy_cutoff), to_y(balanced_cutoff)
    bands = (
        (
            heavy_cutoff,
            y_top,
            y_h,
            "var(--band-heavy)",
            "var(--c-green)",
            f"Heavy Band (≥90%: {heavy_cutoff:.1f})",
        ),
        (
            balanced_cutoff,
            y_h,
            y_b,
            "var(--band-balanced)",
            "var(--c-blue)",
            f"Balanced Band (≥60%: {balanced_cutoff:.1f})",
        ),
    )
    elements: list[str] = []
    for cutoff, top, bottom, band_fill, text_fill, label in bands:
        if cutoff <= 0:
            continue
        elements.append(_svg_rect(ml, top, pw, max(0.0, bottom - top), band_fill))
        elements.append(
            _svg_text(ml + 10, top + 16, label, font_size=11, weight="600", fill=text_fill)
        )
    return elements


def _pareto_grid_and_axes(
    ml: int,
    mt: int,
    pw: int,
    ph: int,
    x_max: float,
    y_max: float,
    to_x: Callable[[float], float],
    to_y: Callable[[float], float],
) -> list[str]:
    elements: list[str] = []
    for frac in (0.2, 0.4, 0.6, 0.8, 1.0):
        gy = to_y(frac * y_max)
        gx = to_x(frac * x_max)
        elements.extend(
            [
                _svg_line(ml, gy, ml + pw, gy, "var(--grid)", 1),
                _svg_text(
                    ml - 8,
                    gy + 4,
                    str(int(frac * y_max)),
                    font_size=10,
                    fill="var(--text-muted)",
                    anchor="end",
                ),
                _svg_line(gx, mt, gx, mt + ph, "var(--grid)", 1),
                _svg_text(
                    gx,
                    mt + ph + 16,
                    f"{int(frac * x_max)}%",
                    font_size=10,
                    fill="var(--text-muted)",
                    anchor="middle",
                ),
            ]
        )

    elements.extend(
        [
            _svg_line(ml, mt, ml, mt + ph, "var(--border)", 1.5),
            _svg_line(ml, mt + ph, ml + pw, mt + ph, "var(--border)", 1.5),
            _svg_text(
                ml + pw / 2,
                405,
                "Quota Burn %",
                font_size=12,
                fill="var(--text-muted)",
                anchor="middle",
            ),
            _svg_text(
                18,
                mt + ph / 2,
                "Effective Quality",
                font_size=12,
                fill="var(--text-muted)",
                anchor="middle",
                transform=f"rotate(-90, 18, {mt + ph / 2:.1f})",
            ),
        ]
    )
    return elements


def _pareto_points_and_frontier(
    pareto_models: list[dict[str, Any]],
    rank_models: list[dict[str, Any]],
    to_x: Callable[[float], float],
    to_y: Callable[[float], float],
    profile: str,
    weights: tuple[float, float] | None,
) -> list[str]:
    elements: list[str] = []
    for m in rank_models:
        if m.get("is_pareto") or (m.get("aa_iq") is None and m.get("aa_coding") is None):
            continue
        burn = _model_burn_pct(m)
        if burn is None:
            continue
        q = _model_quality(m, profile=profile, weights=weights)
        elements.append(
            f'<circle cx="{to_x(burn):.1f}" cy="{to_y(q):.1f}" r="4" fill="var(--c-slate)" opacity="0.45"/>'
        )

    sorted_frontier = sorted(pareto_models, key=lambda x: _model_burn_pct(x) or 0.0)
    if not sorted_frontier:
        return elements

    pts = [
        (
            to_x(burn := (_model_burn_pct(m) or 0.0)),
            to_y(q := _pareto_quality(m, profile, weights)),
            burn,
            q,
            m,
        )
        for m in sorted_frontier
    ]
    segments = [f"M {pts[0][0]:.1f} {pts[0][1]:.1f}"] + [
        f"L {pts[i][0]:.1f} {pts[i - 1][1]:.1f} L {pts[i][0]:.1f} {pts[i][1]:.1f}"
        for i in range(1, len(pts))
    ]
    elements.append(
        f'<path d="{" ".join(segments)}" fill="none" stroke="var(--c-blue)" stroke-width="2.5" stroke-dasharray="4,3"/>'
    )
    for cx, cy, burn, q, m in pts:
        short_id = _model_id(m, "model").removeprefix("opencode-go/")
        elements.append(
            f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="5.5" fill="var(--bg)" stroke="var(--c-blue)" stroke-width="2.5"/>'
        )
        elements.append(
            _svg_text(
                cx + 8,
                cy - 6,
                f"{short_id} ({burn:.1f}%, {q:.1f})",
                font_size=11,
                weight="600",
                fill="var(--text)",
            )
        )
    return elements


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
        _svg_text(
            400,
            30,
            "Live Pareto Frontier: Effective Quality vs Quota Burn %",
            font_size=15,
            weight="bold",
            fill="var(--text)",
            anchor="middle",
        ),
        *_pareto_bands(ml, pw, to_y, y_max, heavy_cutoff, balanced_cutoff),
        *_pareto_grid_and_axes(ml, mt, pw, ph, x_max, y_max, to_x, to_y),
        *_pareto_points_and_frontier(pareto_models, rank_models, to_x, to_y, profile, weights),
        "</svg>",
    ]
    return "\n".join(svg)


def _render_catalog_ranking_svg(rank_models: list[dict[str, Any]]) -> str:
    """Render the horizontal bar breakdown SVG for top ranked catalog models."""
    top_models = rank_models[:12]
    row_h, header_h, footer_h = 42, 55, 35
    svg_h = header_h + len(top_models) * row_h + footer_h
    c_ml, c_mr = 190, 70
    bar_max_w = 800 - c_ml - c_mr

    svg = [
        *_svg_preamble(800, svg_h),
        _svg_text(
            30,
            28,
            "Ranked Model Catalog: Benchmark Scores & Quota Burn",
            font_size=15,
            weight="bold",
            fill="var(--text)",
        ),
        *_svg_legend(
            (
                (480, "var(--c-blue)", "AA IQ"),
                (560, "var(--c-green)", "Engineering"),
                (665, "var(--c-amber)", "Quota Burn %"),
            ),
            y=18,
        ),
    ]

    for i, m in enumerate(top_models):
        y_base = header_h + i * row_h
        short_id = _model_id(m).removeprefix("opencode-go/")
        rank_val = m.get("rank")
        rank_s = f"#{rank_val}" if rank_val is not None else f"#{i + 1}"

        svg.append(
            _svg_text(
                c_ml - 12,
                y_base + 22,
                f"{rank_s} {short_id}",
                font_size=12,
                weight="500",
                fill="var(--text)",
                anchor="end",
            )
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
            width = max(0.0, min(bar_max_w, (val / 100.0) * bar_max_w))
            svg.append(_svg_rect(c_ml, y_pos, width, 7, fill, rx=3))
            svg.append(
                _svg_text(c_ml + width + 6, y_pos + 7, label, font_size=9, fill="var(--text-muted)")
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
            if (allow := _as_float(ex.get("monthly_allowance"))) is not None:
                all_vals.append(allow)
            if (burn := _model_burn_pct(ex)) is not None:
                all_vals.append(burn)
    p_max_val = max(all_vals) * 1.1

    svg = [
        *_svg_preamble(800, 360),
        _svg_text(
            400,
            28,
            "Multi-Plan Comparison: Monthly Allowance ($) vs Quota Burn (%)",
            font_size=15,
            weight="bold",
            fill="var(--text)",
            anchor="middle",
        ),
        *_svg_legend(
            (
                (280, "var(--c-blue)", "Allowance ($)"),
                (420, "var(--c-amber)", "Quota Burn %"),
            ),
            y=40,
        ),
    ]

    tier_w = p_pw / len(PAID_TIERS)
    plan_slot_w = (tier_w * 0.88) / max(1, len(plans))
    bw = (plan_slot_w * 0.8) / 2
    for t_idx, tier in enumerate(PAID_TIERS):
        tier_x = p_ml + t_idx * tier_w
        svg.append(
            _svg_text(
                tier_x + tier_w / 2,
                345,
                tier,
                font_size=13,
                weight="600",
                fill="var(--text)",
                anchor="middle",
            )
        )

        for p_idx, p_name in enumerate(plans):
            ex = (plan_payloads[p_name].get("explanations") or {}).get(tier) or {}
            allow_f = _as_float(ex.get("monthly_allowance")) or 0.0
            burn_f = _model_burn_pct(ex) or 0.0

            slot_x = tier_x + (tier_w * 0.06) + p_idx * plan_slot_w
            h_allow = max(0.0, (allow_f / p_max_val) * p_ph)
            h_burn = max(0.0, (min(burn_f, p_max_val) / p_max_val) * p_ph)

            svg.append(_svg_rect(slot_x, p_mt + p_ph - h_allow, bw, h_allow, "var(--c-blue)", rx=2))
            svg.append(
                _svg_rect(slot_x + bw + 2, p_mt + p_ph - h_burn, bw, h_burn, "var(--c-amber)", rx=2)
            )
            svg.append(
                _svg_text(
                    slot_x + bw,
                    p_mt + p_ph + 14,
                    p_name,
                    font_size=9,
                    fill="var(--text-muted)",
                    anchor="middle",
                )
            )

    svg.append("</svg>")
    return "\n".join(svg)


def _write_svg(charts_dir: Path, filename: str, content: str) -> str:
    (charts_dir / filename).write_text(content, encoding="utf-8")
    return filename


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
    specs: list[tuple[str, str]] = [
        (
            "pareto-frontier.svg",
            _render_pareto_frontier_svg(
                pareto_models,
                rank_models,
                top_quality,
                heavy_cutoff,
                balanced_cutoff,
                profile=profile,
                weights=weights,
            ),
        ),
        ("catalog-ranking.svg", _render_catalog_ranking_svg(rank_models)),
    ]
    if plan_payloads:
        specs.append(
            ("multi-plan-comparison.svg", _render_multi_plan_comparison_svg(plan_payloads))
        )
    return [_write_svg(charts_dir, name, content) for name, content in specs]
