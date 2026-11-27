"""Shared builders for test data."""

from typing import Any

from opencode_go_model_optimizer.ingest.models import NormalizedModel


def make_model(id: str = "m", **overrides: Any) -> NormalizedModel:
    """Build a model while retaining NormalizedModel defaults for unspecified fields."""
    return NormalizedModel(id=id, **overrides)
