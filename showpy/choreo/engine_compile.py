"""
engine_compile.py --- compiles one path row into a MotionPlan: a curve plus a time map

Contains:
    compile_plan(): builds a spot's MotionPlan for one transition, checking continuity
    plan_positions(): evaluates a plan's positions at local counts
    plan_progress(): returns arc lengths travelled at local counts
    plan_breaks(): returns the local counts where a plan's motion may kink
    easing_chord_error(): returns how far an easing strays from its chord on one smooth piece
    curvature_bound(): returns a curve's largest curvature, or None when it has no closed form
"""

import math

import numpy as np
import numpy.typing as npt

from showpy.choreo.engine_models import MotionPiece, MotionPlan, PieceKind
from showpy.choreo.models import Placement
from showpy.choreo.motion import MotionRegistry, ease, easing_breaks
from showpy.choreo.path_models import Easing, PathRow, PathStyle, Route, TransitionSpec
from showpy.core.errors import ContinuityError, PathError
from showpy.core.types import Point, Points
from showpy.geo.curves import (
    AnyCurve,
    ArcCurve,
    CircleCurve,
    CompositeCurve,
    Polyline,
    Segment,
    SplineCurve,
    TrimmedCurve,
    curve_from_construction,
)

type FloatArray = npt.NDArray[np.float64]

