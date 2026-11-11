"""Data models for OpenCode Go Model Optimizer."""

import math
from typing import Any

from pydantic import BaseModel, Field, PrivateAttr, field_serializer, field_validator


class TokenCost(BaseModel):
    """Token pricing in USD per 1M tokens."""

    input: float = 0.0
    output: float = 0.0
    cache_read: float = 0.0
    cache_write: float = 0.0


class PriceMultiplier(BaseModel):
    """A conditional price multiplier from the catalogue (peak hours, promo expiry)."""

    factor: float = 1.0
    when: dict[str, Any] = Field(default_factory=dict)


class ModelLimit(BaseModel):
    """Context and output token limits."""

    context: int = 0
    output: int = 0


class Privacy(BaseModel):
    """Data-handling and region requirements from the docs Privacy section."""

    trains_on_data: bool | None = None
    retention_days: int | None = None  # None: "Not ZDR" or unknown
    zdr: bool | None = None  # True only for "0 days"
    requires_global_region: bool = False
    note: str | None = None
    agreement_valid_until: str | None = None  # ISO date


class RequestEstimate(BaseModel):
    """Docs-published request estimates for one model on one plan (inf = unlimited)."""

    per_5h: float = 0.0
    per_week: float = 0.0
    per_month: float = 0.0

    @field_validator("per_5h", "per_week", "per_month", mode="before")
    @classmethod
    def _parse_count(cls, value: Any) -> float:
        if isinstance(value, str) and value.strip().lower() in ("unlimited", "inf", "infinity"):
            return float("inf")
        try:
            return float(value)
        except TypeError, ValueError:
            return 0.0

    @field_serializer("per_5h", "per_week", "per_month", when_used="json")
    def _serialize_count(self, value: float) -> float | str:
        return "unlimited" if math.isinf(value) else value


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
    multipliers: list[PriceMultiplier] = Field(default_factory=list)
    privacy: Privacy | None = None
    request_estimates: dict[str, RequestEstimate] = Field(default_factory=dict)

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

    # Active plan's request estimate, set by apply_plan (not serialized).
    _request_estimate: RequestEstimate | None = PrivateAttr(default=None)

    # Effective multiplier applied to ``cost`` for this run (not serialized).
    _price_multiplier: float | None = PrivateAttr(default=None)

    @property
    def request_estimate(self) -> RequestEstimate | None:
        """Request estimate for the plan last applied with ``apply_plan``, if published."""
        return self._request_estimate

    @request_estimate.setter
    def request_estimate(self, value: RequestEstimate | None) -> None:
        self._request_estimate = value

    @property
    def price_multiplier(self) -> float | None:
        """Effective price multiplier applied to ``cost``, or None when none was applied."""
        return self._price_multiplier

    @price_multiplier.setter
    def price_multiplier(self, value: float | None) -> None:
        self._price_multiplier = value

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
        except ValueError, TypeError:
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
    benchmark_source: str = "none"  # "aa_live", "reference:<fetched_at>", or "none"


class Snapshot(BaseModel):
    """Normalized, timestamped snapshot of OpenCode Go models and pricing."""

    schema_version: int = 1
    fetched_at: str
    plan: str = "go"
    models: list[NormalizedModel] = Field(default_factory=list)
    metadata: SnapshotMetadata = Field(default_factory=SnapshotMetadata)
    warnings: list[str] = Field(default_factory=list)
