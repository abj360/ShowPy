#!/usr/bin/env python3

"""
test_backend.py --- tests the backend protocol: head, log(since=), the memory index and the layout

Contains:
    test_memory_head_and_log_since_cost_does_not_grow_with_history: reads two entries of 1,000
    test_regression_failed_first_commit_leaves_no_phantom_show: lists no show after a failed commit
    test_head_and_log_since: checks both adapters agree on head and log(since=)
    test_commit_with_unknown_parent_reports_every_landed_revision: keeps the memory conflict
"""

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    ConflictError,
    E8sError,
    SchemaError,
    UnknownRefError,
)
from e8s.show.backend import RepositoryBackend
from e8s.show.document_repository import DocumentRepository
from e8s.show.file_repository import FileRepository
from e8s.show.history_models import Revision
from e8s.show.memory_backend import InMemoryRepository, _MemoryBackend
from e8s.show.storage import ROOT_REVISION
from tests.helpers import context_entries

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
SHOW_ID = "show-1"
HISTORY = 1_000

type BackendFactory = Callable[[], RepositoryBackend]


def _revision_id(position: int) -> str:
    return f"{position:064x}"


def _fill(backend: RepositoryBackend, count: int) -> list[str]:
    ids: list[str] = []
    parent = ROOT_REVISION
    for position in range(1, count + 1):
        revision = Revision(id=_revision_id(position), parent=parent, at=AT, changed=("/a",))
        backend.commit(SHOW_ID, revision, {}, {})
        ids.append(revision.id)
        parent = revision.id
    return ids


class _CountingIndex(dict[tuple[str, str], Revision]):
    """Counts the revisions read by key."""

    reads = 0

    def __getitem__(self, key: tuple[str, str]) -> Revision:
        self.reads += 1
        return super().__getitem__(key)


def test_memory_head_and_log_since_cost_does_not_grow_with_history() -> None:
    backend = _MemoryBackend()
    ids = _fill(backend, HISTORY)
    index = _CountingIndex(backend._revisions)
    backend._revisions = index
    landed = backend.log(SHOW_ID, since=ids[-3])
    assert [r.id for r in landed] == ids[-2:]
    assert index.reads == 2
    index.reads = 0
    assert backend.head(SHOW_ID) == ids[-1]
    assert index.reads == 0


def test_regression_failed_first_commit_leaves_no_phantom_show() -> None:
    repo = InMemoryRepository()
    with pytest.raises(E8sError):
        repo.transaction(SHOW_ID, {"/not/a/stored/path": b"x"}, expected_revision="", at=AT)
    with pytest.raises(SchemaError):
        repo.transaction(SHOW_ID, {"/show/show.json": b"{"}, expected_revision="", at=AT)
    assert repo.shows() == []
    assert repo.backend.shows() == []


def test_regression_conflicting_first_commit_leaves_no_phantom_show() -> None:
    backend = _MemoryBackend()
    stray = Revision(id=_revision_id(2), parent=_revision_id(1), at=AT)
    with pytest.raises(ConflictError):
        backend.commit(SHOW_ID, stray, {}, {})
    assert backend.shows() == []
    assert backend.head(SHOW_ID) == ROOT_REVISION


@pytest.fixture(params=["memory", "file"])
def backend_factory(request: pytest.FixtureRequest, tmp_path: Path) -> BackendFactory:
    if request.param == "memory":
        return lambda: InMemoryRepository().backend
    return lambda: FileRepository(tmp_path / "repo").backend


def test_head_and_log_since(backend_factory: BackendFactory) -> None:
    backend = backend_factory()
    assert backend.head(SHOW_ID) == ROOT_REVISION
    assert backend.log(SHOW_ID) == []
    assert backend.log(SHOW_ID, since=ROOT_REVISION) == []
    ids = _fill(backend, 4)
    assert backend.shows() == [SHOW_ID]
    assert backend.head(SHOW_ID) == ids[-1]
    assert [r.id for r in backend.log(SHOW_ID)] == ids
    assert [r.id for r in backend.log(SHOW_ID, since=ROOT_REVISION)] == ids
    assert [r.id for r in backend.log(SHOW_ID, since=ids[1])] == ids[2:]
    assert backend.log(SHOW_ID, since=ids[-1]) == []


def test_log_since_an_unknown_revision_fails_closed(backend_factory: BackendFactory) -> None:
    backend = backend_factory()
    ids = _fill(backend, 3)
    missing = _revision_id(99)
    with pytest.raises(UnknownRefError) as caught:
        backend.log(SHOW_ID, since=missing)
    assert caught.value.code == ErrorCode.UNKNOWN_REF
    assert caught.value.context["revision"] == missing
    assert "head()" in caught.value.hint
    with pytest.raises(UnknownRefError):
        backend.log("other-show", since=ids[0])


def test_repository_write_on_an_unknown_expected_revision_is_unknown_ref(
    backend_factory: BackendFactory,
) -> None:
    repo = DocumentRepository(backend_factory())
    first = repo.transaction(SHOW_ID, {"/renders/a.txt": b"a"}, expected_revision="", at=AT)
    with pytest.raises(UnknownRefError):
        repo.transaction(
            SHOW_ID, {"/renders/b.txt": b"b"}, expected_revision=_revision_id(99), at=AT
        )
    assert repo.head(SHOW_ID) == first.id


def test_commit_with_unknown_parent_reports_every_landed_revision() -> None:
    backend = _MemoryBackend()
    ids = _fill(backend, 3)
    stray = Revision(id=_revision_id(50), parent=_revision_id(49), at=AT)
    with pytest.raises(ConflictError) as caught:
        backend.commit(SHOW_ID, stray, {}, {})
    assert caught.value.context["head"] == ids[-1]
    assert len(context_entries(caught.value, "changes")) == 3
    assert backend.head(SHOW_ID) == ids[-1]
