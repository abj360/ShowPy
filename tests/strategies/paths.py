#!/usr/bin/env python3

"""
paths.py --- strategies for path rows of every style, built with the path builders

Every row compiles against the show it was drawn for: styles that pin the target (HOLD) are
offered only when the spot already stands still, and ROTATE pivots on the perpendicular
bisector of the move so the arc lands on the target.

Contains:
    ROW_STYLES: the path styles a row can take without a stored route; FOLLOW needs one
    edited_row(): applies drawn window, style, facing, easing and pace edits to one spot's row
    path_rows(): draws one compiling path row for a spot of a transition of the show
"""

import math
from typing import Literal

from hypothesis import strategies as st

from showpy.choreo.match_ops import clear_rows
from showpy.choreo.models import (
    Easing,
    FacingMode,
    PathRow,
    PathStyle,
    Placement,
    TransitionSpec,
    TurnDirection,
)
from showpy.choreo.paths import (
    add_waypoint,
    arrive_early,
    curve_path,
    custom_path,
    delay,
    lock,
    set_easing,
    set_facing,
    set_pace,
    set_style,
    window,
)
from showpy.choreo.routes import rotate_path, two_part
from showpy.core.ids import TransitionId
from showpy.show.models import Show
from tests.helpers import bisector_rotation
from tests.strategies.sheet import headings, points_on

STILL_TOL_M = 1e-6
MIN_REACH_M = 0.5
MAX_REACH_M = 4.0
WAYPOINT_STEPS = 20
MAX_WAYPOINTS = 3
MAX_TURN_COUNTS = 2.0
MIN_PACE = 0.5
MAX_PACE = 2.0
MAX_STRENGTH = 4.0
MAX_STEPS = 6
MAX_AMPLITUDE_M = 2.0
MAX_CYCLES_HALVES = 8
MAX_SPIRAL_TURNS = 2
ROW_STYLES = (
    PathStyle.STRAIGHT, PathStyle.HOLD, PathStyle.CURVE, PathStyle.POLYLINE, PathStyle.ROTATE,
    PathStyle.CUSTOM,
)  # fmt: skip
SMOOTH_EASINGS = ("linear", "ease_in", "ease_out", "ease_in_out")
SHAPED_EASINGS = ("hold_then_burst", "burst_then_settle")
CORNERS: tuple[Literal["x_first", "y_first"], ...] = ("x_first", "y_first")
MOTIONS = ("weave", "zigzag", "figure_eight", "spiral")


def _ends(show: Show, tid: TransitionId, entity: str) -> tuple[Placement, Placement]:
    return show.set(tid[0]).get(entity), show.set(tid[1]).get(entity)


def _is_still(a: Placement, b: Placement) -> bool:
    return math.dist(a.point, b.point) <= STILL_TOL_M


