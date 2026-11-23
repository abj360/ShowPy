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
    test_regression_spiral_rejects_huge_finite_and_numpy_turns: raises GeometryError for 1e300
        turns and numpy scalars instead of overflowing
    test_regression_spiral_table_cap_sits_between_976_and_977_turns: builds 976 turns and rejects
        977
    test_regression_spline_rejects_a_table_over_the_cap: rejects a 4,000-point spline table
    test_regression_svg_curve_rejects_a_table_over_the_cap: rejects a 50,000-piece SVG table
    test_regression_trimmed_curve_rejects_out_of_range_arc_lengths: rejects s0/s1 outside [0, L] of
        an open base instead of reporting length 15
    test_regression_trimmed_breaks_merge_a_break_within_a_nanometer_of_an_end: drops a base break
        within 1e-9 m of a trim end instead of keeping a stub piece
    test_regression_composite_rejects_gapped_parts: rejects parts whose ends do not meet instead of
        jumping across the gap
    test_regression_composite_gap_names_the_part: names the first part that does not meet the one
        before it, and the gap in meters
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
    test_regression_extreme_svg_path_raises_geometry_error: raises a GeometryError for steps near
        1e308 instead of returning NaN
    test_regression_overflowing_curves_raise_geometry_error: raises a GeometryError, never a
        RuntimeWarning, for finite inputs whose lengths overflow
    test_regression_unit_tangent_survives_subnormal_intermediates: still evaluates a curve whose
        intermediate values underflow
