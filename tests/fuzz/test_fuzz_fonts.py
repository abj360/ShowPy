#!/usr/bin/env python3

"""
test_fuzz_fonts.py --- fuzzes font loading and text layout with arbitrary bytes, font dicts and text

Contains:
    FONT_DICTS: draws font-shaped dicts with valid and hostile fields
    test_fuzz_load_font_raw_bytes: raises only ShowPyError for arbitrary bytes
    test_fuzz_load_font_font_shaped_json: raises only ShowPyError for font-shaped JSON
    test_fuzz_register_font_font_shaped_json: raises only ShowPyError and never changes the
        registry it extends
    test_fuzz_layout_text_arbitrary_text: raises only ShowPyError for arbitrary Unicode text laid
        out with the bundled fallback chain, then evaluated
    test_fuzz_layout_text_extreme_numbers: raises only ShowPyError for any finite height,
        position, spacing and rotation, laying out and evaluating every stroke
"""

import json
from collections.abc import Iterable
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from showpy.geo.fonts import FontRegistry, load_font, register_font
from showpy.geo.models import LaidGlyph
from showpy.geo.text_layout import MAX_LAYOUT_CHARS, layout_text
from tests.helpers import only_showpy_errors

EMPTY = FontRegistry(fonts={}, sources={}, names={})
BUNDLED_CHAIN = ["simplex", "hershey-roman", "relief-singleline"]
LAYOUT_TEXT = st.one_of(
    st.text(max_size=40),
    st.text(alphabet="AHOae\u0301 \r\n!☃", max_size=40),
    st.text(min_size=MAX_LAYOUT_CHARS - 2, max_size=MAX_LAYOUT_CHARS + 2),
)
FINITE = st.one_of(
    st.sampled_from([1.7976931348623157e308, 1e308, -1e308, 5e-324, 0.0, 1.0]),
    st.floats(allow_nan=False, allow_infinity=False),
)
STROKES = st.one_of(
    st.sampled_from(["M0 0 L1 1", "M0 0 L0 0", "M0 0", "M0 0 L1 0 M2 0 L3 0", "M0 0 L1 0 Z", ""]),
    st.text(alphabet="MLCQAZ0123456789 .,-e", max_size=40),
)
NUMBERS = st.one_of(
    st.integers(-5, 20), st.floats(allow_nan=True, allow_infinity=True), st.just(None), st.text()
)
GLYPHS = st.dictionaries(
    st.one_of(st.text(max_size=2), st.sampled_from(list("AHI .́"))),
    st.fixed_dictionaries(
        {"advance": NUMBERS, "strokes": st.lists(STROKES, max_size=4)},
        optional={"extra": st.integers()},
    ),
    max_size=4,
)
FONT_DICTS = st.fixed_dictionaries(
    {
        "format": st.sampled_from(["showpy.font.v1", "other"]),
        "name": st.one_of(st.sampled_from(["tiny", "Tiny", "a-b", "", "-x"]), st.text(max_size=70)),
        "family": st.text(max_size=8),
        "license": st.text(max_size=8),
        "source": st.text(max_size=8),
        "units_per_em": NUMBERS,
        "cap_height": NUMBERS,
        "ascender": NUMBERS,
        "descender": NUMBERS,
        "glyphs": st.one_of(GLYPHS, st.lists(st.integers()), st.none()),
    },
    optional={"unknown": st.integers()},
)


def _encode(document: dict[str, Any]) -> bytes:
    return json.dumps(document).encode()


def _evaluate(glyphs: Iterable[LaidGlyph]) -> None:
    for glyph in glyphs:
        for stroke in glyph.strokes:
            stroke.point_at(stroke.length() / 2)


@given(st.binary(max_size=300))
def test_fuzz_load_font_raw_bytes(data: bytes) -> None:
    """Raises only ShowPyError for arbitrary bytes."""
    only_showpy_errors(lambda: load_font(data), data)


@given(FONT_DICTS)
def test_fuzz_load_font_font_shaped_json(document: dict[str, Any]) -> None:
    """Raises only ShowPyError for JSON shaped like a font file."""
    data = _encode(document)
    only_showpy_errors(lambda: load_font(data), data)


@given(FONT_DICTS)
def test_fuzz_register_font_font_shaped_json(document: dict[str, Any]) -> None:
    """Raises only ShowPyError for font-shaped JSON and leaves the source registry empty."""
    data = _encode(document)
    only_showpy_errors(lambda: register_font(EMPTY, data), data)
    assert EMPTY.fonts == {}


@given(LAYOUT_TEXT)
def test_fuzz_layout_text_arbitrary_text(text: str) -> None:
    """Raises only ShowPyError laying out and evaluating arbitrary text in the bundled chain."""
    only_showpy_errors(lambda: _evaluate(layout_text(text, BUNDLED_CHAIN, 1.0, (0.0, 0.0))), text)


@given(
    st.text(alphabet="HOa\n ", min_size=1, max_size=6),
    FINITE,
    st.tuples(FINITE, FINITE),
    FINITE,
    FINITE,
    FINITE,
)
def test_fuzz_layout_text_extreme_numbers(
    text: str,
    height: float,
    at: tuple[float, float],
    letter_spacing: float,
    line_spacing: float,
    rotation: float,
) -> None:
    """Raises only ShowPyError laying out and evaluating text at any finite size and place."""
    only_showpy_errors(
        lambda: _evaluate(
            layout_text(
                text,
                BUNDLED_CHAIN,
                height,
                at,
                rotation=rotation,
                letter_spacing=letter_spacing,
                line_spacing=line_spacing,
            )
        ),
        (text, height, at, letter_spacing, line_spacing, rotation),
    )
