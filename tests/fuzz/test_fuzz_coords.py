#!/usr/bin/env python3

"""
test_fuzz_coords.py --- fuzzes the coordinate parser and writer; only ShowPyError may escape

Contains:
    test_fuzz_from_coords_text(): reads arbitrary text, long or short style
    test_fuzz_from_coords_unicode_digits(): reads text built from digits of every script
    test_fuzz_from_coords_long_numbers(): reads digit runs far beyond float range
    test_fuzz_from_coords_huge_origin(): reads huge numbers from a huge finite origin
    test_fuzz_to_coords_huge_origin(): words huge points from a huge finite origin
    test_fuzz_from_coords_systems(): reads text under any coordinate system, valid or not
    test_fuzz_to_coords_floats(): words arbitrary floats, NaN, infinities and huge values included
    test_fuzz_to_coords_systems(): words a point under any coordinate system, valid or not
    test_fuzz_round_trip(): reads back what to_coords wrote, within the printed precision
"""

import math
import unicodedata

import pytest
from hypothesis import given
from hypothesis import strategies as st

from showpy.core.errors import ShowPyError
from showpy.examples import two_set_show
from showpy.sheet.coords import from_coords, to_coords
from showpy.sheet.models import CoordinateKind, CoordinateSystem
from showpy.show.models import Show
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
    kind=st.one_of(st.sampled_from(CoordinateKind), st.just("x"), st.none()),
    origin=st.one_of(
        st.tuples(FLOATS, FLOATS),
        st.tuples(FLOATS),
        st.tuples(FLOATS, FLOATS, FLOATS),
        st.sampled_from(["center", "front edge", "nowhere", ""]),
    ),
    audience_side=st.sampled_from(["front", "back", "left", "right", "x", "", "FRONT"]),
    units=UNITS,
    precision=PRECISIONS,
    labels=LABELS,
)
DIGIT_CHARS = [chr(c) for c in range(0x110000) if unicodedata.category(chr(c)) == "Nd"][:400]
UNICODE_TEXT = st.lists(
    st.sampled_from([*DIGIT_CHARS, ".", " ", ",", "R", "L", "U", "D", "m", " of "]),
    max_size=40,
).map("".join)


def _read_finite(show: Show, text: str) -> None:
    point = from_coords(show, text)
    assert all(math.isfinite(axis) for axis in point), f"non-finite point {point} from {text!r}"


def _only_errors_or_finite(show: Show, text: str, sample: object) -> None:
    def run() -> None:
        try:
            _read_finite(show, text)
        except AssertionError as error:
            pytest.fail(str(error), pytrace=False)

    only_showpy_errors(run, sample)


DIGIT_RUNS = st.builds(
    lambda digit, count, tail: digit * count + tail,
    st.sampled_from(["9", "1", "0", "7"]),
    st.integers(300, 1200),
    st.sampled_from(["", ".5", ".0e5"]),
)
LONG_NUMBER_TEXT = st.one_of(
    DIGIT_RUNS.map(lambda number: f"R{number} U1"),
    DIGIT_RUNS.map(lambda number: f"{number} m right of center, 1 m upstage of front edge"),
    DIGIT_RUNS.map(lambda number: f"1 m right of center, {number} ft upstage of front edge"),
)


HUGE_ORIGIN_SHOW = SHOW.model_copy(
    update={"coordinates": CoordinateSystem(origin=(1.7e308, -1.7e308))}
)


@given(
    digits=st.integers(300, 309),
    unit_code=st.sampled_from(["R", "L", "U", "D"]),
    sign_flip=st.booleans(),
)
def test_fuzz_from_coords_huge_origin(digits: int, unit_code: str, sign_flip: bool) -> None:
    """Reads large numbers from a huge finite origin without returning an infinite point."""
    text = f"{unit_code}{'9' * digits} {'U' if unit_code in 'RL' else 'R'}1"
    show = HUGE_ORIGIN_SHOW if sign_flip else SHOW
    _only_errors_or_finite(show, text, text)


@given(x=st.sampled_from([1.7e308, -1.7e308, 1e300]), y=st.sampled_from([1.7e308, -1.7e308, 0.0]))
def test_fuzz_to_coords_huge_origin(x: float, y: float) -> None:
    """Words huge finite points from a huge finite origin; only ShowPyError may escape."""
    only_showpy_errors(lambda: to_coords(HUGE_ORIGIN_SHOW, (x, y)), (x, y))


@given(text=st.one_of(st.text(), st.text(min_size=2000, max_size=6000)))
def test_fuzz_from_coords_text(text: str) -> None:
    """Reads arbitrary text, long or short style."""
    _only_errors_or_finite(SHOW, text, text)


@given(text=UNICODE_TEXT)
def test_fuzz_from_coords_unicode_digits(text: str) -> None:
    """Reads text built from digits of every script."""
    _only_errors_or_finite(SHOW, text, text)


@given(text=LONG_NUMBER_TEXT)
def test_fuzz_from_coords_long_numbers(text: str) -> None:
    """Reads digit runs far beyond float range."""
    _only_errors_or_finite(SHOW, text, text)


@given(system=SYSTEMS, text=st.one_of(st.sampled_from(["R1 U1", "L2.5 D3"]), st.text()))
def test_fuzz_from_coords_systems(system: CoordinateSystem, text: str) -> None:
    """Reads text under any coordinate system, valid or not."""
    show = SHOW.model_copy(update={"coordinates": system})
    _only_errors_or_finite(show, text, (system, text))


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
