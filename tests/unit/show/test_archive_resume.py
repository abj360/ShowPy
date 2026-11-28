#!/usr/bin/env python3

"""
test_archive_resume.py --- tests that importing an archive again finishes a stopped import

Contains:
    test_regression_a_stopped_import_finishes_when_run_again: lands the rest of the history
        after an import stopped at a slot, in a file and a memory repository
    test_regression_a_second_import_of_a_completed_archive_writes_nothing: returns the show ID
        and changes nothing when the show holds the whole history already
    test_regression_a_show_that_diverged_after_a_shared_prefix_is_refused: refuses a show
        holding the archive's first two revisions and another third, writing nothing
    test_regression_a_show_longer_than_the_archive_is_refused: refuses a show that holds the
        whole archive and more, writing nothing
    test_regression_a_loser_at_slot_one_to_the_same_archive_is_told_to_run_it_again: tells an
        import that lost slot 1 to another import of the same archive that it is in progress or
        complete
    test_regression_a_loser_at_a_later_slot_is_told_more_may_have_landed: tells an import that
        lost a later slot how many of its revisions had landed and that more may have since
    test_regression_an_archive_tampered_after_the_stored_head_is_refused_before_any_write:
        verifies the whole archive before resuming, so damage past the stored head writes nothing
"""

import json
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, StorageError
from e8s.show.archive import export_archive, import_archive
from e8s.show.document_repository import DocumentRepository
from e8s.show.file_repository import FileRepository
from e8s.show.fsck import fsck
from e8s.show.memory_backend import InMemoryRepository
from tests.fixtures.archives import (
    SHOW_ID,
    Interrupted,
    entry_map,
    listing,
    rebuilt,
    with_history,
)

REVISIONS = 6
STOP_AT = 4


def _fail() -> None:
    raise StorageError("disk full", hint="free space", code=ErrorCode.IO)


def _stopped_import(repo: DocumentRepository, archive: bytes) -> None:
    """Imports through a backend whose commit of slot STOP_AT fails, as a crash there would."""
    with pytest.raises(StorageError) as caught:
        import_archive(DocumentRepository(Interrupted(repo.backend, STOP_AT, _fail)), archive)
    assert caught.value.context["landed"] == STOP_AT - 1
    assert "import the same archive again" in caught.value.hint


@pytest.mark.parametrize("kind", ["files", "memory"])
def test_regression_a_stopped_import_finishes_when_run_again(tmp_path: Path, kind: str) -> None:
    source = InMemoryRepository()
    landed = with_history(source, REVISIONS)
    archive = export_archive(source, SHOW_ID)
    target = FileRepository(tmp_path) if kind == "files" else InMemoryRepository()
    _stopped_import(target, archive)
    assert target.revisions(SHOW_ID) == landed[: STOP_AT - 1]
    assert import_archive(target, archive) == SHOW_ID
    assert target.revisions(SHOW_ID) == landed
    assert export_archive(target, SHOW_ID) == archive
    if kind == "files":
        assert fsck(tmp_path).ok


def test_regression_a_second_import_of_a_completed_archive_writes_nothing(tmp_path: Path) -> None:
    source = InMemoryRepository()
    with_history(source, 3)
    archive = export_archive(source, SHOW_ID)
    files = FileRepository(tmp_path)
    import_archive(files, archive)
    before = listing(tmp_path)
    assert import_archive(files, archive) == SHOW_ID
    assert listing(tmp_path) == before
    assert import_archive(source, archive) == SHOW_ID
    assert len(source.revisions(SHOW_ID)) == 3


def test_regression_an_archive_tampered_after_the_stored_head_is_refused_before_any_write(
    tmp_path: Path,
) -> None:
    source = InMemoryRepository()
    with_history(source, REVISIONS)
    archive = export_archive(source, SHOW_ID)
    files = FileRepository(tmp_path)
    _stopped_import(files, archive)
    before = listing(tmp_path)
    entries = entry_map(archive)
    last = json.loads(entries[f"log/{REVISIONS:012d}"])["id"]
    record = json.loads(entries[f"records/{last}.json"])
    blob = next(
        record["tree"][path] for path in record["revision"]["changed"] if path in record["tree"]
    )
    entries[f"objects/{blob}"] = b"tampered"
    with pytest.raises(StorageError) as caught:
        import_archive(files, rebuilt(entries))
    assert caught.value.code == ErrorCode.CORRUPT
    assert listing(tmp_path) == before
    assert len(files.revisions(SHOW_ID)) == STOP_AT - 1


def _refused_unchanged(root: Path, repo: DocumentRepository, archive: bytes) -> ConflictError:
    """Imports into a show with another history; the import refuses and writes nothing."""
    before = listing(root)
    with pytest.raises(ConflictError) as caught:
        import_archive(repo, archive)
    assert listing(root) == before
    assert "another history" in caught.value.message
    return caught.value


def test_regression_a_show_that_diverged_after_a_shared_prefix_is_refused(tmp_path: Path) -> None:
    source = InMemoryRepository()
    landed = with_history(source, REVISIONS)
    files = FileRepository(tmp_path)
    shared = with_history(files, 2)
    assert shared == landed[:2]
    other = {"/renders/diverged.txt": b"diverged"}
    files.transaction(SHOW_ID, other, expected_revision=shared[-1].id, at=landed[2].at)
    _refused_unchanged(tmp_path, files, export_archive(source, SHOW_ID))
    assert len(files.revisions(SHOW_ID)) == len(shared) + 1


def test_regression_a_show_longer_than_the_archive_is_refused(tmp_path: Path) -> None:
    source = InMemoryRepository()
    with_history(source, 3)
    files = FileRepository(tmp_path)
    with_history(files, REVISIONS)
    _refused_unchanged(tmp_path, files, export_archive(source, SHOW_ID))
    assert len(files.revisions(SHOW_ID)) == REVISIONS


def _raced(at_commit: int) -> tuple[ConflictError, InMemoryRepository, list[str]]:
    """Runs two imports of one archive; the second completes before the first's commit `n`."""
    source = InMemoryRepository()
    landed = [r.id for r in with_history(source, REVISIONS)]
    archive = export_archive(source, SHOW_ID)
    target = InMemoryRepository()

    def other_import() -> None:
        import_archive(target, archive)

    racing = DocumentRepository(Interrupted(target.backend, at_commit, other_import))
    with pytest.raises(ConflictError) as caught:
        import_archive(racing, archive)
    return caught.value, target, landed


def test_regression_a_loser_at_slot_one_to_the_same_archive_is_told_to_run_it_again() -> None:
    error, target, landed = _raced(1)
    assert "in progress or complete" in error.message
    assert "another history" not in error.message
    assert "import the same archive again" in error.hint
    assert [r.id for r in target.revisions(SHOW_ID)] == landed


def test_regression_a_loser_at_a_later_slot_is_told_more_may_have_landed() -> None:
    error, target, landed = _raced(STOP_AT)
    assert f"stopped after {STOP_AT - 1} of {REVISIONS}" in error.message
    assert "more may have landed since" in error.hint
    assert error.context["landed"] == STOP_AT - 1
    assert [r.id for r in target.revisions(SHOW_ID)] == landed
