"""
stale_temps.py --- decides which temp files a layout-2 commit removes as left behind by a crash

A commit removes temp files last modified more than STALE_TEMP_AGE before its time. The caller's
time alone must not decide that: a time far ahead of the file system's clock, as an imported
archive may carry, would make every commit remove other writers' fresh temp files. The library
reads no clock, so the file system's own clock dates the moment instead: the modification time
of a temp file the commit writes. That probe is written only when some temp file is old enough
by the caller's time, so a commit with nothing to remove writes nothing extra.

Contains:
    EARLIEST: holds the earliest moment a time-zone-aware time can hold
    stale_temp_cutoff(): returns the moment before which a commit removes a temp file
    remove_stale_temps_before(): removes temp files left more than STALE_TEMP_AGE before both
        the commit's time and the file system's clock
"""

import math
from datetime import UTC, datetime, timedelta
from pathlib import Path

from e8s.show import durable_io, durable_reads
from e8s.show.commit_writes import STALE_TEMP_AGE

EARLIEST = datetime.min.replace(tzinfo=UTC)
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def _seconds(at: datetime) -> float | None:
    """Returns a time as seconds since the epoch, or None when the platform cannot place it.

    A naive time is the caller's local time, which the platform may not place before 1970.
    """
    try:
        return at.timestamp()
    except (OverflowError, OSError, ValueError):
        return None


def stale_temp_cutoff(at: datetime, file_system_now: float) -> datetime:
    """Returns the moment before which a commit removes a temp file as left behind by a crash.

    It is STALE_TEMP_AGE before the earlier of the commit's time and the file system's now; a
    moment before the earliest time a datetime holds is clamped to it, which removes nothing.

    Args:
        at: The commit's time; a naive one is the caller's local time.
        file_system_now: The file system's current time, in seconds since the epoch.

    Returns:
        cutoff: A time-zone-aware moment; temp files modified before it are removed.

    >>> now = datetime(2026, 11, 27, tzinfo=UTC)
    >>> forged = datetime(9999, 1, 1, tzinfo=UTC)
    >>> stale_temp_cutoff(forged, now.timestamp()) == now - STALE_TEMP_AGE
    True
    """
    seconds = _seconds(at)
    if seconds is None:
        return EARLIEST
    earlier = min(seconds, file_system_now) - STALE_TEMP_AGE.total_seconds()
    if earlier <= EARLIEST.timestamp():
        return EARLIEST
    return EPOCH + timedelta(seconds=earlier)


def _file_system_now(show_id: str, temp_folder: Path) -> float:
    """Writes an empty temp file and returns its modification time, the file system's now.

    A probe gone before its lookup gives no time, and then nothing is removed.
    """
    probe = durable_io.write_temp(temp_folder, b"")
    try:
        info = durable_reads.lstat_info(show_id, probe)
    finally:
        durable_io.remove_file(probe)
    return -math.inf if info is None else info.st_mtime


def _has_stale_temps(temp_folder: Path, cutoff: datetime) -> bool:
    entries = durable_reads.listed_entries(temp_folder)
    return any(durable_io.is_stale_temp(name, info, cutoff) for name, info in entries)


def remove_stale_temps_before(show_id: str, temp_folder: Path, at: datetime) -> None:
    """Removes the temp files left more than STALE_TEMP_AGE before the commit and the clock.

    Args:
        show_id: Show the commit belongs to, named in errors.
        temp_folder: The root's temp folder; it must exist.
        at: The commit's time, which may be far from the file system's clock either way.
    """
    if not _has_stale_temps(temp_folder, stale_temp_cutoff(at, math.inf)):
        return
    cutoff = stale_temp_cutoff(at, _file_system_now(show_id, temp_folder))
    durable_io.remove_stale_temps(temp_folder, cutoff)
