#!/usr/bin/env python3

"""
test_workspace_cache.py --- tests that a workspace decodes unchanged documents once across revisions

Contains:
    test_regression_show_at_reuses_unchanged_documents_across_revisions: parses only the changed
        and uncached documents of each later revision, not the whole tree
    test_show_at_never_serves_a_changed_document_from_the_cache: every revision decodes equal to
        a fresh decode and carries its own edited placement
    test_regression_workspace_cache_is_bounded: holds at most WORKSPACE_DECODE_CACHE_SIZE
        documents, a few trees' worth, never the repository-wide bound
"""

from datetime import UTC, datetime, timedelta

import pytest

from e8s.choreo.models import PlacementPatch
from e8s.core.json_types import JsonValue
from e8s.core.strict_json import parse_strict_json
from e8s.show import storage
from e8s.show.decode_cache import DECODE_CACHE_SIZE
from e8s.show.documents import decode_show
from e8s.show.models import Show
from e8s.show.repositories import InMemoryRepository
from e8s.show.storage import load_show, open_workspace, save_show
from tests.fixtures.generators import seeded_show

SHOW_ID = "cached-show"
AT = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)
STEP = timedelta(seconds=1)
SEED = 3
SPOTS = 8
SETS = 10
EDITS = 5
EDITED_SET = 1
PARSER = "e8s.show.document_reading.parse_strict_json"


def _one_spot_edit(show: Show, x: float) -> Show:
    entity = show.cast.ids()[0]
    edited = show.sets[EDITED_SET].with_placements({entity: PlacementPatch(x=x)}, force=True)
    return show.replace(sets=(*show.sets[:EDITED_SET], edited, *show.sets[EDITED_SET + 1 :]))


def _history() -> InMemoryRepository:
    """Saves a show, then EDITS revisions that each move one spot in one set."""
    repo = InMemoryRepository()
    save_show(open_workspace(repo, SHOW_ID), seeded_show(SEED, SPOTS, SETS), AT)
    for n in range(EDITS):
        ws = open_workspace(repo, SHOW_ID)
        save_show(ws, _one_spot_edit(load_show(ws), float(n + 1)), AT + STEP * (n + 1))
    return repo


def test_regression_show_at_reuses_unchanged_documents_across_revisions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = _history()
    parses: list[str] = []

    def counted(raw: bytes, *, document: str) -> JsonValue:
        parses.append(document)
        return parse_strict_json(raw, document=document)

    monkeypatch.setattr(PARSER, counted)
    ws = open_workspace(repo, SHOW_ID)
    counts = []
    for revision in repo.revisions(SHOW_ID):
        parses.clear()
        ws.show_at(revision.id)
        counts.append(len(parses))
    first, *later = counts
    assert len(later) == EDITS
    assert all(count <= first // 4 for count in later), counts


def test_show_at_never_serves_a_changed_document_from_the_cache() -> None:
    repo = _history()
    ws = open_workspace(repo, SHOW_ID)
    entity = seeded_show(SEED, SPOTS, SETS).cast.ids()[0]
    for n, revision in enumerate(repo.revisions(SHOW_ID)):
        show = ws.show_at(revision.id)
        assert show is not None
        assert show == decode_show(repo.documents(SHOW_ID, revision.id), revision.id)
        if n:
            placed = {p.entity: p for p in show.sets[EDITED_SET].placements}
            assert placed[entity].x == float(n)


def test_regression_workspace_cache_is_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    assert storage.WORKSPACE_DECODE_CACHE_SIZE < DECODE_CACHE_SIZE
    repo = _history()
    bound = 4
    monkeypatch.setattr(storage, "WORKSPACE_DECODE_CACHE_SIZE", bound)
    ws = open_workspace(repo, SHOW_ID)
    for revision in repo.revisions(SHOW_ID):
        ws.show_at(revision.id)
    assert len(ws._decode_cache) == bound
