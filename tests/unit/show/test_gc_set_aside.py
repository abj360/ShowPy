#!/usr/bin/env python3

"""
test_gc_set_aside.py --- tests the files gc holds aside: put back, dropped, conflicting or read

Contains:
    test_regression_a_stopped_gc_leaves_a_set_aside_file_the_next_gc_puts_back: reports the
        set-aside file, then puts it back fresh and removes it once it is old again
    test_regression_gc_keeps_a_writers_file_its_put_back_renamed_over: keeps a blob a writer
        placed between a rename put-back's lookup and its rename, which swapped in the old copy
    test_gc_puts_back_a_set_aside_file_a_slot_reaches: restores a set-aside blob a tree names
    test_regression_a_read_of_a_file_a_stopped_gc_holds_aside_names_fsck_and_gc: hints at
        fsck and gc, not at a backup restore or head(), when a blob or record is set aside
    test_regression_the_tree_of_a_head_whose_record_is_lost_is_corrupt: raises CORRUPT naming
        fsck, not UnknownRefError, when the head's record is gone
    test_fsck_reports_a_set_aside_name_it_does_not_understand: reports a malformed set-aside name
    test_gc_leaves_a_set_aside_copy_that_differs_from_its_name: reports a set-aside copy whose
        name holds other bytes as needing an operator, and gc skips the show saying why
    test_gc_drops_a_set_aside_copy_of_the_file_under_its_name: drops a copy a gc stopped
        between its link and its unlink left beside the same file
    test_gc_leaves_both_files_when_a_name_fills_with_other_bytes_while_aside: keeps both and
        reports them as needing an operator
    test_gc_removes_a_file_whose_move_set_its_change_time: removes an unreached blob although
        the link, unlink or rename that set it aside changed its change time
    test_gc_puts_back_a_file_that_changes_once_set_aside: keeps a blob whose set-aside copy
        changes between gc's lookup after the move and its removal
    test_move_new_renames_where_links_fail: moves by rename without replacing, where the file
        system refuses hard links (EINVAL included), and raises other link failures
    test_move_new_reports_a_name_taken_during_its_rename: answers False when the rename finds the
        name taken
"""

import errno
import os
from collections.abc import Callable
from datetime import UTC, datetime
from functools import partial
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show import durable_io, durable_reads
from e8s.show import gc as gc_module
from e8s.show.backend import digest
from e8s.show.file_backend import FileBackend
from e8s.show.fsck import fsck
from e8s.show.fsck_findings import SET_ASIDE_PREFIX
from e8s.show.gc import gc
from e8s.show.history_models import Revision
from e8s.show.storage_models import GcReport
from tests.fixtures.fsck_repos import with_stat
from tests.fixtures.gc_races import (
    OLD,
    REDO,
    SHOW_ID,
    Crash,
    StrPath,
    change,
    folder,
    make_old,
    moved_name_freed,
    unreachable_blob,
)


