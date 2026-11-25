#!/usr/bin/env python3

"""
test_storage_encoding.py --- checks that cached encoding always equals a fresh encode

Contains:
    test_cached_documents_equal_a_fresh_encode_after_random_edits: one cache, a bounded or an
        unbounded one, serves a chain of random edits, rebuilds and reverts with fresh bytes
"""

from hypothesis import given
from hypothesis import strategies as st

from e8s.choreo.models import PlacementPatch
from e8s.show.documents import encode_show
from e8s.show.encode_cache import ENCODE_CACHE_BYTES, EncodeCache
from e8s.show.models import Show
from tests.fixtures.generators import seeded_show

SPOTS = 6
SETS = 4
SMALL_CAP_BYTES = 8_000
MAX_EDITS = 8
EXTENSION_KEY = "x-note"

seeds = st.integers(min_value=0, max_value=2**16)
coordinates = st.floats(min_value=-20.0, max_value=20.0, allow_nan=False, allow_infinity=False)
json_scalars = st.one_of(
    st.booleans(), st.integers(-3, 3), st.sampled_from([0.0, -0.0, 1.0]), st.text(max_size=3)
)
moves = st.tuples(st.just("move"), st.integers(0, SETS - 1), st.integers(0, SPOTS - 1), coordinates)
titles = st.tuples(st.just("title"), st.text(min_size=1, max_size=8))
extensions = st.tuples(st.just("extend"), st.lists(json_scalars, max_size=3))
rebuilds = st.tuples(st.just("rebuild"))
reverts = st.tuples(st.just("revert"), st.integers(0, MAX_EDITS))
edits = st.lists(st.one_of(moves, titles, extensions, rebuilds, reverts), max_size=MAX_EDITS)


def _move(show: Show, set_index: int, spot_index: int, x: float) -> Show:
    entity = show.cast.ids()[spot_index]
    moved = show.sets[set_index].with_placements({entity: PlacementPatch(x=x)}, force=True)
    return show.replace(sets=(*show.sets[:set_index], moved, *show.sets[set_index + 1 :]))


def _edited(show: Show, edit: tuple[object, ...], history: list[Show]) -> Show:
    """Applies one generated edit; a revert returns an earlier show object itself."""
    match edit:
        case ("move", int(set_index), int(spot_index), float(x)):
            return _move(show, set_index, spot_index, x)
        case ("title", str(title)):
            return show.replace(title=title)
        case ("extend", list(values)):
            return show.replace(extensions={EXTENSION_KEY: values})
        case ("revert", int(back)):
            return history[back % len(history)]
        case _:
            return Show.from_json(show.to_json())


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
