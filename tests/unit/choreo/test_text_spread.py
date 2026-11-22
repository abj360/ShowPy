#!/usr/bin/env python3

"""
test_text_spread.py --- tests the junction gap between text slots and the crowded-stroke report

Contains:
    test_regression_junction_slots_keep_a_share_of_the_spacing(): meets the junction gap on the
        relief E, simplex P and simplex B counts where spots sat under 2 mm apart
    test_regression_crowded_strokes_never_stack_and_are_reported(): places doubled and
        converging strokes without COINCIDENT_SLOTS and reports every slot under the gap
    test_regression_every_glyph_keeps_or_reports_the_junction_gap(): checks every bundled glyph
        at its minimum count and three times it
    test_regression_every_glyph_keeps_or_reports_the_gap_over_forty_counts(): checks every
        bundled glyph at forty counts from its minimum (slow)
    test_text_report_*(): describes the crowded strokes of one text form
"""

import math
from collections.abc import Iterator

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from scipy.spatial import cKDTree

from showpy.choreo.form_models import Form, TextRecipe
from showpy.choreo.form_text import min_text_spots, text_recipe, text_report, text_strokes
from showpy.choreo.forms import line_form, slots, text_form
from showpy.choreo.text_spread import COINCIDENT_TOL_M, JUNCTION_GAP_SHARE
from showpy.core.errors import SchemaError
from showpy.core.types import Points
from showpy.geo.fonts import bundled_fonts
from showpy.geo.spacing import apportion

HEIGHT = 2.0
AT = (0.0, 10.0)
SWEEP_COUNTS = 40
OPEN_MINIMUM = 2
CLOSED_MINIMUM = 3
JUNCTION_CASES = [("relief-singleline", "E", 56), ("simplex", "P", 59), ("simplex", "B", 55)]
# Glyphs whose strokes double or converge (final review I1 sweep, 2026-11-16): no spread keeps
# the junction gap at some counts from the minimum to forty above it.
CROWDED_GLYPHS = [
    *(("hershey-roman", char) for char in "$&8@{}~"),
    *(("hershey-script", char) for char in "!$&01346789@AYdgqy{}~"),
    *(("relief-singleline", char) for char in "gæĝğġģπ∏"),
]
SCRIPT_BANG_STROKES = 4
ignore_legibility = pytest.mark.filterwarnings("ignore::showpy.core.errors.LegibilityWarning")


def _form(font: str, char: str, n: int) -> Form:
    return text_form(
        "g", [f"S{i}" for i in range(n)], char, fonts=[font], height=HEIGHT, at=AT, n=n
    )


def _least(font: str, char: str) -> int:
    uri = bundled_fonts().resolve(font)
    return min_text_spots(text_strokes(TextRecipe(text=char, fonts=(uri,), height=HEIGHT, at=AT)))


def _spacings(form: Form) -> np.ndarray:
    """Returns each slot's stroke spacing: its stroke's length over its gaps between spots."""
    strokes = text_strokes(text_recipe(form.recipe.model_dump(mode="json")))
    minimums = [CLOSED_MINIMUM if stroke.closed else OPEN_MINIMUM for stroke in strokes]
    counts = apportion(
        [stroke.length() for stroke in strokes], form.recipe.model_dump(mode="json")["n"], minimums
    )
    return np.concatenate(
        [
            np.full(k, stroke.length() / (k if stroke.closed else k - 1))
            for stroke, k in zip(strokes, counts, strict=True)
        ]
    )


def _crowded_slots(points: Points, spacings: np.ndarray) -> set[int]:
    """Returns the later slot of every pair closer than the junction gap of its spacings."""
    reach = max(COINCIDENT_TOL_M, JUNCTION_GAP_SHARE * float(spacings.max()))
    return {
        j
        for i, j in cKDTree(points).query_pairs(reach)
        if math.dist(points[i], points[j])
        < max(COINCIDENT_TOL_M, JUNCTION_GAP_SHARE * min(spacings[i], spacings[j]))
    }


def _check(font: str, char: str, n: int) -> set[int]:
    """Asserts no two slots stack and every crowded slot is reported; returns the crowded."""
    form = _form(font, char, n)
    points = slots(form)
    distances, _ = cKDTree(points).query(points, k=2)
    assert float(distances[:, 1].min()) >= COINCIDENT_TOL_M, (font, char, n)
    crowded = _crowded_slots(points, _spacings(form))
    report = text_report(form)
    reported = {
        slot
        for entry in report.crowded
        for slot in range(entry.first_slot, entry.first_slot + entry.spots)
    }
    assert crowded <= reported, (font, char, n, sorted(crowded - reported))
    for entry in report.crowded:
        assert COINCIDENT_TOL_M <= entry.gap < entry.wanted, entry
    return crowded


def _glyph_cases() -> Iterator[tuple[str, str]]:
    registry = bundled_fonts()
    for name, uri in sorted(registry.names.items()):
        for char, glyph in registry.get(uri).glyphs.items():
            if glyph.strokes:
                yield name, char


@ignore_legibility
@pytest.mark.parametrize(("font", "char", "n"), JUNCTION_CASES)
def test_regression_junction_slots_keep_a_share_of_the_spacing(
    font: str, char: str, n: int
) -> None:
    form = _form(font, char, n)
    assert not _crowded_slots(slots(form), _spacings(form))
    assert text_report(form).crowded == ()


@ignore_legibility
@given(st.sampled_from(CROWDED_GLYPHS), st.integers(0, SWEEP_COUNTS - 1))
def test_regression_crowded_strokes_never_stack_and_are_reported(
    glyph: tuple[str, str], offset: int
) -> None:
    font, char = glyph
    _check(font, char, _least(font, char) + offset)


@ignore_legibility
def test_regression_every_glyph_keeps_or_reports_the_junction_gap() -> None:
    for font, char in _glyph_cases():
        least = _least(font, char)
        for n in (least, 3 * least):
            _check(font, char, n)


@pytest.mark.slow
@ignore_legibility
def test_regression_every_glyph_keeps_or_reports_the_gap_over_forty_counts() -> None:
    for font, char in _glyph_cases():
        least = _least(font, char)
        for n in range(least, least + SWEEP_COUNTS):
            _check(font, char, n)


@ignore_legibility
def test_text_report_names_the_crowded_strokes_of_a_converging_glyph() -> None:
    least = _least("hershey-script", "!")
    report = text_report(_form("hershey-script", "!", least))
    assert report.crowded
    for entry in report.crowded:
        assert (entry.char, entry.line, entry.column) == ("!", 0, 0)
        assert 0 < entry.stroke < SCRIPT_BANG_STROKES
        assert entry.spots >= OPEN_MINIMUM


def test_text_report_is_empty_when_every_gap_holds() -> None:
    hi = text_form("hi", [f"S{i}" for i in range(36)], "HI", fonts=["simplex"], height=HEIGHT,
                   at=AT, n=36)  # fmt: skip
    assert text_report(hi).crowded == ()


def test_text_report_rejects_other_kinds() -> None:
    with pytest.raises(SchemaError) as caught:
        text_report(line_form("row", ["A", "B"], (0, 0), (2, 0)))
    assert caught.value.code == "NOT_A_TEXT_FORM"
