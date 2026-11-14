#!/usr/bin/env python3

"""
show.py --- the shows() strategy: whole shows built only through public showpy builders

Contains:
    with_paths(): gives one transition a follow route, held spots, edited rows and a stagger
    shows(): draws a valid show with groups, sets, forms, paths, tempo changes and holds
"""

import math
from typing import Literal

from hypothesis import assume
from hypothesis import strategies as st

from showpy.choreo.facing import add_facing_override
from showpy.choreo.match_ops import with_set
from showpy.choreo.models import FacingMode, FacingOverride, TransitionSpec
from showpy.choreo.paths import hold, ripple, stagger
from showpy.choreo.routes import follow_and_set_targets, make_route
from showpy.core.ids import TransitionId
from showpy.core.types import Point
from showpy.show.changes import store_paths
from showpy.show.manage import new_show
from showpy.show.models import PathsOp, Show
from tests.strategies.cast import with_cast
from tests.strategies.forms import with_form
from tests.strategies.paths import WAYPOINT_STEPS, edited_row
from tests.strategies.placements import with_next_set
from tests.strategies.sheet import points_on, sheets
from tests.strategies.time import timelines

ROUTE_GAP_M = 1e-9
SHOW_ID = "generated"
TITLE = "Generated show"
FIRST_SET = "1"
MAX_HALF_COUNTS = 32
MAX_FILE = 4
MAX_ROUTE_WAYPOINTS = 2
MAX_HELD = 3
OVERRIDE_MODES = (FacingMode.AUDIENCE, FacingMode.FIXED, FacingMode.TRAVEL)
OVERRIDE_FIXED_DEG = 90.0
type RippleCurve = Literal["linear", "ease_in", "ease_out", "ease_in_out"]
type StaggerEnd = Literal["start", "end"]

RIPPLE_CURVES: tuple[RippleCurve, ...] = ("linear", "ease_in", "ease_out", "ease_in_out")
STAGGER_ENDS: tuple[StaggerEnd, ...] = ("start", "end")


set_counts = st.integers(1, MAX_HALF_COUNTS).map(lambda halves: halves / 2)


def _formed(show: Show, set_id: str) -> set[str]:
    return {p.entity for p in show.set(set_id).placements if p.form is not None}


def _is_one_point(show: Show, tid: TransitionId, file: list[str], ahead: list[Point]) -> bool:
    ends = ahead or [show.set(tid[1]).get(file[0]).point]
    points = [show.set(tid[0]).get(m).point for m in file] + list(ends)
    return all(math.dist(points[0], p) <= ROUTE_GAP_M for p in points)


