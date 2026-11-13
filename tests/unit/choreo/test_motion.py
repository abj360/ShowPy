#!/usr/bin/env python3

"""
test_motion.py --- tests easing curves, facing turns and the custom motion registry

Contains:
    test_regression_nan_easing_strength_is_rejected: UP-9.2-12
    test_regression_infinite_steps_strength_raises_showpy_error: UP-9.2-13
    test_regression_easing_strength_below_one_is_not_clamped_silently: UP-9.2-14
    test_regression_steps_strength_has_a_cap: UP-9.2-15
"""

from collections.abc import Callable, Mapping
from typing import Any, TypeVar, cast

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from showpy.choreo.models import Easing, TurnDirection
from showpy.choreo.motion import (
    DEFAULT_MOTIONS,
    FloatArray,
    ease,
    easing_breaks,
    motions,
    register_motion,
    turn,
)
from showpy.core.errors import PathError, ShowPyError, UnknownRefError
from showpy.core.types import Point, Points
from showpy.core.util import approx_equal

KINDS = [
    "linear",
    "ease_in",
    "ease_out",
    "ease_in_out",
    "hold_then_burst",
    "burst_then_settle",
    "steps",
]


@pytest.mark.parametrize("kind", KINDS)
def test_easing_endpoints(kind: str) -> None:
    e = Easing(kind=kind, strength=3.0)
    assert approx_equal(ease(e, np.array([0.0, 1.0])), [0.0, 1.0])


@pytest.mark.parametrize("kind", KINDS)
@given(st.floats(min_value=1.0, max_value=6.0))
def test_easing_is_monotone(kind: str, strength: float) -> None:
    u = np.linspace(0.0, 1.0, 401)
    out = ease(Easing(kind=kind, strength=strength), u)
    assert np.all(np.diff(out) >= -1e-12)
    assert np.all((out >= 0.0) & (out <= 1.0))


def test_linear_is_identity_and_ease_in_starts_slow() -> None:
    u = np.array([0.25, 0.5])
    assert approx_equal(ease(Easing(), u), u)
    assert ease(Easing(kind="ease_in"), np.array([0.25]))[0] < 0.25


def test_hold_then_burst_holds_first_part() -> None:
    out = ease(Easing(kind="hold_then_burst", strength=2.0), np.array([0.25, 0.5, 0.75]))
    assert approx_equal(out, [0.0, 0.0, 0.5])


def test_burst_then_settle_finishes_early() -> None:
    out = ease(Easing(kind="burst_then_settle", strength=2.0), np.array([0.25, 0.5, 0.75]))
    assert approx_equal(out, [0.5, 1.0, 1.0])


def test_steps_pause_between_bursts() -> None:
    out = ease(Easing(kind="steps", strength=2.0), np.array([0.25, 0.5, 0.75]))
    assert approx_equal(out, [0.25, 0.5, 0.75])
    assert approx_equal(ease(Easing(kind="steps", strength=2.0), np.array([0.5]))[0], 0.5)


def test_custom_easing_interpolates_points() -> None:
    e = Easing(kind="custom", points=((0.0, 0.0), (0.5, 0.2), (1.0, 1.0)))
    assert approx_equal(ease(e, np.array([0.25, 0.75])), [0.1, 0.6])


def test_turn_directions() -> None:
    assert approx_equal(turn(350.0, 10.0, 0.5), 0.0)
    assert approx_equal(turn(350.0, 10.0, 0.5, TurnDirection.CLOCKWISE), 180.0)
    assert approx_equal(turn(0.0, 90.0, 0.5, TurnDirection.CLOCKWISE), 225.0)
    assert approx_equal(turn(0.0, 90.0, 1.0, TurnDirection.COUNTERCLOCKWISE), 90.0)


@pytest.mark.parametrize("name", ["weave", "zigzag", "figure_eight", "spiral"])
def test_builtin_motions_start_and_end_on_target(name: str) -> None:
    fn = DEFAULT_MOTIONS.get(name)
    u = np.array([0.0, 0.37, 1.0])
    pts = fn(u, (0.0, 0.0), (8.0, 0.0), {})
    assert approx_equal(pts[0], (0.0, 0.0))
    assert approx_equal(pts[-1], (8.0, 0.0))
    assert not approx_equal(pts[1], (0.37 * 8.0, 0.0))


def test_register_motion_validates_endpoints() -> None:
    def broken(u: FloatArray, start: Point, end: Point, params: Mapping[str, Any]) -> Points:
        return np.zeros((len(u), 2))

    with pytest.raises(PathError):
        register_motion(DEFAULT_MOTIONS, "broken", broken)


def test_register_motion_returns_new_registry() -> None:
    def straight(u: FloatArray, start: Point, end: Point, params: Mapping[str, Any]) -> Points:
        """Moves in a straight line."""
        a, b = np.asarray(start), np.asarray(end)
        return a + np.outer(u, b - a)

    extended = register_motion(DEFAULT_MOTIONS, "straight2", straight)
    assert "straight2" in extended.names()
    assert "straight2" not in DEFAULT_MOTIONS.names()


def test_unknown_motion_suggests() -> None:
    with pytest.raises(UnknownRefError) as info:
        DEFAULT_MOTIONS.get("weeve")
    assert "weave" in info.value.hint


def test_motions_lists_names_with_summaries() -> None:
    listing = {m.name: m.summary for m in motions(DEFAULT_MOTIONS)}
    assert listing["weave"]


def test_easing_breaks_mark_kinks() -> None:
    assert easing_breaks(Easing(kind="steps", strength=4.0)) == [0.25, 0.5, 0.75]
    assert easing_breaks(Easing(kind="hold_then_burst", strength=2.0)) == [0.5]
    assert easing_breaks(Easing(kind="burst_then_settle", strength=4.0)) == [0.25]
    assert easing_breaks(Easing()) == []


F = TypeVar("F", bound=Callable[..., object])
SHARES = np.array([0.0, 0.25, 0.5, 1.0])
OVERSIZED_STEP_COUNT = 10_000.0


def defect(defect_id: str) -> Callable[[F], F]:
    """Marks a test as an open audited defect: a strict xfail keyed on its ID."""

    def decorate(test: F) -> F:
        marked = pytest.mark.defect(defect_id)(test)
        return cast(F, pytest.mark.xfail(strict=True, reason=defect_id)(marked))

    return decorate


@defect("UP-9.2-12")
def test_regression_nan_easing_strength_is_rejected() -> None:
    """Rejects a NaN easing strength instead of producing NaN positions (audit 9.2)."""
    with pytest.raises(ShowPyError):
        ease(Easing(kind="ease_in", strength=float("nan")), SHARES)


@defect("UP-9.2-13")
def test_regression_infinite_steps_strength_raises_showpy_error() -> None:
    """Raises a ShowPyError, not OverflowError, for infinite step counts (audit 9.2)."""
    with pytest.raises(ShowPyError):
        ease(Easing(kind="steps", strength=float("inf")), SHARES)


@defect("UP-9.2-14")
def test_regression_easing_strength_below_one_is_not_clamped_silently() -> None:
    """Rejects a strength below 1 instead of clamping it to 1 (audit 9.2)."""
    with pytest.raises(ShowPyError):
        ease(Easing(kind="ease_in", strength=0.5), SHARES)


@defect("UP-9.2-15")
def test_regression_steps_strength_has_a_cap() -> None:
    """Rejects a 10,000-step easing instead of building 9,999 edges (audit 9.2)."""
    with pytest.raises(ShowPyError):
        easing_breaks(Easing(kind="steps", strength=OVERSIZED_STEP_COUNT))
