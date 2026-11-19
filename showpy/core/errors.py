"""
errors.py --- the ShowPyError family: every library error carries code, message, context, hint

Contains:
    ShowPyError: reports a library failure with a code and a hint that says what is allowed
    ShowPyError.to_dict(): renders the error as a JSON object
    bounded_context(): bounds a context mapping to JSON values of limited size and depth
    more_count(): renders the `(+n more)` count of items left out of a list
    join_listed(): joins the first five parts of a message list and counts the rest
    UnknownRefError: reports an ID, name or unit that does not resolve
    VersionError: reports a pinned version that is missing or incompatible
    SchemaError: reports data that fails its schema or text format
    schema_error_from(): converts a pydantic ValidationError into a SchemaError
    field_errors(): returns the per-field errors of a converted SchemaError or re-raises it
    bounded_repr(): renders a value in at most 80 characters, cutting its middle to `…`; catches
        only the ValueError that `repr` raises for an over-long int, so other repr errors escape
    GeometryError: reports a degenerate shape
    ZeroLengthError: reports a request for the direction of a zero vector
    SpacingError: reports items that do not fit
    TimeRangeError: reports times outside range or start >= end
    TimelineError: reports inconsistent tempo, measure or hold data
    SurfaceError: reports a point that is off the sheet
    MatchError: reports an assignment problem with no feasible solution
    PathError: reports an invalid path instruction
    ContinuityError: reports a path that misses its target placement
    LockedError: reports an edit that touches a pinned or locked item
    StorageError: reports a persistence failure
    ConflictError: reports a concurrent edit that landed first
    ValidationError: reports a structural integrity failure
    FontError: reports a font file that is invalid, hash-mismatched or whose name is taken
    MissingFontError: reports a font name or URI the registry does not hold
    GlyphError: reports a character the font chain has no glyph for
    DeprecatedWarning: warns that a deprecated API was used
    LegibilityWarning: warns that text was laid out with fewer spots than it reads well at
"""

import heapq
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from itertools import islice
from typing import TYPE_CHECKING, ClassVar, cast

import numpy as np
from pydantic import ValidationError as PydanticValidationError

from showpy.core.error_codes import ErrorCode
from showpy.core.json_types import JsonValue

if TYPE_CHECKING:
    from _typeshed import SupportsRichComparison

MAX_LISTED_ERRORS = 5
MAX_ECHOED_INPUT_CHARS = 80
MAX_CONTEXT_ITEMS = 20
# Deeper values are rendered as text; a reference cycle stops here too.
MAX_CONTEXT_DEPTH = 8
CUT_MARK = "…"
# A cut keeps both ends: the start names what the value is, the end holds a path's file name.
ECHO_HEAD_CHARS = 40
ECHO_TAIL_CHARS = MAX_ECHOED_INPUT_CHARS - ECHO_HEAD_CHARS - len(CUT_MARK)
MORE_PATTERN = re.compile(r"\(\+[0-9]+ more\)")
# CPython refuses to print ints longer than sys.int_max_str_digits, which the environment can
# lower to 640 and no further; ints at or past this bound echo by size so every environment
# prints the same text.
ECHO_INT_LIMIT = 10**640
ROOT_LOC = "(root)"
SCHEMA_ERROR_HINT = "fix the fields named in context"


class ShowPyError(Exception):
    """Reports a library failure with a code, a message, debugging context and a hint.

    Context is bounded on construction by `bounded_context`, so the error always prints,
    pickles and dumps to JSON in bounded size.

    Attributes:
        code: Stable machine-readable error code.
        message: What went wrong.
        context: JSON values that help debug the failure without reproducing it.
        hint: What the caller is allowed to do instead.
    """

    default_code: ClassVar[ErrorCode] = ErrorCode.STAGE

    def __init__(
        self,
        message: str,
        *,
        hint: str,
        context: Mapping[str, object] | None = None,
        code: ErrorCode | None = None,
    ) -> None:
        self.code = code or self.default_code
        self.message = message
        self.context: dict[str, JsonValue] = bounded_context(context or {})
        self.hint = hint
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"{self.code}: {self.message} (hint: {self.hint})"

    def __repr__(self) -> str:
        return f"{type(self).__name__}(code={str(self.code)!r}, context={self.context!r})"

    def __reduce__(self) -> tuple[Callable[..., "ShowPyError"], tuple[object, ...]]:
        """Pickles the error as its class, message, hint, context and code.

        The cause, context chain and traceback are dropped by design: they can hold objects
        that do not pickle, and the four fields already say what failed and what is allowed.
        The bounded context holds only JSON values, so it always pickles.
        """
        return _rebuild, (type(self), self.message, self.hint, self.context, self.code)

    def to_dict(self) -> dict[str, JsonValue]:
        """Renders the error as a JSON object with its type, code, message, hint and context.

        Returns:
            error_object: Keys `type`, `code`, `message`, `hint` and `context`, all JSON values.

        >>> LockedError("T4 is pinned", hint="pass force=True").to_dict()["code"]
        'LOCKED'
        """
        return {
            "type": type(self).__name__,
            "code": str(self.code),
            "message": self.message,
            "hint": self.hint,
            "context": dict(self.context),
        }


