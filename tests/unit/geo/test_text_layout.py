#!/usr/bin/env python3

"""
test_text_layout.py --- tests multi-line text layout with a per-character font fallback chain

Contains:
    test_*(): one behavior of showpy.geo.text_layout.layout_text each
"""

import math
from collections.abc import Iterable

import numpy as np
import pytest

from showpy.core.errors import (
    FontError,
    GeometryError,
    GlyphError,
    MissingFontError,
    SchemaError,
)
from showpy.core.types import Points
from showpy.core.util import approx_equal
from showpy.geo.curves import Polyline, SvgCurve
from showpy.geo.fonts import FontRegistry, bundled_fonts, register_font
from showpy.geo.models import Font, LaidGlyph, TextAlign, TextAnchor
from showpy.geo.text_layout import MAX_LAYOUT_CHARS, MAX_LAYOUT_EXTENT_M, layout_text
from tests.fixtures.fonts import TINY, tiny_bytes

TOL = 1e-12
CURVE_TOL = 1e-6
SIMPLEX = ["simplex"]
RELIEF = ["relief-singleline"]
TINY_REGISTRY, TINY_URI = register_font(bundled_fonts(), tiny_bytes())
SIMPLEX_ADVANCE = 5.0
SIMPLEX_H_INK = 4.0
ANCHORS: tuple[TextAnchor, ...] = ("baseline", "middle", "top")
SWEEP_HEIGHTS = (1.0, 4e5, 1e300, 1e305)
SWEEP_ORIGINS = ((0.0, 0.0), (1.7e308, 1.7e308), (-1.7e308, -1.7e308))
LARGEST_ACCEPTED_HEIGHT = 4e5
ALIGN_SHARES: tuple[tuple[TextAlign, float], ...] = (("center", 0.5), ("right", 1.0))


def _points(glyphs: Iterable[LaidGlyph]) -> Points:
    return np.vstack(
        [stroke.to_polyline(CURVE_TOL) for glyph in glyphs for stroke in glyph.strokes]
    )


def _unchecked_registry(**changes: object) -> FontRegistry:
    font = Font.model_validate({**TINY, **changes})
    return FontRegistry(fonts={TINY_URI: font}, sources={}, names={"tiny": TINY_URI})


def _line(glyphs: Iterable[LaidGlyph], line: int) -> list[LaidGlyph]:
    return [glyph for glyph in glyphs if glyph.line == line]


def test_layout_puts_baseline_left_on_at() -> None:
    """Puts the first glyph's baseline-left corner on `at`, scaled to the cap height."""
    points = _points(layout_text("H", SIMPLEX, 2.0, (1.0, 5.0)))
    assert approx_equal(points[:, 0].min(), 1.0, tol=TOL)
    assert approx_equal(points[:, 1].min(), 5.0, tol=TOL)
    assert approx_equal(points[:, 1].max(), 7.0, tol=TOL)


def test_layout_matches_cap_heights_across_fonts() -> None:
    """Scales each glyph to its own font's cap height and advances by its own font's advance."""
    height, at = 2.0, (1.0, 5.0)
    glyph_i, glyph_h = layout_text(
        "IH", ["tiny", "simplex"], height, at, font_registry=TINY_REGISTRY
    )
    assert glyph_i.font == TINY_URI
    assert glyph_h.font == bundled_fonts().resolve("simplex")
    assert approx_equal(_points([glyph_i])[:, 1].max(), at[1] + height, tol=TOL)
    assert approx_equal(_points([glyph_h])[:, 1].max(), at[1] + height, tol=TOL)
    tiny_advance = TINY["glyphs"]["I"]["advance"]
    tiny_scale = height / TINY["cap_height"]
    assert approx_equal(_points([glyph_h])[:, 0].min(), at[0] + tiny_advance * tiny_scale, tol=TOL)


def test_layout_falls_back_per_character() -> None:
    """Takes each character from the first font in the chain that has it."""
    glyphs = layout_text("a!", ["simplex", "hershey-roman"], 1.0, (0.0, 0.0))
    registry = bundled_fonts()
    assert [glyph.font for glyph in glyphs] == [
        registry.resolve("simplex"),
        registry.resolve("hershey-roman"),
    ]


