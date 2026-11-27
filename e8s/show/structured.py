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
    decode_notes(): decodes a notes document into its model
    decode_selection(): decodes a selection file into its selection
    require_structured(): checks a text file decodes the way its reader decodes it
"""

from collections.abc import Callable

from pydantic import TypeAdapter

from e8s.core.errors import SchemaError
from e8s.core.selection import Selection
from e8s.core.strict_json import parse_strict_json
from e8s.core.validation import validate_adapter
from e8s.show._notes_document import NotesDocument
from e8s.show.collab_models import Comment
from e8s.show.migrations.v1_to_v2_notes import NOTES_DOC

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
    """Decodes a comments file, raising SchemaError for bad UTF-8, loose JSON or comment shapes.

    Args:
        raw: Comments file bytes.

    Returns:
        comments: The stored comments, in file order.

    >>> decode_comments(b"[]")
    []
    """
    parsed = parse_strict_json(raw, document=COMMENTS_DOC)
    return validate_adapter(COMMENTS_ADAPTER, parsed, name="Comments")


def decode_notes(raw: bytes) -> NotesDocument:
    """Decodes a notes document, raising SchemaError for bad UTF-8, loose JSON or a wrong shape.

    Args:
        raw: Notes document bytes.

    Returns:
        notes: The stored notes document.

    >>> decode_notes(b'{"show": "show-1"}').show
    'show-1'
    """
    parsed = parse_strict_json(raw, document=NOTES_DOC)
    return validate_adapter(NOTES_ADAPTER, parsed, name="NotesDocument")


def decode_selection(raw: str | bytes) -> Selection:
    """Decodes a selection file, raising SchemaError for loose JSON or a wrong shape.

    Args:
        raw: Selection file bytes, or the text a workspace read returns for them.

    Returns:
        selection: The stored canvas selection.

    >>> decode_selection('{"ids": ["T1"]}').ids
    ('T1',)
    """
    data = raw.encode() if isinstance(raw, str) else raw
    parsed = parse_strict_json(data, document=SELECTION_DOC)
    return validate_adapter(SELECTION_ADAPTER, parsed, name="Selection")


def _check_json(raw: bytes) -> None:
    parse_strict_json(raw, document="JSON")


EXACT_CHECKS: dict[str, Callable[[bytes], object]] = {
    SELECTION_DOC: decode_selection,
    COMMENTS_DOC: decode_comments,
    NOTES_DOC: decode_notes,
}


def require_structured(path: str, raw: bytes) -> None:
    r"""Checks a text file decodes as its reader decodes it; other files pass unread.

    Text files must be UTF-8; `.json` files must parse as strict JSON; the selection, comments and
    notes files must also match their models. The error keeps the code of the failed check.

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
            code=exc.code,
        ) from exc
