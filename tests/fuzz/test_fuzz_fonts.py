#!/usr/bin/env python3

"""
test_fuzz_fonts.py --- fuzzes font loading and text layout with arbitrary bytes, font dicts and text

Contains:
    FONT_DICTS: draws font-shaped dicts with valid and hostile fields
    test_fuzz_load_font_raw_bytes: raises only ShowPyError for arbitrary bytes
    test_fuzz_load_font_font_shaped_json: raises only ShowPyError for font-shaped JSON
    test_fuzz_register_font_font_shaped_json: raises only ShowPyError and never changes the
        registry it extends
    test_fuzz_load_font_glyph_piece_cap: raises only ShowPyError for glyphs near the piece cap,
        split over several strokes, and accepts none over the glyph or stroke cap
    test_fuzz_layout_text_piece_cap: raises only ShowPyError laying out heavy glyphs that
        normalization doubles, and lays out none over the piece cap
    test_fuzz_import_fonts_arbitrary_documents: raises only ShowPyError for JSON-like documents
    test_fuzz_import_fonts_corrupted_base64: raises only ShowPyError for a real font entry with
        corrupted base64, a wrong URI or flipped bytes, and never registers a mismatched font
    test_fuzz_layout_text_arbitrary_text: raises only ShowPyError for arbitrary Unicode text laid
        out with the bundled fallback chain, then evaluated
    test_fuzz_layout_text_extreme_numbers: raises only ShowPyError for any finite height,
        position, spacing and rotation, laying out and evaluating every stroke
"""

import base64
import json
from collections.abc import Iterable
from typing import Any

from hypothesis import given, settings
from hypothesis import strategies as st

from showpy.core.errors import FontError, SchemaError, ShowPyError
from showpy.core.util import asset_uri
from showpy.export.data import FONTS_KEY, import_fonts
from showpy.geo.fonts import (
    MAX_GLYPH_PIECES,
    MAX_STROKE_PIECES,
    FontRegistry,
    bundled_fonts,
    load_font,
    register_font,
)
from showpy.geo.models import LaidGlyph
from showpy.geo.text_layout import MAX_LAYOUT_CHARS, MAX_LAYOUT_PIECES, layout_text
from tests.fixtures.fonts import tiny_bytes
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
JSON_LEAVES = st.one_of(
    st.none(), st.booleans(), st.integers(), st.floats(allow_nan=False), st.text(max_size=30)
)
JSON_VALUES = st.recursive(
    JSON_LEAVES,
    lambda inner: st.one_of(
        st.lists(inner, max_size=4), st.dictionaries(st.text(max_size=70), inner, max_size=4)
    ),
    max_leaves=12,
)
TINY_BYTES = tiny_bytes()
NEAR_CAP = 3
# U+0344 normalizes to U+0308 U+0301, so each character lays out two glyphs.
EXPANDING = "\u0344"
HEAVY_EXAMPLES = 40
TINY_URI = asset_uri(TINY_BYTES)


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


def _zigzag(pieces: int) -> str:
    return "M0 0 " + " ".join(f"L{i % 2} {i + 1}" for i in range(pieces))


def _loads_only_under_the_caps(data: bytes, pieces: int, longest: int) -> None:
    legal = pieces <= MAX_GLYPH_PIECES and longest <= MAX_STROKE_PIECES
    try:
        load_font(data)
    except FontError:
        assert not legal
        return
    assert legal


@given(
    st.integers(MAX_GLYPH_PIECES - NEAR_CAP, MAX_GLYPH_PIECES + NEAR_CAP),
    st.integers(1, 4),
)
def test_fuzz_load_font_glyph_piece_cap(pieces: int, strokes: int) -> None:
    """Raises only ShowPyError near the glyph piece cap, and accepts no glyph over a cap."""
    shares = [pieces // strokes + (index < pieces % strokes) for index in range(strokes)]
    glyphs = {"I": {"advance": 4, "strokes": [_zigzag(share) for share in shares]}}
    data = tiny_bytes(glyphs=glyphs)
    only_showpy_errors(
        lambda: _loads_only_under_the_caps(data, pieces, max(shares)), (pieces, strokes)
    )


def _lays_only_under_the_cap(text: str, registry: FontRegistry, pieces: int) -> None:
    try:
        layout_text(text, ["tiny"], 1.0, (0.0, 0.0), font_registry=registry)
    except SchemaError:
        assert pieces > MAX_LAYOUT_PIECES
        return
    assert pieces <= MAX_LAYOUT_PIECES


@settings(max_examples=HEAVY_EXAMPLES)
@given(st.integers(1, MAX_STROKE_PIECES), st.integers(-NEAR_CAP, NEAR_CAP))
def test_fuzz_layout_text_piece_cap(pieces: int, offset: int) -> None:
    """Raises only ShowPyError for heavy text normalization doubles; lays none over the cap."""
    heavy = {"advance": 4, "strokes": [_zigzag(pieces)]}
    registry, _ = register_font(
        bundled_fonts(), tiny_bytes(glyphs={"\u0308": heavy, "\u0301": heavy})
    )
    copies = min(MAX_LAYOUT_CHARS, max(1, MAX_LAYOUT_PIECES // (2 * pieces) + offset))
    laid = 2 * copies * pieces
    only_showpy_errors(
        lambda: _lays_only_under_the_cap(EXPANDING * copies, registry, laid), (pieces, copies)
    )


@given(st.one_of(JSON_VALUES, st.dictionaries(st.sampled_from([FONTS_KEY, "x"]), JSON_VALUES)))
def test_fuzz_import_fonts_arbitrary_documents(document: Any) -> None:
    """Raises only ShowPyError for any JSON-like document."""
    only_showpy_errors(lambda: import_fonts(document, font_registry=EMPTY), document)


def _corrupt(draw: st.DrawFn) -> dict[str, Any]:
    encoded = list(base64.b64encode(TINY_BYTES).decode())
    for _ in range(draw(st.integers(0, 3))):
        position = draw(st.integers(0, len(encoded)))
        encoded.insert(position, draw(st.sampled_from(list("=!\n é+/AZ"))))
    uri = draw(st.sampled_from([TINY_URI, asset_uri(b"other"), "asset:sha256:", ""]))
    return {FONTS_KEY: {uri: "".join(encoded)}}


@given(st.composite(_corrupt)())
def test_fuzz_import_fonts_corrupted_base64(document: dict[str, Any]) -> None:
    """Raises only ShowPyError, and registers a font only when its bytes hash to its URI."""
    ((uri, encoded),) = document[FONTS_KEY].items()
    try:
        registry = import_fonts(document, font_registry=EMPTY)
    except ShowPyError:
        return
    assert uri == TINY_URI
    assert registry.source(uri) == base64.b64decode(encoded, validate=True)
