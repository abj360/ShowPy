#!/usr/bin/env python3

"""
test_fuzz_coords.py --- fuzzes the coordinate parser and writer; only ShowPyError may escape

Contains:
    test_fuzz_from_coords_text(): reads arbitrary text, long or short style
    test_fuzz_from_coords_unicode_digits(): reads text built from digits of every script
    test_fuzz_to_coords_floats(): words arbitrary floats, NaN, infinities and huge values included
    test_fuzz_to_coords_systems(): words a point under any coordinate system, valid or not
    test_fuzz_round_trip(): reads back what to_coords wrote, within the printed precision
"""

import unicodedata

from hypothesis import given
from hypothesis import strategies as st

from showpy.core.errors import ShowPyError
from showpy.examples import two_set_show
from showpy.sheet.coords import from_coords, to_coords
from showpy.sheet.models import CoordinateKind, CoordinateSystem
from tests.helpers import only_showpy_errors

SHOW = two_set_show()
ROUND_TRIP_TOL = 0.0051
FLOATS = st.floats(allow_nan=True, allow_infinity=True, allow_subnormal=True)
PRECISIONS = st.one_of(
    FLOATS,
    st.sampled_from([0.0, -0.0, -1.0, 1e-320, 5e-324, 1e300, 1.7976931348623157e308, 0.01, 0.5]),
)
LABEL_KEYS = [
    "left",
    "right",
    "up",
    "down",
    "left_short",
    "right_short",
    "up_short",
    "down_short",
    "across_ref",
    "depth_ref",
]
LABELS = st.dictionaries(st.sampled_from(LABEL_KEYS), st.text(max_size=12), max_size=6)
UNITS = st.sampled_from(["m", "ft", "yd", "cm", "bogus", ""])
SYSTEMS = st.builds(
    CoordinateSystem.model_construct,
    kind=st.sampled_from(CoordinateKind),
    origin=st.one_of(
        st.tuples(FLOATS, FLOATS),
        st.sampled_from(["center", "front edge", "nowhere", ""]),
    ),
    audience_side=st.sampled_from(["front", "back", "left", "right"]),
    units=UNITS,
    precision=PRECISIONS,
    labels=LABELS,
)
DIGIT_CHARS = [chr(c) for c in range(0x110000) if unicodedata.category(chr(c)) == "Nd"][:400]
UNICODE_TEXT = st.lists(
    st.sampled_from([*DIGIT_CHARS, ".", " ", ",", "R", "L", "U", "D", "m", " of "]),
    max_size=40,
).map("".join)


@given(text=st.one_of(st.text(), st.text(min_size=2000, max_size=6000)))
def test_fuzz_from_coords_text(text: str) -> None:
    """Reads arbitrary text, long or short style."""
    only_showpy_errors(lambda: from_coords(SHOW, text), text)


@given(text=UNICODE_TEXT)
def test_fuzz_from_coords_unicode_digits(text: str) -> None:
    """Reads text built from digits of every script."""
    only_showpy_errors(lambda: from_coords(SHOW, text), text)


@given(x=FLOATS, y=FLOATS, style=st.sampled_from(["long", "short"]))
def test_fuzz_to_coords_floats(x: float, y: float, style: str) -> None:
    """Words arbitrary floats, NaN, infinities and huge values included."""
    only_showpy_errors(lambda: to_coords(SHOW, (x, y), style), (x, y))  # type: ignore[arg-type]


@given(system=SYSTEMS, x=FLOATS, y=FLOATS, style=st.sampled_from(["long", "short"]))
def test_fuzz_to_coords_systems(system: CoordinateSystem, x: float, y: float, style: str) -> None:
    """Words a point under any coordinate system, valid or not."""
    show = SHOW.model_copy(update={"coordinates": system})

    def run() -> None:
        to_coords(show, (x, y), style)  # type: ignore[arg-type]

    only_showpy_errors(run, (system, x, y))


@given(
    x=st.floats(-1e4, 1e4),
    y=st.floats(-1e4, 1e4),
    style=st.sampled_from(["long", "short"]),
)
def test_fuzz_round_trip(x: float, y: float, style: str) -> None:
    """Reads back what to_coords wrote, within the printed precision."""
    try:
        text = to_coords(SHOW, (x, y), style)  # type: ignore[arg-type]
        point = from_coords(SHOW, text)
    except ShowPyError:
        return
    assert abs(point[0] - x) <= ROUND_TRIP_TOL
    assert abs(point[1] - y) <= ROUND_TRIP_TOL
