#!/usr/bin/env python3

"""
test_text_edit.py --- tests edit_text_count, the explicit way to change a text form's count

Contains:
    test_*(): one behavior of showpy.choreo.text_edit each
"""

from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from showpy.choreo.form_models import Form
from showpy.choreo.form_text import text_recipe, text_strokes
from showpy.choreo.forms import circle_form, slots, text_form
from showpy.choreo.text_count import spacing_count
from showpy.choreo.text_edit import edit_text_count
from showpy.core.errors import LockedError, SchemaError, SpacingError

FORM_SPOTS = 60
PROPERTY_SPOTS = 30
MAX_CHANGED = 5
FEWEST_SLOTS = PROPERTY_SPOTS - MAX_CHANGED
MOST_SLOTS = PROPERTY_SPOTS + MAX_CHANGED
HI_MINIMUM = 12


def spots(n: int) -> list[str]:
    return [f"S{i}" for i in range(n)]


def go_form(n: int = FORM_SPOTS) -> Form:
    return text_form("t", spots(n), "GO", fonts=["hershey-roman"], height=4.0, at=(0, 10), n=n)


FORM = go_form()


def test_edit_text_count_adds_named_spots() -> None:
    g = edit_text_count(FORM, add=["X1", "X2"], n=62)
    assert g.members == (*FORM.members, "X1", "X2")
    assert g.geometry["n"] == 62
    assert g.geometry["spacing"] is None
    assert len(slots(g)) == 62
    assert go_form() == FORM


def test_edit_text_count_removes_named_spots() -> None:
    g = edit_text_count(FORM, remove=["S3", "S10"], n=58)
    assert g.members == tuple(m for m in FORM.members if m not in {"S3", "S10"})


def test_edit_text_count_swaps_spots_at_the_same_count() -> None:
    g = edit_text_count(FORM, add=["X1"], remove=["S0"], n=60)
    assert len(g.members) == FORM_SPOTS
    assert g.members[-1] == "X1"
    assert "S0" not in g.members
    implicit = edit_text_count(FORM, add=["X1"], remove=["S0"])
    assert implicit.geometry["n"] == FORM_SPOTS
    assert implicit.members == g.members


def test_edit_text_count_from_spacing_needs_matching_members() -> None:
    derived = spacing_count(text_strokes(text_recipe(FORM.geometry)), 1.0)
    gap = derived - FORM_SPOTS
    with pytest.raises(SpacingError) as caught:
        edit_text_count(FORM, spacing=1.0)
    assert caught.value.code == "MEMBER_COUNT_MISMATCH"
    assert caught.value.context["n"] == derived
    expected = f"add {gap} more spots" if gap > 0 else f"remove {-gap} spots"
    assert caught.value.hint == expected
    change: dict[str, Any] = (
        {"add": [f"X{i}" for i in range(gap)]} if gap > 0 else {"remove": spots(-gap)}
    )
    g = edit_text_count(FORM, spacing=1.0, **change)
    assert g.geometry["spacing"] == 1.0
    assert g.geometry["n"] == derived == len(g.members)


def test_edit_text_count_wrong_number_of_spots_says_how_many() -> None:
    with pytest.raises(SpacingError) as added:
        edit_text_count(FORM, add=["X1"], n=63)
    assert added.value.code == "MEMBER_COUNT_MISMATCH"
    assert added.value.hint == "add 2 more spots"
    with pytest.raises(SpacingError) as removed:
        edit_text_count(FORM, remove=["S0"], n=50)
    assert removed.value.hint == "remove 9 spots"


@pytest.mark.parametrize(("add", "culprits"), [(["S5"], ["S5"]), (["X1", "X1"], ["X1"])])
def test_edit_text_count_rejects_an_existing_member(add: list[str], culprits: list[str]) -> None:
    with pytest.raises(SchemaError) as caught:
        edit_text_count(FORM, add=add)
    assert caught.value.code == "ALREADY_MEMBER"
    assert caught.value.context == {"form": "t", "spots": culprits}


