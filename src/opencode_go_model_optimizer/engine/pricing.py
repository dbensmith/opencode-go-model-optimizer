"""Time-aware pricing: evaluate catalogue price multipliers (peak hours, promo expiry)."""

import contextlib
import functools
from collections.abc import Callable, Iterator
from datetime import UTC, datetime, time, timedelta
from math import gcd
from typing import Any

from opencode_go_model_optimizer.ingest.models import NormalizedModel, PriceMultiplier

PRICE_BASES = ("now", "blended", "worst", "list")
_DAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")
_OPS: dict[str, Callable[[Any, Any], bool]] = {
    "gte": lambda a, b: a >= b,
    "gt": lambda a, b: a > b,
    "lte": lambda a, b: a <= b,
    "lt": lambda a, b: a < b,
}


class UnsupportedConditionError(ValueError):
    """Raised for a multiplier condition this version cannot interpret."""


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=UTC) if dt.tzinfo is None else dt.astimezone(UTC)


@functools.lru_cache(maxsize=256)
def _parse_hhmm_str(value: str) -> int:
    try:
        t = time.fromisoformat(value)
    except ValueError as exc:
        raise UnsupportedConditionError(f"bad time {value!r}") from exc
    return t.hour * 3600 + t.minute * 60 + t.second


def _hhmm(value: Any) -> int:
    """Parse "HH:MM" into seconds since midnight."""
    return _parse_hhmm_str(str(value))


def _compare(spec: Any, actual: Any, parse: Callable[[Any], Any]) -> bool:
    if not isinstance(spec, dict) or not spec:
        raise UnsupportedConditionError(f"bad comparison {spec!r}")
    results = []
    for op, bound in spec.items():
        if op not in _OPS:
            raise UnsupportedConditionError(f"unknown operator {op!r}")
        results.append(_OPS[op](actual, parse(bound)))
    return all(results)


@functools.lru_cache(maxsize=256)
def _parse_date_str(value: str) -> Any:
    try:
        return datetime.fromisoformat(value).date()
    except ValueError as exc:
        raise UnsupportedConditionError(f"bad date {value!r}") from exc


def _parse_date(value: Any) -> Any:
    return _parse_date_str(str(value))


def _eval(cond: Any, dt: datetime) -> bool:
    """Evaluate a condition fully (no short-circuit) so unsupported parts always raise."""
    if not isinstance(cond, dict):
        raise UnsupportedConditionError(f"condition must be an object, got {cond!r}")
    results: list[bool] = []
    for key, spec in cond.items():
        if key == "utc_days":
            days = spec.get("in") if isinstance(spec, dict) and set(spec) == {"in"} else None
            if not isinstance(days, list) or any(d not in _DAYS for d in days):
                raise UnsupportedConditionError(f"bad utc_days {spec!r}")
            results.append(_DAYS[dt.weekday()] in days)
        elif key == "utc_hours":
            secs = dt.hour * 3600 + dt.minute * 60 + dt.second + dt.microsecond / 1e6
            results.append(_compare(spec, secs, _hhmm))
        elif key == "date":
            results.append(_compare(spec, dt.date(), _parse_date))
        elif key in ("all", "any"):
            if not isinstance(spec, list):
                raise UnsupportedConditionError(f"{key} needs a list")
            subs = [_eval(s, dt) for s in spec]
            results.append(all(subs) if key == "all" else any(subs))
        else:
            raise UnsupportedConditionError(f"unknown condition key {key!r}")
    return all(results)


def _factor(m: PriceMultiplier) -> float:
    if not m.factor > 0:
        raise UnsupportedConditionError(f"bad factor {m.factor!r}")
    return m.factor


def validate_multiplier(m: PriceMultiplier) -> str | None:
    """Return None if the multiplier is interpretable, else a reason string."""
    try:
        _factor(m)
        _eval(m.when, datetime(2000, 1, 1, tzinfo=UTC))
    except UnsupportedConditionError as exc:
        return str(exc)
    return None


def _valid_multipliers(m: NormalizedModel) -> list[PriceMultiplier]:
    """Return multipliers on ``m`` that pass validation."""
    return [mult for mult in m.multipliers if validate_multiplier(mult) is None]


def _eval_multipliers(valid_mults: list[PriceMultiplier], dt: datetime) -> float:
    """Product of factors of pre-validated multipliers matching at UTC ``dt``."""
    result = 1.0
    for mult in valid_mults:
        if _eval(mult.when, dt):
            result *= mult.factor
    return result