def test_layout_accepts_font_uris_in_the_chain() -> None:
    """Resolves a chain entry given as a URI exactly like one given as a name."""
    uri = bundled_fonts().resolve("simplex")
    assert layout_text("H", [uri], 1.0, (0.0, 0.0)) == layout_text("H", SIMPLEX, 1.0, (0.0, 0.0))


def test_layout_reports_a_missing_glyph() -> None:
    """Names the character, its code point and the chain, and points to register_font()."""
    with pytest.raises(GlyphError) as caught:
        layout_text("A☃", SIMPLEX, 1.0, (0.0, 0.0))
    error = caught.value
    assert error.code == "MISSING_GLYPH"
    assert error.context["char"] == "☃"
    assert error.context["codepoint"] == "U+2603"
    assert error.context["fonts"] == ["simplex"]
    assert "U+2603" in error.hint
    assert "register_font()" in error.hint


def test_layout_reports_an_unknown_font() -> None:
    """Raises MissingFontError for a chain entry the registry does not hold."""
    with pytest.raises(MissingFontError):
        layout_text("A", ["no-such-font"], 1.0, (0.0, 0.0))


def test_layout_normalizes_decomposed_accents() -> None:
    """Composes e + U+0301 into one precomposed glyph."""
    glyphs = layout_text("é", RELIEF, 1.0, (0.0, 0.0))
    assert [glyph.char for glyph in glyphs] == ["é"]


def test_layout_treats_crlf_as_one_line_break() -> None:
    """Breaks lines once on \\r\\n and on a lone \\r, as on \\n."""
    lines = [glyph.line for glyph in layout_text("A\nB", SIMPLEX, 1.0, (0.0, 0.0))]
    assert lines == [0, 1]
    for text in ("A\r\nB", "A\rB"):
        assert [glyph.line for glyph in layout_text(text, SIMPLEX, 1.0, (0.0, 0.0))] == lines


def test_layout_gives_spaces_no_strokes() -> None:
    """Lays a space out as a glyph with no strokes and allows a whitespace-only line."""
    glyphs = layout_text("H\n ", SIMPLEX, 1.0, (0.0, 0.0))
    assert [(glyph.char, glyph.line, glyph.strokes) for glyph in glyphs[1:]] == [(" ", 1, ())]


def test_layout_aligns_each_line() -> None:
    """Centers or right-aligns each line's advance box on `at`."""
    for align, share in ALIGN_SHARES:
        glyphs = layout_text("HH\nH", SIMPLEX, 6.0, (0.0, 0.0), align=align)
        for line in (0, 1):
            xs = _points(_line(glyphs, line))[:, 0]
            start, end = xs.min(), xs.max() + SIMPLEX_ADVANCE - SIMPLEX_H_INK
            assert approx_equal(start + share * (end - start), 0.0, tol=TOL)


def test_layout_anchor_top_and_middle() -> None:
    """Puts the cap line on `at` for top, and the cap-to-last-baseline midpoint for middle."""
    top = _points(layout_text("H", SIMPLEX, 6.0, (0.0, 2.0), anchor="top"))
    assert approx_equal(top[:, 1].max(), 2.0, tol=TOL)
    middle = layout_text("H\nH", SIMPLEX, 6.0, (0.0, 2.0), anchor="middle")
    cap_line = _points(middle)[:, 1].max()
    last_baseline = _points(_line(middle, 1))[:, 1].min()
    assert approx_equal((cap_line + last_baseline) / 2, 2.0, tol=TOL)


def test_layout_spacing_uses_the_first_fonts_em() -> None:
    """Measures letter and line spacing in the first font's em, 6 m for simplex at height 6."""
    _, second = layout_text("HH", SIMPLEX, 6.0, (0.0, 0.0), letter_spacing=0.5)
    assert approx_equal(_points([second])[:, 0].min(), 8.0, tol=TOL)
    glyphs = layout_text("H\nH", SIMPLEX, 6.0, (0.0, 0.0), line_spacing=2.0)
    assert approx_equal(_points(_line(glyphs, 1))[:, 1].min(), -12.0, tol=TOL)


