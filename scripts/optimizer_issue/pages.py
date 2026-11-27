"""Backward-compatible facade re-exporting page renderers from focused submodules."""

from __future__ import annotations

from opencode_go_model_optimizer.engine.tiers import STANDARD_TIERS

from .analysis import (
    _quadrant_points,
    _render_free_matrix_section,
    _render_multi_plan_section,
    _render_quadrant_chart,
    render_analysis_markdown,
)
from .common import _render_ranked_catalog_rows, _render_report_header
from .data_page import render_data_markdown
from .issue import (
    _render_published_artifacts_section,
    _render_tier_explanations,
    _render_visual_tier_summary,
    render_issue_markdown,
)

__all__ = [
    "STANDARD_TIERS",
    "_quadrant_points",
    "_render_free_matrix_section",
    "_render_multi_plan_section",
    "_render_published_artifacts_section",
    "_render_quadrant_chart",
    "_render_ranked_catalog_rows",
    "_render_report_header",
    "_render_tier_explanations",
    "_render_visual_tier_summary",
    "render_analysis_markdown",
    "render_data_markdown",
    "render_issue_markdown",
]
