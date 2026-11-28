"""
archive_checks.py --- the entries of a show archive and the history checks export and import share

An archive holds one show: `archive.json` naming the format and the show, `log/<seq>` for each
slot, `records/<revision>.json` for each revision and `objects/<digest>` for each blob a tree
names, each encoded exactly as a layout-2 repository stores it. Export and import run the same
history check, so export never writes an archive that import refuses: the slots chain from the
root, every revision ID recomputes from its parent, time and changes, every tree is the tree
before it with the revision's changes, every changed path is a document path (absolute, with no
`..`, `.` or empty segment), and the history fits the caps a repository read keeps.

Contains:
    ARCHIVE_FORMAT: names the archive format in its manifest; a frozen wire string
    ARCHIVE_VERSION: numbers the archive format this release writes and reads
    MANIFEST_ENTRY: names the manifest entry, the first of every archive
    SLOT_PREFIX: starts the name of every slot entry
    RECORD_PREFIX: starts the name of every record entry
    RECORD_SUFFIX: ends the name of every record entry
    OBJECT_PREFIX: starts the name of every blob entry
    MANIFEST_KEYS: names the keys a manifest holds, all of them and no other
    SLOT_ENTRY: matches the name of a slot entry
    RECORD_ENTRY: matches the name of a record entry
    OBJECT_ENTRY: matches the name of a blob entry
    ENTRY_NAMES: describes the entry names an archive may hold, for errors
    EXPORT_DAMAGE_HINT: tells the caller how to find the damage an export found in a repository
    LEGACY_ID_HINT: tells the caller why a history whose IDs do not recompute has no archive
    slot_entry(): names the entry of a slot
    record_entry(): names the entry of a revision record
    object_entry(): names the entry of a blob
    manifest_bytes(): encodes the manifest naming the format and the show
    entry_cap(): returns an entry's size cap, refusing a name outside the archive's entries
    manifest_show(): checks the manifest entry and returns the show ID it names
    HistoryCheck: checks revisions in slot order and collects the blobs their trees name
"""

import re
from collections.abc import Mapping

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError, StorageError, VersionError, bounded_repr
from e8s.core.json_types import JsonValue
from e8s.core.strict_json import parse_strict_json
from e8s.show.archive_zip import ARCHIVE_HINT, archive_error
from e8s.show.backend import MAX_REVISIONS, SHORT_ID, history_too_long_error
from e8s.show.backend_checks import ReadBudget, applied_tree, recomputes
from e8s.show.document_dump import dump_json
from e8s.show.document_paths import split_path
from e8s.show.document_repository import require_new_show_id
from e8s.show.file_backend import (
    LOG_READ_MAX_BYTES,
    MANIFEST_MAX_BYTES,
    SLOT_DIGITS,
    SLOT_MAX_BYTES,
)
from e8s.show.history_models import Revision
from e8s.show.layout1 import BLOB_MAX_BYTES, TREE_MAX_BYTES
from e8s.show.storage import ROOT_REVISION

ARCHIVE_FORMAT = "e8.show-archive"
ARCHIVE_VERSION = 1
MANIFEST_ENTRY = "archive.json"
SLOT_PREFIX = "log/"
RECORD_PREFIX = "records/"
RECORD_SUFFIX = ".json"
OBJECT_PREFIX = "objects/"
MANIFEST_KEYS = frozenset({"format", "show", "version"})
SLOT_ENTRY = re.compile(rf"log/[0-9]{{{SLOT_DIGITS}}}")
RECORD_ENTRY = re.compile(r"records/[0-9a-f]{64}\.json")
OBJECT_ENTRY = re.compile(r"objects/[0-9a-f]{64}")
ENTRY_NAMES = "archive.json, log/<12-digit slot>, records/<revision>.json and objects/<digest>"
EXPORT_DAMAGE_HINT = "run fsck(root) on a file repository to find the damage, then restore it"
LEGACY_ID_HINT = (
    "an archive holds only a history whose every revision ID recomputes from its parent, time "
    "and changes; a show upgraded from layout 1 keeps the IDs of the older scheme, so save its "
    "head as a new show with put_show and archive that one"
)


def slot_entry(seq: int) -> str:
    """Names the entry of slot `seq`, as the slot's file is named in a repository."""
    return f"{SLOT_PREFIX}{seq:0{SLOT_DIGITS}d}"


def record_entry(revision_id: str) -> str:
    """Names the entry of a revision record."""
    return f"{RECORD_PREFIX}{revision_id}{RECORD_SUFFIX}"


def object_entry(digest: str) -> str:
    """Names the entry of a blob."""
    return f"{OBJECT_PREFIX}{digest}"


def manifest_bytes(show_id: str) -> bytes:
    """Encodes the manifest: the archive format, its version and the show the archive holds.

    >>> manifest_bytes("show_1").decode().split()
    ['{', '"format":', '"e8.show-archive",', '"show":', '"show_1",', '"version":', '1', '}']
    """
    return dump_json({"format": ARCHIVE_FORMAT, "show": show_id, "version": ARCHIVE_VERSION})


def entry_cap(name: str) -> int:
    """Returns the size cap of an entry, the cap its file has in a repository.

    Args:
        name: Entry name, untrusted.

    Returns:
        cap: Largest size in bytes the entry may have.

    >>> entry_cap("log/000000000001") == SLOT_MAX_BYTES
    True
    """
    if name == MANIFEST_ENTRY:
        return MANIFEST_MAX_BYTES
    if SLOT_ENTRY.fullmatch(name):
        return SLOT_MAX_BYTES
    if RECORD_ENTRY.fullmatch(name):
        return TREE_MAX_BYTES
    if OBJECT_ENTRY.fullmatch(name):
        return BLOB_MAX_BYTES
    raise archive_error(f"an archive holds only {ENTRY_NAMES}", entry=name)


