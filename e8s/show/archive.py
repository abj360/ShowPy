"""
archive.py --- carries one show's whole history between repositories as a portable archive

An archive is a ZIP of stored entries holding the show's slots, revision records and reachable
blobs exactly as a layout-2 repository stores them, read through the repository protocol, so a
memory repository exports too. Names are sorted and times zeroed, so equal histories give equal
bytes. Revision IDs, parents and times travel unchanged.

An import checks the whole archive before it writes anything, then commits each revision in order
through the backend, which keeps every ID. A backend has no commit of several revisions at once,
so an import that stops part way leaves the revisions that landed: a complete, valid, shorter
history, which the error names and which importing the same archive again finishes.

Contains:
    FIXED_ENTRIES: counts the entries every archive holds besides its revisions' and blobs'
    ENTRIES_PER_REVISION: counts the entries each revision adds: its slot and its record
    export_archive(): writes one show's whole history as an archive
    import_archive(): verifies a whole archive, then commits the history the show lacks
"""

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import ConflictError, E8sError, StorageError, UnknownRefError
from e8s.core.util import close_matches
from e8s.show import archive_zip
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
from e8s.show.archive_reading import VerifiedArchive, archive_record, verified_archive
from e8s.show.archive_zip import ArchiveWriter
from e8s.show.backend import SHORT_ID
from e8s.show.backend_checks import FIRST_SEQ
from e8s.show.commit_writes import record_bytes, slot_bytes
from e8s.show.document_repository import DocumentRepository, require_new_show_id
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest
from e8s.show.storage import ROOT_REVISION

# The manifest; every revision adds its slot and its record.
FIXED_ENTRIES = 1
ENTRIES_PER_REVISION = 2


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


def _require_entry_room(show_id: str, revisions: list[Revision]) -> None:
    """Refuses, before reading any tree or blob, a history whose slots and records pass the cap.

    Every archive holds its manifest and at least one blob besides a slot and a record per
    revision, so that many revisions fit at most; more blobs fit fewer.
    """
    cap = archive_zip.MAX_ARCHIVE_ENTRIES
    if FIXED_ENTRIES + ENTRIES_PER_REVISION * len(revisions) <= cap:
        return
    fits = (cap - FIXED_ENTRIES - 1) // ENTRIES_PER_REVISION
    raise StorageError(
        f"show {show_id} has {len(revisions)} revisions; an archive holds at most {fits} "
        f"revisions, fewer as their blobs add entries, within its {cap} entries",
        hint="save the head as a new show with put_show and archive that one",
        context={"show": show_id, "revisions": len(revisions), "fits": fits, "cap": cap},
        code=ErrorCode.TOO_LARGE,
    )


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
    _require_entry_room(show_id, revisions)
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


def _exists(show_id: str) -> ConflictError:
    return ConflictError(
        f"show {show_id} already exists in this repository with another history",
        hint="an archive restores a show under the ID it was exported with: import it into a "
        "repository that does not hold that show; a show left by a stopped import of this same "
        "archive is finished by importing it again",
        context={"show": show_id},
    )


def _stopped(archive: VerifiedArchive, seq: int, error: E8sError) -> StorageError:
    """Builds the error of an import whose commit of slot `seq` failed after earlier ones landed."""
    landed, total = seq - 1, len(archive.revisions)
    last = archive.revisions[landed - 1].id
    kind = ConflictError if isinstance(error, ConflictError) else StorageError
    return kind(
        f"import of show {archive.show_id} stopped after {landed} of {total} revisions: "
        f"{error.message}",
        hint=f"the show holds the archive's first {landed} revisions, each complete, ending at "
        f"{last[:SHORT_ID]}; import the same archive again to finish",
        context={"show": archive.show_id, "landed": landed, "total": total, "revision": last},
        code=error.code,
    )


def _landed_before(repo: DocumentRepository, archive: VerifiedArchive) -> int:
    """Returns how many of the archive's revisions the show holds: its head's slot, or 0.

    Revision IDs hash their parent and changes, and trees chain, so a head equal to the ID of
    slot `k` means the show holds exactly the archive's first `k` revisions.
    """
    head = repo.head(archive.show_id)
    if head == ROOT_REVISION:
        return 0
    for seq, revision in enumerate(archive.revisions, start=FIRST_SEQ):
        if revision.id == head:
            return seq
    raise _exists(archive.show_id)


def _commit_history(repo: DocumentRepository, archive: VerifiedArchive, landed: int) -> None:
    """Commits the revisions after slot `landed` in order, each blob with the first tree naming it.

    A resumed run passes the blobs an earlier run stored again, which stores nothing new.
    """
    stored: set[str] = set()
    for seq, revision in enumerate(archive.revisions[landed:], start=landed + FIRST_SEQ):
        tree = archive_record(seq, revision, archive.records[revision.id]).tree
        blobs = {d: bytes(archive.objects[d]) for d in sorted(set(tree.values()) - stored)}
        try:
            repo.backend.commit(archive.show_id, revision, tree, blobs)
        except ConflictError as error:
            if seq == FIRST_SEQ:
                raise _exists(archive.show_id) from error
            raise _stopped(archive, seq, error) from error
        except E8sError as error:
            if seq == FIRST_SEQ:
                raise
            raise _stopped(archive, seq, error) from error
        stored.update(blobs)


def import_archive(repo: DocumentRepository, data: bytes | bytearray | memoryview) -> str:
    """Verifies a whole archive, then commits its history into a repository.

    Every entry is checked before anything is written: the ZIP form, sizes and CRCs, the
    manifest, each slot, record and blob as a repository read and fsck check them, and that no
    entry is left over. A refusal raises StorageError naming the entry and writes nothing; an
    archive of a newer format version raises VersionError. Revisions keep their IDs, parents
    and times.

    The checks prove an archive consistent, not authentic: anyone can build a history whose IDs,
    trees and blobs agree, and no digest covers the show ID the manifest names. Import an
    archive only from a source the caller trusts with the show.

    The import is resumable and idempotent. A show the repository does not hold gets the whole
    history. A show whose head is the archive's revision in slot `k`, as an import that stopped
    there leaves, gets slots `k + 1` on; one whose head is the last revision is left as it is.
    That is safe because each revision ID hashes its parent and changes and every tree chains
    from the one before, so an equal head means an equal history. Any other show under the ID
    raises ConflictError, also when another writer creates it while the import runs.

    Args:
        repo: Repository to import into.
        data: Archive bytes, untrusted; copied first, so a caller's buffer may change after.

    Returns:
        show_id: The show the archive held, now stored whole in `repo`.

    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> source, target = InMemoryRepository(), InMemoryRepository()
    >>> at = datetime(2026, 11, 27, tzinfo=UTC)
    >>> _ = source.put_show("show_1", two_set_show(), expected_revision="", at=at)
    >>> import_archive(target, export_archive(source, "show_1"))
    'show_1'
    >>> target.revisions("show_1") == source.revisions("show_1")
    True
    """
    archive = verified_archive(bytes(data))
    _commit_history(repo, archive, _landed_before(repo, archive))
    return archive.show_id
