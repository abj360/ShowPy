"""
backend_checks.py --- the corruption error and the read checks the layout-2 file backend shares

Contains:
    CORRUPT_HINT: tells the caller how to repair a damaged show folder
    FIRST_SEQ: numbers the first slot of every show
    corrupt(): builds the StorageError for a stored file that fails a check
    MISSING_FILE_HINT: tells the caller how to find and put back a blob or record that is missing
    missing_file(): builds the StorageError for a blob or record a read needs that is missing
    TORN_SLOT_HINT: tells the caller how to clear a slot its writer left torn
    torn_slot(): builds the StorageError for a slot still empty or cut short after every reread
    parsed_json(): parses a stored JSON file, mapping a parse failure to CORRUPT
    stored_revision(): checks a stored revision and returns it
    parsed_slot(): checks the bytes of a slot and returns its revision, or raises TORN_SLOT
    require_blob_digest(): raises CORRUPT unless a stored blob's bytes hash to its file name
    recomputes(): tells whether a revision's ID is the current scheme's hash of what it holds
    Record: holds a revision record's slot number, revision and tree
    parsed_record(): checks a parsed revision record against its name and returns it
    require_slot_record(): raises CORRUPT unless a slot's record holds its number and revision
    applied_tree(): returns the previous tree with a revision's changes applied, as the next
        tree must read
    ReadBudget: counts the bytes one log read takes and refuses to pass its limit
    last_present(): gallops from a slot known present and bisects back to the last present one
    refuse_redirect(): refuses a folder that is a symlink or a junction
    require_plain_folders(): refuses the first listed folder that is a symlink or a junction
    require_caller_ids(): refuses a commit whose revision, parent, blob or tree IDs are malformed
    require_readable_sizes(): refuses a commit holding a blob larger than reads accept
"""

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import NamedTuple

from e8s.core.canonical import jcs
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError
from e8s.core.json_types import JsonValue
from e8s.core.strict_json import parse_strict_json
from e8s.show import document_repository, durable_reads
from e8s.show.backend import REVISION_ID_PATTERN
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest
from e8s.show.layout1 import BLOB_MAX_BYTES
from e8s.show.storage import ROOT_REVISION
from e8s.show.torn_reads import is_unfinished_line