CONTINUITY_TOL = 1e-6
CUSTOM_SAMPLES = 257
INVERSE_EASE_STEPS = 53
SMOOTHSTEP_INFLECTION = 0.5
EASE_IN_OUT_TURN = 0.5
PIECE_KIND: dict[PathStyle, PieceKind] = {
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
    route = None if row.route is None else spec.routes.get(row.route)
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
    plan_kind: PieceKind, counts: float, times: tuple[float, ...], dists: tuple[float, ...]
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

    >>> from showpy.choreo.path_models import TransitionSpec, PathRow
    >>> from showpy.choreo.models import Placement
    >>> from showpy.choreo.motion import DEFAULT_MOTIONS
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
    """Returns the arc length travelled at each local count.

    >>> from showpy.choreo.path_models import TransitionSpec, PathRow
    >>> from showpy.choreo.models import Placement
    >>> from showpy.choreo.motion import DEFAULT_MOTIONS
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

    >>> from showpy.choreo.path_models import TransitionSpec, PathRow
    >>> from showpy.choreo.models import Placement
    >>> from showpy.choreo.motion import DEFAULT_MOTIONS
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


def _eased_shares(easing: Easing, targets: FloatArray) -> FloatArray:
    lo, hi = np.zeros_like(targets), np.ones_like(targets)
    for _ in range(INVERSE_EASE_STEPS):
        mid = (lo + hi) / 2
        below = ease(easing, mid) < targets
        lo, hi = np.where(below, mid, lo), np.where(below, hi, mid)
    return hi


def _local_counts(plan: MotionPlan, eased: FloatArray) -> FloatArray:
    start, end = plan.window
    shares = np.clip((eased - start) / (end - start), 0.0, 1.0)
    inner = (shares > 0.0) & (shares < 1.0)
    if plan.easing.kind != "linear" and inner.any():
        shares[inner] = _eased_shares(plan.easing, shares[inner])
    counts: FloatArray = start + shares * (end - start)
    return counts


def _easing_edges(easing: Easing) -> list[float]:
    edges = easing_breaks(easing)
    if easing.kind == "steps":
        n = max(round(max(easing.strength, 1.0)), 1)
        edges += [(i + SMOOTHSTEP_INFLECTION) / n for i in range(n)]
    return edges


def plan_breaks(plan: MotionPlan) -> FloatArray:
    """Returns the sorted local counts inside the window where a plan's motion may kink.

    Time knots, curve breaks (polyline corners, spline knots, joins) and easing breaks are
    mapped through the inverse time map, so a segment can end exactly on each of them. A
    custom motion's sample vertices are not breaks: they sample a function, they are not
    corners the designer drew.

    >>> from showpy.choreo.path_models import TransitionSpec, PathRow, Waypoint
    >>> from showpy.choreo.models import Placement
    >>> from showpy.choreo.motion import DEFAULT_MOTIONS
    >>> spec = TransitionSpec(id=("1", "2"), counts=8.0)
    >>> a, b = Placement(entity="A", x=0, y=0), Placement(entity="A", x=0, y=4)
    >>> row = PathRow(entity="A", style="polyline", waypoints=(Waypoint(x=3, y=0),))
    >>> plan_breaks(compile_plan(row, spec, a, b, DEFAULT_MOTIONS)).tolist()
    [0.0, 3.0, 8.0]
    """
    start, end = plan.window
    sampled = plan.curve is None or plan.style == PathStyle.CUSTOM
    breaks = np.array([]) if sampled or plan.curve is None else plan.curve._breaks()
    eased = np.concatenate([plan.knots_t, np.interp(breaks, plan.knots_s, plan.knots_t)])
    shares = np.asarray(_easing_edges(plan.easing), dtype=float)
    counts = np.concatenate([_local_counts(plan, eased), start + shares * (end - start)])
    return np.unique(np.concatenate([[start, end], counts]))


def _power_chord_error(k: float, a: float, b: float) -> float:
    if k == 1.0 or b <= a:
        return 0.0
    slope = (math.pow(b, k) - math.pow(a, k)) / (b - a)
    x = min(max(math.pow(slope / k, 1.0 / (k - 1.0)), a), b)
    return max(math.pow(a, k) + slope * (x - a) - math.pow(x, k), 0.0)


def _smoothstep(x: float) -> float:
    return x * x * (3.0 - 2.0 * x)


def _smoothstep_chord_error(a: float, b: float) -> float:
    if b <= a:
        return 0.0
    slope = (_smoothstep(b) - _smoothstep(a)) / (b - a)
    root = math.sqrt(max(1.0 - 2.0 * slope / 3.0, 0.0)) / 2.0
    convex = (a + b) / 2 < SMOOTHSTEP_INFLECTION
    x = min(max(SMOOTHSTEP_INFLECTION - root if convex else SMOOTHSTEP_INFLECTION + root, a), b)
    return abs(_smoothstep(a) + slope * (x - a) - _smoothstep(x))


def easing_chord_error(easing: Easing, u0: float, u1: float) -> float:
    """Returns the largest gap between an easing and its chord over shares [u0, u1].

    The piece must hold no easing break and no smoothstep inflection, so the easing is convex
    or concave on it and the gap peaks where its slope equals the chord's. The gap never exceeds
    the linear-interpolation bound, h^2/8 times the largest second derivative. Affine pieces
    have no gap.

    Args:
        easing: Easing whose distance share is compared with its chord.
        u0: Time share where the piece starts.
        u1: Time share where the piece ends.

    Returns:
        gap: Largest distance-share difference between the easing and its chord.

    >>> easing_chord_error(Easing(kind="ease_in", strength=2.0), 0.0, 1.0)
    0.25
    """
    k = max(easing.strength, 1.0)
    mid = (u0 + u1) / 2
    if easing.kind == "ease_in":
        return _power_chord_error(k, u0, u1)
    if easing.kind == "ease_out":
        return _power_chord_error(k, 1.0 - u1, 1.0 - u0)
    if easing.kind == "ease_in_out" and mid < EASE_IN_OUT_TURN:
        return _power_chord_error(k, 2.0 * u0, 2.0 * u1) / 2
    if easing.kind == "ease_in_out":
        return _power_chord_error(k, 2.0 - 2.0 * u1, 2.0 - 2.0 * u0) / 2
    if easing.kind == "steps":
        n = max(round(k), 1)
        step = min(math.floor(mid * n), n - 1)
        return _smoothstep_chord_error(u0 * n - step, u1 * n - step) / n
    return 0.0


def curvature_bound(curve: AnyCurve) -> float | None:
    """Returns the largest curvature of a curve with exact arc length, or None when opaque.

    Table-based kinds (spline, ellipse, Bézier, SVG, spiral) have no cheap closed form; the
    engine probes them instead.

    >>> from showpy.geo.curves import ArcCurve
    >>> curvature_bound(ArcCurve(center=(0, 0), radius=2.0, start_deg=0, sweep_deg=90))
    0.5
    """
    if isinstance(curve, Segment | Polyline):
        return 0.0
    if isinstance(curve, ArcCurve | CircleCurve):
        return 1.0 / curve.radius
    if isinstance(curve, TrimmedCurve):
        return curvature_bound(curve.base)
    if isinstance(curve, CompositeCurve):
        bounds = [curvature_bound(part) for part in curve.parts]
        known = [b for b in bounds if b is not None]
        return max(known) if len(known) == len(bounds) else None
    return None
