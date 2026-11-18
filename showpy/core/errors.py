"""
errors.py --- the ShowPyError family: every library error carries code, message, context, hint

Contains:
    ShowPyError: reports a library failure with a code and a hint that says what is allowed
    UnknownRefError: reports an ID, name or unit that does not resolve
    VersionError: reports a pinned version that is missing or incompatible
    SchemaError: reports data that fails its schema or text format
    schema_error_from(): converts a pydantic ValidationError into a SchemaError
    field_errors(): returns the per-field errors of a converted SchemaError or re-raises it
    bounded_repr(): renders a value for an error context in at most 80 characters; never fails
        on size, and catches only the ValueError that `repr` raises for an over-long int
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

from collections.abc import Callable, Mapping
from typing import Any, ClassVar

from pydantic import ValidationError as PydanticValidationError

MAX_LISTED_ERRORS = 5
MAX_ECHOED_INPUT_CHARS = 80
# CPython refuses to print ints longer than sys.int_max_str_digits, which the environment can
# lower to 640 and no further; ints at or past this bound echo by size so every environment
# prints the same text.
ECHO_INT_LIMIT = 10**640
ROOT_LOC = "(root)"
SCHEMA_ERROR_HINT = "fix the fields named in context"


class ShowPyError(Exception):
    """Reports a library failure with a code, a message, debugging context and a hint.

    Attributes:
        code: Stable machine-readable error code.
        message: What went wrong.
        context: Values that help debug the failure without reproducing it.
        hint: What the caller is allowed to do instead.
    """

    default_code: ClassVar[str] = "STAGE"

    # Any: context carries arbitrary JSON-like debugging values chosen by each raise site.
    def __init__(
        self,
        message: str,
        *,
        hint: str,
        context: Mapping[str, Any] | None = None,
        code: str | None = None,
    ) -> None:
        self.code = code or self.default_code
        self.message = message
        self.context: dict[str, Any] = dict(context or {})
        self.hint = hint
        super().__init__(str(self))

    def __str__(self) -> str:
        return f"{self.code}: {self.message} (hint: {self.hint})"

    def __reduce__(self) -> tuple[Callable[..., "ShowPyError"], tuple[object, ...]]:
        """Pickles the error as its class, message, hint, context and code.

        The cause, context chain and traceback are dropped by design: they can hold objects
        that do not pickle, and the four fields already say what failed and what is allowed.
        """
        return _rebuild, (type(self), self.message, self.hint, self.context, self.code)


def _rebuild(
    error_class: type[ShowPyError],
    message: str,
    hint: str,
    context: Mapping[str, Any],
    code: str,
) -> ShowPyError:
    return error_class(message, hint=hint, context=context, code=code)


class UnknownRefError(ShowPyError):
    """Reports an ID, name or unit that does not resolve."""

    default_code = "UNKNOWN_REF"


class VersionError(ShowPyError):
    """Reports a pinned version that is missing or incompatible."""

    default_code = "VERSION"


class SchemaError(ShowPyError):
    """Reports data that fails its schema or text format."""

    default_code = "SCHEMA"


def schema_error_from(exc: PydanticValidationError, *, model: str) -> SchemaError:
    """Converts a pydantic ValidationError into a SchemaError that echoes bounded input.

    Args:
        exc: The pydantic error raised while validating the model.
        model: Name of the model that rejected the data.

    Returns:
        error: A SchemaError listing at most five failing fields, each input cut to 80 characters.

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
    listed = "; ".join(f"{item['loc']}: {item['msg']}" for item in errors[:MAX_LISTED_ERRORS])
    return SchemaError(
        f"{model} rejects {len(errors)} field(s): {listed}",
        hint=SCHEMA_ERROR_HINT,
        context={"model": model, "errors": errors},
    )


def field_errors(error: SchemaError) -> list[dict[str, Any]]:
    """Returns the per-field errors of a converted pydantic error, or re-raises the error.

    A SchemaError raised by a model validator carries no field errors; it already teaches, so
    it propagates unchanged.

    Args:
        error: SchemaError caught around a model validation call.

    Returns:
        errors: The `context["errors"]` list that `schema_error_from` produced.

    >>> field_errors(SchemaError("x", hint="y", context={"errors": []}))
    []
    """
    if "errors" not in error.context:
        raise error
    errors: list[dict[str, Any]] = error.context["errors"]
    return errors


def bounded_repr(value: object) -> str:
    """Renders a value for an error context in at most 80 characters, never raising on size.

    Text echoes as itself, ints of 640 or more digits as their bit count, and anything else as
    its `repr`, or as its type name when the repr holds an int too long to print.

    Args:
        value: Input to echo, as received from the caller.

    Returns:
        echo: Printable text of at most 80 characters.

    >>> bounded_repr(10**700)
    '<int of 2326 bits>'
    >>> bounded_repr([1.5, "a"])
    "[1.5, 'a']"
    """
    if isinstance(value, int) and abs(value) >= ECHO_INT_LIMIT:
        return f"<int of {value.bit_length()} bits>"
    try:
        text = value if isinstance(value, str) else repr(value)
    except ValueError:
        text = f"<unprintable {type(value).__name__}>"
    return text[:MAX_ECHOED_INPUT_CHARS]


class GeometryError(ShowPyError):
    """Reports a degenerate shape such as a zero radius or collinear arc points."""

    default_code = "GEOMETRY"


class ZeroLengthError(GeometryError):
    """Reports a request for the direction of a zero vector."""

    default_code = "ZERO_LENGTH"


class SpacingError(ShowPyError):
    """Reports items that do not fit; context carries the maximum that fits."""

    default_code = "SPACING"


class TimeRangeError(ShowPyError):
    """Reports times outside range or a start not before its end."""

    default_code = "TIME_RANGE"


class TimelineError(ShowPyError):
    """Reports inconsistent tempo, measure or hold data."""

    default_code = "TIMELINE"


class SurfaceError(ShowPyError):
    """Reports a point that is off the sheet."""

    default_code = "SURFACE"


class MatchError(ShowPyError):
    """Reports an assignment problem with no feasible solution."""

    default_code = "MATCH"


class PathError(ShowPyError):
    """Reports an invalid path instruction."""

    default_code = "PATH"


class ContinuityError(PathError):
    """Reports a path that does not end on its target placement."""

    default_code = "CONTINUITY"


class LockedError(ShowPyError):
    """Reports an edit that touches something the designer locked."""

    default_code = "LOCKED"


class StorageError(ShowPyError):
    """Reports a persistence failure."""

    default_code = "STORAGE"


class ConflictError(StorageError):
    """Reports a concurrent edit that landed first."""

    default_code = "CONFLICT"


class ValidationError(ShowPyError):
    """Reports a structural integrity failure."""

    default_code = "VALIDATION"


class FontError(ShowPyError):
    """Reports a font file that is invalid, mismatches its hash or claims a taken name."""

    default_code = "BAD_FONT"


class MissingFontError(UnknownRefError):
    """Reports a font name or URI the registry does not hold."""

    default_code = "MISSING_FONT"


class GlyphError(UnknownRefError):
    """Reports a character that no font in the chain has a glyph for."""

    default_code = "MISSING_GLYPH"


class DeprecatedWarning(UserWarning):
    """Warns that a deprecated API was used and names its replacement."""


class LegibilityWarning(UserWarning):
    """Warns that text was laid out with fewer spots than it reads well at."""
