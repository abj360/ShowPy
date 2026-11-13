#!/usr/bin/env python3

"""
transform_base.py --- dispatches a map over points, curves and set selections, keeping the kind

Shared by `showpy.geo.transforms` and `showpy.geo.snapping`. Sets move only selected on-field
placements and respect pins; affine maps carry facings with them.

Contains:
    About: names the anchor a rotation, scale or skew turns about
    PointMap: names a function that maps an (N, 2) array to a new (N, 2) array
    pick(): looks up a named option, raising UnknownRefError with suggestions
    anchor_source(): returns what anchors are measured on: points, a curve or set positions
    about_point(): resolves "center", "centroid" or a point to coordinates
    apply_matrix(): maps an object through an affine matrix; facings follow
    apply_pointwise(): maps an object's points through a function; facings stay
    map_curve(): maps a curve's defining points, or a dense polyline when it has none
"""

from collections.abc import Callable, Mapping, Sequence
from typing import Literal, cast

import numpy as np

from showpy.choreo.models import Set
from showpy.core.errors import UnknownRefError
from showpy.core.ids import SpotId
from showpy.core.types import Degrees, Point, Points
from showpy.core.util import as_points, close_matches
from showpy.geo.affine import Matrix, transform_curve, transform_facing, transform_points
from showpy.geo.curves import (
    AnyCurve,
    BezierCurve,
    CompositeCurve,
    CurveModel,
    Polyline,
    Segment,
    SplineCurve,
)
from showpy.geo.measure import SetSelector, center, centroid, on_field_ids

type About = Literal["center", "centroid"] | Point
type PointMap = Callable[[Points], Points]

FALLBACK_TOL = 0.001


def pick[V](kind: str, name: str, options: Mapping[str, V]) -> V:
    """Looks up a named option; an unknown name raises UnknownRefError with close matches.

    >>> pick("axis", "x", {"x": 0, "y": 1})
    0
    """
    if name not in options:
        raise UnknownRefError(
            f"unknown {kind} {name!r}",
            hint=f"use one of {sorted(options)}; did you mean {close_matches(name, options)}?",
            context={kind: name},
        )
    return options[name]


def anchor_source(obj: Points | CurveModel | Set, sel: SetSelector) -> Points | CurveModel:
    """Returns what anchors are measured on: the points, the curve, or selected on-field spots.

    >>> anchor_source(np.array([[1.0, 2.0]]), "all").tolist()
    [[1.0, 2.0]]
    """
    if isinstance(obj, Set):
        return obj.positions(on_field_ids(obj, sel))
    return as_points(obj) if isinstance(obj, np.ndarray) else obj


def about_point(obj: Points | CurveModel | Set, about: About, sel: SetSelector) -> Point:
    """Resolves "center" (box center), "centroid" (mean) or a point to coordinates.

    >>> about_point(np.array([[0.0, 0.0], [2.0, 4.0]]), "center", "all")
    (1.0, 2.0)
    """
    if not isinstance(about, str):
        return float(about[0]), float(about[1])
    measure = pick("anchor", about, {"center": center, "centroid": centroid})
    return measure(anchor_source(obj, sel))


def apply_matrix[T: (Points, CurveModel, Set)](
    obj: T, matrix: Matrix, sel: SetSelector, force: bool
) -> T:
    """Maps an object through an affine matrix; set facings follow its linear part.

    >>> from showpy.geo.affine import translation_matrix
    >>> apply_matrix(np.array([[0.0, 0.0]]), translation_matrix(1, 0), "all", False).tolist()
    [[1.0, 0.0]]
    """
    if isinstance(obj, np.ndarray):
        return transform_points(as_points(obj), matrix)
    if isinstance(obj, Set):
        ids = on_field_ids(obj, sel)
        pts = transform_points(obj.positions(ids), matrix)
        facings = [transform_facing(obj.get(i).facing, matrix) for i in ids]
        return _update_set(obj, ids, pts, facings, force)
    return transform_curve(cast(AnyCurve, obj), matrix)


def apply_pointwise[T: (Points, CurveModel, Set)](
    obj: T, fn: PointMap, sel: SetSelector, force: bool
) -> T:
    """Maps an object's points through `fn`; set facings stay as they are.

    >>> apply_pointwise(np.array([[1.0, 1.0]]), lambda p: p * 2, "all", False).tolist()
    [[2.0, 2.0]]
    """
    if isinstance(obj, np.ndarray):
        return fn(as_points(obj))
    if isinstance(obj, Set):
        ids = on_field_ids(obj, sel)
        return _update_set(obj, ids, fn(obj.positions(ids)), None, force)
    return map_curve(obj, fn)


def _update_set(
    s: Set, ids: Sequence[SpotId], pts: Points, facings: Sequence[Degrees] | None, force: bool
) -> Set:
    updates: dict[SpotId, dict[str, float]] = {}
    for k, entity in enumerate(ids):
        updates[entity] = {"x": float(pts[k, 0]), "y": float(pts[k, 1])}
        if facings is not None:
            updates[entity]["facing"] = facings[k]
    return s.with_placements(updates, force=force)


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
        return curve.model_copy(update={"points": _ring(fn(np.asarray(curve.points, float)))})
    if isinstance(curve, BezierCurve):
        return BezierCurve(control=_ring(fn(np.asarray(curve.control, dtype=float))))
    if isinstance(curve, CompositeCurve):
        return CompositeCurve(parts=tuple(map_curve(part, fn) for part in curve.parts))
    pts = curve.to_polyline(FALLBACK_TOL)
    if curve.closed:
        pts = pts[:-1]
    return Polyline(points=_ring(fn(pts)), closed=curve.closed)
