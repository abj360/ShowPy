#!/usr/bin/env python3

"""
test_backend.py --- tests the backend protocol: head, log(since=), the memory index and the layout

Contains:
    test_memory_head_and_log_since_cost_does_not_grow_with_history: reads two entries of 1,000
    test_regression_failed_first_commit_leaves_no_phantom_show: lists no show after a failed commit
    test_regression_conflicting_first_commit_leaves_no_phantom_show: lists no show after a conflict
    test_head_and_log_since: checks both adapters agree on head and log(since=)
    test_log_since_an_unknown_revision_fails_closed: raises UnknownRefError
    test_repository_write_on_an_unknown_expected_revision_is_unknown_ref: keeps the error
    test_commit_with_unknown_parent_reports_every_landed_revision: keeps the memory conflict
    test_regression_memory_commit_refuses_a_revision_that_is_its_own_parent: refuses a self-parent
    test_regression_memory_commit_refuses_a_revision_id_already_stored: refuses a repeated ID
    test_regression_memory_log_fails_closed_on_a_parent_cycle: raises CORRUPT on a cycle
    test_memory_tree_and_blob_fail_closed_on_unknown_ids: raises on unknown IDs
    test_regression_memory_commit_verifies_its_inputs: checks IDs, blobs and tree digests
    test_memory_commit_accepts_a_tree_over_stored_and_passed_blobs: accepts a valid tree
    test_tree_of_an_unknown_revision_is_unknown_ref: raises UnknownRefError in both adapters
    test_repository_reads_and_writes_at_the_head_touch_a_constant_number_of_revisions:
        reads O(1) entries
    test_repository_conflict_on_a_stale_write_touches_only_the_landed_revisions:
        reads O(landed) entries
"""

from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    ConflictError,
    E8sError,
    SchemaError,
    StorageError,
    UnknownRefError,
)
from e8s.show.backend import RepositoryBackend
from e8s.show.document_repository import DocumentRepository
from e8s.show.file_repository import FileRepository
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest
from e8s.show.memory_backend import InMemoryRepository, _MemoryBackend
from e8s.show.storage import ROOT_REVISION
from tests.helpers import context_entries

AT = datetime(2026, 11, 11, 12, 0, tzinfo=UTC)
SHOW_ID = "show_1"
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


class _CountingIndex(dict[str, Revision]):
    """Counts the revisions read by key."""

    reads = 0

    def __getitem__(self, key: str) -> Revision:
        self.reads += 1
        return super().__getitem__(key)


def test_memory_head_and_log_since_cost_does_not_grow_with_history() -> None:
    backend = _MemoryBackend()
    ids = _fill(backend, HISTORY)
    index = _CountingIndex(backend._revisions[SHOW_ID])
    backend._revisions[SHOW_ID] = index
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


def _commit(backend: RepositoryBackend, revision: Revision, **kwargs: object) -> None:
    tree = cast(Mapping[str, str], kwargs.get("tree", {}))
    blobs = cast(Mapping[str, bytes], kwargs.get("blobs", {}))
    backend.commit(SHOW_ID, revision, tree, blobs)


def test_regression_memory_commit_refuses_a_revision_that_is_its_own_parent() -> None:
    backend = _MemoryBackend()
    ids = _fill(backend, 2)
    loop = Revision(id=ids[-1], parent=ids[-1], at=AT)
    with pytest.raises(StorageError) as caught:
        _commit(backend, loop)
    assert caught.value.code == ErrorCode.BAD_ID
    assert [r.id for r in backend.log(SHOW_ID)] == ids
    assert backend.head(SHOW_ID) == ids[-1]


def test_regression_memory_commit_refuses_a_revision_id_already_stored() -> None:
    backend = _MemoryBackend()
    ids = _fill(backend, 3)
    clash = Revision(id=ids[0], parent=ids[-1], at=AT)
    with pytest.raises(StorageError) as caught:
        _commit(backend, clash)
    assert caught.value.code == ErrorCode.BAD_ID
    assert [r.id for r in backend.log(SHOW_ID)] == ids


def test_regression_memory_log_fails_closed_on_a_parent_cycle() -> None:
    backend = _MemoryBackend()
    ids = _fill(backend, 3)
    backend._revisions[SHOW_ID][ids[0]] = Revision(id=ids[0], parent=ids[1], at=AT)
    with pytest.raises(StorageError) as looped:
        backend.log(SHOW_ID)
    assert looped.value.code == ErrorCode.CORRUPT


def test_memory_tree_and_blob_fail_closed_on_unknown_ids() -> None:
    backend = _MemoryBackend()
    _fill(backend, 1)
    with pytest.raises(UnknownRefError):
        backend.tree(SHOW_ID, _revision_id(99))
    with pytest.raises(UnknownRefError):
        backend.tree("other-show", _revision_id(1))
    with pytest.raises(StorageError) as caught:
        backend.blob(SHOW_ID, blob_digest(b"absent"))
    assert caught.value.code == ErrorCode.CORRUPT


