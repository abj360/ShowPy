"""
durable_io.py --- the file-system calls of the file backends: writes, capped reads, error mapping

Every folder, file and link a layout-2 commit creates goes through the write functions, so a test
can stop a commit after any one of them. Every read goes through the capped, no-follow readers and
every OSError is mapped to a StorageError by `mapped_io`.

Contains:
    TEMP_PREFIX: starts the name of every temp file a commit writes
    NO_HARDLINK_ERRNOS: lists the errno values meaning the file system cannot hard-link
    WINDOWS_INVALID_NAME: names the Windows error code for an unusable path component
    COMPONENT_MAX_CHARS: caps the length of one path component on NTFS
    READ_FLAGS: opens stored files without following symlinks or blocking on FIFOs
    SYMLINK_OPEN_ERRNOS: lists the errno values an O_NOFOLLOW open of a symlink raises
    mapped_io(): turns an OSError raised inside a block into a StorageError with a code and hint
    lstat_mode(): returns the lstat mode of a path, or None when it is absent
    is_redirect(): tells whether a path is a symlink or a Windows junction
    read_capped(): reads a regular file of at most a given size, reporting problems through a hook
    make_folder(): creates a folder and its missing parents
    write_temp(): writes bytes to a new temp file in a folder and returns its path
    link_new(): links a file under a new name, reporting whether the name was free
    remove_file(): removes a file when it exists
"""

import errno
import os
import stat
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError

TEMP_PREFIX = ".tmp-"
NO_HARDLINK_ERRNOS = frozenset({errno.EPERM, errno.EXDEV, errno.ENOTSUP})
# Windows has no ENAMETOOLONG for a component over 255 characters: with long paths enabled it
# raises ERROR_INVALID_NAME (errno EINVAL), which also covers bad characters, so the length
# decides. NTFS caps one component at 255 UTF-16 units.
WINDOWS_INVALID_NAME = 123
COMPONENT_MAX_CHARS = 255

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


def _is_name_too_long(error: OSError, path: str) -> bool:
    if error.errno == errno.ENAMETOOLONG:
        return True
    return getattr(error, "winerror", None) == WINDOWS_INVALID_NAME and any(
        len(part) > COMPONENT_MAX_CHARS for part in Path(path).parts
    )


def _storage_error(show_id: str, error: OSError, fallback: Path, *, is_link: bool) -> StorageError:
    path = str(error.filename or fallback)
    context: dict[str, object] = {"path": path, "errno": error.errno}
    if show_id:
        context["show"] = show_id
    owner = f" of {show_id}" if show_id else ""
    if _is_name_too_long(error, path):
        winerror = getattr(error, "winerror", None)
        return StorageError(
            f"name too long{' for ' + show_id if show_id else ''}: {path}",
            hint="use a shorter show ID or a repository root with a shorter path",
            context={**context, "errno": errno.ENAMETOOLONG}
            | ({"winerror": winerror} if winerror is not None else {}),
            code=ErrorCode.NAME_TOO_LONG,
        )
    if is_link and (isinstance(error, PermissionError) or error.errno in NO_HARDLINK_ERRNOS):
        return StorageError(
            f"file system under {path} cannot create hard links",
            hint="put the repository root on a file system that supports hard links",
            context=context,
            code=ErrorCode.NO_HARDLINKS,
        )
    return StorageError(
        f"file-system error in repository{owner}: {error.strerror or error}",
        hint="check that the repository root exists, is writable and is a directory",
        context=context,
        code=ErrorCode.IO,
    )


@contextmanager
def mapped_io(show_id: str, path: Path, *, is_link: bool = False) -> Iterator[None]:
    """Turns an OSError raised inside the block into a StorageError with a code and hint.

    Args:
        show_id: Show the block works on; empty for a repository-wide call, which leaves it out.
        path: Path named in the error when the OSError carries none.
        is_link: Whether the block hard-links, so a refusal means the file system cannot.
    """
    try:
        yield
    except OSError as error:
        raise _storage_error(show_id, error, path, is_link=is_link) from error


def lstat_mode(show_id: str, path: Path) -> int | None:
    """Returns the lstat mode of `path`, or None if absent; ENAMETOOLONG is never hidden."""
    with mapped_io(show_id, path):
        try:
            return os.lstat(path).st_mode
        except FileNotFoundError:
            return None


def is_redirect(path: Path, mode: int) -> bool:
    """Tells whether a path with lstat mode `mode` is a symlink or a Windows junction.

    Python 3.12 stopped reporting a junction as a symlink, so a junction needs its own check.
    """
    return stat.S_ISLNK(mode) or os.path.isjunction(path)


def make_folder(path: Path) -> None:
    """Creates a folder and its missing parents; an existing folder is kept."""
    path.mkdir(parents=True, exist_ok=True)


def write_temp(folder: Path, data: bytes) -> Path:
    """Writes bytes to a new temp file in a folder and flushes them to the drive.

    Args:
        folder: Folder that holds the temp file; it must exist.
        data: Bytes to write.

    Returns:
        temp: Path of the written file; the caller links or removes it.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     write_temp(Path(folder), b"hi").read_bytes()
    b'hi'
    """
    with tempfile.NamedTemporaryFile(dir=folder, delete=False, prefix=TEMP_PREFIX) as handle:
        try:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        except OSError:
            handle.close()
            Path(handle.name).unlink(missing_ok=True)
            raise
    return Path(handle.name)


def link_new(source: Path, target: Path) -> bool:
    """Links `source` under the name `target`, which a link never replaces.

    Args:
        source: Existing file to link.
        target: New name for it.

    Returns:
        is_created: True when the name was free and now names the file, False when it existed.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     source, target = Path(folder) / "a", Path(folder) / "b"
    ...     _ = source.write_bytes(b"x")
    ...     link_new(source, target), link_new(source, target)
    (True, False)
    """
    try:
        os.link(source, target)
    except FileExistsError:
        return False
    return True


def remove_file(path: Path) -> None:
    """Removes a file; a missing one is no error."""
    path.unlink(missing_ok=True)
