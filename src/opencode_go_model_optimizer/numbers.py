"""Shared numeric coercion and infinity serialization helpers."""

import math
from typing import Any

_INF_STRINGS = frozenset({"unlimited", "inf", "+inf", "infinity"})


def to_float(value: Any) -> float:
    """Convert to float, propagating conversion errors."""
    return float(value)


def parse_float(value: Any, *, default: float | None = None) -> float | None:
    """Parse a scalar float, rejecting booleans and returning ``default`` on failure."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return to_float(value)
    except ValueError, TypeError:
        return default


def finite_float(value: Any) -> float | None:
    """Return a finite float, or ``None`` for invalid or non-finite values."""
    result = parse_float(value)
    return result if result is not None and math.isfinite(result) else None


def parse_inf_float(value: Any, *, default: float | None = None) -> float | None:
    """Parse a float, accepting unlimited/infinity aliases and preserving non-finite floats."""
    if isinstance(value, str) and value.strip().lower() in _INF_STRINGS:
        return float("inf")
    return parse_float(value, default=default)


def coerce_nested_float(value: Any) -> float:
    """Coerce catalogue price shapes to a float, returning ``0.0`` when unavailable."""
    if value is None or isinstance(value, bool):
        return 0.0
    if isinstance(value, (int, float)):
        return to_float(value)
    if isinstance(value, dict):
        for key in ("price", "default", "standard"):
            if (
                key in value
                and not isinstance(value[key], bool)
                and isinstance(value[key], (int, float, str))
            ):
                return coerce_nested_float(value[key])
        for item in value.values():
            if not isinstance(item, bool) and isinstance(item, (int, float, str)):
                parsed = parse_float(item)
                if parsed is not None:
                    return parsed
        return 0.0
    parsed = parse_float(value, default=0.0)
    return parsed if parsed is not None else 0.0


def serialize_inf_float(value: float | None) -> float | str | None:
    """Serialize either float infinity as ``'unlimited'`` for JSON compliance."""
    if value is not None and isinstance(value, float) and math.isinf(value):
        return "unlimited"
    return value


__all__ = [
    "coerce_nested_float",
    "finite_float",
    "parse_float",
    "parse_inf_float",
    "serialize_inf_float",
    "to_float",
]
