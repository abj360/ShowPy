"""
blame.py --- names the revision that last changed one show item

An item key maps to the stored documents that can hold it. A revision is a candidate when its
changed paths include one of them; only candidates are decoded, so the cost follows the item's
own history, not the show's length. Layout-1 documents (a set's placements inside set.json, the
cast inside show.json) count as holders, so revisions saved before the role split are covered.

Contains:
    blame(): returns the newest revision whose save changed one item's value
"""

import fnmatch
from collections.abc import Callable

from e8s.show.change_models import ItemKey
from e8s.show.document_paths import (
    CAST_DOC,
    DRILL_NAME,
    FACING_NAME,
    FORMS_NAME,
    LABELS_NAME,
    PATHS_NAME,
    SHEET_DOC,
    TIMELINE_DOC,
    is_storable_folder,
    role_docs,
    set_doc,
)
from e8s.show.history_models import Revision
from e8s.show.items import changes, items
from e8s.show.migrate import SHOW_DOC
from e8s.show.storage import ROOT_REVISION
from e8s.show.workspace import Workspace

ANY_FORMS_DOC = f"/sets/*/{FORMS_NAME}"
TRANSITION_ROOT = "/transitions"
ROLE_DOCUMENT = {"drill": DRILL_NAME, "pin": DRILL_NAME, "facing": FACING_NAME}
CAST_DOCUMENTS = frozenset({SHOW_DOC, CAST_DOC})
SHOW_ONLY = frozenset({SHOW_DOC})


def _set_documents(key: ItemKey) -> frozenset[str] | None:
    (folder, *_) = key.path
    if not is_storable_folder(folder):
        return None
    if key.kind == "placement":
        name = ROLE_DOCUMENT.get(key.role or "", LABELS_NAME)
        return frozenset({set_doc(folder), f"/sets/{folder}/{name}"})
    return frozenset({set_doc(folder), *role_docs(folder), f"/sets/{folder}/{FORMS_NAME}"})


def _transition_documents(key: ItemKey) -> frozenset[str]:
    return frozenset({f"{TRANSITION_ROOT}/{key.path[0]}/{PATHS_NAME}"})


def _holder_test(key: ItemKey) -> Callable[[str], bool]:
    """Returns a test for the stored paths that can hold the item."""
    fixed: frozenset[str] | None
    match key.kind:
        case "placement" | "set":
            fixed = _set_documents(key)
        case "path_row" | "route" | "transition":
            fixed = _transition_documents(key)
        case "form":
            return lambda path: fnmatch.fnmatchcase(path, ANY_FORMS_DOC)
        case "spot_order" | "spot" | "group" | "selection" | "show":
            fixed = CAST_DOCUMENTS
        case "sheet":
            fixed = frozenset({SHEET_DOC})
        case "timeline":
            fixed = frozenset({TIMELINE_DOC})
        case _:
            fixed = SHOW_ONLY
    held = fixed or frozenset()
    return held.__contains__


def _changes_item(ws: Workspace, revision: Revision, key: ItemKey) -> tuple[bool, bool]:
    """Reports whether a revision changed the item and whether the item exists after it."""
    show = ws.show_at(revision.id)
    if show is None:
        return False, False
    if revision.parent == ROOT_REVISION or (before := ws.show_at(revision.parent)) is None:
        is_present = key in items(show)
        return is_present, is_present
    for change in changes(before, show).changes:
        if change.key == key:
            return True, change.after is not None
    return False, True


def blame(ws: Workspace, key: ItemKey) -> Revision | None:
    """Returns the newest revision whose save changed an item's value, or None.

    Walks the log newest first and decodes only revisions that wrote a document able to hold the
    item. A revision that re-encodes documents without changing the item's value, such as the
    first layout-2 save over layout-1 documents, is skipped. An item that exists at the head
    was last changed by the returned revision; the first save is blamed for an item no later
    save touched. Returns None when the item does not exist at the head, when no revision
    changed it, or when the show has no revisions.

    Args:
        ws: The workspace whose repository and show are read; its own revision is ignored.
        key: The item to look up.

    Returns:
        revision: The revision that last changed the item.

    >>> from datetime import UTC, datetime
    >>> from e8s.examples import two_set_show
    >>> from e8s.show.change_models import ItemKey
    >>> from e8s.show.memory_backend import InMemoryRepository
    >>> from e8s.show.show_io import save_show
    >>> from e8s.show.workspace import open_workspace
    >>> ws = open_workspace(InMemoryRepository(), "show_1")
    >>> first = save_show(ws, two_set_show(), datetime(2026, 11, 25, tzinfo=UTC))
    >>> blame(ws, ItemKey(kind="spot", path=("T1",))) == first
    True
    """
    holds = _holder_test(key)
    for revision in reversed(ws.repo.revisions(ws.show_id)):
        if not any(holds(path) for path in revision.changed):
            continue
        is_changed, is_present = _changes_item(ws, revision, key)
        if is_changed:
            return revision if is_present else None
    return None
