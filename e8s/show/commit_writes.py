"""
commit_writes.py --- the writes of a layout-2 commit: files placed under names never replaced

Contains:
    STALE_TEMP_AGE: sets how old a temp file must be before a commit removes it
    LAYOUT_BYTES: holds the content of `layout.json` for the layout this backend writes
    write_layout_file(): places `layout.json` under a layout-2 root, after `tmp/` exists
    slot_bytes(): encodes a slot: its revision as one line of canonical JSON
    record_bytes(): encodes a revision record: the revision, its slot number and its tree
    flush_directories(): flushes each folder so the names placed in it survive power loss
    place_file(): writes bytes to a temp file and links it under a name that never gets replaced
    PROBE_SUFFIX: ends the name the hard-link probe links its temp file under
    links_work(): probes once whether the file system under the temp folder can hard-link
    FilePlacer: places files by hard link, or by exclusive create where links do not work
    PLACE_ATTEMPTS: caps how often a name freed by another writer's failed write is placed again
    place_or_check(): places a file, or checks the one another writer placed, placing it again
        when that writer freed the name
    store_blobs(): places each blob, keeping one already there only when it holds the same bytes
    remove_lost_record(): removes the record of a commit that lost its slot to another revision
    store_record(): places a revision record, or checks the one an identical commit placed first
"""

from collections.abc import Callable, Mapping
from datetime import timedelta
from functools import partial
from pathlib import Path

from e8s.core.canonical import jcs
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show import durable_io, durable_reads
from e8s.show.backend_checks import corrupt, require_blob_digest
from e8s.show.document_dump import dump_json
from e8s.show.history_models import Revision
from e8s.show.layout import (
    CURRENT_LAYOUT,
    LAYOUT_FILE,
    LAYOUT_FORMAT,
    TEMP_DIR,
    read_layout,
    read_layout_file,
)
from e8s.show.layout1 import BLOB_MAX_BYTES, TREE_MAX_BYTES, old_layout_error
from e8s.show.torn_reads import is_short_of, read_settled

STALE_TEMP_AGE = timedelta(hours=24)
LAYOUT_BYTES = dump_json({"format": LAYOUT_FORMAT, "layout": CURRENT_LAYOUT})
PROBE_SUFFIX = ".link-probe"
# A writer that takes a name and fails its write frees the name again. Placing it again covers
# that; a name freed on every attempt means writers keep failing, and the commit stops.
PLACE_ATTEMPTS = 3


def _link_unless_unsupported(source: Path, target: Path) -> bool | None:
    """Links `source` under `target`; None when the file system refuses hard links there."""
    try:
        return durable_io.link_new(source, target)
    except OSError as error:
        if error.errno in durable_io.NO_HARDLINK_ERRNOS:
            return None
        raise


def place_file(temp_folder: Path, show_id: str, target: Path, data: bytes) -> bool | None:
    """Writes `data` under `target` through a temp file and a hard link.

    Args:
        temp_folder: Existing folder that holds the temp file.
        show_id: Show the write belongs to, named in errors.
        target: New name for the file; a file already there is never replaced.
        data: Bytes to write.

    Returns:
        is_placed: True when the name was free and now holds the bytes, False when it was taken,
            None when the file system refused the link, which placed nothing.
    """
    temp = durable_io.write_temp(temp_folder, data)
    try:
        with durable_io.mapped_io(show_id, target, is_link=True):
            return _link_unless_unsupported(temp, target)
    finally:
        durable_io.remove_file(temp)


def links_work(temp_folder: Path, show_id: str) -> bool:
    """Probes whether the file system under `temp_folder` can hard-link, with one link.

    Args:
        temp_folder: Existing folder that holds the temp files; the probe leaves nothing in it.
        show_id: Show the commit belongs to, named in errors.

    Returns:
        has_links: False when the link fails with an errno meaning the file system cannot
            hard-link; any other failure raises.
    """
    probe = durable_io.write_temp(temp_folder, b"")
    linked = probe.with_name(probe.name + PROBE_SUFFIX)
    try:
        with durable_io.mapped_io(show_id, linked):
            return _link_unless_unsupported(probe, linked) is True
    finally:
        durable_io.remove_file(linked)
        durable_io.remove_file(probe)


