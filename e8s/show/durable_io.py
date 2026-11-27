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
    lstat_info(): returns the lstat result of a path, or None when it is absent
    lstat_mode(): returns the lstat mode of a path, or None when it is absent
    listed_entries(): lists a folder's entries with their lstat results, sorted by name
    is_redirect(): tells whether a path is a symlink or a Windows junction
    read_capped(): reads a regular file of at most a given size, reporting problems through a hook
    NOFOLLOW_FLAG: refuses to open a symlink where the system has the flag
    DIRECTORY_FLAGS: opens a folder for a flush without following symlinks
    IS_WINDOWS: tells whether the process runs on Windows, which has no directory flush
    IS_MACOS: tells whether the process runs on macOS, which flushes with fcntl commands
    F_FULLFSYNC: names the macOS fcntl command that flushes the drive's cache
    F_BARRIERFSYNC: names the macOS fcntl command that orders writes without a cache flush
    fcntl_flush(): runs a macOS fcntl flush command on a descriptor
    flush_file(): flushes a written file
    flush_directory(): flushes a folder so its new names survive, fully for the commit point
    make_folder(): creates a folder and its missing parents
    write_temp(): writes bytes to a new temp file in a folder and returns its path
    link_new(): links a file under a new name, reporting whether the name was free
    CREATE_FLAGS: creates a file only where no file or link has its name
    CREATED_FILE_MODE: gives a file created in place the mode of a linked temp file
    open_new(): creates an empty file only where no file or link has its name
    write_new(): writes a new file's bytes in one call, raising when it writes fewer
    create_new(): creates a file under a new name, writes it in one call and flushes it
    remove_file(): removes a file when it exists
    is_stale_temp(): tells whether a folder entry is a temp file last modified before a cut-off
    remove_stale_temps(): removes the temp files in a folder last modified before a cut-off
