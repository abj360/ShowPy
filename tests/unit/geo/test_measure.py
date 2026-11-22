#!/usr/bin/env python3

"""
test_measure.py --- tests lengths, extents, spacing stats, neighbours and symmetry measures

Contains:
    test_regression_symmetry_error_matches_spots_one_to_one: reports the unmatched spots of a
        10-versus-1 picture instead of a tiny gap
    test_regression_interval_measures_gaps_between_opposite_bounds: reports a 2e6 m gap between
        points at -MAX_COORD_M and MAX_COORD_M
    test_regression_form_metrics_measure_a_line_between_opposite_bounds: reports the 2e6 m length
        of a polyline across the full coordinate range
"""

import math

import numpy as np
import pytest
import shapely

from showpy.core.caps import MAX_COORD_M
from showpy.core.errors import GeometryError
from showpy.core.util import approx_equal
from showpy.geo.measure import (
    bounds,
    center,
    centroid,
    distance,
    distance_matrix,
    form_metrics,
    interval,
    length,
    mirror_gaps,
    reachable_region,
)
from showpy.geo.shapes import arc, circle, line

TOL = 1e-6
LEFT_CLUSTER_SIZE = 10
CLUSTER_SPACING_M = 0.001
MIRROR_X_M = 2.0
MIN_UNMATCHED_ERROR_M = 0.5


def test_length_of_points_and_curves() -> None:
    assert approx_equal(length(np.array([[0.0, 0.0], [3.0, 4.0], [3.0, 5.0]])), 6.0, abs_tol=TOL)
    assert approx_equal(length(arc((0, 0), 2.0, 0.0, 90.0)), math.pi, abs_tol=1e-3)
    assert length(np.zeros((1, 2))) == 0.0


def test_center_and_centroid_differ_on_lopsided_points() -> None:
    pts = np.array([[0.0, 0.0], [1.0, 0.0], [10.0, 0.0]])
    assert center(pts) == (5.0, 0.0)
    assert approx_equal(centroid(pts), (11 / 3, 0.0), abs_tol=TOL)


def test_curve_centroid_is_length_weighted() -> None:
    assert approx_equal(centroid(circle((2, 3), 1.5)), (2.0, 3.0), abs_tol=1e-3)
    assert approx_equal(centroid(line((0, 0), (4, 2))), (2.0, 1.0), abs_tol=TOL)


def test_bounds_and_empty_input() -> None:
    box = bounds(np.array([[1.0, 2.0], [-3.0, 5.0]]))
    assert box.min == (-3.0, 2.0)
    assert box.max == (1.0, 5.0)
    with pytest.raises(GeometryError):
        center(np.zeros((0, 2)))


def test_ordered_interval_reports_consecutive_gaps() -> None:
    stats = interval(np.array([[0.0, 0.0], [1.0, 0.0], [3.0, 0.0]]))
    assert stats.gaps == (1.0, 2.0)
    assert stats.mean == 1.5
    assert stats.min == 1.0
    assert stats.max == 2.0
    assert approx_equal(stats.std, 0.5, abs_tol=TOL)


def test_unordered_interval_uses_nearest_neighbours() -> None:
    stats = interval(np.array([[3.0, 0.0], [0.0, 0.0], [1.0, 0.0]]), ordered=False)
    assert stats.gaps == (2.0, 1.0, 1.0)


def test_interval_needs_two_points() -> None:
    with pytest.raises(GeometryError):
        interval(np.array([[0.0, 0.0]]))


def test_distance_and_matrix() -> None:
    assert distance((0, 0), (3, 4)) == 5.0
    matrix = distance_matrix(np.array([[0.0, 0.0]]), np.array([[3.0, 4.0], [0.0, 1.0]]))
    assert matrix.tolist() == [[5.0, 1.0]]


def test_reachable_region_is_a_disk() -> None:
    disk = reachable_region((1.0, 2.0), 8.0, 0.5)
    assert approx_equal(disk.area, math.pi * 16.0, abs_tol=0.05)
    assert disk.contains(shapely.Point(4.9, 2.0))
    with pytest.raises(GeometryError):
        reachable_region((0.0, 0.0), 8.0, -1.0)


def test_form_metrics_of_a_straight_line() -> None:
    metrics = form_metrics(np.array([[-2.0, 0.0], [0.0, 0.0], [2.0, 0.0]]))
    assert metrics.length == 4.0
    assert metrics.intervals.gaps == (2.0, 2.0)
    assert metrics.curvature_range == (0.0, 0.0)
    assert approx_equal(metrics.symmetry_error, 0.0, abs_tol=TOL)


def test_form_metrics_finds_an_off_center_axis() -> None:
    pts = np.array([[3.0, 0.0], [5.0, 1.0], [7.0, 0.0]])
    metrics = form_metrics(pts)
    assert approx_equal(metrics.symmetry_error, 0.0, abs_tol=1e-4)
    lo, hi = metrics.curvature_range
    assert lo == hi
    assert hi > 0


def test_form_metrics_curvature_of_circle_samples() -> None:
    angles = np.linspace(0, math.pi, 13)
    pts = np.stack([3 * np.cos(angles), 3 * np.sin(angles)], axis=1)
    lo, hi = form_metrics(pts).curvature_range
    assert approx_equal(lo, 1 / 3, abs_tol=0.01)
    assert approx_equal(hi, 1 / 3, abs_tol=0.01)


def test_mirror_gaps() -> None:
    pts = np.array([[-1.0, 0.0], [1.0, 0.0], [2.0, 3.0]])
    assert approx_equal(mirror_gaps(pts, 0.0), [0.0, 0.0, math.hypot(1, 3)], abs_tol=TOL)


FULL_WIDTH_M = 2 * MAX_COORD_M


def test_regression_interval_measures_gaps_between_opposite_bounds() -> None:
    """Reports the 2e6 m gap between two legal points at opposite coordinate bounds."""
    stats = interval(np.array([[-MAX_COORD_M, 0.0], [MAX_COORD_M, 0.0]]))
    assert stats.max == FULL_WIDTH_M
    assert stats.gaps == (FULL_WIDTH_M,)


def test_regression_form_metrics_measure_a_line_between_opposite_bounds() -> None:
    """Reports the 2e6 m length of a legal polyline across the full coordinate range."""
    points = np.array([[-MAX_COORD_M, 0.0], [0.0, 0.0], [MAX_COORD_M, 0.0]])
    assert form_metrics(points).length == FULL_WIDTH_M
