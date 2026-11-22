#!/usr/bin/env python3

"""
test_transforms.py --- tests every transform on points and curves

Contains:
    test_regression_affine_accepts_tiny_but_invertible_scales: applies a uniform 1e-7 scale instead
        of calling the plane collapsed
"""

import math

import numpy as np
import pytest

from showpy.core.errors import GeometryError, UnknownRefError
from showpy.core.util import approx_equal
from showpy.geo.curves import ArcCurve, CompositeCurve, Polyline, Segment
from showpy.geo.shapes import arc, circle, join, line, polyline
from showpy.geo.transforms import (
    affine,
    align,
    contract,
    distribute,
    expand,
    interpolate_forms,
    mirror,
    move_to,
    rotate,
    scale,
    set_interval,
    shift,
    skew,
    snap,
)

TOL = 1e-9
SQUARE = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 2.0], [0.0, 2.0]])


def test_shift_points_and_curve() -> None:
    assert shift(SQUARE, 1.0, -1.0)[0].tolist() == [1.0, -1.0]
    moved = shift(line((0, 0), (1, 0)), 0.0, 2.0)
    assert isinstance(moved, Segment)
    assert moved.a == (0.0, 2.0)


def test_inputs_are_not_mutated() -> None:
    pts = SQUARE.copy()
    rotate(pts, 30.0)
    assert np.array_equal(pts, SQUARE)


@pytest.mark.parametrize(
    ("anchor", "expected"),
    [
        ("center", (1.0, 1.0)),
        ("front-left", (0.0, 0.0)),
        ("front-center", (1.0, 0.0)),
        ("back-right", (2.0, 2.0)),
        ("left-center", (0.0, 1.0)),
        ("right-center", (2.0, 1.0)),
    ],
)
def test_move_to_puts_the_anchor_on_the_target(anchor: str, expected: tuple[float, float]) -> None:
    moved = move_to(SQUARE, anchor, (10.0, 10.0))  # type: ignore[arg-type]
    offset = np.array([10.0, 10.0]) - np.array(expected)
    assert approx_equal(moved, SQUARE + offset, abs_tol=TOL)


def test_move_to_centroid_and_unknown_anchor() -> None:
    pts = np.array([[0.0, 0.0], [0.0, 0.0], [3.0, 0.0]])
    assert approx_equal(move_to(pts, "centroid", (0.0, 0.0))[2], (2.0, 0.0), abs_tol=TOL)
    with pytest.raises(UnknownRefError) as caught:
        move_to(pts, "front-centre", (0.0, 0.0))  # type: ignore[arg-type]
    assert "front-center" in caught.value.hint


def test_rotate_about_center_and_point() -> None:
    assert approx_equal(rotate(SQUARE, 90.0)[0], (2.0, 0.0), abs_tol=TOL)
    assert approx_equal(rotate(SQUARE, 90.0, about=(0.0, 0.0))[1], (0.0, 2.0), abs_tol=TOL)


def test_rotate_keeps_arcs_exact() -> None:
    turned = rotate(arc((0, 0), 2.0, 0.0, 90.0), 90.0, about=(0.0, 0.0))
    assert isinstance(turned, ArcCurve)
    assert approx_equal(turned.start_deg, 90.0, abs_tol=1e-9)


def test_scale_and_negative_scale() -> None:
    assert approx_equal(scale(SQUARE, 2.0)[2], (3.0, 3.0), abs_tol=TOL)
    assert approx_equal(scale(SQUARE, 2.0, 1.0, about=(0.0, 0.0))[2], (4.0, 2.0), abs_tol=TOL)
    with pytest.raises(GeometryError) as caught:
        scale(SQUARE, -1.0)
    assert "mirror" in caught.value.hint


def test_expand_and_contract_move_points_radially() -> None:
    pts = np.array([[0.0, 0.0], [3.0, 0.0], [0.0, -4.0]])
    grown = expand(pts, 1.0, about=(0.0, 0.0))
    assert approx_equal(grown, [[0.0, 0.0], [4.0, 0.0], [0.0, -5.0]], abs_tol=TOL)
    shrunk = contract(pts, 2.0, about=(0.0, 0.0))
    assert approx_equal(shrunk, [[0.0, 0.0], [1.0, 0.0], [0.0, -2.0]], abs_tol=TOL)
    with pytest.raises(GeometryError) as caught:
        contract(pts, 3.5, about=(0.0, 0.0))
    assert caught.value.context["max_d"] == 3.0


def test_expand_curve_maps_its_points() -> None:
    grown = expand(polyline((-1, 0), (1, 0)), 1.0)
    assert isinstance(grown, Polyline)
    assert grown.points == ((-2.0, 0.0), (2.0, 0.0))
    ring = expand(circle((0, 0), 1.0), 1.0, about=(0.0, 0.0))
    assert approx_equal(np.linalg.norm(ring.point_at_u(0.3)), 2.0, abs_tol=1e-2)


def test_set_interval_respaces_about_the_center() -> None:
    pts = np.array([[0.0, 0.0], [1.0, 0.0], [2.0, 0.0], [3.0, 0.0]])
    wide = set_interval(pts, 2.0)
    assert approx_equal(wide[:, 0], [-1.5, 0.5, 2.5, 4.5], abs_tol=TOL)
    start = set_interval(pts, 0.5, anchor="start")
    assert approx_equal(start[:, 0], [0.0, 0.5, 1.0, 1.5], abs_tol=TOL)


