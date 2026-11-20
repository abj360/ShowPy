#!/usr/bin/env python3

"""
test_shapes.py --- tests curve constructors, joins, offsets and prefab outlines

Contains:
    test_regression_offset_honours_clockwise_circles: grows a clockwise circle when offsetting to
        its left, the outward side
    test_regression_offset_rejects_unknown_side: raises UnknownRefError for side="up" instead of
        treating it as "right"
    test_regression_outward_offset_of_closed_curve_stays_closed: keeps the ring closed when
        offsetting a closed polyline outward
    test_regression_arc_through_is_accurate_far_from_the_origin: recovers a 1 km radius within 1 um
        when the arc sits 10,000 km from the origin
    test_regression_text_outline_loops_are_closed_without_repeated_start: emits glyph loops as
        closed polylines that do not repeat their start point
    test_regression_simplex_text_outline_keeps_its_geometry: keeps every simplex glyph's points,
        order and open flags; only loops close
    test_text_outline_spells_lowercase_and_punctuation: spells lowercase and punctuation in
        hershey-roman as polylines
    test_text_outline_takes_a_fallback_chain: falls back to the next font for a missing glyph
    test_regression_regular_polygon_rejects_fractional_sides: raises a ShowPyError for n=3.5 instead
        of leaking TypeError
    test_regression_rect_rejects_nan_width: rejects a NaN width instead of building a polygon of NaN
        points
"""

import math

import numpy as np
import pytest

from showpy.core.errors import GeometryError, GlyphError, ShowPyError, UnknownRefError
from showpy.core.util import approx_equal
from showpy.geo.curves import CircleCurve, Polyline
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
    text_outline,
    wedge,
)
from showpy.geo.text import SIMPLEX

OFFSET_M = 1.0
CIRCLE_RADIUS_M = 2.0
FAR_CENTER_M = 1e7
FAR_RADIUS_M = 1000.0
FAR_ARC_ANGLES_DEG = (0.0, 45.0, 90.0)
RADIUS_TOL_M = 1e-6
LOOP_GLYPHS = ["O", "0", "8", "D", "Q"]
GLYPH_HEIGHT_M = 2.0
FRACTIONAL_SIDES = 3.5
SQUARE_SIDE_M = 4.0
AT = (1.0, 3.0)
SIMPLEX_HEIGHT_M = 2.0
SIMPLEX_GRID_HEIGHT = 6.0
GEOMETRY_TOL = 1e-12


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


@pytest.mark.defect("UP-9.1-13")
@pytest.mark.xfail(strict=True, reason="UP-9.1-13")
def test_regression_offset_honours_clockwise_circles() -> None:
    """Grows a clockwise circle when offsetting to its left, the outward side (audit 9.1)."""
    clockwise = CircleCurve(center=(0.0, 0.0), radius=CIRCLE_RADIUS_M, ccw=False)
    result = offset(clockwise, OFFSET_M, "left")
    assert isinstance(result, CircleCurve)
    assert approx_equal(result.radius, CIRCLE_RADIUS_M + OFFSET_M, abs_tol=1e-9)


@pytest.mark.defect("UP-9.1-14")
@pytest.mark.xfail(strict=True, reason="UP-9.1-14")
def test_regression_offset_rejects_unknown_side() -> None:
    """Raises UnknownRefError for side="up" instead of treating it as "right" (audit 9.1)."""
    with pytest.raises(UnknownRefError):
        offset(line((0.0, 0.0), (1.0, 0.0)), OFFSET_M, "up")


@pytest.mark.defect("UP-9.1-15")
@pytest.mark.xfail(strict=True, reason="UP-9.1-15")
def test_regression_outward_offset_of_closed_curve_stays_closed() -> None:
    """Keeps the ring closed when offsetting a closed polyline outward (audit 9.1)."""
    outward = offset(rect((0.0, 0.0), SQUARE_SIDE_M, SQUARE_SIDE_M), OFFSET_M, "right")
    assert outward.closed