class FilePlacer:
    """Places files under names that are never replaced, by hard link or by exclusive create.

    The first placement probes the file system once. Where it can hard-link, each file is written
    to a temp file and linked into place, so it appears whole. Where it cannot, each file is
    created in place with an exclusive create and written in one call, so readers reread a file
    they find empty or cut short. A link refused later, as where a folder sits on another mount,
    switches the placer to exclusive creates too.

    Attributes:
        temp_folder: Folder that holds the temp files and the probe.

    >>> import tempfile
    >>> with tempfile.TemporaryDirectory() as folder:
    ...     placer = FilePlacer(Path(folder))
    ...     target = Path(folder) / "a"
    ...     placer.place("show_1", target, b"x"), placer.place("show_1", target, b"y")
    (True, False)
    """

    def __init__(self, temp_folder: Path) -> None:
        self.temp_folder = temp_folder
        self._has_links: bool | None = None

    def place(self, show_id: str, target: Path, data: bytes) -> bool:
        """Writes `data` under `target` unless the name is taken.

        Args:
            show_id: Show the write belongs to, named in errors.
            target: New name for the file; a file already there is never replaced.
            data: Bytes to write.

        Returns:
            is_placed: True when the name was free and now holds the bytes.
        """
        if self._has_links is None:
            self._has_links = links_work(self.temp_folder, show_id)
        if self._has_links:
            is_placed = place_file(self.temp_folder, show_id, target, data)
            if is_placed is not None:
                return is_placed
            self._has_links = False
        with durable_io.mapped_io(show_id, target):
            return durable_io.create_new(target, data)


def _freed_name_error(show_id: str, target: Path) -> StorageError:
    return StorageError(
        f"{target.name} was freed {PLACE_ATTEMPTS} times by writers whose writes failed",
        hint="write again; if this repeats, check the free space and health of the repository",
        context={"show": show_id, "path": str(target), "attempts": PLACE_ATTEMPTS},
        code=ErrorCode.IO,
    )


def place_or_check(
    placer: FilePlacer,
    show_id: str,
    target: Path,
    data: bytes,
    check: Callable[[], object],
    *,
    is_known_taken: bool = False,
) -> bool:
    """Places `data` under `target`, or checks the file another writer placed there.

    A writer that creates a file in place and then fails its write removes the file, which frees
    the name another writer found taken. A check that fails while the name is free again places
    the file again, up to PLACE_ATTEMPTS times; then the commit fails with IO.

    Args:
        placer: Places new files.
        show_id: Show the write belongs to, named in errors.
        target: Name of the file.
        data: Bytes to place.
        check: Reads and checks the file under `target`, raising StorageError when it is missing
            or wrong.
        is_known_taken: Whether the name was just seen taken, so the check runs first.

    Returns:
        is_placed: True when this call placed the file, False when it checked another writer's.
    """
    should_place = not is_known_taken
    for _ in range(PLACE_ATTEMPTS):
        if should_place and placer.place(show_id, target, data):
            return True
        should_place = True
        try:
            check()
        except StorageError:
            if durable_reads.lstat_mode(show_id, target) is not None:
                raise
            continue
        return False
    raise _freed_name_error(show_id, target)


def _read_stored(show_id: str, target: Path, kind: str, cap: int, data: bytes) -> bytes:
    """Reads a stored file, rereading it while it is a shorter start of `data`."""
    fail = partial(corrupt, show_id)
    read = partial(durable_reads.read_capped, target, kind, cap, fail, allows_growth=True)
    return read_settled(read, is_short_of(data))


def _keep_fresh(show_id: str, target: Path) -> None:
    """Freshens a file this commit reuses; one removed since its check is placed again."""
    if not durable_io.freshen(target):
        raise corrupt(show_id, target, "file this commit reuses was removed")


def _check_blob(show_id: str, target: Path, data: bytes) -> None:
    stored = _read_stored(show_id, target, "blob", BLOB_MAX_BYTES, data)
    require_blob_digest(show_id, target, stored)
    _keep_fresh(show_id, target)


