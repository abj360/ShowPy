"""
archive_reading.py --- verifies a whole show archive before anything of it is written

The archive is untrusted. Its ZIP form, every size and every CRC are checked first, then its
manifest, then each slot, record and blob with the checks a layout-2 repository and fsck apply
to the same files: slots run from 1 with no gap and chain from the root, each record names its
slot's revision and number, each ID recomputes, each tree is the tree before it with its
revision's changes, every blob a tree names is present and hashes to its name, and no entry is
left that no slot reaches. A failed check names the entry, never its bytes.

Contains:
    VerifiedArchive: holds a verified archive's show, revisions, records and blobs
    verified_archive(): checks every entry of an archive and returns its history
    archive_record(): checks the record entry of one slot and returns the record
"""

from collections.abc import Callable
from functools import partial
from pathlib import Path
from typing import NamedTuple

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show.archive_checks import (
    MANIFEST_ENTRY,
    OBJECT_PREFIX,
    RECORD_PREFIX,
    RECORD_SUFFIX,
    SLOT_PREFIX,
    HistoryCheck,
    entry_cap,
    manifest_show,
    object_entry,
    record_entry,
    slot_entry,
)
from e8s.show.archive_zip import ZipEntry, archive_error, read_entries
from e8s.show.backend import MAX_REVISIONS
from e8s.show.backend_checks import (
    FIRST_SEQ,
    Record,
    parsed_json,
    parsed_record,
    parsed_slot,
    require_blob_digest,
    require_slot_record,
)
from e8s.show.commit_writes import record_bytes, slot_bytes
from e8s.show.history_models import Revision

# HistoryCheck recomputes every ID itself, with an error that teaches the older-scheme limit, so
# the record check is given a legacy prefix past any slot.
NO_LEGACY_PREFIX = MAX_REVISIONS + 1
REPOSITORY_PREFIX = "repository is corrupt: "
FORM_REASON = "it is not in the bytes a repository writes"


class VerifiedArchive(NamedTuple):
    """Holds an archive whose every entry passed its checks.

    Attributes:
        show_id: Show the archive holds.
        revisions: The slots' revisions, oldest first.
        records: Record entry bytes by revision ID.
        objects: Blob entry bytes by digest.
    """

    show_id: str
    revisions: list[Revision]
    records: dict[str, memoryview]
    objects: dict[str, memoryview]


def _reused[T](entry: str, check: Callable[[], T]) -> T:
    """Runs a repository read check on an entry, reporting its failure as the archive's."""
    try:
        return check()
    except StorageError as error:
        is_corrupt = error.code == ErrorCode.CORRUPT
        reason = error.message.removeprefix(REPOSITORY_PREFIX) if is_corrupt else "it is cut short"
        raise archive_error(reason, entry=entry) from error


def _grouped(
    entries: list[ZipEntry],
) -> tuple[list[ZipEntry], dict[str, memoryview], dict[str, memoryview]]:
    """Splits entries whose names passed `entry_cap` into slots, records and blobs."""
    slots: list[ZipEntry] = []
    records: dict[str, memoryview] = {}
    objects: dict[str, memoryview] = {}
    for entry in entries:
        if entry.name.startswith(SLOT_PREFIX):
            slots.append(entry)
        elif entry.name.startswith(RECORD_PREFIX):
            revision_id = entry.name.removeprefix(RECORD_PREFIX).removesuffix(RECORD_SUFFIX)
            records[revision_id] = entry.data
        else:
            objects[entry.name.removeprefix(OBJECT_PREFIX)] = entry.data
    return slots, records, objects


