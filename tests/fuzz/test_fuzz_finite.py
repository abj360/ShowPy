#!/usr/bin/env python3

"""
test_fuzz_finite.py --- fuzzes curve evaluation for non-finite results

Contains:
    CURVE_RECIPES: draws curve recipes with in-bounds points and extreme unchecked numbers
    test_fuzz_curve_results_are_finite: raises an E8sError or gives finite length, points and
        tangents for every curve recipe
"""

import math

import numpy as np
from hypothesis import given
from hypothesis import strategies as st

from e8s.core.caps import MAX_COORD_M
from e8s.geo.curves import curve_from_construction
from tests.helpers import only_e8s_errors

HUGE = 1e308
SAMPLES = 5

coordinates = st.one_of(
    st.floats(-MAX_COORD_M, MAX_COORD_M),
    st.sampled_from([MAX_COORD_M, -MAX_COORD_M, 0.0, 1e-200, 5e-324]),
)
points = st.tuples(coordinates, coordinates)
point_lists = st.lists(points, min_size=2, max_size=8)
radii = st.one_of(st.floats(1e-12, MAX_COORD_M), st.sampled_from([MAX_COORD_M, 1e-200]))
angles = st.floats(-720.0, 720.0)
extremes = st.one_of(
    st.floats(allow_nan=False, allow_infinity=False), st.just(HUGE), st.just(-HUGE)
)
matrices = st.tuples(
    st.tuples(extremes, extremes, extremes),
    st.tuples(extremes, extremes, extremes),
    st.just((0.0, 0.0, 1.0)),
)
path_numbers = st.sampled_from(
    ["0", "1", "-3.5", "1e6", "-1e6", "1e150", "1e308", "-1e308", "1e-300"]
)
path_data = st.builds(
    lambda start, steps: "M" + " ".join(start) + "".join(" L" + " ".join(s) for s in steps),
    st.tuples(path_numbers, path_numbers),
    st.lists(st.tuples(path_numbers, path_numbers), min_size=1, max_size=5),
)
CURVE_RECIPES = st.one_of(
    st.builds(lambda a, b: {"kind": "segment", "a": a, "b": b}, points, points),
    st.builds(
        lambda p, c: {"kind": "polyline", "points": p, "closed": c}, point_lists, st.booleans()
    ),
    st.builds(
        lambda p, a, c: {"kind": "spline", "points": p, "alpha": a, "closed": c},
        point_lists,
        st.one_of(st.floats(-2.0, 2.0), extremes),
        st.booleans(),
    ),
    st.builds(lambda p: {"kind": "bezier", "control": p}, point_lists),
    st.builds(
        lambda c, r, a, w: {
            "kind": "arc",
            "center": c,
            "radius": r,
            "start_deg": a,
            "sweep_deg": w,
        },
        points,
        radii,
        angles,
        angles,
    ),
    st.builds(lambda c, r: {"kind": "circle", "center": c, "radius": r}, points, radii),
    st.builds(
        lambda c, rx, ry, a: {
            "kind": "ellipse",
            "center": c,
            "rx": rx,
            "ry": ry,
            "rotation_deg": a,
        },
        points,
        radii,
        radii,
        angles,
    ),
    st.builds(
        lambda c, r0, r1, t: {"kind": "spiral", "center": c, "r0": r0, "r1": r1, "turns": t},
        points,
        radii,
        radii,
        st.floats(1e-6, 50.0),
    ),
    st.builds(lambda d, m: {"kind": "svg", "d": d, "transform": m}, path_data, matrices),
)


def _assert_curve_results_are_finite(recipe: dict[str, object]) -> None:
    curve = curve_from_construction(recipe)
    length = curve.length()
    assert math.isfinite(length), f"length {length}"
    stations = np.linspace(0.0, length, SAMPLES)
    for name, values in (
        ("points", curve.point_at(stations)),
        ("tangents", curve.tangent_at(stations)),
    ):
        assert np.all(np.isfinite(values)), f"{name} {values}"


@given(CURVE_RECIPES)
def test_fuzz_curve_results_are_finite(recipe: dict[str, object]) -> None:
    """Raises an E8sError or gives a finite length, finite points and finite tangents."""
    only_e8s_errors(lambda: _assert_curve_results_are_finite(recipe), recipe)
