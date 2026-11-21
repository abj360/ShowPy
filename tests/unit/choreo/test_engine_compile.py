#!/usr/bin/env python3

"""
test_engine_compile.py --- tests compiling path rows into plans, and the audited compile defects

Contains:
    test_regression_timed_waypoint_survives_style_change_without_index_error: compiles or rejects a
        timed waypoint kept through a style change, never IndexError (9.2)
    test_regression_engine_and_editors_agree_a_straight_row_has_no_bend: moves a STRAIGHT row
        straight, as the path editors treat it, even with a waypoint
    test_regression_custom_motion_that_never_leaves_start_is_rejected: raises ContinuityError for a
        custom motion that stays put but has a distant target (9.2)
    test_regression_custom_motion_with_wrong_shape_raises_path_error: raises PathError for a custom
        motion that returns the wrong output shape
    test_regression_custom_motion_with_nan_samples_is_rejected: rejects NaN samples from a custom
        motion instead of dropping them silently
    test_regression_timed_waypoint_is_reached_at_its_count_under_easing: reaches a waypoint timed
        "at 2" at count 2 even when the row is eased
    test_regression_easing_chord_error_matches_a_dense_scan: computes the closed-form easing gap
        to within 1e-6 of a 20,001-point scan on pieces between easing edges
    test_curvature_bound_is_known_only_for_exact_arc_length_curves: bounds lines, arcs, circles,
        trims and composites of them, and leaves table-based curves opaque
    test_custom_motion_receives_plain_json: passes a custom motion its stored parameters as
        plain dicts and lists, never read-only containers
"""

import itertools
from collections.abc import Callable, Mapping
from typing import Any, cast

import numpy as np
import pytest

from showpy.choreo.engine import PathEngine
from showpy.choreo.engine_compile import curvature_bound, easing_chord_error
from showpy.choreo.motion import DEFAULT_MOTIONS, FloatArray, ease, easing_breaks
from showpy.choreo.path_models import Easing, PathRow, PathStyle, TransitionSpec, Waypoint
from showpy.choreo.paths import add_waypoint, set_style
from showpy.choreo.routes import follow, make_route
from showpy.core.errors import ContinuityError, PathError, ShowPyError
from showpy.core.ids import SpotId
from showpy.core.json_types import JsonValue
from showpy.core.types import Point, Points
from showpy.core.util import approx_equal
from showpy.geo.curves import (
    ArcCurve,
    CircleCurve,
    CompositeCurve,
    Polyline,
    Segment,
    SplineCurve,
    TrimmedCurve,
)
from showpy.show.models import Show
from tests.fixtures.motion_shows import TID, with_rows, with_targets
from tests.fixtures.shows import two_set_show
from tests.helpers import defect

BEND: Point = (1.0, 7.0)
BEND_COUNT = 4.0
SWEEP_FULL_TURN = 360.0
NAN_SAMPLE_INDEX = 10
SCAN_SAMPLES = 20_001
CHORD_ERROR_TOL = 1e-6
CUSTOM_PARAMS: dict[str, JsonValue] = {"bend": [1, {"deep": True}], "label": "x"}
PIECE_SHARES = ((0.0, 1.0), (0.0, 0.3), (0.2, 0.9), (0.6, 0.61), (0.97, 1.0))
CHORD_EASINGS = [
    Easing(kind="linear"),
    Easing(kind="ease_in", strength=1.5),
    Easing(kind="ease_in", strength=3.7),
    Easing(kind="ease_out", strength=2.0),
    Easing(kind="ease_in_out", strength=3.0),
    Easing(kind="hold_then_burst", strength=2.0),
    Easing(kind="burst_then_settle", strength=3.0),
    Easing(kind="steps", strength=3.0),
    Easing(kind="custom", points=((0.0, 0.0), (0.4, 0.1), (1.0, 1.0))),
]


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
    """Moves a STRAIGHT row straight, as the path editors do, even with a waypoint (audit 9.2)."""
    row = PathRow(
        entity=SpotId("T1"), style=PathStyle.STRAIGHT, waypoints=(Waypoint(x=1.0, y=7.0),)
    )
    engine = PathEngine(with_rows({"T1": row}))
    try:
        mid = engine.position("T1", 4.0, local=TID)
    except ShowPyError:
        return
    assert approx_equal(mid, (0.0, 7.0), abs_tol=1e-6)


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
    row = PathRow(entity=SpotId("T1"), style=PathStyle.CUSTOM, motion_fn="probe")
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
        entity=SpotId("T1"),
        style=PathStyle.POLYLINE,
        waypoints=(bend,),
        easing=Easing(kind="ease_in", strength=2.0),
    )
    engine = PathEngine(with_rows({"T1": row}))
    assert approx_equal(engine.position("T1", 2.0, local=TID), (bend.x, bend.y), abs_tol=1e-6)


