"""
strict_json.py --- parses stored and imported JSON strictly, so two readers never disagree on it

Standard JSON has no NaN or infinity, and a document with a repeated key or a lone surrogate
reads differently in different parsers; a byte-order mark hides the UTF-8 encoding. All four
are refused, each with SchemaError code STRICT_JSON naming the document. A number too large for
a float, such as 1e400, parses to infinity and is refused when the model validates, not here, so
parsing stays on the C fast path without a Python call per float.

Contains:
    json_nesting(): measures how deep arrays and objects nest in JSON bytes, without recursing
    require_nesting(): refuses JSON bytes nested deeper than MAX_NESTING levels
    parse_strict_json(): parses UTF-8 JSON bytes, refusing non-standard tokens, repeated keys,
        a byte-order mark and lone surrogates
    strict_json_copy(): copies an already parsed JSON value through the strict parser
"""

import json
import re
from collections import Counter

import numpy as np

from e8s.core.canonical import MAX_INT_DIGITS, MAX_NESTING
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError
from e8s.core.json_types import JsonValue

UTF8_BOM = b"\xef\xbb\xbf"
SURROGATE_ESCAPE = re.compile(rb"\\u[dD][89a-fA-F]")
SURROGATE_HINT = "write text as valid Unicode; a \\ud800-\\udfff escape must be part of a pair"
STRUCTURE_BYTES = b'[]{}"'
NON_STRUCTURE_BYTES = bytes(sorted(set(range(256)) - set(STRUCTURE_BYTES)))
QUOTE = ord('"')
# Bit 1 is set in `[` (91) and `{` (123) and clear in `]` (93) and `}` (125), so a bracket's
# step is (byte & 2) - 1.
OPENER_BIT = 2
BACKSLASH = b"\\"
ESCAPED_BACKSLASH = b"\\\\"
ESCAPED_QUOTE = b'\\"'
STRICT_HINT = "write standard UTF-8 JSON, as save_show() and export_json() do"


def _strict_error(document: str, problem: str, **context: str | int) -> SchemaError:
    return SchemaError(
        f"{document} is not strict JSON: {problem}",
        hint=STRICT_HINT,
        context={"path": document, "problem": problem, **context},
        code=ErrorCode.STRICT_JSON,
    )


def json_nesting(raw: bytes) -> int:
    """Measures how deep arrays and objects nest in JSON bytes, counting brackets outside strings.

    Escaped backslashes and quotes are dropped first, so every quote left opens or closes a
    string; brackets between a quote pair are skipped. The scan runs in C and numpy without
    recursion, so no interpreter or stack limit decides the result. For text that is not JSON
    the count is only an estimate; the parser refuses such text anyway.

    Args:
        raw: JSON bytes.

    Returns:
        depth: The most arrays and objects open at once; 0 for a bare scalar.

    >>> json_nesting(b'{"a": [1, "]]"], "b": {}}')
    2
    """
    return _depth(_structure(raw))


def _structure(raw: bytes) -> bytes:
    """Keeps only the brackets and the quotes that delimit strings, dropping escaped ones."""
    if BACKSLASH in raw:
        raw = raw.replace(ESCAPED_BACKSLASH, b"").replace(ESCAPED_QUOTE, b"")
    return raw.translate(None, NON_STRUCTURE_BYTES)


def _depth(structure: bytes) -> int:
    """Returns the deepest bracket nesting of a structure, skipping brackets inside strings."""
    marks = np.frombuffer(structure, dtype=np.uint8)
    quotes = marks == QUOTE
    brackets = marks[~(np.bitwise_xor.accumulate(quotes) | quotes)]
    if brackets.size == 0:
        return 0
    steps = (brackets & OPENER_BIT).view(np.int8) - 1
    return max(int(steps.cumsum(dtype=np.int32).max()), 0)


def require_nesting(raw: bytes, *, document: str) -> None:
    """Refuses JSON bytes whose arrays and objects nest deeper than MAX_NESTING levels.

    The check is explicit so the cap holds whatever recursion the interpreter's JSON parser
    allows; the canonical writer refuses the same depth.

    Args:
        raw: JSON bytes.
        document: Path or name of the document, used in errors.

    >>> require_nesting(b"[[1]]", document="d.json")
    >>> try:
    ...     require_nesting(b"[" * 101 + b"]" * 101, document="d.json")
    ... except SchemaError as exc:
    ...     print(exc.context["max_nesting"])
    100
    """
    structure = _structure(raw)
    if len(structure) - structure.count(b'"') <= MAX_NESTING:
        return
    depth = _depth(structure)
    if depth > MAX_NESTING:
        raise SchemaError(
            f"{document} nests deeper than {MAX_NESTING} levels",
            hint=f"write JSON nested at most {MAX_NESTING} levels",
            context={"path": document, "depth": depth, "max_nesting": MAX_NESTING},
        )


