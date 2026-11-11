#!/usr/bin/env python3

"""
affine.py --- builds 3x3 affine matrices and maps points, facings and curves through them

Curves stay exact where the family is closed under the map: point-defined curves
(segments, polylines, splines, Béziers, SVG paths) under any affine map, and arcs, circles
and spirals under similarities (rotation, translation, uniform scale, mirror). Anything
else is mapped as a dense polyline.

Contains:
    translation_matrix(): builds a translation
    rotation_matrix(): builds a counter-clockwise rotation about a point
    scale_matrix(): builds a scale about a point
    mirror_matrix(): builds a reflection across the line through two points
    skew_matrix(): builds a shear about a point
    is_similarity(): reports whether a map preserves shape (angles and length ratios)
    transform_points(): maps an (N, 2) array
    transform_facing(): maps a facing so it points the same way relative to the mapped picture
    transform_curve(): maps a curve, keeping its family when exact
"""

import math

import numpy as np
import numpy.typing as npt

from stage.core.types import Degrees, Point, Points
from stage.core.units import facing_vector, vector_facing
from stage.geo.curves import (
    AnyCurve,
    ArcCurve,
    BezierCurve,
    CircleCurve,
    CompositeCurve,
    Polyline,
    Segment,
    SpiralCurve,
    SplineCurve,
    SvgCurve,
    TrimmedCurve,
)

type Matrix = npt.NDArray[np.float64]

FALLBACK_TOL = 0.001
SIMILARITY_TOL = 1e-9


def translation_matrix(dx: float, dy: float) -> Matrix:
    """Builds a translation.

    >>> translation_matrix(1, 2)[:2, 2].tolist()
    [1.0, 2.0]
    """
    return np.array([[1.0, 0.0, dx], [0.0, 1.0, dy], [0.0, 0.0, 1.0]])


def _about(linear: Matrix, about: Point) -> Matrix:
    m = np.eye(3)
    m[:2, :2] = linear
    return translation_matrix(*about) @ m @ translation_matrix(-about[0], -about[1])


def rotation_matrix(deg: Degrees, about: Point) -> Matrix:
    """Builds a counter-clockwise rotation about a point.

    >>> transform_points(np.array([[1.0, 0.0]]), rotation_matrix(90, (0, 0))).round(6).tolist()
    [[0.0, 1.0]]
    """
    rad = math.radians(deg)
    c, s = math.cos(rad), math.sin(rad)
    return _about(np.array([[c, -s], [s, c]]), about)


def scale_matrix(sx: float, sy: float, about: Point) -> Matrix:
    """Builds a scale about a point.

    >>> scale_matrix(2, 3, (0, 0))[:2, :2].tolist()
    [[2.0, 0.0], [0.0, 3.0]]
    """
    return _about(np.array([[sx, 0.0], [0.0, sy]]), about)


def mirror_matrix(a: Point, b: Point) -> Matrix:
    """Builds a reflection across the line through `a` and `b`.

    >>> transform_points(np.array([[1.0, 5.0]]), mirror_matrix((0, 0), (0, 1))).round(6).tolist()
    [[-1.0, 5.0]]
    """
    angle = math.atan2(b[1] - a[1], b[0] - a[0])
    c, s = math.cos(2 * angle), math.sin(2 * angle)
    return _about(np.array([[c, s], [s, -c]]), a)


def skew_matrix(deg_x: Degrees, deg_y: Degrees, about: Point) -> Matrix:
    """Builds a shear: x shifts by tan(deg_x)·y and y by tan(deg_y)·x, about a point.

    >>> skew_matrix(45, 0, (0, 0))[0, 1].round(6)
    np.float64(1.0)
    """
    shear = np.array([[1.0, math.tan(math.radians(deg_x))], [math.tan(math.radians(deg_y)), 1.0]])
    return _about(shear, about)


def is_similarity(matrix: Matrix) -> bool:
    """Reports whether the map is a rotation, translation, uniform scale or mirror.

    >>> is_similarity(scale_matrix(2, 2, (0, 0))), is_similarity(scale_matrix(2, 1, (0, 0)))
    (True, False)
    """
    linear = matrix[:2, :2]
    gram = linear.T @ linear
    return bool(abs(gram[0, 1]) < SIMILARITY_TOL and abs(gram[0, 0] - gram[1, 1]) < SIMILARITY_TOL)


