#!/usr/bin/env python3

"""
test_id_changes.py --- tests the set and spot ID renames reported between two shows

Contains:
    test_renumber_reports_every_set_rename: reports each set whose ID changed, matched by key
    test_rename_spots_is_reported_when_placements_match: pairs one vanished and one new spot
    test_rename_and_move_is_a_removal_and_an_addition: reports nothing when placements differ
    test_several_renames_pair_only_when_each_is_unambiguous: pairs unique matches, skips twins
    test_added_and_removed_sets_are_not_renames: ignores sets without a counterpart by key
    test_a_show_compared_with_itself_has_no_renames: reports nothing
    test_results_are_sorted: orders both lists
"""

from e8s.cast.spots import rename_spots
from e8s.choreo.models import PlacementPatch
from e8s.examples import two_set_show
from e8s.show.id_changes import id_changes
from e8s.show.manage import insert_subset
from e8s.show.models import Show
from e8s.show.renumber import renumber


def _with_third_set() -> Show:
    return insert_subset(two_set_show(), "1", 2.0)[0]


def _moved(show: Show, spot: str) -> Show:
    edited = [s.with_placements({spot: PlacementPatch(x=99.0)}, force=True) for s in show.sets]
    return show.replace(sets=tuple(edited))


def test_renumber_reports_every_set_rename() -> None:
    show = _with_third_set()
    renumbered, mapping = renumber(show)
    changes = id_changes(show, renumbered)
    assert changes.sets == (("1A", "2"), ("2", "3"))
    assert changes.sets == tuple(sorted((a, b) for a, b in mapping.items() if a != b))
    assert changes.spots == ()


def test_rename_spots_is_reported_when_placements_match() -> None:
    show = two_set_show()
    renamed, _ = rename_spots(show, {"T1": "S1"})
    assert id_changes(show, renamed).spots == (("T1", "S1"),)
    assert id_changes(renamed, show).spots == (("S1", "T1"),)


def test_rename_and_move_is_a_removal_and_an_addition() -> None:
    show = two_set_show()
    renamed, _ = rename_spots(show, {"T1": "S1"})
    assert id_changes(show, _moved(renamed, "S1")).spots == ()


def test_several_renames_pair_only_when_each_is_unambiguous() -> None:
    show = two_set_show()
    renamed, _ = rename_spots(show, {"T1": "S1", "T2": "S2"})
    assert id_changes(show, renamed).spots == (("T1", "S1"), ("T2", "S2"))
    stacked = show.replace(
        sets=tuple(
            s.with_placements({"T2": PlacementPatch(x=0.0, y=s.get("T1").y)}, force=True)
            for s in show.sets
        )
    )
    twins, _ = rename_spots(stacked, {"T1": "S1", "T2": "S2"})
    assert id_changes(stacked, twins).spots == ()


def test_added_and_removed_sets_are_not_renames() -> None:
    show = two_set_show()
    assert id_changes(show, _with_third_set()).sets == ()
    assert id_changes(_with_third_set(), show).sets == ()


def test_a_show_compared_with_itself_has_no_renames() -> None:
    show = two_set_show()
    assert id_changes(show, show) == id_changes(show, show.replace())
    assert id_changes(show, show).sets == ()


def test_results_are_sorted() -> None:
    show = two_set_show()
    renamed, _ = rename_spots(show, {"T1": "S9", "T2": "S1", "T3": "S5"})
    assert id_changes(show, renamed).spots == (("T1", "S9"), ("T2", "S1"), ("T3", "S5"))
