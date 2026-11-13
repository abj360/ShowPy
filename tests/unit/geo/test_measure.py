#!/usr/bin/env python3

"""
test_measure.py --- tests lengths, extents, spacing stats, neighbours and symmetry measures
"""

import math

import numpy as np
import pytest
import shapely

from showpy.choreo.models import Placement, Set
from showpy.core.errors import GeometryError, UnknownRefError
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
    nearest,
    neighbors,
    on_field_ids,
    reachable_region,
    symmetry_error,
)
from showpy.geo.shapes import arc, circle, line

TOL = 1e-6


def _set(*rows: tuple[str, float, float], off: tuple[str, ...] = ()) -> Set:
    placements = tuple(Placement(entity=e, x=x, y=y, on_field=e not in off) for e, x, y in rows)
    return Set(id="1", counts=0.0, placements=placements)


def test_length_of_points_and_curves() -> None:
    assert approx_equal(length(np.array([[0.0, 0.0], [3.0, 4.0], [3.0, 5.0]])), 6.0, tol=TOL)
    assert approx_equal(length(arc((0, 0), 2.0, 0.0, 90.0)), math.pi, tol=1e-3)
    assert length(np.zeros((1, 2))) == 0.0


def test_center_and_centroid_differ_on_lopsided_points() -> None:
    pts = np.array([[0.0, 0.0], [1.0, 0.0], [10.0, 0.0]])
    assert center(pts) == (5.0, 0.0)
    assert approx_equal(centroid(pts), (11 / 3, 0.0), tol=TOL)


def test_curve_centroid_is_length_weighted() -> None:
    assert approx_equal(centroid(circle((2, 3), 1.5)), (2.0, 3.0), tol=1e-3)
    assert approx_equal(centroid(line((0, 0), (4, 2))), (2.0, 1.0), tol=TOL)


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
    assert approx_equal(stats.std, 0.5, tol=TOL)


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


def test_neighbors_skip_off_field_and_break_ties_by_id() -> None:
    s = _set(("T1", 0, 0), ("T10", 1, 0), ("T2", -1, 0), ("T3", 0.5, 0), off=("T3",))
    assert neighbors(s, "T1", k=2) == [("T2", 1.0), ("T10", 1.0)]


def test_neighbors_unknown_entity() -> None:
    with pytest.raises(UnknownRefError):
        neighbors(_set(("T1", 0, 0)), "T9")


def test_nearest_to_a_point() -> None:
    s = _set(("A", 0, 0), ("B", 5, 0), ("C", 2, 0), off=("C",))
    assert nearest(s, (1.0, 0.0)) == [("A", 1.0)]
    assert [e for e, _ in nearest(s, (1.0, 0.0), k=5)] == ["A", "B"]


def test_on_field_ids_keeps_selection_order() -> None:
    s = _set(("A", 0, 0), ("B", 1, 0), ("C", 2, 0), off=("B",))
    assert on_field_ids(s) == ("A", "C")
    assert on_field_ids(s, ["C", "B", "A"]) == ("C", "A")


def test_reachable_region_is_a_disk() -> None:
    disk = reachable_region((1.0, 2.0), 8.0, 0.5)
    assert approx_equal(disk.area, math.pi * 16.0, tol=0.05)
    assert disk.contains(shapely.Point(4.9, 2.0))
    with pytest.raises(GeometryError):
        reachable_region((0.0, 0.0), 8.0, -1.0)


def test_form_metrics_of_a_straight_line() -> None:
    metrics = form_metrics(np.array([[-2.0, 0.0], [0.0, 0.0], [2.0, 0.0]]))
    assert metrics.length == 4.0
    assert metrics.intervals.gaps == (2.0, 2.0)
    assert metrics.curvature_range == (0.0, 0.0)
    assert approx_equal(metrics.symmetry_error, 0.0, tol=TOL)


def test_form_metrics_finds_an_off_center_axis() -> None:
    pts = np.array([[3.0, 0.0], [5.0, 1.0], [7.0, 0.0]])
    metrics = form_metrics(pts)
    assert approx_equal(metrics.symmetry_error, 0.0, tol=1e-4)
    lo, hi = metrics.curvature_range
    assert lo == hi
    assert hi > 0


def test_form_metrics_curvature_of_circle_samples() -> None:
    angles = np.linspace(0, math.pi, 13)
    pts = np.stack([3 * np.cos(angles), 3 * np.sin(angles)], axis=1)
    lo, hi = form_metrics(pts).curvature_range
    assert approx_equal(lo, 1 / 3, tol=0.01)
    assert approx_equal(hi, 1 / 3, tol=0.01)


def test_mirror_gaps_and_symmetry_error() -> None:
    pts = np.array([[-1.0, 0.0], [1.0, 0.0], [2.0, 3.0]])
    assert approx_equal(mirror_gaps(pts, 0.0), [0.0, 0.0, math.hypot(1, 3)], tol=TOL)
    s = _set(("A", -1, 0), ("B", 1, 0), ("C", 2, 3), ("D", 9, 9), off=("D",))
    assert approx_equal(symmetry_error(s, axis_x=0.0), math.hypot(1, 3), tol=TOL)
    assert symmetry_error(s, ["A", "B"], axis_x=0.0) == 0.0
