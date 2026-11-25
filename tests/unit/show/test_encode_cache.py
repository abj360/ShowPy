#!/usr/bin/env python3

"""
test_encode_cache.py --- tests that saves encode only the documents whose sources changed

Contains:
    test_one_spot_save_encodes_only_the_edited_set_documents: a saved show edited at one spot
        encodes the edited set's set and role documents and nothing else, show.json included
    test_put_show_encodes_through_the_repository_cache: put_show reuses the cache save_show filled
    test_cache_never_returns_bytes_for_a_reused_identity: fresh sets made and dropped one after
        another always encode to their own bytes, with and without eviction
    test_cache_stays_under_its_byte_cap: evicts least recently used groups to stay within the cap
    test_cache_stores_no_group_larger_than_its_cap: still returns the documents, caching none
    test_encode_documents_returns_only_the_named_documents: picks documents out of their groups
"""

from datetime import UTC, datetime, timedelta

import pytest

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
