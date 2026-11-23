#!/usr/bin/env python3

"""
test_spacing.py --- tests even spacing along curves, area fills, blocks, rings and scatter

Contains:
    test_regression_scatter_rejects_negative_min_gap: rejects a negative gap instead of looping
        forever
    test_regression_fill_caps_the_lattice_size: rejects a 100-million-point fill instead of building
        the lattice
    test_regression_fill_stops_once_it_passes_the_limit: refuses a fill past `limit` without
        testing the rest of the lattice
    test_fill_within_the_limit_matches_an_unlimited_fill: keeps the spots a limit allows
    test_regression_space_along_caps_the_point_count: rejects more than MAX_POINTS spots
    test_regression_space_by_interval_caps_the_point_count: rejects a 100-million-point spacing
        instead of allocating it
    test_regression_space_by_interval_closed_curve_has_no_seam_duplicate: places L/10 spots on a
        circle without a coincident first and last spot
    test_regression_space_with_gaps_closed_curve_does_not_repeat_start: keeps the last spot off the
        first spot on a closed curve
    test_regression_space_along_closed_curve_wraps_from_a_later_start: spaces spots evenly around a
        circle even when start > 0
    test_regression_space_along_rejects_end_before_start: rejects end < start instead of silently
        reversing the spots
    test_regression_space_along_rejects_end_past_length: rejects end > L instead of stacking spots
        on the clamped end
    test_regression_scatter_reaches_every_polygon_component: seeds each component of a MultiPolygon
        so the second square gets spots
    test_regression_scatter_fills_thin_regions: places 20 spots 1 m apart along a 100 x 0.05 m strip
        that fill() fills with 101
    test_regression_scatter_does_not_crowd_the_edge: keeps at most 8 of 20 spots within 1 m of the
        edge of a 20 m square
    test_regression_space_by_interval_rejects_unknown_align: raises UnknownRefError for
        align="middle" instead of leaking KeyError
    test_regression_block_rejects_unknown_anchor: raises UnknownRefError for anchor="middle" instead
        of leaking KeyError
    test_regression_fill_rejects_a_huge_area_without_an_anchor: raises SpacingError for a 1e308
        square whose centroid overflows
    test_regression_space_by_interval_accepts_every_named_align: places the same spots for each
        of start, center and end as before
    test_regression_space_proportional_rejects_zero_length_curves: raises SpacingError for all-zero
        lengths instead of leaking ZeroDivisionError
    test_regression_fill_rejects_zero_interval: raises SpacingError for interval=0 instead of
        leaking ZeroDivisionError
    test_regression_scatter_rejects_zero_min_gap: raises SpacingError for min_gap=0 instead of
        leaking ZeroDivisionError
    test_regression_scatter_rejects_fractional_seed: raises an E8sError for seed=1.5 instead of
        leaking TypeError
    test_regression_fill_rejects_empty_area: raises an E8sError for an empty area instead of
        leaking GEOSException
    test_regression_scatter_rejects_n_over_the_cap: rejects n over MAX_POINTS before sampling
    test_regression_scatter_rejects_sample_bound_over_the_cap: rejects a gap whose sample bound is
        over MAX_POINTS before sampling
    test_regression_scatter_rejects_non_finite_min_gap: rejects NaN and infinite gaps
    test_regression_fill_rejects_non_finite_interval: rejects NaN and infinite intervals
    test_regression_space_by_interval_rejects_non_finite_interval: rejects NaN and infinite
        intervals
    test_regression_spacing_rejects_subnormal_gaps: raises SpacingError, not OverflowError, for a
        1e-320 gap or interval
    test_regression_fill_rejects_tiny_interval_without_overflow: raises SpacingError for a 1e-300
        interval
    test_regression_spacing_rejects_huge_area_with_small_gap: raises SpacingError for a 1e308 m
        area
    test_regression_spacing_rejects_empty_area: raises SpacingError for an empty area in scatter
        and fill
    test_scatter_sample_bound_counts_the_exact_multiple_cell: bounds samples by floor(w / cell) + 1
        per axis
    test_regression_fill_estimate_bounds_the_lattice: estimates at least the lattice the builder
        generates
    test_regression_fill_refuses_a_lattice_just_over_the_cap: refuses a 1,162,049-point lattice
    test_regression_space_by_interval_rejects_non_finite_start: names a non-finite start
    test_regression_fill_rejects_non_finite_angle: raises E8sError for NaN or inf angle_deg
        (UP-9.1-68)
    test_regression_fill_rejects_non_finite_margin: raises E8sError for NaN or inf margin
        (UP-9.1-69)
    test_regression_fill_rejects_non_finite_anchor: raises E8sError for a NaN or inf anchor
        (UP-9.1-70)
    test_regression_block_rejects_infinite_facing: raises E8sError for an inf facing (UP-9.1-71)
    test_regression_block_rejects_nan_facing: raises E8sError for a NaN facing (UP-9.1-73)
    test_regression_scatter_on_a_field_finishes_or_refuses_within_ten_seconds: bounds a field-size
        scatter near the cap to 10 s (UP-9.1-72)
"""

