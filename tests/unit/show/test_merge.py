#!/usr/bin/env python3

"""
test_merge.py --- tests the three-way, item-by-item merge of two edits of a show

Contains:
    test_three_role_agents_on_one_set_merge_with_no_conflict: merges a move, a turn and a note on
        one spot in set 2 of the seeded show, 300 x 60 in the slow suite
    test_two_x_values_for_one_spot_conflict_on_that_item: names the drill item and both values
    test_regression_merge_refused_by_the_models_is_a_conflict: reports a selection of a deleted
        spot, and two sets given one ID, as conflicts on the items involved
    test_merge_refused_with_no_changed_item_involved_names_the_show: names the show item when
        the refused part holds no item either side changed
    test_set_order_changes_merge_when_they_touch_different_sets: merges an added and a removed set
    test_a_removed_and_a_changed_item_conflict: names a deleted spot's placement the other moved
    test_order_edits_that_cannot_combine_conflict_with_the_reason: names a spot moved and removed
    test_placement_orders_merge_element_by_element: merges a spot added and a placement reordered
    test_items_changed_alike_on_both_sides_merge: keeps a set both sides added alike
    test_merge_with_no_change_on_one_side_returns_the_other_show: returns that show itself
"""

from collections.abc import Mapping

import pytest

from e8s.cast.spots import add_spot, define_selection, delete_spots
from e8s.choreo.form_models import Form
from e8s.choreo.models import PlacementPatch
from e8s.core.ids import FormId, SetId, SpotId
from e8s.examples import two_set_show
from e8s.show.change_models import ItemKey, MergeConflict, MergeResult
from e8s.show.items import changes, items
from e8s.show.manage import add_set, remove_set
from e8s.show.merge import merge
from e8s.show.models import Show
from e8s.show.renumber import renumber
from tests.fixtures.generators import seeded_show
from tests.fixtures.recipes import LINE_RECIPE

SEED = 7
SECOND = 1
SMALL, LARGE = (20, 4), (300, 60)
REFUSED = "the models refuse the merged show"


def _patched(show: Show, position: int, spot: str, patch: PlacementPatch) -> Show:
    edited = show.sets[position].with_placements({spot: patch}, force=True)
    return show.replace(sets=tuple(edited if i == position else s for i, s in enumerate(show.sets)))


def _merged(base: Show, ours: Show, theirs: Show) -> Show:
    result = merge(base, ours, theirs)
    assert result.conflicts == ()
    assert result.show is not None
    return result.show


def _swapped(result: MergeResult) -> tuple[MergeConflict, ...]:
    return tuple(c.replace(ours=c.theirs, theirs=c.ours) for c in result.conflicts)


@pytest.mark.parametrize(
    ("spots", "sets"), [SMALL, pytest.param(*LARGE, marks=pytest.mark.slow)], ids=["20x4", "300x60"]
)
def test_three_role_agents_on_one_set_merge_with_no_conflict(spots: int, sets: int) -> None:
    base = seeded_show(SEED, spots, sets)
    spot = base.cast.spots[0].id
    drill = _patched(base, SECOND, spot, PlacementPatch(x=1.5, y=2.5))
    facing = _patched(base, SECOND, spot, PlacementPatch(facing=137.5))
    labels = _patched(base, SECOND, spot, PlacementPatch(note="solo"))
    merged = _merged(base, _merged(base, drill, facing), labels)
    placed = merged.sets[SECOND].get(spot)
    assert (placed.x, placed.y, placed.facing, placed.note) == (1.5, 2.5, 137.5, "solo")
    assert [c.key.role for c in changes(base, merged).changes] == ["drill", "facing", "labels"]
    untouched = [i for i in range(len(base.sets)) if i != SECOND]
    assert all(merged.sets[i] is base.sets[i] for i in untouched)


def test_two_x_values_for_one_spot_conflict_on_that_item() -> None:
    base = two_set_show()
    ours = _patched(base, SECOND, "T1", PlacementPatch(x=3.0, note="lead"))
    theirs = _patched(base, SECOND, "T1", PlacementPatch(x=4.0))
    result = merge(base, ours, theirs)
    assert result.show is None
    (conflict,) = result.conflicts
    assert conflict.key == ItemKey(kind="placement", path=("2", "T1"), role="drill")
    assert conflict.reason == "both sides changed this item differently"
    assert [_x(conflict.base), _x(conflict.ours), _x(conflict.theirs)] == [
        base.sets[SECOND].get("T1").x,
        3.0,
        4.0,
    ]
    assert merge(base, theirs, ours).conflicts == _swapped(result)


def _x(value: object) -> object:
    assert isinstance(value, Mapping)
    return value["x"]


