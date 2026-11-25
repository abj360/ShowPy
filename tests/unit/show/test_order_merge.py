#!/usr/bin/env python3

"""
test_order_merge.py --- tests merging two edits of one ordered list of IDs

Contains:
    test_edits_that_combine_merge_the_same_either_way: merges removals, insertions and moves
    test_insertions_at_one_place_follow_natural_sort: orders concurrent runs by natural sort
    test_a_run_follows_an_anchor_the_other_side_moved: lands after the anchor's new place
    test_a_run_after_a_removed_anchor_lands_after_its_base_predecessor: passes the anchor back
    test_edits_that_cannot_combine_conflict: names each element and why, the same either way
    test_pin_paths_merge_as_elements: merges tuple elements and names them with slashes
"""

import pytest

from e8s.show.order_merge import Element, OrderConflict, merge_order

BASE = ["1", "2", "3", "4"]
COMBINING: dict[str, tuple[list[str], list[str], tuple[str, ...]]] = {
    "insert and remove": (["1", "1A", "2", "3", "4"], ["1", "2", "4"], ("1", "1A", "2", "4")),
    "move and append": (["2", "1", "3", "4"], [*BASE, "5"], ("2", "1", "3", "4", "5")),
    "both remove one": (["1", "3", "4"], ["1", "3", "4", "5"], ("1", "3", "4", "5")),
    "same insertion": (["1", "9", "2", "3", "4"], ["1", "9", "2", "3"], ("1", "9", "2", "3")),
}
CONFLICTING: dict[str, tuple[list[str], list[str], str]] = {
    "moved and removed": (["2", "3", "4", "1"], ["2", "3", "4"], "1 moved on one side and removed"),
    "placed differently": (["1", "9", *BASE[1:]], [*BASE, "9"], "9 placed differently"),
    "moved after each other": (["2", "3", "1", "4"], ["1", "3", "2", "4"], "moved after each"),
    "listed twice": (["1", "1", "2"], BASE, "an order lists one element twice"),
}


@pytest.mark.parametrize("edits", COMBINING.values(), ids=COMBINING.keys())
def test_edits_that_combine_merge_the_same_either_way(
    edits: tuple[list[str], list[str], tuple[str, ...]],
) -> None:
    ours, theirs, merged = edits
    assert merge_order(BASE, ours, theirs) == merged
    assert merge_order(BASE, theirs, ours) == merged


def test_insertions_at_one_place_follow_natural_sort() -> None:
    ours, theirs = ["1", "10", "2"], ["1", "9", "2"]
    assert merge_order(["1", "2"], ours, theirs) == ("1", "9", "10", "2")
    assert merge_order(["1", "2"], theirs, ours) == ("1", "9", "10", "2")


def test_a_run_follows_an_anchor_the_other_side_moved() -> None:
    added = [*BASE, "5"]
    moved = ["4", "1", "2", "3"]
    assert merge_order(BASE, added, moved) == ("4", "5", "1", "2", "3")
    assert merge_order(BASE, moved, added) == ("4", "5", "1", "2", "3")


def test_a_run_after_a_removed_anchor_lands_after_its_base_predecessor() -> None:
    assert merge_order(BASE, ["1", "2", "3", "3A", "4"], ["1", "4"]) == ("1", "3A", "4")
    assert merge_order(BASE, ["1A", *BASE], ["2", "3", "4"]) == ("1A", "2", "3", "4")


@pytest.mark.parametrize("edits", CONFLICTING.values(), ids=CONFLICTING.keys())
def test_edits_that_cannot_combine_conflict(edits: tuple[list[str], list[str], str]) -> None:
    ours, theirs, reason = edits
    for found in (merge_order(BASE, ours, theirs), merge_order(BASE, theirs, ours)):
        assert isinstance(found, OrderConflict)
        assert reason in found.reason


def test_pin_paths_merge_as_elements() -> None:
    spot: Element = ("entity", "T1")
    form: Element = ("form", "f1")
    assert merge_order([spot], [spot, form], []) == (form,)
    found = merge_order([spot, form], [form, spot], [spot])
    assert isinstance(found, OrderConflict)
    assert found.reason == "form/f1 moved on one side and removed on the other"
