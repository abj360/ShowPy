#!/usr/bin/env python3

"""
fsck_repos.py --- builds small layout-2 repositories for the fsck, repair and gc tests to damage

Contains:
    AT: times every revision the helpers build
    SHOW_ID: names the show the helpers build
    OTHER_ID: names a second show
    STATIC_BYTES: holds the blob every revision's tree keeps
    DAY: is one day, the age a torn or stale file must pass
    STAT_FIELDS: names the lstat fields with_stat() changes by their place in the result
    STAT_TIMES: names the lstat times with_stat() copies and changes
    NS_PER_SECOND: converts a nanosecond time to the float seconds of an lstat time
    numbered_change(): builds revision n, which changes the render path only after the first
    landed_revisions(): commits revisions 0 to count - 1 through FileBackend
    show_path(): names a show's folder
    slot_path(): names a slot of SHOW_ID
    record_path(): names the record of a revision of SHOW_ID
    blob_path(): names the blob of some bytes in SHOW_ID
    relative(): names a path relative to the root, as findings do
    age_by(): sets a file's times to a moment before AT
    snapshot(): maps every path under a root to its bytes and modification time
    codes(): lists the code and path of each finding fsck reports
    with_stat(): returns an lstat result with some fields changed, as another file reports them
    with_zero_inode(): returns an lstat result whose inode is 0
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path

from e8s.show.backend import digest
from e8s.show.document_repository import revision_id
from e8s.show.file_backend import SLOT_DIGITS, FileBackend
from e8s.show.fsck import fsck
from e8s.show.history_models import Revision
from e8s.show.show_folders import show_folder

AT = datetime(2026, 11, 27, 12, 0, tzinfo=UTC)
SHOW_ID = "fsck_show"
OTHER_ID = "other_show"
STATIC = "/static.txt"
RENDER = "/render.txt"
STAT_FIELDS = ("st_mode", "st_ino", "st_dev", "st_nlink", "st_uid", "st_gid", "st_size")
STAT_TIMES = ("st_atime", "st_mtime", "st_ctime", "st_atime_ns", "st_mtime_ns", "st_ctime_ns")
NS_PER_SECOND = 10**9
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


def codes(root: Path, *, at: datetime | None = None) -> list[tuple[str, str]]:
    return [(item.code, item.path) for item in fsck(root, at=at).findings]


def with_stat(info: os.stat_result, **changes: int) -> os.stat_result:
    """Returns a copy of an lstat result with some fields changed, as another file reports them.

    Args:
        info: The lstat result to copy.
        changes: New values by field name: a field of STAT_FIELDS, or a time in nanoseconds
            (`st_mtime_ns`, `st_ctime_ns`, `st_atime_ns`), which sets its float time too.
    """
    fields = list(info)
    times: dict[str, float | int] = {name: getattr(info, name) for name in STAT_TIMES}
    for name, value in changes.items():
        if name in STAT_TIMES:
            times[name] = value
            times[name.removesuffix("_ns")] = value / NS_PER_SECOND
        else:
            fields[STAT_FIELDS.index(name)] = value
    return os.stat_result(fields, times)


def with_zero_inode(info: os.stat_result) -> os.stat_result:
    """Returns an lstat result with st_ino 0, as some Windows and network file systems report."""
    return with_stat(info, st_ino=0)
