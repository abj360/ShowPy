"""
commit_writes.py --- the writes of a layout-2 commit: files placed by exclusive link

Contains:
    flush_directories: flushes each folder so the names placed in it survive power loss
    place_file: writes bytes to a temp file and links it under a name that never gets replaced
    store_blobs: places each blob, keeping one already there only when it holds the same bytes
    store_record: places a revision record, or checks the one an identical commit placed first
"""

from collections.abc import Callable, Mapping
from pathlib import Path

from e8s.show import durable_io
from e8s.show.backend_checks import corrupt
from e8s.show.layout1 import TREE_MAX_BYTES


def place_file(temp_folder: Path, show_id: str, target: Path, data: bytes) -> bool:
    """Writes `data` under `target` through a temp file; False when the name was taken.

    Args:
        temp_folder: Existing folder that holds the temp file.
        show_id: Show the write belongs to, named in errors.
        target: New name for the file; a file already there is never replaced.
        data: Bytes to write.

    Returns:
        is_placed: True when the name was free and now holds the bytes.
    """
    temp = durable_io.write_temp(temp_folder, data)
    try:
        with durable_io.mapped_io(show_id, target, is_link=True):
            return durable_io.link_new(temp, target)
    finally:
        durable_io.remove_file(temp)


def store_blobs(
    temp_folder: Path,
    show_id: str,
    objects: Path,
    blobs: Mapping[str, bytes],
    verify: Callable[[Path], bytes],
) -> None:
    """Places each blob, keeping one already there only when `verify` accepts its bytes.

    Args:
        temp_folder: Existing folder that holds the temp files.
        show_id: Show the blobs belong to, named in errors.
        objects: Folder that holds the show's blobs.
        blobs: Bytes by digest.
        verify: Reads a stored blob and raises when its bytes do not match its name.
    """
    for digest, data in blobs.items():
        target = objects / digest
        if durable_io.lstat_mode(show_id, target) is None and place_file(
            temp_folder, show_id, target, data
        ):
            continue
        verify(target)


def store_record(temp_folder: Path, show_id: str, path: Path, record: bytes) -> None:
    """Places a revision record, or checks the one an identical commit placed first.

    Args:
        temp_folder: Existing folder that holds the temp file.
        show_id: Show the record belongs to, named in errors.
        path: Name of the record file.
        record: Encoded record bytes.
    """
    if place_file(temp_folder, show_id, path, record):
        return
    stored = durable_io.read_capped(
        path, "revision record", TREE_MAX_BYTES, lambda p, why: corrupt(show_id, p, why)
    )
    if stored != record:
        raise corrupt(show_id, path, "revision record differs from this commit")


def flush_directories(*folders: Path, is_commit_point: bool = False) -> None:
    """Flushes each folder so the names placed in it survive power loss.

    Args:
        folders: Folders whose entries were just created.
        is_commit_point: Whether the last entry makes a commit visible, so it gets a full flush.
    """
    for folder in folders:
        durable_io.flush_directory(folder, is_commit_point=is_commit_point)