def test_layout_rotation_turns_counter_clockwise() -> None:
    """Turns the text counter-clockwise about `at`."""
    points = _points(layout_text("H", SIMPLEX, 6.0, (0.0, 0.0), rotation=90.0))
    assert points[:, 0].min() >= -6.0 - TOL
    assert points[:, 0].max() <= TOL
    assert points[:, 1].min() >= -TOL
    assert points[:, 1].max() <= 4.0 + TOL


def test_layout_curved_strokes_are_svg_curves() -> None:
    """Keeps curved strokes as mapped SvgCurves and straight ones as explicit Polylines."""
    (relief_o,) = layout_text("O", RELIEF, 6.8, (1.0, 0.0))
    (curve,) = relief_o.strokes
    assert isinstance(curve, SvgCurve)
    assert curve.closed
    assert approx_equal(curve.point_at(0.0), (1.0 + 3.57, 6.8), tol=TOL)
    (simplex_o,) = layout_text("O", SIMPLEX, 6.0, (0.0, 0.0))
    (polyline,) = simplex_o.strokes
    assert isinstance(polyline, Polyline)
    assert polyline.closed
    assert polyline.points[0] != polyline.points[-1]
    assert len(polyline.points) == 8


def test_layout_rejects_bad_sizes() -> None:
    """Rejects a non-positive or non-finite size and an empty font chain."""
    for kwargs in (
        {"height": 0.0},
        {"height": math.nan},
        {"line_spacing": 0.0},
        {"letter_spacing": math.inf},
        {"rotation": math.nan},
        {"at": (math.nan, 0.0)},
    ):
        call = {"height": 1.0, "at": (0.0, 0.0), **kwargs}
        with pytest.raises(GeometryError):
            layout_text("H", SIMPLEX, **call)  # type: ignore[arg-type]
    with pytest.raises(SchemaError):
        layout_text("H", [], 1.0, (0.0, 0.0))


def test_layout_caps_the_text_length() -> None:
    """Rejects text longer than MAX_LAYOUT_CHARS before laying any glyph out."""
    assert len(layout_text("H" * MAX_LAYOUT_CHARS, SIMPLEX, 1.0, (0.0, 0.0))) == MAX_LAYOUT_CHARS
    with pytest.raises(SchemaError) as caught:
        layout_text("☃" * (MAX_LAYOUT_CHARS + 1), SIMPLEX, 1.0, (0.0, 0.0))
    assert caught.value.context["cap"] == MAX_LAYOUT_CHARS


def test_layout_round_trips_through_json() -> None:
    """Dumps and reloads laid glyphs, keeping each stroke's curve kind."""
    glyphs = layout_text("Oa", RELIEF, 1.0, (0.0, 0.0))
    assert tuple(LaidGlyph.model_validate_json(g.model_dump_json()) for g in glyphs) == glyphs


def test_layout_rejects_a_font_without_a_cap_height() -> None:
    """Raises FontError for an unchecked font whose cap height falls back to a zero ascender."""
    registry = _unchecked_registry(cap_height=None, ascender=0)
    with pytest.raises(FontError):
        layout_text("I", ["tiny"], 1.0, (0.0, 0.0), font_registry=registry)


def test_layout_rejects_a_stroke_with_two_subpaths() -> None:
    """Raises FontError for an unchecked font whose stroke holds two subpaths."""
    glyphs = {"I": {"advance": 4, "strokes": ["M0 0 L1 0 M2 0 L3 0"]}}
    with pytest.raises(FontError):
        layout_text(
            "I", ["tiny"], 1.0, (0.0, 0.0), font_registry=_unchecked_registry(glyphs=glyphs)
        )


