#!/usr/bin/env python3

"""
test_affine.py --- tests exact affine mapping of curves, points and facings
"""

import math

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st

from stage.core.util import approx_equal
from stage.geo.affine import (
    is_similarity,
    mirror_matrix,
    rotation_matrix,
    scale_matrix,
    transform_curve,
    transform_facing,
    transform_points,
    translation_matrix,
)
from stage.geo.curves import ArcCurve, CircleCurve, Polyline, SplineCurve
from stage.geo.shapes import arc, bezier, circle, curve_through, ellipse, join, line, spiral

CURVES = [
    line((0, 0), (4, 1)),
    arc((1, 1), 3.0, 10.0, 120.0),
    circle((2, 0), 2.0, 30.0),
    curve_through((0, 0), (2, 3), (5, 1)),
    bezier((0, 0), (1, 4), (3, 0)),
    spiral((0, 0), 1.0, 3.0, 1.5),
    ellipse((0, 0), 3.0, 1.0, 20.0),
    join(line((0, 0), (1, 0)), arc((1, 1), 1.0, -90.0, 90.0)),
    line((0, 0), (4, 0)).substring(1.0, 3.0),
]
MATRICES = [
    rotation_matrix(37.0, (1.0, 2.0)),
    translation_matrix(3.0, -2.0),
    scale_matrix(2.0, 2.0, (0.0, 0.0)),
    mirror_matrix((0.0, 0.0), (0.0, 1.0)),
    scale_matrix(2.0, 0.5, (0.0, 0.0)),
]


@pytest.mark.parametrize("curve", CURVES)
@pytest.mark.parametrize("matrix", MATRICES)
def test_mapped_curve_matches_mapped_points(curve: object, matrix: np.ndarray) -> None:
    mapped = transform_curve(curve, matrix)  # type: ignore[arg-type]
    for u in (0.0, 0.5, 1.0):
        expected = transform_points(curve.point_at_u(u), matrix)  # type: ignore[attr-defined]
        if is_similarity(matrix) or u in (0.0, 1.0):
            assert approx_equal(mapped.point_at_u(u), expected, tol=2e-3)


def test_similarity_keeps_arcs_exact() -> None:
    mapped = transform_curve(arc((0, 0), 1.0, 0.0, 90.0), scale_matrix(3.0, 3.0, (0, 0)))
    assert isinstance(mapped, ArcCurve)
    assert approx_equal(mapped.radius, 3.0)


def test_mirror_reverses_circle_direction() -> None:
    mapped = transform_curve(circle((1, 0), 1.0), mirror_matrix((0, 0), (0, 1)))
    assert isinstance(mapped, CircleCurve)
    assert not mapped.ccw
    quarter = mapped.point_at(mapped.length() / 4)
    assert approx_equal(quarter, (-1.0, 1.0))


def test_non_similarity_spline_maps_its_points() -> None:
    mapped = transform_curve(curve_through((0, 0), (1, 1), (2, 0)), scale_matrix(2, 1, (0, 0)))
    assert isinstance(mapped, SplineCurve)
    assert mapped.points == ((0.0, 0.0), (2.0, 1.0), (4.0, 0.0))


def test_non_similarity_arc_falls_back_to_polyline() -> None:
    assert isinstance(transform_curve(arc((0, 0), 1, 0, 90), scale_matrix(2, 1, (0, 0))), Polyline)


@given(st.floats(min_value=0, max_value=360), st.floats(min_value=-180, max_value=180))
def test_rotation_adds_to_facing(theta: float, deg: float) -> None:
    out = transform_facing(theta, rotation_matrix(deg, (0.0, 0.0)))
    assert abs(math.remainder(out - (theta + deg), 360.0)) < 1e-6


def test_mirror_across_vertical_axis_reflects_facing() -> None:
    assert approx_equal(transform_facing(90.0, mirror_matrix((0, 0), (0, 1))), 270.0)
    assert approx_equal(transform_facing(0.0, mirror_matrix((0, 0), (0, 1))), 0.0)
