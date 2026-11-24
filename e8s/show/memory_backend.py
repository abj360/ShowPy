"""
memory_backend.py --- the in-memory repository and its backend

Contains:
    InMemoryRepository: keeps every revision in process memory
"""

import threading
from collections.abc import Mapping
from typing import override

from e8s.show.backend import RepositoryBackend, conflict_error, unknown_revision_error
from e8s.show.document_repository import DocumentRepository
from e8s.show.history_models import Revision
from e8s.show.storage import ROOT_REVISION


class _MemoryBackend(RepositoryBackend):
    def __init__(self) -> None:
        self._revisions: dict[tuple[str, str], Revision] = {}
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
        if since != ROOT_REVISION and (show_id, since) not in self._revisions:
            known = [r.id for (owner, _), r in self._revisions.items() if owner == show_id]
            raise unknown_revision_error(show_id, since, known)
        landed: list[Revision] = []
        current = self._heads.get(show_id, ROOT_REVISION)
        while current != since:
            revision = self._revisions[(show_id, current)]
            landed.append(revision)
            current = revision.parent
        landed.reverse()
        return landed

    @override
    def tree(self, show_id: str, revision_id: str) -> Mapping[str, str]:
        with self._lock:
            return dict(self._trees[(show_id, revision_id)])

    @override
    def blob(self, show_id: str, digest: str) -> bytes:
        with self._lock:
            return self._blobs[digest]

    @override
    def commit(
        self,
        show_id: str,
        revision: Revision,
        tree: Mapping[str, str],
        blobs: Mapping[str, bytes],
    ) -> None:
        with self._lock:
            if self._heads.get(show_id, ROOT_REVISION) != revision.parent:
                raise conflict_error(show_id, revision.parent, self._landed(show_id, revision))
            self._blobs.update(blobs)
            self._trees[(show_id, revision.id)] = dict(tree)
            self._revisions[(show_id, revision.id)] = revision
            self._heads[show_id] = revision.id

    def _landed(self, show_id: str, revision: Revision) -> list[Revision]:
        """Lists what landed after the commit's parent, or every revision for an unknown parent."""
        if revision.parent == ROOT_REVISION or (show_id, revision.parent) in self._revisions:
            return self._after(show_id, revision.parent)
        return self._after(show_id, ROOT_REVISION)


class InMemoryRepository(DocumentRepository):
    """Keeps every revision in process memory; for tests and single-process sessions."""

    def __init__(self) -> None:
        super().__init__(_MemoryBackend())
