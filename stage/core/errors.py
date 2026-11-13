#!/usr/bin/env python3

"""
errors.py --- the StageError family: every library error carries code, message, context, hint

Contains:
    StageError: reports a library failure with a code and a hint that says what is allowed
    UnknownRefError: reports an ID, name or unit that does not resolve
    VersionError: reports a pinned version that is missing or incompatible
    SchemaError: reports data that fails its schema or text format
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
    DeprecatedWarning: warns that a deprecated API was used
"""

from collections.abc import Mapping
from typing import Any, ClassVar


class StageError(Exception):
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


class UnknownRefError(StageError):
    """Reports an ID, name or unit that does not resolve."""

    default_code = "UNKNOWN_REF"


class VersionError(StageError):
    """Reports a pinned version that is missing or incompatible."""

    default_code = "VERSION"


class SchemaError(StageError):
    """Reports data that fails its schema or text format."""

    default_code = "SCHEMA"


class GeometryError(StageError):
    """Reports a degenerate shape such as a zero radius or collinear arc points."""

    default_code = "GEOMETRY"


class ZeroLengthError(GeometryError):
    """Reports a request for the direction of a zero vector."""

    default_code = "ZERO_LENGTH"


class SpacingError(StageError):
    """Reports items that do not fit; context carries the maximum that fits."""

    default_code = "SPACING"


class TimeRangeError(StageError):
    """Reports times outside range or a start not before its end."""

    default_code = "TIME_RANGE"


class TimelineError(StageError):
    """Reports inconsistent tempo, measure or hold data."""

    default_code = "TIMELINE"


class SurfaceError(StageError):
    """Reports a point that is off the sheet."""

    default_code = "SURFACE"


class MatchError(StageError):
    """Reports an assignment problem with no feasible solution."""

    default_code = "MATCH"


class PathError(StageError):
    """Reports an invalid path instruction."""

    default_code = "PATH"


class ContinuityError(PathError):
    """Reports a path that does not end on its target placement."""

    default_code = "CONTINUITY"


class LockedError(StageError):
    """Reports an edit that touches something the designer locked."""

    default_code = "LOCKED"


class StorageError(StageError):
    """Reports a persistence failure."""

    default_code = "STORAGE"


class ConflictError(StorageError):
    """Reports a concurrent edit that landed first."""

    default_code = "CONFLICT"


class ValidationError(StageError):
    """Reports a structural integrity failure."""

    default_code = "VALIDATION"


class DeprecatedWarning(UserWarning):
    """Warns that a deprecated API was used and names its replacement."""
