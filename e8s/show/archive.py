"""
archive.py --- carries one show's whole history between repositories as a portable archive

An archive is a ZIP of stored entries holding the show's slots, revision records and reachable
blobs exactly as a layout-2 repository stores them, read through the repository protocol, so a
memory repository exports too. Names are sorted and times zeroed, so equal histories give equal
bytes. Revision IDs, parents and times travel unchanged.

Contains:
    export_archive(): writes one show's whole history as an archive
"""

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError, UnknownRefError
from e8s.core.util import close_matches
from e8s.show.archive_checks import (
    EXPORT_DAMAGE_HINT,
    MANIFEST_ENTRY,
    HistoryCheck,
    entry_cap,
    manifest_bytes,
    object_entry,
    record_entry,
    slot_entry,
)
from e8s.show.archive_zip import ArchiveWriter
from e8s.show.backend_checks import FIRST_SEQ
from e8s.show.commit_writes import record_bytes, slot_bytes
from e8s.show.document_repository import DocumentRepository, require_new_show_id
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest


def _history(repo: DocumentRepository, show_id: str) -> list[Revision]:
    """Returns every revision of a stored show, raising UnknownRefError for a show not stored."""
    revisions = repo.revisions(show_id)
    if revisions:
        return revisions
    close = close_matches(show_id, repo.shows())
    raise UnknownRefError(
        f"show {show_id} has no saved revision",
        hint=f"did you mean {', '.join(close)}?" if close else "save it with put_show()",
        context={"show": show_id},
    )


def _require_importable_id(show_id: str) -> None:
    """Refuses a stored show whose ID a new show may not take, as an import would."""
    try:
        require_new_show_id(show_id)
    except StorageError as error:
        raise StorageError(
            f"show {show_id} cannot be archived: an import would refuse its ID",
            hint=f"{error.hint}; save its head as a new show with put_show and archive that one",
            context={"show": show_id},
            code=error.code,
        ) from error


def _require_blob(show_id: str, digest: str, data: bytes) -> bytes:
    if blob_digest(data) != digest:
        raise StorageError(
            f"blob {digest} of {show_id} does not hash to its digest",
            hint=EXPORT_DAMAGE_HINT,
            context={"show": show_id, "entry": object_entry(digest)},
            code=ErrorCode.CORRUPT,
        )
    return data


def export_archive(repo: DocumentRepository, show_id: str) -> bytes:
    """Writes one show's whole history as an archive: equal histories give equal bytes.

    The history is checked as an import checks it, so a show an import would refuse, such as
    one upgraded from layout 1 whose IDs follow the older scheme, raises instead of exporting.
    The archive is held in memory; the repository's blobs are read one at a time.

    Args:
        repo: Repository holding the show, read through its revisions, trees and blobs.
        show_id: Show to export.

    Returns:
        archive: ZIP bytes holding the show's slots, records and every blob a tree names.

    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> repo = InMemoryRepository()
    >>> at = datetime(2026, 11, 27, tzinfo=UTC)
    >>> _ = repo.put_show("show_1", two_set_show(), expected_revision="", at=at)
    >>> export_archive(repo, "show_1")[:2]
    b'PK'
    """
    revisions = _history(repo, show_id)
    _require_importable_id(show_id)
    check = HistoryCheck(show_id, is_export=True)
    records: dict[str, bytes] = {}
    for seq, revision in enumerate(revisions, start=FIRST_SEQ):
        tree = repo.backend.tree(show_id, revision.id)
        record = record_bytes(revision, seq, tree)
        check.add(revision, tree, size=len(slot_bytes(revision)) + len(record))
        records[record_entry(revision.id)] = record
    writer = ArchiveWriter(entry_cap)
    writer.add(MANIFEST_ENTRY, manifest_bytes(show_id))
    for seq, revision in enumerate(revisions, start=FIRST_SEQ):
        writer.add(slot_entry(seq), slot_bytes(revision))
    for digest in sorted(check.digests):
        writer.add(
            object_entry(digest), _require_blob(show_id, digest, repo.backend.blob(show_id, digest))
        )
    for name in sorted(records):
        writer.add(name, records.pop(name))
    return writer.finish()
