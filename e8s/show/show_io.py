"""
show_io.py --- reads and writes a whole show through a workspace

Contains:
    load_show(): reads the whole show at the workspace revision
    save_show(): writes the whole show and its text forms' fonts, refusing when another write
        landed meanwhile
"""

from datetime import datetime

from e8s.core.errors import StorageError
from e8s.geo.fonts import FontRegistry
from e8s.show.document_repository import encode_cache_of
from e8s.show.files import (
    show_changes,
)
from e8s.show.font_documents import font_changes
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
    show = ws.show_at(ws.revision)
    if show is None:
        raise StorageError(
            f"show {ws.show_id} has no revisions yet",
            hint="save a show first with save_show()",
            context={"show": ws.show_id},
        )
    return show


def save_show(
    ws: Workspace, show: Show, at: datetime, *, font_registry: FontRegistry | None = None
) -> Revision:
    """Writes the whole show against `show.revision`, the revision it was loaded at.

    Raises ConflictError listing every revision (short ID, time, paths) saved since, so the
    caller sees what changed meanwhile; reload, reapply and save again. Stored documents that
    are not canonical are re-encoded in a revision of their own first; if the edit
    then conflicts, that revision stays, it changes no content, and the retry builds on it.
    Through a DocumentRepository, saves share its encode cache, so a document is encoded again
    only when one of its source objects is new.

    Each font of a text form that is not bundled is stored as `/assets/fonts/<sha256>.json` in
    the same revision as the form, and a stored font no text form uses is deleted; history keeps
    it. Fonts come from `font_registry`, or, when it is None, from the bundled fonts and the
    fonts already stored at `show.revision`. A font found nowhere raises MissingFontError.

    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.examples import two_set_show
    >>> from datetime import datetime, UTC
    >>> from e8s.show.workspace import open_workspace
    >>> ws = open_workspace(InMemoryRepository(), "show-1")
    >>> save_show(ws, two_set_show(), datetime(2026, 11, 11, tzinfo=UTC)).parent
    ''
    """
    tree = ws.tree_at(show.revision)
    changes = show_changes(show, tree, cache=encode_cache_of(ws.repo))
    changes |= font_changes(show, tree, font_registry)
    return ws._commit_normalizing(changes, at=at, expected_revision=show.revision, is_complete=True)
