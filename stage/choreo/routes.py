#!/usr/bin/env python3

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

import itertools
import math
from collections.abc import Sequence
from typing import Literal

import numpy as np

from stage.choreo.models import (
    FacingMode,
    PathRow,
    PathStyle,
    Route,
    Set,
    TransitionSpec,
    Waypoint,
)
from stage.choreo.paths import set_style
from stage.core.errors import PathError
from stage.core.ids import SpotId, TransitionId
from stage.core.types import Point
from stage.geo.curves import (
    AnyCurve,
    ArcCurve,
    CompositeCurve,
    Polyline,
    Segment,
    curve_from_construction,
)
from stage.show.models import Show

MIN_GAP = 1e-9
COUNTER_MARCH_WIDTH_M = 1.0
DEFAULT_ROUTE_ID = "route"


def _dedupe(points: Sequence[Point]) -> tuple[Point, ...]:
    kept: list[Point] = []
    for p in points:
        if not kept or math.dist(kept[-1], p) > MIN_GAP:
            kept.append((float(p[0]), float(p[1])))
    return tuple(kept)


def _cumulative(points: Sequence[Point]) -> list[float]:
    lengths = [0.0]
    for a, b in itertools.pairwise(points):
        lengths.append(lengths[-1] + math.dist(a, b))
    return lengths