CORRUPT_HINT = "restore the show folder from a backup or remove the damaged file"
RECORD_KEYS = frozenset({"revision", "seq", "tree"})
SLOT_KEYS = frozenset({"id", "parent", "at", "changed"})
FIRST_SEQ = 1
MISSING_FILE_HINT = (
    "run fsck(root); if it lists a set-aside .gc- copy of this file, "
    "gc(root, at=datetime.now(UTC), confirm=True) puts it back, otherwise restore the show "
    "folder from a backup"
)
TORN_SLOT_HINT = (
    "read again later; if the slot stays torn, its writer stopped before writing it: run "
    "fsck(root, repair=True, confirm=True, at=datetime.now(UTC)), which removes a torn last "
    "slot once it is a day old"
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


def missing_file(show_id: str, path: Path, reason: str) -> StorageError:
    """Builds the CORRUPT StorageError for a blob or record a read needs that is missing.

    A gc that stopped part way can hold such a file aside, and one confirmed gc puts it back,
    while restoring a backup could roll back commits that already returned.

    Args:
        show_id: Show the file belongs to.
        path: The missing file.
        reason: What needs it.
    """
    error = corrupt(show_id, path, reason)
    return StorageError(
        error.message, hint=MISSING_FILE_HINT, context=error.context, code=error.code
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
        revision: The revision, whose ID and parent are 64-digit hex IDs.
    """
    try:
        revision = Revision._validated(stored)
    except SchemaError as error:
        raise corrupt(show_id, path, "stored revision is malformed") from error
    ids = [revision.id] if revision.parent == ROOT_REVISION else [revision.id, revision.parent]
    if not all(REVISION_ID_PATTERN.fullmatch(value) for value in ids):
        raise corrupt(show_id, path, "stored revision ID is not a 64-digit hex id")
    return revision


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


def require_blob_digest(show_id: str, path: Path, data: bytes | memoryview) -> None:
    """Raises CORRUPT unless a stored blob's bytes hash to its file name."""
    if blob_digest(data) != path.name:
        raise corrupt(show_id, path, "blob bytes do not match their digest")


class Record(NamedTuple):
    """Holds a revision record: its slot number, its revision and its tree."""

    seq: int
    revision: Revision
    tree: dict[str, str]


def recomputes(revision: Revision, tree: Mapping[str, str]) -> bool:
    """Tells whether a revision's ID is the current scheme's hash of its parent, time and changes.

    Args:
        revision: The revision.
        tree: Path to blob digest at the revision; a changed path it lacks was deleted.

    Returns:
        is_current: False for an ID of the older scheme, or one that names other content.
    """
    changes = {changed: tree.get(changed) for changed in revision.changed}
    return document_repository.revision_id(revision.parent, revision.at, changes) == revision.id


def _require_scheme_id(show_id: str, path: Path, record: Record, legacy_before: int) -> None:
    """Requires the ID to be recomputable from the record, except before `legacy_before`."""
    if record.seq >= legacy_before and not recomputes(record.revision, record.tree):
        raise corrupt(show_id, path, "record does not hash to its name")


def parsed_record(
    show_id: str, path: Path, stored: JsonValue, revision_id: str, legacy_before: int
) -> Record:
    """Checks a parsed revision record and returns it.

    Args:
        show_id: Show the record belongs to, named in errors.
        path: The record file.
        stored: Parsed JSON of the record.
        revision_id: Revision the record's name gives; the record must hold it.
        legacy_before: First slot whose revision ID must be recomputable from its record.

    Returns:
        record: The record's slot number, revision and tree, each checked.
    """
    if not isinstance(stored, dict) or stored.keys() != RECORD_KEYS:
        raise corrupt(show_id, path, f"record must hold exactly {sorted(RECORD_KEYS)}")
    seq, tree = stored["seq"], stored["tree"]
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 1:
        raise corrupt(show_id, path, "record seq is not a positive integer")
    revision = stored_revision(show_id, path, stored["revision"])
    if revision.id != revision_id:
        raise corrupt(show_id, path, "record holds another revision than its name")
    if not isinstance(tree, dict) or not all(
        isinstance(value, str) and REVISION_ID_PATTERN.fullmatch(value) for value in tree.values()
    ):
        raise corrupt(show_id, path, "tree is not a mapping of paths to digests")
    record = Record(seq, revision, {key: str(value) for key, value in tree.items()})
    _require_scheme_id(show_id, path, record, legacy_before)
    return record


def require_slot_record(
    show_id: str, path: Path, seq: int, revision: Revision, record: Record
) -> None:
    """Raises CORRUPT unless the record a slot names holds the slot's number and revision.

    Args:
        show_id: Show the slot belongs to, named in errors.
        path: The slot file, named in errors.
        seq: The slot's number.
        revision: The revision the slot holds.
        record: The record of that revision.
    """
    if record.seq != seq:
        raise corrupt(show_id, path, f"record of this slot holds seq {record.seq}, not {seq}")
    if record.revision != revision:
        raise corrupt(show_id, path, "slot differs from the revision in its record")


def applied_tree(
    previous: Mapping[str, str], revision: Revision, tree: Mapping[str, str]
) -> dict[str, str]:
    """Returns the previous tree with each changed path set as the new tree has it.

    A revision's tree must equal this: every path it did not change keeps its previous digest.

    Args:
        previous: Path to blob digest at the revision before; empty before the first.
        revision: The revision, whose `changed` paths may differ.
        tree: Path to blob digest the revision's record holds.

    Returns:
        applied: The tree the revision's record must hold.

    >>> from datetime import UTC, datetime
    >>> at = datetime(2026, 11, 27, tzinfo=UTC)
    >>> revision = Revision(id="a" * 64, parent="", at=at, changed=("/b",))
    >>> applied_tree({"/a": "1", "/b": "2"}, revision, {"/a": "9", "/b": "3"})
    {'/a': '1', '/b': '3'}
    """
    applied = {path: digest for path, digest in previous.items() if path not in revision.changed}
    applied.update({path: tree[path] for path in revision.changed if path in tree})
    return applied


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
    if durable_reads.is_redirect(path, mode):
        raise corrupt(show_id, path, "folder is a symbolic link or junction")


def require_plain_folders(show_id: str, *folders: Path) -> None:
    """Raises CORRUPT for the first listed folder that is a symlink or a junction.

    Args:
        show_id: Show the folders belong to, named in the error.
        folders: Folders a read or write passes through; a missing one is fine.
    """
    for path in folders:
        mode = durable_reads.lstat_mode(show_id, path)
        if mode is not None:
            refuse_redirect(path, mode, show_id)


def require_readable_sizes(
    show_id: str, tree: Mapping[str, str], blobs: Mapping[str, bytes]
) -> None:
    """Refuses blobs over BLOB_MAX_BYTES before any write, since reads would refuse them.

    Args:
        show_id: Show the commit belongs to, named in the error.
        tree: The commit's tree, which names the paths of the oversized blobs.
        blobs: The commit's blob bytes by digest.
    """
    oversized = {d for d, data in blobs.items() if len(data) > BLOB_MAX_BYTES}
    if not oversized:
        return
    paths = sorted(p for p, d in tree.items() if d in oversized)
    raise StorageError(
        f"files of {show_id} over the {BLOB_MAX_BYTES}-byte cap: {', '.join(paths)}",
        hint="keep each file under the cap; store larger media outside the repository",
        context={"show": show_id, "paths": paths, "cap": BLOB_MAX_BYTES},
        code=ErrorCode.TOO_LARGE,
    )


def last_present(is_present: Callable[[int], bool], seq: int) -> int:
    """Gallops from slot `seq + 1`, known present, and bisects back to the last present slot.

    Slots are created in order, so at most 2 log2(n) + 2 lookups find the last of n slots.

    Args:
        is_present: Looks one slot number up.
        seq: Slot before the first one known present.

    Returns:
        last: The number of the last present slot.

    >>> last_present(lambda seq: seq <= 5, 0)
    5
    """
    present, step = seq + 1, 2
    while is_present(seq + step):
        present, step = seq + step, step * 2
    absent = seq + step
    while absent - present > 1:
        middle = (present + absent) // 2
        if is_present(middle):
            present = middle
        else:
            absent = middle
    return present
