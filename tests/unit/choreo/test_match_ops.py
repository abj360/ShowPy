#!/usr/bin/env python3

"""
test_match_ops.py --- tests applying matches, swapping spots and swap suggestions on a show
"""

import pytest

from stage.choreo.forms import line_form
from stage.choreo.match import match
from stage.choreo.match_ops import apply_match, clear_rows, suggest_swaps, swap
from stage.choreo.models import PathRow, PathStyle, TransitionSpec, Waypoint
from stage.core.errors import LockedError, UnknownRefError
from stage.show.models import Show
from tests.fixtures.shows import two_set_show

TID = ("1", "2")


def _with_rows(show: Show, locked: bool = False) -> Show:
    rows = {
        e: PathRow(entity=e, style=PathStyle.CURVE, waypoints=(Waypoint(x=1, y=7),), locked=locked)
        for e in ("T1", "T2")
    }
    return show.model_copy(update={"transitions": (TransitionSpec(id=TID, counts=8.0, rows=rows),)})


def _pin(show: Show, set_id: str, entity: str) -> Show:
    pinned = show.set(set_id).with_placements({entity: {"pinned": True}})
    return show.model_copy(
        update={"sets": tuple(pinned if s.id == set_id else s for s in show.sets)}
    )


def test_apply_match_writes_targets_and_drops_rows() -> None:
    show = _with_rows(two_set_show())
    result = match(show.set("1"), [(6.0, 3.0), (4.0, 3.0), (2.0, 3.0), (0.0, 3.0)])
    applied = apply_match(show, TID, result)
    assert applied.set("2").get("T1").point == (0.0, 3.0)
    assert applied.transition(TID).rows == {}
    assert show.set("2").get("T1").point == (0.0, 9.0)


def test_apply_match_respects_pins_and_locked_rows() -> None:
    result = match(two_set_show().set("1"), [(0.0, 3.0), (2.0, 3.0), (4.0, 3.0), (6.0, 3.0)])
    with pytest.raises(LockedError):
        apply_match(_pin(two_set_show(), "2", "T1"), TID, result)
    with pytest.raises(LockedError):
        apply_match(_with_rows(two_set_show(), locked=True), TID, result)
    forced = apply_match(_with_rows(two_set_show(), locked=True), TID, result, force=True)
    assert forced.transition(TID).rows == {}


def test_apply_match_skips_pinned_spot_already_on_target() -> None:
    show = _pin(two_set_show(), "2", "T1")
    result = match(show.set("1"), [(0.0, 9.0), (2.0, 3.0), (4.0, 3.0), (6.0, 3.0)])
    assert apply_match(show, TID, result).set("2").get("T2").point == (2.0, 3.0)


def test_apply_match_rejects_non_consecutive_transition() -> None:
    result = match(two_set_show().set("1"), [(0.0, 0.0), (1.0, 0.0), (2.0, 0.0), (3.0, 0.0)])
    with pytest.raises(UnknownRefError):
        apply_match(two_set_show(), ("2", "1"), result)


def test_clear_rows_keeps_other_rows() -> None:
    cleared = clear_rows(_with_rows(two_set_show()), TID, ["T1"])
    assert list(cleared.transition(TID).rows) == ["T2"]


def test_swap_exchanges_places_forms_and_drops_rows() -> None:
    show = _with_rows(two_set_show())
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
        swap(_pin(two_set_show(), "2", "T3"), "2", "T3", "T4")


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
