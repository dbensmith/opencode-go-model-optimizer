"""Tests for shared numeric coercion helpers."""

import math

import pytest

from opencode_go_model_optimizer.numbers import (
    coerce_nested_float,
    finite_float,
    parse_float,
    parse_inf_float,
    serialize_inf_float,
    to_float,
)


def test_to_float_accepts_booleans_like_builtin_float():
    assert to_float(True) == 1.0
    assert to_float(False) == 0.0


@pytest.mark.parametrize(("value", "error"), [(None, TypeError), ("n/a", ValueError)])
def test_to_float_propagates_conversion_errors(value, error):
    with pytest.raises(error):
        to_float(value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [(1, 1.0), (1.5, 1.5), (" 3.14 ", 3.14), ("1e3", 1000.0), (b"2.5", 2.5)],
)
def test_parse_float_accepts_numeric_values(value, expected):
    assert parse_float(value) == expected


@pytest.mark.parametrize("value", [None, True, False, "", "n/a", "unlimited", [], {}])
def test_parse_float_returns_default_for_invalid_values(value):
    assert parse_float(value, default=-1.0) == -1.0


@pytest.mark.parametrize("value", [math.inf, -math.inf, "inf", "-inf"])
def test_parse_float_preserves_infinities(value):
    assert parse_float(value) == float(value)


def test_parse_float_preserves_nan():
    assert math.isnan(parse_float("nan"))


def test_parse_float_propagates_overflow_error():
    with pytest.raises(OverflowError):
        parse_float(10**10000)


@pytest.mark.parametrize(("value", "expected"), [(2, 2.0), ("2.5", 2.5), (b"3", 3.0)])
def test_finite_float_accepts_numeric_values(value, expected):
    assert finite_float(value) == expected


@pytest.mark.parametrize(
    "value",
    [None, True, False, "n/a", "unlimited", [], {}, math.nan, math.inf, -math.inf, "nan", "inf"],
)
def test_finite_float_rejects_invalid_or_non_finite_values(value):
    assert finite_float(value) is None


@pytest.mark.parametrize("value", ["unlimited", " INF ", "+inf", "Infinity"])
def test_parse_inf_float_accepts_infinity_aliases(value):
    assert parse_inf_float(value) == math.inf


@pytest.mark.parametrize(("value", "expected"), [("3.14", 3.14), ("-inf", -math.inf), (2, 2.0)])
def test_parse_inf_float_accepts_other_numeric_values(value, expected):
    assert parse_inf_float(value) == expected


@pytest.mark.parametrize("value", [None, True, False, "n/a", "unlimited-ish", [], {}])
def test_parse_inf_float_returns_default_for_invalid_values(value):
    assert parse_inf_float(value, default=-1.0) == -1.0


def test_parse_inf_float_preserves_nan():
    assert math.isnan(parse_inf_float("nan"))


@pytest.mark.parametrize("value", [None, True, False, "not-a-number", "unlimited", [], {}])
def test_coerce_nested_float_returns_zero_for_invalid_values(value):
    assert coerce_nested_float(value) == 0.0


@pytest.mark.parametrize(("value", "expected"), [(42, 42.0), ("3.14", 3.14), (math.inf, math.inf)])
def test_coerce_nested_float_accepts_scalars(value, expected):
    assert coerce_nested_float(value) == expected


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ({"price": 5.0}, 5.0),
        ({"default": 1.25}, 1.25),
        ({"standard": 2.5}, 2.5),
        ({"standard": 2.5, "default": 1.25, "price": 5.0}, 5.0),
        ({"price": True, "default": 1.25}, 1.25),
        ({"key": "invalid", "another": 7.5}, 7.5),
        ({"all": "invalid"}, 0.0),
        ({"price": "invalid", "default": 1.25}, 0.0),
        ({"default": {"price": 2.0}}, 0.0),
    ],
)
def test_coerce_nested_float_preserves_catalogue_dict_rules(value, expected):
    assert coerce_nested_float(value) == expected


def test_coerce_nested_float_preserves_nan():
    assert math.isnan(coerce_nested_float("nan"))


@pytest.mark.parametrize("value", [math.inf, -math.inf])
def test_serialize_inf_float_uses_unlimited(value):
    assert serialize_inf_float(value) == "unlimited"


@pytest.mark.parametrize("value", [None, 0, 1.5, True, "unlimited"])
def test_serialize_inf_float_leaves_other_values_unchanged(value):
    assert serialize_inf_float(value) is value


def test_serialize_inf_float_leaves_nan_unchanged():
    assert math.isnan(serialize_inf_float(math.nan))
