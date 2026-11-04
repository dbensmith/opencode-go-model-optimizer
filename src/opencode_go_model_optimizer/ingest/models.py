"""Data models for OpenCode Go Model Optimizer."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


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
