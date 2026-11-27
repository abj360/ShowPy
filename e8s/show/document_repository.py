"""
document_repository.py --- implements the Repository protocol over a storage backend

Contains:
    SHOW_ID_PATTERN: matches the show IDs a repository can store
    REVISION_HASH_DOMAIN: names the hash domain every stored revision ID is computed under
    revision_id(): computes a revision's ID from its parent, time and changed digests
    DocumentRepository: implements Repository over a backend of revisions, trees and blobs
    encode_cache_of(): returns the encode cache a repository shares across its saves
"""

import functools
import re
from collections.abc import Mapping
from datetime import datetime

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import (
    SchemaError,
    StorageError,
    UnknownRefError,
)
from e8s.core.ids import WINDOWS_DEVICE_NAMES, check_id, is_device_name
from e8s.core.util import close_matches, content_hash
from e8s.show.backend import (
    RepositoryBackend,
    conflict_error,
    digest,
    unknown_revision_error,
)
from e8s.show.decode_cache import DecodeCache
from e8s.show.documents import (
    decode_show,
    split_path,
)
from e8s.show.encode_cache import EncodeCache
from e8s.show.files import show_changes
from e8s.show.history_models import Revision
from e8s.show.integrity import (
    LazyBlobs,
    require_known_path,
    require_valid_digests,
)
from e8s.show.models import Show
from e8s.show.storage import ROOT_REVISION

SHOW_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")
# Frozen at the old library name: every stored revision ID is hashed under it.
REVISION_HASH_DOMAIN = "showpy.revision"


def revision_id(parent: str, at: datetime, changes: Mapping[str, str | None]) -> str:
    """Computes a revision's ID from its parent, time and the blob digest of each changed path.

    Args:
        parent: ID of the previous revision; empty for the first.
        at: When the revision was saved.
        changes: Each changed path with its new blob digest, or None when the path was deleted.

    Returns:
        revision_id: The 64-digit hex ID every stored revision is named by.

    >>> from datetime import UTC
    >>> len(revision_id("", datetime(2026, 11, 24, tzinfo=UTC), {"/a.txt": "0" * 64}))
    64
    """
    body = {"parent": parent, "at": at.isoformat(), "changes": dict(changes)}
    return content_hash(body, domain=REVISION_HASH_DOMAIN)


def _require_show_id(show_id: str) -> None:
    if not SHOW_ID_PATTERN.fullmatch(show_id) or ".." in show_id:
        raise StorageError(
            f"show id {show_id!r} cannot be stored",
            hint="use letters, digits, _, . and - only, starting with a letter or digit",
            context={"show": show_id},
            code=ErrorCode.BAD_PATH,
        )


def _require_not_device_name(show_id: str) -> None:
    """Refuses a Windows device name for a new show before its folder is looked up.

    On Windows that lookup opens the device itself. Shows already stored under such a name still
    load and take commits on their head.
    """
    if is_device_name(show_id):
        raise StorageError(
            f"show id {show_id!r} is a Windows device name and cannot start a new show",
            hint=f"pick another ID: {', '.join(sorted(WINDOWS_DEVICE_NAMES))}, alone or before a"
            " dot, name devices on Windows",
            context={"show": show_id},
            code=ErrorCode.BAD_PATH,
        )


def _id_rule_refusal(show_id: str) -> StorageError | None:
    """Returns the refusal for a show ID that fails the ID rule or is not NFC, else None."""
    try:
        normalized = check_id(show_id, kind="show")
    except SchemaError as error:
        refusal = StorageError(
            f"show id {show_id!r} cannot start a new show",
            hint=error.hint,
            context={"show": show_id},
            code=ErrorCode.BAD_PATH,
        )
        refusal.__cause__ = error
        return refusal
    if normalized == show_id:
        return None
    return StorageError(
        f"show id {show_id!r} is not in NFC form and cannot start a new show",
        hint=f"use the NFC form {normalized!r}",
        context={"show": show_id},
        code=ErrorCode.BAD_PATH,
    )


