#!/usr/bin/env python3

"""
fsck_repos.py --- builds small layout-2 repositories for the fsck, repair and gc tests to damage

Contains:
    AT: times every revision the helpers build
    SHOW_ID: names the show the helpers build
    OTHER_ID: names a second show
    STATIC_BYTES: holds the blob every revision's tree keeps
    DAY: is one day, the age a torn or stale file must pass
    numbered_change(): builds revision n, which changes the render path only after the first
    landed_revisions(): commits revisions 0 to count - 1 through FileBackend
    show_path(): names a show's folder
    slot_path(): names a slot of SHOW_ID
    record_path(): names the record of a revision of SHOW_ID
    blob_path(): names the blob of some bytes in SHOW_ID
    relative(): names a path relative to the root, as findings do
    age_by(): sets a file's times to a moment before AT
    snapshot(): maps every path under a root to its bytes and modification time
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import SLOT_DIGITS, FileBackend
from e8s.show.history_models import Revision
from e8s.show.show_folders import show_folder

AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
SHOW_ID = "fsck_show"
OTHER_ID = "other_show"
STATIC = "/static.txt"
RENDER = "/render.txt"
STATIC_BYTES = b"static"
DAY = timedelta(days=1)


def numbered_change(parent: str, n: int) -> tuple[Revision, dict[str, str], dict[str, bytes]]:
    """Builds revision n: the first writes both paths, every later one changes the render only."""
    render = f"render {n}".encode()
    tree = {STATIC: digest(STATIC_BYTES), RENDER: digest(render)}
    changed = (RENDER, STATIC) if parent == "" else (RENDER,)
    changes = {path: tree[path] for path in changed}
    revision = Revision(
        id=revision_id(parent, AT, changes), parent=parent, at=AT, changed=tuple(sorted(changed))
    )
    return revision, tree, {digest(render): render, digest(STATIC_BYTES): STATIC_BYTES}


def landed_revisions(root: Path, count: int, show_id: str = SHOW_ID) -> list[Revision]:
    backend, parent, landed = FileBackend(root), "", []
    for n in range(count):
        revision, tree, blobs = numbered_change(parent, n)
        backend.commit(show_id, revision, tree, blobs)
        landed.append(revision)
        parent = revision.id
    return landed


def show_path(root: Path, show_id: str = SHOW_ID) -> Path:
    return root / "shows" / show_folder(show_id)


def slot_path(root: Path, seq: int) -> Path:
    return show_path(root) / "log" / f"{seq:0{SLOT_DIGITS}d}"


def record_path(root: Path, revision: Revision) -> Path:
    return show_path(root) / "records" / f"{revision.id}.json"


def blob_path(root: Path, data: bytes) -> Path:
    return show_path(root) / "objects" / digest(data)


def relative(root: Path, path: Path) -> str:
    return path.relative_to(root).as_posix()


def age_by(path: Path, by: timedelta) -> None:
    then = (AT - by).timestamp()
    os.utime(path, (then, then))


def snapshot(root: Path) -> dict[str, tuple[bytes, int]]:
    return {
        relative(root, path): (
            path.read_bytes() if path.is_file() else b"",
            path.stat().st_mtime_ns,
        )
        for path in sorted(root.rglob("*"))
    }