def _manifest_field(stored: dict[str, JsonValue]) -> tuple[str, int]:
    """Returns the manifest's show and version once its format is the archive format."""
    if stored["format"] != ARCHIVE_FORMAT:
        named = bounded_repr(stored["format"])
        raise archive_error(
            f"it names format {named}, not {ARCHIVE_FORMAT!r}", entry=MANIFEST_ENTRY
        )
    show, version = stored["show"], stored["version"]
    if not isinstance(show, str) or not isinstance(version, int) or isinstance(version, bool):
        raise archive_error("its show must be text and its version a number", entry=MANIFEST_ENTRY)
    return show, version


def manifest_show(raw: bytes) -> str:
    """Checks the manifest entry and returns the show ID it names.

    Args:
        raw: Bytes of the manifest entry, untrusted.

    Returns:
        show_id: The show the archive holds, under the ID rule new shows follow.

    >>> manifest_show(manifest_bytes("show_1"))
    'show_1'
    """
    try:
        stored = parse_strict_json(raw, document=MANIFEST_ENTRY)
    except SchemaError as error:
        raise archive_error("it is not valid JSON", entry=MANIFEST_ENTRY) from error
    if not isinstance(stored, dict) or stored.keys() != MANIFEST_KEYS:
        raise archive_error(f"it must hold exactly {sorted(MANIFEST_KEYS)}", entry=MANIFEST_ENTRY)
    show_id, version = _manifest_field(stored)
    if version > ARCHIVE_VERSION:
        raise VersionError(
            f"archive version {version} is newer than version {ARCHIVE_VERSION}, which this "
            "release reads",
            hint="import it with the release that exported it, or a later one",
            context={"version": version, "supported": ARCHIVE_VERSION},
        )
    if version != ARCHIVE_VERSION:
        raise archive_error(f"version {version} was never written", entry=MANIFEST_ENTRY)
    require_new_show_id(show_id)
    if raw != manifest_bytes(show_id):
        raise archive_error("it is not in the form export_archive writes", entry=MANIFEST_ENTRY)
    return show_id


class HistoryCheck:
    """Checks a show's revisions in slot order and collects the blobs their trees name.

    Each revision's parent must be the revision before, each changed path a document path, its
    ID the hash of its parent, time and changes, and its tree the tree before it
    with its changes. The slots and records together must fit what one log read takes.

    Attributes:
        show_id: Show the history belongs to, named in errors.
        digests: Every blob digest the trees checked so far name.
    """

    def __init__(self, show_id: str, *, is_export: bool) -> None:
        """Starts the check before the first slot.

        Args:
            show_id: Show the history belongs to.
            is_export: Whether a repository's history is checked for export, where a damaged
                history is the repository's and an older-scheme ID is a limit, not damage.
        """
        self.show_id = show_id
        self.digests: set[str] = set()
        self._is_export = is_export
        self._seq = 0
        self._parent = ROOT_REVISION
        self._tree: Mapping[str, str] = {}
        self._budget = ReadBudget(show_id, LOG_READ_MAX_BYTES)

    def add(self, revision: Revision, tree: Mapping[str, str], *, size: int) -> None:
        """Checks the next revision against the ones before it.

        Args:
            revision: The revision of the next slot.
            tree: Path to blob digest at the revision.
            size: Bytes of the revision's slot and record, counted against the read cap.
        """
        self._seq += 1
        if self._seq > MAX_REVISIONS:
            raise history_too_long_error(self.show_id, MAX_REVISIONS)
        self._budget.spend(size)
        if revision.parent != self._parent:
            raise self._damaged(slot_entry(self._seq), "its parent is not the slot before it")
        for path in revision.changed:
            self._require_path(path)
        if not recomputes(revision, tree):
            raise self._unrecomputable(revision)
        if dict(tree) != applied_tree(self._tree, revision, tree):
            reason = "its tree is not the tree before it with this revision's changes"
            raise self._damaged(record_entry(revision.id), reason)
        self.digests.update(tree.values())
        self._parent, self._tree = revision.id, tree

    def _require_path(self, path: str) -> None:
        """Refuses a changed path that is not a document path, such as one with a `..` segment."""
        try:
            split_path(path)
        except StorageError as error:
            reason = f"it changes {bounded_repr(path)}, which is not a document path"
            raise self._damaged(slot_entry(self._seq), reason) from error

    def _damaged(self, entry: str, reason: str) -> StorageError:
        error = archive_error(reason, entry=entry, show=self.show_id)
        hint = EXPORT_DAMAGE_HINT if self._is_export else ARCHIVE_HINT
        return StorageError(error.message, hint=hint, context=error.context, code=error.code)

    def _unrecomputable(self, revision: Revision) -> StorageError:
        return StorageError(
            f"revision {revision.id[:SHORT_ID]} in slot {self._seq} of {self.show_id} has an ID"
            " hashed under an older scheme or time: it is not the hash of the parent, time and"
            " changes it holds",
            hint=LEGACY_ID_HINT,
            context={
                "show": self.show_id,
                "revision": revision.id,
                "seq": self._seq,
                "entry": record_entry(revision.id),
            },
            code=ErrorCode.STORAGE if self._is_export else ErrorCode.CORRUPT,
        )