@st.composite
def _followed(draw: st.DrawFn, show: Show, tid: TransitionId, free: list[str]) -> Show:
    file = draw(st.lists(st.sampled_from(free), min_size=1, max_size=MAX_FILE, unique=True))
    ahead = draw(st.lists(points_on(show.sheet), max_size=MAX_ROUTE_WAYPOINTS))
    # UP-9.2-27: make_route raises IndexError, not PathError, when file and route are one point.
    assume(not _is_one_point(show, tid, file, ahead))
    route = make_route(show, tid, file[0], ahead, members=file)
    first = draw(st.integers(0, WAYPOINT_STEPS // 2))
    last = draw(st.integers(first + 1, WAYPOINT_STEPS))
    counts = show.set(tid[1]).counts
    start, end = counts * first / WAYPOINT_STEPS, counts * last / WAYPOINT_STEPS
    return follow_and_set_targets(show, tid, route, start, end)


def _held(show: Show, tid: TransitionId, held: list[str]) -> Show:
    start, target = show.set(tid[0]), show.set(tid[1])
    still = {e: {"x": start.get(e).x, "y": start.get(e).y} for e in held}
    show = with_set(show, target.with_placements(still))
    return store_paths(show, PathsOp(spec=hold(show.transition(tid), held)))


@st.composite
def _spread(draw: st.DrawFn, spec: TransitionSpec, ordered: list[str]) -> TransitionSpec:
    share = draw(st.integers(1, WAYPOINT_STEPS - 1)) / WAYPOINT_STEPS
    if draw(st.booleans()):
        direction = draw(st.sampled_from(STAGGER_ENDS))
        return stagger(spec, ordered, spec.counts * share / len(ordered), direction)
    return ripple(spec, ordered, spec.counts * share, draw(st.sampled_from(RIPPLE_CURVES)))


@st.composite
def with_paths(draw: st.DrawFn, show: Show, tid: TransitionId) -> Show:
    """Gives transition `tid` a follow route, held spots, edited rows and a stagger or ripple.

    Spots in a form of the destination set keep their form slots: routes and holds, which
    rewrite targets, use only the other spots.

    Args:
        draw: Hypothesis draw function.
        show: Show holding both sets of `tid` and no stored paths for it.
        tid: Transition to give paths.
    """
    free = [e for e in show.cast.ids() if e not in _formed(show, tid[1])]
    if draw(st.booleans()) and free:
        show = draw(_followed(show, tid, free))
    routed = {m for route in show.transition(tid).routes.values() for m in route.members}
    free = [e for e in free if e not in routed]
    held = draw(st.lists(st.sampled_from(free), max_size=MAX_HELD, unique=True)) if free else []
    show = _held(show, tid, held) if held else show
    rest = [e for e in show.cast.ids() if e not in routed and e not in held]
    edited = draw(st.lists(st.sampled_from(rest), unique=True)) if rest else []
    spec = show.transition(tid)
    for entity in edited:
        spec = draw(edited_row(show, spec, entity))
    untouched = [e for e in rest if e not in edited]
    if draw(st.booleans()) and untouched:
        spec = draw(_spread(spec, untouched))
    return store_paths(show, PathsOp(spec=spec))


@st.composite
def _with_override(draw: st.DrawFn, show: Show, total: float) -> Show:
    entities = draw(st.lists(st.sampled_from(show.cast.ids()), min_size=1, unique=True))
    first = draw(st.integers(0, WAYPOINT_STEPS - 1))
    last = draw(st.integers(first + 1, WAYPOINT_STEPS))
    mode = draw(st.sampled_from(OVERRIDE_MODES))
    value = OVERRIDE_FIXED_DEG if mode is FacingMode.FIXED else None
    override = FacingOverride(
        entities=entities,
        mode=mode,
        value=value,
        start=total * first / WAYPOINT_STEPS,
        end=total * last / WAYPOINT_STEPS,
    )
    return add_facing_override(show, override)


@st.composite
def shows(draw: st.DrawFn, max_spots: int = 20, max_sets: int = 6, min_sets: int = 1) -> Show:
    """Draws a valid show built through the public builders.

    It covers every path style, easing, window builder, timed and untimed waypoint, facing
    mode, entrance and exit, and form kind across examples, plus tempo ramps and holds.

    Args:
        draw: Hypothesis draw function.
        max_spots: Most spots in the cast.
        max_sets: Most sets in the show.
        min_sets: Fewest sets in the show.
    """
    moves = draw(st.lists(set_counts, min_size=min_sets - 1, max_size=max_sets - 1))
    total = sum(moves)
    show = new_show(SHOW_ID, TITLE, draw(sheets()), draw(timelines(total)))
    show = draw(with_cast(show, max_spots))
    if draw(st.booleans()):
        show = draw(with_form(show, FIRST_SET, f"form-{FIRST_SET}"))
    for number, counts in enumerate(moves, start=2):
        set_id = str(number)
        show = draw(with_next_set(show, set_id, counts))
        if draw(st.booleans()):
            show = draw(with_form(show, set_id, f"form-{set_id}"))
        show = draw(with_paths(show, (show.sets[-2].id, set_id)))
    if total > 0 and draw(st.booleans()):
        show = draw(_with_override(show, total))
    return show
