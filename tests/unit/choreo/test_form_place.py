#!/usr/bin/env python3

"""
test_form_place.py --- tests building sets from forms, reading members back and rebuilding forms
"""

import pytest

from showpy.choreo.form_place import filled_forms, place, rebuild
from showpy.choreo.forms import circle_form, line_form
from showpy.choreo.models import MatchOptions
from showpy.core.errors import LockedError, MatchError, SchemaError, UnknownRefError
from showpy.core.util import approx_equal
from tests.fixtures.shows import two_set_show
from tests.helpers import pin_spot, with_curve_rows

TID = ("1", "2")
ROW_SPOTS = ("T1", "T3")


def test_place_puts_members_on_their_slots() -> None:
    placed = place(two_set_show(), "2", [line_form("f", ["T4", "T3", "T2", "T1"], (0, 9), (6, 9))])
    assert [placed.get(e).x for e in ("T4", "T3", "T2", "T1")] == [0.0, 2.0, 4.0, 6.0]
    assert placed.forms == ("f",)
    assert placed.get("T4").form == "f"


def test_place_matches_unassigned_spots_into_open_slots() -> None:
    form = line_form("f", [], (6, 12), (0, 12), n=4)
    placed = place(two_set_show(), "2", [form])
    assert [placed.get(e).x for e in ("T1", "T2", "T3", "T4")] == [0.0, 2.0, 4.0, 6.0]
    assert [placed.get(e).order for e in ("T1", "T2", "T3", "T4")] == [3, 2, 1, 0]


def test_place_passes_match_options() -> None:
    form = line_form("f", [], (0, 12), (6, 12), n=4)
    placed = place(two_set_show(), "2", [form], MatchOptions(pinned={"T1": 3}))
    assert placed.get("T1").x == 6.0


def test_place_leaves_pinned_placements_alone() -> None:
    show = pin_spot(two_set_show(), "2", "T1")
    placed = place(show, "2", [line_form("f", ["T1", "T2"], (0, 15), (2, 15))])
    assert placed.get("T1").point == (0.0, 9.0)
    assert placed.get("T2").point == (2.0, 15.0)


def test_place_rejects_overlapping_forms_and_too_few_slots() -> None:
    show = two_set_show()
    with pytest.raises(SchemaError):
        place(
            show,
            "2",
            [line_form("a", ["T1"], (0, 0), (1, 0)), line_form("b", ["T1"], (0, 1), (1, 1))],
        )
    with pytest.raises(MatchError, match="open slots"):
        place(show, "2", [line_form("a", ["T1"], (0, 9), (6, 9), n=2)])


def test_place_in_first_set_needs_members() -> None:
    with pytest.raises(MatchError, match="first set"):
        place(two_set_show(), "1", [line_form("a", [], (0, 9), (6, 9), n=4)])


def test_place_with_spare_slots_keeps_them_open() -> None:
    form = line_form("f", ["T1"], (0, 12), (12, 12), n=6)
    placed = place(two_set_show(), "2", [form], MatchOptions(pinned={"T4": 4}))
    orders = sorted(p.order for p in placed.placements if p.order is not None)
    assert len(orders) == 4
    assert orders == [0, 1, 2, 5]
    with pytest.raises(SchemaError, match="open slots"):
        filled_forms(placed, [form])


def test_filled_forms_reads_members_back_in_slot_order() -> None:
    form = circle_form("c", [], (0, 10), 3.0, n=4)
    placed = place(two_set_show(), "2", [form])
    (filled,) = filled_forms(placed, [form])
    assert filled.set == "2"
    assert sorted(filled.members) == ["T1", "T2", "T3", "T4"]
    assert [placed.get(m).order for m in filled.members] == [0, 1, 2, 3]


def test_rebuild_regenerates_and_drops_neighbouring_rows() -> None:
    form = line_form("row", ["T1", "T2", "T3", "T4"], (0, 14), (6, 14), set_id="2")
    show = with_curve_rows(two_set_show(), ROW_SPOTS).model_copy(update={"forms": {"row": form}})
    rebuilt = rebuild(show, "row")
    assert approx_equal([rebuilt.set("2").get(e).y for e in ("T1", "T4")], [14.0, 14.0])
    assert rebuilt.transition(TID).rows == {}


def test_rebuild_respects_pins_and_locked_rows() -> None:
    form = line_form("row", ["T1", "T2", "T3", "T4"], (0, 14), (6, 14), set_id="2")
    pinned = pin_spot(with_curve_rows(two_set_show(), ROW_SPOTS), "2", "T1").model_copy(
        update={"forms": {"row": form}}
    )
    rebuilt = rebuild(pinned, "row")
    assert rebuilt.set("2").get("T1").point == (0.0, 9.0)
    assert list(rebuilt.transition(TID).rows) == ["T1"]
    locked = with_curve_rows(two_set_show(), ROW_SPOTS, locked=True).model_copy(
        update={"forms": {"row": form}}
    )
    with pytest.raises(LockedError):
        rebuild(locked, "row")
    assert rebuild(locked, "row", force=True).transition(TID).rows == {}


def test_rebuild_unknown_form_suggests_close_id() -> None:
    form = line_form("row", ["T1"], (0, 14), (6, 14), set_id="2")
    show = two_set_show().model_copy(update={"forms": {"row": form}})
    with pytest.raises(UnknownRefError) as caught:
        rebuild(show, "rows")
    assert "row" in caught.value.hint
