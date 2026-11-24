"""
memory_backend.py --- the in-memory repository and its backend

Contains:
    InMemoryRepository: keeps every revision in process memory
"""

import threading
from typing import override

from e8s.show.backend import RepositoryBackend, conflict_error, revisions_since
from e8s.show.document_repository import DocumentRepository
from e8s.show.history_models import Revision
from e8s.show.storage import ROOT_REVISION


class _MemoryBackend(RepositoryBackend):
    def __init__(self) -> None:
        self._logs: dict[str, list[Revision]] = {}
        self._trees: dict[tuple[str, str], dict[str, str]] = {}
        self._blobs: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def show_ids(self) -> list[str]:
        with self._lock:
            return list(self._logs)

    def log(self, show_id: str) -> list[Revision]:
        with self._lock:
            return list(self._logs.get(show_id, []))

    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        with self._lock:
            return dict(self._trees[(show_id, revision_id)])

    @override
    def blob(self, show_id: str, digest: str) -> bytes:
        with self._lock:
            return self._blobs[digest]

    def commit(
        self, show_id: str, revision: Revision, tree: dict[str, str], blobs: dict[str, bytes]
    ) -> None:
        with self._lock:
            log = self._logs.setdefault(show_id, [])
            if (log[-1].id if log else ROOT_REVISION) != revision.parent:
                raise conflict_error(
                    show_id, revision.parent, revisions_since(log, revision.parent)
                )
            self._blobs.update(blobs)
            self._trees[(show_id, revision.id)] = dict(tree)
            log.append(revision)


class InMemoryRepository(DocumentRepository):
    """Keeps every revision in process memory; for tests and single-process sessions."""

    def __init__(self) -> None:
        super().__init__(_MemoryBackend())
