#!/usr/bin/env python3

"""
engine_compile.py --- compiles one path row into a MotionPlan: a curve plus a time map

Contains:
    compile_plan(): builds a spot's MotionPlan for one transition, checking continuity
    plan_positions(): evaluates a plan's positions at local counts
    plan_progress(): returns arc lengths travelled at local counts
"""

import math

import numpy as np
import numpy.typing as npt

from stage.choreo.engine_models import MotionPiece, MotionPlan
from stage.choreo.models import PathRow, PathStyle, Placement, Route, TransitionSpec
from stage.choreo.motion import MotionRegistry, ease
from stage.core.errors import ContinuityError, PathError
from stage.core.types import Point, Points
from stage.geo.curves import (
    AnyCurve,
    ArcCurve,
    Polyline,
    Segment,
    SplineCurve,
    TrimmedCurve,
    curve_from_construction,
)

type FloatArray = npt.NDArray[np.float64]

CONTINUITY_TOL = 1e-6
CUSTOM_SAMPLES = 257
PIECE_KIND = {
    PathStyle.STRAIGHT: "line",
    PathStyle.POLYLINE: "line",
    PathStyle.CURVE: "curve",
    PathStyle.ROTATE: "arc",
    PathStyle.FOLLOW: "route",
    PathStyle.CUSTOM: "custom",
    PathStyle.HOLD: "hold",
}
WAYPOINT_STYLES = (PathStyle.STRAIGHT, PathStyle.POLYLINE, PathStyle.CURVE)


def _require_end(entity: str, curve_end: Point, target: Placement, style: PathStyle) -> None:
    if math.dist(curve_end, target.point) > CONTINUITY_TOL:
        raise ContinuityError(
            f"{style.value} path of {entity} ends at {curve_end}, not on its target {target.point}",
            hint="change the path parameters or the target placement so they agree",
            context={"entity": entity, "end": curve_end, "target": target.point},
        )


def _vertices(row: PathRow, a: Placement, b: Placement) -> tuple[list[Point], list[int]]:
    raw = [a.point, *((w.x, w.y) for w in row.waypoints), b.point]
    kept: list[Point] = []
    index_of: list[int] = []
    for p in raw:
        if not kept or math.dist(kept[-1], p) > CONTINUITY_TOL:
            kept.append(p)
        index_of.append(len(kept) - 1)
    return kept, index_of


def _waypoint_curve(row: PathRow, a: Placement, b: Placement) -> tuple[AnyCurve | None, list[int]]:
    pts, index_of = _vertices(row, a, b)
    if len(pts) == 1:
        return None, index_of
    if len(pts) == 2:
        return Segment(a=pts[0], b=pts[1]), index_of
    if row.style == PathStyle.CURVE:
        return SplineCurve(points=tuple(pts)), index_of
    return Polyline(points=tuple(pts)), index_of


def _rotate_curve(row: PathRow, a: Placement, b: Placement) -> AnyCurve | None:
    if row.pivot is None or row.sweep_deg is None:
        raise PathError(
            f"rotate path of {row.entity} needs pivot and sweep_deg",
            hint="use rotate_path(spec, sel, pivot, deg)",
            context={"entity": row.entity},
        )
    radius = math.dist(a.point, row.pivot)
    if radius <= CONTINUITY_TOL or row.sweep_deg == 0:
        _require_end(row.entity, a.point, b, row.style)
        return None
    start_deg = math.degrees(math.atan2(a.y - row.pivot[1], a.x - row.pivot[0]))
    return ArcCurve(center=row.pivot, radius=radius, start_deg=start_deg, sweep_deg=row.sweep_deg)


def _route(row: PathRow, spec: TransitionSpec) -> Route:
    route = spec.routes.get(row.route or "")
    if route is None or row.entity not in route.members:
        raise PathError(
            f"follow path of {row.entity} names no route it belongs to",
            hint="create the route with make_route and assign it with follow",
            context={"entity": row.entity, "route": row.route},
        )
    return route


