#!/usr/bin/env python3

"""
test_encode_cache.py --- tests that saves encode only the documents whose sources changed

Contains:
    test_one_spot_save_encodes_only_the_edited_set_documents: a saved show edited at one spot
        encodes the edited set's set and role documents and nothing else, show.json included
    test_put_show_encodes_through_the_repository_cache: put_show reuses the cache save_show filled
    test_cache_never_returns_bytes_for_a_reused_identity: fresh sets made and dropped one after
        another always encode to their own bytes, with and without eviction
    test_cache_keeps_the_sources_of_each_cached_group_alive: a cached set outlives the show it
        came from, so its identity cannot be reused, and is released once its group is evicted
    test_changing_one_source_encodes_only_its_documents: a new form, transition, sheet, timeline,
        cast or set order encodes exactly its own document, equal to a fresh encode
    test_cache_stays_under_its_byte_cap: evicts least recently used groups to stay within the cap
    test_cache_stores_no_group_larger_than_its_cap: still returns the documents, caching none
    test_encode_documents_returns_only_the_named_documents: picks documents out of their groups
"""

import gc
import weakref
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import pytest

from e8s.choreo.form_ops import edit
from e8s.choreo.models import PlacementPatch
from e8s.show.document_paths import role_docs, set_doc
from e8s.show.document_repository import encode_cache_of
from e8s.show.documents import encode_documents, encode_show
from e8s.show.encode_cache import EncodeCache
from e8s.show.memory_backend import InMemoryRepository
from e8s.show.models import Show
from e8s.show.set_keys import set_key
from e8s.show.show_io import save_show
from e8s.show.workspace import open_workspace
from tests.fixtures.generators import seeded_show

SHOW_ID = "encoded-show"
AT = datetime(2026, 11, 24, 12, 0, tzinfo=UTC)
STEP = timedelta(seconds=1)
SEED = 5
SPOTS = 8
SETS = 6
EDITED_SET = 2
ROUNDS = 40
SMALL_CAP_BYTES = 20_000
TINY_CAP_BYTES = 1
GROUP = "brass"


def _one_spot_edit(show: Show, x: float) -> Show:
    entity = show.cast.ids()[0]
    edited = show.sets[EDITED_SET].with_placements({entity: PlacementPatch(x=x)}, force=True)
    return show.replace(sets=(*show.sets[:EDITED_SET], edited, *show.sets[EDITED_SET + 1 :]))


def _repository_cache(repo: InMemoryRepository) -> EncodeCache:
    cache = encode_cache_of(repo)
    assert cache is not None
    return cache


def _edited_set_documents(show: Show) -> set[str]:
    folder = set_key(show.sets[EDITED_SET])
    return {set_doc(folder), *role_docs(folder)}


def test_one_spot_save_encodes_only_the_edited_set_documents() -> None:
    repo = InMemoryRepository()
    ws = open_workspace(repo, SHOW_ID)
    show = seeded_show(SEED, SPOTS, SETS)
    saved = save_show(ws, show, AT)
    cache = _repository_cache(repo)
    before = cache.misses
    edited = _one_spot_edit(show, -1.0).replace(revision=saved.id)
    revision = save_show(ws, edited, AT + STEP)
    assert cache.misses - before == len(_edited_set_documents(show))
    assert set(revision.changed) <= _edited_set_documents(show)
    assert revision.changed


def test_put_show_encodes_through_the_repository_cache() -> None:
    repo = InMemoryRepository()
    show = seeded_show(SEED, SPOTS, SETS)
    saved = repo.put_show(SHOW_ID, show, expected_revision="", at=AT)
    cache = _repository_cache(repo)
    before = cache.misses
    repo.put_show(SHOW_ID, _one_spot_edit(show, -1.0), expected_revision=saved.id, at=AT + STEP)
    assert cache.misses - before == len(_edited_set_documents(show))


@pytest.mark.parametrize("cap", [None, SMALL_CAP_BYTES])
def test_cache_never_returns_bytes_for_a_reused_identity(cap: int | None) -> None:
    cache = EncodeCache() if cap is None else EncodeCache(cap)
    base = seeded_show(SEED, SPOTS, SETS)
    for n in range(ROUNDS):
        edited = _one_spot_edit(base, float(n))
        assert cache.documents(edited) == encode_show(edited)


def test_cache_keeps_the_sources_of_each_cached_group_alive() -> None:
    show = _one_spot_edit(seeded_show(SEED, SPOTS, SETS), -1.0)
    cache = EncodeCache(sum(len(raw) for raw in encode_show(show).values()))
    cache.documents(show)
    edited = weakref.ref(show.sets[EDITED_SET])
    rebuilt = Show.from_json(show.to_json())
    del show
    gc.collect()
    assert edited() is not None
    cache.documents(rebuilt)
    gc.collect()
    assert edited() is None
    assert cache.nbytes <= cache.max_bytes


def _new_form(show: Show) -> Show:
    form_id = min(show.forms)
    form = show.forms[form_id]
    return show.replace(
        forms={**show.forms, form_id: edit(form, force=True, locked=not form.locked)}
    )


def _new_transition(show: Show) -> Show:
    first, *rest = show.transitions
    return show.replace(transitions=(first.replace(notes="moved"), *rest))


def _new_sheet(show: Show) -> Show:
    return show.replace(sheet=show.sheet.replace(name="Renamed field"))


def _new_timeline(show: Show) -> Show:
    first, *rest = show.timeline.tempos
    tempos = (first.replace(counts_per_minute=first.counts_per_minute + 1.0), *rest)
    return show.replace(timeline=show.timeline.replace(tempos=tempos))


def _new_cast(show: Show) -> Show:
    groups = {**show.cast.groups, GROUP: show.cast.groups[GROUP].replace(name="Horns")}
    return show.replace(cast=show.cast.replace(groups=groups))


def _new_set_order(show: Show) -> Show:
    return show.replace(sets=tuple(reversed(show.sets)), transitions=())


@pytest.mark.parametrize(
    "change",
    [_new_form, _new_transition, _new_sheet, _new_timeline, _new_cast, _new_set_order],
    ids=["forms", "paths", "sheet", "timeline", "cast", "set-order"],
)
def test_changing_one_source_encodes_only_its_documents(change: Callable[[Show], Show]) -> None:
    cache = EncodeCache()
    show = seeded_show(SEED, SPOTS, SETS)
    cache.documents(show)
    before = cache.misses
    changed = change(show)
    assert cache.documents(changed) == encode_show(changed)
    assert cache.misses - before == 1


def test_cache_stays_under_its_byte_cap() -> None:
    cache = EncodeCache(SMALL_CAP_BYTES)
    show = seeded_show(SEED, SPOTS, SETS)
    for n in range(ROUNDS):
        show = _one_spot_edit(show, float(n))
        cache.documents(show)
        assert 0 < cache.nbytes <= SMALL_CAP_BYTES
    assert len(cache) > 0


def test_cache_stores_no_group_larger_than_its_cap() -> None:
    cache = EncodeCache(TINY_CAP_BYTES)
    show = seeded_show(SEED, SPOTS, SETS)
    assert cache.documents(show) == encode_show(show)
    assert (len(cache), cache.nbytes) == (0, 0)
    assert cache.misses == len(encode_show(show))


def test_encode_documents_returns_only_the_named_documents() -> None:
    show = seeded_show(SEED, SPOTS, SETS)
    wanted = sorted(_edited_set_documents(show))[:2]
    assert encode_documents(show, wanted) == {p: encode_show(show)[p] for p in wanted}