def transform_points(points: Points, matrix: Matrix) -> Points:
    """Maps an (N, 2) array of points.

    >>> transform_points(np.array([[0.0, 0.0]]), translation_matrix(1, 1)).tolist()
    [[1.0, 1.0]]
    """
    mapped: Points = np.asarray(points, dtype=float) @ matrix[:2, :2].T + matrix[:2, 2]
    return mapped


def transform_facing(theta: Degrees, matrix: Matrix) -> Degrees:
    """Maps a facing through the linear part of the map.

    >>> round(transform_facing(0.0, rotation_matrix(90, (0, 0))), 6)
    90.0
    """
    v = matrix[:2, :2] @ np.asarray(facing_vector(theta))
    return vector_facing((float(v[0]), float(v[1])))


def _point(p: Point, matrix: Matrix) -> Point:
    q = transform_points(np.asarray([p]), matrix)[0]
    return float(q[0]), float(q[1])


def _ring(points: tuple[Point, ...], matrix: Matrix) -> tuple[Point, ...]:
    return tuple(_point(p, matrix) for p in points)


def _similarity_parts(matrix: Matrix) -> tuple[float, bool]:
    linear = matrix[:2, :2]
    return math.sqrt(abs(np.linalg.det(linear))), bool(np.linalg.det(linear) < 0)


def _angle_of(center: Point, p: Point) -> Degrees:
    return math.degrees(math.atan2(p[1] - center[1], p[0] - center[0]))


def _map_round(curve: ArcCurve | CircleCurve | SpiralCurve, matrix: Matrix) -> AnyCurve:
    scale, flips = _similarity_parts(matrix)
    center = _point(curve.center, matrix)
    start = curve.point_at(0.0)
    start_deg = _angle_of(center, _point((float(start[0]), float(start[1])), matrix))
    if isinstance(curve, ArcCurve):
        sweep = -curve.sweep_deg if flips else curve.sweep_deg
        return ArcCurve(
            center=center, radius=curve.radius * scale, start_deg=start_deg, sweep_deg=sweep
        )
    if isinstance(curve, CircleCurve):
        return CircleCurve(
            center=center, radius=curve.radius * scale, start_deg=start_deg, ccw=curve.ccw != flips
        )
    if flips:
        return _fallback(curve, matrix)
    return curve.model_copy(
        update={
            "center": center,
            "r0": curve.r0 * scale,
            "r1": curve.r1 * scale,
            "start_deg": start_deg,
        }
    )


def _fallback(curve: AnyCurve, matrix: Matrix) -> Polyline:
    pts = curve.to_polyline(FALLBACK_TOL)
    if curve.closed:
        pts = pts[:-1]
    mapped = transform_points(pts, matrix)
    return Polyline(points=tuple((float(x), float(y)) for x, y in mapped), closed=curve.closed)


def transform_curve(curve: AnyCurve, matrix: Matrix) -> AnyCurve:
    """Maps a curve; the result keeps its family whenever the map allows it exactly.

    >>> transform_curve(Segment(a=(0, 0), b=(1, 0)), translation_matrix(0, 2)).a
    (0.0, 2.0)
    """
    similar = is_similarity(matrix)
    if isinstance(curve, Segment):
        return Segment(a=_point(curve.a, matrix), b=_point(curve.b, matrix))
    if isinstance(curve, Polyline | SplineCurve):
        return curve.model_copy(update={"points": _ring(curve.points, matrix)})
    if isinstance(curve, BezierCurve):
        return BezierCurve(control=_ring(curve.control, matrix))
    if isinstance(curve, SvgCurve):
        composed = matrix @ np.asarray(curve.transform)
        rows = tuple((float(r[0]), float(r[1]), float(r[2])) for r in composed)
        return curve.model_copy(update={"transform": rows})
    if isinstance(curve, CompositeCurve):
        return CompositeCurve(parts=tuple(transform_curve(p, matrix) for p in curve.parts))
    if isinstance(curve, TrimmedCurve) and similar:
        scale, _ = _similarity_parts(matrix)
        base = transform_curve(curve.base, matrix)
        return TrimmedCurve(base=base, s0=curve.s0 * scale, s1=curve.s1 * scale)
    if isinstance(curve, ArcCurve | CircleCurve | SpiralCurve) and similar:
        return _map_round(curve, matrix)
    return _fallback(curve, matrix)
