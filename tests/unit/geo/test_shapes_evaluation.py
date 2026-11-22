#!/usr/bin/env python3

"""
test_shapes_evaluation.py --- records the evaluation finding on near-collinear arcs

Contains:
    test_arc_through_near_collinear_points_is_a_near_straight_line: keeps the length of a flat arc
        within 1e-9 m of the chord
"""

import pytest

from showpy.core.util import approx_equal
from showpy.geo import shapes

FLAT_ARC_LENGTH_M = 2.0
LENGTH_TOL_M = 1e-9
SAGITTA_M = 1e-12


@pytest.mark.defect("UP-E-06")
@pytest.mark.xfail(strict=True, reason="UP-E-06")
def test_arc_through_near_collinear_points_is_a_near_straight_line() -> None:
    """Measures 2.0 m, within 1e-9 m, for an arc through three nearly collinear points."""
    length = shapes.arc_through((0, 0), (1, SAGITTA_M), (2, 0)).length()
    assert approx_equal(length, FLAT_ARC_LENGTH_M, abs_tol=LENGTH_TOL_M)