def store_blobs(
    placer: FilePlacer, show_id: str, objects: Path, blobs: Mapping[str, bytes]
) -> None:
    """Places each blob, keeping one already there only when its bytes hash to its name.

    A blob another writer is still creating in place is reread until it holds these bytes. A
    blob already there is freshened, so a clean-up keeps it until this commit's slot lands; one
    removed meanwhile is placed again.

    Args:
        placer: Places new files.
        show_id: Show the blobs belong to, named in errors.
        objects: Folder that holds the show's blobs.
        blobs: Bytes by digest.
    """
    for digest, data in blobs.items():
        target = objects / digest
        is_taken = durable_reads.lstat_mode(show_id, target) is not None
        check = partial(_check_blob, show_id, target, data)
        place_or_check(placer, show_id, target, data, check, is_known_taken=is_taken)


def store_record(placer: FilePlacer, show_id: str, path: Path, record: bytes) -> None:
    """Places a revision record, or checks the one an identical commit placed first.

    A record another writer is still creating in place is reread until it holds these bytes. A
    record already there is freshened, as a reused blob is.

    Args:
        placer: Places new files.
        show_id: Show the record belongs to, named in errors.
        path: Name of the record file.
        record: Encoded record bytes.
    """

    def check() -> None:
        if _read_stored(show_id, path, "revision record", TREE_MAX_BYTES, record) != record:
            raise corrupt(show_id, path, "revision record differs from this commit")
        _keep_fresh(show_id, path)

    place_or_check(placer, show_id, path, record, check)


def remove_lost_record(path: Path, winner_id: str, revision_id: str) -> None:
    """Removes the record of a commit that lost its slot, unless the winner is the same revision.

    Args:
        path: Name of the losing commit's record file.
        winner_id: ID of the revision that holds the slot the commit wanted.
        revision_id: ID of the losing commit; the same ID means the winner shares the record.
    """
    if winner_id != revision_id:
        durable_io.remove_file(path)


def flush_directories(*folders: Path, is_commit_point: bool = False) -> None:
    """Flushes each folder so the names placed in it survive power loss.

    Args:
        folders: Folders whose entries were just created.
        is_commit_point: Whether the entries make a commit visible, so every folder gets a full
            flush.
    """
    for folder in folders:
        durable_io.flush_directory(folder, is_commit_point=is_commit_point)


def write_layout_file(root: Path, show_id: str, placer: FilePlacer) -> None:
    """Creates `tmp/` and places `layout.json`, or checks the one already there.

    Args:
        root: Repository root folder.
        show_id: Show the commit belongs to, named in errors.
        placer: Places new files; its temp folder is `tmp/` under `root`.
    """
    if read_layout(root) != CURRENT_LAYOUT:
        raise old_layout_error(root, show_id)
    durable_io.make_folder(root / TEMP_DIR)
    check = partial(read_layout_file, root)
    place_or_check(placer, show_id, root / LAYOUT_FILE, LAYOUT_BYTES, check)


def slot_bytes(revision: Revision) -> bytes:
    """Encodes a slot: its revision as one line of canonical JSON, which readers require.

    >>> from datetime import UTC, datetime
    >>> at = datetime(2026, 11, 27, tzinfo=UTC)
    >>> slot = slot_bytes(Revision(id="a" * 64, parent="", at=at))
    >>> slot.splitlines() == [slot.rstrip()], slot != slot.rstrip()
    (True, True)
    """
    return jcs(revision.to_json()) + b"\n"


def record_bytes(revision: Revision, seq: int, tree: Mapping[str, str]) -> bytes:
    """Encodes a revision record: the revision, the number of its slot and its tree.

    >>> from datetime import UTC, datetime
    >>> at = datetime(2026, 11, 27, tzinfo=UTC)
    >>> b'"seq": 1' in record_bytes(Revision(id="a" * 64, parent="", at=at), 1, {})
    True
    """
    return dump_json({"revision": revision.to_json(), "seq": seq, "tree": dict(tree)})
