#!/usr/bin/env python3

"""
test_spacing.py --- tests even spacing along curves, area fills, blocks, rings and scatter
"""

import math
import sys

import numpy as np
import pytest
import shapely
from hypothesis import given, settings
from hypothesis import strategies as st

from showpy.core.errors import ShowPyError, SpacingError, UnknownRefError
from showpy.core.util import approx_equal
from showpy.geo.curves import Segment
from showpy.geo.regions import region
from showpy.geo.shapes import arc, circle, line, rect
from showpy.geo.spacing import (
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

SIGNAL_TIMEOUT_S = 5
SQUARE_SIDE_M = 10.0
TINY_FILL_INTERVAL_M = 0.005
TINY_LINE_INTERVAL_M = 1e-5
TINY_LINE_LENGTH_M = 50.0
SEAM_SPOTS = 10
GAP_EXTRA_M = 0.5
RING_SPOTS = 4
RING_START_M = 1.0
CLEAR_SEPARATION_M = 1e-3
SECOND_SQUARE_SHIFT_M = 100.0
SECOND_SQUARE_SPOTS = 10
STRIP_LENGTH_M = 100.0
STRIP_WIDTH_M = 0.05
FIELD_SIDE_M = 20.0
EDGE_BAND_M = 1.0
MAX_EDGE_SPOTS = 8
WIDE_SPOTS = 20
FRACTIONAL_SEED = 1.5

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


def _square(origin_x: float = 0.0, side: float = SQUARE_SIDE_M) -> shapely.Polygon:
    return region([(origin_x, 0), (origin_x + side, 0), (origin_x + side, side), (origin_x, side)])


@pytest.mark.defect("UP-9.1-03")
@pytest.mark.xfail(strict=True, reason="UP-9.1-03")
@pytest.mark.timeout(SIGNAL_TIMEOUT_S, method="signal")
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_scatter_rejects_negative_min_gap() -> None:
    """Rejects a negative gap instead of looping forever (audit 9.1; P0 #7)."""
    with pytest.raises(SpacingError):
        scatter(_square(), 5, -1.0)


@pytest.mark.defect("UP-9.1-07")
@pytest.mark.xfail(strict=True, reason="UP-9.1-07")
@pytest.mark.timeout(SIGNAL_TIMEOUT_S, method="signal")
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_fill_caps_the_lattice_size() -> None:
    """Rejects a 4-million-point fill instead of running for 20 s (audit 9.1; P0 #8)."""
    with pytest.raises(SpacingError):
        fill(_square(), TINY_FILL_INTERVAL_M, anchor=(0.0, 0.0))


@pytest.mark.defect("UP-9.1-08")
@pytest.mark.xfail(strict=True, reason="UP-9.1-08")
@pytest.mark.timeout(SIGNAL_TIMEOUT_S, method="signal")
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_space_by_interval_caps_the_point_count() -> None:
    """Rejects a 5-million-point spacing instead of allocating it (audit 9.1; P0 #8)."""
    with pytest.raises(SpacingError):
        space_by_interval(line((0.0, 0.0), (TINY_LINE_LENGTH_M, 0.0)), TINY_LINE_INTERVAL_M)


@pytest.mark.defect("UP-9.1-26")
@pytest.mark.xfail(strict=True, reason="UP-9.1-26")
def test_regression_space_by_interval_closed_curve_has_no_seam_duplicate() -> None:
    """Places L/10 spots on a circle without a coincident first and last spot (audit 9.1)."""
    ring = circle((0.0, 0.0), 1.0)
    pts = space_by_interval(ring, ring.length() / SEAM_SPOTS)
    assert float(np.linalg.norm(pts[0] - pts[-1])) > CLEAR_SEPARATION_M


@pytest.mark.defect("UP-9.1-27")
@pytest.mark.xfail(strict=True, reason="UP-9.1-27")
def test_regression_space_with_gaps_closed_curve_does_not_repeat_start() -> None:
    """Keeps the last spot off the first spot on a closed curve (audit 9.1)."""
    pts = space_with_gaps(circle((0.0, 0.0), 1.0), 5, [(0, GAP_EXTRA_M)])
    assert float(np.linalg.norm(pts[0] - pts[-1])) > CLEAR_SEPARATION_M


@pytest.mark.defect("UP-9.1-29")
@pytest.mark.xfail(strict=True, reason="UP-9.1-29")
def test_regression_space_along_closed_curve_wraps_from_a_later_start() -> None:
    """Spaces spots evenly around a circle even when start > 0 (audit 9.1)."""
    pts = space_along(circle((0.0, 0.0), 1.0), RING_SPOTS, start=RING_START_M)
    gaps = np.linalg.norm(pts - np.roll(pts, -1, axis=0), axis=1)
    assert approx_equal(gaps, [math.sqrt(2.0)] * RING_SPOTS, tol=1e-9)


@pytest.mark.defect("UP-9.1-30")
@pytest.mark.xfail(strict=True, reason="UP-9.1-30")
def test_regression_space_along_rejects_end_before_start() -> None:
    """Rejects end < start instead of silently reversing the spots (audit 9.1)."""
    with pytest.raises(SpacingError):
        space_along(LINE, 3, start=8.0, end=2.0)


@pytest.mark.defect("UP-9.1-31")
@pytest.mark.xfail(strict=True, reason="UP-9.1-31")
def test_regression_space_along_rejects_end_past_length() -> None:
    """Rejects end > L instead of stacking spots on the clamped end (audit 9.1)."""
    with pytest.raises(SpacingError):
        space_along(LINE, 3, end=20.0)


@pytest.mark.defect("UP-9.1-32")
@pytest.mark.xfail(strict=True, reason="UP-9.1-32")
def test_regression_scatter_reaches_every_polygon_component() -> None:
    """Seeds each component of a MultiPolygon so the second square gets spots (audit 9.1)."""
    two_squares = shapely.MultiPolygon([_square(), _square(SECOND_SQUARE_SHIFT_M)])
    pts = scatter(two_squares, SECOND_SQUARE_SPOTS, 1.0)
    in_second = int((pts[:, 0] > SQUARE_SIDE_M).sum())
    assert in_second >= SECOND_SQUARE_SPOTS // 4


@pytest.mark.defect("UP-9.1-33")
@pytest.mark.xfail(strict=True, reason="UP-9.1-33")
def test_regression_scatter_fills_thin_regions() -> None:
    """Places 20 spots 1 m apart along a 100 x 0.05 m strip that fill() fills with 101."""
    strip = region(
        [(0, 0), (STRIP_LENGTH_M, 0), (STRIP_LENGTH_M, STRIP_WIDTH_M), (0, STRIP_WIDTH_M)]
    )
    assert len(scatter(strip, WIDE_SPOTS, 1.0)) == WIDE_SPOTS


@pytest.mark.defect("UP-9.1-34")
@pytest.mark.xfail(strict=True, reason="UP-9.1-34")
def test_regression_scatter_does_not_crowd_the_edge() -> None:
    """Keeps at most 8 of 20 spots within 1 m of the edge of a 20 m square (audit 9.1)."""
    pts = scatter(_square(side=FIELD_SIDE_M), WIDE_SPOTS, 1.0)
    near_edge = (pts.min(axis=1) < EDGE_BAND_M) | (pts.max(axis=1) > FIELD_SIDE_M - EDGE_BAND_M)
    assert int(near_edge.sum()) <= MAX_EDGE_SPOTS


@pytest.mark.defect("UP-9.1-53")
@pytest.mark.xfail(strict=True, reason="UP-9.1-53")
def test_regression_space_by_interval_rejects_unknown_align() -> None:
    """Raises UnknownRefError for align="middle" instead of leaking KeyError (audit 9.1)."""
    with pytest.raises(UnknownRefError):
        space_by_interval(LINE, 1.0, align="middle")  # type: ignore[arg-type]


@pytest.mark.defect("UP-9.1-54")
@pytest.mark.xfail(strict=True, reason="UP-9.1-54")
def test_regression_block_rejects_unknown_anchor() -> None:
    """Raises UnknownRefError for anchor="middle" instead of leaking KeyError (audit 9.1)."""
    with pytest.raises(UnknownRefError):
        block((0.0, 0.0), 2, 2, 1.0, anchor="middle")  # type: ignore[arg-type]


@pytest.mark.defect("UP-9.1-57")
@pytest.mark.xfail(strict=True, reason="UP-9.1-57")
def test_regression_space_proportional_rejects_zero_length_curves() -> None:
    """Raises SpacingError for all-zero lengths instead of leaking ZeroDivisionError."""
    dots = [Segment(a=(0.0, 0.0), b=(0.0, 0.0)), Segment(a=(1.0, 1.0), b=(1.0, 1.0))]
    with pytest.raises(SpacingError):
        space_proportional(dots, 4)


@pytest.mark.defect("UP-9.1-58")
@pytest.mark.xfail(strict=True, reason="UP-9.1-58")
def test_regression_fill_rejects_zero_interval() -> None:
    """Raises SpacingError for interval=0 instead of leaking ZeroDivisionError (audit 9.1)."""
    with pytest.raises(SpacingError):
        fill(_square(), 0.0)


@pytest.mark.defect("UP-9.1-59")
@pytest.mark.xfail(strict=True, reason="UP-9.1-59")
def test_regression_scatter_rejects_zero_min_gap() -> None:
    """Raises SpacingError for min_gap=0 instead of leaking ZeroDivisionError (audit 9.1)."""
    with pytest.raises(SpacingError):
        scatter(_square(), 3, 0.0)


@pytest.mark.defect("UP-9.1-61")
@pytest.mark.xfail(strict=True, reason="UP-9.1-61")
def test_regression_scatter_rejects_fractional_seed() -> None:
    """Raises a ShowPyError for seed=1.5 instead of leaking TypeError (audit 9.1)."""
    with pytest.raises(ShowPyError):
        scatter(_square(), 3, 1.0, seed=FRACTIONAL_SEED)  # type: ignore[arg-type]


@pytest.mark.defect("UP-9.1-62")
@pytest.mark.xfail(strict=True, reason="UP-9.1-62")
def test_regression_fill_rejects_empty_area() -> None:
    """Raises a ShowPyError for an empty area instead of leaking GEOSException (audit 9.1)."""
    with pytest.raises(ShowPyError):
        fill(shapely.Polygon(), 1.0)