class DocumentRepository:
    """Implements Repository over a backend that stores revisions, trees and blobs.

    Every write checks validity: known paths, decodable structured files and a show that
    decodes. No write checks pins or locks. Edit functions enforce those for every caller, the
    designer included; a host that writes raw documents owns them.

    Attributes:
        backend: Where revisions, trees and blobs live; read-only.
    """

    def __init__(self, backend: RepositoryBackend):
        self._backend = backend
        self._decode_cache = DecodeCache()
        self._encode_cache = EncodeCache()
        self._heads: dict[str, Revision] = {}

    @property
    def backend(self) -> RepositoryBackend:
        """Returns the backend that stores revisions, trees and blobs; it is fixed at creation."""
        return self._backend

    def shows(self) -> list[str]:
        """Returns the IDs of stored shows, sorted."""
        return sorted(self._backend.shows())

    def revisions(self, show_id: str) -> list[Revision]:
        """Returns every revision of a show, oldest first; the cost grows with the history."""
        _require_show_id(show_id)
        return self._backend.log(show_id)

    def head(self, show_id: str) -> str:
        """Returns the head revision ID, or ROOT_REVISION before the first save."""
        _require_show_id(show_id)
        return self._backend.head(show_id)

    def get_revision(self, show_id: str, revision_id: str) -> Revision:
        """Returns one revision, raising UnknownRefError with close matches.

        The cost grows with the history; reads and writes at the head never call this.
        """
        log = self.revisions(show_id)
        for revision in log:
            if revision.id == revision_id:
                return revision
        raise unknown_revision_error(show_id, revision_id, [r.id for r in log])

    def _tree(self, show_id: str, revision: str | None) -> Mapping[str, str]:
        rev = self.head(show_id) if revision is None else revision
        if rev == ROOT_REVISION:
            return {}
        return self._backend.tree(show_id, rev)

    def paths(self, show_id: str, revision: str | None = None) -> list[str]:
        """Returns the stored document paths at a revision (default head), sorted."""
        return sorted(self._tree(show_id, revision))

    def read(self, show_id: str, path: str, revision: str | None = None) -> bytes | None:
        """Returns a document at a revision (default head), or None when absent."""
        digest = self._tree(show_id, revision).get(path)
        return None if digest is None else self._backend.blob(show_id, digest)

    def documents(self, show_id: str, revision: str | None = None) -> Mapping[str, bytes]:
        """Returns the stored documents at a revision (default head), reading blobs on lookup.

        The revision is resolved once; each blob is read the first time a path holding it is
        looked up, so listing paths or decoding the show never loads renders or exports.

        >>> from datetime import UTC, datetime
        >>> from e8s.show.memory_backend import InMemoryRepository
        >>> repo = InMemoryRepository()
        >>> at = datetime(2026, 11, 11, tzinfo=UTC)
        >>> _ = repo.transaction("s", {"/renders/a.txt": b"hi"}, expected_revision="", at=at)
        >>> repo.documents("s")["/renders/a.txt"]
        b'hi'
        """
        load = functools.partial(self._backend.blob, show_id)
        return LazyBlobs(self._tree(show_id, revision), functools.cache(load), uncached=load)

    def _require_new_show_id(self, show_id: str) -> None:
        """Refuses a write from the root under an ID a new show may not take.

        The device check touches no path. The ID rule refuses only a show that is not stored:
        a show stored under an older ID, written from the root, gets the ConflictError of any
        stale write. A head lookup that fails for such an ID, such as a name too long for the
        file system, counts as not stored.
        """
        _require_not_device_name(show_id)
        refusal = _id_rule_refusal(show_id)
        if refusal is None:
            return
        try:
            is_stored = self.head(show_id) != ROOT_REVISION
        except StorageError as error:
            raise refusal from error
        if not is_stored:
            raise refusal

    def _require_head(self, show_id: str, expected: str) -> str:
        head = self.head(show_id)
        if head != expected:
            raise conflict_error(show_id, expected, self._backend.log(show_id, since=expected))
        return head

    def _head_revision(self, show_id: str, head: str) -> Revision:
        """Returns the head revision from the last one this repository landed or read."""
        remembered = self._heads.get(show_id)
        if remembered is None or remembered.id != head:
            remembered = self.get_revision(show_id, head)
            self._heads[show_id] = remembered
        return remembered

    def _require_valid_tree(
        self,
        show_id: str,
        old: Mapping[str, str],
        new: Mapping[str, str],
        added: Mapping[str, bytes],
    ) -> None:
        def blob(blob_digest: str) -> bytes:
            return (
                added[blob_digest]
                if blob_digest in added
                else self._backend.blob(show_id, blob_digest)
            )

        require_valid_digests(old, new, blob, self._decode_cache)

    def transaction(
        self,
        show_id: str,
        changes: Mapping[str, bytes | None],
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Writes or deletes documents atomically as one revision with a deterministic ID.

        Args:
            show_id: Show written.
            changes: Path to new bytes, or None to delete.
            expected_revision: Head the caller read at; ConflictError when the head moved.
            at: Time stamp.

        Returns:
            revision: The new head, or the unchanged head when nothing differs.

        >>> from datetime import UTC, datetime
        >>> from e8s.show.memory_backend import InMemoryRepository
        >>> repo = InMemoryRepository()
        >>> at = datetime(2026, 11, 11, tzinfo=UTC)
        >>> rev = repo.transaction("s", {"/renders/a.txt": b"hi"}, expected_revision="", at=at)
        >>> repo.transaction("s", {"/renders/a.txt": b"hi"}, expected_revision=rev.id, at=at) == rev
        True
        """
        for path, data in changes.items():
            split_path(path)
            require_known_path(path, is_write=data is not None)
        if expected_revision == ROOT_REVISION:
            self._require_new_show_id(show_id)
        head = self._require_head(show_id, expected_revision)
        old = self._tree(show_id, expected_revision)
        blobs = {digest(d): d for d in changes.values() if d is not None}
        new = {**old, **{p: digest(d) for p, d in changes.items() if d is not None}}
        for path in (p for p, d in changes.items() if d is None):
            new.pop(path, None)
        changed = tuple(sorted(p for p in set(old) | set(new) if old.get(p) != new.get(p)))
        if not changed:
            if head == ROOT_REVISION:
                raise StorageError(
                    f"nothing to save for {show_id}",
                    hint="write at least one document",
                    context={"show": show_id},
                )
            return self._head_revision(show_id, head)
        revision = Revision(
            id=revision_id(expected_revision, at, {p: new.get(p) for p in changed}),
            parent=expected_revision,
            at=at,
            changed=changed,
        )
        self._require_valid_tree(show_id, old, new, blobs)
        self._backend.commit(show_id, revision, new, blobs)
        self._heads[show_id] = revision
        return revision

    def get_show(self, show_id: str, revision: str | None = None) -> Show:
        """Returns the whole show at a revision (default head)."""
        rev = self.head(show_id) if revision is None else revision
        docs = self.documents(show_id, rev)
        if not docs:
            close = close_matches(show_id, self.shows())
            raise UnknownRefError(
                f"show {show_id} has no saved revision",
                hint=f"did you mean {', '.join(close)}?" if close else "save it with put_show()",
                context={"show": show_id},
            )
        return decode_show(docs, rev)

    def put_show(
        self,
        show_id: str,
        show: Show,
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Writes the whole show, deleting documents of sets and paths it no longer has.

        Checks validity only, never pins or locks; edit functions enforce those.
        """
        return self.transaction(
            show_id,
            show_changes(
                show, self.documents(show_id, expected_revision), cache=self._encode_cache
            ),
            expected_revision=expected_revision,
            at=at,
        )


def encode_cache_of(repo: object) -> EncodeCache | None:
    """Returns the encode cache every save through a DocumentRepository shares, else None.

    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> encode_cache_of(InMemoryRepository()) is not None, encode_cache_of(object())
    (True, None)
    """
    return repo._encode_cache if isinstance(repo, DocumentRepository) else None
