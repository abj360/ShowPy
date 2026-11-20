#!/usr/bin/env python3

"""
test_show_properties.py --- checks set, index, storage and rename invariants on random shows
"""

from hypothesis import given
from hypothesis import strategies as st

from showpy.core.util import approx_equal
from showpy.show.documents import decode_show, encode_show
from showpy.show.history import rename_entities
from showpy.show.manage import add_set, index, insert_subset, renumber
from showpy.show.models import Show
from tests.fixtures.shows import two_set_show

TOL = 1e-9
counts_strategy = st.floats(min_value=0.5, max_value=32.0, allow_nan=False)
coordinate = st.floats(min_value=-20.0, max_value=20.0, allow_nan=False)


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
    updates = {f"T{i}": {"x": x, "y": y} for i, (x, y) in enumerate(points, start=1)}
    moved = show.sets[1].with_placements(updates)
    edited = show.model_copy(update={"sets": (show.sets[0], moved)})
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
    assert rename_entities(rename_entities(show, mapping), inverse) == show
