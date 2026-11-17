#!/usr/bin/env python3

"""
test_text_count.py --- tests text spot counts derived from a spacing between spots

Contains:
    test_*(): one behavior of showpy.choreo.text_count each
    test_regression_text_spot_count_gives_the_count_text_form_derives(): names the members a
        spacing-built text form needs before it is built
    test_regression_spacing_count_follows_a_scaled_text_form(): counts a scaled form's spots
        from its stage-meter stroke lengths
    test_regression_legibility_warning_reports_stage_meters(): names a scaled form's spacing
        and letter height after its transform
"""

import math
import warnings

import pytest
from scipy.spatial import cKDTree

from showpy.choreo.form_layout import layout
from showpy.choreo.form_ops import transform_form
from showpy.choreo.form_text import (
    COINCIDENT_TOL_M,
    MAX_TEXT_SPOTS,
    text_recipe,
    text_slots,
    text_strokes,
)
from showpy.choreo.forms import slots, text_form
from showpy.choreo.models import TextRecipe
from showpy.choreo.text_count import count_recipe, spacing_count, text_spot_count
from showpy.choreo.text_edit import edit_text_count
from showpy.core.errors import LegibilityWarning, SchemaError, SpacingError
from showpy.core.types import Points
from showpy.geo.affine import scale_matrix
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
SCALE = 2.0
SCALED_L_SPOTS = 21
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


def test_regression_spacing_count_follows_a_scaled_text_form() -> None:
    ell = text_form("L", spots(L_SPOTS_AT_ONE_METER), "L", fonts=["simplex"],
                    height=LETTER_HEIGHT, at=(0, 0), spacing=1.0)  # fmt: skip
    scaled = transform_form(ell, scale_matrix(SCALE, SCALE, (0.0, 0.0)))
    added = [f"X{i}" for i in range(SCALED_L_SPOTS - L_SPOTS_AT_ONE_METER)]
    edited = edit_text_count(scaled, add=added, spacing=1.0)
    assert edited.geometry["n"] == SCALED_L_SPOTS
    stem = slots(edited)[: SCALED_L_SPOTS // 2]
    assert math.dist(stem[0], stem[1]) == pytest.approx(1.0)


@pytest.mark.parametrize(
    ("sx", "sy", "wanted"),
    [(SCALE, SCALE, "20.00 m apart on 12.00 m"), (1.0, 4.0, "28.00 m apart on 12.00 m")],
)
def test_regression_legibility_warning_reports_stage_meters(
    sx: float, sy: float, wanted: str
) -> None:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", LegibilityWarning)
        ell = text_form("L", spots(2), "L", fonts=["simplex"], height=LETTER_HEIGHT, at=(0, 0), n=2)
    stretched = transform_form(ell, scale_matrix(sx, sy, (0.0, 0.0)))
    with pytest.warns(LegibilityWarning, match=f"up to {wanted} letters"):
        layout(stretched)


@pytest.mark.parametrize("font", ["hershey-roman", "tiny"])
def test_regression_text_spot_count_gives_the_count_text_form_derives(font: str) -> None:
    registry, _ = register_font(bundled_fonts(), tiny_bytes())
    text = "I" if font == "tiny" else "GO\nTEAM"
    n = text_spot_count(text, fonts=[font], height=4.0, spacing=0.5, rotation=30.0,
                        align="center", letter_spacing=0.1, line_spacing=1.5,
                        font_registry=registry)  # fmt: skip
    form = text_form("t", spots(n), text, fonts=[font], height=4.0, at=(3, 9), rotation=30.0,
                     align="center", letter_spacing=0.1, line_spacing=1.5, spacing=0.5,
                     font_registry=registry)  # fmt: skip
    assert form.geometry["n"] == n
    strokes = text_strokes(text_recipe(form.geometry), font_registry=registry)
    assert n == spacing_count(strokes, 0.5)
