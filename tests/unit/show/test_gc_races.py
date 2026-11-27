#!/usr/bin/env python3

"""
test_gc_races.py --- tests gc running while a commit reuses, writes or lands the files it lists

Contains:
    test_regression_gc_keeps_what_a_retried_commit_reuses: keeps the blob, or the blob and record,
        a crashed attempt left two days ago when a retry of the same commit reuses them while gc
        runs before the retry's slot lands
    test_regression_reused_file_removed_before_it_is_freshened_is_placed_again: places a blob or
        record again when a clean-up removed it between the commit's check and its freshen
    test_freshen_moves_a_files_time_and_never_a_link_target: freshens a file to the file system's
        time, reports a missing one and leaves a link's target alone
    test_regression_gc_keeps_a_file_freshened_just_before_it_moves: keeps an unreachable blob a
        writer freshened just before gc's first move of it
    test_regression_gc_keeps_a_file_a_writer_placed_again_while_set_aside: reports nothing removed
        when a writer placed the file again while gc held it aside
    test_regression_gc_stops_on_a_show_whose_head_moves_during_removal: puts the file back and
        skips the show once a slot lands while gc removes
    test_regression_gc_puts_back_a_file_a_slot_names_once_set_aside: puts back a blob gc held
        aside when a commit that did not pass it, so never freshened it, landed naming it
    test_regression_a_stopped_gc_leaves_a_set_aside_file_the_next_gc_puts_back: reports the
        set-aside file, then puts it back and removes it when no slot reaches it
    test_gc_puts_back_a_set_aside_file_a_slot_reaches: restores a set-aside blob a tree names
    test_regression_a_read_of_a_file_a_stopped_gc_holds_aside_names_fsck_and_gc: hints at
        fsck and gc, not at a backup restore, when a blob or record is missing
    test_fsck_reports_a_set_aside_name_it_does_not_understand: reports a malformed set-aside name
    test_gc_leaves_a_set_aside_copy_that_differs_from_its_name: reports a set-aside copy whose
        name holds other bytes as needing an operator, and gc skips the show saying why
    test_gc_drops_a_set_aside_copy_of_the_file_under_its_name: drops a copy a gc stopped
        between its link and its unlink left beside the same file
    test_gc_leaves_both_files_when_a_name_fills_with_other_bytes_while_aside: keeps both and
        reports them as needing an operator
    test_gc_keeps_a_temp_file_touched_after_the_check: keeps a temp file a writer touched
    test_move_new_renames_where_links_fail: moves by rename without replacing, where the file
        system refuses hard links (EINVAL included), and raises other link failures
    test_move_new_reports_a_name_taken_during_its_rename: answers False when the rename finds the
        name taken
"""

import errno
import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from functools import partial
from os import PathLike
from pathlib import Path

import pytest

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show import durable_io, fsck_scan
from e8s.show import gc as gc_module
from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import FileBackend
from e8s.show.fsck import fsck
from e8s.show.gc import gc
from e8s.show.history_models import Revision
from e8s.show.show_folders import show_folder

AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
type StrPath = str | PathLike[str]

SHOW_ID = "race_show"
STATIC = "/renders/static.txt"
RENDER = "/renders/render.txt"
REDO = b"redo me"
OLD = timedelta(days=2)
MOVED = "a slot landed while gc ran; run gc again"


class _Crash(BaseException):
    """Stops a commit the way a killed process would, after its blobs and record are written."""


def _change(parent: str, data: bytes) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    tree = {STATIC: digest(b"static"), RENDER: digest(data)}
    changed = (RENDER, STATIC) if parent == "" else (RENDER,)
    changes = {path: tree[path] for path in changed}
    revision = Revision(
        id=revision_id(parent, AT, changes), parent=parent, at=AT, changed=tuple(sorted(changed))
    )
    return revision, tree, {digest(data): data, digest(b"static"): b"static"}


def _folder(root: Path) -> Path:
    return root / "shows" / show_folder(SHOW_ID)


def _make_old(path: Path, now: datetime) -> None:
    then = (now - OLD).timestamp()
    os.utime(path, (then, then))


