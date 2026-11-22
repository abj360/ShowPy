#!/usr/bin/env python3

"""
test_geo_transforms.py --- checks transform invariants on random points, curves and sets
"""

import numpy as np
from hypothesis import assume, given
from hypothesis import strategies as st
from hypothesis.extra.numpy import arrays

from showpy.choreo.models import Placement, Set
from showpy.choreo.set_transforms import mirror, rotate, scale, shift
from showpy.core.ids import SetId, SpotId
from showpy.core.types import Points
from showpy.core.units import angle_diff, normalize_angle
from showpy.core.util import approx_equal
from showpy.geo.shapes import curve_through

TOL = 1e-6
COORD = 50.0

coords = st.floats(min_value=-COORD, max_value=COORD, allow_nan=False)
point_arrays = st.integers(min_value=1, max_value=12).flatmap(
    lambda n: arrays(np.float64, (n, 2), elements=coords)
)
facings = st.floats(min_value=0.0, max_value=359.0)
angles = st.floats(min_value=-720.0, max_value=720.0)
factors = st.floats(min_value=0.1, max_value=10.0)
anchors = st.one_of(st.sampled_from(["center", "centroid"]), st.tuples(coords, coords))
mirror_axes = st.one_of(
    st.sampled_from(["center", "x", "y"]),
    st.tuples(st.tuples(coords, coords), st.tuples(coords, coords)).filter(
        lambda ab: np.hypot(ab[0][0] - ab[1][0], ab[0][1] - ab[1][1]) > 1.0
    ),
)


@st.composite
def sets(draw: st.DrawFn) -> Set:
    pts = draw(point_arrays)
    rows = tuple(
        Placement(entity=SpotId(f"S{i}"), x=float(x), y=float(y), facing=draw(facings))
        for i, (x, y) in enumerate(pts)
    )
    return Set(id=SetId("1"), counts=0.0, placements=rows)


@given(point_arrays, anchors)
def test_rotate_360_is_identity(pts: Points, about: object) -> None:
    assert approx_equal(rotate(pts, 360.0, about=about), pts, abs_tol=TOL)  # type: ignore[arg-type]


@given(point_arrays, mirror_axes)
def test_mirror_twice_is_identity(pts: Points, axis: object) -> None:
    twice = mirror(mirror(pts, axis), axis)  # type: ignore[arg-type]
    assert approx_equal(twice, pts, abs_tol=TOL)


@given(point_arrays, coords, coords)
def test_shift_then_back_is_identity(pts: Points, dx: float, dy: float) -> None:
    assert approx_equal(shift(shift(pts, dx, dy), -dx, -dy), pts, abs_tol=TOL)


@given(point_arrays, factors, factors, st.tuples(coords, coords))
def test_scale_then_inverse_is_identity(
    pts: Points, sx: float, sy: float, about: tuple[float, float]
) -> None:
    there = scale(pts, sx, sy, about=about)
    assert approx_equal(scale(there, 1 / sx, 1 / sy, about=about), pts, abs_tol=TOL)


@given(sets(), angles)
def test_rotate_adds_the_angle_to_facings(s: Set, deg: float) -> None:
    turned = rotate(s, deg)
    for before, after in zip(s.placements, turned.placements, strict=True):
        assert abs(angle_diff(normalize_angle(before.facing + deg), after.facing)) < TOL


@given(sets(), mirror_axes)
def test_mirror_twice_restores_set_facings(s: Set, axis: object) -> None:
    back = mirror(mirror(s, axis), axis)  # type: ignore[arg-type]
    for before, after in zip(s.placements, back.placements, strict=True):
        assert approx_equal(after.point, before.point, abs_tol=TOL)
        assert abs(angle_diff(before.facing, after.facing)) < TOL


@given(st.lists(st.tuples(coords, coords), min_size=3, max_size=6, unique=True), angles)
def test_rotated_curve_matches_rotated_points(raw: list[tuple[float, float]], deg: float) -> None:
    pts = np.array(raw)
    assume(np.min(np.linalg.norm(np.diff(pts, axis=0), axis=1)) >= 1.0)
    curve = curve_through(*raw)
    turned = rotate(curve, deg, about=(0.0, 0.0))
    expected = rotate(curve.point_at_u(np.array([0.0, 1.0])), deg, about=(0.0, 0.0))
    assert approx_equal(turned.point_at_u(np.array([0.0, 1.0])), expected, abs_tol=TOL)
