#!/usr/bin/env python3

"""
test_crash_repair_hints.py --- tests that a file a crashed commit left torn fails with a hint
that names the repair, and that the repair works

Without hard links a commit creates each file under its final name and then writes it, so a
commit stopped in between leaves the file empty or cut short. Each test stops a real commit
there, checks the CORRUPT error and its hint, then runs the call the hint names.

Contains:
    Crash: stops a commit the way a killed process does
    test_regression_torn_blob_or_record_hint_names_the_gc_that_clears_it: fails the retry of a
        commit that stopped while writing a blob or record with a hint naming fsck and gc, which
        removes the file once it is a day old so the retry lands
    test_damaged_blob_or_record_keeps_the_restore_hint: keeps the backup hint for a blob or
        record that is no shorter start of what the commit writes
"""

import errno
import os
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show import durable_io
from e8s.show.backend_checks import CORRUPT_HINT
from e8s.show.commit_writes import STALE_TEMP_AGE
from e8s.show.file_backend import FileBackend
from e8s.show.fsck_findings import UNREACHABLE
from e8s.show.gc import gc
from e8s.show.history_models import Revision
from tests.fixtures.fsck_repos import (
    AT,
    DAY,
    SHOW_ID,
    age_by,
    blob_path,
    codes,
    landed_revisions,
    numbered_change,
    record_path,
    relative,
)

pytestmark = pytest.mark.usefixtures("no_reread_pauses")

GC_CALL = "gc(root, at=datetime.now(UTC), confirm=True)"
HOUR = timedelta(hours=1)
# The share of a file's bytes the stopped writer got onto the disk.
WRITTEN_SHARES = [0.0, 0.5]
WRITTEN_IDS = ["empty", "cut_short"]
TORN_KINDS = ["blob", "record"]
NEW_BLOB = b"render 1"

type Change = tuple[Revision, dict[str, str], dict[str, bytes]]
type StrPath = str | os.PathLike[str]


class Crash(BaseException):
    """Stops a commit the way a killed process does; no library handler catches it."""


def _refuse_link(source: StrPath, _target: StrPath, **_options: bool) -> None:
    raise OSError(errno.EPERM, os.strerror(errno.EPERM), str(source))


def _commit_stopped_writing(
    root: Path, change: Change, is_target: Callable[[Path], bool], share: float
) -> None:
    """Runs a commit without hard links and stops it inside the write of one file.

    Args:
        root: Repository root folder.
        change: Revision, tree and blobs to commit.
        is_target: Tells the file whose write stops the commit.
        share: Share of the file's bytes written before the stop.
    """
    write_new = durable_io.write_new

    def stopping(descriptor: int, target: Path, data: bytes) -> None:
        if not is_target(target):
            write_new(descriptor, target, data)
            return
        os.write(descriptor, data[: int(len(data) * share)])
        raise Crash

    with pytest.MonkeyPatch.context() as patches:
        patches.setattr(os, "link", _refuse_link)
        patches.setattr(durable_io, "write_new", stopping)
        with pytest.raises(Crash):
            FileBackend(root).commit(SHOW_ID, *change)


def _torn_path(root: Path, kind: str, change: Change) -> Path:
    """Names the new blob or the record of revision 1, which only its commit writes."""
    if kind == "record":
        return record_path(root, change[0])
    return blob_path(root, NEW_BLOB)


def _refused_retry(root: Path, change: Change) -> StorageError:
    with pytest.raises(StorageError) as caught:
        FileBackend(root).commit(SHOW_ID, *change)
    assert caught.value.code == ErrorCode.CORRUPT
    return caught.value


@pytest.mark.parametrize("share", WRITTEN_SHARES, ids=WRITTEN_IDS)
@pytest.mark.parametrize("kind", TORN_KINDS)
def test_regression_torn_blob_or_record_hint_names_the_gc_that_clears_it(
    tmp_path: Path, kind: str, share: float
) -> None:
    landed = landed_revisions(tmp_path, 1)
    change = numbered_change(landed[-1].id, 1)
    torn = _torn_path(tmp_path, kind, change)
    _commit_stopped_writing(tmp_path, change, lambda target: target == torn, share)
    assert torn.exists()
    hint = _refused_retry(tmp_path, change).hint
    assert "fsck(root)" in hint
    assert UNREACHABLE in hint
    assert GC_CALL in hint
    assert "a day old" in hint
    assert STALE_TEMP_AGE == DAY
    assert (UNREACHABLE, relative(tmp_path, torn)) in codes(tmp_path)
    age_by(torn, STALE_TEMP_AGE - HOUR)
    gc(tmp_path, at=AT, confirm=True)
    assert torn.exists()
    age_by(torn, STALE_TEMP_AGE + HOUR)
    gc(tmp_path, at=AT, confirm=True)
    assert not torn.exists()
    FileBackend(tmp_path).commit(SHOW_ID, *change)
    assert FileBackend(tmp_path).log(SHOW_ID) == [*landed, change[0]]
    assert codes(tmp_path) == []


@pytest.mark.parametrize("kind", TORN_KINDS)
def test_damaged_blob_or_record_keeps_the_restore_hint(tmp_path: Path, kind: str) -> None:
    landed = landed_revisions(tmp_path, 1)
    change = numbered_change(landed[-1].id, 1)
    damaged = _torn_path(tmp_path, kind, change)
    damaged.parent.mkdir(parents=True, exist_ok=True)
    damaged.write_bytes(b"not what this commit writes")
    assert _refused_retry(tmp_path, change).hint == CORRUPT_HINT
