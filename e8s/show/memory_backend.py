"""
memory_backend.py --- the in-memory repository and its backend

Contains:
    InMemoryRepository: keeps every revision in process memory
"""

import threading
from collections.abc import Mapping
from typing import override

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import StorageError
from e8s.show.backend import (
    REVISION_ID_PATTERN,
    RepositoryBackend,
    conflict_error,
    unknown_revision_error,
)
from e8s.show.document_repository import DocumentRepository
from e8s.show.history_models import Revision
from e8s.show.integrity import blob_digest
from e8s.show.storage import ROOT_REVISION


def _bad_id(show_id: str, role: str, value: str) -> StorageError:
    return StorageError(
        f"{role} {value!r} is not a 64-digit hex id",
        hint="revision IDs and blob digests are 64 lowercase hex digits made by "
        "DocumentRepository.transaction",
        context={"show": show_id, role: value},
        code=ErrorCode.BAD_ID,
    )


def _corrupt(show_id: str, reason: str) -> StorageError:
    return StorageError(
        f"repository of {show_id} is corrupt: {reason}",
        hint="rebuild the repository from its source; memory repositories are not persisted",
        context={"show": show_id},
        code=ErrorCode.CORRUPT,
    )


class _MemoryBackend(RepositoryBackend):
    def __init__(self) -> None:
        self._revisions: dict[str, dict[str, Revision]] = {}
        self._heads: dict[str, str] = {}
        self._trees: dict[tuple[str, str], dict[str, str]] = {}
        self._blobs: dict[str, bytes] = {}
        self._lock = threading.Lock()

    @override
    def shows(self) -> list[str]:
        with self._lock:
            return list(self._heads)

    @override
    def head(self, show_id: str) -> str:
        with self._lock:
            return self._heads.get(show_id, ROOT_REVISION)

    @override
    def log(self, show_id: str, *, since: str = ROOT_REVISION) -> list[Revision]:
        with self._lock:
            return self._after(show_id, since)

    def _after(self, show_id: str, since: str) -> list[Revision]:
        """Walks parent links back from the head, so the cost follows the revisions returned."""
        index = self._revisions.get(show_id, {})
        if since != ROOT_REVISION and since not in index:
            raise unknown_revision_error(show_id, since, list(index))
        landed: list[Revision] = []
        current = self._heads.get(show_id, ROOT_REVISION)
        while current != since:
            if current not in index or len(landed) >= len(index):
                raise _corrupt(show_id, "revision parent links do not reach the root")
            revision = index[current]
            landed.append(revision)
            current = revision.parent
        landed.reverse()
        return landed

    @override
    def tree(self, show_id: str, revision_id: str) -> Mapping[str, str]:
        with self._lock:
            stored = self._trees.get((show_id, revision_id))
            if stored is None:
                raise unknown_revision_error(
                    show_id, revision_id, list(self._revisions.get(show_id, {}))
                )
            return dict(stored)

    @override
    def blob(self, show_id: str, digest: str) -> bytes:
        with self._lock:
            data = self._blobs.get(digest)
            if data is None:
                raise _corrupt(show_id, f"blob {digest[:7]} is not stored")
            return data

    @override
    def commit(
        self,
        show_id: str,
        revision: Revision,
        tree: Mapping[str, str],
        blobs: Mapping[str, bytes],
    ) -> None:
        with self._lock:
            self._require_valid(show_id, revision, tree, blobs)
            if self._heads.get(show_id, ROOT_REVISION) != revision.parent:
                raise conflict_error(show_id, revision.parent, self._landed(show_id, revision))
            self._blobs.update(blobs)
            self._trees[(show_id, revision.id)] = dict(tree)
            self._revisions.setdefault(show_id, {})[revision.id] = revision
            self._heads[show_id] = revision.id

    def _require_valid(
        self,
        show_id: str,
        revision: Revision,
        tree: Mapping[str, str],
        blobs: Mapping[str, bytes],
    ) -> None:
        """Refuses a commit that could corrupt the store, before anything is written."""
        if not REVISION_ID_PATTERN.fullmatch(revision.id):
            raise _bad_id(show_id, "revision id", revision.id)
        if revision.parent and not REVISION_ID_PATTERN.fullmatch(revision.parent):
            raise _bad_id(show_id, "parent id", revision.parent)
        if revision.id == revision.parent or revision.id in self._revisions.get(show_id, {}):
            raise _bad_id(show_id, "revision id", revision.id)
        for digest, data in blobs.items():
            if not REVISION_ID_PATTERN.fullmatch(digest):
                raise _bad_id(show_id, "blob digest", digest)
            if blob_digest(data) != digest:
                raise _corrupt(show_id, f"blob bytes do not match digest {digest[:7]}")
        missing = sorted({d for d in tree.values() if d not in blobs and d not in self._blobs})
        if missing:
            raise _corrupt(show_id, f"tree names blobs that are not stored: {missing[:3]}")

    def _landed(self, show_id: str, revision: Revision) -> list[Revision]:
        """Lists what landed after the commit's parent, or every revision for an unknown parent."""
        known = self._revisions.get(show_id, {})
        if revision.parent == ROOT_REVISION or revision.parent in known:
            return self._after(show_id, revision.parent)
        return self._after(show_id, ROOT_REVISION)


class InMemoryRepository(DocumentRepository):
    """Keeps every revision in process memory; for tests and single-process sessions."""

    def __init__(self) -> None:
        super().__init__(_MemoryBackend())