def _crashed_attempt(
    root: Path, change: tuple[Revision, dict[str, str], dict[str, bytes]], left: str
) -> list[Path]:
    """Leaves what a crashed attempt of `change` wrote: its new blob, or its blob and record."""
    revision, tree, blobs = change
    blob = _folder(root) / "objects" / digest(REDO)
    if left == "blob":
        blob.write_bytes(REDO)
        return [blob]

    def crash(*_args: object) -> None:
        raise _Crash

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(FileBackend, "_claim_slot", crash)
        with pytest.raises(_Crash):
            FileBackend(root).commit(SHOW_ID, revision, tree, blobs)
    return [blob, _folder(root) / "records" / f"{revision.id}.json"]


@pytest.mark.parametrize("left", ["blob", "record"])
def test_regression_gc_keeps_what_a_retried_commit_reuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, left: str
) -> None:
    """gc runs after the retry checked the old files and before it claims its slot."""
    now = datetime.now(UTC)
    first = _change("", b"render 0")
    FileBackend(tmp_path).commit(SHOW_ID, *first)
    retried = _change(first[0].id, REDO)
    for path in _crashed_attempt(tmp_path, retried, left):
        _make_old(path, now)
    claim = FileBackend._claim_slot
    removed: list[tuple[str, ...]] = []

    def claim_after_gc(backend: FileBackend, show_id: str, revision: Revision, seq: int) -> None:
        removed.append(gc(tmp_path, at=now, confirm=True).removed)
        claim(backend, show_id, revision, seq)

    monkeypatch.setattr(FileBackend, "_claim_slot", claim_after_gc)
    FileBackend(tmp_path).commit(SHOW_ID, *retried)
    monkeypatch.undo()
    assert removed == [()]
    assert fsck(tmp_path).findings == ()
    assert FileBackend(tmp_path).log(SHOW_ID) == [first[0], retried[0]]


@pytest.mark.parametrize("left", ["blob", "record"])
def test_regression_reused_file_removed_before_it_is_freshened_is_placed_again(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, left: str
) -> None:
    now = datetime.now(UTC)
    first = _change("", b"render 0")
    FileBackend(tmp_path).commit(SHOW_ID, *first)
    retried = _change(first[0].id, REDO)
    reused = _crashed_attempt(tmp_path, retried, left)[-1]
    _make_old(reused, now)
    freshen = durable_io.freshen
    removed: list[Path] = []

    def removed_first(path: Path) -> bool:
        if path == reused and not removed:
            path.unlink()
            removed.append(path)
        return freshen(path)

    monkeypatch.setattr(durable_io, "freshen", removed_first)
    FileBackend(tmp_path).commit(SHOW_ID, *retried)
    monkeypatch.undo()
    assert removed == [reused]
    assert reused.stat().st_mtime > (now - OLD).timestamp()
    assert fsck(tmp_path).findings == ()
    assert FileBackend(tmp_path).log(SHOW_ID) == [first[0], retried[0]]


