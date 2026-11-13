#!/usr/bin/env python3

"""
sheet.py --- strategies for design sheets and for points that stand on them

Contains:
    sheets(): draws a built-in template or a blank sheet of drawn size
    inner_box(): returns the sheet rectangle shrunk by a margin, as (x0, y0, x1, y1)
    points_on(): draws a point inside the sheet, a margin away from its edges
    headings(): draws a facing in degrees, in [0, 360)
"""

from hypothesis import strategies as st

from showpy.core.types import Point
from showpy.sheet.models import Sheet
from showpy.sheet.templates import blank_sheet, sheet_templates

MIN_BLANK_M = 12.0
MAX_BLANK_M = 60.0
EDGE_MARGIN_M = 1.0
FULL_TURN = 360.0

type Box = tuple[float, float, float, float]


def sheets() -> st.SearchStrategy[Sheet]:
    """Draws a built-in sheet template or a blank sheet of whole-meter size."""
    sizes = st.integers(min_value=int(MIN_BLANK_M), max_value=int(MAX_BLANK_M))
    blank = st.builds(blank_sheet, sizes.map(float), sizes.map(float))
    return st.one_of(st.sampled_from(sheet_templates()), blank)


def inner_box(sheet: Sheet, margin: float = EDGE_MARGIN_M) -> Box:
    """Returns the sheet rectangle (origin front-center) shrunk by `margin` on every side.

    Returns:
        box: (x_min, y_min, x_max, y_max) in meters.
    """
    width, depth = sheet.size_m
    half = width / 2
    return (-half + margin, margin, half - margin, depth - margin)


def points_on(sheet: Sheet, margin: float = EDGE_MARGIN_M) -> st.SearchStrategy[Point]:
    """Draws a point on the sheet at least `margin` meters inside its edges."""
    x0, y0, x1, y1 = inner_box(sheet, margin)
    xs = st.floats(min_value=x0, max_value=x1, allow_nan=False, allow_subnormal=False)
    ys = st.floats(min_value=y0, max_value=y1, allow_nan=False, allow_subnormal=False)
    return st.tuples(xs, ys)


def headings() -> st.SearchStrategy[float]:
    """Draws a facing in degrees in [0, 360)."""
    return st.floats(min_value=0.0, max_value=FULL_TURN, exclude_max=True, allow_subnormal=False)
