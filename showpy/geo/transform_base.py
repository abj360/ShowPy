"""
transform_base.py --- maps points and curves through an affine matrix or a point function

Shared by `showpy.geo.transforms` and `showpy.geo.snapping`. A `Set` is not handled here;
`showpy.choreo.set_transforms` moves set placements and reuses these maps.

Contains:
    About: names the anchor a rotation, scale or skew turns about
    PointMap: names a function that maps an (N, 2) array to a new (N, 2) array
    warn_set_moved(): warns that handling a Set through geo is deprecated
    is_set_input(): tells whether an argument is neither points nor a curve
    anchor_source(): returns what anchors are measured on: the points or the curve
    about_point(): resolves "center", "centroid" or a point to coordinates
    apply_matrix(): maps points or a curve through an affine matrix
    apply_pointwise(): maps points or a curve's points through a function
    map_curve(): maps a curve's defining points, or a dense polyline when it has none
"""

from collections.abc import Callable
from typing import Literal, cast

import numpy as np

from showpy.core.deprecation import warn_deprecated
from showpy.core.types import Point, Points
from showpy.core.util import as_points
from showpy.geo.affine import Matrix, transform_curve, transform_points
from showpy.geo.checks import pick
from showpy.geo.curves import (
    AnyCurve,
    BezierCurve,
    CompositeCurve,
    CurveModel,
    Polyline,
    Segment,
    SplineCurve,
)
from showpy.geo.measure import center, centroid

type About = Literal["center", "centroid"] | Point
type PointMap = Callable[[Points], Points]

FALLBACK_TOL = 0.001
SET_MOVED_SINCE = "0.4.0"
SET_MOVED_REMOVE = "0.6.0"


def warn_set_moved(geo_module: str, name: str, choreo_module: str) -> None:
    """Warns, at the caller of the geo function, that handling a `Set` there is deprecated.

    Call it from the geo function itself; the `Set` branch of a function that still takes
    points and curves uses it before forwarding to the choreo twin.

    Args:
        geo_module: Dotted name of the geo module that holds the function.
        name: Name of the function, the same in both modules.
        choreo_module: Dotted name of the choreo module that holds the twin.
    """
    warn_deprecated(
        f"{geo_module}.{name}(Set)",
        f"{choreo_module}.{name}",
        since=SET_MOVED_SINCE,
        remove=SET_MOVED_REMOVE,
        extra_frames=1,
    )


def is_set_input(obj: object) -> bool:
    """Tells whether an argument is neither points nor a curve, so a deprecated `Set` call.

    >>> is_set_input(np.array([[1.0, 2.0]]))
    False
    """
    return not isinstance(obj, np.ndarray | CurveModel)


def anchor_source(obj: Points | CurveModel) -> Points | CurveModel:
    """Returns what anchors are measured on: the points or the curve.

    >>> anchor_source(np.array([[1.0, 2.0]])).tolist()
    [[1.0, 2.0]]
    """
    return as_points(obj) if isinstance(obj, np.ndarray) else obj


def about_point(obj: Points | CurveModel, about: About) -> Point:
    """Resolves "center" (box center), "centroid" (mean) or a point to coordinates.

    >>> about_point(np.array([[0.0, 0.0], [2.0, 4.0]]), "center")
    (1.0, 2.0)
    """
    if not isinstance(about, str):
        return float(about[0]), float(about[1])
    measure = pick("anchor", about, {"center": center, "centroid": centroid})
    return measure(anchor_source(obj))


def apply_matrix[T: (Points, CurveModel)](obj: T, matrix: Matrix) -> T:
    """Maps points or a curve through an affine matrix.

    >>> from showpy.geo.affine import translation_matrix
    >>> apply_matrix(np.array([[0.0, 0.0]]), translation_matrix(1, 0)).tolist()
    [[1.0, 0.0]]
    """
    if isinstance(obj, np.ndarray):
        return transform_points(as_points(obj), matrix)
    return transform_curve(cast(AnyCurve, obj), matrix)


def apply_pointwise[T: (Points, CurveModel)](obj: T, fn: PointMap) -> T:
    """Maps points, or a curve's points, through `fn`.

    >>> apply_pointwise(np.array([[1.0, 1.0]]), lambda p: p * 2).tolist()
    [[2.0, 2.0]]
    """
    if isinstance(obj, np.ndarray):
        return fn(as_points(obj))
    return map_curve(obj, fn)


def _ring(pts: Points) -> tuple[Point, ...]:
    return tuple((float(x), float(y)) for x, y in pts)


def map_curve(curve: CurveModel, fn: PointMap) -> AnyCurve:
    """Maps a curve's defining points; curves without them become a dense Polyline.

    Segments, polylines, splines and Béziers keep their family; composites map each part.

    >>> map_curve(Segment(a=(0, 0), b=(1, 0)), lambda p: p + 1).b
    (2.0, 1.0)
    """
    if isinstance(curve, Segment):
        a, b = fn(np.array([curve.a, curve.b], dtype=float))
        return Segment(a=(float(a[0]), float(a[1])), b=(float(b[0]), float(b[1])))
    if isinstance(curve, Polyline | SplineCurve):
        return curve.replace(points=_ring(fn(np.asarray(curve.points, float))))
    if isinstance(curve, BezierCurve):
        return BezierCurve(control=_ring(fn(np.asarray(curve.control, dtype=float))))
    if isinstance(curve, CompositeCurve):
        return CompositeCurve(parts=tuple(map_curve(part, fn) for part in curve.parts))
    pts = curve.to_polyline(FALLBACK_TOL)
    if curve.closed:
        pts = pts[:-1]
    return Polyline(points=_ring(fn(pts)), closed=curve.closed)
