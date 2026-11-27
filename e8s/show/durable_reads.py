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
    moved_file_identity(): returns the lstat fields a move keeps: device, inode, size, mtime
    file_identity(): returns the lstat fields that tell a file and its state apart
    is_same_file(): tells whether two lstat results show one file in one state
    COMPARE_PIECE_BYTES: sets how many bytes of each file one step of a comparison reads
    has_same_bytes(): tells whether two regular files hold the same bytes, reading in pieces
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
    vanishes between the listing and its lstat was removed by a writer, so it is left out. An
    entry the listing reports with inode 0, as every Windows listing does, is looked up again,
    so its result names its device and inode wherever the file system has them.

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
            return _entry_stats(entries, None)
    descriptor = os.open(
        folder, DIRECTORY_FLAGS & ~NOFOLLOW_FLAG if follows_links else DIRECTORY_FLAGS
    )
    try:
        with os.scandir(descriptor) as entries:
            return _entry_stats(entries, descriptor)
    finally:
        os.close(descriptor)


def _entry_lstat(entry: os.DirEntry[str], descriptor: int | None) -> os.stat_result:
    """Returns an entry's lstat result, looked up again where the listing reports inode 0.

    Windows listings report the device, inode and link count as 0, which no lookup of the file
    matches; the lookup goes through the folder's descriptor where the listing used one.
    """
    info = entry.stat(follow_symlinks=False)
    if info.st_ino != 0:
        return info
    if descriptor is None:
        return os.lstat(entry.path)
    return os.lstat(entry.name, dir_fd=descriptor)


def _entry_stats(
    entries: Iterator[os.DirEntry[str]], descriptor: int | None
) -> list[tuple[str, os.stat_result]]:
    listed: list[tuple[str, os.stat_result]] = []
    for entry in entries:
        try:
            listed.append((entry.name, _entry_lstat(entry, descriptor)))
        except FileNotFoundError:
            continue
    return sorted(listed, key=lambda item: item[0])


def is_redirect(path: Path, mode: int) -> bool:
    """Tells whether a path with lstat mode `mode` is a symlink or a Windows junction.

    Python 3.12 stopped reporting a junction as a symlink, so a junction needs its own check.
    """
    return stat.S_ISLNK(mode) or os.path.isjunction(path)


# Two files up to BLOB_MAX_BYTES each are compared a mebibyte at a time, never held whole.
COMPARE_PIECE_BYTES = 2**20


class _RefusedError(Exception):
    """Marks a file a comparison will not open: a link, or not a regular file."""


def _refused(_path: Path, _reason: str) -> _RefusedError:
    return _RefusedError()


def moved_file_identity(info: os.stat_result) -> tuple[int, int, int, int]:
    """Returns what a move keeps of a file and its state: device, inode, size, modification time.

    A link, unlink or rename sets the file's change time on POSIX, so this leaves it out.
    """
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns


def file_identity(info: os.stat_result) -> tuple[int, int, int, int, int]:
    """Returns what tells a file and its state apart: moved_file_identity() and the change time.

    A file system may give a freed inode to the next file it creates, so the inode never proves
    alone that a name still holds the file it held. A replacement or a write changes the size or
    a time, and the change time moves even where a writer sets the modification time back.
    Windows reports the creation time instead, which a replacement also changes.
    """
    return (*moved_file_identity(info), info.st_ctime_ns)


def is_same_file(first: os.stat_result, second: os.stat_result) -> bool:
    """Tells whether two lstat results show one file in one state, by file_identity().

    Python documents st_ino as unique only when non-zero; where a file system reports 0, the
    device, size and times must still all match, and a result with 0 never matches one without.
    """
    return file_identity(first) == file_identity(second)


def _open_regular(path: Path) -> BinaryIO | None:
    """Opens a file for reading only if its descriptor is a regular file; else closes it.

    A folder, FIFO or device swapped in after the lookup is refused by the descriptor's own
    mode, before `os.fdopen`, which raises on a folder without closing the descriptor.
    """
    descriptor = _open_no_follow(path, _refused)
    is_regular = False
    try:
        is_regular = stat.S_ISREG(os.fstat(descriptor).st_mode)
    finally:
        if not is_regular:
            os.close(descriptor)
    return os.fdopen(descriptor, "rb") if is_regular else None


def _pieces_match(first: BinaryIO, second: BinaryIO) -> bool:
    while True:
        piece = first.read(COMPARE_PIECE_BYTES)
        if piece != second.read(COMPARE_PIECE_BYTES):
            return False
        if not piece:
            return True


def has_same_bytes(first: Path, second: Path) -> bool:
    """Tells whether two regular files hold the same bytes, comparing them a piece at a time.

    One file under two names, known by a non-zero inode and the same size and times, holds the
    same bytes and is not read; files of different sizes differ. A link, a folder, a missing file
    or a file swapped for one of those after the lookup never matches. At most
    COMPARE_PIECE_BYTES of each file are in memory at once.

    Args:
        first: One file.
        second: The other file.

    Returns:
        is_same: True only when both are regular files with equal bytes.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     a, b = Path(folder) / "a", Path(folder) / "b"
    ...     _ = a.write_bytes(b"x"), b.write_bytes(b"x")
    ...     has_same_bytes(a, b)
    True
    """
    infos = [lstat_info("", path) for path in (first, second)]
    if not all(info is not None and stat.S_ISREG(info.st_mode) for info in infos):
        return False
    one, other = (info for info in infos if info is not None)
    if one.st_ino != 0 and is_same_file(one, other):
        return True
    if one.st_size != other.st_size:
        return False
    try:
        left = _open_regular(first)
        if left is None:
            return False
        with left:
            right = _open_regular(second)
            if right is None:
                return False
            with right:
                return _pieces_match(left, right)
    except (_RefusedError, FileNotFoundError):
        return False
