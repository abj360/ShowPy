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
"""

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from e8s.show import durable_io
from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import FileBackend
from e8s.show.fsck import fsck
from e8s.show.gc import gc
from e8s.show.history_models import Revision
from e8s.show.show_folders import show_folder

AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
SHOW_ID = "race_show"
STATIC = "/renders/static.txt"
RENDER = "/renders/render.txt"
REDO = b"redo me"
OLD = timedelta(days=2)


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
