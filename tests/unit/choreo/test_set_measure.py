#!/usr/bin/env python3

"""
test_set_measure.py --- tests set measures: on-field spots, neighbours, nearest and symmetry error

Contains:
    test_regression_symmetry_error_matches_spots_one_to_one: reports the unmatched spots of a
        10-versus-1 picture instead of a tiny gap
    test_symmetry_error_of_a_set: measures the mirror gap of on-field spots about an axis
"""

import math

import pytest

from e8s.choreo.set_measure import nearest, neighbors, on_field_ids, symmetry_error
from e8s.core.errors import UnknownRefError
from e8s.core.util import approx_equal
from tests.helpers import make_set

TOL = 1e-6
LEFT_CLUSTER_SIZE = 10
CLUSTER_SPACING_M = 0.001
MIRROR_X_M = 2.0
MIN_UNMATCHED_ERROR_M = 0.5


def test_neighbors_skip_off_field_and_break_ties_by_id() -> None:
    s = make_set(("T1", 0, 0), ("T10", 1, 0), ("T2", -1, 0), ("T3", 0.5, 0), off=("T3",))
    assert neighbors(s, "T1", k=2) == [("T2", 1.0), ("T10", 1.0)]


def test_neighbors_unknown_entity() -> None:
    with pytest.raises(UnknownRefError):
        neighbors(make_set(("T1", 0, 0)), "T9")


def test_nearest_to_a_point() -> None:
    s = make_set(("A", 0, 0), ("B", 5, 0), ("C", 2, 0), off=("C",))
    assert nearest(s, (1.0, 0.0)) == [("A", 1.0)]
    assert [e for e, _ in nearest(s, (1.0, 0.0), k=5)] == ["A", "B"]


def test_on_field_ids_keeps_selection_order() -> None:
    s = make_set(("A", 0, 0), ("B", 1, 0), ("C", 2, 0), off=("B",))
    assert on_field_ids(s) == ("A", "C")
    assert on_field_ids(s, ["C", "B", "A"]) == ("C", "A")


@pytest.mark.defect("UP-9.1-35")
@pytest.mark.xfail(strict=True, reason="UP-9.1-35")
def test_regression_symmetry_error_matches_spots_one_to_one() -> None:
    """Reports the unmatched spots of a 10-versus-1 picture instead of a tiny gap (audit 9.1)."""
    left = [(f"L{i}", -MIRROR_X_M - CLUSTER_SPACING_M * i, 1.0) for i in range(LEFT_CLUSTER_SIZE)]
    lopsided = make_set(*left, ("R", MIRROR_X_M, 1.0))
    assert symmetry_error(lopsided) > MIN_UNMATCHED_ERROR_M


def test_symmetry_error_of_a_set() -> None:
    s = make_set(("A", -1, 0), ("B", 1, 0), ("C", 2, 3), ("D", 9, 9), off=("D",))
    assert approx_equal(symmetry_error(s, axis_x=0.0), math.hypot(1, 3), abs_tol=TOL)
    assert symmetry_error(s, ["A", "B"], axis_x=0.0) == 0.0