def test_regression_merge_refused_by_the_models_is_a_conflict() -> None:
    base = two_set_show()
    deleted = delete_spots(base, "T4", force=True)
    selected = define_selection(base, "solo", "T4")
    result = merge(base, deleted, selected)
    assert result.show is None
    keys = [c.key for c in result.conflicts]
    assert ItemKey(kind="selection", path=("solo",)) in keys
    assert ItemKey(kind="spot", path=("T4",)) in keys
    assert all(c.reason.startswith(REFUSED) and "T4" in c.reason for c in result.conflicts)
    assert merge(base, selected, deleted).conflicts == _swapped(result)
    longer = add_set(base, "5", 4.0)
    renamed, _ = renumber(longer)
    added = add_set(longer, "3", 4.0)
    clash = merge(longer, renamed, added)
    assert clash.show is None
    assert [c.key for c in clash.conflicts] == [
        ItemKey(kind="set", path=("3",)),
        ItemKey(kind="set", path=("5",)),
        ItemKey(kind="set_order", path=()),
    ]
    assert all(c.reason.startswith(REFUSED) for c in clash.conflicts)


def test_merge_refused_with_no_changed_item_involved_names_the_show() -> None:
    base = two_set_show()
    stray = Form(id=FormId("f9"), set=SetId("9"), recipe=LINE_RECIPE, members=(SpotId("T1"),))
    unstorable = base.replace(forms={stray.id: stray})
    ours = _patched(unstorable, SECOND, "T1", PlacementPatch(x=3.0))
    theirs = _patched(unstorable, SECOND, "T2", PlacementPatch(x=3.0))
    (conflict,) = merge(unstorable, ours, theirs).conflicts
    assert conflict.key == ItemKey(kind="show", path=())
    assert conflict.reason.startswith(REFUSED)
    assert "show cannot be stored" in conflict.reason


def _four_set_show() -> Show:
    return add_set(add_set(two_set_show(), "3", 4.0), "4", 4.0)


def test_set_order_changes_merge_when_they_touch_different_sets() -> None:
    base = _four_set_show()
    ours = add_set(base, "1A", 4.0, after="1")
    theirs = remove_set(base, "3", force=True)
    merged = _merged(base, ours, theirs)
    assert merged.set_ids() == ["1", "1A", "2", "4"]
    assert merged == _merged(base, theirs, ours)
    swapped = base.replace(sets=(*base.sets[:2], base.sets[3], base.sets[2]))
    assert _merged(base, ours, swapped).set_ids() == ["1", "1A", "2", "4", "3"]


def test_a_removed_and_a_changed_item_conflict() -> None:
    base = two_set_show()
    deleted = delete_spots(base, "T2", force=True)
    moved = _patched(base, SECOND, "T2", PlacementPatch(x=9.0))
    result = merge(base, deleted, moved)
    (conflict,) = result.conflicts
    assert conflict.key == ItemKey(kind="placement", path=("2", "T2"), role="drill")
    assert conflict.reason == "one side removed this item and the other changed it"
    assert conflict.ours is None


def test_order_edits_that_cannot_combine_conflict_with_the_reason() -> None:
    base = two_set_show()
    spots = base.cast.spots
    moved = base.replace(cast=base.cast.replace(spots=(*spots[1:], spots[0])))
    deleted = delete_spots(base, "T1", force=True)
    result = merge(base, moved, deleted)
    (conflict,) = [c for c in result.conflicts if c.key.kind == "spot_order"]
    assert conflict.reason == "T1 moved on one side and removed on the other"
    assert merge(base, deleted, moved).conflicts == _swapped(result)


def test_placement_orders_merge_element_by_element() -> None:
    base = two_set_show()
    added = add_spot(base, (1.0, 2.0), "trumpets", "1")
    first = base.sets[0]
    reordered = base.replace(
        sets=(first.replace(placements=tuple(reversed(first.placements))), *base.sets[1:])
    )
    merged = _merged(base, added, reordered)
    new_spot = added.cast.spots[-1].id
    assert merged.sets[0].entity_ids() == ("T4", new_spot, "T3", "T2", "T1")
    assert merged == _merged(base, reordered, added)


def test_items_changed_alike_on_both_sides_merge() -> None:
    base = two_set_show()
    ours, theirs = add_set(base, "3", 4.0), add_set(base, "3", 4.0)
    merged = _merged(base, ours, theirs)
    assert items(merged) == items(ours)


def test_merge_with_no_change_on_one_side_returns_the_other_show() -> None:
    base = two_set_show()
    edited = _patched(base, SECOND, "T1", PlacementPatch(note="solo"))
    copy = Show.from_json(base.to_json())
    assert merge(base, copy, edited).show is edited
    assert merge(base, edited, copy).show is edited
