"""
measure.py --- static measures of points, curves and sets: extents, spacing, neighbours, symmetry

Nothing here reads motion over time; positions over time come from the PathEngine. Measures
of a `Set` live in `e8s.choreo.set_measure`; the set names kept here are deprecated stubs.

Contains:
    SetSelector: names the selections a set measure or transform accepts
    SET_TYPE, SHEET_TYPE: name the types a forwarded Set or Sheet argument must have
    wrong_type_error(): builds the error for a Set or Sheet argument of another type
    on_field_ids(): forwards to e8s.choreo.set_measure.on_field_ids, deprecated
    length(): returns the length of a curve or of points read as a polyline
    center(): returns the bounding-box center
    centroid(): returns the mean point (length-weighted for curves)
    bounds(): returns the bounding box
    interval(): summarizes gaps between consecutive or nearest-neighbour spots
    distance(): returns the distance between two points
    distance_matrix(): returns every pairwise distance between two point arrays
    neighbors(): forwards to e8s.choreo.set_measure.neighbors, deprecated
    nearest(): forwards to e8s.choreo.set_measure.nearest, deprecated
    reachable_region(): returns the disk a spot can reach in a number of counts
    mirror_gaps(): returns how far each mirrored point lands from its nearest point
    form_metrics(): summarizes length, spacing, curvature and symmetry of a form's points
    symmetry_error(): forwards to e8s.choreo.set_measure.symmetry_error, deprecated
"""

import math
from collections.abc import Sequence
from typing import Literal

import numpy as np
import shapely
from scipy.optimize import minimize_scalar
from scipy.spatial import cKDTree
from scipy.spatial.distance import cdist

from e8s.core.deprecation import deprecated_call
from e8s.core.errors import GeometryError, SchemaError
from e8s.core.ids import SpotId
from e8s.core.selection import Selection
from e8s.core.types import Box2, Count, Meters, Point, Points, Polygon
from e8s.core.util import as_points
from e8s.geo.curve_base import Curve
from e8s.geo.models import FormMetrics, IntervalStats

ALL = "all"
SET_TYPE = "e8s.choreo.models.Set"
SHEET_TYPE = "e8s.sheet.models.Sheet"
CURVE_TOL = 0.001
DISK_SEGMENTS = 64
STRAIGHT_TOL = 1e-12

type SetSelector = Literal["all"] | Sequence[str] | Selection


def wrong_type_error(function: str, argument: str, value: object, expected: str) -> SchemaError:
    """Builds the error for an argument that must be a `Set` or `Sheet` but is another type.

    Args:
        function: Dotted name of the geo function that received the argument.
        argument: Parameter name.
        value: The object received.
        expected: The type that was needed, as `e8s.choreo.models.Set`.

    Returns:
        error: A SchemaError that names the argument, the type received and the type needed.
    """
    return SchemaError(
        f"`{argument}` must be a {expected}, got {type(value).__name__}",
        hint=f"pass a {expected}; {function}() takes it only to forward to its choreo twin",
        context={"function": function, "argument": argument, "got": type(value).__name__},
    )


@deprecated_call("e8s.choreo.set_measure.on_field_ids", since="0.4.0", remove="0.6.0")
def on_field_ids(s: object, sel: SetSelector = ALL) -> tuple[SpotId, ...]:
    """Returns the selected spots that are on the field; moved to `e8s.choreo.set_measure`."""
    from e8s.choreo.models import Set
    from e8s.choreo.set_measure import on_field_ids as twin

    if not isinstance(s, Set):
        raise wrong_type_error("e8s.geo.measure.on_field_ids", "s", s, SET_TYPE)
    return twin(s, sel)


def _points(obj: Points | Curve) -> Points:
    if isinstance(obj, np.ndarray):
        return as_points(obj)
    return obj.to_polyline(CURVE_TOL)


def _require_points(pts: Points, needed: int = 1) -> None:
    if len(pts) < needed:
        raise GeometryError(
            f"need at least {needed} point(s), got {len(pts)}",
            hint="select or pass more spots",
            context={"points": len(pts)},
        )


