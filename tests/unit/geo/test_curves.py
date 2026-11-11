#!/usr/bin/env python3

"""
test_curves.py --- tests the Curve implementations: length, sampling, projection, trimming
"""

import math

import numpy as np
import pytest
import shapely
from hypothesis import given, settings
from hypothesis import strategies as st

from stage.core.util import approx_equal
from stage.geo.curves import (
    ArcCurve,
    BezierCurve,
    CircleCurve,
    CompositeCurve,
    EllipseCurve,
    Polyline,
    Segment,
    SpiralCurve,
    SplineCurve,
    curve_from_construction,
)

SEG = Segment(a=(0.0, 0.0), b=(4.0, 0.0))
QUARTER = ArcCurve(center=(0.0, 0.0), radius=2.0, start_deg=0.0, sweep_deg=90.0)


def test_segment_length_and_scalar_point() -> None:
    assert SEG.length() == 4.0
    assert SEG.point_at(1.0).shape == (2,)
    assert approx_equal(SEG.point_at(1.0), (1.0, 0.0))


def test_segment_array_points_and_clamp() -> None:
    pts = SEG.point_at(np.array([-1.0, 2.0, 9.0]))
    assert approx_equal(pts, [[0.0, 0.0], [2.0, 0.0], [4.0, 0.0]])


def test_segment_tangent_and_left_normal() -> None:
    assert approx_equal(SEG.tangent_at(1.0), (1.0, 0.0))
    assert approx_equal(SEG.normal_at(1.0), (0.0, 1.0))


def test_point_at_u_uses_fraction() -> None:
    assert approx_equal(SEG.point_at_u(0.25), (1.0, 0.0))


def test_closed_polyline_includes_closing_edge_and_wraps() -> None:
    square = Polyline(points=((0, 0), (1, 0), (1, 1), (0, 1)), closed=True)
    assert approx_equal(square.length(), 4.0)
    assert approx_equal(square.point_at(4.5), (0.5, 0.0))


def test_arc_length_endpoints_and_direction() -> None:
    assert approx_equal(QUARTER.length(), math.pi)
    assert approx_equal(QUARTER.point_at(0.0), (2.0, 0.0))
    assert approx_equal(QUARTER.point_at(QUARTER.length()), (0.0, 2.0))
    clockwise = QUARTER.model_copy(update={"sweep_deg": -90.0})
    assert approx_equal(clockwise.point_at(clockwise.length()), (0.0, -2.0))


def test_arc_tangent_follows_sweep() -> None:
    assert approx_equal(QUARTER.tangent_at(0.0), (0.0, 1.0))


def test_circle_is_closed_with_full_length() -> None:
    ring = CircleCurve(center=(1.0, 1.0), radius=1.0)
    assert ring.closed
    assert approx_equal(ring.length(), 2 * math.pi)


def test_ellipse_length_matches_ramanujan() -> None:
    oval = EllipseCurve(center=(0.0, 0.0), rx=3.0, ry=1.0)
    a, b = 3.0, 1.0
    h = ((a - b) / (a + b)) ** 2
    expected = math.pi * (a + b) * (1 + 3 * h / (10 + math.sqrt(4 - 3 * h)))
    assert approx_equal(oval.length(), expected, tol=1e-4)


def test_spline_passes_through_control_points() -> None:
    pts = ((0.0, 0.0), (2.0, 1.0), (4.0, 0.0), (6.0, 2.0))
    spline = SplineCurve(points=pts)
    sampled = spline.to_polyline(0.001)
    for p in pts:
        assert np.min(np.linalg.norm(sampled - np.array(p), axis=1)) < 1e-9


def test_bezier_quadratic_midpoint() -> None:
    curve = BezierCurve(control=((0.0, 0.0), (1.0, 2.0), (2.0, 0.0)))
    assert approx_equal(curve.point_at_u(0.0), (0.0, 0.0))
    assert approx_equal(curve.point_at_u(1.0), (2.0, 0.0))
    mid = curve.point_at(curve.length() / 2)
    assert approx_equal(mid, (1.0, 1.0), tol=1e-4)


def test_spiral_radius_grows_from_r0_to_r1() -> None:
    spiral = SpiralCurve(center=(0.0, 0.0), r0=1.0, r1=3.0, turns=2.0)
    assert approx_equal(np.linalg.norm(spiral.point_at(0.0)), 1.0)
    assert approx_equal(np.linalg.norm(spiral.point_at(spiral.length())), 3.0)


def test_project_returns_arc_length_of_closest_point() -> None:
    assert approx_equal(SEG.project((1.5, 3.0)), 1.5)
    assert approx_equal(QUARTER.project((5.0, 5.0)), math.pi / 2, tol=1e-6)


def test_substring_and_reversed() -> None:
    piece = SEG.substring(1.0, 3.0)
    assert approx_equal(piece.length(), 2.0)
    assert approx_equal(piece.point_at(0.0), (1.0, 0.0))
    backwards = SEG.substring(3.0, 1.0)
    assert approx_equal(backwards.point_at(0.0), (3.0, 0.0))
    assert approx_equal(QUARTER.reversed().point_at(0.0), (0.0, 2.0))


@settings(max_examples=30)
@given(
    st.floats(min_value=0.5, max_value=30.0),
    st.floats(min_value=10.0, max_value=350.0),
    st.sampled_from([0.1, 0.01, 0.001]),
)
def test_to_polyline_respects_chord_error(radius: float, sweep: float, tol: float) -> None:
    arc = ArcCurve(center=(0.0, 0.0), radius=radius, start_deg=0.0, sweep_deg=sweep)
    pts = arc.to_polyline(tol)
    mids = (pts[:-1] + pts[1:]) / 2
    assert np.all(radius - np.linalg.norm(mids, axis=1) <= tol + 1e-9)


def test_to_shapely_closed_is_ring() -> None:
    assert isinstance(CircleCurve(center=(0, 0), radius=1).to_shapely(), shapely.LinearRing)
    assert isinstance(SEG.to_shapely(), shapely.LineString)


@pytest.mark.parametrize(
    "curve",
    [
        SEG,
        QUARTER,
        Polyline(points=((0, 0), (1, 1))),
        SplineCurve(points=((0, 0), (1, 1), (2, 0))),
        CompositeCurve(parts=(SEG, Segment(a=(4, 0), b=(4, 4)))),
        SEG.substring(1, 2),
    ],
)
def test_construction_round_trips(curve: object) -> None:
    recipe = curve.construction()  # type: ignore[attr-defined]
    assert curve_from_construction(recipe) == curve


def test_composite_concatenates_parts() -> None:
    corner = CompositeCurve(parts=(SEG, Segment(a=(4, 0), b=(4, 4))))
    assert approx_equal(corner.length(), 8.0)
    assert approx_equal(corner.point_at(6.0), (4.0, 2.0))
