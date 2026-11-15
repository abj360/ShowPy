#!/usr/bin/env python3

"""
test_units.py --- tests unit conversion, unit registries and angle helpers

Contains:
    test_*(): one behavior of showpy.core.units each
    test_regression_default_units_cannot_be_mutated: refuses in-place edits to the process-wide
        default registry
    test_regression_zero_factor_registry_never_divides_by_zero: rejects a zero unit factor with a
        ShowPyError
    test_regression_register_unit_refuses_to_redefine_meters: refuses to redefine the base unit `m`
    test_regression_register_unit_rejects_infinite_size: rejects an infinite unit size
    test_regression_register_unit_rejects_an_empty_name: rejects an empty unit name
    test_regression_register_unit_bad_size_is_not_an_unknown_ref: reports a bad size as a schema
        problem, not an unknown reference
    test_regression_round_to_accepts_numpy_scalars: rounds a numpy float like a Python float
    test_regression_round_to_rejects_infinity_cleanly: rejects an infinite value with a ShowPyError,
        not decimal.InvalidOperation
    test_regression_round_to_rejects_zero_precision_cleanly: rejects a zero precision with a
        ShowPyError, not decimal.InvalidOperation
    test_regression_round_to_handles_bools_cleanly: rounds a bool to 1.0 or rejects it, never
        leaking decimal.InvalidOperation
    test_regression_round_to_never_returns_negative_zero: returns 0.0, not -0.0, for a small
        negative value
    test_regression_normalize_angle_never_returns_negative_zero: returns 0.0, not -0.0, for -0.0
    test_regression_normalize_angle_rejects_infinity_cleanly: rejects an infinite angle with a
        ShowPyError, not ValueError
    test_regression_normalize_angle_rejects_nan: rejects a NaN angle instead of passing it through
"""

import math

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from showpy.core.errors import ShowPyError, UnknownRefError, ZeroLengthError
from showpy.core.units import (
    DEFAULT_UNITS,
    UnitRegistry,
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


@pytest.mark.defect("UP-9.3-39")
@pytest.mark.xfail(strict=True, reason="UP-9.3-39")
def test_regression_default_units_cannot_be_mutated() -> None:
    """Refuses in-place edits to the process-wide default registry (audit 9.3)."""
    try:
        with pytest.raises((TypeError, ShowPyError)):
            DEFAULT_UNITS.meters_per_unit["hack"] = 9.0
    finally:
        DEFAULT_UNITS.meters_per_unit.pop("hack", None)


@pytest.mark.defect("UP-9.3-40")
@pytest.mark.xfail(strict=True, reason="UP-9.3-40")
def test_regression_zero_factor_registry_never_divides_by_zero() -> None:
    """Rejects a zero unit factor with a ShowPyError (audit 9.3)."""
    with pytest.raises(ShowPyError):
        convert(1.0, "m", "zero", UnitRegistry(meters_per_unit={"m": 1.0, "zero": 0.0}))


@pytest.mark.defect("UP-9.3-41")
@pytest.mark.xfail(strict=True, reason="UP-9.3-41")
def test_regression_register_unit_refuses_to_redefine_meters() -> None:
    """Refuses to redefine the base unit `m` (audit 9.3)."""
    with pytest.raises(ShowPyError):
        register_unit(DEFAULT_UNITS, "m", 2.0)


def test_regression_register_unit_rejects_infinite_size() -> None:
    """Rejects an infinite unit size (audit 9.3)."""
    with pytest.raises(ShowPyError):
        register_unit(DEFAULT_UNITS, "far", math.inf)


@pytest.mark.defect("UP-9.3-43")
@pytest.mark.xfail(strict=True, reason="UP-9.3-43")
def test_regression_register_unit_rejects_an_empty_name() -> None:
    """Rejects an empty unit name (audit 9.3)."""
    with pytest.raises(ShowPyError):
        register_unit(DEFAULT_UNITS, "", 1.0)


@pytest.mark.defect("UP-9.3-44")
@pytest.mark.xfail(strict=True, reason="UP-9.3-44")
def test_regression_register_unit_bad_size_is_not_an_unknown_ref() -> None:
    """Reports a bad size as a schema problem, not an unknown reference (audit 9.3)."""
    with pytest.raises(ShowPyError) as excinfo:
        register_unit(DEFAULT_UNITS, "bad", 0.0)
    assert not isinstance(excinfo.value, UnknownRefError)


@pytest.mark.defect("UP-9.3-45")
@pytest.mark.xfail(strict=True, reason="UP-9.3-45")
def test_regression_round_to_accepts_numpy_scalars() -> None:
    """Rounds a numpy float like a Python float (audit 9.3)."""
    assert approx_equal(round_to(np.float64(2.345)), 2.35)


@pytest.mark.defect("UP-9.3-46")
@pytest.mark.xfail(strict=True, reason="UP-9.3-46")
def test_regression_round_to_rejects_infinity_cleanly() -> None:
    """Rejects an infinite value with a ShowPyError, not decimal.InvalidOperation (audit 9.3)."""
    with pytest.raises(ShowPyError):
        round_to(math.inf)


@pytest.mark.defect("UP-9.3-47")
@pytest.mark.xfail(strict=True, reason="UP-9.3-47")
def test_regression_round_to_rejects_zero_precision_cleanly() -> None:
    """Rejects a zero precision with a ShowPyError, not decimal.InvalidOperation (audit 9.3)."""
    with pytest.raises(ShowPyError):
        round_to(1.0, 0.0)


@pytest.mark.defect("UP-9.3-48")
@pytest.mark.xfail(strict=True, reason="UP-9.3-48")
def test_regression_round_to_handles_bools_cleanly() -> None:
    """Rounds a bool to 1.0 or rejects it, never leaking decimal.InvalidOperation (audit 9.3).

    The audit allows reject or accept; an accepted result must be the numeric value.
    """
    try:
        rounded = round_to(True)
    except ShowPyError:
        return
    assert approx_equal(rounded, 1.0)


@pytest.mark.defect("UP-9.3-49")
@pytest.mark.xfail(strict=True, reason="UP-9.3-49")
def test_regression_round_to_never_returns_negative_zero() -> None:
    """Returns 0.0, not -0.0, for a small negative value (audit 9.3)."""
    assert math.copysign(1.0, round_to(-0.001)) > 0


@pytest.mark.defect("UP-9.3-50")
@pytest.mark.xfail(strict=True, reason="UP-9.3-50")
def test_regression_normalize_angle_never_returns_negative_zero() -> None:
    """Returns 0.0, not -0.0, for -0.0 (audit 9.3)."""
    assert math.copysign(1.0, normalize_angle(-0.0)) > 0


@pytest.mark.defect("UP-9.3-51")
@pytest.mark.xfail(strict=True, reason="UP-9.3-51")
def test_regression_normalize_angle_rejects_infinity_cleanly() -> None:
    """Rejects an infinite angle with a ShowPyError, not ValueError (audit 9.3)."""
    with pytest.raises(ShowPyError):
        normalize_angle(math.inf)


@pytest.mark.defect("UP-9.3-52")
@pytest.mark.xfail(strict=True, reason="UP-9.3-52")
def test_regression_normalize_angle_rejects_nan() -> None:
    """Rejects a NaN angle instead of passing it through (audit 9.3)."""
    with pytest.raises(ShowPyError):
        normalize_angle(math.nan)
