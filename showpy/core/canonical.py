"""
canonical.py --- normalizes values to the JSON data model and encodes them per RFC 8785 (ADR 0014)

Contains:
    JsonValue: the JSON data model every canonical value belongs to
    canonical_value(): normalizes a supported value into the JSON data model
    jcs(): serializes a value as RFC 8785 (JCS) canonical JSON bytes
"""

import math
import re
from collections.abc import Mapping

import numpy as np
from pydantic import BaseModel
from pydantic_core import PydanticSerializationError

from showpy.core.errors import SchemaError, bounded_repr

type JsonValue = bool | int | float | str | list[JsonValue] | dict[str, JsonValue] | None
type JsonScalar = bool | int | float | str | None

TAG_PREFIX = "$showpy:"
MAP_TAG = "$showpy:map"
# One input level can become three output levels (tagged map, pair list, pair) and the encoder
# recurses once per output level, so 100 keeps it under CPython's default recursion limit of 1000.
MAX_NESTING = 100
# CPython refuses to print ints longer than sys.int_max_str_digits, which the environment can
# lower to 640 and no further; this cap gives every environment the same bytes or the same error.
MAX_INT_DIGITS = 640
INT_LIMIT = 10**MAX_INT_DIGITS
CONTEXT_TEXT_CHARS = 80
ES_MAX_PLAIN_POINT = 21
ES_MIN_PLAIN_POINT = -6
SURROGATE = re.compile(r"[\ud800-\udfff]")
STRING_ESCAPE = re.compile(r'[\x00-\x1f"\\]')
SHORT_ESCAPES = {
    '"': '\\"',
    "\\": "\\\\",
    "\b": "\\b",
    "\f": "\\f",
    "\n": "\\n",
    "\r": "\\r",
    "\t": "\\t",
}
SUPPORTED_HINT = (
    "pass None, bool, int, float, str, lists, tuples, sets, mappings, pydantic models or numpy"
    " values"
)


def canonical_value(obj: object) -> JsonValue:
    """Normalizes a supported value into the JSON data model of ADR 0014.

    Models dump in JSON mode, tuples become lists, sets become lists sorted by canonical bytes,
    `-0.0` becomes `0.0`, and a mapping with any non-string key becomes the tagged form
    `{"$showpy:map": [[key, value], ...]}` sorted by the canonical bytes of each pair.

    A set held by a model field is dumped by pydantic in iteration order, before this function
    sees it, so it is not sorted; that order can change with `PYTHONHASHSEED` until stored
    models hold tuples instead (UP-9.3-55). Containers nest at most 100 levels and ints stay
    under 10**640 (ADR 0014, addendum 2026-11-15).

    Args:
        obj: Value to normalize.

    Returns:
        value: The canonical value, built only from plain `dict`, `list`, `str`, `int`, `float`,
            `bool` and `None`.

    >>> canonical_value({2: "b", "a": frozenset({3, 1})})
    {'$showpy:map': [['a', [1, 3]], [2, 'b']]}
    """
    return _normalize(obj, 0)


def jcs(obj: object) -> bytes:
    """Serializes a value as RFC 8785 canonical JSON after normalizing it with canonical_value.

    Args:
        obj: Value to serialize.

    Returns:
        canonical_bytes: UTF-8 JSON with no whitespace, keys sorted by UTF-16 code units and
            numbers in ECMAScript shortest round-trip form.

    >>> jcs({"b": 1.0, "a": [True, None]})
    b'{"a":[true,null],"b":1}'
    """
    return _encoded(canonical_value(obj))


