#!/usr/bin/env python3

"""
test_svg_text.py --- tests SVG path import and the built-in stroke font

Contains:
    test_regression_parse_path_accepts_packed_arc_flags: parses packed arc flags the same as spaced
        ones
    test_regression_parse_path_scales_linearly_with_arc_count: parses twice the arcs in under 3x the
        time; a quadratic parser takes about 4x
    test_regression_svg_outline_stretches_a_degenerate_span: fits a flat line across the full 20 m
        box width instead of leaving it 5 m
    test_regression_svg_outline_ignores_data_d_attributes: reads only the d attribute, not data-d,
        so one path gives one curve
    test_regression_svg_outline_applies_the_transform_attribute: applies transform="scale(4 1)" so a
        unit triangle fits as 8 x 2 m
    test_regression_svg_outline_honours_the_viewbox: maps a shape at 10..20 of a 100-unit viewBox to
        1..2 m in a 10 m box
    test_regression_svg_outline_expands_use_elements: draws one curve per <use> and none for the
        <defs> template
    test_regression_svg_outline_reads_basic_shapes: outlines a <rect> element as one closed curve
    test_regression_parse_path_rejects_closepath_with_missing_arguments: raises SchemaError for "M0
        0 L1 Z" instead of leaking ValueError
    test_regression_parse_path_accepts_packed_flags_before_a_fraction: parses "a1 1 0 01.5.5" as
        flags 0 and 1 then the point (0.5, 0.5)
    test_regression_parse_path_rejects_arc_flag_two: rejects the arc flag 2, which SVG allows only
        as 0 or 1
    test_regression_parse_path_rejects_fractional_arc_flag: rejects the arc flag 0.5, which SVG
        allows only as 0 or 1
    test_regression_parse_path_rejects_overflowing_numbers: rejects 1e400 instead of turning it into
        infinity
    test_regression_parse_path_rejects_unicode_digits: rejects the Arabic-Indic digit three instead
        of reading it as 3
    test_regression_parse_path_requires_a_leading_move: rejects path data that does not start with M
    test_regression_parse_path_rejects_oversized_data: raises SchemaError for path data over
        MAX_TEXT_CHARS before tokenizing
    test_regression_parse_path_rejects_a_command_where_a_number_belongs: raises SchemaError for "MM"
        instead of leaking ValueError
    test_regression_parse_path_rejects_arcs_that_overflow: raises SchemaError for an arc with a
        1e300 endpoint instead of leaking OverflowError
    test_regression_svg_outline_rejects_empty_data: raises a ShowPyError for empty data instead of
        leaking ValueError
    test_regression_svg_outline_rejects_bad_path_index: raises a ShowPyError for an out-of-range
        path_index instead of leaking IndexError
    test_regression_parse_path_rejects_arcs_with_non_finite_geometry: raises SchemaError for 1e80
        radii instead of returning NaN angles
    test_regression_svg_outline_rejects_arcs_with_non_finite_geometry: raises SchemaError for 1e80
        radii instead of leaking IndexError
    test_regression_svg_outline_rejects_a_path_with_too_many_pieces: raises a ShowPyError for
        50,000 pieces
"""

import math
import statistics
import sys
import time

import numpy as np
import pytest

from showpy.core.caps import MAX_TEXT_CHARS
from showpy.core.errors import SchemaError, ShowPyError, UnknownRefError
from showpy.core.types import Box2
from showpy.core.util import approx_equal
from showpy.geo.curves import SvgCurve
from showpy.geo.shapes import svg_outline, text_outline
from showpy.geo.svg import parse_path
from tests.helpers import fails_after

BOX = Box2(min=(0.0, 0.0), max=(10.0, 10.0))
WIDE_BOX = Box2(min=(0.0, 0.0), max=(20.0, 2.0))
SCALED_BOX = Box2(min=(0.0, 0.0), max=(8.0, 8.0))
SIGNAL_TIMEOUT_S = 5
# 5 500 arcs take about 0.25 s today, so scheduler noise cannot swing one sample's ratio.
ARC_COUNT = 5_500
TIMING_RUNS = 5
SCALING_TIMEOUT_S = 10
MAX_SCALING_RATIO = 3.0
FIT_TOL_M = 1e-6
FLATTEN_TOL_M = 0.001
BAD_PATH_INDEX = 5
VIEWBOX_SVG = '<svg viewBox="0 0 100 100"><path d="M10 10 L20 10 L20 20"/></svg>'
USE_SVG = (
    '<svg><defs><path id="a" d="M0 0 L1 0"/></defs><use href="#a"/><use href="#a" x="5"/></svg>'
)