def _follow_curve(row: PathRow, spec: TransitionSpec, a: Placement) -> AnyCurve:
    route = _route(row, spec)
    base = curve_from_construction(route.curve)
    s0 = route.offsets[route.members.index(row.entity)]
    s1 = s0 + route.travel
    if not (min(s0, s1) >= -CONTINUITY_TOL and max(s0, s1) <= base.length() + CONTINUITY_TOL):
        raise PathError(
            f"{row.entity} would leave route {route.id}",
            hint="extend the route or shorten its travel",
            context={"s0": s0, "s1": s1, "length": base.length()},
        )
    curve = TrimmedCurve(base=base, s0=s0, s1=s1)
    start = curve.point_at(0.0)
    if math.dist((float(start[0]), float(start[1])), a.point) > CONTINUITY_TOL:
        raise ContinuityError(
            f"{row.entity} does not start on route {route.id}",
            hint="set the member offset to its starting arc length on the route",
            context={"entity": row.entity, "offset": s0},
        )
    return curve


def _custom_curve(
    row: PathRow, a: Placement, b: Placement, motions: MotionRegistry
) -> AnyCurve | None:
    if row.motion_fn is None:
        raise PathError(f"custom path of {row.entity} names no motion", hint="set motion_fn")
    fn = motions.get(row.motion_fn)
    u = np.linspace(0.0, 1.0, CUSTOM_SAMPLES)
    pts = np.asarray(fn(u, a.point, b.point, row.motion_params), dtype=float)
    keep = np.concatenate([[True], np.linalg.norm(np.diff(pts, axis=0), axis=1) > CONTINUITY_TOL])
    vertices = tuple((float(x), float(y)) for x, y in pts[keep])
    if math.dist(vertices[0], a.point) > CONTINUITY_TOL:
        raise ContinuityError(
            f"motion {row.motion_fn} does not start at {row.entity}", hint="fix it"
        )
    return Polyline(points=vertices) if len(vertices) > 1 else None


def _curve_for(
    row: PathRow, spec: TransitionSpec, a: Placement, b: Placement, motions: MotionRegistry
) -> tuple[AnyCurve | None, list[int]]:
    if row.style in WAYPOINT_STYLES:
        return _waypoint_curve(row, a, b)
    if row.style == PathStyle.HOLD:
        _require_end(row.entity, a.point, b, row.style)
        return None, []
    if row.style == PathStyle.ROTATE:
        return _rotate_curve(row, a, b), []
    if row.style == PathStyle.FOLLOW:
        return _follow_curve(row, spec, a), []
    return _custom_curve(row, a, b, motions), []


def _window(row: PathRow, spec: TransitionSpec) -> tuple[float, float]:
    if row.style == PathStyle.FOLLOW:
        route = _route(row, spec)
        start, end = route.start, spec.counts if route.end is None else route.end
    else:
        start, end = row.window(spec.counts)
    if end > spec.counts + CONTINUITY_TOL or start >= end:
        raise PathError(
            f"window {start}–{end} of {row.entity} does not fit {spec.counts} counts",
            hint="keep 0 <= start < end <= counts",
            context={"entity": row.entity, "start": start, "end": end},
        )
    return start, end


def _knots(
    row: PathRow, curve: AnyCurve | None, index_of: list[int], window: tuple[float, float]
) -> tuple[tuple[float, ...], tuple[float, ...]]:
    start, end = window
    length = 0.0 if curve is None else curve.length()
    times, dists = [start], [0.0]
    breaks = curve._breaks() if curve is not None else np.array([0.0])
    for i, waypoint in enumerate(row.waypoints):
        if waypoint.at is None:
            continue
        if not times[-1] < waypoint.at < end:
            raise PathError(
                f"waypoint {i} of {row.entity} is timed at {waypoint.at}, outside the move",
                hint="time waypoints in increasing order strictly inside the window",
                context={"entity": row.entity, "at": waypoint.at},
            )
        times.append(waypoint.at)
        dists.append(float(breaks[index_of[i + 1]]) if len(breaks) > index_of[i + 1] else length)
    times.append(end)
    dists.append(length)
    return tuple(times), tuple(dists)


