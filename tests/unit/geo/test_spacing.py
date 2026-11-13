#!/usr/bin/env python3

"""
test_spacing.py --- tests even spacing along curves, area fills, blocks, rings and scatter
"""

import numpy as np
import pytest
import shapely
from hypothesis import given, settings
from hypothesis import strategies as st

from stage.core.errors import SpacingError
from stage.core.util import approx_equal
from stage.geo.regions import region
from stage.geo.shapes import arc, circle, line, rect
from stage.geo.spacing import (
    block,
    concentric,
    distribute_on_points,
    fill,
    ranks_and_files,
    scatter,
    space_along,
    space_by_interval,
    space_proportional,
    space_with_gaps,
)

LINE = line((0.0, 0.0), (10.0, 0.0))


def _gaps(pts: np.ndarray) -> np.ndarray:
    return np.linalg.norm(np.diff(pts, axis=0), axis=1)


def test_space_along_includes_endpoints() -> None:
    assert approx_equal(space_along(LINE, 3), [[0, 0], [5, 0], [10, 0]])


def test_space_along_without_endpoints_centers_cells() -> None:
    assert approx_equal(space_along(LINE, 2, endpoints=False), [[2.5, 0], [7.5, 0]])


def test_space_along_window_and_single_spot() -> None:
    assert approx_equal(space_along(LINE, 2, start=2.0, end=4.0), [[2, 0], [4, 0]])
    assert approx_equal(space_along(LINE, 1), [[5, 0]])


def test_space_along_closed_curve_does_not_duplicate_start() -> None:
    pts = space_along(circle((0, 0), 1.0), 4)
    assert len(np.unique(np.round(pts, 9), axis=0)) == 4


def test_space_along_rejects_zero() -> None:
    with pytest.raises(SpacingError):
        space_along(LINE, 0)


@settings(max_examples=25)
@given(st.integers(min_value=2, max_value=40), st.floats(min_value=1.0, max_value=50.0))
def test_space_along_arc_is_even(n: int, radius: float) -> None:
    pts = space_along(arc((0, 0), radius, 0.0, 120.0), n)
    gaps = _gaps(pts)
    assert approx_equal(gaps, np.full(n - 1, gaps[0]), tol=1e-6)


def test_space_by_interval_fills_max_that_fits() -> None:
    pts = space_by_interval(LINE, 3.0)
    assert len(pts) == 4
    assert approx_equal(pts[-1], (9.0, 0.0))


def test_space_by_interval_alignment() -> None:
    assert approx_equal(space_by_interval(LINE, 4.0, n=2, align="end"), [[6, 0], [10, 0]])
    assert approx_equal(space_by_interval(LINE, 4.0, n=2, align="center"), [[3, 0], [7, 0]])


def test_space_by_interval_reports_max_that_fits() -> None:
    with pytest.raises(SpacingError) as info:
        space_by_interval(LINE, 3.0, n=5)
    assert info.value.context["max_fit"] == 4


def test_space_proportional_shares_one_interval() -> None:
    short, long_ = line((0, 0), (4, 0)), line((0, 5), (12, 5))
    parts = space_proportional([short, long_], 6)
    assert [len(p) for p in parts] == [2, 4]
    assert approx_equal(_gaps(parts[0])[0], _gaps(parts[1])[0])


def test_space_with_gaps_adds_extra_after_index() -> None:
    pts = space_with_gaps(LINE, 4, [(1, 1.0)])
    gaps = _gaps(pts)
    assert approx_equal(gaps, [3.0, 4.0, 3.0])


def test_fill_grid_inside_region_at_interval() -> None:
    area = region(rect((0, 5), 4, 4))
    pts = fill(area, 1.0, anchor=(0.0, 5.0))
    assert len(pts) == 25
    assert all(area.buffer(1e-9).contains_properly(shapely.Point(p)) for p in pts)


def test_fill_hex_has_closest_gap_of_interval() -> None:
    pts = fill(region(rect((0, 0), 10, 10)), 1.0, pattern="hex")
    d = np.linalg.norm(pts[:, None] - pts[None, :], axis=2)
    np.fill_diagonal(d, np.inf)
    assert approx_equal(d.min(), 1.0)


def test_fill_n_picks_closest_to_anchor_and_margin_shrinks() -> None:
    area = region(rect((0, 0), 10, 10))
    assert len(fill(area, 1.0, n=5, anchor=(0.0, 0.0))) == 5
    assert len(fill(area, 1.0, margin=2.0)) < len(fill(area, 1.0))


def test_fill_too_many_raises() -> None:
    with pytest.raises(SpacingError):
        fill(region(rect((0, 0), 2, 2)), 1.0, n=100)


def test_block_front_row_first_toward_audience() -> None:
    pts = block((0.0, 10.0), rows=2, cols=3, interval=2.0)
    assert pts.shape == (6, 2)
    assert approx_equal(pts.mean(axis=0), (0.0, 10.0))
    assert np.all(pts[:3, 1] < pts[3:, 1])
    assert np.all(np.diff(pts[:3, 0]) > 0)


def test_block_anchor_front_left_and_facing() -> None:
    pts = block((0.0, 0.0), rows=2, cols=2, interval=1.0, anchor="front-left")
    assert approx_equal(pts[0], (0.0, 0.0))
    turned = block((0.0, 0.0), rows=1, cols=2, interval=1.0, facing=90.0, anchor="front-left")
    assert approx_equal(turned, [[0.0, 0.0], [0.0, 1.0]])


def test_ranks_and_files_indexes_block_order() -> None:
    assert ranks_and_files(block((0, 0), 2, 3, 1.0), 2, 3) == [[0, 1, 2], [3, 4, 5]]


def test_concentric_rings() -> None:
    rings = concentric((0.0, 0.0), [1.0, 2.0], [4, 8])
    assert [len(r) for r in rings] == [4, 8]
    assert approx_equal(np.linalg.norm(rings[1], axis=1), [2.0] * 8)


def test_scatter_respects_min_gap_and_seed() -> None:
    area = region(rect((0, 0), 10, 10))
    pts = scatter(area, 20, 1.5, seed=3)
    d = np.linalg.norm(pts[:, None] - pts[None, :], axis=2)
    np.fill_diagonal(d, np.inf)
    assert len(pts) == 20
    assert d.min() >= 1.5
    assert np.array_equal(pts, scatter(area, 20, 1.5, seed=3))


def test_scatter_too_dense_raises() -> None:
    with pytest.raises(SpacingError):
        scatter(region(rect((0, 0), 2, 2)), 50, 1.0)


def test_distribute_on_points_farthest_spreads() -> None:
    candidates = np.array([[0, 0], [0.1, 0], [10, 0], [5, 0]], dtype=float)
    picked = distribute_on_points(candidates, 2)
    assert {tuple(p) for p in picked} == {(0.0, 0.0), (10.0, 0.0)}


def test_distribute_on_points_random_is_seeded() -> None:
    candidates = np.arange(20, dtype=float).reshape(10, 2)
    a = distribute_on_points(candidates, 3, method="random", seed=1)
    assert np.array_equal(a, distribute_on_points(candidates, 3, method="random", seed=1))
