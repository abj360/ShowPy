"""
durable_reads.py --- the reads of the file backends: capped, no-follow reads, lookups and listings

A file swapped for a symlink or a FIFO after it was looked up is never followed or blocked on,
and every read is capped at a size before it allocates.

Contains:
    READ_FLAGS: opens stored files without following symlinks or blocking on FIFOs
    SYMLINK_OPEN_ERRNOS: lists the errno values an O_NOFOLLOW open of a symlink raises
    read_capped(): reads a regular file of at most a given size, reporting problems through a hook
    lstat_info(): returns the lstat result of a path, or None when it is absent
    lstat_mode(): returns the lstat mode of a path, or None when it is absent
    listed_entries(): lists a folder's entries with their lstat results, sorted by name
    is_redirect(): tells whether a path is a symlink or a Windows junction
"""

import errno
import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import BinaryIO

from e8s.show.durable_io import DIRECTORY_FLAGS, IS_WINDOWS, NOFOLLOW_FLAG, mapped_io

# A file swapped in between the lstat check and the open must not be followed (symlink) or
# block the open (FIFO), so the open itself refuses both and fstat checks the descriptor.
# Windows has neither flag; there only the lstat check guards, and creating symlinks needs
# privilege. O_BINARY (Windows only) keeps the C runtime from translating line endings.
READ_FLAGS = (
    os.O_RDONLY
    | getattr(os, "O_NOFOLLOW", 0)
    | getattr(os, "O_NONBLOCK", 0)
    | getattr(os, "O_BINARY", 0)
)
# open(O_NOFOLLOW) on a symlink fails with ELOOP on Linux and macOS, EMLINK on FreeBSD.
SYMLINK_OPEN_ERRNOS = frozenset({errno.ELOOP, errno.EMLINK})


def _open_no_follow(path: Path, fail: Callable[[Path, str], Exception]) -> int:
    try:
        return os.open(path, READ_FLAGS)
    except OSError as error:
        if error.errno in SYMLINK_OPEN_ERRNOS:
            raise fail(path, "file is a symbolic link") from error
        raise


def _read_sized(
    path: Path,
    handle: BinaryIO,
    kind: str,
    cap: int,
    fail: Callable[[Path, str], Exception],
    *,
    allows_growth: bool,
) -> bytes:
    """Reads an open file by its fstat size, which `read(n)` allocates up front."""
    size = os.fstat(handle.fileno()).st_size
    if size > cap:
        raise fail(path, f"{kind} is larger than {cap} bytes")
    data = handle.read(size + 1)
    if len(data) > size:
        if allows_growth:
            return data[:size]
        raise fail(path, f"{kind} grew while it was read")
    return data


def read_capped(
    path: Path,
    kind: str,
    cap: int,
    fail: Callable[[Path, str], Exception],
    *,
    allows_growth: bool = False,
) -> bytes:
    """Reads a regular file of at most `cap` bytes, checking its size before reading.

    Args:
        path: File to read; a symlink, a FIFO, a directory or a missing file is refused.
        kind: What the file holds, named in the size messages.
        cap: Largest size accepted, in bytes.
        fail: Builds the exception raised for a refused file from its path and the reason.
        allows_growth: Whether a file that grows while it is read, as one another writer is
            still filling in place does, returns the bytes it held when the read began instead
            of being refused.

    Returns:
        data: The file's bytes.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     (Path(folder) / "a").write_bytes(b"hi")
    ...     read_capped(Path(folder) / "a", "note", 16, lambda p, why: ValueError(why))
    2
    b'hi'
    """
    try:
        mode = path.lstat().st_mode
        if stat.S_ISLNK(mode):
            raise fail(path, "file is a symbolic link")
        if not stat.S_ISREG(mode):
            raise fail(path, "file is missing or not a regular file")
        with os.fdopen(_open_no_follow(path, fail), "rb") as handle:
            if not stat.S_ISREG(os.fstat(handle.fileno()).st_mode):
                raise fail(path, "file is missing or not a regular file")
            return _read_sized(path, handle, kind, cap, fail, allows_growth=allows_growth)
    except (FileNotFoundError, IsADirectoryError) as error:
        raise fail(path, "file is missing or not a file") from error


def lstat_info(show_id: str, path: Path) -> os.stat_result | None:
    """Returns the lstat result of `path`, or None if absent; ENAMETOOLONG is never hidden."""
    with mapped_io(show_id, path):
        try:
            return os.lstat(path)
        except FileNotFoundError:
            return None


def lstat_mode(show_id: str, path: Path) -> int | None:
    """Returns the lstat mode of `path`, or None if absent; ENAMETOOLONG is never hidden."""
    info = lstat_info(show_id, path)
    return None if info is None else info.st_mode


def listed_entries(
    folder: Path, *, follows_links: bool = False
) -> list[tuple[str, os.stat_result]]:
    """Lists a folder's entries with their lstat results, sorted by name.

    The folder is opened without following a symlink where the system allows it, so a folder
    swapped for a link after its caller checked it is refused rather than listed. An entry that
    vanishes between the listing and its lstat was removed by a writer, so it is left out.

    Args:
        folder: Existing folder.
        follows_links: Whether `folder` may be a symlink; true only for the repository root,
            which a host may link, while every folder under it is checked to be a real folder.

    Returns:
        entries: Each entry's name and lstat result, sorted by name.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     _ = (Path(folder) / "b").write_bytes(b"x")
    ...     [(name, info.st_size) for name, info in listed_entries(Path(folder))]
    [('b', 1)]
    """
    if IS_WINDOWS or os.scandir not in os.supports_fd:
        with os.scandir(folder) as entries:
            return _entry_stats(entries)
    descriptor = os.open(
        folder, DIRECTORY_FLAGS & ~NOFOLLOW_FLAG if follows_links else DIRECTORY_FLAGS
    )
    try:
        with os.scandir(descriptor) as entries:
            return _entry_stats(entries)
    finally:
        os.close(descriptor)


def _entry_stats(entries: Iterator[os.DirEntry[str]]) -> list[tuple[str, os.stat_result]]:
    listed: list[tuple[str, os.stat_result]] = []
    for entry in entries:
        try:
            listed.append((entry.name, entry.stat(follow_symlinks=False)))
        except FileNotFoundError:
            continue
    return sorted(listed, key=lambda item: item[0])


def is_redirect(path: Path, mode: int) -> bool:
    """Tells whether a path with lstat mode `mode` is a symlink or a Windows junction.

    Python 3.12 stopped reporting a junction as a symlink, so a junction needs its own check.
    """
    return stat.S_ISLNK(mode) or os.path.isjunction(path)
