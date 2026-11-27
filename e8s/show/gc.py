"""
gc.py --- removes what no slot of a layout-2 file repository reaches, and stale temp files

Contains:
    TORN_LAYOUT_REASON: says a show was skipped because the layout file is torn
    MOVED_REASON: says a show was skipped because a slot landed while gc ran
    gc(): removes unreachable records and blobs and stale temp files, or reports them in a dry run
"""

import os
import stat
from datetime import datetime
from pathlib import Path

from e8s.show import durable_io
from e8s.show.commit_writes import STALE_TEMP_AGE
from e8s.show.durable_io import mapped_io
from e8s.show.durable_reads import has_same_bytes, lstat_info
from e8s.show.file_backend import LOG_DIR, SLOT_DIGITS
from e8s.show.fsck import require_zoned
from e8s.show.fsck_findings import (
    FAILING_CODES,
    SET_ASIDE_PREFIX,
    TORN_FILE,
    Leftover,
    ShowScan,
    relative_path,
)
from e8s.show.fsck_scan import RepositoryScan, scan_repository
from e8s.show.storage_models import GcReport

TORN_LAYOUT_REASON = "layout.json is torn; run fsck(root)"
MOVED_REASON = "a slot landed while gc ran; run gc again"


def _is_old(leftover: Leftover, cutoff: datetime) -> bool:
    return leftover.info.st_mtime < cutoff.timestamp()


def _has_moved(show: ShowScan) -> bool:
    """Tells whether a slot landed after the one the check found last."""
    following = show.folder / LOG_DIR / f"{show.last_seq + 1:0{SLOT_DIGITS}d}"
    return lstat_info(show.show_id, following) is not None


def _is_listed_file(info: os.stat_result, listed: os.stat_result) -> bool:
    """Tells whether an lstat result is the file listed, by its inode where it has one.

    Python documents st_ino as unique only when non-zero; where a file system reports 0, the
    device, size and modification time must all match instead.
    """
    if info.st_ino == 0 or listed.st_ino == 0:
        return (info.st_dev, info.st_size, info.st_mtime_ns) == (
            listed.st_dev,
            listed.st_size,
            listed.st_mtime_ns,
        )
    return (info.st_dev, info.st_ino) == (listed.st_dev, listed.st_ino)


def _is_unchanged(path: Path, leftover: Leftover, cutoff: datetime) -> bool:
    """Tells whether `path` is still the regular file listed, and still older than the cut-off."""
    info = lstat_info("", path)
    return (
        info is not None
        and stat.S_ISREG(info.st_mode)
        and _is_listed_file(info, leftover.info)
        and info.st_mtime < cutoff.timestamp()
    )


def _removed_temps(temps: list[Leftover], cutoff: datetime) -> list[Leftover]:
    """Removes each temp file that is unchanged since it was listed, returning those removed."""
    removed: list[Leftover] = []
    for temp in temps:
        if _is_unchanged(temp.path, temp, cutoff):
            durable_io.remove_file(temp.path)
            removed.append(temp)
    return removed


def _put_back(aside: Path, origin: Path) -> None:
    """Moves a set-aside file back, fresh; when a writer placed its name again, drops the copy.

    A move by rename may replace a file a writer placed meanwhile with the old copy and its old
    time, so the restored file is freshened: no gc removes it before its age passes again.
    """
    if durable_io.move_new(aside, origin):
        durable_io.freshen(origin)
        return
    if has_same_bytes(aside, origin):
        durable_io.remove_file(aside)


def _set_aside_and_remove(show: ShowScan, leftover: Leftover, cutoff: datetime) -> bool:
    """Moves an unreached file aside, removes it if nothing touched it, and puts it back if so.

    A writer that reuses the file freshens it first, and one that finds it gone places it
    again, so a file that is fresh, replaced or placed again once aside goes back, and so does
    every file of a show that landed a slot meanwhile.
    """
    origin = leftover.path
    aside = origin.with_name(SET_ASIDE_PREFIX + origin.name)
    if not _is_unchanged(origin, leftover, cutoff) or not durable_io.move_new(origin, aside):
        return False
    is_untouched = _is_unchanged(aside, leftover, cutoff) and lstat_info("", origin) is None
    if is_untouched and not _has_moved(show):
        durable_io.remove_file(aside)
        return True
    _put_back(aside, origin)
    return False