@pytest.mark.parametrize(
    ("label", "revision", "extra", "code"),
    [
        ("short id", Revision(id="abc", parent="", at=AT), {}, ErrorCode.BAD_ID),
        ("upper id", Revision(id="A" * 64, parent="", at=AT), {}, ErrorCode.BAD_ID),
        ("bad parent", Revision(id=_revision_id(1), parent="zz", at=AT), {}, ErrorCode.BAD_ID),
        (
            "blob key not a digest",
            Revision(id=_revision_id(1), parent="", at=AT),
            {"blobs": {"not-hex": b"x"}},
            ErrorCode.BAD_ID,
        ),
        (
            "blob bytes differ from key",
            Revision(id=_revision_id(1), parent="", at=AT),
            {"blobs": {blob_digest(b"x"): b"y"}},
            ErrorCode.CORRUPT,
        ),
        (
            "tree digest not stored",
            Revision(id=_revision_id(1), parent="", at=AT),
            {"tree": {"/a": blob_digest(b"x")}},
            ErrorCode.CORRUPT,
        ),
    ],
)
def test_regression_memory_commit_verifies_its_inputs(
    label: str, revision: Revision, extra: dict[str, object], code: ErrorCode
) -> None:
    backend = _MemoryBackend()
    with pytest.raises(StorageError) as caught:
        _commit(backend, revision, **extra)
    assert caught.value.code == code, label
    assert backend.shows() == []
    assert backend.head(SHOW_ID) == ROOT_REVISION


def test_memory_commit_accepts_a_tree_over_stored_and_passed_blobs() -> None:
    backend = _MemoryBackend()
    old = b"old"
    first = Revision(id=_revision_id(1), parent="", at=AT, changed=("/a",))
    _commit(backend, first, tree={"/a": blob_digest(old)}, blobs={blob_digest(old): old})
    new = b"new"
    second = Revision(id=_revision_id(2), parent=first.id, at=AT, changed=("/b",))
    tree = {"/a": blob_digest(old), "/b": blob_digest(new)}
    _commit(backend, second, tree=tree, blobs={blob_digest(new): new})
    assert backend.tree(SHOW_ID, second.id) == tree
    assert backend.blob(SHOW_ID, blob_digest(new)) == new


def test_tree_of_an_unknown_revision_is_unknown_ref(backend_factory: BackendFactory) -> None:
    backend = backend_factory()
    ids = _fill(backend, 2)
    for unknown in (_revision_id(99), "nope"):
        with pytest.raises(UnknownRefError) as caught:
            backend.tree(SHOW_ID, unknown)
        assert caught.value.code == ErrorCode.UNKNOWN_REF
    with pytest.raises(UnknownRefError):
        backend.tree("other-show", ids[0])
    assert backend.tree(SHOW_ID, ids[0]) == {}


def _long_history() -> tuple[DocumentRepository, _CountingIndex, str]:
    repo = InMemoryRepository()
    backend = cast(_MemoryBackend, repo.backend)
    head = ROOT_REVISION
    for position in range(HISTORY):
        changes = {"/renders/a.txt": str(position).encode()}
        head = repo.transaction(SHOW_ID, changes, expected_revision=head, at=AT).id
    index = _CountingIndex(backend._revisions[SHOW_ID])
    backend._revisions[SHOW_ID] = index
    return repo, index, head


def test_repository_reads_and_writes_at_the_head_touch_a_constant_number_of_revisions() -> None:
    repo, index, head = _long_history()
    assert repo.head(SHOW_ID) == head
    assert repo.paths(SHOW_ID) == ["/renders/a.txt"]
    assert repo.read(SHOW_ID, "/renders/a.txt") == str(HISTORY - 1).encode()
    assert repo.documents(SHOW_ID)["/renders/a.txt"] == str(HISTORY - 1).encode()
    assert index.reads == 0
    landed = repo.transaction(SHOW_ID, {"/renders/b.txt": b"b"}, expected_revision=head, at=AT)
    assert index.reads <= 2
    index.reads = 0
    assert repo.transaction(SHOW_ID, {"/renders/b.txt": b"b"}, expected_revision=landed.id, at=AT)
    assert index.reads <= 2


def test_repository_conflict_on_a_stale_write_touches_only_the_landed_revisions() -> None:
    repo, index, head = _long_history()
    repo.transaction(SHOW_ID, {"/renders/b.txt": b"b"}, expected_revision=head, at=AT)
    index.reads = 0
    with pytest.raises(ConflictError):
        repo.transaction(SHOW_ID, {"/renders/c.txt": b"c"}, expected_revision=head, at=AT)
    assert index.reads <= 2
