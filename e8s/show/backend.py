"""
backend.py --- the storage backend protocol and the helpers every backend shares

Contains:
    SHORT_ID: sets how many characters of a revision ID messages show
    RepositoryBackend: stores revisions, trees and blobs; commits compare-and-swap on the head
    digest(): returns the SHA-256 hex digest blobs and assets are addressed by
    revisions_after(): lists the revisions of a log after one revision, failing on an unknown one
    unknown_revision_error(): builds the UnknownRefError for a revision a show does not hold
    conflict_error(): builds the ConflictError that lists the revisions that landed meanwhile
"""

from collections.abc import Mapping
from typing import Protocol

from e8s.core.errors import (
    ConflictError,
    UnknownRefError,
    bounded_repr,
    join_listed,
)
from e8s.core.util import close_matches
from e8s.show.history_models import Revision
from e8s.show.integrity import (
    blob_digest,
)
from e8s.show.storage import ROOT_REVISION

SHORT_ID = 7


def digest(data: bytes) -> str:
    """Returns the SHA-256 hex digest blobs and assets are addressed by."""
    return blob_digest(data)


class RepositoryBackend(Protocol):
    """Stores revisions, path-to-digest trees and blobs; commit is compare-and-swap on head.

    A backend never trusts IDs or paths it reads back from storage, lands a commit only when its
    parent is the head, and leaves nothing visible when a commit fails.
    """

    def shows(self) -> list[str]:
        """Returns the IDs of shows with at least one revision."""
        ...

    def head(self, show_id: str) -> str:
        """Returns the newest revision ID, or the root revision for an unknown or empty show."""
        ...

    def log(self, show_id: str, *, since: str = ROOT_REVISION) -> list[Revision]:
        """Returns the revisions after `since`, oldest first, excluding `since` itself.

        The root revision returns the whole log. A revision missing from the log raises
        UnknownRefError.
        """
        ...

    def tree(self, show_id: str, revision_id: str) -> Mapping[str, str]:
        """Returns path to blob digest at a revision; an unknown revision raises UnknownRefError."""
        ...

    def blob(self, show_id: str, digest: str) -> bytes:
        """Returns the bytes of a blob."""
        ...

    def commit(
        self,
        show_id: str,
        revision: Revision,
        tree: Mapping[str, str],
        blobs: Mapping[str, bytes],
    ) -> None:
        """Stores blobs and the tree, then lands the revision only if its parent is the head."""
        ...


def revisions_after(show_id: str, log: list[Revision], since: str) -> list[Revision]:
    """Lists the revisions of a log that come after one revision, failing on an unknown one.

    Args:
        show_id: Show the log belongs to, named in the error.
        log: A show's revisions, oldest first.
        since: Revision to start after; the root revision means the whole log.

    Returns:
        landed: The revisions after `since`, oldest first, without `since` itself.

    >>> from datetime import UTC, datetime
    >>> at = datetime(2026, 11, 11, tzinfo=UTC)
    >>> log = [Revision(id=i, parent=p, at=at) for i, p in (("a", ""), ("b", "a"), ("c", "b"))]
    >>> [r.id for r in revisions_after("s", log, "a")], len(revisions_after("s", log, ""))
    (['b', 'c'], 3)
    """
    if since == ROOT_REVISION:
        return list(log)
    for position, revision in enumerate(log):
        if revision.id == since:
            return log[position + 1 :]
    raise unknown_revision_error(show_id, since, [r.id for r in log])


def unknown_revision_error(show_id: str, revision_id: str, known: list[str]) -> UnknownRefError:
    """Builds the UnknownRefError for a revision a show does not hold, with close matches.

    >>> unknown_revision_error("s", "abc", ["abd"]).context["revision"]
    'abc'
    """
    close = [c[:SHORT_ID] for c in close_matches(revision_id, known)]
    guess = f"did you mean {', '.join(close)}? " if close else ""
    return UnknownRefError(
        f"no revision {revision_id[:SHORT_ID] or '(root)'} in show {show_id}",
        hint=f"{guess}read the head with head() or list revisions with log()",
        context={"show": show_id, "revision": revision_id},
    )


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
