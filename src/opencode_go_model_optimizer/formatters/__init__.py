"""Output formatters for CLI outputs."""

from opencode_go_model_optimizer.formatters.diff import format_diff_json, format_diff_table
from opencode_go_model_optimizer.formatters.rank import format_rank_json, format_rank_table
from opencode_go_model_optimizer.formatters.select import (
    explain_tiers,
    format_select_compact,
    format_select_env,
    format_select_github_output,
    format_select_json,
    format_select_table,
    settings_payload,
    tier_display_names,
)
from opencode_go_model_optimizer.formatters.table import render_table

__all__ = [
    "explain_tiers",
    "format_diff_json",
    "format_diff_table",
    "format_rank_json",
    "format_rank_table",
    "format_select_compact",
    "format_select_env",
    "format_select_github_output",
    "format_select_json",
    "format_select_table",
    "render_table",
    "settings_payload",
    "tier_display_names",
]