def test_edit_text_count_already_member_hint_names_the_addable_spots() -> None:
    with pytest.raises(SchemaError) as caught:
        edit_text_count(FORM, add=["X1", "S5"])
    assert caught.value.hint == "add only spots outside form t: X1"


@pytest.mark.parametrize(("remove", "culprits"), [(["Z9"], ["Z9"]), (["S1", "S1"], ["S1"])])
def test_edit_text_count_rejects_a_non_member(remove: list[str], culprits: list[str]) -> None:
    with pytest.raises(SchemaError) as caught:
        edit_text_count(FORM, remove=remove)
    assert caught.value.code == "NOT_A_MEMBER"
    assert caught.value.context == {"form": "t", "spots": culprits}
    assert caught.value.hint.startswith("remove only members of form t: S0, S1, S2")


def test_edit_text_count_n_and_spacing_together_is_rejected() -> None:
    with pytest.raises(SchemaError) as caught:
        edit_text_count(FORM, n=60, spacing=1.0)
    assert caught.value.code == "N_OR_SPACING"


def test_edit_text_count_rejects_other_kinds() -> None:
    with pytest.raises(SchemaError) as caught:
        edit_text_count(circle_form("c", ["A", "B"], (0, 5), 2.0), add=["C"])
    assert caught.value.code == "NOT_A_TEXT_FORM"
    assert caught.value.hint == "use edit() for other kinds"


def test_edit_text_count_locked_form_needs_force() -> None:
    locked = FORM.replace(locked=True)
    with pytest.raises(LockedError):
        edit_text_count(locked, add=["X1"], n=61)
    assert len(edit_text_count(locked, add=["X1"], n=61, force=True).members) == 61


@pytest.mark.filterwarnings("ignore::showpy.core.errors.LegibilityWarning")
def test_edit_text_count_below_the_minimum_names_it() -> None:
    f = text_form("hi", spots(HI_MINIMUM), "HI", fonts=["simplex"], height=4.0, at=(0, 10),
                  n=HI_MINIMUM)  # fmt: skip
    with pytest.raises(SpacingError) as caught:
        edit_text_count(f, remove=spots(8), n=4)
    assert caught.value.code == "TOO_FEW_SPOTS"
    assert caught.value.context["min_n"] == HI_MINIMUM


@pytest.mark.parametrize("field", ["add", "remove"])
def test_regression_edit_text_count_rejects_a_bare_string(field: str) -> None:
    bare: dict[str, Any] = {field: "S1"}
    with pytest.raises(SchemaError) as caught:
        edit_text_count(FORM, **bare)
    assert caught.value.context == {"form": "t", "field": field}


@pytest.mark.parametrize("add", [["bad id"], [5], ["S1/x"]])
def test_regression_edit_text_count_checks_new_spot_ids(add: list[Any]) -> None:
    with pytest.raises(SchemaError):
        edit_text_count(FORM, add=add, n=61)


def test_regression_edit_text_count_stores_normalized_spot_ids() -> None:
    assert edit_text_count(FORM, add=["\N{KELVIN SIGN}1"], n=61).members[-1] == "K1"


@given(
    added=st.integers(0, MAX_CHANGED),
    removed=st.lists(st.sampled_from(spots(PROPERTY_SPOTS)), unique=True, max_size=MAX_CHANGED),
    n=st.none() | st.integers(FEWEST_SLOTS, MOST_SLOTS),
)
def test_regression_edit_text_count_matches_slots_and_members(
    added: int, removed: list[str], n: int | None
) -> None:
    f = go_form(PROPERTY_SPOTS)
    add = [f"X{i}" for i in range(added)]
    member_count = PROPERTY_SPOTS + added - len(removed)
    if n is not None and n != member_count:
        with pytest.raises(SpacingError) as caught:
            edit_text_count(f, add=add, remove=removed, n=n)
        assert caught.value.code == "MEMBER_COUNT_MISMATCH"
        return
    g = edit_text_count(f, add=add, remove=removed, n=n)
    assert len(g.members) == g.geometry["n"] == len(slots(g)) == member_count
    assert f == go_form(PROPERTY_SPOTS)
