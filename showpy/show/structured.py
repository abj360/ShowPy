"""
structured.py --- one table of which stored files decode as text, JSON or a model

The file reader, the file writer and the commit funnel all decode through this table, so a
path that reads back as text is a path a commit may store.

Contains:
    SELECTION_DOC: names the designer's current canvas selection
    COMMENTS_DOC: names the canvas comment list
    TEXT_SUFFIXES: lists the file suffixes read as UTF-8 text
    decode_text(): decodes a text file's bytes, raising SchemaError for invalid UTF-8
    decode_comments(): decodes a comments file into its comments
    require_structured(): checks a text file decodes the way its reader decodes it
"""

from collections.abc import Callable

from pydantic import TypeAdapter

from showpy.core.errors import SchemaError
from showpy.core.selection import Selection
from showpy.core.strict_json import parse_strict_json
from showpy.core.validation import validate_adapter_json
from showpy.notes.models import NotesDocument
from showpy.show.migrations.v1_to_v2_notes import NOTES_DOC
from showpy.show.models import Comment

SELECTION_DOC = "/canvas/selection.json"
COMMENTS_DOC = "/canvas/comments.json"
TEXT_SUFFIXES = (".json", ".csv", ".py", ".yaml", ".yml", ".txt", ".md", ".svg")
JSON_SUFFIX = ".json"
BYTE_ORDER_MARK = "\ufeff"
COMMENTS_ADAPTER = TypeAdapter(list[Comment])
SELECTION_ADAPTER = TypeAdapter(Selection)
NOTES_ADAPTER = TypeAdapter(NotesDocument)


def decode_text(path: str, raw: bytes) -> str:
    """Decodes a text file, raising SchemaError that names the path for invalid UTF-8.

    Args:
        path: Workspace or stored path, echoed in the error.
        raw: File bytes.

    Returns:
        text: The decoded text.

    >>> decode_text("/config/limits.yaml", b"min: 1")
    'min: 1'
    """
    try:
        return raw.decode()
    except UnicodeDecodeError as exc:
        raise SchemaError(
            f"{path} is not valid UTF-8: {exc}",
            hint="write UTF-8 text to text files",
            context={"path": path},
        ) from exc


def decode_comments(raw: bytes) -> list[Comment]:
    """Decodes a comments file, raising SchemaError for bad UTF-8, JSON or comment shapes.

    Args:
        raw: Comments file bytes.

    Returns:
        comments: The stored comments, in file order.

    >>> decode_comments(b"[]")
    []
    """
    return validate_adapter_json(COMMENTS_ADAPTER, raw, name="Comments")


def _check_selection(raw: bytes) -> None:
    validate_adapter_json(SELECTION_ADAPTER, raw, name="Selection")


def _check_notes(raw: bytes) -> None:
    validate_adapter_json(NOTES_ADAPTER, raw, name="NotesDocument")


def _check_json(raw: bytes) -> None:
    parse_strict_json(raw, document="JSON")


EXACT_CHECKS: dict[str, Callable[[bytes], object]] = {
    SELECTION_DOC: _check_selection,
    COMMENTS_DOC: decode_comments,
    NOTES_DOC: _check_notes,
}


def require_structured(path: str, raw: bytes) -> None:
    r"""Checks a text file decodes as its reader decodes it; other files pass unread.

    Text files must be UTF-8; `.json` files must parse; the selection, comments and notes files
    must also match their models.

    Args:
        path: Workspace or stored path.
        raw: File bytes.

    >>> require_structured("/renders/a.png", b"\xff")
    >>> try:
    ...     require_structured("/canvas/comments.json", b"{}")
    ... except SchemaError as exc:
    ...     print(exc.context["path"])
    /canvas/comments.json
    """
    if not path.endswith(TEXT_SUFFIXES):
        return
    try:
        text = decode_text(path, raw)
        if path.endswith(JSON_SUFFIX) and text.startswith(BYTE_ORDER_MARK):
            raise SchemaError(
                "JSON starts with a byte order mark",
                hint="write JSON without a BOM",
                context={},
            )
        check = EXACT_CHECKS.get(path)
        if check is not None:
            check(raw)
        elif path.endswith(JSON_SUFFIX):
            _check_json(raw)
    except SchemaError as exc:
        raise SchemaError(
            f"{path} does not hold valid content: {exc.message}",
            hint="write UTF-8 text; JSON files must parse and match their model",
            context={**exc.context, "path": path},
        ) from exc
