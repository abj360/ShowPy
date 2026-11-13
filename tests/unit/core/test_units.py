#!/usr/bin/env python3

"""
test_units.py --- tests unit conversion, unit registries and angle helpers
"""

import math

import pytest
from hypothesis import given
from hypothesis import strategies as st

from showpy.core.errors import UnknownRefError, ZeroLengthError
from showpy.core.units import (
    DEFAULT_UNITS,
    angle_diff,
    convert,
    facing_vector,
    normalize_angle,
    register_unit,
    round_to,
    units,
    vector_facing,
)
from showpy.core.util import approx_equal


def test_convert_feet_to_meters() -> None:
    assert approx_equal(convert(1.0, "ft", "m"), 0.3048)


def test_convert_yards_to_inches() -> None:
    assert approx_equal(convert(1.0, "yd", "in"), 36.0)


def test_unknown_unit_raises_with_known_list_in_hint() -> None:
    with pytest.raises(UnknownRefError) as info:
        convert(1.0, "furlong", "m")
    assert "m" in info.value.hint


def test_register_unit_returns_new_registry_without_mutating_default() -> None:
    marching = register_unit(DEFAULT_UNITS, "step85", 0.5715)
    assert approx_equal(convert(8.0, "step85", "m", marching), 4.572)
    assert "step85" not in units()


def test_register_unit_rejects_non_positive_scale() -> None:
    with pytest.raises(UnknownRefError):
        register_unit(DEFAULT_UNITS, "bad", 0.0)


def test_units_lists_builtins() -> None:
    assert {"m", "cm", "mm", "in", "ft", "yd"} <= set(units())


@pytest.mark.parametrize(("a", "expected"), [(370.0, 10.0), (-90.0, 270.0), (360.0, 0.0)])
def test_normalize_angle(a: float, expected: float) -> None:
    assert approx_equal(normalize_angle(a), expected)


@pytest.mark.parametrize(
    ("a", "b", "expected"), [(10.0, 350.0, -20.0), (350.0, 10.0, 20.0), (0.0, 180.0, 180.0)]
)
def test_angle_diff_is_signed_shortest_turn(a: float, b: float, expected: float) -> None:
    assert approx_equal(angle_diff(a, b), expected)


def test_facing_zero_points_at_audience() -> None:
    assert approx_equal(facing_vector(0.0), (0.0, -1.0))
    assert approx_equal(facing_vector(90.0), (1.0, 0.0))


@given(st.floats(min_value=0.0, max_value=359.999))
def test_facing_vector_round_trips(theta: float) -> None:
    back = vector_facing(facing_vector(theta))
    assert abs(angle_diff(theta, back)) < 1e-6


def test_vector_facing_rejects_zero_vector() -> None:
    with pytest.raises(ZeroLengthError):
        vector_facing((0.0, 0.0))


@given(st.floats(min_value=-1e6, max_value=1e6), st.floats(min_value=-1e6, max_value=1e6))
def test_angle_diff_in_half_open_range(a: float, b: float) -> None:
    d = angle_diff(a, b)
    assert -180.0 < d <= 180.0
    assert approx_equal(math.cos(math.radians(a + d)), math.cos(math.radians(b)), tol=1e-6)


@pytest.mark.parametrize(
    ("value", "precision", "expected"),
    [(2.345, 0.01, 2.35), (-2.345, 0.01, -2.35), (2.5, 1.0, 3.0), (0.125, 0.25, 0.25)],
)
def test_round_to_half_away_from_zero(value: float, precision: float, expected: float) -> None:
    assert round_to(value, precision) == expected
