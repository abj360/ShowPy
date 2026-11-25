#!/usr/bin/env python3

"""
test_merge_properties.py --- checks the three-way merge over random edits of a seeded show

Contains:
    test_merge_with_an_unchanged_side_returns_the_other: returns the edited side itself
    test_merge_is_symmetric_in_ours_and_theirs: gives one show, or the same conflicts with the
        sides swapped, whichever side is which
    test_clean_merge_keeps_every_change_one_side_made: keeps each item only one side changed
"""

from functools import cache

from hypothesis import given
from hypothesis import strategies as st

from e8s.cast.spots import delete_spots
from e8s.choreo.models import PlacementPatch
from e8s.core.readonly import freeze_json
from e8s.show.items import changes, items
from e8s.show.manage import add_set, remove_set, replace_sets
from e8s.show.merge import merge
from e8s.show.models import Show
from e8s.show.pinning import pin
from tests.fixtures.generators import seeded_show

SEEDED_SPOTS = 6
SEEDED_SETS = 3
EDIT_KINDS = 10
ADDED_SET_IDS = ("9", "9A", "9B")
seeds = st.integers(min_value=0, max_value=3)
edits = st.lists(
    st.tuples(
        st.integers(min_value=0, max_value=EDIT_KINDS - 1),
        st.integers(min_value=0, max_value=60),
        st.floats(min_value=-20.0, max_value=20.0, allow_nan=False),
    ),
    max_size=3,
)


@cache
def _base(seed: int) -> Show:
    return seeded_show(seed, SEEDED_SPOTS, SEEDED_SETS)


def _patched(show: Show, position: int, spot: str, patch: PlacementPatch) -> Show:
    edited = show.sets[position].with_placements({spot: patch}, force=True)
    return show.replace(sets=tuple(edited if i == position else s for i, s in enumerate(show.sets)))


def _structural(show: Show, kind: int, pick: int, spot: str) -> Show:
    sets = show.sets
    match kind:
        case 4:
            set_id = ADDED_SET_IDS[pick % len(ADDED_SET_IDS)]
            return show if set_id in show.set_ids() else add_set(show, set_id, 4.0)
        case 5:
            return remove_set(show, sets[-1].id, force=True) if len(sets) > 1 else show
        case 6:
            spots = tuple(reversed(show.cast.spots))
            return show.replace(cast=show.cast.replace(spots=spots))
        case 7:
            return pin(show, "entity", spot)
        case 8:
            return replace_sets(show, (*sets[1:2], sets[0], *sets[2:]), force=True)
        case _:
            return delete_spots(show, spot, force=True) if len(show.cast.spots) > 1 else show


def _edited(show: Show, kind: int, pick: int, value: float) -> Show:
    position = pick % len(show.sets)
    placements = show.sets[position].placements
    spot = placements[pick % len(placements)].entity
    match kind:
        case 0:
            return _patched(show, position, spot, PlacementPatch(x=value))
        case 1:
            return _patched(show, position, spot, PlacementPatch(facing=abs(value)))
        case 2:
            return _patched(show, position, spot, PlacementPatch(note=f"note {pick % 3}"))
        case 3:
            return show.replace(title=f"title {pick % 3}")
        case _:
            return _structural(show, kind, pick, spot)


def _applied(show: Show, steps: list[tuple[int, int, float]]) -> Show:
    for kind, pick, value in steps:
        show = _edited(show, kind, pick, value)
    return show


@given(seeds, edits)
def test_merge_with_an_unchanged_side_returns_the_other(
    seed: int, steps: list[tuple[int, int, float]]
) -> None:
    base = _base(seed)
    edited = _applied(base, steps)
    copy = Show.from_json(base.to_json())
    assert merge(base, edited, copy).show is edited
    returned = merge(base, copy, edited).show
    assert returned is (edited if changes(base, edited).changes else copy)


@given(seeds, edits, edits)
def test_merge_is_symmetric_in_ours_and_theirs(
    seed: int, our_steps: list[tuple[int, int, float]], their_steps: list[tuple[int, int, float]]
) -> None:
    base = _base(seed)
    ours, theirs = _applied(base, our_steps), _applied(base, their_steps)
    forward, backward = merge(base, ours, theirs), merge(base, theirs, ours)
    swapped = tuple(c.replace(ours=c.theirs, theirs=c.ours) for c in forward.conflicts)
    assert backward.conflicts == swapped
    keys = [c.key.sort_key() for c in forward.conflicts]
    assert keys == sorted(set(keys))
    assert forward.show == backward.show


@given(seeds, edits, edits)
def test_clean_merge_keeps_every_change_one_side_made(
    seed: int, our_steps: list[tuple[int, int, float]], their_steps: list[tuple[int, int, float]]
) -> None:
    base = _base(seed)
    ours, theirs = _applied(base, our_steps), _applied(base, their_steps)
    merged = merge(base, ours, theirs).show
    if merged is None:
        return
    mine, other = changes(base, ours).changes, changes(base, theirs).changes
    found = items(merged)
    for side, rest in ((mine, other), (other, mine)):
        shared = {c.key.sort_key() for c in rest}
        for change in side:
            if change.key.sort_key() not in shared:
                assert freeze_json(found.get(change.key)) == change.after