def _normalize(obj: object, depth: int) -> JsonValue:
    """Normalizes one value whose container nesting so far is `depth`."""
    if depth > MAX_NESTING:
        raise SchemaError(
            f"value nests deeper than {MAX_NESTING} levels or holds a cycle",
            hint="flatten the data or break the cycle",
            context={"max_nesting": MAX_NESTING},
        )
    if obj is None or isinstance(obj, bool | str | int | float):
        return _normalize_scalar(obj)
    if isinstance(obj, BaseModel):
        return _normalize(_dumped(obj), depth)
    if isinstance(obj, np.ndarray | np.generic):
        return _normalize(obj.tolist() if isinstance(obj, np.ndarray) else obj.item(), depth)
    if isinstance(obj, Mapping):
        return _normalize_mapping(obj, depth + 1)
    if isinstance(obj, list | tuple):
        return [_normalize(item, depth + 1) for item in obj]
    if isinstance(obj, set | frozenset):
        return sorted((_normalize(item, depth + 1) for item in obj), key=_encoded)
    raise SchemaError(
        f"cannot canonicalize a value of type {type(obj).__name__}",
        hint=SUPPORTED_HINT,
        context={"type": type(obj).__qualname__},
    )


def _dumped(model: BaseModel) -> object:
    """Dumps a model in JSON mode, turning serializer failures and reference cycles into errors."""
    try:
        return model.model_dump(mode="json", fallback=_numpy_content)
    except (PydanticSerializationError, ValueError) as exc:
        raise SchemaError(
            f"cannot dump a {type(model).__name__} model to canonicalize it",
            hint="pass a model whose fields serialize to JSON and hold no reference cycle",
            context={"model": type(model).__name__, "reason": bounded_repr(str(exc))},
        ) from exc


def _numpy_content(value: object) -> object:
    """Dumps a numpy value held by a model field in full; any other unknown type is rejected."""
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    raise SchemaError(
        f"cannot canonicalize a model field of type {type(value).__name__}",
        hint=SUPPORTED_HINT,
        context={"type": type(value).__qualname__},
    )


def _normalize_scalar(obj: JsonScalar) -> JsonScalar:
    """Normalizes a scalar to its plain builtin type, rejecting values JSON cannot hold."""
    if obj is None or isinstance(obj, bool):
        return obj
    if isinstance(obj, str):
        return _checked_text(str.__str__(obj))
    if isinstance(obj, int):
        return _checked_int(int(obj))
    return _checked_float(float(obj))


def _checked_text(text: str) -> str:
    """Returns text unchanged after rejecting lone surrogates, which UTF-8 cannot encode."""
    surrogate = SURROGATE.search(text)
    if surrogate is None:
        return text
    raise SchemaError(
        f"text holds a lone surrogate at index {surrogate.start()}",
        hint="pass valid Unicode text",
        context={"position": surrogate.start(), "text": ascii(text[:CONTEXT_TEXT_CHARS])},
    )


def _checked_int(number: int) -> int:
    """Returns an int unchanged after rejecting one too long to print in every environment."""
    if -INT_LIMIT < number < INT_LIMIT:
        return number
    raise SchemaError(
        f"int has more than {MAX_INT_DIGITS} digits",
        hint="store very large numbers as text",
        context={"bits": number.bit_length(), "max_digits": MAX_INT_DIGITS},
    )


def _checked_float(number: float) -> float:
    """Returns a finite float with `-0.0` folded into `0.0`, rejecting NaN and infinity."""
    if not math.isfinite(number):
        raise SchemaError(
            f"cannot canonicalize the non-finite number {number!r}",
            hint="replace NaN and infinity with finite numbers or None",
            context={"value": repr(number)},
        )
    return 0.0 if number == 0 else number


def _normalize_mapping(mapping: Mapping[object, object], depth: int) -> JsonValue:
    """Normalizes a mapping to a JSON object, or to the tagged form if any key is not a string."""
    items = list(mapping.items())
    for key, _ in items:
        if isinstance(key, str) and key.startswith(TAG_PREFIX):
            raise SchemaError(
                f"key {key[:CONTEXT_TEXT_CHARS]!r} uses the reserved prefix {TAG_PREFIX!r}",
                hint=f"rename keys that start with {TAG_PREFIX!r}; it marks type tags",
                context={"key": ascii(key[:CONTEXT_TEXT_CHARS])},
            )
    text_keyed = [(key, value) for key, value in items if isinstance(key, str)]
    if len(text_keyed) == len(items):
        return {
            _checked_text(str.__str__(key)): _normalize(value, depth) for key, value in text_keyed
        }
    pairs = [(_normalize(key, depth), _normalize(value, depth)) for key, value in items]
    pairs.sort(key=lambda pair: (_encoded(pair[0]), _encoded(pair[1])))
    tagged: list[JsonValue] = [[key, value] for key, value in pairs]
    return {MAP_TAG: tagged}


