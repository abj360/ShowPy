"""
routes.py --- show-aware path builders: follow-the-leader routes, counter-marches, L-moves, rotation

These need placements from the show; pure row edits live in paths.py.

Contains:
    make_route(): builds a follow-the-leader route through the file and the leader's waypoints
    follow(): assigns FOLLOW rows for a route's members
    follow_and_set_targets(): writes followers' end positions into the next set and assigns rows
    counter_march(): turns a file back on itself alongside its own path
    two_part(): makes L-shaped moves, x first or y first or through a corner point
    rotate_path(): rotates spots about a pivot point or their center
"""

import math
from collections.abc import Sequence
from typing import Literal

import numpy as np

from e8s.choreo.models import PlacementPatch, Set
from e8s.choreo.path_models import (
    FacingMode,
    PathRow,
    PathStyle,
    Route,
    TransitionSpec,
    Waypoint,
)
from e8s.choreo.paths import set_style
from e8s.core.errors import LockedError, PathError
from e8s.core.ids import SpotId, TransitionId, format_tid
from e8s.core.types import Point
from e8s.geo.curves import (
    AnyCurve,
    ArcCurve,
    CompositeCurve,
    Polyline,
    Segment,
)
from e8s.show.locks import locked_row_changes, require_unlocked
from e8s.show.models import Show

MIN_GAP = 1e-9
COUNTER_MARCH_WIDTH_M = 1.0
DEFAULT_ROUTE_ID = "route"


def _dedupe(points: Sequence[Point]) -> tuple[Point, ...]:
    return _dedupe_indexed(points)[0]


def _dedupe_indexed(points: Sequence[Point]) -> tuple[tuple[Point, ...], list[int]]:
    """Drops consecutive repeats and maps every input point to the vertex that kept it.

    A route may pass one place twice, so a point's vertex comes from its position in the
    sequence, never from looking its coordinates up.

    Returns:
        vertices: The points with consecutive repeats removed.
        vertex_of: For each input point, the index of its vertex in `vertices`.
    """
    kept: list[Point] = []
    vertex_of: list[int] = []
    for p in points:
        if not kept or math.dist(kept[-1], p) > MIN_GAP:
            kept.append((float(p[0]), float(p[1])))
        vertex_of.append(len(kept) - 1)
    return tuple(kept), vertex_of


def make_route(
    show: Show,
    tid: tuple[str, str],
    leader: str,
    waypoints: Sequence[Point],
    members: Sequence[str] | None = None,
    spacing: Literal["keep"] = "keep",
    route_id: str = DEFAULT_ROUTE_ID,
) -> Route:
    """Builds a route that runs back through the file, then the leader's waypoints.

    `members` is the file, leader first; each follower starts on the route at its current
    spot and every member travels the leader's distance, so spacing along the route is kept.
    Each offset is the route curve's own arc length at the member's vertex, so a member starts
    exactly on that vertex. Without waypoints the leader heads to its placement in the next set.

    >>> from e8s.examples import two_set_show
    >>> make_route(two_set_show(), ("1", "2"), "T4", [(6.0, 15.0)], ["T4", "T3"]).travel
    10.0
    """
    if spacing != "keep":
        raise PathError(f"unknown spacing {spacing!r}", hint='use spacing="keep"')
    file = list(members or [leader])
    if file[0] != leader:
        raise PathError("the leader must be first in members", hint="put the leader first")
    start_set = show.set(tid[0])
    starts = [start_set.get(m).point for m in reversed(file)]
    ahead = list(waypoints) or [show.set(tid[1]).get(leader).point]
    vertices, vertex_of = _dedupe_indexed([*starts, *ahead])
    if len(vertices) < 2:
        raise PathError(
            "the route is a single point",
            hint="give a waypoint away from the leader's start",
            context={"leader": leader},
        )
    curve = (
        Polyline(points=vertices) if len(vertices) > 2 else Segment(a=vertices[0], b=vertices[1])
    )
    lengths = curve._breaks()
    offsets = tuple(float(lengths[v]) for v in reversed(vertex_of[: len(file)]))
    return Route._validated(
        {
            "id": route_id,
            "curve": curve,
            "members": tuple(file),
            "offsets": offsets,
            "travel": float(lengths[-1]) - offsets[0],
        }
    )


def follow(
    spec: TransitionSpec,
    route: Route,
    start: float = 0.0,
    end: float | None = None,
    *,
    force: bool = False,
) -> TransitionSpec:
    """Stores the route with its window and assigns FOLLOW rows to its members.

    Args:
        spec: Transition that receives the route and the rows.
        route: Route the members follow.
        start: Local count at which the route window opens.
        end: Local count at which the route window closes; the transition end when None.
        force: Rewrites locked rows instead of raising LockedError.

    Returns:
        edited_spec: The transition with the route stored and member rows set to FOLLOW.

    >>> from e8s.examples import two_set_show
    >>> route = make_route(two_set_show(), ("1", "2"), "T4", [(6.0, 15.0)])
    >>> follow(TransitionSpec(id=("1", "2"), counts=8.0), route).row("T4").style.value
    'follow'
    """
    timed = route.replace(start=start, end=end)
    routed = spec.replace(routes={**spec.routes, route.id: timed})
    touched = locked_row_changes(spec, routed)
    if touched and not force:
        raise LockedError(
            f"route {route.id} is followed by locked rows: {', '.join(touched)}",
            hint="use a new route id, unlock the rows first, or pass force=True",
            context={"route": route.id, "touched": touched},
        )
    spec = routed
    return set_style(spec, list(route.members), PathStyle.FOLLOW, force=force, route=route.id)


