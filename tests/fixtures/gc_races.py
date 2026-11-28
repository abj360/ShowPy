#!/usr/bin/env python3

"""
gc_races.py --- builds the shows and seams the gc race and set-aside tests share

Contains:
    AT: times every revision the helpers build
    StrPath: types a path an os function takes
    SHOW_ID: names the show the helpers build
    STATIC: names the path every revision keeps
    RENDER: names the path each revision changes
    REDO: holds the bytes a crashed attempt left
    OLD: is two days, older than every gc cut-off
    MOVED: is gc's reason for a show whose head moved
    Crash: stops a commit the way a killed process would
    change(): builds a revision whose render path holds some bytes
    folder(): names the show's folder
    make_old(): sets a file's times to OLD before a moment
    crashed_attempt(): leaves what a crashed attempt of a change wrote
    unreachable_blob(): lands one revision and leaves an old blob no slot reaches
    moved_name_freed(): runs a callback once gc moved a file away from its name
"""

import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from os import PathLike
from pathlib import Path

import pytest

from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import FileBackend
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


class Crash(BaseException):
    """Stops a commit the way a killed process would, after its blobs and record are written."""


def change(parent: str, data: bytes) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    tree = {STATIC: digest(b"static"), RENDER: digest(data)}
    changed = (RENDER, STATIC) if parent == "" else (RENDER,)
    changes = {path: tree[path] for path in changed}
    revision = Revision(
        id=revision_id(parent, AT, changes), parent=parent, at=AT, changed=tuple(sorted(changed))
    )
    return revision, tree, {digest(data): data, digest(b"static"): b"static"}


def folder(root: Path) -> Path:
    return root / "shows" / show_folder(SHOW_ID)


def make_old(path: Path, now: datetime) -> None:
    then = (now - OLD).timestamp()
    os.utime(path, (then, then))


def crashed_attempt(
    root: Path, attempt: tuple[Revision, dict[str, str], dict[str, bytes]], left: str
) -> list[Path]:
    """Leaves what a crashed commit of `attempt` wrote: its new blob, or its blob and record."""
    revision, tree, blobs = attempt
    blob = folder(root) / "objects" / digest(REDO)
    if left == "blob":
        blob.write_bytes(REDO)
        return [blob]

    def crash(*_args: object) -> None:
        raise Crash

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(FileBackend, "_claim_slot", crash)
        with pytest.raises(Crash):
            FileBackend(root).commit(SHOW_ID, revision, tree, blobs)
    return [blob, folder(root) / "records" / f"{revision.id}.json"]


def unreachable_blob(root: Path, now: datetime) -> tuple[Revision, Path]:
    """Lands one revision and leaves the old blob of a crashed second commit, unreached."""
    first = change("", b"render 0")
    FileBackend(root).commit(SHOW_ID, *first)
    blob = crashed_attempt(root, change(first[0].id, REDO), "blob")[0]
    make_old(blob, now)
    return first[0], blob


def moved_name_freed(
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
