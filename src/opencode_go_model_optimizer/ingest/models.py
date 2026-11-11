"""Data models for OpenCode Go Model Optimizer."""

from __future__ import annotations

import math
from typing import Any

from pydantic import BaseModel, Field, field_serializer, field_validator


class TokenCost(BaseModel):
    """Token pricing in USD per 1M tokens."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


class ModelLimit(BaseModel):
    """Context and output token limits."""

    context: int = 0
    output: int = 0


class NormalizedModel(BaseModel):
    """Unified representation of a model in the OpenCode Go catalog."""

    id: str
    provider_id: str = "opencode-go"
    full_id: str = ""
    name: str = ""
    status: str = "active"  # "active" or "deprecated"
    release_date: str = ""
    cost: TokenCost = Field(default_factory=TokenCost)
    limit: ModelLimit = Field(default_factory=ModelLimit)
    monthly_allowance: float | None = None  # USD limit (e.g. 15.0, 60.0, float('inf'))
    plan_allowances: dict[str, float] = Field(default_factory=dict)

    # Benchmark metrics (used in pareto mode)
    aa_iq: float | None = None
    aa_coding: float | None = None
    speed_tps: float | None = None

    # Classification flags
    is_preview: bool = False
    is_contributor: bool = False
    is_zero_cost: bool = False

    # Assigned tier: "heavy", "balanced", "light", "free"
    tier: str | None = None

    # Ranking & Pareto metrics
    rank: int | None = None
    bpe: float | None = None
    is_pareto: bool | None = None
    composite_burn_pct: float | None = None

    @field_validator("monthly_allowance", mode="before")
    @classmethod
    def validate_allowance(cls, value: Any) -> float | None:
        """Validate allowance, handling string 'unlimited' or float inf."""
        if value is None:
            return None
        if isinstance(value, str):
            val_lower = value.strip().lower()
            if val_lower in ("unlimited", "inf", "+inf", "infinity"):
                return float("inf")
        try:
            val = float(value)
            return val
        except (ValueError, TypeError):
            return None

    @field_serializer("monthly_allowance", when_used="json")
    def serialize_allowance(self, value: float | None) -> float | str | None:
        """Serialize float inf as string 'unlimited' for RFC 8259 JSON compliance."""
        if value is not None and math.isinf(value):
            return "unlimited"
        return value

    @field_validator("plan_allowances", mode="before")
    @classmethod
    def validate_plan_allowances(cls, value: Any) -> dict[str, float]:
        """Validate per-plan allowances mapping."""
        if not isinstance(value, dict):
            return {}
        result: dict[str, float] = {}
        for k, v in value.items():
            val = cls.validate_allowance(v)
            if val is not None:
                result[str(k)] = val
        return result

    @field_serializer("plan_allowances", when_used="json")
    def serialize_plan_allowances(self, value: dict[str, float]) -> dict[str, float | str]:
        """Serialize float inf as 'unlimited' for RFC 8259 JSON compliance."""
        return {k: ("unlimited" if math.isinf(v) else v) for k, v in value.items()}

    def model_post_init(self, context: Any, /) -> None:
        """Derive helper flags and identifiers if not already set."""
        if not self.full_id:
            if "/" in self.id:
                self.full_id = self.id
            else:
                self.full_id = f"{self.provider_id}/{self.id}"

        id_lower = self.id.lower()
        name_lower = self.name.lower()

        if (
            "preview" in id_lower
            or "preview" in name_lower
            or "experimental" in id_lower
            or "experimental" in name_lower
        ):
            self.is_preview = True

        if "contributor" in id_lower or "contributor" in name_lower:
            self.is_contributor = True

        if self.cost.input == 0.0 and self.cost.output == 0.0:
            self.is_zero_cost = True


class SnapshotMetadata(BaseModel):
    """Metadata describing a snapshot capture."""

    source_catalogue: str = ""
    source_docs: str = ""
    catalogue_count: int = 0
    docs_pricing_count: int = 0


class Snapshot(BaseModel):
    """Normalized, timestamped snapshot of OpenCode Go models and pricing."""

    schema_version: int = 1
    fetched_at: str
    plan: str = "go"
    models: list[NormalizedModel] = Field(default_factory=list)
    metadata: SnapshotMetadata = Field(default_factory=SnapshotMetadata)
