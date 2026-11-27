"""
backend_checks.py --- the corruption error and the read checks the layout-2 file backend shares

Contains:
    CORRUPT_HINT: tells the caller how to repair a damaged show folder
    FIRST_SEQ: numbers the first slot of every show
    corrupt(): builds the StorageError for a stored file that fails a check
    TORN_SLOT_HINT: tells the caller how to clear a slot its writer left torn
    torn_slot(): builds the StorageError for a slot still empty or cut short after every reread
    parsed_json(): parses a stored JSON file, mapping a parse failure to CORRUPT
    stored_revision(): checks a stored revision and returns it, reading a naive time as UTC
    hashed_time(): returns the time a stored revision's ID was hashed from, naive if stored so
    parsed_slot(): checks the bytes of a slot and returns its revision, or raises TORN_SLOT
    parsed_manifest(): checks a parsed manifest and returns its show ID and `legacy_before` slot
    require_blob_digest(): raises CORRUPT unless a stored blob's bytes hash to its file name
    Record: holds a revision record's slot number, revision and tree
    ReadBudget: counts the bytes one log read takes and refuses to pass its limit
    refuse_redirect(): refuses a folder that is a symlink or a junction
    require_plain_folders(): refuses the first listed folder that is a symlink or a junction
    require_caller_ids(): refuses a commit whose revision, parent, blob or tree IDs are malformed
"""

from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import NamedTuple, TypedDict

from pydantic import TypeAdapter

from e8s.core.canonical import jcs
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError
from e8s.core.json_types import JsonValue
from e8s.core.strict_json import parse_strict_json
from e8s.show import durable_io
from e8s.show.history_models import STORED_TIME_KEY, Revision
from e8s.show.integrity import blob_digest
from e8s.show.layout1 import REVISION_ID_PATTERN
from e8s.show.show_folders import show_folder
from e8s.show.storage import ROOT_REVISION
from e8s.show.torn_reads import is_unfinished_line

CORRUPT_HINT = "restore the show folder from a backup or remove the damaged file"
MANIFEST_KEYS = frozenset({"id", "legacy_before"})
SLOT_KEYS = frozenset({"id", "parent", "at", "changed"})
FIRST_SEQ = 1
TORN_SLOT_HINT = (
    "read again later; if the slot stays torn, its writer stopped before writing it: run "
    "fsck(root, repair=True, confirm=True), which removes a torn last slot"
)


def corrupt(show_id: str, path: Path, reason: str) -> StorageError:
    """Builds the CORRUPT StorageError for a stored file that fails a check.

    Args:
        show_id: Show the file belongs to; empty when it is not known yet, which leaves it out.
        path: The damaged file.
        reason: What check it failed.
    """
    context: dict[str, JsonValue] = {"path": str(path), "file": path.name}
    if show_id:
        context["show"] = show_id
    return StorageError(
        f"repository{' of ' + show_id if show_id else ''} is corrupt: {reason}",
        hint=CORRUPT_HINT,
        context=context,
        code=ErrorCode.CORRUPT,
    )


def torn_slot(show_id: str, path: Path) -> StorageError:
    """Builds the TORN_SLOT StorageError for a slot still empty or cut short after every reread.

    Args:
        show_id: Show the slot belongs to.
        path: The torn slot.
    """
    return StorageError(
        f"repository of {show_id} has a torn slot {path.name}: it is empty or cut short",
        hint=TORN_SLOT_HINT,
        context={"path": str(path), "file": path.name, "show": show_id},
        code=ErrorCode.TORN_SLOT,
    )


def parsed_json(show_id: str, path: Path, kind: str, raw: bytes) -> JsonValue:
    """Parses the bytes of a stored JSON file, raising CORRUPT when they are not strict JSON."""
    try:
        return parse_strict_json(raw, document=kind)
    except SchemaError as error:
        raise corrupt(show_id, path, f"{kind} is not valid JSON") from error


def stored_revision(show_id: str, path: Path, stored: JsonValue) -> Revision:
    """Checks a revision read from a slot or a record and returns it.

    Args:
        show_id: Show the file belongs to, named in errors.
        path: File the revision was read from.
        stored: Parsed JSON of the revision.

    Returns:
        revision: The revision, whose ID and parent are 64-digit hex IDs and whose time, when
            stored with no timezone, reads as UTC.
    """
    try:
        revision = Revision._validated(stored, context={STORED_TIME_KEY: True})
    except SchemaError as error:
        raise corrupt(show_id, path, "stored revision is malformed") from error
    ids = [revision.id] if revision.parent == ROOT_REVISION else [revision.id, revision.parent]
    if not all(REVISION_ID_PATTERN.fullmatch(value) for value in ids):
        raise corrupt(show_id, path, "stored revision ID is not a 64-digit hex id")
    return revision


class _StoredTime(TypedDict):
    at: datetime


_STORED_TIME = TypeAdapter(_StoredTime)