def _segment_lengths(pts: Points) -> Points:
    lengths: Points = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    return lengths


def length(obj: Points | Curve) -> Meters:
    """Returns the length of a curve, or of points read as an open polyline in order.

    >>> length(np.array([[0.0, 0.0], [3.0, 4.0]]))
    5.0
    """
    if not isinstance(obj, np.ndarray):
        return obj.length()
    return float(_segment_lengths(as_points(obj)).sum())


def center(obj: Points | Curve) -> Point:
    """Returns the bounding-box center.

    >>> center(np.array([[0.0, 0.0], [4.0, 2.0], [1.0, 1.0]]))
    (2.0, 1.0)
    """
    return bounds(obj).center


def centroid(obj: Points | Curve) -> Point:
    """Returns the mean point; curves weight each piece by its length.

    >>> centroid(np.array([[0.0, 0.0], [3.0, 3.0]]))
    (1.5, 1.5)
    """
    pts = _points(obj)
    _require_points(pts)
    if isinstance(obj, np.ndarray) or len(pts) < 2 or obj.length() == 0:
        mean = pts.mean(axis=0)
        return float(mean[0]), float(mean[1])
    weights = _segment_lengths(pts)
    mids = (pts[:-1] + pts[1:]) / 2
    weighted = (mids * weights[:, None]).sum(axis=0) / weights.sum()
    return float(weighted[0]), float(weighted[1])


def bounds(obj: Points | Curve) -> Box2:
    """Returns the bounding box.

    >>> bounds(np.array([[1.0, 2.0], [-1.0, 0.0]])).max
    (1.0, 2.0)
    """
    pts = _points(obj)
    _require_points(pts)
    lo, hi = pts.min(axis=0), pts.max(axis=0)
    return Box2(min=(float(lo[0]), float(lo[1])), max=(float(hi[0]), float(hi[1])))


def _stats(gaps: Points) -> IntervalStats:
    return IntervalStats(
        mean=float(gaps.mean()),
        min=float(gaps.min()),
        max=float(gaps.max()),
        std=float(gaps.std()),
        gaps=tuple(float(g) for g in gaps),
    )


def interval(points: Points, *, ordered: bool = True) -> IntervalStats:
    """Summarizes gaps: consecutive pairs when ordered, each spot's nearest neighbour otherwise.

    >>> interval(np.array([[0.0, 0.0], [2.0, 0.0], [3.0, 0.0]])).gaps
    (2.0, 1.0)
    """
    pts = as_points(points)
    _require_points(pts, 2)
    if ordered:
        return _stats(_segment_lengths(pts))
    gaps, _ = cKDTree(pts).query(pts, k=2)
    return _stats(np.asarray(gaps[:, 1], dtype=float))


def distance(a: Point, b: Point) -> Meters:
    """Returns the Euclidean distance between two points.

    >>> distance((0, 0), (3, 4))
    5.0
    """
    return math.dist(a, b)


def distance_matrix(a: Points, b: Points) -> Points:
    """Returns the (len(a), len(b)) matrix of Euclidean distances.

    >>> distance_matrix(np.array([[0.0, 0.0]]), np.array([[0.0, 2.0]])).tolist()
    [[2.0]]
    """
    matrix: Points = cdist(as_points(a), as_points(b))
    return matrix


@deprecated_call("e8s.choreo.set_measure.neighbors", since="0.4.0", remove="0.6.0")
def neighbors(s: object, entity: str, k: int = 2) -> list[tuple[SpotId, Meters]]:
    """Returns the k spots closest to `entity`; moved to `e8s.choreo.set_measure`."""
    from e8s.choreo.models import Set
    from e8s.choreo.set_measure import neighbors as twin

    if not isinstance(s, Set):
        raise wrong_type_error("e8s.geo.measure.neighbors", "s", s, SET_TYPE)
    return twin(s, entity, k)


