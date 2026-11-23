#!/usr/bin/env python3

"""
cast.py --- strategies that add groups and spots to a show through the cast builders

Contains:
    GROUP_KEYS: group keys the strategies use, with their label prefixes
    lines_on(): draws a horizontal line segment that lies on the sheet
    with_cast(): adds one to three groups whose spots stand along lines in set "1"
"""

from hypothesis import strategies as st

from e8s.cast.models import Group, Shape
from e8s.cast.spots import add_group, add_spots_along, set_shape
from e8s.geo.curves import Segment
from e8s.geo.shapes import line
from e8s.sheet.models import Sheet
from e8s.show.models import Show
from tests.strategies.sheet import inner_box

GROUP_KEYS = {"brass": "B", "flags": "F", "drums": "D"}
FIRST_SET = "1"
MIN_LINE_M = 2.0
MIN_RADIUS_M = 0.2
MAX_RADIUS_M = 0.6
MAX_RECT_M = 1.5


@st.composite
def lines_on(draw: st.DrawFn, sheet: Sheet) -> Segment:
    """Draws a horizontal segment at least MIN_LINE_M long that lies inside the sheet."""
    x0, y0, x1, y1 = inner_box(sheet)
    y = draw(st.floats(min_value=y0, max_value=y1, allow_nan=False))
    start = draw(st.floats(min_value=x0, max_value=x1 - MIN_LINE_M, allow_nan=False))
    length = draw(st.floats(min_value=MIN_LINE_M, max_value=x1 - start, allow_nan=False))
    return line((start, y), (start + length, y))


@st.composite
def _rectangle(draw: st.DrawFn) -> Shape:
    sizes = st.floats(min_value=MIN_RADIUS_M, max_value=MAX_RECT_M, allow_nan=False)
    return Shape(kind="rectangle", width_m=draw(sizes), depth_m=draw(sizes))


@st.composite
def with_cast(draw: st.DrawFn, show: Show, max_spots: int) -> Show:
    """Adds one to three groups to `show`, their spots spaced along lines in set "1".

    Args:
        draw: Hypothesis draw function.
        show: Show with an empty cast and a set "1".
        max_spots: Most spots added across all groups.
    """
    keys = list(GROUP_KEYS)[: draw(st.integers(1, min(len(GROUP_KEYS), max_spots)))]
    budget = max_spots - len(keys)
    radii = st.floats(min_value=MIN_RADIUS_M, max_value=MAX_RADIUS_M, allow_nan=False)
    for key in keys:
        extra = draw(st.integers(0, budget))
        budget -= extra
        group = Group(key=key, label_prefix=GROUP_KEYS[key], default_radius=draw(radii))
        show = add_group(show, group)
        show = add_spots_along(show, draw(lines_on(show.sheet)), 1 + extra, key, FIRST_SET)
    if draw(st.booleans()):
        ids = draw(st.lists(st.sampled_from(show.cast.ids()), min_size=1, unique=True))
        show = set_shape(show, ids, draw(_rectangle()))
    return show
