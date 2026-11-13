#!/usr/bin/env python3

"""
test_motion.py --- tests easing curves, facing turns and the custom motion registry
"""

from collections.abc import Mapping
from typing import Any

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
from showpy.core.errors import PathError, UnknownRefError
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
