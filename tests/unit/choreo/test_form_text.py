#!/usr/bin/env python3

"""
test_form_text.py --- tests the text recipe and exact-count text slots on glyph strokes

Contains:
    test_*(): one behavior of showpy.choreo.form_text each
"""

from collections.abc import Iterator

import numpy as np
import pytest
from scipy.spatial import cKDTree

from showpy.choreo.form_text import (
    COINCIDENT_TOL_M,
    LEGIBLE_SPACING_SHARE,
    min_text_spots,
    text_recipe,
    text_slots,
    text_strokes,
)
from showpy.choreo.models import TextRecipe
from showpy.core.errors import GeometryError, LegibilityWarning, SchemaError, SpacingError
from showpy.core.types import Points
from showpy.core.util import approx_equal
from showpy.geo.fonts import FontRegistry, bundled_fonts, register_font
from showpy.geo.text_layout import MAX_LAYOUT_CHARS
from tests.fixtures.fonts import tiny_bytes

TOL = 1e-9
HEIGHT = 2.0
AT = (0.0, 10.0)
HI_MIN_SPOTS = 12
DENSE_HI_SPOTS = 36
LOOP_TEXT = "O0D8Q6B"
LOOP_SPOTS = 60
SPARSE_WARNING = r"^W: text spots are up to \d+\.\d\d m apart on 2\.00 m letters; raise n$"
ignore_legibility = pytest.mark.filterwarnings("ignore::showpy.core.errors.LegibilityWarning")


def recipe(text: str, *fonts: str) -> TextRecipe:
    """Builds a 2 m recipe at (0, 10) with fonts resolved through the bundled registry."""
    uris = tuple(bundled_fonts().resolve(font) for font in fonts or ("simplex",))
    return TextRecipe(text=text, fonts=uris, height=HEIGHT, at=AT)


def min_gap(points: Points) -> float:
    distances, _ = cKDTree(points).query(points, k=2)
    return float(distances[:, 1].min())


def geometry(**changes: object) -> dict[str, object]:
    simplex = bundled_fonts().resolve("simplex")
    return {"text": "HI", "fonts": [simplex], "height": HEIGHT, "at": list(AT), **changes}


def _tiny_registry(*strokes: str) -> tuple[FontRegistry, str]:
    glyphs = {"I": {"advance": 4, "strokes": list(strokes)}}
    return register_font(bundled_fonts(), tiny_bytes(glyphs=glyphs))


def _glyph_cases() -> Iterator[tuple[str, str]]:
    registry = bundled_fonts()
    for name, uri in sorted(registry.names.items()):
        for char, glyph in registry.get(uri).glyphs.items():
            if glyph.strokes:
                yield name, char


@ignore_legibility
@pytest.mark.parametrize("n", [12, 13, 37])
def test_text_slots_count_is_exact(n: int) -> None:
    points, tangents = text_slots(recipe("HI"), n)
    assert points.shape == (n, 2)
    assert tangents.shape == (n, 2)


@ignore_legibility
def test_text_slots_follow_reading_order() -> None:
    hi = recipe("HI")
    strokes = text_strokes(hi)
    points, _ = text_slots(hi, HI_MIN_SPOTS)
    i_left = min(float(stroke.to_polyline()[:, 0].min()) for stroke in strokes[3:])
    assert approx_equal(points[0], strokes[0].point_at(0.0), TOL)
    assert (points[-6:, 0] >= i_left - TOL).all()


@ignore_legibility
def test_text_slots_tangents_are_unit_and_follow_the_stroke() -> None:
    _, tangents = text_slots(recipe("HI"), HI_MIN_SPOTS)
    assert approx_equal(np.linalg.norm(tangents, axis=1), np.ones(HI_MIN_SPOTS), TOL)
    assert approx_equal(np.abs(tangents[0]), [0.0, 1.0], TOL)


@ignore_legibility
@pytest.mark.parametrize("font", ["simplex", "relief-singleline"])
def test_regression_closed_loops_never_double_a_spot(font: str) -> None:
    points, _ = text_slots(recipe(LOOP_TEXT, font), LOOP_SPOTS)
    assert min_gap(points) > COINCIDENT_TOL_M


@ignore_legibility
def test_regression_crossing_strokes_never_stack_spots() -> None:
    for name, char in _glyph_cases():
        glyph_recipe = recipe(char, name)
        least = min_text_spots(text_strokes(glyph_recipe))
        for n in (least, 3 * least):
            points, _ = text_slots(glyph_recipe, n)
            assert min_gap(points) >= COINCIDENT_TOL_M, (name, char, n)


