#!/usr/bin/env python3

"""
test_shapes.py --- tests curve constructors, joins, offsets and prefab outlines
"""

import math

import numpy as np
import pytest

from showpy.core.errors import GeometryError
from showpy.core.util import approx_equal
from showpy.geo.shapes import (
    arc,
    arc_from_chord,
    arc_through,
    bezier,
    circle,
    curve_through,
    ellipse,
    join,
    line,
    offset,
    polyline,
    rect,
    regular_polygon,
    spiral,
    star,
    wedge,
)


def test_line_and_polyline() -> None:
    assert line((0, 0), (3, 4)).length() == 5.0
    assert approx_equal(polyline((0, 0), (1, 0), (1, 1), closed=True).length(), 2 + math.sqrt(2))


def test_arc_rejects_zero_radius() -> None:
    with pytest.raises(GeometryError):
        arc((0, 0), 0.0, 0.0, 90.0)


def test_circle_rejects_negative_radius() -> None:
    with pytest.raises(GeometryError):
        circle((0, 0), -1.0)


def test_arc_through_passes_through_middle_point() -> None:
    curve = arc_through((-1.0, 0.0), (0.0, 1.0), (1.0, 0.0))
    assert approx_equal(curve.radius, 1.0)
    assert approx_equal(curve.point_at(0.0), (-1.0, 0.0))
    assert approx_equal(curve.point_at(curve.length()), (1.0, 0.0))
    assert approx_equal(curve.point_at(curve.length() / 2), (0.0, 1.0))


def test_arc_through_rejects_collinear() -> None:
    with pytest.raises(GeometryError):
        arc_through((0, 0), (1, 1), (2, 2))


@pytest.mark.parametrize("bulge", [2.0, -2.0, 0.5])
def test_arc_from_chord_bows_by_sagitta(bulge: float) -> None:
    curve = arc_from_chord((-4.0, 0.0), (4.0, 0.0), bulge)
    mid = curve.point_at(curve.length() / 2)
    assert approx_equal(mid, (0.0, bulge))


def test_arc_from_chord_rejects_zero_bulge() -> None:
    with pytest.raises(GeometryError):
        arc_from_chord((0, 0), (1, 0), 0.0)


def test_ellipse_curve_through_bezier_spiral_construct() -> None:
    assert ellipse((0, 0), 2, 1).closed
    assert curve_through((0, 0), (1, 1), (2, 0)).length() > 2.0
    assert approx_equal(bezier((0, 0), (1, 0)).length(), 1.0)
    assert spiral((0, 0), 1, 2, 1).length() > 2 * math.pi


def test_rect_is_closed_with_perimeter() -> None:
    outline = rect((0, 0), 4, 2)
    assert outline.closed
    assert approx_equal(outline.length(), 12.0)


def test_rect_rotation_keeps_center() -> None:
    pts = np.array(rect((1, 1), 4, 2, rotation_deg=30).points)
    assert approx_equal(pts.mean(axis=0), (1.0, 1.0))


def test_regular_polygon_vertices_on_radius() -> None:
    hexagon = regular_polygon((0, 0), 2.0, 6)
    assert len(hexagon.points) == 6
    assert approx_equal(np.linalg.norm(np.array(hexagon.points), axis=1), [2.0] * 6)


def test_star_alternates_radii() -> None:
    pts = np.array(star((0, 0), 2.0, 1.0, 5).points)
    radii = np.linalg.norm(pts, axis=1)
    assert approx_equal(radii[::2], [2.0] * 5)
    assert approx_equal(radii[1::2], [1.0] * 5)


def test_wedge_apex_leads_toward_facing() -> None:
    v = wedge((0.0, 10.0), 4.0, 90.0)
    assert approx_equal(v.points[1], (0.0, 10.0))
    assert v.points[0][1] > 10.0
    assert v.points[2][1] > 10.0
    assert approx_equal(v.length(), 8.0)


def test_join_requires_touching_ends_unless_bridged() -> None:
    a, b = line((0, 0), (1, 0)), line((2, 0), (3, 0))
    with pytest.raises(GeometryError):
        join(a, b)
    assert approx_equal(join(a, b, bridge=True).length(), 3.0)
    assert approx_equal(join(a, line((1, 0), (1, 1))).length(), 2.0)


def test_offset_segment_left_and_right() -> None:
    seg = line((0, 0), (4, 0))
    assert approx_equal(offset(seg, 1.0).point_at(0.0), (0.0, 1.0))
    assert approx_equal(offset(seg, 1.0, side="right").point_at(0.0), (0.0, -1.0))


def test_offset_ccw_arc_left_moves_inward() -> None:
    curve = arc((0, 0), 5.0, 0.0, 90.0)
    assert approx_equal(offset(curve, 1.0).radius, 4.0)  # type: ignore[attr-defined]


def test_offset_through_center_raises() -> None:
    with pytest.raises(GeometryError):
        offset(circle((0, 0), 1.0), 1.0, side="left")
