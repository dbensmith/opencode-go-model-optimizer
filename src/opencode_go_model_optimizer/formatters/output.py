"""Backward-compatible re-export facade for output formatters."""

from opencode_go_model_optimizer.formatters.diff import (
    _format_diff_value as _format_diff_value,
)
from opencode_go_model_optimizer.formatters.diff import (
    format_diff_json,
    format_diff_table,
)
from opencode_go_model_optimizer.formatters.rank import (
    format_rank_json,
    format_rank_table,
)
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
from opencode_go_model_optimizer.formatters.table import (
    _append_column as _append_column,
)
from opencode_go_model_optimizer.formatters.table import (
    _burn_basis as _burn_basis,
)
from opencode_go_model_optimizer.formatters.table import (
    _count as _count,
)
from opencode_go_model_optimizer.formatters.table import (
    _fmt_allowance as _fmt_allowance,
)
from opencode_go_model_optimizer.formatters.table import (
    _fmt_context as _fmt_context,
)
from opencode_go_model_optimizer.formatters.table import (
    _fmt_output_cost as _fmt_output_cost,
)
from opencode_go_model_optimizer.formatters.table import (
    _model_map as _model_map,
)
from opencode_go_model_optimizer.formatters.table import (
    _multiplier_cell as _multiplier_cell,
)
from opencode_go_model_optimizer.formatters.table import (
    _next_change as _next_change,
)
from opencode_go_model_optimizer.formatters.table import (
    _privacy_cell as _privacy_cell,
)
from opencode_go_model_optimizer.formatters.table import (
    _render_table as _render_table,
)
from opencode_go_model_optimizer.formatters.table import (
    _requests_cell as _requests_cell,
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
