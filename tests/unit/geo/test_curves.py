#!/usr/bin/env python3

"""
test_curves.py --- tests the Curve implementations: length, sampling, projection, trimming

Contains:
    test_regression_model_copy_drops_cached_arc_length_table: drops the cached arc-length table when
        model_copy changes a spiral radius
    test_regression_spiral_rejects_unbounded_turns: rejects 1e7 turns instead of tabulating 1e10
        samples
    test_regression_spiral_rejects_infinite_turns: rejects infinite turns with a ShowPyError instead
        of leaking OverflowError
    test_regression_trimmed_curve_rejects_out_of_range_arc_lengths: rejects s0/s1 outside [0, L] of
        an open base instead of reporting length 15
    test_regression_composite_rejects_gapped_parts: rejects parts whose ends do not meet instead of
        jumping across the gap
    test_regression_composite_rejects_false_closure: rejects closed=True when the last part does not
        end on the first start
    test_regression_svg_curve_rejects_closed_flag_on_open_path: rejects closed=True on an open path
        instead of wrapping it
    test_regression_svg_curve_rejects_open_flag_on_closed_path: rejects closed=False on a path
        ending with Z instead of ignoring the Z
    test_regression_svg_curve_rejects_wrong_transform_shape: rejects a 1-row transform instead of
        returning wrong lengths and point shapes
    test_regression_model_copy_update_raises_showpy_error: raises a ShowPyError for an invalid
        model_copy update, not a pydantic error
    test_regression_construction_with_unknown_kind_raises_showpy_error: raises a ShowPyError for an
        unknown curve kind, not a pydantic error
    test_regression_polyline_with_text_points_raises_showpy_error: raises a ShowPyError for text
        points, not a pydantic error
    test_regression_bezier_degree_past_overflow_raises_showpy_error: rejects a degree-1099 Bezier
        instead of returning NaN with RuntimeWarnings
    test_regression_arc_rejects_infinite_radius: rejects an infinite radius instead of building an
        arc of infinite length
    test_regression_substring_of_unknown_curve_kind_raises_geometry_error: raises a GeometryError
        for a curve subclass outside AnyCurve, not a pydantic error
"""

import math
import sys
from typing import Literal, get_args

import numpy as np
import pytest
import shapely
from hypothesis import given, settings
from hypothesis import strategies as st

from showpy.core.errors import GeometryError, ShowPyError
from showpy.core.util import approx_equal
from showpy.geo.curves import (
    CURVE_TYPES,
    AnyCurve,
    ArcCurve,
    BezierCurve,
    CircleCurve,
    CompositeCurve,
    CurveModel,
    EllipseCurve,
    Polyline,
    Segment,
    SpiralCurve,
    SplineCurve,
    SvgCurve,
    TrimmedCurve,
    curve_from_construction,
)
from showpy.geo.shapes import arc, bezier, circle, line, spiral
from tests.helpers import capped_address_space, fails_after

SIGNAL_TIMEOUT_S = 5
HUGE_TURNS = 1e7
OPEN_SVG_PATH = "M0 0 L1 0 L1 1"
CLOSED_SVG_PATH = "M0 0 L1 0 L1 1 Z"
BEZIER_DEGREE_PAST_LIMIT = 1100

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


def test_regression_model_copy_drops_cached_arc_length_table() -> None:
    small = SpiralCurve(center=(0.0, 0.0), r0=1.0, r1=2.0, turns=1.0)
    small.length()
    bigger = small.model_copy(update={"r1": 6.0})
    assert bigger.length() > small.length() * 2


@pytest.mark.defect("UP-9.1-04")
@pytest.mark.xfail(strict=True, reason="UP-9.1-04")
@fails_after(SIGNAL_TIMEOUT_S)
@pytest.mark.skipif(sys.platform != "linux", reason="needs signal timeout and RLIMIT_AS")
def test_regression_spiral_rejects_unbounded_turns() -> None:
    """Rejects 1e7 turns instead of tabulating 1e10 samples (audit 9.1; P0 #8).

    The address space is capped so a missing check fails fast instead of exhausting memory.
    """
    with capped_address_space(), pytest.raises(GeometryError):
        spiral((0.0, 0.0), 1.0, 2.0, HUGE_TURNS).length()


@pytest.mark.defect("UP-9.1-05")
@pytest.mark.xfail(strict=True, reason="UP-9.1-05")
def test_regression_spiral_rejects_infinite_turns() -> None:
    """Rejects infinite turns with a ShowPyError instead of leaking OverflowError (audit 9.1)."""
    with pytest.raises(GeometryError):
        spiral((0.0, 0.0), 1.0, 2.0, math.inf).length()


@pytest.mark.defect("UP-9.1-20")
@pytest.mark.xfail(strict=True, reason="UP-9.1-20")
def test_regression_trimmed_curve_rejects_out_of_range_arc_lengths() -> None:
    """Rejects s0/s1 outside [0, L] of an open base instead of reporting length 15 (audit 9.1)."""
    with pytest.raises(GeometryError):
        TrimmedCurve(base=line((0.0, 0.0), (1.0, 0.0)), s0=-5.0, s1=10.0)


