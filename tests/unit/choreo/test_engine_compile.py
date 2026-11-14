#!/usr/bin/env python3

"""
test_engine_compile.py --- tests compiling path rows into plans, and the audited compile defects

Contains:
    test_regression_timed_waypoint_survives_style_change_without_index_error: UP-9.2-03
    test_regression_engine_and_editors_agree_a_straight_row_has_no_bend: UP-9.2-05
    test_regression_custom_motion_that_never_leaves_start_is_rejected: UP-9.2-09
    test_regression_custom_motion_with_wrong_shape_raises_path_error: UP-9.2-10
    test_regression_custom_motion_with_nan_samples_is_rejected: UP-9.2-11
    test_regression_timed_waypoint_is_reached_at_its_count_under_easing: UP-9.2-16
"""

from collections.abc import Callable
from typing import Any, cast

import numpy as np
import pytest

from showpy.choreo.engine import PathEngine
from showpy.choreo.models import Easing, PathRow, PathStyle, TransitionSpec, Waypoint
from showpy.choreo.motion import DEFAULT_MOTIONS, FloatArray
from showpy.choreo.paths import add_waypoint, set_style
from showpy.choreo.routes import follow, make_route
from showpy.core.errors import ContinuityError, PathError, ShowPyError
from showpy.core.types import Point, Points
from showpy.core.util import approx_equal
from showpy.show.models import Show
from tests.fixtures.motion_shows import TID, with_rows, with_targets
from tests.fixtures.shows import two_set_show
from tests.helpers import defect

BEND: Point = (1.0, 7.0)
BEND_COUNT = 4.0
SWEEP_FULL_TURN = 360.0
NAN_SAMPLE_INDEX = 10


def _stored(show: Show, spec: TransitionSpec) -> Show:
    return show.model_copy(update={"transitions": (spec,)})


# Any: style parameters are PathRow field values whose types depend on the style.
def _timed_bend_then(style: PathStyle, show: Show, **params: Any) -> Show:
    spec = add_waypoint(show.transition(TID), "T1", BEND, at=BEND_COUNT)
    return _stored(show, set_style(spec, ["T1"], style, **params))


def _hold_case() -> Show:
    return _timed_bend_then(PathStyle.HOLD, with_targets({"T1": {"y": 5.0}}))


def _rotate_case() -> Show:
    show = with_targets({"T1": {"y": 5.0}})
    return _timed_bend_then(PathStyle.ROTATE, show, pivot=(0.0, 7.0), sweep_deg=SWEEP_FULL_TURN)


def _custom_case() -> Show:
    return _timed_bend_then(PathStyle.CUSTOM, two_set_show(), motion_fn="weave")


def _follow_case() -> Show:
    show = two_set_show()
    spec = add_waypoint(show.transition(TID), "T1", BEND, at=BEND_COUNT)
    return _stored(show, follow(spec, make_route(show, TID, "T1", [(0.0, 9.0)])))


@defect("UP-9.2-03")
@pytest.mark.parametrize("case", [_hold_case, _rotate_case, _custom_case, _follow_case])
def test_regression_timed_waypoint_survives_style_change_without_index_error(
    case: Callable[[], Show],
) -> None:
    """Compiles or rejects a timed waypoint kept through a style change, never IndexError (9.2)."""
    show = case()
    engine = PathEngine(show)
    try:
        engine.compile(TID)
    except ShowPyError:
        return
    target = show.set("2").get("T1").point
    assert approx_equal(engine.position("T1", 8.0, local=TID), target)


@defect("UP-9.2-05")
def test_regression_engine_and_editors_agree_a_straight_row_has_no_bend() -> None:
    """Moves a STRAIGHT row straight, as the path editors treat it, even with a waypoint (9.2)."""
    row = PathRow(entity="T1", style=PathStyle.STRAIGHT, waypoints=(Waypoint(x=1.0, y=7.0),))
    engine = PathEngine(with_rows({"T1": row}))
    try:
        mid = engine.position("T1", 4.0, local=TID)
    except ShowPyError:
        return
    assert approx_equal(mid, (0.0, 7.0), tol=1e-6)


def _stay_at_start(u: FloatArray, start: Point, end: Point, params: object) -> Points:
    return np.tile(np.asarray(start, dtype=float), (len(u), 1))


def _flat_output(u: FloatArray, start: Point, end: Point, params: object) -> Points:
    return cast(Points, np.zeros(len(u)))


def _with_nan_sample(u: FloatArray, start: Point, end: Point, params: object) -> Points:
    pts = np.linspace(start, end, len(u))
    pts[NAN_SAMPLE_INDEX] = np.nan
    return pts


def _custom_engine(motion: Callable[..., Points]) -> PathEngine:
    registry = DEFAULT_MOTIONS.with_motion("probe", motion)
    row = PathRow(entity="T1", style=PathStyle.CUSTOM, motion_fn="probe")
    return PathEngine(with_rows({"T1": row}), motions=registry)


@defect("UP-9.2-09")
def test_regression_custom_motion_that_never_leaves_start_is_rejected() -> None:
    """Raises ContinuityError for a custom motion that stays put but has a distant target (9.2)."""
    with pytest.raises(ContinuityError):
        _custom_engine(_stay_at_start).compile(TID)


@defect("UP-9.2-10")
def test_regression_custom_motion_with_wrong_shape_raises_path_error() -> None:
    """Raises PathError for a custom motion that returns the wrong output shape (audit 9.2)."""
    with pytest.raises(PathError):
        _custom_engine(_flat_output).compile(TID)


@defect("UP-9.2-11")
def test_regression_custom_motion_with_nan_samples_is_rejected() -> None:
    """Rejects NaN samples from a custom motion instead of dropping them silently (audit 9.2)."""
    with pytest.raises(ShowPyError):
        _custom_engine(_with_nan_sample).compile(TID)


@defect("UP-9.2-16")
def test_regression_timed_waypoint_is_reached_at_its_count_under_easing() -> None:
    """Reaches a waypoint timed "at 2" at count 2 even when the row is eased (audit 9.2)."""
    bend = Waypoint(x=3.0, y=7.0, at=2.0)
    row = PathRow(
        entity="T1",
        style=PathStyle.POLYLINE,
        waypoints=(bend,),
        easing=Easing(kind="ease_in", strength=2.0),
    )
    engine = PathEngine(with_rows({"T1": row}))
    assert approx_equal(engine.position("T1", 2.0, local=TID), (bend.x, bend.y), tol=1e-6)
