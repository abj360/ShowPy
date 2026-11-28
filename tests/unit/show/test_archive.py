#!/usr/bin/env python3

"""
test_archive.py --- tests that an archive carries a show's whole history between repositories

Contains:
    test_archive_round_trips_every_revision: carries a history from memory to files and back
        with the same revision IDs, parents, times, trees, blob bytes and shows at every revision
    test_import_into_a_file_repository_passes_fsck: leaves a root fsck finds clean
    test_import_into_a_memory_repository_takes_further_commits: lands the history and a later
        save on top of it
    test_import_refuses_an_existing_show: refuses a show ID the repository holds, writing nothing
    test_regression_import_refuses_a_show_another_writer_creates_meanwhile: answers a show that
        lands between the check and the first commit with ConflictError
    test_regression_an_import_stopped_part_way_reports_what_landed: names the revisions that
        landed when a later commit fails, leaving that complete shorter history
    test_regression_any_library_error_after_the_first_slot_reports_what_landed: names what
        landed when a later commit raises an error that is not a storage error
    test_a_failed_first_commit_raises_its_own_error: raises the backend's error when nothing
        landed, and a later conflict as ConflictError naming what landed
    test_regression_an_archive_dated_year_one_imports_into_a_file_repository: imports a history
        a memory repository saved within a day of the earliest time
    test_archive_bytes_do_not_depend_on_the_hash_seed: gives equal bytes under two hash seeds
    test_a_thousand_revision_archive_round_trips: carries 1,000 revisions to files and back
"""

import hashlib
import os
import subprocess
import sys
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, SchemaError, StorageError
from e8s.examples import two_set_show
from e8s.show.archive import export_archive, import_archive
from e8s.show.backend import RepositoryBackend
from e8s.show.document_repository import DocumentRepository
from e8s.show.file_repository import FileRepository
from e8s.show.fsck import fsck
from e8s.show.history_models import Revision
from e8s.show.memory_backend import InMemoryRepository
from tests.fixtures.archives import AT, SHOW_ID, listing, with_history

SEED_SCRIPT = """
import hashlib
from e8s.show.archive import export_archive
from e8s.show.memory_backend import InMemoryRepository
from tests.fixtures.archives import with_history
repo = InMemoryRepository()
with_history(repo, 5)
print(hashlib.sha256(export_archive(repo, "show_1")).hexdigest())
"""


class _Interrupted:
    """Wraps a backend; a hook runs before the n-th commit, which may raise instead of landing."""

    def __init__(
        self, backend: RepositoryBackend, at_commit: int, hook: Callable[[], None]
    ) -> None:
        self._backend = backend
        self._at_commit = at_commit
        self._hook = hook
        self._commits = 0

    def shows(self) -> list[str]:
        return self._backend.shows()

    def head(self, show_id: str) -> str:
        return self._backend.head(show_id)

    def log(self, show_id: str, *, since: str = "") -> list[Revision]:
        return self._backend.log(show_id, since=since)

    def tree(self, show_id: str, revision_id: str) -> Mapping[str, str]:
        return self._backend.tree(show_id, revision_id)

    def blob(self, show_id: str, digest: str) -> bytes:
        return self._backend.blob(show_id, digest)

    def commit(
        self, show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
    ) -> None:
        self._commits += 1
        if self._commits == self._at_commit:
            self._hook()
        self._backend.commit(show_id, revision, tree, blobs)


def _assert_same_history(source: DocumentRepository, target: DocumentRepository) -> None:
    revisions = source.revisions(SHOW_ID)
    assert target.revisions(SHOW_ID) == revisions
    for revision in revisions:
        tree = source.backend.tree(SHOW_ID, revision.id)
        assert target.backend.tree(SHOW_ID, revision.id) == tree
        for digest in tree.values():
            assert target.backend.blob(SHOW_ID, digest) == source.backend.blob(SHOW_ID, digest)
        assert target.get_show(SHOW_ID, revision.id) == source.get_show(SHOW_ID, revision.id)


def test_archive_round_trips_every_revision(tmp_path: Path) -> None:
    memory = InMemoryRepository()
    landed = with_history(memory, 8)
    archive = export_archive(memory, SHOW_ID)
    files = FileRepository(tmp_path)
    assert import_archive(files, archive) == SHOW_ID
    _assert_same_history(memory, files)
    assert [(r.id, r.parent, r.at) for r in files.revisions(SHOW_ID)] == [
        (r.id, r.parent, r.at) for r in landed
    ]
    back = InMemoryRepository()
    assert import_archive(back, export_archive(FileRepository(tmp_path), SHOW_ID)) == SHOW_ID
    _assert_same_history(memory, back)
    assert export_archive(back, SHOW_ID) == archive


def test_import_into_a_file_repository_passes_fsck(tmp_path: Path) -> None:
    memory = InMemoryRepository()
    with_history(memory, 6)
    import_archive(FileRepository(tmp_path), export_archive(memory, SHOW_ID))
    report = fsck(tmp_path)
    assert report.findings == ()
    assert report.ok


def test_import_into_a_memory_repository_takes_further_commits() -> None:
    source, target = InMemoryRepository(), InMemoryRepository()
    landed = with_history(source, 4)
    import_archive(target, export_archive(source, SHOW_ID))
    renamed = two_set_show().replace(title="after import")
    later = target.put_show(SHOW_ID, renamed, expected_revision=landed[-1].id, at=AT)
    assert target.revisions(SHOW_ID) == [*landed, later]


