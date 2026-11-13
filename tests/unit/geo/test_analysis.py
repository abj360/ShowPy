#!/usr/bin/env python3

"""
test_analysis.py --- tests density, coverage, balance, symmetry and change design measures
"""

import math
import sys

import pytest

from showpy.core.errors import GeometryError, ShowPyError, UnknownRefError
from showpy.core.util import approx_equal
from showpy.geo.analysis import balance, change_amount, coverage, density_map, symmetry_score
from tests.fixtures.shows import field_sheet, two_set_show
from tests.helpers import make_set

TOL = 1e-9
SIGNAL_TIMEOUT_S = 5
TINY_CELL_M = 0.005
LEFT_CLUSTER_SIZE = 10
CLUSTER_SPACING_M = 0.001
MIRROR_X_M = 2.0
MAX_MANY_TO_ONE_SCORE = 0.5


def test_density_map_counts_on_field_spots_per_cell() -> None:
    s = make_set(("A", -19.5, 0.5), ("B", -19.2, 0.9), ("C", 19.9, 19.9), ("D", 0, 0), off=("D",))
    grid = density_map(s, field_sheet(), cell=1.0)
    assert grid.shape == (20, 40)
    assert grid[0, 0] == 2
    assert grid[19, 39] == 1
    assert grid.sum() == 3


def test_density_map_ignores_spots_off_the_sheet() -> None:
    grid = density_map(make_set(("A", 0, -2), ("B", 0.5, 0.5)), field_sheet(), cell=5.0)
    assert grid.shape == (4, 8)
    assert grid.sum() == 1
    with pytest.raises(GeometryError):
        density_map(make_set(("A", 0, 1)), field_sheet(), cell=0.0)


def test_coverage_is_share_of_walkable_area() -> None:
    sheet = field_sheet()
    one = coverage(make_set(("A", 0, 10)), sheet, radius=2.0)
    assert approx_equal(one, math.pi * 4 / 800, tol=1e-4)
    edge = coverage(make_set(("A", 0, 0)), sheet, radius=2.0)
    assert approx_equal(edge, one / 2, tol=1e-4)
    assert coverage(make_set(("A", 0, 1), off=("A",)), sheet) == 0.0


def test_balance_left_right_and_front_back() -> None:
    sheet = field_sheet()
    assert balance(make_set(("A", -10, 10), ("B", 10, 10)), sheet) == 0.0
    assert approx_equal(balance(make_set(("A", 10, 10), ("B", 20, 10)), sheet), 0.75, tol=TOL)
    assert approx_equal(balance(make_set(("A", 0, 20)), sheet, axis="y"), 1.0, tol=TOL)
    assert balance(make_set(), sheet) == 0.0
    with pytest.raises(UnknownRefError):
        balance(make_set(("A", 0, 0)), sheet, axis="z")  # type: ignore[arg-type]


def test_symmetry_score_extremes() -> None:
    assert symmetry_score(make_set(("A", -2, 1), ("B", 2, 1), ("C", 0, 4))) == 1.0
    lopsided = symmetry_score(make_set(("A", 5, 1), ("B", 6, 1), ("C", 7, 1)))
    assert lopsided == 0.0
    nearly = symmetry_score(make_set(("A", -2, 1), ("B", 2.2, 1), ("C", 0, 4)))
    assert 0.8 < nearly < 1.0
    assert symmetry_score(make_set()) == 1.0


def test_change_amount_uses_spots_in_both_sets() -> None:
    show = two_set_show()
    assert approx_equal(change_amount(show.sets[0], show.sets[1]), 4.0, tol=TOL)
    a = make_set(("A", 0, 0), ("B", 0, 0), ("C", 0, 0), off=("C",))
    b = make_set(("A", 3, 4), ("C", 100, 0))
    assert change_amount(a, b) == 5.0
    assert change_amount(make_set(("A", 0, 0)), make_set(("B", 1, 1))) == 0.0


@pytest.mark.defect("UP-9.1-06")
@pytest.mark.xfail(strict=True, reason="UP-9.1-06")
@pytest.mark.timeout(SIGNAL_TIMEOUT_S, method="signal")
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_density_map_caps_the_grid_size() -> None:
    """Rejects a 32-million-cell grid instead of allocating it (audit 9.1; P0 #8)."""
    with pytest.raises(GeometryError):
        density_map(make_set(("A", 0, 1)), field_sheet(), cell=TINY_CELL_M)


@pytest.mark.defect("UP-9.1-36")
@pytest.mark.xfail(strict=True, reason="UP-9.1-36")
def test_regression_symmetry_score_matches_spots_one_to_one() -> None:
    """Scores ten spots on one side against one on the other below 0.5, not 0.993 (audit 9.1)."""
    left = [(f"L{i}", -MIRROR_X_M - CLUSTER_SPACING_M * i, 1.0) for i in range(LEFT_CLUSTER_SIZE)]
    lopsided = make_set(*left, ("R", MIRROR_X_M, 1.0))
    assert symmetry_score(lopsided) < MAX_MANY_TO_ONE_SCORE


@pytest.mark.defect("UP-9.1-63")
@pytest.mark.xfail(strict=True, reason="UP-9.1-63")
def test_regression_coverage_rejects_infinite_radius() -> None:
    """Raises a ShowPyError for radius=inf instead of leaking GEOSException (audit 9.1)."""
    with pytest.raises(ShowPyError):
        coverage(make_set(("A", 0, 10)), field_sheet(), radius=math.inf)