"""

import errno
import os
import stat
import sys
import tempfile
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import datetime
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
# A folder swapped for a symlink after the lstat checks must not be followed and flushed instead.
NOFOLLOW_FLAG = getattr(os, "O_NOFOLLOW", 0)
DIRECTORY_FLAGS = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | NOFOLLOW_FLAG
CREATE_FLAGS = os.O_WRONLY | os.O_CREAT | os.O_EXCL | NOFOLLOW_FLAG | getattr(os, "O_BINARY", 0)
# tempfile creates its files with this mode, so a file placed by a link has it too.
CREATED_FILE_MODE = 0o600
IS_WINDOWS = sys.platform == "win32"
IS_MACOS = sys.platform == "darwin"
# fcntl commands of macOS (sys/fcntl.h); Python's fcntl module has only F_FULLFSYNC.
F_FULLFSYNC = 51
F_BARRIERFSYNC = 85


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


def make_folder(path: Path) -> None:
    """Creates a folder and its missing parents; an existing folder is kept."""
    path.mkdir(parents=True, exist_ok=True)


def fcntl_flush(descriptor: int, command: int) -> None:
    """Flushes a descriptor with a macOS `fcntl` command."""
    import fcntl

    fcntl.fcntl(descriptor, command)


def flush_file(descriptor: int) -> None:
    """Flushes a written file to the drive, ordered before every later write.

    On macOS `fsync` does not order writes on the drive, so a file gets a barrier flush.

    Args:
        descriptor: Open descriptor of the file.
    """
    if IS_MACOS:
        fcntl_flush(descriptor, F_BARRIERFSYNC)
    else:
        os.fsync(descriptor)


def flush_directory(
    path: Path, *, is_commit_point: bool = False, follows_links: bool = False
) -> None:
    """Flushes a folder so the names created in it survive power loss; a no-op on Windows.

    On macOS a folder gets a barrier flush, which orders its entries before every later write.
    The folder that holds a commit's slot gets a full flush instead: once it returns, the slot's
    name and everything the slot names are on the drive.

    Args:
        path: Existing folder.
        is_commit_point: Whether the folder's new entry makes a commit visible.
        follows_links: Whether `path` may be a symlink; true only for the repository root, which
            a host may link, while every folder under it is checked to be a real folder.
    """
    if IS_WINDOWS:
        return
    descriptor = os.open(
        path, DIRECTORY_FLAGS & ~NOFOLLOW_FLAG if follows_links else DIRECTORY_FLAGS
    )
    try:
        if not IS_MACOS:
            os.fsync(descriptor)
        else:
            fcntl_flush(descriptor, F_FULLFSYNC if is_commit_point else F_BARRIERFSYNC)
    finally:
        os.close(descriptor)


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
            flush_file(handle.fileno())
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


def open_new(target: Path) -> int | None:
    """Creates an empty file only where no file or link has the name `target`.

    Args:
        target: New name for the file.

    Returns:
        descriptor: Open descriptor of the new file for writing, or None when the name was taken.
    """
    try:
        return os.open(target, CREATE_FLAGS, CREATED_FILE_MODE)
    except FileExistsError:
        return None


def write_new(descriptor: int, target: Path, data: bytes) -> None:
    """Writes the bytes of a new file in one call, raising OSError when it writes fewer.

    Args:
        descriptor: Open descriptor of the new file.
        target: Name of the file, named in the error.
        data: Bytes to write.
    """
    written = os.write(descriptor, data)
    if written != len(data):
        raise OSError(errno.EIO, f"wrote {written} of {len(data)} bytes", str(target))


def create_new(target: Path, data: bytes) -> bool:
    """Creates `target`, which must not exist, writes `data` in one call and flushes it.

    A file system without hard links places files this way. Until the write returns, a reader
    can find the file empty, so readers of such files reread them. A write that fails, or writes
    less than `data`, removes the file it created and raises: only a shorter start was ever in
    place, which no reader accepts. Once every byte is written a reader may already rely on the
    file, so a failing flush keeps it and raises; so does an interruption of the write, which may
    have written every byte.

    Args:
        target: New name for the file; a file or link already there is never replaced.
        data: Bytes to write.

    Returns:
        is_created: True when the name was free and now holds the bytes, False when it existed.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     target = Path(folder) / "a"
    ...     create_new(target, b"x"), create_new(target, b"y"), target.read_bytes()
    (True, False, b'x')
    """
    descriptor = open_new(target)
    if descriptor is None:
        return False
    try:
        write_new(descriptor, target, data)
    except OSError:
        os.close(descriptor)
        remove_file(target)
        raise
    except BaseException:
        os.close(descriptor)
        raise
    try:
        flush_file(descriptor)
    finally:
        os.close(descriptor)
    return True


def remove_file(path: Path) -> None:
    """Removes a file; a missing one is no error."""
    path.unlink(missing_ok=True)


def is_stale_temp(name: str, info: os.stat_result, cutoff: datetime) -> bool:
    """Tells whether a folder entry is a temp file whose modification time is before `cutoff`.

    Args:
        name: The entry's name; a temp file's starts with TEMP_PREFIX.
        info: The entry's lstat result; only a regular file counts.
        cutoff: Moment before which a temp file counts as left behind by a crashed writer.
    """
    return (
        name.startswith(TEMP_PREFIX)
        and stat.S_ISREG(info.st_mode)
        and info.st_mtime < cutoff.timestamp()
    )


def remove_stale_temps(folder: Path, cutoff: datetime) -> None:
    """Removes the temp files in `folder` whose modification time is before `cutoff`.

    A file that vanishes between the listing and the removal belongs to a writer that finished,
    so it is skipped.

    Args:
        folder: Folder that holds the temp files; it must exist.
        cutoff: Moment before which a temp file counts as left behind by a crashed writer.
    """
    with os.scandir(folder) as entries:
        for entry in entries:
            if not entry.name.startswith(TEMP_PREFIX):
                continue
            try:
                info = entry.stat(follow_symlinks=False)
            except FileNotFoundError:
                continue
            if is_stale_temp(entry.name, info, cutoff):
                remove_file(Path(entry.path))
