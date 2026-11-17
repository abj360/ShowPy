#!/usr/bin/env python3

"""
test_text_count.py --- tests text spot counts derived from a spacing between spots

Contains:
    test_*(): one behavior of showpy.choreo.text_count each
"""

import math

import pytest
from scipy.spatial import cKDTree

from showpy.choreo.form_text import (
    COINCIDENT_TOL_M,
    MAX_TEXT_SPOTS,
    text_slots,
    text_strokes,
)
from showpy.choreo.forms import text_form
from showpy.choreo.models import TextRecipe
from showpy.choreo.text_count import count_recipe, spacing_count
from showpy.core.errors import LegibilityWarning, SchemaError, SpacingError
from showpy.core.types import Points
from showpy.geo.fonts import bundled_fonts, register_font
from tests.fixtures.fonts import tiny_bytes

LETTER_HEIGHT = 6.0
L_LENGTH = 10.0
L_SPOTS_AT_ONE_METER = 11
LOOP_TEXT = "O0D8Q6B"
LOOP_HEIGHT = 2.0
LOOP_SPACING = 0.3
CLOSED_MINIMUM = 3
TINY_SPACING = 1e-6
DENSE_STROKES = tuple(f"M{x} 0 L{x} 10" for x in range(6))
DENSE_CHARS = 900
ignore_legibility = pytest.mark.filterwarnings("ignore::showpy.core.errors.LegibilityWarning")


def recipe(text: str, font: str = "simplex", height: float = LETTER_HEIGHT) -> TextRecipe:
    """Builds a recipe at the origin with the font resolved through the bundled registry."""
    return TextRecipe(text=text, fonts=(bundled_fonts().resolve(font),), height=height, at=(0, 0))


def spots(n: int) -> list[str]:
    return [f"S{i}" for i in range(n)]


def min_gap(points: Points) -> float:
    distances, _ = cKDTree(points).query(points, k=2)
    return float(distances[:, 1].min())


def test_spacing_count_follows_total_stroke_length() -> None:
    strokes = text_strokes(recipe("L"))
    assert sum(stroke.length() for stroke in strokes) == pytest.approx(L_LENGTH)
    assert spacing_count(strokes, 1.0) == L_SPOTS_AT_ONE_METER
    form = text_form(
        "l", spots(L_SPOTS_AT_ONE_METER), "L", fonts=["simplex"], height=LETTER_HEIGHT,
        at=(0, 0), spacing=1.0,
    )  # fmt: skip
    assert form.geometry["n"] == L_SPOTS_AT_ONE_METER
    assert form.geometry["spacing"] == 1.0


@ignore_legibility
def test_spacing_count_rounds_half_up() -> None:
    assert spacing_count(text_strokes(recipe("L")), 4.0) == math.floor(2.5 + 1 + 0.5)
    points, _ = text_slots(recipe("L"), spacing_count(text_strokes(recipe("L")), 4.0))
    assert len(points) == 4


def test_spacing_count_respects_stroke_minimums() -> None:
    assert spacing_count(text_strokes(recipe("O")), 100.0) == CLOSED_MINIMUM
    with pytest.warns(LegibilityWarning):
        text_slots(recipe("O"), CLOSED_MINIMUM)


def test_tiny_spacing_hits_the_spot_cap() -> None:
    with pytest.raises(SpacingError) as caught:
        spacing_count(text_strokes(recipe("L")), TINY_SPACING)
    assert caught.value.code == "TOO_MANY_SPOTS"
    assert caught.value.context["max_n"] == 10_000
    assert caught.value.context["n"] == 10_000_001
    assert caught.value.context["spacing"] == TINY_SPACING
    assert caught.value.hint == "use spacing >= 0.002 m"


@pytest.mark.parametrize("spacing", [1e-300, 5e-324])
def test_regression_spacing_count_never_overflows_on_tiny_spacing(spacing: float) -> None:
    with pytest.raises(SpacingError) as caught:
        spacing_count(text_strokes(recipe("L")), spacing)
    assert caught.value.code == "TOO_MANY_SPOTS"
    assert caught.value.context["max_n"] == MAX_TEXT_SPOTS


def test_regression_spacing_count_caps_text_whose_minimum_is_over_the_cap() -> None:
    glyphs = {"I": {"advance": 8, "strokes": list(DENSE_STROKES)}}
    registry, uri = register_font(bundled_fonts(), tiny_bytes(glyphs=glyphs))
    dense = TextRecipe(text="I" * DENSE_CHARS, fonts=(uri,), height=0.01, at=(0, 0))
    with pytest.raises(SpacingError) as caught:
        spacing_count(text_strokes(dense, font_registry=registry), 100.0)
    assert caught.value.code == "TOO_MANY_SPOTS"
    assert caught.value.context["n"] == 2 * len(DENSE_STROKES) * DENSE_CHARS
    assert caught.value.hint == "shorten the text or split it into several forms"


@pytest.mark.parametrize("spacing", [0.0, -1.0, math.nan, math.inf])
def test_spacing_count_rejects_a_spacing_that_is_not_positive_and_finite(spacing: float) -> None:
    with pytest.raises(SchemaError):
        spacing_count(text_strokes(recipe("L")), spacing)


@ignore_legibility
@pytest.mark.parametrize("font", ["simplex", "relief-singleline"])
def test_regression_spacing_never_doubles_a_spot_on_closed_loops(font: str) -> None:
    loops = recipe(LOOP_TEXT, font, LOOP_HEIGHT)
    points, _ = text_slots(loops, spacing_count(text_strokes(loops), LOOP_SPACING))
    assert min_gap(points) > COINCIDENT_TOL_M


@pytest.mark.parametrize(("n", "spacing"), [(None, None), (11, 1.0)])
def test_count_recipe_needs_exactly_one_of_n_and_spacing(
    n: int | None, spacing: float | None
) -> None:
    geometry = recipe("L").model_dump(mode="json")
    with pytest.raises(SchemaError) as caught:
        count_recipe(geometry, n, spacing, None)
    assert caught.value.code == "N_OR_SPACING"


def test_count_recipe_with_n_clears_the_spacing() -> None:
    geometry = {**recipe("L").model_dump(mode="json"), "spacing": 1.0, "n": 11}
    assert count_recipe(geometry, 12, None, None) == {**geometry, "spacing": None, "n": 12}


@pytest.mark.parametrize("n", [0, True, "12", MAX_TEXT_SPOTS + 1])
def test_count_recipe_rejects_a_bad_n(n: object) -> None:
    with pytest.raises(SpacingError):
        count_recipe(recipe("L").model_dump(mode="json"), n, None, None)  # type: ignore[arg-type]