def test_regression_a_stopped_gc_leaves_a_set_aside_file_the_next_gc_puts_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The put-back freshens the file, so a later gc removes it only once it is old again."""
    now = datetime.now(UTC)
    _, blob = unreachable_blob(tmp_path, now)

    def stop() -> None:
        raise Crash

    moved_name_freed(monkeypatch, blob, stop)
    with pytest.raises(Crash):
        gc(tmp_path, at=now, confirm=True)
    monkeypatch.undo()
    aside = blob.with_name(".gc-" + blob.name)
    assert aside.read_bytes() == REDO
    report = fsck(tmp_path)
    assert [(f.code, f.path) for f in report.findings] == [
        ("STALE_TEMP", aside.relative_to(tmp_path).as_posix())
    ]
    assert report.ok
    assert gc(tmp_path, at=now).removed == ()
    assert aside.exists()
    restored = gc(tmp_path, at=now, confirm=True)
    assert restored.removed == ()
    assert blob.read_bytes() == REDO
    assert not aside.exists()
    swept = gc(tmp_path, at=now + OLD, confirm=True)
    assert swept.removed == (blob.relative_to(tmp_path).as_posix(),)
    assert not blob.exists()
    assert not aside.exists()
    assert fsck(tmp_path).findings == ()


def test_regression_gc_keeps_a_writers_file_its_put_back_renamed_over(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without hard links a put-back renames; a writer may place the name after its lookup.

    The rename then swaps the old set-aside copy, with its old time, over the writer's fresh
    file, and the same gc run must still keep it until the writer's slot lands.
    """
    now = datetime.now(UTC)
    first, blob = unreachable_blob(tmp_path, now)
    blob.rename(blob.with_name(".gc-" + blob.name))
    second = change(first.id, REDO)
    real_lexists = os.path.lexists
    reports: list[GcReport] = []

    def refused(*_args: object) -> None:
        raise OSError(errno.EPERM, "no hard links")

    def lexists_before_the_writer(path: StrPath) -> bool:
        if Path(path) == blob and not reports:
            return False
        return real_lexists(path)

    def gc_then_claim(backend: FileBackend, show_id: str, revision: Revision, seq: int) -> None:
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(os, "link", refused)
            patch.setattr(os.path, "lexists", lexists_before_the_writer)
            reports.append(gc(tmp_path, at=now, confirm=True))
        claim(backend, show_id, revision, seq)

    claim = FileBackend._claim_slot
    monkeypatch.setattr(FileBackend, "_claim_slot", gc_then_claim)
    FileBackend(tmp_path).commit(SHOW_ID, *second)
    monkeypatch.undo()
    assert reports[0].removed == ()
    assert fsck(tmp_path).findings == ()
    assert FileBackend(tmp_path).blob(SHOW_ID, digest(REDO)) == REDO