def test_parse_path_absolute_and_relative_lines() -> None:
    subpaths = parse_path("M0 0 L10 0 l0 10 h-10 z")
    assert len(subpaths) == 1
    assert subpaths[0].closed
    assert approx_equal(subpaths[0].pieces[0].end, (10.0, 0.0))


def test_parse_path_splits_subpaths() -> None:
    assert len(parse_path("M0 0 L1 0 M5 5 L6 5")) == 2


def test_parse_path_rejects_unknown_command() -> None:
    with pytest.raises(SchemaError):
        parse_path("M0 0 X4 4")


def test_svg_outline_fits_box_and_keeps_aspect() -> None:
    curves = svg_outline("M0 0 L20 0 L20 10 Z", BOX)
    pts = np.vstack([c.to_polyline(0.01) for c in curves])
    assert approx_equal(pts[:, 0].min(), 0.0)
    assert approx_equal(pts[:, 0].max(), 10.0)
    assert approx_equal(pts[:, 1].max() - pts[:, 1].min(), 5.0)


def test_svg_outline_flips_y_so_top_is_upstage() -> None:
    curve = svg_outline("M0 0 L0 10", BOX)[0]
    assert curve.point_at(0.0)[1] > curve.point_at(curve.length())[1]


def test_svg_cubic_and_arc_commands() -> None:
    cubic = svg_outline("M0 0 C0 10 10 10 10 0", BOX, keep_aspect=False)[0]
    assert cubic.length() > 10.0
    wide = Box2(min=(0.0, 0.0), max=(10.0, 5.0))
    half = svg_outline("M0 0 A5 5 0 0 1 10 0", wide, flip_y=False)[0]
    assert approx_equal(half.length(), math.pi * 5, tol=1e-3)


def test_svg_outline_selects_path_index() -> None:
    assert len(svg_outline("M0 0 L1 0 M0 1 L1 1", BOX, path_index=1)) == 1


def test_text_outline_height_and_position() -> None:
    curves = text_outline("HI", "simplex", 2.0, (0.0, 0.0))
    pts = np.vstack([c.to_polyline(0.01) for c in curves])
    assert approx_equal(pts[:, 1].max() - pts[:, 1].min(), 2.0)
    assert approx_equal(pts[:, 0].min(), 0.0)


def test_text_outline_unknown_font_or_glyph() -> None:
    with pytest.raises(UnknownRefError):
        text_outline("A", "comic", 1.0, (0, 0))
    with pytest.raises(UnknownRefError):
        text_outline("~", "simplex", 1.0, (0, 0))


def test_regression_parse_path_accepts_packed_arc_flags() -> None:
    packed = parse_path("M0 0 a1 1 0 011 1")
    spaced = parse_path("M0 0 a1 1 0 0 1 1 1")
    assert len(packed[0].pieces) == 1
    assert packed[0].pieces[0] == spaced[0].pieces[0]
    assert approx_equal(packed[0].pieces[0].end, (1.0, 1.0))


def _bounds(curves: list[SvgCurve]) -> tuple[float, float, float, float]:
    pts = np.vstack([curve.to_polyline(FLATTEN_TOL_M) for curve in curves])
    return (
        float(pts[:, 0].min()),
        float(pts[:, 1].min()),
        float(pts[:, 0].max()),
        float(pts[:, 1].max()),
    )


def _median_parse_times(arc_count: int) -> tuple[float, float]:
    """Times parse_path on `arc_count` and twice as many arcs, interleaved, median of each.

    Interleaving puts both sizes under the same background load.
    """
    small_path = "M0 0 " + "a1 1 0 011 1 " * arc_count
    large_path = "M0 0 " + "a1 1 0 011 1 " * (2 * arc_count)
    small_times: list[float] = []
    large_times: list[float] = []
    for _ in range(TIMING_RUNS):
        for path, times in ((small_path, small_times), (large_path, large_times)):
            started = time.perf_counter()
            parse_path(path)
            times.append(time.perf_counter() - started)
    return statistics.median(small_times), statistics.median(large_times)