def test_regression_huge_finite_values_raise_showpy_error() -> None:
    """Raises GeometryError, never a numpy RuntimeWarning, when finite inputs overflow."""
    huge = (1.7e308, 1.7e308)
    calls = (
        {"text": "HH", "height": 1e308, "at": huge, "rotation": 45.0},
        {"text": "OO", "fonts": RELIEF, "height": 1e308, "at": huge},
        {"text": "H\nH", "height": 1e308, "at": (0.0, 0.0), "line_spacing": 1e308},
        {"text": "HHH", "height": 1.0, "at": (0.0, 0.0), "letter_spacing": 1.7e308},
    )
    for call in calls:
        arguments = {"fonts": SIMPLEX, **call}
        with pytest.raises(GeometryError):
            layout_text(**arguments)  # type: ignore[arg-type]


def _evaluate(glyphs: Iterable[LaidGlyph]) -> None:
    for glyph in glyphs:
        for stroke in glyph.strokes:
            half = stroke.length() / 2
            assert np.isfinite(stroke.point_at(half)).all()
            assert np.isfinite(stroke.tangent_at(half)).all()


def test_regression_laid_text_evaluates_without_overflow() -> None:
    """Rejects text too large to evaluate; every stroke it returns evaluates without a warning."""
    accepted = []
    for height in SWEEP_HEIGHTS:
        for at in SWEEP_ORIGINS:
            for fonts in (SIMPLEX, RELIEF):
                try:
                    glyphs = layout_text("HO", fonts, height, at)
                except GeometryError:
                    continue
                _evaluate(glyphs)
                accepted.append((height, at))
    assert (LARGEST_ACCEPTED_HEIGHT, (0.0, 0.0)) in accepted
    assert (1.0, (0.0, 0.0)) in accepted


def test_layout_caps_the_laid_extent() -> None:
    """Accepts strokes up to MAX_LAYOUT_EXTENT_M from the origin and rejects any reaching past."""
    edge = MAX_LAYOUT_EXTENT_M - 10.0
    _evaluate(layout_text("HO", RELIEF, 1.0, (edge, -edge), rotation=30.0))
    too_far = (
        {"height": MAX_LAYOUT_EXTENT_M * 2},
        {"at": (MAX_LAYOUT_EXTENT_M * 2, 0.0)},
        {"at": (edge, 0.0), "height": 20.0},
        {"letter_spacing": MAX_LAYOUT_EXTENT_M},
        {"text": "H\nH", "line_spacing": MAX_LAYOUT_EXTENT_M * 2},
    )
    for changes in too_far:
        call = {"text": "HH", "fonts": SIMPLEX, "height": 1.0, "at": (0.0, 0.0), **changes}
        with pytest.raises(GeometryError):
            layout_text(**call)  # type: ignore[arg-type]


def test_layout_bounds_quadratic_and_arc_strokes_by_their_hulls() -> None:
    """Bounds Q and A strokes by their hulls, so end points inside the extent are not enough."""
    bulges = {
        "Q": {"advance": 10, "strokes": ["M0 0 Q5 10 10 0"]},
        "A": {"advance": 10, "strokes": ["M0 0 A5 5 0 0 0 10 0"]},
    }
    registry, _ = register_font(bundled_fonts(), tiny_bytes(glyphs=bulges))
    near_top = (0.0, MAX_LAYOUT_EXTENT_M - 1.0)
    for char in bulges:
        _evaluate(layout_text(char, ["tiny"], 10.0, (0.0, 0.0), font_registry=registry))
        with pytest.raises(GeometryError):
            layout_text(char, ["tiny"], 10.0, near_top, font_registry=registry)


def test_layout_drops_trailing_empty_lines() -> None:
    """Lays "H\\n" out exactly like "H" for every anchor; interior empty lines stay."""
    for anchor in ANCHORS:
        plain = layout_text("H", SIMPLEX, 1.0, (0.0, 0.0), anchor=anchor)
        for text in ("H\n", "H\n\n", "H\r\n"):
            assert layout_text(text, SIMPLEX, 1.0, (0.0, 0.0), anchor=anchor) == plain
    assert [glyph.line for glyph in layout_text("H\n\nH", SIMPLEX, 1.0, (0.0, 0.0))] == [0, 2]
