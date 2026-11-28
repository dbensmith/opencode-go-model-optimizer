"""Modular rendering helpers for optimizer GitHub issues, Docsify pages, SVG charts, and PR comments."""

from .analysis import render_analysis_markdown
from .charts import generate_analysis_svgs
from .comments import render_ci_comment_markdown, render_dual_optimizer_output_comment
from .data_page import render_data_markdown
from .issue import render_dual_issue_markdown

__all__ = [
    "generate_analysis_svgs",
    "render_analysis_markdown",
    "render_ci_comment_markdown",
    "render_data_markdown",
    "render_dual_issue_markdown",
    "render_dual_optimizer_output_comment",
]