def _easing_edges(easing: Easing) -> list[float]:
    inflections = []
    if easing.kind == "steps":
        n = round(easing.strength)
        inflections = [(i + 0.5) / n for i in range(n)]
    return sorted({0.0, 1.0, *easing_breaks(easing), *inflections})


def _scanned_gap(easing: Easing, u0: float, u1: float) -> float:
    u = np.linspace(u0, u1, SCAN_SAMPLES)
    e = ease(easing, u)
    chord = e[0] + (u - u0) / (u1 - u0) * (e[-1] - e[0])
    return float(np.max(np.abs(e - chord)))


@pytest.mark.parametrize("easing", CHORD_EASINGS, ids=lambda e: f"{e.kind}-{e.strength}")
def test_regression_easing_chord_error_matches_a_dense_scan(easing: Easing) -> None:
    """Computes the closed-form easing gap to within 1e-6 of a 20,001-point scan per piece."""
    edges = _easing_edges(easing)
    for a, b in itertools.pairwise(edges):
        for f0, f1 in PIECE_SHARES:
            u0, u1 = a + f0 * (b - a), a + f1 * (b - a)
            gap = easing_chord_error(easing, u0, u1)
            assert gap == pytest.approx(_scanned_gap(easing, u0, u1), abs=CHORD_ERROR_TOL)


def test_curvature_bound_is_known_only_for_exact_arc_length_curves() -> None:
    """Bounds lines, arcs, circles, trims and composites of them; leaves table curves opaque."""
    arc = ArcCurve(center=(0.0, 0.0), radius=4.0, start_deg=0.0, sweep_deg=720.0)
    circle = CircleCurve(center=(0.0, 0.0), radius=0.5)
    line = Segment(a=(4.0, 0.0), b=(5.0, 0.0))
    spline = SplineCurve(points=((0.0, 0.0), (1.0, 1.0), (2.0, 0.0)))
    assert curvature_bound(line) == 0.0
    assert curvature_bound(Polyline(points=((0.0, 0.0), (1.0, 0.0), (1.0, 1.0)))) == 0.0
    assert curvature_bound(arc) == 0.25
    assert curvature_bound(TrimmedCurve(base=circle, s0=0.0, s1=1.0)) == 2.0
    assert curvature_bound(CompositeCurve(parts=(arc, line))) == 0.25
    assert curvature_bound(spline) is None
    assert curvature_bound(CompositeCurve(parts=(line, spline))) is None


def test_custom_motion_receives_plain_json() -> None:
    """Passes a custom motion its stored parameters as plain dicts and lists, never frozen ones."""
    seen: list[Mapping[str, JsonValue]] = []

    def recording(
        u: FloatArray, start: Point, end: Point, params: Mapping[str, JsonValue]
    ) -> Points:
        seen.append(params)
        result: Points = np.linspace(start, end, len(u))
        return result

    registry = DEFAULT_MOTIONS.with_motion("probe", recording)
    row = PathRow.model_validate(
        {"entity": "T1", "style": "custom", "motion_fn": "probe", "motion_params": CUSTOM_PARAMS}
    )
    PathEngine(with_rows({"T1": row}), motions=registry).compile(TID)
    assert seen == [CUSTOM_PARAMS]
    assert type(seen[0]) is dict
    bend = seen[0]["bend"]
    assert isinstance(bend, list)
    assert type(bend[1]) is dict