def _encoded(value: JsonValue) -> bytes:
    """Serializes an already canonical value as RFC 8785 bytes."""
    parts: list[str] = []
    _write(value, parts)
    return "".join(parts).encode("utf-8")


def _write(value: JsonValue, parts: list[str]) -> None:
    """Appends the RFC 8785 text of one canonical value to `parts`."""
    if value is None:
        parts.append("null")
    elif isinstance(value, bool):
        parts.append("true" if value else "false")
    elif isinstance(value, int):
        parts.append(str(value))
    elif isinstance(value, float):
        parts.append(_format_number(value))
    elif isinstance(value, str):
        parts.append(_quote(value))
    elif isinstance(value, list):
        _write_array(value, parts)
    else:
        _write_object(value, parts)


def _write_array(values: list[JsonValue], parts: list[str]) -> None:
    parts.append("[")
    for index, item in enumerate(values):
        if index:
            parts.append(",")
        _write(item, parts)
    parts.append("]")


def _write_object(members: dict[str, JsonValue], parts: list[str]) -> None:
    """Appends a JSON object with its keys sorted by UTF-16 code units, as RFC 8785 requires."""
    parts.append("{")
    for index, key in enumerate(sorted(members, key=lambda name: name.encode("utf-16-be"))):
        if index:
            parts.append(",")
        parts.append(_quote(key))
        parts.append(":")
        _write(members[key], parts)
    parts.append("}")


def _quote(text: str) -> str:
    """Quotes text with the minimal escapes of RFC 8785: quote, backslash and controls only."""
    return '"' + STRING_ESCAPE.sub(_escape, text) + '"'


def _escape(match: re.Match[str]) -> str:
    char = match.group()
    return SHORT_ESCAPES.get(char, f"\\u{ord(char):04x}")


def _format_number(number: float) -> str:
    """Formats a finite float as ECMAScript Number::toString does (ECMA-262 Number::toString)."""
    if number == 0:
        return "0"
    if number < 0:
        return "-" + _format_number(-number)
    digits, point = _shortest_digits(number)
    return _place_point(digits, point)


def _shortest_digits(number: float) -> tuple[str, int]:
    """Splits a positive float into shortest round-trip digits and a decimal point position.

    Returns:
        digits_and_point: Digits `d` with no leading or trailing zeros and the position `n`, so
            that the number equals `0.d * 10**n`.
    """
    mantissa, _, exponent = repr(number).partition("e")
    whole, _, fraction = mantissa.partition(".")
    raw_digits = whole + fraction
    digits = raw_digits.lstrip("0")
    leading_zeros = len(raw_digits) - len(digits)
    return digits.rstrip("0"), len(whole) + int(exponent or "0") - leading_zeros


def _place_point(digits: str, point: int) -> str:
    """Writes `0.digits * 10**point` in plain or exponent form by the ECMAScript rules."""
    if len(digits) <= point <= ES_MAX_PLAIN_POINT:
        return digits + "0" * (point - len(digits))
    if 0 < point <= ES_MAX_PLAIN_POINT:
        return f"{digits[:point]}.{digits[point:]}"
    if ES_MIN_PLAIN_POINT < point <= 0:
        return f"0.{'0' * -point}{digits}"
    exponent = point - 1
    sign = "+" if exponent >= 0 else "-"
    mantissa = digits if len(digits) == 1 else f"{digits[0]}.{digits[1:]}"
    return f"{mantissa}e{sign}{abs(exponent)}"