def _slot_revisions(slots: list[ZipEntry]) -> list[Revision]:
    """Checks that the slots run from 1 with no gap, each in a repository's bytes."""
    if not slots:
        raise archive_error("it holds no slot, so no history")
    revisions: list[Revision] = []
    for seq, entry in enumerate(slots, start=FIRST_SEQ):
        if entry.name != slot_entry(seq):
            raise archive_error(
                f"slots must run from 1 with no gap; slot {seq} is missing", entry=entry.name
            )
        raw = bytes(entry.data)
        revision = _reused(entry.name, partial(parsed_slot, "", Path(entry.name), raw))
        if slot_bytes(revision) != raw:
            raise archive_error(FORM_REASON, entry=entry.name)
        revisions.append(revision)
    return revisions


def _require_record_names(records: dict[str, memoryview], revisions: list[Revision]) -> None:
    """Refuses a slot whose revision has no record, and a record no slot names."""
    named = {revision.id for revision in revisions}
    missing = next((r.id for r in revisions if r.id not in records), None)
    if missing is not None:
        raise archive_error(
            "a slot names this revision, which has no record", entry=record_entry(missing)
        )
    unknown = sorted(records.keys() - named)
    if unknown:
        raise archive_error("no slot names this revision", entry=record_entry(unknown[0]))


def archive_record(seq: int, revision: Revision, data: memoryview) -> Record:
    """Checks the record entry of slot `seq` and returns the record.

    Args:
        seq: The slot's number.
        revision: The revision the slot holds.
        data: Bytes of the record entry.

    Returns:
        record: Its slot number, revision and tree, each checked against the slot.
    """
    name = record_entry(revision.id)
    path, raw = Path(name), bytes(data)
    stored = _reused(name, partial(parsed_json, "", path, "revision record", raw))
    check = partial(parsed_record, "", path, stored, revision.id, NO_LEGACY_PREFIX)
    record = _reused(name, check)
    _reused(name, partial(require_slot_record, "", path, seq, revision, record))
    if record_bytes(revision, seq, record.tree) != raw:
        raise archive_error(FORM_REASON, entry=name)
    return record


def _require_objects(objects: dict[str, memoryview], digests: set[str]) -> None:
    """Refuses a blob a tree names that is absent, one no tree names, and one that hashes wrong."""
    missing = sorted(digests - objects.keys())
    if missing:
        reason = "a tree names this blob, which the archive does not hold"
        raise archive_error(reason, entry=object_entry(missing[0]))
    unknown = sorted(objects.keys() - digests)
    if unknown:
        raise archive_error("no tree names this blob", entry=object_entry(unknown[0]))
    for digest in sorted(objects):
        name = object_entry(digest)
        _reused(name, partial(require_blob_digest, "", Path(name), objects[digest]))


def verified_archive(data: bytes) -> VerifiedArchive:
    """Checks every entry of an untrusted archive and returns the history it holds.

    Args:
        data: The archive.

    Returns:
        archive: The show, its revisions in slot order, and its record and blob entries.

    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.archive import export_archive
    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> repo = InMemoryRepository()
    >>> at = datetime(2026, 11, 27, tzinfo=UTC)
    >>> landed = repo.put_show("show_1", two_set_show(), expected_revision="", at=at)
    >>> verified_archive(export_archive(repo, "show_1")).revisions == [landed]
    True
    """
    entries = read_entries(data, entry_cap)
    if not entries or entries[0].name != MANIFEST_ENTRY:
        raise archive_error(f"it holds no {MANIFEST_ENTRY}, so it is not a show archive")
    show_id = manifest_show(bytes(entries[0].data))
    slots, records, objects = _grouped(entries[1:])
    revisions = _slot_revisions(slots)
    _require_record_names(records, revisions)
    check = HistoryCheck(show_id, is_export=False)
    for seq, (revision, slot) in enumerate(zip(revisions, slots, strict=True), start=FIRST_SEQ):
        record = records[revision.id]
        check.add(
            revision, archive_record(seq, revision, record).tree, size=len(slot.data) + len(record)
        )
    _require_objects(objects, check.digests)
    return VerifiedArchive(show_id, revisions, records, objects)