def hashed_time(revision: Revision, stored: JsonValue) -> datetime:
    """Returns the time a stored revision's ID was hashed from: its time exactly as stored.

    A time stored with no timezone reads as UTC, but its ID hashes the naive time.

    Args:
        revision: The revision `stored_revision` returned for `stored`.
        stored: Parsed JSON of the revision.
    """
    if _STORED_TIME.validate_python(stored)["at"].utcoffset() is None:
        return revision.at.replace(tzinfo=None)
    return revision.at


def parsed_slot(show_id: str, path: Path, raw: bytes) -> Revision:
    """Checks the bytes of a slot and returns the revision it holds.

    Bytes that are not yet one finished line raise TORN_SLOT; a finished line that is not
    canonical JSON of a revision raises CORRUPT.

    Args:
        show_id: Show the slot belongs to, named in errors.
        path: The slot file.
        raw: Bytes read from it.

    Returns:
        revision: The revision the slot lands.
    """
    if is_unfinished_line(raw):
        raise torn_slot(show_id, path)
    stored = parsed_json(show_id, path, "slot", raw)
    if jcs(stored) + b"\n" != raw:
        raise corrupt(show_id, path, "slot is not one line of canonical JSON")
    if not isinstance(stored, dict) or stored.keys() != SLOT_KEYS:
        raise corrupt(show_id, path, f"slot must hold exactly {sorted(SLOT_KEYS)}")
    return stored_revision(show_id, path, stored)


def parsed_manifest(show_id: str, path: Path, stored: JsonValue) -> tuple[str, int]:
    """Checks a parsed manifest and returns the show ID it names and its `legacy_before` slot.

    Args:
        show_id: Show being read, named in errors; empty when it is not known yet.
        path: The manifest file, whose folder must be the one its show ID encodes.
        stored: Parsed JSON of the manifest.

    Returns:
        manifest: The listed show ID and the first slot whose revision ID is recomputable.
    """
    if not isinstance(stored, dict) or not {"id"} <= stored.keys() <= MANIFEST_KEYS:
        raise corrupt(show_id, path, "manifest must hold id and may hold legacy_before")
    listed, legacy_before = stored["id"], stored.get("legacy_before", FIRST_SEQ)
    if not isinstance(listed, str) or show_folder(listed) != path.parent.name:
        raise corrupt(show_id, path, "manifest names a show stored in another folder")
    if not isinstance(legacy_before, int) or isinstance(legacy_before, bool) or legacy_before < 1:
        raise corrupt(show_id, path, "manifest legacy_before is not a positive integer")
    return listed, legacy_before


def require_blob_digest(show_id: str, path: Path, data: bytes) -> None:
    """Raises CORRUPT unless a stored blob's bytes hash to its file name."""
    if blob_digest(data) != path.name:
        raise corrupt(show_id, path, "blob bytes do not match their digest")


class Record(NamedTuple):
    """Holds a revision record: its slot number, its revision and its tree."""

    seq: int
    revision: Revision
    tree: dict[str, str]


class ReadBudget:
    """Counts the bytes one log() call has read and refuses to pass its limit."""

    def __init__(self, show_id: str, limit: int) -> None:
        self._show_id = show_id
        self._limit = limit
        self._remaining = limit

    def spend(self, size: int) -> None:
        """Takes `size` bytes from the budget, raising TOO_LARGE when it runs out."""
        self._remaining -= size
        if self._remaining < 0:
            raise StorageError(
                f"log of {self._show_id} holds more than {self._limit} bytes",
                hint="save the current state as a new show and continue there",
                context={"show": self._show_id, "cap": self._limit},
                code=ErrorCode.TOO_LARGE,
            )


def _caller_id(show_id: str, value: str, role: str) -> None:
    if not REVISION_ID_PATTERN.fullmatch(value):
        raise StorageError(
            f"{role} {value!r} is not a 64-digit hex id",
            hint="revision IDs and blob digests are 64 lowercase hex digits made by "
            "DocumentRepository.transaction",
            context={"show": show_id, role: value},
            code=ErrorCode.BAD_ID,
        )


def require_caller_ids(
    show_id: str, revision: Revision, tree: Mapping[str, str], blobs: Mapping[str, bytes]
) -> None:
    """Refuses a commit whose revision, parent, blob or tree IDs are not 64-digit hex."""
    _caller_id(show_id, revision.id, "revision id")
    if revision.parent != ROOT_REVISION:
        _caller_id(show_id, revision.parent, "parent id")
    for digest in [*blobs, *tree.values()]:
        _caller_id(show_id, digest, "blob digest")


def refuse_redirect(path: Path, mode: int, show_id: str = "") -> None:
    """Raises CORRUPT when `path`, with stat mode `mode`, is a symlink or a junction."""
    if durable_io.is_redirect(path, mode):
        raise corrupt(show_id, path, "folder is a symbolic link or junction")


def require_plain_folders(show_id: str, *folders: Path) -> None:
    """Raises CORRUPT for the first listed folder that is a symlink or a junction.

    Args:
        show_id: Show the folders belong to, named in the error.
        folders: Folders a read or write passes through; a missing one is fine.
    """
    for path in folders:
        mode = durable_io.lstat_mode(show_id, path)
        if mode is not None:
            refuse_redirect(path, mode, show_id)
