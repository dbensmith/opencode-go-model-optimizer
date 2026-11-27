"""Formatters for catalog ranking outputs."""

import json
from collections.abc import Callable

from opencode_go_model_optimizer.formatters.table import (
    _append_column,
    _count,
    _fmt_allowance,
    _fmt_context,
    _fmt_output_cost,
    _multiplier_cell,
    _privacy_cell,
    _requests_cell,
    render_table,
)
from opencode_go_model_optimizer.ingest.models import NormalizedModel


def format_rank_table(
    ranked_models: list[NormalizedModel],
    mode: str = "allowance",
) -> str:
    """Format full catalog ranking as an ASCII table."""
    is_pareto = mode == "pareto"
    headers = ["Rank", "Model ID", "Tier"]
    if is_pareto:
        headers.extend(["BPE", "IQ", "Burn %", "Allowance", "Output Cost", "Release"])
    else:
        headers.extend(["Allowance", "Output Cost", "Release Date", "Context", "Status"])

    rows: list[list[str]] = []
    for m in ranked_models:
        row = [str(m.rank or "-"), m.id, m.tier or "-"]
        allow_str = _fmt_allowance(m)
        cost_str = _fmt_output_cost(m)
        release_str = m.release_date or "-"
        if is_pareto:
            row.extend(
                [
                    f"{m.bpe:.3f}" if m.bpe is not None else "-",
                    f"{m.aa_iq:.1f}" if m.aa_iq is not None else "-",
                    f"{m.composite_burn_pct:.1f}%" if m.composite_burn_pct is not None else "-",
                    allow_str,
                    cost_str,
                    release_str,
                ]
            )
        else:
            row.extend([allow_str, cost_str, release_str, _fmt_context(m), m.status])
        rows.append(row)

    optional_columns: tuple[
        tuple[str, Callable[[NormalizedModel], bool], Callable[[NormalizedModel], str]],
        ...,
    ] = (
        ("Req/month", lambda m: m.request_estimate is not None, _requests_cell),
        ("Privacy", lambda m: m.privacy is not None, _privacy_cell),
        ("Price x", lambda m: m.price_multiplier is not None, _multiplier_cell),
    )
    for header, has_value, format_cell in optional_columns:
        if any(has_value(m) for m in ranked_models):
            _append_column(headers, rows, header, (format_cell(m) for m in ranked_models))

    return render_table(headers, rows)


def format_rank_json(ranked_models: list[NormalizedModel]) -> str:
    """Format full catalog ranking as JSON array."""
    data = []
    for m in ranked_models:
        item = m.model_dump(mode="json", exclude_none=True)
        for empty_key in ("canonical_id", "multipliers", "request_estimates"):
            if not item.get(empty_key):
                item.pop(empty_key, None)
        if m.request_estimate is not None:
            item["requests_per_month"] = _count(m.request_estimate.per_month)
        if m.price_multiplier is not None:
            item["price_multiplier"] = round(m.price_multiplier, 4)
        data.append(item)
    return json.dumps(data, indent=2)
