#!/usr/bin/env python3

"""
test_curves_evaluation.py --- records the evaluation finding on very high degree Bezier curves

Contains:
    test_bezier_of_degree_1100_never_returns_a_non_finite_length: raises a ShowPyError or returns a
        finite length for 1,101 small control points
"""

import math

from showpy.core.errors import ShowPyError
from showpy.geo import shapes

HIGH_DEGREE = 1_100
COORDINATE_PERIOD = 7
COORDINATE_SPAN_M = 10.0


def test_bezier_of_degree_1100_never_returns_a_non_finite_length() -> None:
    """Raises a ShowPyError or returns a finite length; never NaN or infinity."""
    points = [
        (
            COORDINATE_SPAN_M * (i % COORDINATE_PERIOD) / COORDINATE_PERIOD,
            -COORDINATE_SPAN_M * ((i * 3) % COORDINATE_PERIOD) / COORDINATE_PERIOD,
        )
        for i in range(HIGH_DEGREE + 1)
    ]
    try:
        length = shapes.bezier(*points).length()
    except ShowPyError:
        return
    assert math.isfinite(length)
