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
    test_gc_keeps_a_temp_file_touched_after_the_check: keeps a temp file a writer touched
"""

import os
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from e8s.show import durable_io, fsck_scan
from e8s.show import gc as gc_module
from e8s.show.backend import digest
from e8s.show.file_backend import FileBackend
from e8s.show.fsck import fsck
from e8s.show.gc import gc
from e8s.show.history_models import Revision
from tests.fixtures.gc_races import (
    MOVED,
    OLD,
    REDO,
    SHOW_ID,
    change,
    crashed_attempt,
    folder,
    make_old,
    moved_name_freed,
    unreachable_blob,
)


@pytest.mark.parametrize("left", ["blob", "record"])
def test_regression_gc_keeps_what_a_retried_commit_reuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, left: str
) -> None:
    """gc runs after the retry checked the old files and before it claims its slot."""
    now = datetime.now(UTC)
    first = change("", b"render 0")
    FileBackend(tmp_path).commit(SHOW_ID, *first)
    retried = change(first[0].id, REDO)
    for path in crashed_attempt(tmp_path, retried, left):
        make_old(path, now)
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
    first = change("", b"render 0")
    FileBackend(tmp_path).commit(SHOW_ID, *first)
    retried = change(first[0].id, REDO)
    reused = crashed_attempt(tmp_path, retried, left)[-1]
    make_old(reused, now)
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
    make_old(target, now)
    old = target.stat().st_mtime
    link.symlink_to(target)
    assert durable_io.freshen(link) is True
    assert target.stat().st_mtime == old
    assert durable_io.freshen(target) is True
    assert target.stat().st_mtime > old
    assert durable_io.freshen(tmp_path / "missing") is False


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


def test_regression_gc_keeps_a_file_freshened_just_before_it_moves(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    _, blob = unreachable_blob(tmp_path, now)
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
    _, blob = unreachable_blob(tmp_path, now)
    moved_name_freed(monkeypatch, blob, lambda: blob.write_bytes(REDO))
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
    first, blob = unreachable_blob(tmp_path, now)
    landing = change(first.id, REDO)
    _around_first_move(monkeypatch, blob, lambda: FileBackend(tmp_path).commit(SHOW_ID, *landing))
    report = gc(tmp_path, at=now, confirm=True)
    monkeypatch.undo()
    assert report.removed == ()
    assert report.skipped == ((folder(tmp_path).relative_to(tmp_path).as_posix(), MOVED),)
    assert fsck(tmp_path).findings == ()
    assert FileBackend(tmp_path).log(SHOW_ID) == [first, landing[0]]


def test_regression_gc_puts_back_a_file_a_slot_names_once_set_aside(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The backend protocol lets a commit name a stored blob it does not pass; nothing freshens it.

    Only the check that no slot landed while the blob was aside keeps it.
    """
    now = datetime.now(UTC)
    first, blob = unreachable_blob(tmp_path, now)
    revision, tree, blobs = change(first.id, REDO)
    unpassed = {name: data for name, data in blobs.items() if name != digest(REDO)}

    def land() -> None:
        FileBackend(tmp_path).commit(SHOW_ID, revision, tree, unpassed)

    moved_name_freed(monkeypatch, blob, land)
    report = gc(tmp_path, at=now, confirm=True)
    monkeypatch.undo()
    assert report.removed == ()
    assert report.skipped == ((folder(tmp_path).relative_to(tmp_path).as_posix(), MOVED),)
    assert blob.read_bytes() == REDO
    assert fsck(tmp_path).findings == ()
    assert FileBackend(tmp_path).log(SHOW_ID) == [first, revision]


def test_gc_keeps_a_temp_file_touched_after_the_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(UTC)
    unreachable_blob(tmp_path, now)
    temp = tmp_path / "tmp" / ".tmp-in-use"
    temp.write_bytes(b"writing")
    make_old(temp, now)
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
