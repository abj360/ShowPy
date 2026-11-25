#!/usr/bin/env python3

"""
test_storage_encoding.py --- checks that cached encoding always equals a fresh encode

Contains:
    test_cached_documents_equal_a_fresh_encode_after_random_edits: one cache, a bounded or an
        unbounded one, serves a chain of random edits to every document's sources, rebuilds and
        reverts with fresh bytes
"""

from collections.abc import Callable, Sequence

from hypothesis import given
from hypothesis import strategies as st

from e8s.choreo.form_ops import edit
from e8s.choreo.models import PlacementPatch
from e8s.show.documents import encode_show
from e8s.show.encode_cache import ENCODE_CACHE_BYTES, EncodeCache
from e8s.show.manage import add_set, remove_set, replace_sets
from e8s.show.models import Show
from tests.fixtures.generators import seeded_show

SPOTS = 6
SETS = 4
SMALL_CAP_BYTES = 8_000
MAX_EDITS = 8
EXTENSION_KEY = "x-note"
ADDED_SET_COUNTS = 8.0
NAMES = ("Alpha", "Beta", "Gamma")

seeds = st.integers(min_value=0, max_value=2**16)
indexes = st.integers(min_value=0, max_value=31)
names = st.sampled_from(NAMES)
coordinates = st.floats(min_value=-20.0, max_value=20.0, allow_nan=False, allow_infinity=False)
tempos = st.floats(min_value=60.0, max_value=200.0, allow_nan=False, allow_infinity=False)
json_scalars = st.one_of(
    st.booleans(), st.integers(-3, 3), st.sampled_from([0.0, -0.0, 1.0]), st.text(max_size=3)
)


def _at[T](items: Sequence[T], index: int) -> T:
    return items[index % len(items)]


def _move(show: Show, set_index: int, spot_index: int, x: float) -> Show:
    set_index %= len(show.sets)
    entity = _at(show.cast.ids(), spot_index)
    moved = show.sets[set_index].with_placements({entity: PlacementPatch(x=x)}, force=True)
    return show.replace(sets=(*show.sets[:set_index], moved, *show.sets[set_index + 1 :]))


def _retitle(show: Show, title: str) -> Show:
    return show.replace(title=title)


def _extend(show: Show, values: list[object]) -> Show:
    return show.replace(extensions={EXTENSION_KEY: values})


def _toggle_form_lock(show: Show, index: int) -> Show:
    if not show.forms:
        return show
    form = show.forms[_at(sorted(show.forms), index)]
    edited = edit(form, force=True, locked=not form.locked)
    return show.replace(forms={**show.forms, form.id: edited})


def _annotate_transition(show: Show, index: int, notes: str) -> Show:
    if not show.transitions:
        return show
    index %= len(show.transitions)
    specs = list(show.transitions)
    specs[index] = specs[index].replace(notes=notes)
    return show.replace(transitions=tuple(specs))


def _drop_transition(show: Show, index: int) -> Show:
    if not show.transitions:
        return show
    index %= len(show.transitions)
    return show.replace(transitions=show.transitions[:index] + show.transitions[index + 1 :])


def _rename_sheet(show: Show, name: str) -> Show:
    return show.replace(sheet=show.sheet.replace(name=name))


def _retempo(show: Show, counts_per_minute: float) -> Show:
    first, *rest = show.timeline.tempos
    retimed = (first.replace(counts_per_minute=counts_per_minute), *rest)
    return show.replace(timeline=show.timeline.replace(tempos=retimed))


def _rename_group(show: Show, index: int, name: str) -> Show:
    key = _at(sorted(show.cast.groups), index)
    groups = {**show.cast.groups, key: show.cast.groups[key].replace(name=name)}
    return show.replace(cast=show.cast.replace(groups=groups))


def _add_set(show: Show, index: int) -> Show:
    new_id = f"a{len(show.sets)}x{index}"
    if new_id in show.set_ids():
        return show
    return add_set(show, new_id, ADDED_SET_COUNTS, after=_at(show.set_ids(), index))


def _remove_set(show: Show, index: int) -> Show:
    if len(show.sets) < 2:
        return show
    return remove_set(show, _at(show.set_ids(), index), force=True)


def _reverse_sets(show: Show) -> Show:
    return replace_sets(show, tuple(reversed(show.sets)), force=True)


def _rebuild(show: Show) -> Show:
    return Show.from_json(show.to_json())


EDITS: dict[str, Callable[..., Show]] = {
    "move": _move,
    "title": _retitle,
    "extend": _extend,
    "form": _toggle_form_lock,
    "paths": _annotate_transition,
    "drop_paths": _drop_transition,
    "sheet": _rename_sheet,
    "timeline": _retempo,
    "cast": _rename_group,
    "add_set": _add_set,
    "remove_set": _remove_set,
    "reverse_sets": _reverse_sets,
    "rebuild": _rebuild,
}
edits = st.lists(
    st.one_of(
        st.tuples(st.just("move"), indexes, indexes, coordinates),
        st.tuples(st.just("title"), names),
        st.tuples(st.just("extend"), st.lists(json_scalars, max_size=3)),
        st.tuples(st.just("form"), indexes),
        st.tuples(st.just("paths"), indexes, names),
        st.tuples(st.just("drop_paths"), indexes),
        st.tuples(st.just("sheet"), names),
        st.tuples(st.just("timeline"), tempos),
        st.tuples(st.just("cast"), indexes, names),
        st.tuples(st.just("add_set"), indexes),
        st.tuples(st.just("remove_set"), indexes),
        st.tuples(st.just("reverse_sets")),
        st.tuples(st.just("rebuild")),
        st.tuples(st.just("revert"), indexes),
    ),
    max_size=MAX_EDITS,
)


def _edited(show: Show, step: tuple[object, ...], history: list[Show]) -> Show:
    """Applies one generated edit; a revert returns an earlier show object itself."""
    name, *args = step
    if name == "revert":
        assert isinstance(args[0], int)
        return history[args[0] % len(history)]
    return EDITS[str(name)](show, *args)


@given(seeds, edits, st.sampled_from([ENCODE_CACHE_BYTES, SMALL_CAP_BYTES]))
def test_cached_documents_equal_a_fresh_encode_after_random_edits(
    seed: int, steps: list[tuple[object, ...]], cap: int
) -> None:
    cache = EncodeCache(cap)
    show = seeded_show(seed, SPOTS, SETS)
    history = [show]
    assert cache.documents(show) == encode_show(show)
    for step in steps:
        show = _edited(show, step, history)
        history.append(show)
        assert cache.documents(show) == encode_show(show)
        assert cache.nbytes <= cap