def _rebuild(
    error_class: type[ShowPyError],
    message: str,
    hint: str,
    context: Mapping[str, object],
    code: ErrorCode,
) -> ShowPyError:
    return error_class(message, hint=hint, context=context, code=code)


def bounded_context(context: Mapping[str, object]) -> dict[str, JsonValue]:
    """Bounds a context mapping to JSON values of limited size and depth.

    Text and reprs are cut to 80 characters. Lists, tuples, arrays, sets and mappings keep 20
    items or entries plus a `"(+n more)"` count; sets keep their 20 smallest items, by value,
    or by repr when the items do not compare. Numpy values become plain numbers or lists,
    mapping keys become text, non-finite floats become text, and anything else, or anything
    nested deeper than 8 levels, becomes its bounded repr, or its type name when the repr
    raises. Bounding a bounded context changes nothing.

    Cost is linear in the kept items, except that a set is scanned once in full to find its
    smallest items, since its iteration order can change with the hash seed.

    Args:
        context: Debugging values chosen by a raise site.

    Returns:
        bounded: A new dict of JSON values.

    >>> bounded_context({"ids": tuple(range(25))})["ids"][-1]
    '(+5 more)'
    """
    return _bounded_mapping(context, 0)


def _bounded_value(value: object, depth: int) -> JsonValue:
    """Bounds one context value nested `depth` levels down."""
    if value is None or isinstance(value, bool):
        return value
    if isinstance(value, np.generic):
        return _bounded_value(value.item(), depth)
    if isinstance(value, int):
        return value if abs(value) < ECHO_INT_LIMIT else bounded_repr(value)
    if isinstance(value, float):
        return value if math.isfinite(value) else bounded_repr(value)
    if isinstance(value, str) or depth >= MAX_CONTEXT_DEPTH:
        return _safe_repr(value)
    if isinstance(value, np.ndarray):
        return _bounded_array(value, depth)
    if isinstance(value, Mapping):
        return _bounded_mapping(value, depth + 1)
    if isinstance(value, list | tuple):
        return _bounded_items(value, depth)
    if isinstance(value, set | frozenset):
        return _bounded_set(value, depth)
    return _safe_repr(value)


def _safe_repr(value: object) -> str:
    """Returns the bounded repr of a value, or its type name when its repr raises."""
    try:
        return bounded_repr(value)
    # Every exception: a context value's own __repr__ can raise anything, and a failing repr
    # must not stop the error that carries it from being built.
    except Exception:
        return f"<unprintable {type(value).__name__}>"


def _bounded_mapping[K](mapping: Mapping[K, object], depth: int) -> dict[str, JsonValue]:
    """Keeps the first 20 entries of a mapping, counting the rest; a capped one stays whole."""
    keep = MAX_CONTEXT_ITEMS + 1 if _is_capped_mapping(mapping) else MAX_CONTEXT_ITEMS
    bounded = {
        _safe_repr(key): _bounded_value(item, depth) for key, item in islice(mapping.items(), keep)
    }
    if len(mapping) > keep:
        bounded[more_count(len(mapping) - keep)] = None
    return bounded


def _is_capped_mapping[K](mapping: Mapping[K, object]) -> bool:
    """Tells whether a mapping is a capped one: 20 entries and its `"(+n more)"` key."""
    if len(mapping) != MAX_CONTEXT_ITEMS + 1:
        return False
    last = next(islice(mapping, MAX_CONTEXT_ITEMS, None))
    return isinstance(last, str) and MORE_PATTERN.fullmatch(last) is not None