def test_set_interval_follows_a_bend() -> None:
    pts = np.array([[0.0, 0.0], [2.0, 0.0], [2.0, 2.0]])
    tight = set_interval(pts, 1.0, anchor="start")
    assert approx_equal(tight, [[0.0, 0.0], [1.0, 0.0], [2.0, 0.0]], abs_tol=TOL)


@pytest.mark.parametrize(
    ("axis", "expected_first"),
    [("center", (4.0, 0.0)), ("x", (-1.0, 0.0)), ("y", (1.0, 2.0)), (((0, 0), (1, 1)), (0.0, 1.0))],
)
def test_mirror_axes(axis: object, expected_first: tuple[float, float]) -> None:
    pts = np.array([[1.0, 0.0], [3.0, 1.0], [4.0, 2.0]])
    assert approx_equal(mirror(pts, axis)[0], expected_first, abs_tol=TOL)  # type: ignore[arg-type]


def test_skew_shears_about_the_anchor() -> None:
    out = skew(SQUARE, deg_x=45.0, about=(0.0, 0.0))
    assert approx_equal(out[2], (4.0, 2.0), abs_tol=TOL)


def test_affine_accepts_2x3_and_3x3() -> None:
    m = [[1.0, 0.0, 5.0], [0.0, 1.0, 0.0]]
    assert affine(SQUARE, m)[0].tolist() == [5.0, 0.0]
    assert affine(SQUARE, [*m, [0.0, 0.0, 1.0]])[0].tolist() == [5.0, 0.0]
    with pytest.raises(GeometryError):
        affine(SQUARE, [[1.0, 0.0], [0.0, 1.0]])
    with pytest.raises(GeometryError):
        affine(SQUARE, [[0.0, 0.0, 0.0], [0.0, 0.0, 0.0]])


def test_snap_modes_and_report() -> None:
    pts = np.array([[0.24, 0.26], [1.0, 1.0], [-0.25, 0.0]])
    out, report = snap(pts, 0.5)
    assert approx_equal(out, [[0.0, 0.5], [1.0, 1.0], [-0.5, 0.0]], abs_tol=TOL)
    assert report.moved == 2
    assert approx_equal(report.max_shift, math.hypot(0.24, 0.24), abs_tol=TOL)
    floored, _ = snap(pts, 0.5, mode="floor", axes="x")
    assert approx_equal(floored[0], (0.0, 0.26), abs_tol=TOL)
    ceiled, _ = snap(np.array([[0.3, 0.0]]), 0.1, mode="ceil")
    assert approx_equal(ceiled[0], (0.3, 0.0), abs_tol=TOL)


def test_snap_rejects_bad_precision() -> None:
    with pytest.raises(GeometryError):
        snap(SQUARE, 0.0)


def test_align_values() -> None:
    pts = np.array([[0.0, 1.0], [2.0, 3.0]])
    assert align(pts, "y", "mean")[:, 1].tolist() == [2.0, 2.0]
    assert align(pts, "x", "max")[:, 0].tolist() == [2.0, 2.0]
    assert align(pts, "x", 7.0)[:, 0].tolist() == [7.0, 7.0]


def test_distribute_keeps_order_along_the_axis() -> None:
    pts = np.array([[5.0, 0.0], [0.0, 1.0], [1.0, 2.0], [10.0, 3.0]])
    out = distribute(pts, "x")
    assert approx_equal(out[:, 0], [20 / 3, 0.0, 10 / 3, 10.0], abs_tol=TOL)
    assert out[:, 1].tolist() == [0.0, 1.0, 2.0, 3.0]
    spread = distribute(pts, "x", start=0.0, end=30.0)
    assert approx_equal(spread[:, 0], [20.0, 0.0, 10.0, 30.0], abs_tol=TOL)


def test_interpolate_forms() -> None:
    a = np.array([[0.0, 0.0], [2.0, 0.0]])
    b = np.array([[0.0, 4.0], [2.0, 8.0]])
    assert interpolate_forms(a, b, 0.25).tolist() == [[0.0, 1.0], [2.0, 2.0]]
    with pytest.raises(GeometryError):
        interpolate_forms(a, b[:1], 0.5)
    with pytest.raises(GeometryError):
        interpolate_forms(a, b, 1.5)


def test_composite_curves_keep_their_parts() -> None:
    curve = join(line((0, 0), (1, 0)), line((1, 0), (1, 1)))
    grown = expand(curve, 1.0, about=(0.0, 0.0))
    assert isinstance(grown, CompositeCurve)
    assert len(grown.parts) == 2


TINY_UNIFORM_SCALE = 1e-7


@pytest.mark.defect("UP-9.1-19")
@pytest.mark.xfail(strict=True, reason="UP-9.1-19")
def test_regression_affine_accepts_tiny_but_invertible_scales() -> None:
    """Applies a uniform 1e-7 scale instead of calling the plane collapsed (audit 9.1)."""
    pts = np.array([[0.0, 0.0], [1.0, 2.0]])
    matrix = [[TINY_UNIFORM_SCALE, 0.0, 0.0], [0.0, TINY_UNIFORM_SCALE, 0.0]]
    assert approx_equal(affine(pts, matrix), pts * TINY_UNIFORM_SCALE, abs_tol=1e-15)