@ignore_legibility
def test_regression_touching_strokes_skip_the_shared_point() -> None:
    registry, uri = _tiny_registry("M0 0 L0 10", "M0 0 L4 0")
    corner = TextRecipe(text="I", fonts=(uri,), height=HEIGHT, at=AT)
    points, _ = text_slots(corner, 4, font_registry=registry)
    assert approx_equal(points, [[0.0, 10.0], [0.0, 12.0], [0.4, 10.0], [0.8, 10.0]], TOL)


def test_too_few_spots_names_the_minimum() -> None:
    with pytest.raises(SpacingError) as caught:
        text_slots(recipe("HI"), 5)
    assert caught.value.code == "TOO_FEW_SPOTS"
    assert caught.value.context == {"n": 5, "min_n": HI_MIN_SPOTS, "text": "HI"}
    assert str(HI_MIN_SPOTS) in caught.value.hint


@pytest.mark.parametrize("n", [12.0, True, "12"])
def test_text_slots_reject_a_count_that_is_not_an_int(n: object) -> None:
    with pytest.raises(SpacingError, match="whole number"):
        text_slots(recipe("HI"), n)  # type: ignore[arg-type]


def test_whitespace_only_text_is_rejected() -> None:
    with pytest.raises(SchemaError) as caught:
        text_slots(recipe(" \n "), HI_MIN_SPOTS)
    assert "visible character" in caught.value.hint


def test_sparse_text_warns_about_legibility() -> None:
    relief_i = recipe("I", "relief-singleline")
    least = min_text_spots(text_strokes(relief_i))
    with pytest.warns(LegibilityWarning, match=SPARSE_WARNING) as record:
        text_slots(relief_i, least)
    assert record[0].filename == __file__


def test_dense_text_does_not_warn() -> None:
    text_slots(recipe("HI"), DENSE_HI_SPOTS)


def test_short_strokes_do_not_trigger_the_warning() -> None:
    period = recipe(".", "relief-singleline")
    strokes = text_strokes(period)
    assert max(stroke.length() for stroke in strokes) < LEGIBLE_SPACING_SHARE * HEIGHT
    text_slots(period, min_text_spots(strokes))


def test_text_slots_are_byte_identical_across_calls() -> None:
    first = text_slots(recipe("HI\nOB", "relief-singleline"), 80)
    second = text_slots(recipe("HI\nOB", "relief-singleline"), 80)
    assert first[0].tobytes() == second[0].tobytes()
    assert first[1].tobytes() == second[1].tobytes()


def test_min_text_spots_counts_two_per_open_and_three_per_closed_stroke() -> None:
    assert min_text_spots(text_strokes(recipe("HI"))) == HI_MIN_SPOTS
    assert min_text_spots(text_strokes(recipe("O"))) == 3


def test_retraced_closed_stroke_raises_coincident_slots() -> None:
    registry, uri = _tiny_registry("M2 0 L2 10 Z")
    retraced = TextRecipe(text="I", fonts=(uri,), height=HEIGHT, at=AT)
    with pytest.raises(GeometryError) as caught:
        text_slots(retraced, 3, font_registry=registry)
    assert caught.value.code == "COINCIDENT_SLOTS"
    assert caught.value.context["char"] == "I"
    assert caught.value.hint == "change n or the text"


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("height", 0),
        ("fonts", []),
        ("fonts", ["relief-singleline"]),
        ("mode", "outline"),
        ("align", "justify"),
        ("text", ""),
        ("text", "x" * (MAX_LAYOUT_CHARS + 1)),
    ],
)
def test_text_recipe_rejects_bad_fields(field: str, value: object) -> None:
    with pytest.raises(SchemaError) as caught:
        text_recipe(geometry(**{field: value}))
    assert caught.value.context["field"] == field
    assert caught.value.context["errors"]
    assert isinstance(caught.value.__cause__, SchemaError)


@pytest.mark.parametrize(
    ("field", "allowed"),
    [
        ("align", "left, center, right"),
        ("anchor", "baseline, middle, top"),
        ("fonts", "bundled_fonts().resolve("),
        ("mode", "centerline"),
    ],
)
def test_text_recipe_hint_names_the_allowed_values(field: str, allowed: str) -> None:
    with pytest.raises(SchemaError) as caught:
        text_recipe(geometry(**{field: "nope"}))
    assert allowed in caught.value.hint


def test_text_recipe_ignores_shared_keys() -> None:
    parsed = text_recipe(geometry(n=12, transform=[[1, 0, 0], [0, 1, 0], [0, 0, 1]]))
    assert parsed == recipe("HI")


def test_text_recipe_rejects_unknown_keys() -> None:
    with pytest.raises(SchemaError) as caught:
        text_recipe(geometry(spacing=1.0))
    assert caught.value.context["field"] == "spacing"
    assert "text" in caught.value.hint