@st.composite
def _windowed(draw: st.DrawFn, spec: TransitionSpec, entity: str) -> TransitionSpec:
    counts = spec.counts
    first = draw(st.integers(0, WAYPOINT_STEPS // 2))
    last = draw(st.integers(first + 1, WAYPOINT_STEPS))
    start, end = counts * first / WAYPOINT_STEPS, counts * last / WAYPOINT_STEPS
    builder = draw(st.sampled_from(("none", "window", "delay", "arrive_early")))
    if builder == "window":
        return window(spec, [entity], start, end)
    if builder == "delay":
        return delay(spec, [entity], start)
    if builder == "arrive_early" and end < counts:
        return arrive_early(spec, [entity], counts - end)
    return spec


@st.composite
def _waypoint_times(draw: st.DrawFn, row: PathRow, counts: float, n: int) -> list[float | None]:
    if not draw(st.booleans()):
        return [None] * n
    start, end = row.window(counts)
    steps = draw(st.lists(st.integers(1, WAYPOINT_STEPS - 1), min_size=n, max_size=n, unique=True))
    return [start + (end - start) * k / WAYPOINT_STEPS for k in sorted(steps)]


@st.composite
def _bent(draw: st.DrawFn, show: Show, spec: TransitionSpec, entity: str) -> TransitionSpec:
    pts = draw(st.lists(points_on(show.sheet), min_size=1, max_size=MAX_WAYPOINTS))
    if draw(st.booleans()):
        return curve_path(spec, entity, *pts)
    if draw(st.booleans()):
        corner = draw(st.sampled_from(CORNERS))
        return two_part(show, spec, [entity], corner)
    times = draw(_waypoint_times(spec.row(entity), spec.counts, len(pts)))
    for point, at in zip(pts, times, strict=True):
        spec = add_waypoint(spec, entity, point, at=at)
    return spec


@st.composite
def _rotated(draw: st.DrawFn, show: Show, spec: TransitionSpec, entity: str) -> TransitionSpec:
    a, b = _ends(show, spec.id, entity)
    reach = draw(st.floats(min_value=MIN_REACH_M, max_value=MAX_REACH_M, allow_nan=False))
    side = draw(st.sampled_from((-1.0, 1.0)))
    pivot, sweep = bisector_rotation(a.point, b.point, side * reach, draw(st.booleans()))
    return rotate_path(show, spec, [entity], pivot, sweep)


@st.composite
def _custom(draw: st.DrawFn, spec: TransitionSpec, entity: str) -> TransitionSpec:
    motion = draw(st.sampled_from(MOTIONS))
    amplitude = draw(st.floats(min_value=0.0, max_value=MAX_AMPLITUDE_M, allow_nan=False))
    if motion == "spiral":
        turns = draw(st.integers(0, MAX_SPIRAL_TURNS))
        return custom_path(spec, [entity], motion, turns=turns, clockwise=draw(st.booleans()))
    if motion == "figure_eight":
        return custom_path(spec, [entity], motion, amplitude=amplitude)
    cycles = draw(st.integers(1, MAX_CYCLES_HALVES)) / 2
    return custom_path(spec, [entity], motion, amplitude=amplitude, cycles=cycles)


@st.composite
def _styled(draw: st.DrawFn, show: Show, spec: TransitionSpec, entity: str) -> TransitionSpec:
    a, b = _ends(show, spec.id, entity)
    allowed = [s for s in ROW_STYLES if s is not PathStyle.HOLD or _is_still(a, b)]
    routes = sorted(r.id for r in spec.routes.values() if entity in r.members)
    style = draw(st.sampled_from(allowed + ([PathStyle.FOLLOW] if routes else [])))
    if style is PathStyle.FOLLOW:
        return set_style(spec, [entity], style, route=draw(st.sampled_from(routes)))
    if style in (PathStyle.CURVE, PathStyle.POLYLINE):
        return draw(_bent(show, spec, entity))
    if style is PathStyle.ROTATE:
        return draw(_rotated(show, spec, entity))
    if style is PathStyle.CUSTOM:
        return draw(_custom(spec, entity))
    return set_style(spec, [entity], style)


@st.composite
def _faced(draw: st.DrawFn, show: Show, spec: TransitionSpec, entity: str) -> TransitionSpec:
    others = [e for e in show.cast.ids() if e != entity]
    modes = [m for m in FacingMode if m is not FacingMode.TOWARD_ENTITY or others]
    mode = draw(st.sampled_from(modes))
    value: float | tuple[float, float] | str | None = None
    if mode is FacingMode.FIXED:
        value = draw(headings())
    elif mode is FacingMode.TOWARD_POINT:
        value = draw(points_on(show.sheet))
    elif mode is FacingMode.TOWARD_ENTITY:
        value = draw(st.sampled_from(others))
    turn = draw(st.floats(min_value=0.0, max_value=MAX_TURN_COUNTS, allow_nan=False))
    direction = draw(st.sampled_from(TurnDirection))
    return set_facing(spec, [entity], mode, value, turn, direction)


@st.composite
def _easings(draw: st.DrawFn) -> Easing | str:
    kind = draw(st.sampled_from((*SMOOTH_EASINGS, *SHAPED_EASINGS, "steps", "custom")))
    if kind in SMOOTH_EASINGS:
        return kind
    if kind == "steps":
        return Easing(kind="steps", strength=float(draw(st.integers(1, MAX_STEPS))))
    if kind == "custom":
        knee = draw(st.floats(min_value=0.0, max_value=1.0, allow_nan=False))
        return Easing(kind="custom", points=((0.0, 0.0), (0.5, knee), (1.0, 1.0)))
    strength = draw(st.floats(min_value=1.0, max_value=MAX_STRENGTH, allow_nan=False))
    return Easing(kind=kind, strength=strength)


@st.composite
def edited_row(draw: st.DrawFn, show: Show, spec: TransitionSpec, entity: str) -> TransitionSpec:
    """Applies drawn window, style, facing, easing, pace and lock edits to one spot's row.

    Args:
        draw: Hypothesis draw function.
        show: Show holding both sets of the transition.
        spec: Transition paths to edit; the spot's row must not be locked.
        entity: Spot whose row is edited.
    """
    spec = draw(_windowed(spec, entity))
    spec = draw(_styled(show, spec, entity))
    if draw(st.booleans()):
        spec = draw(_faced(show, spec, entity))
    if draw(st.booleans()):
        spec = set_easing(spec, [entity], draw(_easings()))
    if draw(st.booleans()):
        pace = draw(st.floats(min_value=MIN_PACE, max_value=MAX_PACE, allow_nan=False))
        spec = set_pace(spec, [entity], pace)
    return lock(spec, [entity]) if draw(st.booleans()) else spec


@st.composite
def path_rows(draw: st.DrawFn, show: Show, tid: TransitionId | None = None) -> PathRow:
    """Draws a path row for one spot of transition `tid` that compiles against `show`.

    The spot's stored row is dropped first. FOLLOW is offered only to members of a route
    already stored on the transition (`with_paths` in tests.strategies.show adds routes and
    sets their targets), and HOLD only to spots that stand still.

    Args:
        draw: Hypothesis draw function.
        show: Show with at least two sets.
        tid: Transition the row is for; None draws one.
    """
    tid = tid or draw(st.sampled_from(show.transition_ids()))
    entity = draw(st.sampled_from(show.cast.ids()))
    spec = clear_rows(show, tid, [entity], force=True).transition(tid)
    return draw(edited_row(show, spec, entity)).row(entity)
