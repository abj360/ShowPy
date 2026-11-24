"""
safe_read.py --- reads a stored file without following symlinks, with a size cap

Contains:
    READ_FLAGS: opens stored files without following symlinks or blocking on FIFOs
    SYMLINK_OPEN_ERRNOS: lists the errno values an O_NOFOLLOW open of a symlink raises
    read_capped(): reads a regular file of at most a given size, reporting problems through a hook
"""

import errno
import os
import stat
from collections.abc import Callable
from pathlib import Path
from typing import BinaryIO

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
    path: Path, handle: BinaryIO, kind: str, cap: int, fail: Callable[[Path, str], Exception]
) -> bytes:
    """Reads an open file by its fstat size, which `read(n)` allocates up front."""
    size = os.fstat(handle.fileno()).st_size
    if size > cap:
        raise fail(path, f"{kind} is larger than {cap} bytes")
    data = handle.read(size + 1)
    if len(data) > size:
        raise fail(path, f"{kind} grew while it was read")
    return data


def read_capped(path: Path, kind: str, cap: int, fail: Callable[[Path, str], Exception]) -> bytes:
    """Reads a regular file of at most `cap` bytes, checking its size before reading.

    Args:
        path: File to read; a symlink, a FIFO, a directory or a missing file is refused.
        kind: What the file holds, named in the size messages.
        cap: Largest size accepted, in bytes.
        fail: Builds the exception raised for a refused file from its path and the reason.

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
            return _read_sized(path, handle, kind, cap, fail)
    except (FileNotFoundError, IsADirectoryError) as error:
        raise fail(path, "file is missing or not a file") from error
