"""Tests for shared numeric coercion helpers."""

import math

import pytest

from opencode_go_model_optimizer import numbers


def assert_number(actual, expected):
    if isinstance(expected, float) and math.isnan(expected):
        assert actual is not None and math.isnan(actual)
    else:
        assert actual == expected


@pytest.mark.parametrize(
    ("value", "parsed", "finite", "inf", "nested"),
    [
        (None, -1.0, None, -1.0, 0.0),
        (True, -1.0, None, -1.0, 0.0),
        ("unlimited", -1.0, None, math.inf, 0.0),
        (" INF ", math.inf, None, math.inf, math.inf),
        ("nan", math.nan, None, math.nan, math.nan),
        ("inf", math.inf, None, math.inf, math.inf),
        ("-inf", -math.inf, None, -math.inf, -math.inf),
        ("3.14", 3.14, 3.14, 3.14, 3.14),
        (" 1e2 ", 100.0, 100.0, 100.0, 100.0),
        ("n/a", -1.0, None, -1.0, 0.0),
        ({"price": 5.0}, -1.0, None, -1.0, 5.0),
        ({"standard": 2.5, "default": 1.25, "price": 5.0}, -1.0, None, -1.0, 5.0),
        ({"price": "bad", "default": 1.25}, -1.0, None, -1.0, 0.0),
        ({"price": True, "default": 1.25}, -1.0, None, -1.0, 1.25),
        ({"key": "bad", "another": 7.5}, -1.0, None, -1.0, 7.5),
        ({"default": {"price": 2.0}}, -1.0, None, -1.0, 0.0),
    ],
)
def test_numeric_helpers(value, parsed, finite, inf, nested):
    assert_number(numbers.parse_float(value, default=-1.0), parsed)
    assert_number(numbers.finite_float(value), finite)
    assert_number(numbers.parse_inf_float(value, default=-1.0), inf)
    assert_number(numbers.coerce_nested_float(value), nested)


def test_serialize_inf_float():
    assert numbers.serialize_inf_float(math.inf) == "unlimited"
    assert numbers.serialize_inf_float(-math.inf) == "unlimited"
    assert numbers.serialize_inf_float(1.5) == 1.5
    assert numbers.serialize_inf_float(None) is None
    assert numbers.serialize_inf_float(True) is True
    assert numbers.serialize_inf_float("unlimited") == "unlimited"
    assert math.isnan(numbers.serialize_inf_float(math.nan))


def test_parse_float_propagates_overflow():
    with pytest.raises(OverflowError):
        numbers.parse_float(10**10000)