def make_route(
    show: Show,
    tid: TransitionId,
    leader: SpotId,
    waypoints: Sequence[Point],
    members: Sequence[SpotId] | None = None,
    spacing: Literal["keep"] = "keep",
    route_id: str = DEFAULT_ROUTE_ID,
) -> Route:
    """Builds a route that runs back through the file, then the leader's waypoints.

    `members` is the file, leader first; each follower starts on the route at its current
    spot and every member travels the leader's distance, so spacing along the route is kept.
    Without waypoints the leader heads to its placement in the next set.

    >>> from stage.examples import two_set_show
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
    vertices = _dedupe([*starts, *ahead])
    lengths = _cumulative(vertices)
    offsets = tuple(lengths[vertices.index(_dedupe([start_set.get(m).point])[0])] for m in file)
    curve = (
        Polyline(points=vertices) if len(vertices) > 2 else Segment(a=vertices[0], b=vertices[1])
    )
    return Route(
        id=route_id,
        curve=curve.construction(),
        members=tuple(file),
        offsets=offsets,
        travel=lengths[-1] - offsets[0],
    )


def follow(
    spec: TransitionSpec, route: Route, start: float = 0.0, end: float | None = None
) -> TransitionSpec:
    """Stores the route with its window and assigns FOLLOW rows to its members.

    >>> from stage.examples import two_set_show
    >>> route = make_route(two_set_show(), ("1", "2"), "T4", [(6.0, 15.0)])
    >>> follow(TransitionSpec(id=("1", "2"), counts=8.0), route).row("T4").style.value
    'follow'
    """
    timed = route.model_copy(update={"start": start, "end": end})
    spec = spec.model_copy(update={"routes": {**spec.routes, route.id: timed}})
    return set_style(spec, list(route.members), PathStyle.FOLLOW, route=route.id)


def follow_and_set_targets(
    show: Show, tid: TransitionId, route: Route, start: float = 0.0, end: float | None = None
) -> Show:
    """Writes every member's end on the route into the next set, then assigns FOLLOW rows.

    >>> from stage.examples import two_set_show
    >>> show = two_set_show()
    >>> route = make_route(show, ("1", "2"), "T4", [(6.0, 15.0)])
    >>> follow_and_set_targets(show, ("1", "2"), route).set("2").get("T4").point
    (6.0, 15.0)
    """
    curve = curve_from_construction(route.curve)
    target = show.set(tid[1])
    updates = {}
    for member, offset in zip(route.members, route.offsets, strict=True):
        p = curve.point_at(offset + route.travel)
        updates[member] = {"x": float(p[0]), "y": float(p[1])}
    new_target = target.with_placements(updates)
    spec = follow(show.transition(tid), route, start, end)
    return _store(show, tid, spec, new_target)


def _store(show: Show, tid: TransitionId, spec: TransitionSpec, target: Set) -> Show:
    sets = tuple(target if s.id == tid[1] else s for s in show.sets)
    others = tuple(t for t in show.transitions if t.id != tid)
    return show.model_copy(update={"sets": sets, "transitions": (*others, spec)})


def _pt(v: np.ndarray) -> Point:
    return float(v[0]), float(v[1])


def _u_turn(file_points: tuple[Point, ...], turn_at: Point) -> tuple[AnyCurve, float]:
    leader = np.asarray(file_points[-1])
    heading = np.asarray(turn_at) - leader
    approach = float(np.linalg.norm(heading))
    if approach <= MIN_GAP:
        raise PathError("turn point is on the leader", hint="put turn_at ahead of the leader")
    radius = COUNTER_MARCH_WIDTH_M / 2
    forward = heading / approach
    left = np.array([-forward[1], forward[0]])
    turn_out = np.asarray(turn_at) + left * 2 * radius
    back_end = turn_out - forward * (approach + _cumulative(file_points)[-1])
    lead_in = (
        Polyline(points=(*file_points, turn_at))
        if len(file_points) > 1
        else Segment(a=file_points[0], b=turn_at)
    )
    arc = ArcCurve(
        center=_pt(np.asarray(turn_at) + left * radius),
        radius=radius,
        start_deg=math.degrees(math.atan2(-left[1], -left[0])),
        sweep_deg=180.0,
    )
    curve = CompositeCurve(parts=(lead_in, arc, Segment(a=_pt(turn_out), b=_pt(back_end))))
    return curve, approach + math.pi * radius


def counter_march(
    show: Show,
    tid: TransitionId,
    sel: Sequence[SpotId],
    turn_at: Point,
    counts_per_turn: float = 2.0,
) -> Show:
    """Turns a file (leader first) back on itself, passing 1 m to the leader's left.

    Every member walks to `turn_at`, turns left through a half circle and comes back alongside
    the file; the last member finishes just out of the turn, so the file ends reversed. Facing
    follows travel, blended over `counts_per_turn`.

    >>> from stage.examples import two_set_show
    >>> s = counter_march(two_set_show(), ("1", "2"), ["T4", "T3", "T2", "T1"], (6.0, 8.0))
    >>> round(s.set("2").get("T1").point[1], 6)
    8.0
    """
    file = list(sel)
    start_set = show.set(tid[0])
    file_points = _dedupe([start_set.get(m).point for m in reversed(file)])
    curve, turn_length = _u_turn(file_points, (float(turn_at[0]), float(turn_at[1])))
    lengths = _cumulative(file_points)
    route = Route(
        id=f"counter-march-{file[0]}",
        curve=curve.construction(),
        members=tuple(file),
        offsets=tuple(reversed(lengths)),
        travel=lengths[-1] + turn_length,
    )
    moved = follow_and_set_targets(show, tid, route)
    spec = moved.transition(tid)
    rows = {
        e: spec.row(e).model_copy(
            update={"facing_mode": FacingMode.TRAVEL, "turn_counts": counts_per_turn}
        )
        for e in file
    }
    spec = spec.model_copy(update={"rows": {**spec.rows, **rows}})
    return _store(moved, tid, spec, moved.set(tid[1]))


def two_part(
    show: Show,
    spec: TransitionSpec,
    sel: Sequence[SpotId],
    corner: Literal["x_first", "y_first"] | Point,
    split: float | None = None,
) -> TransitionSpec:
    """Makes L-shaped moves through a corner, reached at local count `split` when given.

    >>> from stage.examples import two_set_show
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
        rows[entity] = PathRow.model_validate(
            {**row.model_dump(), "style": PathStyle.POLYLINE, "waypoints": (waypoint,)}
        )
    return spec.model_copy(update={"rows": rows})


def rotate_path(
    show: Show,
    spec: TransitionSpec,
    sel: Sequence[SpotId],
    pivot: Point | Literal["center"],
    deg: float,
) -> TransitionSpec:
    """Rotates spots about a pivot; "center" uses the centroid of their starting spots.

    >>> from stage.examples import two_set_show
    >>> show = two_set_show()
    >>> rotate_path(show, show.transition(("1", "2")), ["T1"], (0.0, 0.0), 90.0).row("T1").pivot
    (0.0, 0.0)
    """
    ids = list(sel)
    if pivot == "center":
        pts = show.set(spec.id[0]).positions(ids)
        center = pts.mean(axis=0)
        pivot = (float(center[0]), float(center[1]))
    return set_style(spec, ids, PathStyle.ROTATE, pivot=pivot, sweep_deg=deg)
