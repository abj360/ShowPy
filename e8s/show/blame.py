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
from collections.abc import Callable, Mapping

from e8s.core.errors import E8sError, SchemaError, StorageError
from e8s.core.readonly import ReadOnlyMap
from e8s.show.change_models import ItemKey, ItemKind
from e8s.show.document_paths import (
    CAST_DOC,
    DRILL_NAME,
    FACING_NAME,
    FORMS_NAME,
    LABELS_NAME,
    PATHS_NAME,
    SEGMENT_PATTERN,
    SHEET_DOC,
    TIMELINE_DOC,
    is_storable_folder,
    role_docs,
    set_doc,
)
from e8s.show.history_models import Revision
from e8s.show.items import changes, items
from e8s.show.migrate import SHOW_DOC
from e8s.show.models import Show
from e8s.show.storage import ROOT_REVISION
from e8s.show.workspace import Workspace

SHORT_ID = 7
ANY_FORMS_DOC = f"/sets/*/{FORMS_NAME}"
TRANSITION_ROOT = "/transitions"
ROLE_DOCUMENT = {"drill": DRILL_NAME, "pin": DRILL_NAME, "facing": FACING_NAME}
CAST_DOCUMENTS = frozenset({SHOW_DOC, CAST_DOC})
SHOW_ONLY = frozenset({SHOW_DOC})


PATH_LENGTHS: Mapping[ItemKind, int] = ReadOnlyMap(
    {
        "placement": 2,
        "set": 1,
        "form": 1,
        "path_row": 2,
        "route": 2,
        "transition": 1,
        "spot": 1,
        "group": 1,
        "selection": 1,
        "pin": 2,
        "set_order": 0,
        "spot_order": 0,
        "pin_order": 0,
        "facing_override": 0,
        "sheet": 0,
        "timeline": 0,
        "show": 0,
    }
)
PATH_SHAPES: Mapping[ItemKind, str] = ReadOnlyMap(
    {
        "placement": "(set key, spot ID)",
        "set": "(set key,)",
        "form": "(form ID,)",
        "path_row": "(transition key, spot ID)",
        "route": "(transition key, route ID)",
        "transition": "(transition key,)",
        "spot": "(spot ID,)",
        "group": "(group key,)",
        "selection": "(selection name,)",
        "pin": "(pin target, pin ID)",
    }
)


def _require_path_shape(key: ItemKey) -> None:
    expected = PATH_LENGTHS[key.kind]
    if len(key.path) == expected:
        return
    shape = PATH_SHAPES.get(key.kind, "()")
    raise SchemaError(
        f"a {key.kind} item path has {expected} parts, not {len(key.path)}",
        hint=f"use the path {shape}, as items() keys it",
        context={"kind": key.kind, "path": list(key.path)},
    )


def _set_documents(key: ItemKey) -> frozenset[str] | None:
    folder = key.path[0]
    if not is_storable_folder(folder):
        return None
    if key.kind == "placement":
        name = ROLE_DOCUMENT.get(key.role or "", LABELS_NAME)
        return frozenset({set_doc(folder), f"/sets/{folder}/{name}"})
    return frozenset({set_doc(folder), *role_docs(folder), f"/sets/{folder}/{FORMS_NAME}"})


def _transition_documents(key: ItemKey) -> frozenset[str] | None:
    folder = key.path[0]
    if not SEGMENT_PATTERN.fullmatch(folder):
        return None
    return frozenset({f"{TRANSITION_ROOT}/{folder}/{PATHS_NAME}"})


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


def _show_at(ws: Workspace, revision_id: str) -> Show:
    """Decodes a revision, failing closed: an unreadable revision is never skipped."""
    try:
        show = ws.show_at(revision_id)
    except E8sError as error:
        raise _unreadable(ws, revision_id, str(error)) from error
    if show is None:
        raise _unreadable(ws, revision_id, "it holds no show document")
    return show


def _unreadable(ws: Workspace, revision_id: str, reason: str) -> StorageError:
    return StorageError(
        f"cannot read revision {revision_id[:SHORT_ID]} of show {ws.show_id}: {reason}",
        hint="repair or revert the revision; blame never guesses past one it cannot read",
        context={"revision": revision_id, "show": ws.show_id},
    )


def _is_changed(ws: Workspace, revision: Revision, key: ItemKey) -> bool:
    """Reports whether a revision changed the item's value against its parent."""
    show = _show_at(ws, revision.id)
    if revision.parent == ROOT_REVISION:
        return key in items(show)
    before = _show_at(ws, revision.parent)
    return any(change.key == key for change in changes(before, show).changes)


def blame(ws: Workspace, key: ItemKey) -> Revision | None:
    """Returns the newest revision whose save changed an item's value, or None.

    Walks the log newest first and decodes only revisions that wrote a document able to hold the
    item. A revision that re-encodes documents without changing the item's value, such as the
    first layout-2 save over layout-1 documents, is skipped. The first save is blamed for an
    item no later save touched. Returns None when the item does not exist at the head, which
    is checked first and decodes only the head, or when the show has no revisions. A key whose
    path has the wrong length for its kind raises SchemaError; a candidate revision that cannot
    be decoded raises StorageError naming it, never a guessed answer.

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
    _require_path_shape(key)
    log = ws.repo.revisions(ws.show_id)
    if not log or key not in items(_show_at(ws, log[-1].id)):
        return None
    holds = _holder_test(key)
    for revision in reversed(log):
        if any(holds(path) for path in revision.changed) and _is_changed(ws, revision, key):
            return revision
    return None