@pytest.mark.slow
@fails_after(SCALING_TIMEOUT_S)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_parse_path_scales_linearly_with_arc_count() -> None:
    """Parses twice the arcs in under 3x the time; a quadratic parser takes about 4x (P0 #8)."""
    small, large = _median_parse_times(ARC_COUNT)
    assert large / small < MAX_SCALING_RATIO


@pytest.mark.defect("UP-9.1-37")
@pytest.mark.xfail(strict=True, reason="UP-9.1-37")
def test_regression_svg_outline_stretches_a_degenerate_span() -> None:
    """Fits a flat line across the full 20 m box width instead of leaving it 5 m (audit 9.1)."""
    min_x, _, max_x, _ = _bounds(svg_outline("M0 0 L5 0", WIDE_BOX))
    assert approx_equal(max_x - min_x, 20.0, tol=FIT_TOL_M)


@pytest.mark.defect("UP-9.1-38")
@pytest.mark.xfail(strict=True, reason="UP-9.1-38")
def test_regression_svg_outline_ignores_data_d_attributes() -> None:
    """Reads only the d attribute, not data-d, so one path gives one curve (audit 9.1)."""
    markup = '<path data-d="M0 0 L9 9" d="M0 0 L1 0"/>'
    assert len(svg_outline(markup, BOX)) == 1


@pytest.mark.defect("UP-9.1-39")
@pytest.mark.xfail(strict=True, reason="UP-9.1-39")
def test_regression_svg_outline_applies_the_transform_attribute() -> None:
    """Applies transform="scale(4 1)" so a unit triangle fits as 8 x 2 m (audit 9.1)."""
    markup = '<path d="M0 0 L1 0 L1 1 Z" transform="scale(4 1)"/>'
    min_x, min_y, max_x, max_y = _bounds(svg_outline(markup, SCALED_BOX))
    assert approx_equal((max_x - min_x, max_y - min_y), (8.0, 2.0), tol=FIT_TOL_M)


@pytest.mark.defect("UP-9.1-40")
@pytest.mark.xfail(strict=True, reason="UP-9.1-40")
def test_regression_svg_outline_honours_the_viewbox() -> None:
    """Maps a shape at 10..20 of a 100-unit viewBox to 1..2 m in a 10 m box (audit 9.1)."""
    min_x, _, max_x, _ = _bounds(svg_outline(VIEWBOX_SVG, BOX))
    assert approx_equal((min_x, max_x), (1.0, 2.0), tol=FIT_TOL_M)


@pytest.mark.defect("UP-9.1-41")
@pytest.mark.xfail(strict=True, reason="UP-9.1-41")
def test_regression_svg_outline_expands_use_elements() -> None:
    """Draws one curve per <use> and none for the <defs> template (audit 9.1)."""
    assert len(svg_outline(USE_SVG, BOX)) == 2


@pytest.mark.defect("UP-9.1-42")
@pytest.mark.xfail(strict=True, reason="UP-9.1-42")
def test_regression_svg_outline_reads_basic_shapes() -> None:
    """Outlines a <rect> element as one closed curve (audit 9.1)."""
    curves = svg_outline('<svg><rect width="2" height="1"/></svg>', BOX)
    assert len(curves) == 1
    assert curves[0].closed


def test_regression_parse_path_rejects_closepath_with_missing_arguments() -> None:
    """Raises SchemaError for "M0 0 L1 Z" instead of leaking ValueError (audit 9.1)."""
    with pytest.raises(SchemaError):
        parse_path("M0 0 L1 Z")


def test_regression_parse_path_rejects_oversized_data() -> None:
    """Raises SchemaError for path data over MAX_TEXT_CHARS before tokenizing it."""
    with pytest.raises(SchemaError):
        parse_path("M0 0" + " " * MAX_TEXT_CHARS)


def test_regression_parse_path_rejects_a_command_where_a_number_belongs() -> None:
    """Raises SchemaError for "MM" instead of leaking ValueError from float("M")."""
    with pytest.raises(SchemaError):
        parse_path("MM")


