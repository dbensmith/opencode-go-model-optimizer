from opencode_go_model_optimizer import numbers as n


def test_numeric_helpers():
    assert n.coerce_nested_float(None) == 0.0 and n.finite_float(None) is None
    assert n.coerce_nested_float(True) == 0.0 and n.finite_float(True) is None
    assert n.coerce_nested_float("unlimited") == 0.0 and n.finite_float("unlimited") is None
    assert n.parse_inf_float("unlimited") == float("inf")
    assert n.parse_float("3.14") == n.finite_float("3.14") == n.coerce_nested_float("3.14") == 3.14
    assert n.coerce_nested_float("n/a") == 0.0 and n.finite_float("n/a") is None
    assert n.coerce_nested_float({"price": 5.0}) == 5.0
    assert n.coerce_nested_float({"price": "bad", "default": 1.25}) == 0.0
    assert n.coerce_nested_float({"key": "bad", "another": 7.5}) == 7.5
    assert n.finite_float(float("inf")) is None and n.finite_float(float("nan")) is None
    assert n.serialize_inf_float(float("inf")) == "unlimited" and n.serialize_inf_float(1.5) == 1.5
    assert n.serialize_inf_float(None) is None
