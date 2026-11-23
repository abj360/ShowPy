"""
errors.py --- the E8sError family: every library error carries code, message, context, hint

Contains:
    E8sError: reports a library failure with a code and a hint that says what is allowed
    E8sError.to_dict(): renders the error as a JSON object
    bounded_context(), more_count(), bounded_repr(): re-exported from error_context
    join_listed(): joins the first five parts of a message list and counts the rest
    UnknownRefError: reports an ID, name or unit that does not resolve
    VersionError: reports a pinned version that is missing or incompatible
    SchemaError: reports data that fails its schema or text format
    schema_error_from(): converts a pydantic ValidationError into a SchemaError
    BOUND_KEY, ALLOWED_KEY: name the error-context keys that carry a value's named bound; the
        SchemaError hint repeats them
    field_errors(): returns the per-field errors of a converted SchemaError or re-raises it
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
    ShowValidationError: reports a structural integrity failure
    FontError: reports a font file that is invalid, hash-mismatched or whose name is taken
    MissingFontError: reports a font name or URI the registry does not hold
    GlyphError: reports a character the font chain has no glyph for
    DeprecatedWarning: warns that a deprecated API was used (defined in e8s.core.deprecation)
    LegibilityWarning: warns that text was laid out with fewer spots than it reads well at
"""

from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING, ClassVar

from pydantic import ValidationError as PydanticValidationError

from e8s.core.deprecation import DeprecatedWarning as DeprecatedWarning
from e8s.core.deprecation import moved
from e8s.core.error_codes import ErrorCode
from e8s.core.error_context import CUT_MARK as CUT_MARK
from e8s.core.error_context import ECHO_HEAD_CHARS as ECHO_HEAD_CHARS
from e8s.core.error_context import MAX_CONTEXT_ITEMS as MAX_CONTEXT_ITEMS
from e8s.core.error_context import MAX_ECHOED_INPUT_CHARS as MAX_ECHOED_INPUT_CHARS
from e8s.core.error_context import MAX_RANKED_SET_ITEMS as MAX_RANKED_SET_ITEMS
from e8s.core.error_context import bounded_context as bounded_context
from e8s.core.error_context import bounded_repr as bounded_repr
from e8s.core.error_context import more_count as more_count
from e8s.core.json_types import JsonValue

MAX_LISTED_ERRORS = 5
ROOT_LOC = "(root)"
SCHEMA_ERROR_HINT = "fix the fields named in context"
BOUND_KEY = "bound"
ALLOWED_KEY = "allowed"


class E8sError(Exception):
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

    def __reduce__(self) -> tuple[Callable[..., "E8sError"], tuple[object, ...]]:
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
    error_class: type[E8sError],
    message: str,
    hint: str,
    context: Mapping[str, object],
    code: ErrorCode,
) -> E8sError:
    return error_class(message, hint=hint, context=context, code=code)


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


class UnknownRefError(E8sError):
    """Reports an ID, name or unit that does not resolve."""

    default_code = ErrorCode.UNKNOWN_REF


class VersionError(E8sError):
    """Reports a pinned version that is missing or incompatible."""

    default_code = ErrorCode.VERSION


class SchemaError(E8sError):
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
        hint=_schema_hint(exc),
        context={"model": model, "errors": errors},
    )


def _schema_hint(exc: PydanticValidationError) -> str:
    """Names the bound of each value that left its named range, after the generic hint."""
    bounds: dict[str, None] = {}
    for item in exc.errors(include_url=False):
        context = item.get("ctx", {})
        if BOUND_KEY in context and ALLOWED_KEY in context:
            bounds[f"{context[ALLOWED_KEY]} ({context[BOUND_KEY]})"] = None
    if not bounds:
        return SCHEMA_ERROR_HINT
    return f"{SCHEMA_ERROR_HINT}; keep each value {' or '.join(bounds)}"


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


class GeometryError(E8sError):
    """Reports a degenerate shape such as a zero radius or collinear arc points."""

    default_code = ErrorCode.GEOMETRY


class ZeroLengthError(GeometryError):
    """Reports a request for the direction of a zero vector."""

    default_code = ErrorCode.ZERO_LENGTH


class SpacingError(E8sError):
    """Reports items that do not fit; context carries the maximum that fits."""

    default_code = ErrorCode.SPACING


class TimeRangeError(E8sError):
    """Reports times outside range or a start not before its end."""

    default_code = ErrorCode.TIME_RANGE


class TimelineError(E8sError):
    """Reports inconsistent tempo, measure or hold data."""

    default_code = ErrorCode.TIMELINE


class SurfaceError(E8sError):
    """Reports a point that is off the sheet."""

    default_code = ErrorCode.SURFACE


class MatchError(E8sError):
    """Reports an assignment problem with no feasible solution."""

    default_code = ErrorCode.MATCH


class PathError(E8sError):
    """Reports an invalid path instruction."""

    default_code = ErrorCode.PATH


class ContinuityError(PathError):
    """Reports a path that does not end on its target placement."""

    default_code = ErrorCode.CONTINUITY


class LockedError(E8sError):
    """Reports an edit that touches something the designer locked."""

    default_code = ErrorCode.LOCKED


class StorageError(E8sError):
    """Reports a persistence failure."""

    default_code = ErrorCode.STORAGE


class ConflictError(StorageError):
    """Reports a concurrent edit that landed first."""

    default_code = ErrorCode.CONFLICT


class ShowValidationError(E8sError):
    """Reports a structural integrity failure."""

    default_code = ErrorCode.VALIDATION


class FontError(E8sError):
    """Reports a font file that is invalid, mismatches its hash or claims a taken name."""

    default_code = ErrorCode.BAD_FONT


class MissingFontError(UnknownRefError):
    """Reports a font name or URI the registry does not hold."""

    default_code = ErrorCode.MISSING_FONT


class GlyphError(UnknownRefError):
    """Reports a character that no font in the chain has a glyph for."""

    default_code = ErrorCode.MISSING_GLYPH


class LegibilityWarning(UserWarning):
    """Warns that text was laid out with fewer spots than it reads well at."""


__getattr__ = moved(
    __name__,
    {"ValidationError": f"{__name__}.ShowValidationError"},
    since="0.4.0",
    remove="0.6.0",
)

if TYPE_CHECKING:
    ValidationError = ShowValidationError
