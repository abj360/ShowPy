#!/usr/bin/env python3

"""
test_match_ops.py --- tests applying matches, swapping spots and swap suggestions on a show
"""

import pytest

from showpy.choreo.forms import line_form
from showpy.choreo.match import match
from showpy.choreo.match_ops import apply_match, clear_rows, suggest_swaps, swap
from showpy.core.errors import LockedError, UnknownRefError
from tests.fixtures.shows import two_set_show
from tests.helpers import pin_spot, with_curve_rows

TID = ("1", "2")
ROW_SPOTS = ("T1", "T2")


def test_apply_match_writes_targets_and_drops_rows() -> None:
    show = with_curve_rows(two_set_show(), ROW_SPOTS)
    result = match(show.set("1"), [(6.0, 3.0), (4.0, 3.0), (2.0, 3.0), (0.0, 3.0)])
    applied = apply_match(show, TID, result)
    assert applied.set("2").get("T1").point == (0.0, 3.0)
    assert applied.transition(TID).rows == {}
    assert show.set("2").get("T1").point == (0.0, 9.0)


def test_apply_match_respects_pins_and_locked_rows() -> None:
    result = match(two_set_show().set("1"), [(0.0, 3.0), (2.0, 3.0), (4.0, 3.0), (6.0, 3.0)])
    with pytest.raises(LockedError):
        apply_match(pin_spot(two_set_show(), "2", "T1"), TID, result)
    with pytest.raises(LockedError):
        apply_match(with_curve_rows(two_set_show(), ROW_SPOTS, locked=True), TID, result)
    forced = apply_match(
        with_curve_rows(two_set_show(), ROW_SPOTS, locked=True), TID, result, force=True
    )
    assert forced.transition(TID).rows == {}


def test_apply_match_skips_pinned_spot_already_on_target() -> None:
    show = pin_spot(two_set_show(), "2", "T1")
    result = match(show.set("1"), [(0.0, 9.0), (2.0, 3.0), (4.0, 3.0), (6.0, 3.0)])
    assert apply_match(show, TID, result).set("2").get("T2").point == (2.0, 3.0)


def test_apply_match_rejects_non_consecutive_transition() -> None:
    result = match(two_set_show().set("1"), [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)])
    with pytest.raises(UnknownRefError):
        apply_match(two_set_show(), ("2", "1"), result)


def test_clear_rows_keeps_other_rows() -> None:
    cleared = clear_rows(with_curve_rows(two_set_show(), ROW_SPOTS), TID, ["T1"])
    assert list(cleared.transition(TID).rows) == ["T2"]


def test_swap_exchanges_places_forms_and_drops_rows() -> None:
    show = with_curve_rows(two_set_show(), ROW_SPOTS)
    form = line_form("front", ["T1", "T2", "T3", "T4"], (0, 9), (6, 9), set_id="2")
    show = show.model_copy(update={"forms": {"front": form}})
    swapped = swap(show, "2", "T1", "T2")
    assert swapped.set("2").get("T1").x == 2.0
    assert swapped.set("2").get("T2").x == 0.0
    assert swapped.forms["front"].members == ("T2", "T1", "T3", "T4")
    assert list(swapped.transition(TID).rows) == []


def test_swap_respects_locked_forms_and_pins() -> None:
    form = line_form("f", ["T1", "T2"], (0, 9), (2, 9), set_id="2").model_copy(
        update={"locked": True}
    )
    show = two_set_show().model_copy(update={"forms": {"f": form}})
    with pytest.raises(LockedError):
        swap(show, "2", "T1", "T2")
    assert swap(show, "2", "T1", "T2", force=True).forms["f"].members == ("T2", "T1")
    with pytest.raises(LockedError):
        swap(pin_spot(two_set_show(), "2", "T3"), "2", "T3", "T4")


def test_suggest_swaps_finds_the_swap_that_undoes_a_tangle() -> None:
    tangled = swap(two_set_show(), "2", "T1", "T4")
    suggestions = suggest_swaps(tangled, TID)
    assert suggestions[0][:2] == ("T1", "T4")
    assert suggestions[0][2] > 3.0


def test_suggest_swaps_returns_nothing_for_a_clean_move() -> None:
    assert suggest_swaps(two_set_show(), TID) == []


def test_suggest_swaps_ranks_crossing_removal_without_gain() -> None:
    tangled = swap(two_set_show(), "2", "T2", "T3")
    best = suggest_swaps(tangled, TID, k=1)
    assert best[0][:2] == ("T2", "T3")