@pytest.mark.defect("UP-9.1-16")
@pytest.mark.xfail(strict=True, reason="UP-9.1-16")
def test_regression_arc_through_is_accurate_far_from_the_origin() -> None:
    """Recovers a 1 km radius within 1 um when the arc sits 10,000 km from the origin."""
    points = [
        (
            FAR_CENTER_M + FAR_RADIUS_M * math.cos(math.radians(d)),
            FAR_CENTER_M + FAR_RADIUS_M * math.sin(math.radians(d)),
        )
        for d in FAR_ARC_ANGLES_DEG
    ]
    assert abs(arc_through(*points).radius - FAR_RADIUS_M) < RADIUS_TOL_M


@pytest.mark.parametrize("glyph", LOOP_GLYPHS)
def test_regression_text_outline_loops_are_closed_without_repeated_start(glyph: str) -> None:
    """Emits glyph loops as closed polylines that do not repeat their start point (audit 9.1)."""
    strokes = text_outline(glyph, "simplex", GLYPH_HEIGHT_M, (0.0, 0.0))
    assert any(stroke.closed for stroke in strokes)
    assert all(isinstance(stroke, Polyline) for stroke in strokes)
    assert all(
        stroke.closed or stroke.points[0] != stroke.points[-1]
        for stroke in strokes
        if isinstance(stroke, Polyline)
    )


def _expected_simplex_strokes(char: str) -> list[tuple[bool, list[tuple[float, float]]]]:
    scale = SIMPLEX_HEIGHT_M / SIMPLEX_GRID_HEIGHT
    expected = []
    for stroke in SIMPLEX[char]:
        points = [(AT[0] + x * scale, AT[1] + y * scale) for x, y in stroke]
        is_loop = len(points) > 1 and points[0] == points[-1]
        expected.append((is_loop, points[:-1] if is_loop else points))
    return expected


@pytest.mark.parametrize("char", sorted({c for k in SIMPLEX for c in (k, k.lower())}))
def test_regression_simplex_text_outline_keeps_its_geometry(char: str) -> None:
    """Keeps every simplex glyph's points, order and open flags; only loops close."""
    strokes = text_outline(char, "simplex", SIMPLEX_HEIGHT_M, AT)
    expected = _expected_simplex_strokes(char.upper() if char.upper() in SIMPLEX else char)
    assert len(strokes) == len(expected)
    for stroke, (is_loop, points) in zip(strokes, expected, strict=True):
        assert isinstance(stroke, Polyline)
        assert stroke.closed is is_loop
        assert len(stroke.points) == len(points)
        for got, want in zip(stroke.points, points, strict=True):
            assert math.dist(got, want) < GEOMETRY_TOL


def test_text_outline_spells_lowercase_and_punctuation() -> None:
    strokes = text_outline("hi!", "hershey-roman", 1.0, (0, 0))
    assert strokes
    assert all(isinstance(stroke, Polyline) for stroke in strokes)


def test_text_outline_takes_a_fallback_chain() -> None:
    assert text_outline("A\u20ac", ["simplex", "relief-singleline"], 1.0, (0, 0))
    with pytest.raises(GlyphError):
        text_outline("A\u20ac", "simplex", 1.0, (0, 0))


@pytest.mark.defect("UP-9.1-60")
@pytest.mark.xfail(strict=True, reason="UP-9.1-60")
def test_regression_regular_polygon_rejects_fractional_sides() -> None:
    """Raises a ShowPyError for n=3.5 instead of leaking TypeError (audit 9.1)."""
    with pytest.raises(ShowPyError):
        regular_polygon((0.0, 0.0), 1.0, FRACTIONAL_SIDES)  # type: ignore[arg-type]


@pytest.mark.defect("UP-9.1-66")
@pytest.mark.xfail(strict=True, reason="UP-9.1-66")
def test_regression_rect_rejects_nan_width() -> None:
    """Rejects a NaN width instead of building a polygon of NaN points (audit 9.1)."""
    with pytest.raises(GeometryError):
        rect((0.0, 0.0), float("nan"), 1.0)