@deprecated_call("e8s.choreo.set_measure.nearest", since="0.4.0", remove="0.6.0")
def nearest(s: object, point: Point, k: int = 1) -> list[tuple[SpotId, Meters]]:
    """Returns the k spots closest to a point; moved to `e8s.choreo.set_measure`."""
    from e8s.choreo.models import Set
    from e8s.choreo.set_measure import nearest as twin

    if not isinstance(s, Set):
        raise wrong_type_error("e8s.geo.measure.nearest", "s", s, SET_TYPE)
    return twin(s, point, k)


def reachable_region(start: Point, counts: Count, max_speed: float) -> Polygon:
    """Returns the disk a spot can reach from `start` in `counts` at `max_speed` m per count.

    >>> round(reachable_region((0.0, 0.0), 4.0, 0.5).bounds[2], 6)
    2.0
    """
    radius = counts * max_speed
    if not radius > 0:
        raise GeometryError(
            f"reach radius must be above 0, got {radius}",
            hint="pass counts > 0 and max_speed > 0 (meters per count)",
            context={"counts": counts, "max_speed": max_speed},
        )
    return shapely.Point(start).buffer(radius, quad_segs=DISK_SEGMENTS)


def mirror_gaps(points: Points, axis_x: float) -> Points:
    """Returns, per point, how far its mirror image across x = axis_x lands from the nearest point.

    >>> mirror_gaps(np.array([[-1.0, 0.0], [1.0, 0.0]]), 0.0).tolist()
    [0.0, 0.0]
    """
    pts = as_points(points)
    if len(pts) == 0:
        return np.zeros(0)
    mirrored = pts * np.array([-1.0, 1.0]) + np.array([2 * axis_x, 0.0])
    gaps, _ = cKDTree(pts).query(mirrored, k=1)
    result: Points = np.asarray(gaps, dtype=float)
    return result


def _best_axis_error(pts: Points) -> Meters:
    xs = pts[:, 0]
    candidates = [float(xs.mean()), float((xs.min() + xs.max()) / 2)]
    errors = [float(mirror_gaps(pts, a).max()) for a in candidates]
    if xs.max() - xs.min() > STRAIGHT_TOL:
        found = minimize_scalar(
            lambda a: float(mirror_gaps(pts, a).max()),
            bounds=(float(xs.min()), float(xs.max())),
            method="bounded",
        )
        errors.append(float(found.fun))
    return min(errors)


def _curvatures(pts: Points) -> Points:
    pieces = np.diff(pts, axis=0)
    lengths = np.linalg.norm(pieces, axis=1)
    keep = lengths > STRAIGHT_TOL
    pieces, lengths = pieces[keep], lengths[keep]
    if len(pieces) < 2:
        return np.zeros(1)
    a, b = pieces[:-1], pieces[1:]
    cross = a[:, 0] * b[:, 1] - a[:, 1] * b[:, 0]
    turning = np.abs(np.arctan2(cross, (a * b).sum(axis=1)))
    result: Points = turning / ((lengths[:-1] + lengths[1:]) / 2)
    return result


def form_metrics(points: Points) -> FormMetrics:
    """Summarizes a form's points read as an ordered polyline.

    Curvature at each interior point is the turning angle divided by the mean length of the
    two pieces meeting there. Symmetry is measured about the best-fitting vertical axis.

    >>> form_metrics(np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]])).curvature_range
    (0.0, 0.0)
    """
    pts = as_points(points)
    curvature = _curvatures(pts)
    return FormMetrics(
        length=length(pts),
        intervals=interval(pts),
        curvature_range=(float(curvature.min()), float(curvature.max())),
        symmetry_error=_best_axis_error(pts),
    )


@deprecated_call("e8s.choreo.set_measure.symmetry_error", since="0.4.0", remove="0.6.0")
def symmetry_error(s: object, sel: SetSelector = ALL, axis_x: float = 0.0) -> Meters:
    """Returns the largest mirror mismatch of a set; moved to `e8s.choreo.set_measure`."""
    from e8s.choreo.models import Set
    from e8s.choreo.set_measure import symmetry_error as twin

    if not isinstance(s, Set):
        raise wrong_type_error("e8s.geo.measure.symmetry_error", "s", s, SET_TYPE)
    return twin(s, sel, axis_x)
