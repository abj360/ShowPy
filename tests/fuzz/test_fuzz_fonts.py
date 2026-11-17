#!/usr/bin/env python3

"""
test_fuzz_fonts.py --- fuzzes load_font and register_font with arbitrary bytes and font dicts

Contains:
    FONT_DICTS: draws font-shaped dicts with valid and hostile fields
    test_fuzz_load_font_raw_bytes: raises only ShowPyError for arbitrary bytes
    test_fuzz_load_font_font_shaped_json: raises only ShowPyError for font-shaped JSON
    test_fuzz_register_font_font_shaped_json: raises only ShowPyError and never changes the
        registry it extends
"""

import json
from typing import Any

from hypothesis import given
from hypothesis import strategies as st

from showpy.geo.fonts import FontRegistry, load_font, register_font
from tests.helpers import only_showpy_errors

EMPTY = FontRegistry(fonts={}, sources={}, names={})
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
