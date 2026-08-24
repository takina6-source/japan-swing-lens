import math

from scripts.export_web import clean


def test_clean_converts_nested_nan_to_json_null():
    value = {"a": float("nan"), "b": [1.0, float("inf")], "c": "○"}
    assert clean(value) == {"a": None, "b": [1.0, None], "c": "○"}


def test_clean_keeps_finite_numbers():
    assert math.isclose(clean(12.5), 12.5)