def _bounded_array(array: np.ndarray, depth: int) -> JsonValue:
    """Converts only the first 20 rows of an array, counting the rest."""
    if array.ndim == 0:
        return _bounded_value(array.item(), depth)
    kept: list[object] = array[:MAX_CONTEXT_ITEMS].tolist()
    return _listed(kept, len(array) - len(kept), depth)


def _bounded_set(items: set[object] | frozenset[object], depth: int) -> list[JsonValue]:
    """Keeps the 20 smallest items of a set, by value or else by repr, counting the rest."""
    try:
        # Comparability is only known at run time; a TypeError falls back to the repr order.
        kept: Sequence[object] = heapq.nsmallest(
            MAX_CONTEXT_ITEMS, cast("set[SupportsRichComparison]", items)
        )
    except TypeError:
        kept = heapq.nsmallest(MAX_CONTEXT_ITEMS, items, key=_safe_repr)
    return _listed(kept, len(items) - len(kept), depth)


def _bounded_items(items: list[object] | tuple[object, ...], depth: int) -> list[JsonValue]:
    """Keeps the first 20 items, counting the rest; an already capped list is kept whole."""
    kept = items if _is_capped(items) else items[:MAX_CONTEXT_ITEMS]
    return _listed(kept, len(items) - len(kept), depth)


def _listed(kept: Iterable[object], hidden: int, depth: int) -> list[JsonValue]:
    """Bounds the kept items and appends the count of hidden ones, if any."""
    bounded = [_bounded_value(item, depth + 1) for item in kept]
    if hidden:
        bounded.append(more_count(hidden))
    return bounded


def _is_capped(items: list[object] | tuple[object, ...]) -> bool:
    """Tells whether a list is a capped list: 20 items and its `"(+n more)"` count."""
    last = items[-1] if len(items) == MAX_CONTEXT_ITEMS + 1 else None
    return isinstance(last, str) and MORE_PATTERN.fullmatch(last) is not None


def more_count(hidden: int) -> str:
    """Renders the count of items left out of a list.

    >>> more_count(980)
    '(+980 more)'
    """
    return f"(+{hidden} more)"


def join_listed(parts: Sequence[str], *, separator: str = "; ") -> str:
    """Joins the first five parts of a message list and counts the rest.

    Args:
        parts: Message fragments, one per listed item.
        separator: Text placed between fragments.

    Returns:
        listed: At most five fragments, then `(+n more)` when any were left out.

    >>> join_listed([str(n) for n in range(7)])
    '0; 1; 2; 3; 4; (+2 more)'
    """
    shown = list(parts[:MAX_LISTED_ERRORS])
    if len(parts) > MAX_LISTED_ERRORS:
        shown.append(more_count(len(parts) - MAX_LISTED_ERRORS))
    return separator.join(shown)


class UnknownRefError(ShowPyError):
    """Reports an ID, name or unit that does not resolve."""

    default_code = ErrorCode.UNKNOWN_REF


class VersionError(ShowPyError):
    """Reports a pinned version that is missing or incompatible."""

    default_code = ErrorCode.VERSION


class SchemaError(ShowPyError):
    """Reports data that fails its schema or text format."""

    default_code = ErrorCode.SCHEMA


def schema_error_from(exc: PydanticValidationError, *, model: str) -> SchemaError:
    """Converts a pydantic ValidationError into a SchemaError that echoes bounded input.

    Args:
        exc: The pydantic error raised while validating the model.
        model: Name of the model that rejected the data.

    Returns:
        error: A SchemaError listing at most five failing fields and counting the rest, each
            input cut to 80 characters.

    >>> from pydantic import TypeAdapter, ValidationError
    >>> try:
    ...     TypeAdapter(int).validate_python("abc")
    ... except ValidationError as exc:
    ...     print(schema_error_from(exc, model="Count").context["model"])
    Count
    """
    errors = [
        {
            "loc": ".".join(str(part) for part in item["loc"]) or ROOT_LOC,
            "msg": item["msg"],
            "input": bounded_repr(item["input"]),
        }
        for item in exc.errors(include_url=False, include_context=False)
    ]
    listed = join_listed([f"{item['loc']}: {item['msg']}" for item in errors])
    return SchemaError(
        f"{model} rejects {len(errors)} field(s): {listed}",
        hint=SCHEMA_ERROR_HINT,
        context={"model": model, "errors": errors},
    )


