#!/usr/bin/env python3

"""
test_numbers.py --- tests plain-decimal number formatting and angle wrapping

Contains:
    test_format_number_*(): one formatting rule each
    test_format_number_never_writes_an_exponent: writes no exponent and stays within 1e-6 of input
    test_normalize_angle_*(): one wrapping rule each
"""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from showpy.core.errors import SchemaError
from showpy.core.numbers import format_number, normalize_angle

LIMIT = 1e15


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1234567.5, "1234567.5"),
        (3.0, "3"),
        (0.1, "0.1"),
        (-2.50, "-2.5"),
        (1e-7, "0"),
        (-1e-9, "0"),
        (1e15, "1000000000000000"),
        (0.0, "0"),
    ],
)
def test_format_number_writes_plain_decimals(value: float, expected: str) -> None:
    assert format_number(value) == expected


def test_format_number_honors_max_decimals() -> None:
    assert format_number(1 / 3, max_decimals=2) == "0.33"
    assert format_number(2.5, max_decimals=0) == "2"


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_format_number_rejects_non_finite(bad: float) -> None:
    with pytest.raises(SchemaError):
        format_number(bad)


@pytest.mark.parametrize("bad", [-1, 18])
def test_format_number_rejects_out_of_range_decimals(bad: int) -> None:
    with pytest.raises(SchemaError):
        format_number(1.0, max_decimals=bad)


@pytest.mark.parametrize("bad", ["1.0", None, True])
def test_format_number_rejects_non_numbers_and_bools(bad: object) -> None:
    with pytest.raises(SchemaError):
        format_number(bad)  # type: ignore[arg-type]


@given(st.floats(min_value=-LIMIT, max_value=LIMIT, allow_nan=False, allow_infinity=False))
def test_format_number_never_writes_an_exponent(value: float) -> None:
    text = format_number(value)
    assert "e" not in text.lower()
    assert math.isclose(float(text), value, rel_tol=1e-6, abs_tol=1e-6)


def test_normalize_angle_wraps_into_a_turn() -> None:
    assert normalize_angle(-90.0) == 270.0
    assert normalize_angle(720.0) == 0.0
    assert normalize_angle(360.0) == 0.0
    assert normalize_angle(-1e-20) == 0.0


@pytest.mark.parametrize("bad", [math.nan, math.inf, -math.inf])
def test_normalize_angle_rejects_non_finite(bad: float) -> None:
    with pytest.raises(SchemaError):
        normalize_angle(bad)