@pytest.mark.defect("UP-9.1-21")
@pytest.mark.xfail(strict=True, reason="UP-9.1-21")
def test_regression_composite_rejects_gapped_parts() -> None:
    """Rejects parts whose ends do not meet instead of jumping across the gap (audit 9.1)."""
    with pytest.raises(GeometryError):
        CompositeCurve(parts=(line((0.0, 0.0), (1.0, 0.0)), line((5.0, 5.0), (6.0, 5.0))))


@pytest.mark.defect("UP-9.1-22")
@pytest.mark.xfail(strict=True, reason="UP-9.1-22")
def test_regression_composite_rejects_false_closure() -> None:
    """Rejects closed=True when the last part does not end on the first start (audit 9.1)."""
    open_parts = (line((0.0, 0.0), (1.0, 0.0)), line((1.0, 0.0), (1.0, 1.0)))
    with pytest.raises(GeometryError):
        CompositeCurve(parts=open_parts, closed=True)


@pytest.mark.defect("UP-9.1-23")
@pytest.mark.xfail(strict=True, reason="UP-9.1-23")
def test_regression_svg_curve_rejects_closed_flag_on_open_path() -> None:
    """Rejects closed=True on an open path instead of wrapping it (audit 9.1)."""
    with pytest.raises(GeometryError):
        SvgCurve(d=OPEN_SVG_PATH, closed=True)


@pytest.mark.defect("UP-9.1-24")
@pytest.mark.xfail(strict=True, reason="UP-9.1-24")
def test_regression_svg_curve_rejects_open_flag_on_closed_path() -> None:
    """Rejects closed=False on a path ending with Z instead of ignoring the Z (audit 9.1)."""
    with pytest.raises(GeometryError):
        SvgCurve(d=CLOSED_SVG_PATH, closed=False)


@pytest.mark.defect("UP-9.1-25")
@pytest.mark.xfail(strict=True, reason="UP-9.1-25")
def test_regression_svg_curve_rejects_wrong_transform_shape() -> None:
    """Rejects a 1-row transform instead of returning wrong lengths and point shapes (audit 9.1)."""
    with pytest.raises(GeometryError):
        SvgCurve(d="M0 0 L1 0", transform=((1.0, 0.0, 0.0),))


@pytest.mark.defect("UP-9.1-50")
@pytest.mark.xfail(strict=True, reason="UP-9.1-50")
def test_regression_model_copy_update_raises_showpy_error() -> None:
    """Raises a ShowPyError for an invalid model_copy update, not a pydantic error (audit 9.1)."""
    with pytest.raises(ShowPyError):
        circle((0.0, 0.0), 1.0).model_copy(update={"radius": "wide"})


@pytest.mark.defect("UP-9.1-51")
@pytest.mark.xfail(strict=True, reason="UP-9.1-51")
def test_regression_construction_with_unknown_kind_raises_showpy_error() -> None:
    """Raises a ShowPyError for an unknown curve kind, not a pydantic error (audit 9.1)."""
    with pytest.raises(ShowPyError):
        curve_from_construction({"kind": "nope"})


@pytest.mark.defect("UP-9.1-52")
@pytest.mark.xfail(strict=True, reason="UP-9.1-52")
def test_regression_polyline_with_text_points_raises_showpy_error() -> None:
    """Raises a ShowPyError for text points, not a pydantic error (audit 9.1)."""
    with pytest.raises(ShowPyError):
        Polyline(points="abc")  # pyright: ignore[reportArgumentType] - deliberately invalid input


@pytest.mark.defect("UP-9.1-64")
@pytest.mark.xfail(strict=True, reason="UP-9.1-64")
def test_regression_bezier_degree_past_overflow_raises_showpy_error() -> None:
    """Rejects a degree-1099 Bezier instead of returning NaN with RuntimeWarnings (audit 9.1)."""
    controls = [(float(i), float(i % 3)) for i in range(BEZIER_DEGREE_PAST_LIMIT)]
    with pytest.raises(GeometryError):
        bezier(*controls).length()


@pytest.mark.defect("UP-9.1-65")
@pytest.mark.xfail(strict=True, reason="UP-9.1-65")
def test_regression_arc_rejects_infinite_radius() -> None:
    """Rejects an infinite radius instead of building an arc of infinite length (audit 9.1)."""
    with pytest.raises(GeometryError):
        arc((0.0, 0.0), math.inf, 0.0, 90.0).length()


class _UnlistedCurve(CurveModel):
    kind: Literal["unlisted"] = "unlisted"


def test_regression_substring_of_unknown_curve_kind_raises_geometry_error() -> None:
    """Raises a GeometryError for a curve subclass outside AnyCurve, not a pydantic error."""
    with pytest.raises(GeometryError) as raised:
        _UnlistedCurve().substring(0.0, 1.0)
    assert raised.value.context == {"kind": "unlisted"}


def test_curve_types_list_every_member_of_any_curve() -> None:
    union, _ = get_args(AnyCurve.__value__)
    assert set(get_args(union)) == set(CURVE_TYPES)