def parse_strict_json(raw: bytes, *, document: str) -> JsonValue:
    """Parses UTF-8 JSON bytes, refusing what standard JSON forbids or leaves ambiguous.

    Args:
        raw: Document bytes.
        document: Path or name of the document, used in errors.

    Returns:
        value: The parsed value. Invalid JSON, nesting past MAX_NESTING or an int past
            MAX_INT_DIGITS digits raises SchemaError; a NaN or infinity token, a repeated key,
            a leading byte-order mark or a lone surrogate raises SchemaError code STRICT_JSON.

    >>> parse_strict_json(b'{"a": [1, 2.5]}', document="/show/show.json")
    {'a': [1, 2.5]}
    >>> try:
    ...     parse_strict_json(b'{"a": NaN}', document="/show/show.json")
    ... except SchemaError as exc:
    ...     print(exc.code, exc.context["token"])
    STRICT_JSON NaN
    """
    if raw.startswith(UTF8_BOM):
        raise _strict_error(document, "starts with a byte-order mark", position=0)
    require_nesting(raw, document=document)

    def refuse_constant(token: str) -> float:
        raise _strict_error(document, f"non-standard token {token}", token=token)

    def unique_keys(pairs: list[tuple[str, JsonValue]]) -> dict[str, JsonValue]:
        found = dict(pairs)
        if len(found) != len(pairs):
            repeated = _first_repeated([key for key, _ in pairs])
            raise _strict_error(document, f"repeated key {repeated!r}", key=repeated)
        return found

    try:
        value: JsonValue = json.loads(
            raw.decode("utf-8"), parse_constant=refuse_constant, object_pairs_hook=unique_keys
        )
    except (ValueError, RecursionError) as exc:
        raise SchemaError(
            f"{document} is not valid JSON: {exc}",
            hint=f"write UTF-8 JSON nested at most {MAX_NESTING} levels, ints of at most"
            f" {MAX_INT_DIGITS} digits, as save_show() does",
            context={"path": document},
        ) from exc
    if SURROGATE_ESCAPE.search(raw):
        _require_no_lone_surrogate(value, document)
    return value


def _first_repeated(keys: list[str]) -> str:
    """Returns the first key, in reading order, of a list known to hold a repeated key."""
    return next(key for key, count in Counter(keys).items() if count > 1)


def _require_no_lone_surrogate(value: JsonValue, document: str) -> None:
    """Re-encodes a parsed value as UTF-8, which fails exactly when a lone surrogate remains."""
    try:
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SchemaError(
            f"{document} is not strict JSON: a lone surrogate",
            hint=SURROGATE_HINT,
            context={"path": document, "problem": "a lone surrogate"},
            code=ErrorCode.STRICT_JSON,
        ) from exc


def strict_json_copy(value: object, *, document: str) -> JsonValue:
    """Copies a parsed JSON value through the strict parser, so it obeys the same rules as bytes.

    Args:
        value: A value such as a dict a caller parsed itself.
        document: Name of the document, used in errors.

    Returns:
        copy: A fresh JSON value equal to `value`. A value that is not JSON, holds NaN or
            infinity, a lone surrogate or two keys that turn into one string raises SchemaError.

    >>> strict_json_copy({"a": (1, 2)}, document="import")
    {'a': [1, 2]}
    """
    try:
        text = json.dumps(value, allow_nan=False, ensure_ascii=False)
        raw = text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise SchemaError(
            f"{document} is not strict JSON: a lone surrogate",
            hint=SURROGATE_HINT,
            context={"path": document, "problem": "a lone surrogate"},
            code=ErrorCode.STRICT_JSON,
        ) from exc
    except (TypeError, ValueError, RecursionError) as exc:
        raise SchemaError(
            f"{document} is not strict JSON: {exc}",
            hint=STRICT_HINT,
            context={"path": document, "problem": type(exc).__name__},
            code=ErrorCode.STRICT_JSON,
        ) from exc
    return parse_strict_json(raw, document=document)
