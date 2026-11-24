"""
document_dump.py --- writes show documents as canonical JSON bytes, show.json's header first

Equal content always gives equal bytes: keys sorted, sets sorted, two-space indent, `-0.0`
written as `0.0`, and show.json and other headed documents open with `format` and `version`.

Contains:
    dump_json(): writes JSON canonically: sorted keys, sorted sets, two-space indent
    dump_dumped_json(): writes the JSON-mode dump of a model canonically, skipping a second walk
    dump_show_doc(): writes a show.json object canonically, the format header first
    dump_headed_json(): writes a document canonically with its `format` and `version` keys first
"""

import json
import re
from collections.abc import Mapping
from typing import Any

from e8s.core.canonical import MAX_INT_DIGITS, MAX_NESTING, canonical_value
from e8s.core.json_types import JsonValue
from e8s.show.migrate import (
    FORMAT_KEY,
    SHOW_DOC,
    VERSION_KEY,
    require_current_version,
    without_header,
)

JSON_INDENT = 2
NEGATIVE_ZERO = re.compile(r"-0\.0\b")
RESERVED_KEY_PREFIX = "$showpy:"
TOO_DEEP = "\n" + " " * (JSON_INDENT * (MAX_NESTING + 1))
HEADER_CLOSE = b"\n}\n"
OPEN_OBJECT = b"{\n"


# Any: accepts any JSON-like value or model dump.
def dump_json(value: Any) -> bytes:
    r"""Writes JSON canonically so equal content always gives equal bytes and hashes.

    >>> dump_json({"b": frozenset({"y", "x"}), "a": 1})
    b'{\n  "a": 1,\n  "b": [\n    "x",\n    "y"\n  ]\n}\n'
    """
    text = json.dumps(
        canonical_value(value), indent=JSON_INDENT, sort_keys=True, ensure_ascii=False
    )
    return f"{text}\n".encode()


# Any: a model_dump(mode="json") result, whose JSON types and string keys are guaranteed.
def dump_dumped_json(dumped: Any) -> bytes:
    """Writes the JSON-mode dump of a model with the same bytes as dump_json, much faster.

    The dump already holds only JSON types and string keys, so one `json.dumps` pass is enough
    unless the text shows a value canonical_value must treat: `-0.0`, a reserved key prefix, a
    line long enough to hold a huge int or indented deeper than canonical_value allows. NaN,
    infinity and lone surrogates make the pass fail, which hands the value to dump_json so it
    raises the same SchemaError.

    >>> dump_dumped_json({"b": [1.5, None], "a": -0.0}) == dump_json({"b": [1.5, None], "a": -0.0})
    True
    """
    try:
        text = json.dumps(
            dumped, indent=JSON_INDENT, sort_keys=True, ensure_ascii=False, allow_nan=False
        )
        encoded = f"{text}\n".encode()
    except (ValueError, UnicodeEncodeError):
        return dump_json(dumped)
    if _needs_normalizing(text):
        return dump_json(dumped)
    return encoded


def _needs_normalizing(text: str) -> bool:
    """Reports whether JSON text may hold a value that only canonical_value treats correctly.

    With one value per line and two spaces per level, an int past the digit cap of canonical_value
    makes a line longer than the cap, and a value nested past its depth cap makes a line indented
    past TOO_DEEP, so line length and indent stand in for a slow scan of every digit run.
    """
    if RESERVED_KEY_PREFIX in text:
        return True
    if "-0.0" in text and NEGATIVE_ZERO.search(text):
        return True
    if TOO_DEEP in text:
        return True
    return max(map(len, text.split("\n"))) > MAX_INT_DIGITS


def dump_show_doc(document: Mapping[str, JsonValue]) -> bytes:
    """Writes a show.json object canonically, with the format header as its first two keys.

    Use it to commit a hand-edited show.json: dump_json() would sort the header among the
    other keys, which reads the same but is not the canonical encoding.

    Args:
        document: Parsed show.json object in the current format version, header included.

    Returns:
        raw: Its canonical bytes. A document of another format version raises SchemaError
            code OLD_FORMAT, or VersionError when it is newer.

    >>> from e8s.examples import two_set_show
    >>> from e8s.show.documents import encode_show
    >>> raw = encode_show(two_set_show())[SHOW_DOC]
    >>> dump_show_doc(json.loads(raw)) == raw
    True
    """
    require_current_version(document, name=SHOW_DOC)
    return dump_headed_json(document)


def dump_headed_json(document: Mapping[str, JsonValue]) -> bytes:
    """Writes a document canonically with its `format` and `version` keys first, then the rest.

    Args:
        document: Parsed JSON object; keys other than the header are written sorted.

    Returns:
        raw: Its canonical bytes, header first.

    >>> dump_headed_json({"show": "show-1", "version": 1, "format": "e8.notes"}).splitlines()[1]
    b'  "format": "e8.notes",'
    """
    header = {key: document[key] for key in (FORMAT_KEY, VERSION_KEY) if key in document}
    head = dump_dumped_json(header)
    body = without_header(document)
    if not body:
        return head
    rest = dump_dumped_json(body).removeprefix(OPEN_OBJECT)
    return head.removesuffix(HEADER_CLOSE) + b",\n" + rest
