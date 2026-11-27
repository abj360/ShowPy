"""
fsck.py --- checks a layout-2 file repository, and removes what a crashed writer left torn

Contains:
    require_zoned(): refuses a time without a time zone
    report_of(): builds the report of a scan, its findings sorted by show, code and path
    removed_torn_files(): removes each torn file that still passes its checks, returning its path
    fsck(): checks a repository root, or one show in it, and on request repairs torn files
"""

import os
import stat
from collections.abc import Iterable
from datetime import datetime
from functools import partial
from pathlib import Path

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError
from e8s.show.backend_checks import corrupt
from e8s.show.commit_writes import STALE_TEMP_AGE
from e8s.show.durable_io import flush_directory, mapped_io, remove_file
from e8s.show.durable_reads import lstat_info, read_capped
from e8s.show.fsck_findings import FAILING_CODES, Repairable, relative_path
from e8s.show.fsck_scan import RepositoryScan, scan_repository
from e8s.show.storage_models import FsckFinding, FsckReport


def require_zoned(at: datetime | None) -> None:
    """Refuses a time without a time zone, which each host would read as its own local time.

    Args:
        at: The caller's time, or None.
    """
    if at is not None and at.utcoffset() is None:
        raise SchemaError(
            f"time {at.isoformat()} has no time zone",
            hint="pass an aware time, such as datetime.now(UTC)",
            context={"at": at.isoformat()},
        )


def report_of(scan: RepositoryScan, repaired: tuple[str, ...] = ()) -> FsckReport:
    """Builds the report of a scan: every finding, sorted by show, code and path."""
    found: list[FsckFinding] = [*scan.findings]
    for show in scan.shows:
        found.extend(show.findings)
    findings = tuple(sorted(found, key=lambda item: (item.show, item.code, item.path)))
    is_ok = not any(item.code in FAILING_CODES for item in findings)
    return FsckReport(findings=findings, ok=is_ok, repaired=repaired)


def _repair_refused(*, confirm: bool, at: datetime | None) -> StorageError:
    return StorageError(
        "fsck repair removes files, so it needs confirm=True and the caller's time at",
        hint="run fsck(root) to see the findings, then fsck(root, repair=True, confirm=True, "
        "at=datetime.now(UTC)); a torn file is removed only once it is a day older than at",
        context={"confirm": confirm, "at": None if at is None else at.isoformat()},
        code=ErrorCode.NOT_CONFIRMED,
    )


def _identity(info: os.stat_result) -> tuple[int, int, int, int]:
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def _confirm_alone() -> StorageError:
    return StorageError(
        "fsck got confirm=True without repair=True, so it would repair nothing",
        hint="pass repair=True, confirm=True and at to repair, or neither to check only",
        context={"confirm": True, "repair": False},
        code=ErrorCode.NOT_CONFIRMED,
    )


def _is_still_torn(item: Repairable, cutoff: datetime) -> bool:
    """Tells whether a file is still the same regular, torn file older than the cut-off, and safe.

    The file is looked up again after its checks: one replaced or changed meanwhile is kept.
    """
    info = lstat_info("", item.path)
    if info is None or not stat.S_ISREG(info.st_mode) or info.st_mtime >= cutoff.timestamp():
        return False
    try:
        data = read_capped(item.path, "torn file", item.cap, partial(corrupt, ""))
    except StorageError:
        return False
    if not (item.is_torn(data) and item.is_safe()):
        return False
    after = lstat_info("", item.path)
    return after is not None and _identity(after) == _identity(info)


