"""Shared builders for test data."""

from typing import Any

from opencode_go_model_optimizer.ingest.models import ModelLimit, NormalizedModel, TokenCost


def make_model(
    id: str = "m",
    *,
    input: float | None = None,
    output: float | None = None,
    cache_read: float | None = None,
    context: int | None = None,
    max_output: int | None = None,
    **overrides: Any,
) -> NormalizedModel:
    """Build a model, optionally shortcutting its nested cost and limit values."""
    cost = {"input": input, "output": output, "cache_read": cache_read}
    limit = {"context": context, "output": max_output}
    cost = {key: value for key, value in cost.items() if value is not None}
    limit = {key: value for key, value in limit.items() if value is not None}
    if cost and "cost" in overrides:
        raise ValueError("cost shortcuts cannot be combined with cost=")
    if limit and "limit" in overrides:
        raise ValueError("limit shortcuts cannot be combined with limit=")
    if cost:
        overrides["cost"] = TokenCost(**cost)
    if limit:
        overrides["limit"] = ModelLimit(**limit)
    return NormalizedModel(id=id, **overrides)