def field_errors(error: SchemaError) -> list[dict[str, JsonValue]]:
    """Returns the per-field errors of a converted pydantic error, or re-raises the error.

    A SchemaError raised by a model validator carries no field errors; it already teaches, so
    it propagates unchanged.

    Args:
        error: SchemaError caught around a model validation call.

    Returns:
        errors: The per-field entries of the `context["errors"]` list that `schema_error_from`
            produced, without the count of entries past the context cap.

    >>> field_errors(SchemaError("x", hint="y", context={"errors": []}))
    []
    """
    errors = error.context.get("errors")
    if not isinstance(errors, list):
        raise error
    return [entry for entry in errors if isinstance(entry, dict)]


def bounded_repr(value: object) -> str:
    """Renders a value for an error context in at most 80 characters, never raising on size.

    Text echoes as itself, ints of 640 or more digits as their bit count, and anything else as
    its `repr`, or as its type name when the repr holds an int too long to print. Text too
    long to fit keeps its first 40 and last 39 characters around a `…`, so a cut number never
    reads as a whole one and a cut path keeps its file name.

    Args:
        value: Input to echo, as received from the caller.

    Returns:
        echo: Printable text of at most 80 characters.

    >>> bounded_repr(10**700)
    '<int of 2326 bits>'
    >>> bounded_repr([1.5, "a"])
    "[1.5, 'a']"
    >>> bounded_repr("/srv/" + "d/" * 50 + "show.json")
    '/srv/d/d/d/d/d/d/d/d/d/d/d/d/d/d/d/d/d/d…d/d/d/d/d/d/d/d/d/d/d/d/d/d/d/show.json'
    """
    if isinstance(value, int) and abs(value) >= ECHO_INT_LIMIT:
        return f"<int of {value.bit_length()} bits>"
    try:
        text = str.__str__(value) if isinstance(value, str) else repr(value)
    except ValueError:
        text = f"<unprintable {type(value).__name__}>"
    if len(text) <= MAX_ECHOED_INPUT_CHARS:
        return text
    return text[:ECHO_HEAD_CHARS] + CUT_MARK + text[-ECHO_TAIL_CHARS:]


class GeometryError(ShowPyError):
    """Reports a degenerate shape such as a zero radius or collinear arc points."""

    default_code = ErrorCode.GEOMETRY


class ZeroLengthError(GeometryError):
    """Reports a request for the direction of a zero vector."""

    default_code = ErrorCode.ZERO_LENGTH


class SpacingError(ShowPyError):
    """Reports items that do not fit; context carries the maximum that fits."""

    default_code = ErrorCode.SPACING


class TimeRangeError(ShowPyError):
    """Reports times outside range or a start not before its end."""

    default_code = ErrorCode.TIME_RANGE


class TimelineError(ShowPyError):
    """Reports inconsistent tempo, measure or hold data."""

    default_code = ErrorCode.TIMELINE


class SurfaceError(ShowPyError):
    """Reports a point that is off the sheet."""

    default_code = ErrorCode.SURFACE


class MatchError(ShowPyError):
    """Reports an assignment problem with no feasible solution."""

    default_code = ErrorCode.MATCH


class PathError(ShowPyError):
    """Reports an invalid path instruction."""

    default_code = ErrorCode.PATH


class ContinuityError(PathError):
    """Reports a path that does not end on its target placement."""

    default_code = ErrorCode.CONTINUITY


class LockedError(ShowPyError):
    """Reports an edit that touches something the designer locked."""

    default_code = ErrorCode.LOCKED


class StorageError(ShowPyError):
    """Reports a persistence failure."""

    default_code = ErrorCode.STORAGE


class ConflictError(StorageError):
    """Reports a concurrent edit that landed first."""

    default_code = ErrorCode.CONFLICT


class ValidationError(ShowPyError):
    """Reports a structural integrity failure."""

    default_code = ErrorCode.VALIDATION


class FontError(ShowPyError):
    """Reports a font file that is invalid, mismatches its hash or claims a taken name."""

    default_code = ErrorCode.BAD_FONT


class MissingFontError(UnknownRefError):
    """Reports a font name or URI the registry does not hold."""

    default_code = ErrorCode.MISSING_FONT


class GlyphError(UnknownRefError):
    """Reports a character that no font in the chain has a glyph for."""

    default_code = ErrorCode.MISSING_GLYPH


class DeprecatedWarning(UserWarning):
    """Warns that a deprecated API was used and names its replacement."""


class LegibilityWarning(UserWarning):
    """Warns that text was laid out with fewer spots than it reads well at."""