def follow_and_set_targets(
    show: Show,
    tid: tuple[str, str],
    route: Route,
    start: float = 0.0,
    end: float | None = None,
    *,
    force: bool = False,
) -> Show:
    """Writes every member's end on the route into the next set, then assigns FOLLOW rows.

    Args:
        show: Show that holds the transition and its sets.
        tid: Transition whose target set and rows are edited.
        route: Route the members follow.
        start: Local count at which the route window opens.
        end: Local count at which the route window closes; the transition end when None.
        force: Edits pinned placements and locked rows instead of raising LockedError.

    Returns:
        edited_show: The show with member targets moved and FOLLOW rows assigned.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> route = make_route(show, ("1", "2"), "T4", [(6.0, 15.0)])
    >>> follow_and_set_targets(show, ("1", "2"), route).set("2").get("T4").point
    (6.0, 15.0)
    """
    curve = route.curve
    target = show.set(tid[1])
    updates: dict[SpotId, PlacementPatch] = {}
    for member, offset in zip(route.members, route.offsets, strict=True):
        p = curve.point_at(offset + route.travel)
        updates[member] = PlacementPatch(x=float(p[0]), y=float(p[1]))
    new_target = target.with_placements(updates, force=force)
    spec = follow(show.transition(tid), route, start, end, force=force)
    return _store(show, spec.id, spec, new_target)


def _store(show: Show, tid: TransitionId, spec: TransitionSpec, target: Set) -> Show:
    sets = tuple(target if s.id == tid[1] else s for s in show.sets)
    others = tuple(t for t in show.transitions if t.id != tid)
    return show.replace(sets=sets, transitions=(*others, spec))


def _pt(v: np.ndarray) -> Point:
    return float(v[0]), float(v[1])


def _u_turn(
    file_points: tuple[Point, ...], turn_at: Point
) -> tuple[AnyCurve, tuple[float, ...], float]:
    """Builds the counter-march route: along the file, to the turn, round it and back.

    Offsets and travel are read from the route's own pieces, so the rear member starts on its
    vertex and finishes exactly where the half circle ends.

    Returns:
        curve: The whole counter-march route.
        offsets: Arc length of each file point on the route, in `file_points` order.
        travel: Arc length at the end of the half circle, the distance every member travels.
    """
    last = file_points[-1]
    leader = np.asarray(last)
    heading = np.asarray(turn_at) - leader
    approach = float(np.linalg.norm(heading))
    if approach <= MIN_GAP:
        raise PathError("turn point is on the leader", hint="put turn_at ahead of the leader")
    radius = COUNTER_MARCH_WIDTH_M / 2
    forward = heading / approach
    left = np.array([-forward[1], forward[0]])
    turn_out = np.asarray(turn_at) + left * 2 * radius
    lead_in = (
        Polyline(points=(*file_points, turn_at))
        if len(file_points) > 1
        else Segment(a=last, b=turn_at)
    )
    back_end = turn_out - forward * lead_in.length()
    arc = ArcCurve(
        center=_pt(np.asarray(turn_at) + left * radius),
        radius=radius,
        start_deg=math.degrees(math.atan2(-left[1], -left[0])),
        sweep_deg=180.0,
    )
    curve = CompositeCurve(parts=(lead_in, arc, Segment(a=_pt(turn_out), b=_pt(back_end))))
    offsets = tuple(float(v) for v in lead_in._breaks()[: len(file_points)])
    return curve, offsets, lead_in.length() + arc.length()