"""

import math
import sys
from collections.abc import Callable
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
    MAX_BEZIER_DEGREE,
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
    assert approx_equal(oval.length(), expected, abs_tol=1e-4)


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
    assert approx_equal(mid, (1.0, 1.0), abs_tol=1e-4)


def test_spiral_radius_grows_from_r0_to_r1() -> None:
    spiral = SpiralCurve(center=(0.0, 0.0), r0=1.0, r1=3.0, turns=2.0)
    assert approx_equal(np.linalg.norm(spiral.point_at(0.0)), 1.0)
    assert approx_equal(np.linalg.norm(spiral.point_at(spiral.length())), 3.0)


def test_project_returns_arc_length_of_closest_point() -> None:
    assert approx_equal(SEG.project((1.5, 3.0)), 1.5)
    assert approx_equal(QUARTER.project((5.0, 5.0)), math.pi / 2, abs_tol=1e-6)


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


@fails_after(SIGNAL_TIMEOUT_S)
@pytest.mark.skipif(sys.platform != "linux", reason="needs signal timeout and RLIMIT_AS")
def test_regression_spiral_rejects_unbounded_turns() -> None:
    """Rejects 1e7 turns instead of tabulating 1e10 samples (audit 9.1; P0 #8).

    The address space is capped so a missing check fails fast instead of exhausting memory.
    """
    with capped_address_space(), pytest.raises(GeometryError):
        spiral((0.0, 0.0), 1.0, 2.0, HUGE_TURNS).length()


def test_regression_spiral_rejects_infinite_turns() -> None:
    """Rejects infinite turns with a ShowPyError instead of leaking OverflowError (audit 9.1)."""
    with pytest.raises(GeometryError):
        spiral((0.0, 0.0), 1.0, 2.0, math.inf).length()


@pytest.mark.parametrize(
    "turns", [1e300, np.float64(1e300), np.float64(math.inf), np.float64(math.nan), -1.0, 0.0]
)
def test_regression_spiral_rejects_huge_finite_and_numpy_turns(turns: float) -> None:
    """Raises GeometryError for 1e300 turns and numpy scalars instead of overflowing."""
    with pytest.raises(GeometryError):
        spiral((0.0, 0.0), 1.0, 2.0, turns)


SPIRAL_MAX_TURNS = 976
SPIRAL_MIN_REJECTED_TURNS = 977
OVER_TABLE_PIECES = 4000
OVER_TABLE_SVG_PIECES = 50_000


def test_regression_spiral_table_cap_sits_between_976_and_977_turns() -> None:
    """Builds a 976-turn spiral (999,425 samples) and rejects 977 turns (1,000,449 samples)."""
    assert spiral((0.0, 0.0), 1.0, 2.0, SPIRAL_MAX_TURNS).turns == SPIRAL_MAX_TURNS
    with pytest.raises(GeometryError):
        spiral((0.0, 0.0), 1.0, 2.0, SPIRAL_MIN_REJECTED_TURNS)


def test_regression_spline_rejects_a_table_over_the_cap() -> None:
    """Raises GeometryError for a 4,000-point spline (1,023,745 samples) instead of tabulating."""
    points = tuple((float(i), float(i % 7)) for i in range(OVER_TABLE_PIECES))
    with pytest.raises(GeometryError):
        SplineCurve(points=points).length()


def test_regression_svg_curve_rejects_a_table_over_the_cap() -> None:
    """Raises GeometryError for 50,000 pieces (12.8 million samples) instead of tabulating."""
    curve = SvgCurve(d="M0 0" + "h1" * OVER_TABLE_SVG_PIECES)
    with pytest.raises(GeometryError):
        curve.length()


def test_regression_trimmed_curve_rejects_out_of_range_arc_lengths() -> None:
    """Rejects s0/s1 outside [0, L] of an open base instead of reporting length 15 (audit 9.1)."""
    with pytest.raises(GeometryError):
        TrimmedCurve(base=line((0.0, 0.0), (1.0, 0.0)), s0=-5.0, s1=10.0)


VERTEX_ARC_LENGTH_M = 16.0
NEAR_END_OFFSETS_M = (math.nextafter(VERTEX_ARC_LENGTH_M, math.inf), 16.0000000005)


def _two_leg_polyline() -> Polyline:
    return Polyline(points=((0.0, 0.0), (VERTEX_ARC_LENGTH_M, 0.0), (VERTEX_ARC_LENGTH_M, 10.0)))


@pytest.mark.parametrize("end", NEAR_END_OFFSETS_M)
@pytest.mark.parametrize("before", [True, False])
def test_regression_trimmed_breaks_merge_a_break_within_a_nanometer_of_an_end(
    end: float, before: bool
) -> None:
    """Drops a base break that sits within 1e-9 m of the trim end instead of keeping a stub."""
    base = _two_leg_polyline()
    near = VERTEX_ARC_LENGTH_M - (end - VERTEX_ARC_LENGTH_M) if before else end
    for trimmed in (
        TrimmedCurve(base=base, s0=0.0, s1=near),
        TrimmedCurve(base=base, s0=near, s1=0.0),
        TrimmedCurve(base=base, s0=near, s1=base.length()),
        TrimmedCurve(base=base, s0=base.length(), s1=near),
    ):
        breaks = trimmed._breaks()
        assert breaks[0] == 0.0
        assert breaks[-1] == trimmed.length()
        assert np.min(np.diff(breaks)) > 1e-9


def test_trimmed_breaks_keep_a_break_farther_than_the_tolerance_from_an_end() -> None:
    """Keeps an inner base break 1e-6 m from the trim end."""
    trimmed = TrimmedCurve(base=_two_leg_polyline(), s0=0.0, s1=VERTEX_ARC_LENGTH_M + 1e-6)
    assert list(trimmed._breaks()) == [0.0, VERTEX_ARC_LENGTH_M, VERTEX_ARC_LENGTH_M + 1e-6]


def test_regression_composite_rejects_gapped_parts() -> None:
    """Rejects parts whose ends do not meet instead of jumping across the gap (audit 9.1)."""
    with pytest.raises(GeometryError):
        CompositeCurve(parts=(line((0.0, 0.0), (1.0, 0.0)), line((5.0, 5.0), (6.0, 5.0))))


def test_regression_composite_gap_names_the_part() -> None:
    """Names the first part that does not meet the previous one, and the gap in meters."""
    with pytest.raises(GeometryError) as raised:
        CompositeCurve(parts=(line((0.0, 0.0), (1.0, 0.0)), line((1.0, 5.0), (2.0, 5.0))))
    assert raised.value.context["part"] == 1
    assert raised.value.context["gap"] == pytest.approx(5.0)


def test_regression_composite_rejects_false_closure() -> None:
    """Rejects closed=True when the last part does not end on the first start (audit 9.1)."""
    open_parts = (line((0.0, 0.0), (1.0, 0.0)), line((1.0, 0.0), (1.0, 1.0)))
    with pytest.raises(GeometryError):
        CompositeCurve(parts=open_parts, closed=True)


def test_regression_svg_curve_rejects_closed_flag_on_open_path() -> None:
    """Rejects closed=True on an open path instead of wrapping it (audit 9.1)."""
    with pytest.raises(GeometryError):
        SvgCurve(d=OPEN_SVG_PATH, closed=True)


def test_regression_svg_curve_rejects_open_flag_on_closed_path() -> None:
    """Rejects closed=False on a path ending with Z instead of ignoring the Z (audit 9.1)."""
    with pytest.raises(GeometryError):
        SvgCurve(d=CLOSED_SVG_PATH, closed=False)


def test_regression_svg_curve_rejects_wrong_transform_shape() -> None:
    """Rejects a 1-row transform instead of returning wrong lengths and point shapes (audit 9.1)."""
    with pytest.raises(GeometryError):
        SvgCurve(d="M0 0 L1 0", transform=((1.0, 0.0, 0.0),))


def test_composite_accepts_parts_that_meet_and_a_true_closure() -> None:
    """Builds a closed triangle from three parts whose ends meet."""
    parts = (
        line((0.0, 0.0), (1.0, 0.0)),
        line((1.0, 0.0), (1.0, 1.0)),
        line((1.0, 1.0), (0.0, 0.0)),
    )
    assert CompositeCurve(parts=parts, closed=True).closed


def test_trimmed_curve_accepts_reversal_inside_an_open_base() -> None:
    """Builds the reverse of an open curve, whose s0 > s1 stays inside [0, L]."""
    assert line((0.0, 0.0), (2.0, 0.0)).reversed().length() == pytest.approx(2.0)


def test_bezier_degree_cap_is_inclusive() -> None:
    """Accepts a curve of exactly MAX_BEZIER_DEGREE and refuses one degree more."""
    controls = [(float(i), float(i % 3)) for i in range(MAX_BEZIER_DEGREE + 2)]
    assert bezier(*controls[:-1]).length() > 0
    with pytest.raises(GeometryError):
        bezier(*controls)


def test_regression_model_copy_update_raises_showpy_error() -> None:
    """Raises a ShowPyError for an invalid model_copy update, not a pydantic error (audit 9.1)."""
    with pytest.raises(ShowPyError):
        circle((0.0, 0.0), 1.0).model_copy(update={"radius": "wide"})


def test_regression_construction_with_unknown_kind_raises_showpy_error() -> None:
    """Raises a ShowPyError for an unknown curve kind, not a pydantic error (audit 9.1)."""
    with pytest.raises(ShowPyError):
        curve_from_construction({"kind": "nope"})


def test_regression_polyline_with_text_points_raises_showpy_error() -> None:
    """Raises a ShowPyError for text points, not a pydantic error (audit 9.1)."""
    with pytest.raises(ShowPyError):
        Polyline(points="abc")  # pyright: ignore[reportArgumentType] - deliberately invalid input


def test_regression_bezier_degree_past_overflow_raises_showpy_error() -> None:
    """Rejects a degree-1099 Bezier instead of returning NaN with RuntimeWarnings (audit 9.1)."""
    controls = [(float(i), float(i % 3)) for i in range(BEZIER_DEGREE_PAST_LIMIT)]
    with pytest.raises(GeometryError):
        bezier(*controls).length()


def test_regression_arc_rejects_infinite_radius() -> None:
    """Rejects an infinite radius instead of building an arc of infinite length (audit 9.1)."""
    with pytest.raises(GeometryError):
        arc((0.0, 0.0), math.inf, 0.0, 90.0).length()


EXTREME_SVG_PATHS = [
    "M0 0 L1e308 0 L-1e308 0",
    "M0 0 L1e300 1e300 L-1e300 -1e300 L1e300 1e300",
    "M0 0 C1e200 0 1e200 1e200 0 1e200",
]
TINY_M = 1e-200


@pytest.mark.parametrize("d", EXTREME_SVG_PATHS)
def test_regression_extreme_svg_path_raises_geometry_error(d: str) -> None:
    """Raises a GeometryError for steps near 1e308 instead of returning NaN or infinity."""
    curve = SvgCurve(d=d)
    with pytest.raises(GeometryError) as raised:
        curve.length()
    assert raised.value.context["kind"] == "svg"
    assert raised.value.context["operation"] == "length"
    with pytest.raises(GeometryError):
        curve.point_at(0.0)
    with pytest.raises(GeometryError):
        curve.tangent_at(0.0)


HUGE_FLOAT = 1e308
SPLINE_POINTS = ((0.0, 0.0), (5.0, 0.0), (5.0, 5.0), (0.0, 5.0))
HUGE_SCALE = ((HUGE_FLOAT, 0.0, 0.0), (0.0, HUGE_FLOAT, 0.0), (0.0, 0.0, 1.0))


@pytest.mark.parametrize(
    ("curve", "operation"),
    [
        (SplineCurve(points=SPLINE_POINTS, alpha=HUGE_FLOAT), "length"),
        (SvgCurve(d="M0 0 L1 0", transform=HUGE_SCALE), "length"),
        (SplineCurve(points=SPLINE_POINTS, alpha=HUGE_FLOAT), "point_at"),
        (SvgCurve(d="M0 0 L1 0", transform=HUGE_SCALE), "tangent_at"),
    ],
    ids=["spline-length", "svg-scale-length", "spline-point", "svg-scale-tangent"],
)
def test_regression_overflowing_curves_raise_geometry_error(
    curve: AnyCurve, operation: str
) -> None:
    """Raises a GeometryError, never a RuntimeWarning, for finite inputs whose results overflow."""
    calls: dict[str, Callable[[], object]] = {
        "length": curve.length,
        "point_at": lambda: curve.point_at(0.5),
        "tangent_at": lambda: curve.tangent_at(0.5),
    }
    with pytest.raises(GeometryError) as raised:
        calls[operation]()
    assert raised.value.context["kind"] == curve.kind


def test_regression_unit_tangent_survives_subnormal_intermediates() -> None:
    """Keeps evaluating a curve of tiny extent whose squared terms underflow."""
    curve = Polyline(points=((0.0, 0.0), (TINY_M, 0.0)))
    assert curve.length() == pytest.approx(TINY_M)


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
