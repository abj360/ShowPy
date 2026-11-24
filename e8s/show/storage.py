"""
storage.py --- the repository and asset store protocols

Contains:
    ROOT_REVISION: names the revision before the first save
    Repository: versions a show's documents; every write checks the expected revision
    AssetStore: stores binary assets under content-hash URIs
"""

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol

from e8s.show.history_models import Revision
from e8s.show.models import Show

ROOT_REVISION = ""


class Repository(Protocol):
    """Versions each show's documents in a linear history of revisions.

    Paths are stored paths, which address sets by key (`/sets/<key>/set.json`).
    Every write takes the revision the caller read at and raises ConflictError, listing what
    landed since, when the head moved. Writes check validity, never pins or locks: edit
    functions enforce those for every caller; a host that writes raw documents
    owns them.
    """

    def shows(self) -> list[str]:
        """Returns the IDs of stored shows."""
        ...

    def head(self, show_id: str) -> str:
        """Returns the head revision ID, or ROOT_REVISION before the first save."""
        ...

    def revisions(self, show_id: str) -> list[Revision]:
        """Returns every revision, oldest first."""
        ...

    def get_revision(self, show_id: str, revision_id: str) -> Revision:
        """Returns one revision."""
        ...

    def paths(self, show_id: str, revision: str | None = None) -> list[str]:
        """Returns the stored document paths at a revision (default head), sorted."""
        ...

    def read(self, show_id: str, path: str, revision: str | None = None) -> bytes | None:
        """Returns a document at a revision (default head), or None when absent."""
        ...

    def documents(self, show_id: str, revision: str | None = None) -> Mapping[str, bytes]:
        """Returns the stored documents at a revision (default head), reading bytes on lookup."""
        ...

    def transaction(
        self,
        show_id: str,
        changes: Mapping[str, bytes | None],
        *,
        expected_revision: str,
        at: datetime,
    ) -> Revision:
        """Writes (bytes) or deletes (None) documents atomically as one revision.

        An edit that changes nothing returns the head revision unchanged (idempotent upsert).
        """
        ...

    def get_show(self, show_id: str, revision: str | None = None) -> Show:
        """Returns the whole show at a revision (default head)."""
        ...

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
        ...


class AssetStore(Protocol):
    """Stores binary assets such as captured video and audio under content-hash URIs."""

    def put(self, data: bytes) -> str:
        """Stores bytes and returns their URI; storing equal bytes returns the same URI."""
        ...

    def get(self, uri: str) -> bytes:
        """Returns the bytes stored under a URI."""
        ...