def _pieces(
    plan_kind: str, counts: float, times: tuple[float, ...], dists: tuple[float, ...]
) -> tuple[MotionPiece, ...]:
    pieces = []
    if times[0] > 0:
        pieces.append(MotionPiece(t0=0.0, t1=times[0], kind="hold"))
    for t0, t1, s0, s1 in zip(times[:-1], times[1:], dists[:-1], dists[1:], strict=True):
        kind = plan_kind if s1 > s0 else "hold"
        pieces.append(
            MotionPiece(t0=t0, t1=t1, kind=kind, params={"s0": s0, "s1": s1}, length=s1 - s0)
        )
    if times[-1] < counts:
        pieces.append(MotionPiece(t0=times[-1], t1=counts, kind="hold"))
    return tuple(pieces)


def compile_plan(
    row: PathRow, spec: TransitionSpec, a: Placement, b: Placement, motions: MotionRegistry
) -> MotionPlan:
    """Builds a spot's MotionPlan for one transition, raising ContinuityError on a miss.

    >>> from stage.choreo.models import TransitionSpec, PathRow, Placement
    >>> from stage.choreo.motion import DEFAULT_MOTIONS
    >>> spec = TransitionSpec(id=("1", "2"), counts=8.0)
    >>> a, b = Placement(entity="A", x=0, y=0), Placement(entity="A", x=0, y=4)
    >>> compile_plan(PathRow(entity="A"), spec, a, b, DEFAULT_MOTIONS).curve.length()
    4.0
    """
    window = _window(row, spec)
    curve, index_of = _curve_for(row, spec, a, b, motions)
    if curve is not None:
        end = curve.point_at(curve.length())
        _require_end(row.entity, (float(end[0]), float(end[1])), b, row.style)
    times, dists = _knots(row, curve, index_of, window)
    return MotionPlan(
        entity=row.entity,
        pieces=_pieces(PIECE_KIND[row.style] if curve else "hold", spec.counts, times, dists),
        start=a,
        end=b,
        style=row.style,
        curve=curve,
        window=window,
        knots_t=times,
        knots_s=dists,
        easing=row.easing,
        facing_mode=row.facing_mode,
        facing_value=row.facing_value,
        turn_counts=row.turn_counts,
        turn_direction=row.turn_direction,
    )


def plan_progress(plan: MotionPlan, tau: FloatArray) -> FloatArray:
    """Returns the arc length travelled at each local count (ADR 0002, decision 1).

    >>> from stage.choreo.models import TransitionSpec, PathRow, Placement
    >>> from stage.choreo.motion import DEFAULT_MOTIONS
    >>> spec = TransitionSpec(id=("1", "2"), counts=8.0)
    >>> a, b = Placement(entity="A", x=0, y=0), Placement(entity="A", x=0, y=4)
    >>> plan = compile_plan(PathRow(entity="A"), spec, a, b, DEFAULT_MOTIONS)
    >>> plan_progress(plan, np.array([4.0])).tolist()
    [2.0]
    """
    start, end = plan.window
    u = np.clip((np.asarray(tau, dtype=float) - start) / (end - start), 0.0, 1.0)
    eased = start + ease(plan.easing, u) * (end - start)
    progress: FloatArray = np.interp(eased, plan.knots_t, plan.knots_s)
    return progress


def plan_positions(plan: MotionPlan, tau: FloatArray) -> Points:
    """Evaluates a plan's positions at local counts.

    >>> from stage.choreo.models import TransitionSpec, PathRow, Placement
    >>> from stage.choreo.motion import DEFAULT_MOTIONS
    >>> spec = TransitionSpec(id=("1", "2"), counts=8.0)
    >>> a, b = Placement(entity="A", x=0, y=0), Placement(entity="A", x=0, y=4)
    >>> plan = compile_plan(PathRow(entity="A"), spec, a, b, DEFAULT_MOTIONS)
    >>> plan_positions(plan, np.array([2.0])).tolist()
    [[0.0, 1.0]]
    """
    taus = np.asarray(tau, dtype=float)
    if plan.curve is None:
        return np.tile(np.asarray(plan.start.point, dtype=float), (len(taus), 1))
    return plan.curve.point_at(plan_progress(plan, taus))