def test_gc_puts_back_a_set_aside_file_a_slot_reaches(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    first = change("", b"render 0")
    FileBackend(tmp_path).commit(SHOW_ID, *first)
    blob = folder(tmp_path) / "objects" / digest(b"render 0")
    make_old(blob, now)
    blob.rename(blob.with_name(".gc-" + blob.name))
    found = fsck(tmp_path)
    assert sorted(f.code for f in found.findings) == ["MISSING_BLOB", "STALE_TEMP"]
    report = gc(tmp_path, at=now, confirm=True)
    assert report.removed == ()
    assert fsck(tmp_path).findings == ()
    assert FileBackend(tmp_path).blob(SHOW_ID, digest(b"render 0")) == b"render 0"


@pytest.mark.parametrize("kind", ["blob", "record", "head_tree", "since"])
def test_regression_a_read_of_a_file_a_stopped_gc_holds_aside_names_fsck_and_gc(
    tmp_path: Path, kind: str
) -> None:
    """Restoring a backup could roll back acknowledged commits; one confirmed gc puts it back.

    Reading the tree of the head, or the log since a revision, whose record is aside once
    raised UnknownRefError with a hint to read the head with head().
    """
    first = change("", b"render 0")
    second = change(first[0].id, b"render 1")
    backend = FileBackend(tmp_path)
    for landing in (first, second):
        backend.commit(SHOW_ID, *landing)
    record = folder(tmp_path) / "records" / f"{first[0].id}.json"
    reads: dict[str, tuple[Path, Callable[[], object]]] = {
        "blob": (
            folder(tmp_path) / "objects" / digest(b"render 0"),
            partial(FileBackend(tmp_path).blob, SHOW_ID, digest(b"render 0")),
        ),
        "record": (record, partial(FileBackend(tmp_path).log, SHOW_ID)),
        "head_tree": (
            folder(tmp_path) / "records" / f"{second[0].id}.json",
            partial(FileBackend(tmp_path).tree, SHOW_ID, second[0].id),
        ),
        "since": (record, partial(FileBackend(tmp_path).log, SHOW_ID, since=first[0].id)),
    }
    path, read = reads[kind]
    path.rename(path.with_name(".gc-" + path.name))
    with pytest.raises(StorageError) as caught:
        read()
    assert caught.value.code == ErrorCode.CORRUPT
    assert "fsck(root)" in caught.value.hint
    assert "gc(root, at=datetime.now(UTC), confirm=True)" in caught.value.hint
    assert gc(tmp_path, at=datetime.now(UTC), confirm=True).removed == ()
    read()
    assert FileBackend(tmp_path).log(SHOW_ID) == [first[0], second[0]]


def test_regression_the_tree_of_a_head_whose_record_is_lost_is_corrupt(tmp_path: Path) -> None:
    """The head slot names the revision, so it is a lost file, never an unknown revision."""
    first = change("", b"render 0")
    FileBackend(tmp_path).commit(SHOW_ID, *first)
    (folder(tmp_path) / "records" / f"{first[0].id}.json").unlink()
    with pytest.raises(StorageError) as caught:
        FileBackend(tmp_path).tree(SHOW_ID, first[0].id)
    assert caught.value.code == ErrorCode.CORRUPT
    assert "head slot" in caught.value.message
    assert "fsck(root)" in caught.value.hint


def test_fsck_reports_a_set_aside_name_it_does_not_understand(tmp_path: Path) -> None:
    FileBackend(tmp_path).commit(SHOW_ID, *change("", b"render 0"))
    for subfolder, name in (("objects", ".gc-abc"), ("records", ".gc-" + "f" * 64)):
        (folder(tmp_path) / subfolder / name).write_bytes(b"?")
    report = fsck(tmp_path)
    assert [f.code for f in report.findings] == ["UNKNOWN_FILE", "UNKNOWN_FILE"]
    assert gc(tmp_path, at=datetime.now(UTC) + 2 * OLD, confirm=True).removed == ()


def test_gc_leaves_a_set_aside_copy_that_differs_from_its_name(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _, blob = unreachable_blob(tmp_path, now)
    aside = blob.with_name(".gc-" + blob.name)
    aside.write_bytes(b"other bytes")
    found = fsck(tmp_path)
    assert [(f.code, f.path) for f in found.findings] == [
        ("UNKNOWN_FILE", aside.relative_to(tmp_path).as_posix()),
        ("UNREACHABLE", blob.relative_to(tmp_path).as_posix()),
    ]
    assert "keep" in found.findings[0].message
    assert "puts it back" not in found.findings[0].message
    assert not found.ok
    for _ in range(2):
        report = gc(tmp_path, at=now, confirm=True)
        assert report.removed == ()
        assert report.skipped == (
            (
                folder(tmp_path).relative_to(tmp_path).as_posix(),
                "fsck finds UNKNOWN_FILE; run fsck(root)",
            ),
        )
    assert aside.read_bytes() == b"other bytes"
    assert blob.read_bytes() == REDO


def test_gc_drops_a_set_aside_copy_of_the_file_under_its_name(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _, blob = unreachable_blob(tmp_path, now)
    aside = blob.with_name(".gc-" + blob.name)
    os.link(blob, aside)
    found = fsck(tmp_path)
    assert ("STALE_TEMP", "a stopped gc set this copy aside; the next gc drops it") in [
        (f.code, f.message) for f in found.findings
    ]
    report = gc(tmp_path, at=now, confirm=True)
    assert report.removed == (blob.relative_to(tmp_path).as_posix(),)
    assert not aside.exists()
    assert fsck(tmp_path).findings == ()


def test_gc_leaves_both_files_when_a_name_fills_with_other_bytes_while_aside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    _, blob = unreachable_blob(tmp_path, now)
    aside = blob.with_name(".gc-" + blob.name)
    moved_name_freed(monkeypatch, blob, lambda: blob.write_bytes(b"other bytes"))
    report = gc(tmp_path, at=now, confirm=True)
    monkeypatch.undo()
    assert report.removed == ()
    assert aside.read_bytes() == REDO
    assert blob.read_bytes() == b"other bytes"
    codes = {(f.code, f.path) for f in fsck(tmp_path).findings}
    assert ("UNKNOWN_FILE", aside.relative_to(tmp_path).as_posix()) in codes


def _set_aside_lookups(
    monkeypatch: pytest.MonkeyPatch, change: Callable[[os.stat_result, int], os.stat_result]
) -> None:
    """Passes each lookup of a set-aside name through `change`, with its count so far."""
    lookup = durable_reads.lstat_info
    count: list[int] = []

    def changed(show_id: str, path: Path) -> os.stat_result | None:
        info = lookup(show_id, path)
        if info is None or not path.name.startswith(SET_ASIDE_PREFIX):
            return info
        count.append(1)
        return change(info, len(count))

    monkeypatch.setattr(gc_module, "lstat_info", changed)


def test_gc_removes_a_file_whose_move_set_its_change_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """POSIX sets the change time on a link, unlink or rename; Windows keeps its creation time."""
    now = datetime.now(UTC)
    _, blob = unreachable_blob(tmp_path, now)
    later = blob.lstat().st_ctime_ns + 10**9
    _set_aside_lookups(monkeypatch, lambda info, _: with_stat(info, st_ctime_ns=later))
    report = gc(tmp_path, at=now, confirm=True)
    assert report.removed == (blob.relative_to(tmp_path).as_posix(),)
    assert sorted(p.name for p in blob.parent.iterdir()) == sorted(
        [digest(b"render 0"), digest(b"static")]
    )


def test_gc_puts_back_a_file_that_changes_once_set_aside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Another gc linking the copy back changes it after this gc's lookup that follows the move."""
    now = datetime.now(UTC)
    _, blob = unreachable_blob(tmp_path, now)

    def changed_on_second_lookup(info: os.stat_result, count: int) -> os.stat_result:
        return info if count == 1 else with_stat(info, st_ctime_ns=info.st_ctime_ns + 1)

    _set_aside_lookups(monkeypatch, changed_on_second_lookup)
    report = gc(tmp_path, at=now, confirm=True)
    assert report.removed == ()
    assert blob.read_bytes() == REDO
    assert not any(p.name.startswith(SET_ASIDE_PREFIX) for p in blob.parent.iterdir())


@pytest.mark.parametrize("refusal", [errno.EXDEV, errno.EPERM, errno.ENOTSUP, errno.EINVAL])
def test_move_new_renames_where_links_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, refusal: int
) -> None:
    """EINVAL is how exFAT and some FAT drivers refuse a hard link."""

    def refused(*_args: object) -> None:
        raise OSError(refusal, "link refused")

    monkeypatch.setattr(os, "link", refused)
    source, target = tmp_path / "a", tmp_path / "b"
    source.write_bytes(b"x")
    assert durable_io.move_new(source, target) is True
    assert target.read_bytes() == b"x"
    assert not source.exists()
    source.write_bytes(b"y")
    assert durable_io.move_new(source, target) is False
    assert target.read_bytes() == b"x"
    assert durable_io.move_new(tmp_path / "gone", tmp_path / "c") is False

    def failing(*_args: object) -> None:
        raise OSError(errno.EIO, "drive failed")

    monkeypatch.setattr(os, "link", failing)
    with pytest.raises(OSError, match="drive failed"):
        durable_io.move_new(source, tmp_path / "d")


def test_move_new_reports_a_name_taken_during_its_rename(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Windows refuses a rename onto an existing name, as when a writer took it meanwhile."""

    def refused(*_args: object) -> None:
        raise OSError(errno.EXDEV, "link refused")

    def taken(*_args: object) -> None:
        raise FileExistsError(errno.EEXIST, "name taken")

    monkeypatch.setattr(os, "link", refused)
    monkeypatch.setattr(os, "rename", taken)
    source = tmp_path / "a"
    source.write_bytes(b"x")
    assert durable_io.move_new(source, tmp_path / "b") is False
    assert source.read_bytes() == b"x"