def counter_march(
    show: Show,
    tid: tuple[str, str],
    sel: Sequence[str],
    turn_at: Point,
    counts_per_turn: float = 2.0,
    *,
    force: bool = False,
) -> Show:
    """Turns a file (leader first) back on itself, passing 1 m to the leader's left.

    Every member walks to `turn_at`, turns left through a half circle and comes back alongside
    the file; the last member finishes just out of the turn, so the file ends reversed. Facing
    follows travel, blended over `counts_per_turn`.

    Args:
        show: Show that holds the transition and its sets.
        tid: Transition the counter-march runs in.
        sel: Spots of the file, leader first.
        turn_at: Point where the leader starts the turn.
        counts_per_turn: Counts over which facing blends to the direction of travel.
        force: Edits pinned placements and locked rows instead of raising LockedError.

    Returns:
        edited_show: The show with the file's targets and rows rewritten.

    >>> from e8s.examples import two_set_show
    >>> s = counter_march(two_set_show(), ("1", "2"), ["T4", "T3", "T2", "T1"], (6.0, 8.0))
    >>> round(s.set("2").get("T1").point[1], 6)
    8.0
    """
    file = list(sel)
    if not file:
        raise PathError(
            "a counter-march needs at least one member",
            hint="pass at least one spot id, leader first",
            context={"transition": format_tid(tid)},
        )
    start_set = show.set(tid[0])
    file_points = _dedupe([start_set.get(m).point for m in reversed(file)])
    curve, offsets, travel = _u_turn(file_points, (float(turn_at[0]), float(turn_at[1])))
    route = Route._validated(
        {
            "id": f"counter-march-{file[0]}",
            "curve": curve,
            "members": tuple(file),
            "offsets": tuple(reversed(offsets)),
            "travel": travel,
        }
    )
    moved = follow_and_set_targets(show, tid, route, force=force)
    spec = moved.transition(tid)
    rows = {
        row.entity: row.replace(facing_mode=FacingMode.TRAVEL, turn_counts=counts_per_turn)
        for row in map(spec.row, file)
    }
    spec = spec.replace(rows={**spec.rows, **rows})
    return _store(moved, spec.id, spec, moved.set(spec.id.to_set))


def two_part(
    show: Show,
    spec: TransitionSpec,
    sel: Sequence[str],
    corner: Literal["x_first", "y_first"] | Point,
    split: float | None = None,
    *,
    force: bool = False,
) -> TransitionSpec:
    """Makes L-shaped moves through a corner, reached at local count `split` when given.

    Args:
        show: Show that holds the sets the moves run between.
        spec: Transition whose rows are edited.
        sel: Spots that get an L-shaped move.
        corner: Corner of the L: x first, y first, or an explicit point.
        split: Local count at which the corner is reached.
        force: Edits locked rows instead of raising LockedError.

    Returns:
        edited_spec: The transition with the selected rows rewritten.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> two_part(show, show.transition(("1", "2")), ["T1"], "x_first").row("T1").style.value
    'polyline'
    """
    a, b = show.set(spec.id[0]), show.set(spec.id[1])
    rows = dict(spec.rows)
    for entity in sel:
        p, q = a.get(entity).point, b.get(entity).point
        if corner == "x_first":
            point = (q[0], p[1])
        elif corner == "y_first":
            point = (p[0], q[1])
        else:
            point = corner
        row = spec.row(entity)
        waypoint = Waypoint(x=point[0], y=point[1], at=split)
        rows[row.entity] = PathRow._validated(
            {**row._dumped(), "style": PathStyle.POLYLINE, "waypoints": (waypoint,)}
        )
    edited = spec.replace(rows=rows)
    _require_rows_unlocked(show, spec, edited, force=force)
    return edited


def _require_rows_unlocked(
    show: Show, spec: TransitionSpec, edited: TransitionSpec, *, force: bool
) -> None:
    """Raises LockedError when `edited` changes a locked row, unless forced.

    A row is locked when the transition stored in `show` locks it or the caller's `spec` does,
    so a copy with the locks cleared cannot bypass the stored ones.

    Args:
        show: Show that owns the transition.
        spec: Transition as the caller holds it before the edit.
        edited: Transition after the edit.
        force: Whether the caller overrides locks and pins.
    """
    others = tuple(t for t in show.transitions if t.id != spec.id)
    after = show.replace(transitions=(*others, edited))
    require_unlocked(show, after, force=force)
    require_unlocked(show.replace(transitions=(*others, spec)), after, force=force)


def rotate_path(
    show: Show,
    spec: TransitionSpec,
    sel: Sequence[str],
    pivot: Point | Literal["center"],
    deg: float,
    *,
    force: bool = False,
) -> TransitionSpec:
    """Rotates spots about a pivot; "center" uses the centroid of their starting spots.

    Args:
        show: Show that holds the sets the rotation runs between.
        spec: Transition whose rows are edited.
        sel: Spots that rotate.
        pivot: Point to rotate about, or "center" for the centroid of the starting spots.
        deg: Sweep in degrees, counter-clockwise positive.
        force: Edits locked rows instead of raising LockedError.

    Returns:
        edited_spec: The transition with the selected rows set to ROTATE.

    >>> from e8s.examples import two_set_show
    >>> show = two_set_show()
    >>> rotate_path(show, show.transition(("1", "2")), ["T1"], (0.0, 0.0), 90.0).row("T1").pivot
    (0.0, 0.0)
    """
    ids = list(sel)
    if pivot == "center":
        pts = show.set(spec.id[0]).positions(ids)
        center = pts.mean(axis=0)
        pivot = (float(center[0]), float(center[1]))
    edited = set_style(spec, ids, PathStyle.ROTATE, force=force, pivot=pivot, sweep_deg=deg)
    _require_rows_unlocked(show, spec, edited, force=force)
    return edited
