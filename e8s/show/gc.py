"""
gc.py --- removes what no slot of a layout-2 file repository reaches, and stale temp files

Contains:
    gc(): removes unreachable records and blobs and stale temp files, or reports them in a dry run
"""

import stat
from datetime import datetime
from pathlib import Path

from e8s.show.commit_writes import STALE_TEMP_AGE
from e8s.show.durable_io import lstat_info, mapped_io, remove_file
from e8s.show.file_backend import LOG_DIR, SLOT_DIGITS
from e8s.show.fsck import require_zoned
from e8s.show.fsck_scan import scan_repository
from e8s.show.fsck_show import TORN_FILE, Leftover, ShowScan, relative_path
from e8s.show.storage_models import GcReport


def _is_old(leftover: Leftover, cutoff: datetime) -> bool:
    return leftover.info.st_mtime < cutoff.timestamp()


def _has_moved(show: ShowScan) -> bool:
    """Tells whether a slot landed after the one the check found last."""
    following = show.folder / LOG_DIR / f"{show.last_seq + 1:0{SLOT_DIGITS}d}"
    return lstat_info(show.show_id, following) is not None


def _is_unchanged(leftover: Leftover, cutoff: datetime) -> bool:
    """Tells whether a file is still the regular file listed, and still older than the cut-off."""
    info = lstat_info("", leftover.path)
    return (
        info is not None
        and stat.S_ISREG(info.st_mode)
        and (info.st_dev, info.st_ino) == (leftover.info.st_dev, leftover.info.st_ino)
        and info.st_mtime < cutoff.timestamp()
    )


def _removed(leftovers: list[Leftover], cutoff: datetime) -> list[Leftover]:
    """Removes each leftover that is unchanged since it was listed, returning those removed."""
    removed: list[Leftover] = []
    for leftover in leftovers:
        if _is_unchanged(leftover, cutoff):
            remove_file(leftover.path)
            removed.append(leftover)
    return removed


def _report(
    root: Path, leftovers: list[Leftover], skipped: list[Path], *, dry_run: bool
) -> GcReport:
    return GcReport(
        removed=tuple(sorted(relative_path(root, item.path) for item in leftovers)),
        bytes_freed=sum(item.info.st_size for item in leftovers),
        dry_run=dry_run,
        skipped=tuple(sorted(relative_path(root, folder) for folder in skipped)),
    )


def gc(root: Path, *, at: datetime, confirm: bool = False) -> GcReport:
    """Removes the records and blobs no slot reaches, and temp files crashed writers left.

    Reachability is computed from the slots, reading each slot's record and tree, never from
    file names. A file is removed only when it was last modified more than STALE_TEMP_AGE before
    `at`, so the record and blobs of a commit still in flight, written before its slot lands, are
    kept. A show with any failing finding is left untouched and listed as skipped, and so is a
    show whose head moved while gc ran. Slots, manifests, the layout file, `assets/` and every
    entry gc does not understand are never touched, and no link is followed. Running it twice
    removes nothing the second time.

    Args:
        root: Repository root folder; a symlinked root is followed.
        at: The caller's time, with a time zone; the cut-off is STALE_TEMP_AGE before it.
        confirm: Whether to remove the files; without it gc only reports what it would remove.

    Returns:
        report: The paths removed (or that would be), the bytes they held, and the show folders
            skipped.

    Raises:
        VersionError: The root holds layout 1, or a newer layout than this release reads.
        StorageError: The root is missing (IO), holds no repository (CORRUPT), or cannot be
            read (IO).

    >>> import tempfile
    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.document_repository import DocumentRepository
    >>> from e8s.show.file_backend import FileBackend
    >>> at = datetime(2026, 11, 27, tzinfo=UTC)
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     repo = DocumentRepository(FileBackend(Path(folder)))
    ...     _ = repo.put_show("show_1", two_set_show(), expected_revision="", at=at)
    ...     gc(Path(folder), at=at).removed
    ()
    """
    require_zoned(at)
    cutoff = at - STALE_TEMP_AGE
    with mapped_io("", root):
        scan = scan_repository(root, None, at)
        is_sound = not any(item.code == TORN_FILE for item in scan.findings)
        healthy = [show for show in scan.shows if is_sound and not show.is_failing]
        skipped = [show.folder for show in scan.shows if not is_sound or show.is_failing]
        candidates = {
            show.folder: [item for item in show.unreachable if _is_old(item, cutoff)]
            for show in healthy
        }
        if not confirm:
            leftovers = [*scan.temps, *(item for items in candidates.values() for item in items)]
            return _report(root, leftovers, skipped, dry_run=True)
        removed = _removed(scan.temps, cutoff)
        for show in healthy:
            if _has_moved(show):
                skipped.append(show.folder)
            else:
                removed.extend(_removed(candidates[show.folder], cutoff))
        return _report(root, removed, skipped, dry_run=False)
