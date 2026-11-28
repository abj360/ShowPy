#!/usr/bin/env python3

"""
test_archive_resume.py --- tests that importing an archive again finishes a stopped import

Contains:
    test_regression_a_stopped_import_finishes_when_run_again: lands the rest of the history
        after an import stopped at a slot, in a file and a memory repository
    test_regression_a_second_import_of_a_completed_archive_writes_nothing: returns the show ID
        and changes nothing when the show holds the whole history already
    test_regression_an_archive_tampered_after_the_stored_head_is_refused_before_any_write:
        verifies the whole archive before resuming, so damage past the stored head writes nothing
"""

import json
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
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
