#!/usr/bin/env python3

"""
test_show_properties.py --- checks set, index, storage, rename and change invariants on random shows
"""

from hypothesis import given
from hypothesis import strategies as st

from e8s.cast.spots import rename_spots
from e8s.choreo.models import PlacementPatch
from e8s.core.readonly import freeze_json
from e8s.core.util import approx_equal
from e8s.show.documents import decode_show, encode_show
from e8s.show.items import changes, items
from e8s.show.manage import add_set, insert_subset
from e8s.show.models import Show
from e8s.show.renumber import renumber
from e8s.show.set_queries import index
from tests.fixtures.generators import seeded_show
from tests.fixtures.shows import two_set_show

TOL = 1e-9
counts_strategy = st.floats(min_value=0.5, max_value=32.0, allow_nan=False)
coordinate = st.floats(min_value=-20.0, max_value=20.0, allow_nan=False)
seeds = st.integers(min_value=0, max_value=2**16)
SEEDED_SPOTS = 6
SEEDED_SETS = 3


def _show(extra_counts: list[float]) -> Show:
    show = two_set_show()
    for n, counts in enumerate(extra_counts, start=3):
        show = add_set(show, str(n), counts)
    return show


@given(st.lists(counts_strategy, max_size=4), st.floats(min_value=0.01, max_value=0.99))
def test_insert_subset_keeps_total_time(extra: list[float], share: float) -> None:
    show = _show(extra)
    total = index(show).total_counts
    after = show.sets[0].id
    edited, new_id = insert_subset(show, after, show.sets[1].counts * share)
    assert approx_equal(index(edited).total_counts, total, abs_tol=1e-6)
    assert edited.set_ids()[1] == new_id


@given(st.lists(counts_strategy, max_size=4), st.floats(min_value=0.0, max_value=1.0))
def test_count_is_at_a_set_or_inside_one_transition(extra: list[float], share: float) -> None:
    shown = index(_show(extra))
    count = shown.total_counts * share
    at_set, inside = shown.set_at(count), shown.transition_at(count)
    assert (at_set is None) != (inside is None)
    if inside is not None:
        (a, b), tau = inside
        assert 0 < tau < next(e.counts for e in shown.entries if e.set == b)
        assert shown.next(a) == b


@given(st.lists(st.tuples(coordinate, coordinate), min_size=4, max_size=4))
def test_documents_round_trip(points: list[tuple[float, float]]) -> None:
    show = two_set_show()
    updates = {f"T{i}": PlacementPatch(x=x, y=y) for i, (x, y) in enumerate(points, start=1)}
    moved = show.sets[1].with_placements(updates)
    edited = show.replace(sets=(show.sets[0], moved))
    assert decode_show(encode_show(edited)) == edited


@given(st.lists(counts_strategy, max_size=4))
def test_renumber_is_sequential_and_keeps_content(extra: list[float]) -> None:
    show = _show(extra)
    sub, _ = insert_subset(show, "1", show.sets[1].counts / 2)
    renamed, mapping = renumber(sub)
    assert renamed.set_ids() == [str(n) for n in range(1, len(sub.sets) + 1)]
    assert [s.placements for s in renamed.sets] == [s.placements for s in sub.sets]
    assert list(mapping) == sub.set_ids()


@given(st.permutations(["T1", "T2", "T3", "T4"]))
def test_rename_then_inverse_is_identity(order: list[str]) -> None:
    show = two_set_show()
    mapping = dict(zip(["T1", "T2", "T3", "T4"], order, strict=True))
    inverse = {new: old for old, new in mapping.items()}
    renamed, _ = rename_spots(show, mapping)
    assert rename_spots(renamed, inverse)[0] == show


def _edited(show: Show, edit: int, other: int, point: tuple[float, float]) -> Show:
    position = other % len(show.sets)
    current = show.sets[position]
    spot = current.placements[other % len(current.placements)].entity
    moved = current.with_placements({spot: PlacementPatch(x=point[0], y=point[1])}, force=True)
    sets = tuple(moved if i == position else s for i, s in enumerate(show.sets))
    edits = (
        show,
        show.replace(sets=sets),
        show.replace(title=f"{show.title} {other}"),
        seeded_show(other, SEEDED_SPOTS, SEEDED_SETS),
    )
    return edits[edit % len(edits)]


@given(seeds, st.integers(min_value=0, max_value=3), seeds, st.tuples(coordinate, coordinate))
def test_changes_lists_exactly_the_items_that_differ(
    seed: int, edit: int, other: int, point: tuple[float, float]
) -> None:
    before = seeded_show(seed, SEEDED_SPOTS, SEEDED_SETS)
    after = _edited(before, edit, other, point)
    old, new = items(before), items(after)
    keys = [*old, *(key for key in new if key not in old)]
    differ = [key for key in keys if old.get(key) != new.get(key)]
    found = changes(before, after).changes
    assert [c.key for c in found] == sorted(differ, key=lambda key: key.sort_key())
    assert all(c.before == freeze_json(old.get(c.key)) for c in found)
    assert all(c.after == freeze_json(new.get(c.key)) for c in found)