def removed_torn_files(root: Path, items: Iterable[Repairable], cutoff: datetime) -> list[str]:
    """Removes each torn file that still passes its checks, then flushes its folder.

    Each file is looked up and read again just before it is removed: it must still be a regular
    file last modified before `cutoff`, still torn, still safe to remove, and still the same file
    (device, inode, size and modification time) after those checks. A writer that is
    paused, not crashed, can hold a torn file for a moment, so the cut-off keeps every file
    younger than STALE_TEMP_AGE.

    Args:
        root: Repository root folder; its own folder is flushed through a link when it is one.
        items: Torn files the check found, with their checks.
        cutoff: Moment before which a torn file counts as left by a crashed writer.

    Returns:
        removed: Paths removed, relative to the root.
    """
    removed: list[str] = []
    for item in items:
        if _is_still_torn(item, cutoff):
            remove_file(item.path)
            flush_directory(item.path.parent, follows_links=item.path.parent == root)
            removed.append(relative_path(root, item.path))
    return removed


def fsck(
    root: Path,
    show_id: str | None = None,
    *,
    repair: bool = False,
    confirm: bool = False,
    at: datetime | None = None,
) -> FsckReport:
    """Checks a layout-2 file repository and, on request, removes the files a crash left torn.

    It checks the layout file, each manifest, each slot (chain, gaps, torn slots), each record
    (ID, slot number, tree, and that each tree is the previous one with the revision's changes),
    and each blob a tree names (present, hashing to its name). It lists records and blobs no slot
    reaches, temp files, and every entry that is not part of the layout. Findings coded
    UNREACHABLE and STALE_TEMP are left for gc and do not fail the check; every other code does.
    No link under the root is followed; a symlinked root is.

    Without `repair` it reads everything and changes nothing. With `repair=True`,
    `confirm=True` and `at`, it removes only what a crashed writer left torn and nothing refers
    to: a torn last slot after an intact slot, a torn manifest of a show with no slot, and a torn
    layout file of a root with no show folder; each only once it was last modified more than
    STALE_TEMP_AGE before `at`, so a writer that is only paused keeps its file. Then it checks
    again and reports what is left.

    Args:
        root: Repository root folder.
        show_id: The one show to check; None checks the whole root.
        repair: Whether to remove the torn files the check finds.
        confirm: Whether the caller confirms the removal; a repair without it raises, and so
            does confirm without repair.
        at: The caller's time, with a time zone; temp files modified within STALE_TEMP_AGE
            before it are not reported, and a repair needs it. None reports every temp file. The
            file system's clock dates the files, so an `at` more than STALE_TEMP_AGE ahead of it
            lets a repair remove a file a paused writer is still filling; pass the current time.

    Returns:
        report: Every finding, sorted by show, code and path, whether the root passes, and the
            paths a repair removed.

    Raises:
        StorageError: A repair lacks `confirm=True` or `at`, or `confirm=True` comes without
            `repair=True` (NOT_CONFIRMED); the root is missing (IO), holds no repository
            (CORRUPT), or cannot be read (IO).
        VersionError: The root holds layout 1, or a newer layout than this release reads.
        UnknownRefError: `show_id` names no show in the root.

    >>> import tempfile
    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.document_repository import DocumentRepository
    >>> from e8s.show.file_backend import FileBackend
    >>> at = datetime(2026, 11, 27, tzinfo=UTC)
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     repo = DocumentRepository(FileBackend(Path(folder)))
    ...     _ = repo.put_show("show_1", two_set_show(), expected_revision="", at=at)
    ...     report = fsck(Path(folder), repair=True, confirm=True, at=at)
    ...     report.ok, report.repaired
    (True, ())
    """
    require_zoned(at)
    if confirm and not repair:
        raise _confirm_alone()
    if repair and (not confirm or at is None):
        raise _repair_refused(confirm=confirm, at=at)
    scan = scan_repository(root, show_id, at)
    if not repair or at is None:
        return report_of(scan)
    items = [*scan.repairable, *(item for show in scan.shows for item in show.repairable)]
    with mapped_io(show_id or "", root):
        repaired = removed_torn_files(root, items, at - STALE_TEMP_AGE)
    if not repaired:
        return report_of(scan)
    return report_of(scan_repository(root, show_id, at), tuple(sorted(repaired)))
