"""
durable_io.py --- the file-system writes of a layout-2 commit, each in one small function

Every folder, file and link a commit creates goes through these functions, so a test can stop a
commit after any one of them.

Contains:
    TEMP_PREFIX: starts the name of every temp file a commit writes
    make_folder(): creates a folder and its missing parents
    write_temp(): writes bytes to a new temp file in a folder and returns its path
    link_new(): links a file under a new name, reporting whether the name was free
    remove_file(): removes a file when it exists
"""

import os
import tempfile
from pathlib import Path

TEMP_PREFIX = ".tmp-"


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