def test_import_refuses_an_existing_show(tmp_path: Path) -> None:
    source = InMemoryRepository()
    with_history(source, 3)
    archive = export_archive(source, SHOW_ID)
    files = FileRepository(tmp_path)
    with_history(files, 1)
    before = listing(tmp_path)
    with pytest.raises(ConflictError) as caught:
        import_archive(files, archive)
    assert caught.value.code == ErrorCode.CONFLICT
    assert "already" in caught.value.message
    assert listing(tmp_path) == before
    with pytest.raises(ConflictError):
        import_archive(source, archive)
    assert len(source.revisions(SHOW_ID)) == 3


def test_regression_import_refuses_a_show_another_writer_creates_meanwhile() -> None:
    source = InMemoryRepository()
    with_history(source, 3)
    inner = InMemoryRepository()

    def create_it_first() -> None:
        inner.put_show(SHOW_ID, two_set_show().replace(title="first"), expected_revision="", at=AT)

    target = DocumentRepository(_Interrupted(inner.backend, 1, create_it_first))
    with pytest.raises(ConflictError, match="already"):
        import_archive(target, export_archive(source, SHOW_ID))
    assert len(inner.revisions(SHOW_ID)) == 1


def test_regression_an_import_stopped_part_way_reports_what_landed(tmp_path: Path) -> None:
    source = InMemoryRepository()
    landed = with_history(source, 5)

    def fail() -> None:
        raise StorageError("disk full", hint="free space", code=ErrorCode.IO)

    files = FileRepository(tmp_path)
    target = DocumentRepository(_Interrupted(files.backend, 3, fail))
    with pytest.raises(StorageError) as caught:
        import_archive(target, export_archive(source, SHOW_ID))
    assert caught.value.code == ErrorCode.IO
    assert caught.value.context["landed"] == 2
    assert caught.value.context["total"] == len(landed)
    assert caught.value.context["revision"] == landed[1].id
    assert "first 2 revisions" in caught.value.hint
    assert FileRepository(tmp_path).revisions(SHOW_ID) == landed[:2]
    assert fsck(tmp_path).ok


def test_regression_any_library_error_after_the_first_slot_reports_what_landed() -> None:
    source = InMemoryRepository()
    landed = with_history(source, 4)

    def refuse() -> None:
        raise SchemaError("not a revision", hint="fix it", code=ErrorCode.SCHEMA)

    target = InMemoryRepository()
    with pytest.raises(StorageError) as caught:
        import_archive(
            DocumentRepository(_Interrupted(target.backend, 3, refuse)),
            export_archive(source, SHOW_ID),
        )
    assert caught.value.code == ErrorCode.SCHEMA
    assert caught.value.context["landed"] == 2
    assert target.revisions(SHOW_ID) == landed[:2]


def test_a_failed_first_commit_raises_its_own_error() -> None:
    source = InMemoryRepository()
    landed = with_history(source, 4)
    archive = export_archive(source, SHOW_ID)

    def fail() -> None:
        raise StorageError("disk full", hint="free space", code=ErrorCode.IO)

    def conflict() -> None:
        raise ConflictError("raced", hint="read again")

    first = InMemoryRepository()
    with pytest.raises(StorageError, match="disk full") as caught:
        import_archive(DocumentRepository(_Interrupted(first.backend, 1, fail)), archive)
    assert "landed" not in caught.value.context
    assert first.shows() == []
    later = InMemoryRepository()
    with pytest.raises(ConflictError) as raced:
        import_archive(DocumentRepository(_Interrupted(later.backend, 3, conflict)), archive)
    assert raced.value.context["landed"] == 2
    assert later.revisions(SHOW_ID) == landed[:2]


def test_regression_an_archive_dated_year_one_imports_into_a_file_repository(
    tmp_path: Path,
) -> None:
    memory = InMemoryRepository()
    year_one = datetime(1, 1, 1, 12, tzinfo=UTC)
    memory.put_show(SHOW_ID, two_set_show(), expected_revision="", at=year_one)
    files = FileRepository(tmp_path)
    assert import_archive(files, export_archive(memory, SHOW_ID)) == SHOW_ID
    assert files.revisions(SHOW_ID) == memory.revisions(SHOW_ID)
    assert fsck(tmp_path).ok


def test_archive_bytes_do_not_depend_on_the_hash_seed() -> None:
    digests = set()
    root = Path(__file__).resolve().parents[3]
    for seed in ("1", "4242"):
        environment = {**os.environ, "PYTHONHASHSEED": seed}
        result = subprocess.run(
            [sys.executable, "-c", SEED_SCRIPT],
            capture_output=True,
            check=True,
            cwd=root,
            env=environment,
            text=True,
        )
        digests.add(result.stdout.strip())
    memory = InMemoryRepository()
    with_history(memory, 5)
    assert digests == {hashlib.sha256(export_archive(memory, SHOW_ID)).hexdigest()}


@pytest.mark.slow
def test_a_thousand_revision_archive_round_trips(tmp_path: Path) -> None:
    memory = InMemoryRepository()
    with_history(memory, 1_000)
    archive = export_archive(memory, SHOW_ID)
    files = FileRepository(tmp_path)
    import_archive(files, archive)
    assert files.revisions(SHOW_ID) == memory.revisions(SHOW_ID)
    assert export_archive(FileRepository(tmp_path), SHOW_ID) == archive