@pytest.mark.skipif(sys.platform == "win32", reason="symlinks need privilege")
def test_freshen_moves_a_files_time_and_never_a_link_target(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    target, link = tmp_path / "target", tmp_path / "link"
    target.write_bytes(b"x")
    _make_old(target, now)
    old = target.stat().st_mtime
    link.symlink_to(target)
    assert durable_io.freshen(link) is True
    assert target.stat().st_mtime == old
    assert durable_io.freshen(target) is True
    assert target.stat().st_mtime > old
    assert durable_io.freshen(tmp_path / "missing") is False


def _unreachable_blob(root: Path, now: datetime) -> tuple[Revision, Path]:
    """Lands one revision and leaves the old blob of a crashed second commit, unreached."""
    first = _change("", b"render 0")
    FileBackend(root).commit(SHOW_ID, *first)
    blob = _crashed_attempt(root, _change(first[0].id, REDO), "blob")[0]
    _make_old(blob, now)
    return first[0], blob


def _around_first_move(
    monkeypatch: pytest.MonkeyPatch, path: Path, before: Callable[[], object]
) -> None:
    """Runs `before` just before the first OS call that moves or removes `path`."""
    fired: list[bool] = []

    def wrapped(real: Callable[..., object]) -> Callable[..., object]:
        def call(source: object, *args: object, **kwargs: object) -> object:
            if Path(str(source)) == path and not fired:
                fired.append(True)
                before()
            return real(source, *args, **kwargs)

        return call

    for name in ("unlink", "rename", "link"):
        monkeypatch.setattr(os, name, wrapped(getattr(os, name)))


def _moved_name_freed(
    monkeypatch: pytest.MonkeyPatch, path: Path, after: Callable[[], object]
) -> None:
    """Runs `after` once `path` is gone from its name, whichever call gc moved it with."""
    real_unlink, real_rename = os.unlink, os.rename
    fired: list[bool] = []

    def freed(name: StrPath) -> None:
        if Path(name) == path and not fired:
            fired.append(True)
            after()

    def unlink(target: StrPath, *, dir_fd: int | None = None) -> None:
        real_unlink(target, dir_fd=dir_fd)
        freed(target)

    def rename(source: StrPath, target: StrPath) -> None:
        real_rename(source, target)
        freed(source)

    monkeypatch.setattr(os, "unlink", unlink)
    monkeypatch.setattr(os, "rename", rename)


def test_regression_gc_keeps_a_file_freshened_just_before_it_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    _, blob = _unreachable_blob(tmp_path, now)
    _around_first_move(monkeypatch, blob, lambda: durable_io.freshen(blob))
    report = gc(tmp_path, at=now, confirm=True)
    monkeypatch.undo()
    assert report.removed == ()
    assert blob.read_bytes() == REDO
    assert sorted(p.name for p in blob.parent.iterdir()) == sorted(
        [blob.name, digest(b"render 0"), digest(b"static")]
    )


def test_regression_gc_keeps_a_file_a_writer_placed_again_while_set_aside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    _, blob = _unreachable_blob(tmp_path, now)
    _moved_name_freed(monkeypatch, blob, lambda: blob.write_bytes(REDO))
    report = gc(tmp_path, at=now, confirm=True)
    monkeypatch.undo()
    assert report.removed == ()
    assert blob.read_bytes() == REDO
    assert not any(p.name.startswith(".gc-") for p in blob.parent.iterdir())


def test_regression_gc_stops_on_a_show_whose_head_moves_during_removal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A commit landing after gc's last check names the blob gc is removing."""
    now = datetime.now(UTC)
    first, blob = _unreachable_blob(tmp_path, now)
    landing = _change(first.id, REDO)
    _around_first_move(monkeypatch, blob, lambda: FileBackend(tmp_path).commit(SHOW_ID, *landing))
    report = gc(tmp_path, at=now, confirm=True)
    monkeypatch.undo()
    assert report.removed == ()
    assert report.skipped == ((_folder(tmp_path).relative_to(tmp_path).as_posix(), MOVED),)
    assert fsck(tmp_path).findings == ()
    assert FileBackend(tmp_path).log(SHOW_ID) == [first, landing[0]]


def test_regression_gc_puts_back_a_file_a_slot_names_once_set_aside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backend protocol lets a commit name a stored blob it does not pass; nothing freshens it.

    Only the check that no slot landed while the blob was aside keeps it.
    """
    now = datetime.now(UTC)
    first, blob = _unreachable_blob(tmp_path, now)
    revision, tree, blobs = _change(first.id, REDO)
    unpassed = {name: data for name, data in blobs.items() if name != digest(REDO)}

    def land() -> None:
        FileBackend(tmp_path).commit(SHOW_ID, revision, tree, unpassed)

    _moved_name_freed(monkeypatch, blob, land)
    report = gc(tmp_path, at=now, confirm=True)
    monkeypatch.undo()
    assert report.removed == ()
    assert report.skipped == ((_folder(tmp_path).relative_to(tmp_path).as_posix(), MOVED),)
    assert blob.read_bytes() == REDO
    assert fsck(tmp_path).findings == ()
    assert FileBackend(tmp_path).log(SHOW_ID) == [first, revision]


def test_regression_a_stopped_gc_leaves_a_set_aside_file_the_next_gc_puts_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    _, blob = _unreachable_blob(tmp_path, now)

    def stop() -> None:
        raise _Crash

    _moved_name_freed(monkeypatch, blob, stop)
    with pytest.raises(_Crash):
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
    swept = gc(tmp_path, at=now, confirm=True)
    assert swept.removed == (blob.relative_to(tmp_path).as_posix(),)
    assert not blob.exists()
    assert not aside.exists()
    assert fsck(tmp_path).findings == ()


def test_gc_puts_back_a_set_aside_file_a_slot_reaches(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    first = _change("", b"render 0")
    FileBackend(tmp_path).commit(SHOW_ID, *first)
    blob = _folder(tmp_path) / "objects" / digest(b"render 0")
    _make_old(blob, now)
    blob.rename(blob.with_name(".gc-" + blob.name))
    found = fsck(tmp_path)
    assert sorted(f.code for f in found.findings) == ["MISSING_BLOB", "STALE_TEMP"]
    report = gc(tmp_path, at=now, confirm=True)
    assert report.removed == ()
    assert fsck(tmp_path).findings == ()
    assert FileBackend(tmp_path).blob(SHOW_ID, digest(b"render 0")) == b"render 0"


@pytest.mark.parametrize("kind", ["blob", "record"])
def test_regression_a_read_of_a_file_a_stopped_gc_holds_aside_names_fsck_and_gc(
    tmp_path: Path, kind: str
) -> None:
    """Restoring a backup could roll back acknowledged commits; one confirmed gc puts it back."""
    first = _change("", b"render 0")
    FileBackend(tmp_path).commit(SHOW_ID, *first)
    if kind == "blob":
        path = _folder(tmp_path) / "objects" / digest(b"render 0")
        read: Callable[[], object] = partial(
            FileBackend(tmp_path).blob, SHOW_ID, digest(b"render 0")
        )
    else:
        path = _folder(tmp_path) / "records" / f"{first[0].id}.json"
        read = partial(FileBackend(tmp_path).log, SHOW_ID)
    path.rename(path.with_name(".gc-" + path.name))
    with pytest.raises(StorageError) as caught:
        read()
    assert caught.value.code == ErrorCode.CORRUPT
    assert "fsck(root)" in caught.value.hint
    assert "gc(root, at=datetime.now(UTC), confirm=True)" in caught.value.hint
    assert gc(tmp_path, at=datetime.now(UTC), confirm=True).removed == ()
    assert FileBackend(tmp_path).log(SHOW_ID) == [first[0]]


def test_fsck_reports_a_set_aside_name_it_does_not_understand(tmp_path: Path) -> None:
    FileBackend(tmp_path).commit(SHOW_ID, *_change("", b"render 0"))
    for folder, name in (("objects", ".gc-abc"), ("records", ".gc-" + "f" * 64)):
        (_folder(tmp_path) / folder / name).write_bytes(b"?")
    report = fsck(tmp_path)
    assert [f.code for f in report.findings] == ["UNKNOWN_FILE", "UNKNOWN_FILE"]
    assert gc(tmp_path, at=datetime.now(UTC) + 2 * OLD, confirm=True).removed == ()


def test_gc_leaves_a_set_aside_copy_that_differs_from_its_name(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _, blob = _unreachable_blob(tmp_path, now)
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
                _folder(tmp_path).relative_to(tmp_path).as_posix(),
                "fsck finds UNKNOWN_FILE; run fsck(root)",
            ),
        )
    assert aside.read_bytes() == b"other bytes"
    assert blob.read_bytes() == REDO


def test_gc_drops_a_set_aside_copy_of_the_file_under_its_name(tmp_path: Path) -> None:
    now = datetime.now(UTC)
    _, blob = _unreachable_blob(tmp_path, now)
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
    _, blob = _unreachable_blob(tmp_path, now)
    aside = blob.with_name(".gc-" + blob.name)
    _moved_name_freed(monkeypatch, blob, lambda: blob.write_bytes(b"other bytes"))
    report = gc(tmp_path, at=now, confirm=True)
    monkeypatch.undo()
    assert report.removed == ()
    assert aside.read_bytes() == REDO
    assert blob.read_bytes() == b"other bytes"
    codes = {(f.code, f.path) for f in fsck(tmp_path).findings}
    assert ("UNKNOWN_FILE", aside.relative_to(tmp_path).as_posix()) in codes


def test_gc_keeps_a_temp_file_touched_after_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    _unreachable_blob(tmp_path, now)
    temp = tmp_path / "tmp" / ".tmp-in-use"
    temp.write_bytes(b"writing")
    _make_old(temp, now)
    scan = fsck_scan.scan_repository

    def scan_then_touch(
        root: Path, show_id: str | None, at: datetime | None
    ) -> fsck_scan.RepositoryScan:
        found = scan(root, show_id, at)
        durable_io.freshen(temp)
        return found

    monkeypatch.setattr(gc_module, "scan_repository", scan_then_touch)
    report = gc(tmp_path, at=now, confirm=True)
    assert "tmp/.tmp-in-use" not in report.removed
    assert temp.exists()


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