def multiplier_at(m: NormalizedModel, dt: datetime) -> float:
    """Product of the factors of all (valid) multipliers matching at UTC datetime ``dt``."""
    return _eval_multipliers(_valid_multipliers(m), _utc(dt))


def _minutes(cond: Any, out: set[int]) -> None:
    """Collect the minute-of-hour of every time boundary used in a condition."""
    if isinstance(cond, dict):
        for key, spec in cond.items():
            if key == "utc_hours" and isinstance(spec, dict):
                for bound in spec.values():
                    with contextlib.suppress(UnsupportedConditionError):
                        out.add((_hhmm(bound) // 60) % 60)
            elif key in ("all", "any") and isinstance(spec, list):
                for sub in spec:
                    _minutes(sub, out)


def _step_minutes(multipliers: list[PriceMultiplier]) -> int:
    """Grid step (minutes) aligned to every boundary: gcd of 60 and all boundary minutes."""
    minutes: set[int] = set()
    for mult in multipliers:
        _minutes(mult.when, minutes)
    step = 60
    for minute in minutes:
        step = gcd(step, minute)
    return step


def _segments(
    m: NormalizedModel, start: datetime, end: datetime
) -> Iterator[tuple[datetime, datetime, float]]:
    """Yield (seg_start, seg_end, multiplier) over [start, end).

    Conditions only change on the UTC grid of ``_step_minutes``, so each grid-aligned
    segment has a constant value, sampled at its midpoint.
    """
    valid_mults = _valid_multipliers(m)
    if not valid_mults:
        yield start, end, 1.0
        return
    step = timedelta(minutes=_step_minutes(valid_mults))
    midnight = start.replace(hour=0, minute=0, second=0, microsecond=0)
    cursor = start
    nxt = midnight + step * ((start - midnight) // step + 1)
    while cursor < end:
        seg_end = min(nxt, end)
        yield cursor, seg_end, _eval_multipliers(valid_mults, cursor + (seg_end - cursor) / 2)
        cursor, nxt = seg_end, nxt + step


def blended_multiplier(m: NormalizedModel, start: datetime, days: int = 7) -> float:
    """Time-weighted average multiplier over ``days`` days from ``start``."""
    start = _utc(start)
    end = start + timedelta(days=days)
    total = sum((e - s).total_seconds() * v for s, e, v in _segments(m, start, end))
    return total / (end - start).total_seconds()


def worst_multiplier(m: NormalizedModel, start: datetime, days: int = 7) -> float:
    """Highest multiplier in effect at any point within the next ``days`` days."""
    start = _utc(start)
    return max(v for _, _, v in _segments(m, start, start + timedelta(days=days)))


def next_price_change(
    m: NormalizedModel, dt: datetime, horizon_days: int = 60
) -> tuple[datetime, float] | None:
    """First future instant where the multiplier changes, with the new multiplier."""
    dt = _utc(dt)
    current: float | None = None
    for seg_start, _, value in _segments(m, dt, dt + timedelta(days=horizon_days)):
        if current is None:
            current = value
        elif abs(value - current) > 1e-12:
            return seg_start, value
    return None


def effective_multiplier(m: NormalizedModel, basis: str, at: datetime) -> float:
    """Multiplier to apply for a price basis (``list`` ignores multipliers)."""
    if basis == "list" or not m.multipliers:
        return 1.0
    if basis == "now":
        return multiplier_at(m, at)
    if basis == "worst":
        return worst_multiplier(m, at)
    if basis == "blended":
        return blended_multiplier(m, at)
    raise ValueError(f"unknown price basis {basis!r}; choose from {', '.join(PRICE_BASES)}")


def apply_price_basis(
    models: list[NormalizedModel], basis: str, at: datetime | None = None
) -> list[NormalizedModel]:
    """Return models with costs scaled by the effective multiplier.

    Models without multipliers (or with ``list``) are returned unchanged; others are copies
    carrying the effective multiplier in ``price_multiplier``.
    """
    if basis not in PRICE_BASES:
        raise ValueError(f"unknown price basis {basis!r}; choose from {', '.join(PRICE_BASES)}")
    when = _utc(at) if at is not None else datetime.now(UTC)
    out: list[NormalizedModel] = []
    for m in models:
        if basis == "list" or not m.multipliers:
            out.append(m)
            continue
        factor = effective_multiplier(m, basis, when)
        cost = m.cost.model_copy(
            update={
                "input": m.cost.input * factor,
                "output": m.cost.output * factor,
                "cache_read": m.cost.cache_read * factor,
                "cache_write": m.cost.cache_write * factor,
            }
        )
        copy = m.model_copy(update={"cost": cost})
        copy.price_multiplier = factor
        out.append(copy)
    return out
