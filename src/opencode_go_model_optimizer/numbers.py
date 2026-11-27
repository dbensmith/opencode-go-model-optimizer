"""Shared numeric coercion and infinity serialization helpers."""

import math
from typing import Any

_INF_STRINGS = frozenset({"unlimited", "inf", "+inf", "infinity"})


def _is_scalar(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float, str))


def parse_float(value: Any, *, default: float | None = None) -> float | None:
    """Parse a scalar float, rejecting booleans and returning ``default`` on failure."""
    if value is None or isinstance(value, bool):
        return default
    try:
        return float(value)
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
        return float(value)
    if isinstance(value, dict):
        for key in ("price", "default", "standard"):
            candidate = value.get(key)
            if _is_scalar(candidate):
                return coerce_nested_float(candidate)
        for candidate in value.values():
            if _is_scalar(candidate) and (parsed := parse_float(candidate)) is not None:
                return parsed
        return 0.0
    return parse_float(value, default=0.0)


def serialize_inf_float(value: float | None) -> float | str | None:
    """Serialize either float infinity as ``'unlimited'`` for JSON compliance."""
    if value is not None and isinstance(value, float) and math.isinf(value):
        return "unlimited"
    return value
