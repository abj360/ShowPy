"""
document_paths.py --- names the stored paths of show documents and checks workspace paths

Stored paths address sets by key (`/sets/<key>/…`, `/transitions/<key a>-<key b>/…`) and
show.json lists the set order as keys; workspaces map display IDs onto them.

Contains:
    SHEET_DOC: names the sheet document
    NOTES_DOC: names the notes document stored beside the show's documents, from format v2
    LEGACY_NOTES_DOC: names the v1 document that held the notes among the show's documents
    TIMELINE_DOC: names the timeline document
    SET_ORDER_FIELD: names the show.json field that lists the set keys in show order
    SHEET_FIELD, NOTES_FIELD, TIMELINE_FIELD, FORMS_FIELD, TRANSITIONS_FIELD, REVISION_FIELD:
        name the show fields that live outside show.json; NOTES_FIELD only in v1 dumps
    SPLIT_FIELDS: names the show fields stored in documents of their own, not in show.json
    SEGMENT: matches one stored path segment, such as a set key or a transition folder
    SCRIPT_NAME: names a set folder's build script
    FORMS_NAME: names a set folder's forms document
    PLACEMENTS_NAME: names a set folder's derived placements table
    PATHS_NAME: names a transition folder's paths document
    split_path(): checks a workspace path and returns its segments, rejecting traversal
    set_doc(): returns the path of a set folder's placements document
    script_doc(): returns the path of a set folder's build script
    forms_doc(): returns the path of a set folder's forms document
    placements_doc(): returns the path of a set folder's derived placements table
    paths_doc(): returns the path of a transition folder's paths document
    is_show_doc(): reports whether a path is part of the encoded Show, v1 notes included
    is_storable_folder(): reports whether a set folder name can be stored
    docs_for_order(): lists the show-document paths of a tree with a given set order
"""

import itertools
import re
from collections.abc import Sequence

from showpy.core.error_codes import ErrorCode
from showpy.core.errors import SchemaError, StorageError
from showpy.core.ids import check_id
from showpy.show.manage import SET_ID_PATTERN
from showpy.show.migrate import SHOW_DOC
from showpy.show.migrations.v1_to_v2_notes import LEGACY_NOTES_DOC as LEGACY_NOTES_DOC
from showpy.show.migrations.v1_to_v2_notes import NOTES_DOC as NOTES_DOC

SHEET_DOC = "/show/sheet.json"
TIMELINE_DOC = "/time/timeline.json"
SET_ORDER_FIELD = "sets"
SHEET_FIELD = "sheet"
NOTES_FIELD = "notes"
TIMELINE_FIELD = "timeline"
FORMS_FIELD = "forms"
TRANSITIONS_FIELD = "transitions"
REVISION_FIELD = "revision"
SPLIT_FIELDS = frozenset(
    {
        SHEET_FIELD,
        NOTES_FIELD,
        TIMELINE_FIELD,
        SET_ORDER_FIELD,
        FORMS_FIELD,
        TRANSITIONS_FIELD,
        REVISION_FIELD,
    }
)
SEGMENT = r"[A-Za-z0-9_][A-Za-z0-9_.\-]*"
SEGMENT_PATTERN = re.compile(SEGMENT)
SET_DOC_NAME = "set.json"
SCRIPT_NAME = "set.py"
FORMS_NAME = "forms.json"
PLACEMENTS_NAME = "placements.csv"
PATHS_NAME = "paths.json"
SHOW_DOC_PATTERN = re.compile(
    rf"/(show/(show|sheet|notes)\.json|time/timeline\.json|sets/{SEGMENT}/(set|forms)\.json"
    rf"|transitions/{SEGMENT}/paths\.json)"
)


def split_path(path: str) -> list[str]:
    r"""Checks a workspace path and returns its segments; `..`, `.`, `//` and `\\` are refused.

    >>> split_path("/sets/12A/set.py")
    ['sets', '12A', 'set.py']
    """
    segments = path.split("/")[1:] if path.startswith("/") else []
    if path.endswith("/") and len(segments) > 1:
        segments = segments[:-1]
    if path == "/":
        return []
    if not segments or not all(SEGMENT_PATTERN.fullmatch(s) and ".." not in s for s in segments):
        raise StorageError(
            f"path {path!r} is not a workspace path",
            hint="use an absolute path such as /sets/1/set.py, without '..', '.' or empty parts",
            context={"path": path},
            code=ErrorCode.BAD_PATH,
        )
    return segments


def set_doc(folder: str) -> str:
    """Returns the path of a set's placements document, which storage keeps internal.

    The folder is the set key in stored paths and the display ID in workspace paths.
    """
    return f"/sets/{folder}/{SET_DOC_NAME}"


def script_doc(folder: str) -> str:
    """Returns the path of a set folder's build script."""
    return f"/sets/{folder}/{SCRIPT_NAME}"


def forms_doc(folder: str) -> str:
    """Returns the path of a set folder's forms document."""
    return f"/sets/{folder}/{FORMS_NAME}"


def placements_doc(folder: str) -> str:
    """Returns the path of a set folder's derived placements table."""
    return f"/sets/{folder}/{PLACEMENTS_NAME}"


def paths_doc(tid: tuple[str, str]) -> str:
    """Returns the path of a transition folder's paths document: keys stored, IDs in workspaces.

    >>> paths_doc(("12", "12A"))
    '/transitions/12-12A/paths.json'
    """
    return f"/transitions/{tid[0]}-{tid[1]}/{PATHS_NAME}"


def is_show_doc(path: str) -> bool:
    """Reports whether a path is one of the documents encode_show() writes, or v1 notes.

    The v1 notes document still counts, so the first save deletes it once the notes moved,
    and a commit that writes it into a current tree is refused as a document of no show field.

    >>> is_show_doc("/sets/1/forms.json"), is_show_doc("/sets/1/set.py")
    (True, False)
    """
    return SHOW_DOC_PATTERN.fullmatch(path) is not None


def is_storable_folder(folder: object) -> bool:
    """Reports whether a set folder name can be stored: a v0.2 set ID or a key that passes check_id.

    Stored IDs that predate the ID pattern keep loading until they are migrated.

    >>> is_storable_folder("con"), is_storable_folder("_k"), is_storable_folder("1-2")
    (True, True, False)
    """
    if not isinstance(folder, str):
        return False
    if SET_ID_PATTERN.fullmatch(folder) and ".." not in folder:
        return True
    try:
        return check_id(folder, kind="set key") == folder
    except SchemaError:
        return False


def docs_for_order(order: Sequence[str]) -> frozenset[str]:
    """Lists the show-document paths of a tree whose show.json lists the set keys `order`.

    >>> sorted(docs_for_order(["1", "2"]))[:3]
    ['/sets/1/forms.json', '/sets/1/set.json', '/sets/2/forms.json']
    """
    paths = {SHOW_DOC, SHEET_DOC, TIMELINE_DOC}
    paths.update(set_doc(s) for s in order)
    paths.update(forms_doc(s) for s in order)
    paths.update(paths_doc(tid) for tid in itertools.pairwise(order))
    return frozenset(paths)
