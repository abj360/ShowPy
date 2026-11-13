#!/usr/bin/env python3

"""
helpers.py --- small builders shared by many test modules

Contains:
    make_set(): builds set "1" from (entity, x, y) rows, with some spots off the field
    make_facing_set(): builds set "1" from (entity, x, y, facing) rows, with off and pinned spots
    pin_spot(): pins one spot's placement in one set of a show
    with_curve_rows(): gives the spots curved path rows through (1, 7) in transition 1 -> 2
    open_session(): saves a show to a fresh repository and opens agent and designer workspaces
    bisector_rotation(): returns a pivot and sweep whose arc carries one point onto another
"""

import math
from collections.abc import Iterable
from datetime import datetime

from showpy.choreo.match_ops import with_set
from showpy.choreo.models import PathRow, PathStyle, Placement, Set, TransitionSpec, Waypoint
from showpy.core.types import Point
from showpy.show.models import Show
from showpy.show.repositories import InMemoryRepository
from showpy.show.storage import Workspace, open_workspace, save_show

SET_ID = "1"
FIRST_TRANSITION = ("1", "2")
CURVE_COUNTS = 8.0
CURVE_WAYPOINT = Waypoint(x=1, y=7)
SHOW_ID = "s"
CREATE_MESSAGE = "create"
STILL_TOL_M = 1e-6
FULL_TURN = 360.0
HALF_TURN = 180.0


def make_set(*rows: tuple[str, float, float], off: tuple[str, ...] = ()) -> Set:
    """Builds set "1" at count 0 from (entity, x, y) rows.

    Args:
        rows: One (entity, x, y) tuple per spot.
        off: Spots placed off the field.
    """
    placements = tuple(Placement(entity=e, x=x, y=y, on_field=e not in off) for e, x, y in rows)
    return Set(id=SET_ID, counts=0.0, placements=placements)


def make_facing_set(
    *rows: tuple[str, float, float, float],
    off: tuple[str, ...] = (),
    pinned: tuple[str, ...] = (),
) -> Set:
    """Builds set "1" at count 0 from (entity, x, y, facing) rows.

    Args:
        rows: One (entity, x, y, facing) tuple per spot.
        off: Spots placed off the field.
        pinned: Spots whose placement is pinned.
    """
    placements = tuple(
        Placement(entity=e, x=x, y=y, facing=f, on_field=e not in off, pinned=e in pinned)
        for e, x, y, f in rows
    )
    return Set(id=SET_ID, counts=0.0, placements=placements)


def pin_spot(show: Show, set_id: str, entity: str) -> Show:
    """Pins the placement of `entity` in set `set_id`."""
    return with_set(show, show.set(set_id).with_placements({entity: {"pinned": True}}))


def with_curve_rows(show: Show, entities: Iterable[str], locked: bool = False) -> Show:
    """Replaces the transitions with one 8-count 1 -> 2 transition of curved rows.

    Args:
        show: Show whose sets "1" and "2" the transition joins.
        entities: Spots that get a curve row through (1, 7).
        locked: Whether the rows are locked.
    """
    rows = {
        e: PathRow(entity=e, style=PathStyle.CURVE, waypoints=(CURVE_WAYPOINT,), locked=locked)
        for e in entities
    }
    spec = TransitionSpec(id=FIRST_TRANSITION, counts=CURVE_COUNTS, rows=rows)
    return show.model_copy(update={"transitions": (spec,)})


def open_session(show: Show, at: datetime) -> tuple[InMemoryRepository, Workspace, Workspace]:
    """Saves `show` as the designer, then opens an agent workspace on the same repository.

    Returns:
        session: The repository, the agent workspace and the designer workspace.
    """
    repo = InMemoryRepository()
    designer = open_workspace(repo, SHOW_ID, "designer")
    save_show(designer, show, at, CREATE_MESSAGE)
    return repo, open_workspace(repo, SHOW_ID, "agent"), designer


def _angle(origin: Point, p: Point) -> float:
    return math.degrees(math.atan2(p[1] - origin[1], p[0] - origin[0]))


def bisector_rotation(a: Point, b: Point, reach: float, long_way: bool) -> tuple[Point, float]:
    """Returns a pivot and sweep whose arc carries `a` onto `b`, so a ROTATE path ends on target.

    The pivot sits on the perpendicular bisector of a-b, `reach` meters from its midpoint
    (negative reach picks the other side). When a and b coincide the pivot sits `reach`
    meters to the side and the sweep is a full turn.

    Args:
        a: Start point.
        b: Target point.
        reach: Signed distance of the pivot from the midpoint, nonzero.
        long_way: Sweeps the long way around instead of the short way.

    Returns:
        rotation: The pivot point and the signed sweep in degrees.
    """
    chord = math.dist(a, b)
    if chord <= STILL_TOL_M:
        return (a[0] + reach, a[1]), -FULL_TURN if long_way else FULL_TURN
    normal = (-(b[1] - a[1]) / chord, (b[0] - a[0]) / chord)
    mid = ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)
    pivot = (mid[0] + reach * normal[0], mid[1] + reach * normal[1])
    sweep = (_angle(pivot, b) - _angle(pivot, a) + HALF_TURN) % FULL_TURN - HALF_TURN
    if long_way:
        sweep -= math.copysign(FULL_TURN, sweep)
    return pivot, sweep
