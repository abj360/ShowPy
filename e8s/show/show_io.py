"""
show_io.py --- reads and writes a whole show through a workspace

Contains:
    load_show(): reads the whole show at the workspace revision
    load_show_at(): reads the whole show at a given revision without moving the workspace
    save_show(): writes the whole show, refusing when another write landed meanwhile
"""

from datetime import datetime

from e8s.core.errors import StorageError
from e8s.show.document_repository import encode_cache_of
from e8s.show.files import (
    show_changes,
)
from e8s.show.history_models import Revision
from e8s.show.models import Show
from e8s.show.workspace import Workspace


def load_show(ws: Workspace) -> Show:
    """Reads the whole show at the workspace revision; `show.revision` records it.

    A revision stored in an older format is migrated in memory and the show comes back in the
    current format, without the migration report; the next save normalizes the stored documents.
    migrate_documents() returns that report, with every renamed ID and dropped stored field, and
    the repository history keeps the older revision's bytes.

    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.workspace import open_workspace
    >>> ws = open_workspace(InMemoryRepository(), "show-1")
    >>> _ = save_show(ws, two_set_show(), datetime(2026, 11, 11, tzinfo=UTC))
    >>> load_show(ws).id
    'show-1'
    """
    return load_show_at(ws, ws.revision)


def load_show_at(ws: Workspace, revision: str) -> Show:
    """Reads the whole show at a revision; the workspace stays where it is.

    Raises StorageError when that revision holds no show yet.

    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.workspace import open_workspace
    >>> ws = open_workspace(InMemoryRepository(), "show_1")
    >>> saved = save_show(ws, two_set_show(), datetime(2026, 11, 11, tzinfo=UTC))
    >>> load_show_at(ws, saved.id).revision == saved.id
    True
    """
    show = ws.show_at(revision)
    if show is None:
        raise StorageError(
            f"show {ws.show_id} has no revisions yet",
            hint="save a show first with save_show()",
            context={"show": ws.show_id},
        )
    return show


def save_show(ws: Workspace, show: Show, at: datetime) -> Revision:
    """Writes the whole show against `show.revision`, the revision it was loaded at.

    Raises ConflictError listing every revision (short ID, time, paths) saved since, so the
    caller sees what changed meanwhile; reload, reapply and save again. Stored documents that
    are not canonical are re-encoded in a revision of their own first; if the edit
    then conflicts, that revision stays, it changes no content, and the retry builds on it.
    Through a DocumentRepository, saves share its encode cache, so a document is encoded again
    only when one of its source objects is new.

    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.examples import two_set_show
    >>> from datetime import datetime, UTC
    >>> from e8s.show.workspace import open_workspace
    >>> ws = open_workspace(InMemoryRepository(), "show-1")
    >>> save_show(ws, two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)).parent
    ''
    """
    changes = show_changes(show, ws.tree_at(show.revision), cache=encode_cache_of(ws.repo))
    return ws._commit_normalizing(changes, at=at, expected_revision=show.revision, is_complete=True)