def test_regression_parse_path_rejects_arcs_that_overflow() -> None:
    """Raises SchemaError for an arc ending at 1e300 instead of leaking OverflowError."""
    with pytest.raises(SchemaError):
        parse_path("M0 0 A.1 .1 0 0 1 .1e300 .1")


NAN_ARC_PATH = "M0 0 A1e80 1e80 0 0 1 1 1"


def test_regression_parse_path_rejects_arcs_with_non_finite_geometry() -> None:
    """Raises SchemaError for 1e80 radii instead of returning an arc with NaN angles."""
    with pytest.raises(SchemaError):
        parse_path(NAN_ARC_PATH)


def test_regression_svg_outline_rejects_arcs_with_non_finite_geometry() -> None:
    """Raises SchemaError for 1e80 radii instead of leaking IndexError and a RuntimeWarning."""
    with pytest.raises(SchemaError):
        svg_outline(NAN_ARC_PATH + " L2 0", BOX)


def test_regression_svg_outline_rejects_a_path_with_too_many_pieces() -> None:
    """Raises a ShowPyError for 50,000 pieces instead of building a 12.8-million-sample table."""
    with pytest.raises(ShowPyError):
        svg_outline("M0 0" + "h1" * 50_000, BOX)


@pytest.mark.defect("UP-9.1-44")
@pytest.mark.xfail(strict=True, reason="UP-9.1-44")
def test_regression_parse_path_accepts_packed_flags_before_a_fraction() -> None:
    """Parses "a1 1 0 01.5.5" as flags 0 and 1 then the point (0.5, 0.5) (audit 9.1)."""
    piece = parse_path("M0 0 a1 1 0 01.5.5")[0].pieces[0]
    assert approx_equal(piece.end, (0.5, 0.5), tol=FIT_TOL_M)


@pytest.mark.defect("UP-9.1-45")
@pytest.mark.xfail(strict=True, reason="UP-9.1-45")
def test_regression_parse_path_rejects_arc_flag_two() -> None:
    """Rejects the arc flag 2, which SVG allows only as 0 or 1 (audit 9.1)."""
    with pytest.raises(SchemaError):
        parse_path("M0 0 a1 1 0 2 0 1 1")


@pytest.mark.defect("UP-9.1-46")
@pytest.mark.xfail(strict=True, reason="UP-9.1-46")
def test_regression_parse_path_rejects_fractional_arc_flag() -> None:
    """Rejects the arc flag 0.5, which SVG allows only as 0 or 1 (audit 9.1)."""
    with pytest.raises(SchemaError):
        parse_path("M0 0 a1 1 0 0.5 0 1 1")


def test_regression_parse_path_rejects_overflowing_numbers() -> None:
    """Rejects 1e400 instead of turning it into infinity (audit 9.1)."""
    with pytest.raises(SchemaError):
        parse_path("M0 0 L1e400 1")


@pytest.mark.defect("UP-9.1-48")
@pytest.mark.xfail(strict=True, reason="UP-9.1-48")
def test_regression_parse_path_rejects_unicode_digits() -> None:
    """Rejects the Arabic-Indic digit three instead of reading it as 3 (audit 9.1)."""
    with pytest.raises(SchemaError):
        parse_path("M0 0 L٣ 1")


@pytest.mark.defect("UP-9.1-49")
@pytest.mark.xfail(strict=True, reason="UP-9.1-49")
def test_regression_parse_path_requires_a_leading_move() -> None:
    """Rejects path data that does not start with M (audit 9.1)."""
    with pytest.raises(SchemaError):
        parse_path("L1 1")


@pytest.mark.defect("UP-9.1-55")
@pytest.mark.xfail(strict=True, reason="UP-9.1-55")
def test_regression_svg_outline_rejects_empty_data() -> None:
    """Raises a ShowPyError for empty data instead of leaking ValueError (audit 9.1)."""
    with pytest.raises(ShowPyError):
        svg_outline("", BOX)


@pytest.mark.defect("UP-9.1-56")
@pytest.mark.xfail(strict=True, reason="UP-9.1-56")
def test_regression_svg_outline_rejects_bad_path_index() -> None:
    """Raises a ShowPyError for an out-of-range path_index instead of leaking IndexError."""
    with pytest.raises(ShowPyError):
        svg_outline("M0 0 L1 1", BOX, path_index=BAD_PATH_INDEX)
