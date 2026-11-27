#!/usr/bin/env python3

"""
test_history_models.py --- tests the revision record's time stamp rule

Contains:
    test_regression_revision_refuses_a_naive_at: refuses a revision time with no timezone, naming
        the fix, and keeps one with any offset
    test_regression_stored_naive_revision_time_still_reads: reads a stored revision time with no
        timezone as UTC from layout-1 and layout-2 repositories, rewriting no file
    test_regression_stored_revision_with_a_malformed_at_fails_closed: reports a stored revision
        whose time is not a time as a corrupt file, never as a crash
"""

import shutil
from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError
from e8s.core.json_types import JsonValue
from e8s.show.backend import digest
from e8s.show.backend_checks import stored_revision
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import FileBackend
from e8s.show.file_repository import FileRepository
from e8s.show.history_models import Revision

AWARE_AT = datetime(2026, 11, 13, 12, 0, tzinfo=UTC)
NAIVE_AT = AWARE_AT.replace(tzinfo=None)
REVISION_ID = "a" * 64
SLOT = Path("slot.json")
V040_FIXTURE = Path(__file__).resolve().parents[2] / "fixtures" / "repos" / "v0.4.0"
V040_SHOW = "legacy-show"
STORED_AWARE_AT = b'"at": "2026-11-13T12:00:00Z"'
STORED_NAIVE_AT = b'"at": "2026-11-13T12:00:00"'
LAYOUT2_SHOW = "naive-times"
RENDER = "/renders/a.txt"


def test_regression_revision_refuses_a_naive_at() -> None:
    """Refuses a revision time with no timezone and names the fix; any offset is kept."""
    with pytest.raises(SchemaError) as caught:
        Revision(id=REVISION_ID, parent="", at=NAIVE_AT)
    assert "timezone" in caught.value.message
    assert "tzinfo=UTC" in caught.value.hint
    offset = AWARE_AT.astimezone(timezone(timedelta(hours=2)))
    assert Revision(id=REVISION_ID, parent="", at=offset).at == AWARE_AT


def _file_bytes(root: Path) -> dict[Path, tuple[bytes, int]]:
    return {
        path: (path.read_bytes(), path.stat().st_mtime_ns)
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _layout1_with_naive_times(root: Path) -> FileRepository:
    """Copies the v0.4.0 repository and stores every revision time as a naive time would be."""
    shutil.copytree(V040_FIXTURE / "repo", root)
    for record in (root / V040_SHOW / "trees").iterdir():
        stored = record.read_bytes()
        record.write_bytes(stored.replace(STORED_AWARE_AT, STORED_NAIVE_AT))
        assert STORED_NAIVE_AT in record.read_bytes()
    return FileRepository(root)


def _layout2_with_a_naive_time(root: Path) -> FileBackend:
    """Commits a revision whose time has no timezone, as the layout-2 writer stored it before."""
    data = b"render"
    changes = {RENDER: digest(data)}
    revision = Revision._unchecked(
        id=revision_id("", NAIVE_AT, changes), parent="", at=NAIVE_AT, changed=(RENDER,)
    )
    FileBackend(root).commit(LAYOUT2_SHOW, revision, changes, {digest(data): data})
    return FileBackend(root)


@pytest.mark.parametrize("layout", ["layout 1", "layout 2"])
def test_regression_stored_naive_revision_time_still_reads(tmp_path: Path, layout: str) -> None:
    """Reads a stored revision time with no timezone as UTC, leaving the stored files untouched."""
    root = tmp_path / "repo"
    if layout == "layout 1":
        repo = _layout1_with_naive_times(root)
        before = _file_bytes(root)
        revisions = repo.revisions(V040_SHOW)
    else:
        backend = _layout2_with_a_naive_time(root)
        before = _file_bytes(root)
        revisions = backend.log(LAYOUT2_SHOW)
    assert revisions
    assert all(revision.at == AWARE_AT for revision in revisions)
    assert all(revision.at.utcoffset() == timedelta(0) for revision in revisions)
    assert _file_bytes(root) == before


def test_regression_stored_revision_with_a_malformed_at_fails_closed() -> None:
    """Reports a stored revision whose time is not a time as a corrupt file, never a crash."""
    stored: dict[str, JsonValue] = {"id": REVISION_ID, "parent": "", "at": "noon", "changed": []}
    with pytest.raises(StorageError) as caught:
        stored_revision("show_1", SLOT, stored)
    assert caught.value.code == ErrorCode.CORRUPT
    assert isinstance(caught.value.__cause__, SchemaError)
