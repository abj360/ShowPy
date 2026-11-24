"""
backend.py --- the storage backend protocol and the helpers every backend shares

Contains:
    SHORT_ID: sets how many characters of a revision ID messages show
    RepositoryBackend: stores revisions, trees and blobs; commits compare-and-swap on the head
    digest(): returns the SHA-256 hex digest blobs and assets are addressed by
    revisions_since(): lists the revisions of a log that landed after a parent revision
    conflict_error(): builds the ConflictError that lists the revisions that landed meanwhile
"""

from typing import Protocol

from e8s.core.errors import (
    ConflictError,
    bounded_repr,
    join_listed,
)
from e8s.show.integrity import (
    blob_digest,
)
from e8s.show.models import Revision

SHORT_ID = 7


def digest(data: bytes) -> str:
    """Returns the SHA-256 hex digest blobs and assets are addressed by."""
    return blob_digest(data)


class RepositoryBackend(Protocol):
    """Stores revisions, path-to-digest trees and blobs; commit is compare-and-swap on head."""

    def show_ids(self) -> list[str]:
        """Returns the IDs of shows with at least one revision."""
        ...

    def log(self, show_id: str) -> list[Revision]:
        """Returns a show's revisions, oldest first."""
        ...

    def tree(self, show_id: str, revision_id: str) -> dict[str, str]:
        """Returns path to blob digest at a revision."""
        ...

    def blob(self, show_id: str, digest: str) -> bytes:
        """Returns the bytes of a blob."""
        ...

    def commit(
        self, show_id: str, revision: Revision, tree: dict[str, str], blobs: dict[str, bytes]
    ) -> None:
        """Stores blobs and the tree, then lands the revision only if its parent is the head."""
        ...


def revisions_since(log: list[Revision], parent: str) -> list[Revision]:
    """Lists the revisions of a log that landed after a parent revision.

    Args:
        log: A show's revisions, oldest first.
        parent: Revision a commit was based on; the root revision, or one missing from the log,
            means every revision landed since.

    Returns:
        landed: The revisions after the parent, oldest first.

    >>> from datetime import UTC, datetime
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> log = [Revision(id=i, parent=p, at=at) for i, p in (("a", ""), ("b", "a"), ("c", "b"))]
    >>> [r.id for r in revisions_since(log, "a")], len(revisions_since(log, ""))
    (['b', 'c'], 3)
    """
    ids = [r.id for r in log]
    return log[ids.index(parent) + 1 :] if parent in ids else log


def conflict_error(show_id: str, expected: str, landed: list[Revision]) -> ConflictError:
    """Builds the ConflictError listing the first landed revisions' short ID, time and paths.

    The message lists five revisions, each with its paths cut to 80 characters, and counts the
    rest, so a long run of landed revisions still gives a short message.
    """
    summary = join_listed(
        [
            f"{r.id[:SHORT_ID]} {r.at.isoformat()} ({bounded_repr(', '.join(r.changed))})"
            for r in landed
        ]
    )
    return ConflictError(
        f"{len(landed)} revision(s) landed on {show_id} since {expected[:SHORT_ID] or 'the start'}:"
        f" {summary}",
        hint="re-read at the head revision, reapply the edit and write again",
        context={
            "expected": expected,
            "head": landed[-1].id if landed else expected,
            "changes": [r.to_json() for r in landed],
        },
    )