def _removed_unreached(
    show: ShowScan, leftovers: list[Leftover], cutoff: datetime
) -> tuple[list[Leftover], bool]:
    """Removes a show's unreached files one at a time, stopping once a slot lands.

    Returns:
        removed: The files removed.
        has_moved: Whether a slot landed, so the show was left part way.
    """
    removed: list[Leftover] = []
    for leftover in leftovers:
        if _has_moved(show):
            return removed, True
        if _set_aside_and_remove(show, leftover, cutoff):
            removed.append(leftover)
    return removed, _has_moved(show)


def _put_back_set_aside(scan: RepositoryScan) -> bool:
    """Puts back every file a stopped gc left aside, telling whether there was any."""
    found = [item for show in scan.shows for item in show.set_aside]
    for item in found:
        origin = item.path.with_name(item.path.name.removeprefix(SET_ASIDE_PREFIX))
        _put_back(item.path, origin)
    return bool(found)


def _report(
    root: Path, leftovers: list[Leftover], skipped: list[tuple[Path, str]], *, dry_run: bool
) -> GcReport:
    return GcReport(
        removed=tuple(sorted(relative_path(root, item.path) for item in leftovers)),
        bytes_freed=sum(item.info.st_size for item in leftovers),
        dry_run=dry_run,
        skipped=tuple(sorted((relative_path(root, folder), why) for folder, why in skipped)),
    )


def _why_skipped(show: ShowScan, *, is_sound: bool) -> str:
    """Says why a show is left untouched: a torn layout file, or the codes that fail it."""
    if not is_sound:
        return TORN_LAYOUT_REASON
    codes = sorted({item.code for item in show.findings if item.code in FAILING_CODES})
    return f"fsck finds {', '.join(codes)}; run fsck(root)"


def gc(root: Path, *, at: datetime, confirm: bool = False) -> GcReport:
    """Removes the records and blobs no slot reaches, and temp files crashed writers left.

    Reachability is computed from the slots, reading each slot's record and tree, never from
    file names. A file is removed only when it was last modified more than STALE_TEMP_AGE before
    `at`, so the record and blobs of a commit still in flight, written before its slot lands, are
    kept. A show with any failing finding is left untouched and listed as skipped, and so is a
    show whose head moved while gc ran. Slots, manifests, the layout file, `assets/` and every
    entry gc does not understand are never touched, and no link is followed. Running it twice
    removes nothing the second time.

    A record or blob is first moved aside in its own folder, under a `.gc-` name, and removed
    only if it is still the old file it was and no slot landed meanwhile; otherwise it goes back.
    A commit that reuses such a file freshens it, and one that finds it gone places it again, so
    neither loses it. A gc that stops part way leaves `.gc-` files, which fsck reports and the
    next confirmed gc puts back, freshened, before it checks again.

    Args:
        root: Repository root folder; a symlinked root is followed.
        at: The caller's time, with a time zone; the cut-off is STALE_TEMP_AGE before it. The file
            system's clock dates the files, so an `at` more than STALE_TEMP_AGE ahead of it lets
            gc remove the files of a commit still in flight; pass the current time.
        confirm: Whether to remove the files; without it gc only reports what it would remove.

    Returns:
        report: The paths removed (or that would be), the bytes they held, and each show folder
            skipped with the reason.

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
        if confirm and _put_back_set_aside(scan):
            scan = scan_repository(root, None, at)
        is_sound = not any(item.code == TORN_FILE for item in scan.findings)
        healthy = [show for show in scan.shows if is_sound and not show.is_failing]
        skipped = [
            (show.folder, _why_skipped(show, is_sound=is_sound))
            for show in scan.shows
            if not is_sound or show.is_failing
        ]
        candidates = {
            show.folder: [item for item in show.unreachable if _is_old(item, cutoff)]
            for show in healthy
        }
        if not confirm:
            leftovers = [*scan.temps, *(item for items in candidates.values() for item in items)]
            return _report(root, leftovers, skipped, dry_run=True)
        removed = _removed_temps(scan.temps, cutoff)
        for show in healthy:
            from_show, has_moved = _removed_unreached(show, candidates[show.folder], cutoff)
            removed.extend(from_show)
            if has_moved:
                skipped.append((show.folder, MOVED_REASON))
        return _report(root, removed, skipped, dry_run=False)