import math
import sys
from collections.abc import Callable
from typing import cast

import numpy as np
import pytest
import shapely
from hypothesis import given, settings
from hypothesis import strategies as st

from e8s.core.caps import MAX_POINTS
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError, SpacingError, UnknownRefError
from e8s.core.util import approx_equal
from e8s.geo.curves import Segment
from e8s.geo.regions import region
from e8s.geo.shapes import arc, circle, line, rect
from e8s.geo.spacing import (
    FillPattern,
    _lattice_axes,
    _lattice_estimate,
    _poisson_sample_bound,
    _require_lattice_size,
    apportion,
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
from tests.helpers import capped_address_space, defect, fails_after

SIGNAL_TIMEOUT_S = 5
HUGE_SIDE_M = 1e308
SQUARE_SIDE_M = 10.0
TINY_FILL_INTERVAL_M = 1e-3
TINY_LINE_INTERVAL_M = 1e-5
LONG_LINE_LENGTH_M = 1000.0
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
OVER_CAP_SPOTS = MAX_POINTS + 1
SUBNORMAL_GAP_M = 1e-320
TINY_GAP_M = 1e-300
HUGE_SIDE_M = 1e308
NON_FINITE = [math.nan, math.inf, -math.inf]
APPORTION_MIN_N = 4
HUGE_N = 10**18

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
    assert approx_equal(gaps, np.full(n - 1, gaps[0]), abs_tol=1e-6)


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


def test_apportion_shares_by_length_with_one_spot_each_by_default() -> None:
    assert apportion([4.0, 12.0], 6) == [2, 4]


def test_apportion_gives_each_curve_its_minimum_first() -> None:
    assert apportion([1.0, 100.0], 6, [3, 2]) == [3, 3]


def test_apportion_shares_equally_when_every_length_is_zero() -> None:
    assert apportion([0.0, 0.0], 4, [0, 0]) == [2, 2]


def test_apportion_names_the_smallest_n_that_meets_the_minimums() -> None:
    with pytest.raises(SpacingError) as raised:
        apportion([1.0, 1.0], 3, [2, 2])
    assert raised.value.code == "TOO_FEW_SPOTS"
    assert raised.value.context["min_n"] == APPORTION_MIN_N
    assert f"use n >= {APPORTION_MIN_N}" in raised.value.hint


def test_apportion_gives_leftovers_to_the_longer_curve_on_equal_fractions() -> None:
    assert apportion([1.0, 3.0], 2, [0, 0]) == [0, 2]


def test_apportion_breaks_full_ties_by_lower_index() -> None:
    assert apportion([2.0, 2.0, 2.0], 2, [0, 0, 0]) == [1, 1, 0]


def test_apportion_of_nothing_is_empty() -> None:
    assert apportion([], 0) == []


def test_apportion_rejects_spots_with_no_curves() -> None:
    with pytest.raises(SpacingError):
        apportion([], 1)


def test_apportion_counts_a_huge_n_exactly() -> None:
    counts = apportion([1.0, 1.0, 1.0], HUGE_N)
    assert sum(counts) == HUGE_N
    assert max(counts) - min(counts) == 1


def test_apportion_counts_exactly_at_the_largest_lengths() -> None:
    assert sum(apportion([sys.float_info.max] * 3, HUGE_N)) == HUGE_N


@pytest.mark.parametrize("n", [True, 2.0, "2", None, -1])
def test_apportion_rejects_a_bad_n(n: object) -> None:
    with pytest.raises(SpacingError):
        apportion([1.0], n)  # type: ignore[arg-type]


@pytest.mark.parametrize("length", [-1.0, *NON_FINITE, "1", True])
def test_apportion_rejects_a_bad_length(length: object) -> None:
    with pytest.raises(SpacingError):
        apportion([1.0, length], 4)  # type: ignore[list-item]


@pytest.mark.parametrize("minimum", [-1, 1.0, True, "1"])
def test_apportion_rejects_a_bad_minimum(minimum: object) -> None:
    with pytest.raises(SpacingError):
        apportion([1.0, 1.0], 4, [1, minimum])  # type: ignore[list-item]


def test_apportion_rejects_minimums_of_another_length() -> None:
    with pytest.raises(SpacingError) as raised:
        apportion([1.0, 1.0], 4, [1])
    assert raised.value.context == {"lengths": 2, "minimums": 1}


@st.composite
def _apportion_cases(draw: st.DrawFn) -> tuple[list[float], int, list[int]]:
    lengths = draw(st.lists(st.floats(min_value=0.0, max_value=100.0), min_size=1, max_size=30))
    minimums = draw(st.lists(st.integers(0, 3), min_size=len(lengths), max_size=len(lengths)))
    n = draw(st.integers(sum(minimums), sum(minimums) + 200))
    return lengths, n, minimums


@given(_apportion_cases())
def test_apportion_sums_exactly_and_respects_minimums(
    case: tuple[list[float], int, list[int]],
) -> None:
    lengths, n, minimums = case
    counts = apportion(lengths, n, minimums)
    assert sum(counts) == n
    assert all(count >= minimum for count, minimum in zip(counts, minimums, strict=True))
    assert apportion(lengths, n, minimums) == counts
    pairs = range(len(lengths))
    assert all(
        counts[i] >= counts[j]
        for i in pairs
        for j in pairs
        if minimums[i] == minimums[j] and lengths[i] > lengths[j]
    )


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


@fails_after(SIGNAL_TIMEOUT_S)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_scatter_rejects_negative_min_gap() -> None:
    """Rejects a negative gap instead of looping forever (audit 9.1; P0 #7)."""
    with pytest.raises(SpacingError):
        scatter(_square(), 5, -1.0)


@fails_after(SIGNAL_TIMEOUT_S)
@pytest.mark.skipif(sys.platform != "linux", reason="needs signal timeout and RLIMIT_AS")
def test_regression_fill_caps_the_lattice_size() -> None:
    """Rejects a 100-million-point fill instead of building the lattice (audit 9.1; P0 #8)."""
    with capped_address_space(), pytest.raises(SpacingError):
        fill(_square(), TINY_FILL_INTERVAL_M, anchor=(0.0, 0.0))


@fails_after(SIGNAL_TIMEOUT_S)
@pytest.mark.skipif(sys.platform != "linux", reason="needs signal timeout and RLIMIT_AS")
def test_regression_space_by_interval_caps_the_point_count() -> None:
    """Rejects a 100-million-point spacing instead of allocating it (audit 9.1; P0 #8)."""
    tiny_interval_line = line((0.0, 0.0), (LONG_LINE_LENGTH_M, 0.0))
    with capped_address_space(), pytest.raises(SpacingError):
        space_by_interval(tiny_interval_line, TINY_LINE_INTERVAL_M)


FINE_SQUARE = region([(0, 0), (40, 0), (40, 40), (0, 40)])
FINE_INTERVAL_M = 0.045
FILL_LIMIT = 10
FILL_LIMIT_BUDGET_S = 1


@fails_after(FILL_LIMIT_BUDGET_S)
def test_regression_fill_stops_once_it_passes_the_limit() -> None:
    """Refuses a fill past `limit` after limit + 1 spots, not after testing 790,000 lattice
    points, which took 9 s."""
    with pytest.raises(SpacingError) as caught:
        fill(FINE_SQUARE, FINE_INTERVAL_M, limit=FILL_LIMIT)
    assert caught.value.code == ErrorCode.TOO_MANY_SPOTS
    assert caught.value.context == {"limit": FILL_LIMIT}


def test_fill_within_the_limit_matches_an_unlimited_fill() -> None:
    """Returns the same spots with a limit the fill stays within, and refuses a bad limit."""
    square = region([(0, 0), (2, 0), (2, 2), (0, 2)])
    unlimited = fill(square, 1.0, anchor=(0, 0))
    assert approx_equal(fill(square, 1.0, anchor=(0, 0), limit=len(unlimited)), unlimited)
    with pytest.raises(SpacingError, match="limit"):
        fill(square, 1.0, limit=-1)


def test_regression_space_along_caps_the_point_count() -> None:
    """Rejects more than MAX_POINTS spots, a numpy count included, before computing any."""
    for count in (MAX_POINTS + 1, cast(int, np.int64(MAX_POINTS + 1))):
        with pytest.raises(SpacingError, match="over the cap"):
            space_along(LINE, count)
    assert len(space_along(LINE, 3)) == 3


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
    assert approx_equal(gaps, [math.sqrt(2.0)] * RING_SPOTS, abs_tol=1e-9)


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


def test_regression_space_by_interval_rejects_unknown_align() -> None:
    """Raises UnknownRefError for align="middle" instead of leaking KeyError (audit 9.1)."""
    with pytest.raises(UnknownRefError):
        space_by_interval(LINE, 1.0, align="middle")  # type: ignore[arg-type]


def test_regression_block_rejects_unknown_anchor() -> None:
    """Raises UnknownRefError for anchor="middle" instead of leaking KeyError (audit 9.1)."""
    with pytest.raises(UnknownRefError):
        block((0.0, 0.0), 2, 2, 1.0, anchor="middle")  # type: ignore[arg-type]


def test_regression_fill_rejects_a_huge_area_without_an_anchor() -> None:
    """Raises SpacingError for a 1e308 square, whose centroid is not finite, until anchored."""
    with pytest.raises(SpacingError):
        fill(_square(side=HUGE_SIDE_M), 1.0)


def test_regression_space_by_interval_accepts_every_named_align() -> None:
    """Shifts a 3-spot run by 0, half and all of the 4 m spare room on a 10 m line."""
    firsts = {
        align: float(space_by_interval(LINE, 3.0, n=3, align=align)[0, 0])
        for align in ("start", "center", "end")
    }
    assert firsts == {"start": 0.0, "center": 2.0, "end": 4.0}


def test_regression_space_proportional_rejects_zero_length_curves() -> None:
    """Raises SpacingError for all-zero lengths instead of leaking ZeroDivisionError."""
    dots = [Segment(a=(0.0, 0.0), b=(0.0, 0.0)), Segment(a=(1.0, 1.0), b=(1.0, 1.0))]
    with pytest.raises(SpacingError):
        space_proportional(dots, 4)


def test_regression_fill_rejects_zero_interval() -> None:
    """Raises SpacingError for interval=0 instead of leaking ZeroDivisionError (audit 9.1)."""
    with pytest.raises(SpacingError):
        fill(_square(), 0.0)


def test_regression_scatter_rejects_zero_min_gap() -> None:
    """Raises SpacingError for min_gap=0 instead of leaking ZeroDivisionError (audit 9.1)."""
    with pytest.raises(SpacingError):
        scatter(_square(), 3, 0.0)


def test_regression_scatter_rejects_fractional_seed() -> None:
    """Raises an E8sError for seed=1.5 instead of leaking TypeError (audit 9.1)."""
    with pytest.raises(E8sError):
        scatter(_square(), 3, 1.0, seed=FRACTIONAL_SEED)  # type: ignore[arg-type]


def test_regression_fill_rejects_empty_area() -> None:
    """Raises an E8sError for an empty area instead of leaking GEOSException (audit 9.1)."""
    with pytest.raises(E8sError):
        fill(shapely.Polygon(), 1.0)


def test_regression_scatter_rejects_n_over_the_cap() -> None:
    """Rejects n over MAX_POINTS with SpacingError before any sampling."""
    with pytest.raises(SpacingError):
        scatter(_square(), OVER_CAP_SPOTS, 1.0)


@fails_after(SIGNAL_TIMEOUT_S)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_scatter_rejects_sample_bound_over_the_cap() -> None:
    """Rejects a gap whose Poisson-disk sample bound passes MAX_POINTS, before sampling."""
    with pytest.raises(SpacingError):
        scatter(_square(), 5, TINY_FILL_INTERVAL_M)


@pytest.mark.parametrize("min_gap", NON_FINITE)
def test_regression_scatter_rejects_non_finite_min_gap(min_gap: float) -> None:
    """Rejects NaN and infinite gaps with SpacingError."""
    with pytest.raises(SpacingError):
        scatter(_square(), 3, min_gap)


@pytest.mark.parametrize("interval", NON_FINITE)
def test_regression_fill_rejects_non_finite_interval(interval: float) -> None:
    """Rejects NaN and infinite intervals with SpacingError."""
    with pytest.raises(SpacingError):
        fill(_square(), interval)


@pytest.mark.parametrize("interval", NON_FINITE)
def test_regression_space_by_interval_rejects_non_finite_interval(interval: float) -> None:
    """Rejects NaN and infinite intervals with SpacingError."""
    with pytest.raises(SpacingError):
        space_by_interval(LINE, interval)


@pytest.mark.parametrize(
    "call",
    [
        lambda: fill(_square(), SUBNORMAL_GAP_M, anchor=(0.0, 0.0)),
        lambda: scatter(_square(), 3, SUBNORMAL_GAP_M),
        lambda: space_by_interval(LINE, SUBNORMAL_GAP_M),
    ],
    ids=["fill", "scatter", "space_by_interval"],
)
def test_regression_spacing_rejects_subnormal_gaps(call: Callable[[], object]) -> None:
    """Raises SpacingError, not OverflowError, when the gap divides to infinity."""
    with pytest.raises(SpacingError):
        call()


def test_regression_fill_rejects_tiny_interval_without_overflow() -> None:
    """Raises SpacingError, not OverflowError, when the lattice count exceeds a C ssize_t."""
    with pytest.raises(SpacingError):
        fill(_square(), TINY_GAP_M, anchor=(0.0, 0.0))


@pytest.mark.parametrize(
    "call",
    [
        lambda: scatter(_square(side=HUGE_SIDE_M), 3, 1e-5),
        lambda: fill(_square(side=HUGE_SIDE_M), 1e-5, anchor=(0.0, 0.0)),
    ],
    ids=["scatter", "fill"],
)
def test_regression_spacing_rejects_huge_area_with_small_gap(call: Callable[[], object]) -> None:
    """Raises SpacingError, not OverflowError, for an area with 1e308 m sides."""
    with pytest.raises(SpacingError):
        call()


def test_regression_spacing_rejects_empty_area() -> None:
    """Raises SpacingError, not a leaked error, when the area has NaN bounds."""
    with pytest.raises(SpacingError):
        scatter(shapely.Polygon(), 3, 1.0)
    with pytest.raises(SpacingError):
        fill(shapely.Polygon(), 1.0, anchor=(0.0, 0.0))


def test_scatter_sample_bound_counts_the_exact_multiple_cell() -> None:
    """Bounds samples by floor(w / cell) + 1 per axis, so an exact multiple gains a cell."""
    cell_m = 2.0
    assert _poisson_sample_bound(_square(side=4.0), cell_m * math.sqrt(2)) == 3 * 3


LATTICE_BOUNDS = [
    (-0.01, -0.01, 2.01, 2.01),
    (-0.001, -0.001, 997.9, 997.9),
    (0.5, 0.5, 1.5, 1.5),
    (-3.2, -7.9, 11.1, 5.05),
    (0.0, 0.0, 3.0, 3.0),
    (-0.01, -0.01, 166000.01, 2.01),
]


@pytest.mark.parametrize("pattern", ["grid", "offset", "hex"])
@pytest.mark.parametrize("bounds", LATTICE_BOUNDS)
def test_regression_fill_estimate_bounds_the_lattice(
    bounds: tuple[float, ...], pattern: FillPattern
) -> None:
    """Estimates at least the rows times columns that the lattice builder generates."""
    _, rows, cols = _lattice_axes(bounds, 1.0, pattern)
    assert _lattice_estimate(bounds, 1.0, pattern) >= len(rows) * len(cols)


def test_regression_fill_refuses_a_lattice_just_over_the_cap() -> None:
    """Refuses bounds whose lattice has 1,162,049 points, over the cap of 1,000,000."""
    with pytest.raises(SpacingError):
        _require_lattice_size((-0.01, -0.01, 166000.01, 2.01), 1.0, "grid")


NON_FINITE_PARAMETER = [math.nan, math.inf, -math.inf]
SCATTER_DEADLINE_S = 10
FIELD_LENGTH_M = 109.7
FIELD_WIDTH_M = 48.8
NEAR_CAP_GAP_M = 0.11


@pytest.mark.parametrize("start", NON_FINITE_PARAMETER)
def test_regression_space_by_interval_rejects_non_finite_start(start: float) -> None:
    """Names a non-finite start in the message instead of calling the interval too small."""
    with pytest.raises(SpacingError, match="start"):
        space_by_interval(LINE, 1.0, start=start)


@pytest.mark.parametrize("angle_deg", NON_FINITE_PARAMETER)
def test_regression_fill_rejects_non_finite_angle(angle_deg: float) -> None:
    """Raises a SpacingError for a NaN or infinite angle_deg instead of leaking."""
    with pytest.raises(E8sError):
        fill(_square(), 1.0, angle_deg=angle_deg)


@pytest.mark.parametrize("margin", NON_FINITE_PARAMETER)
def test_regression_fill_rejects_non_finite_margin(margin: float) -> None:
    """Raises a SpacingError for a NaN or infinite margin instead of leaking."""
    with pytest.raises(E8sError):
        fill(_square(), 1.0, margin=margin)


@pytest.mark.parametrize("coordinate", NON_FINITE_PARAMETER)
def test_regression_fill_rejects_non_finite_anchor(coordinate: float) -> None:
    """Raises a SpacingError for a NaN or infinite anchor instead of leaking GEOSException."""
    with pytest.raises(E8sError):
        fill(_square(), 1.0, anchor=(coordinate, 0.0))


@pytest.mark.parametrize("facing", [math.inf, -math.inf])
def test_regression_block_rejects_infinite_facing(facing: float) -> None:
    """Raises a SpacingError for an infinite facing instead of leaking ValueError."""
    with pytest.raises(E8sError):
        block((0.0, 0.0), 2, 2, 1.0, facing=facing)


def test_regression_block_rejects_nan_facing() -> None:
    """Raises an E8sError for a NaN facing instead of returning NaN points."""
    with pytest.raises(E8sError):
        block((0.0, 0.0), 2, 2, 1.0, facing=math.nan)


@defect("UP-9.1-72")
@pytest.mark.slow
@pytest.mark.timing
@fails_after(SCATTER_DEADLINE_S)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_scatter_on_a_field_finishes_or_refuses_within_ten_seconds() -> None:
    """Finishes or refuses a field-size scatter at gap 0.11 m within 10 s, not minutes.

    The sample bound at this gap is about 885,000, just under MAX_POINTS. Ten seconds is the
    longest an interactive caller waits on one placement.
    """
    field = region(
        [(0, 0), (FIELD_LENGTH_M, 0), (FIELD_LENGTH_M, FIELD_WIDTH_M), (0, FIELD_WIDTH_M)]
    )
    try:
        scatter(field, 5, NEAR_CAP_GAP_M)
    except SpacingError:
        return
