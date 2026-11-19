"""
validation.py --- ValidationIssue and ValidationReport shared by every structural validator

Contains:
    ValidationIssue: describes one error or warning with location, hint and bounded context
    ValidationReport: collects issues, reports ok, renders one line per issue
    ValidationReport.merge(): combines several reports into one
    ValidationReport.raise_for_errors(): raises a ValidationError when any issue is an error
    ValidationReport.to_json(): renders the report as JSON text
    validate_or_schema_error(): validates data against any pydantic model, raising SchemaError
    validate_adapter(): validates a Python value against a TypeAdapter, raising SchemaError
    validate_adapter_json(): validates JSON bytes or text against a TypeAdapter, raising SchemaError
    require_finite(): checks a numeric parameter is finite and inside optional bounds
"""

import math
from collections.abc import Mapping
from numbers import Real
from typing import Annotated, Literal

import numpy as np
from pydantic import BaseModel, Field, TypeAdapter, field_validator
from pydantic import ValidationError as PydanticValidationError

from showpy.core.errors import (
    SchemaError,
    ValidationError,
    bounded_context,
    bounded_repr,
    join_listed,
    schema_error_from,
)
from showpy.core.json_types import JsonValue
from showpy.core.readonly import ReadOnly
from showpy.core.types import FrozenModel

LEVEL_PREFIX = {"error": "E", "warning": "W"}
ERRORS_HINT = "fix the issues listed in context, then validate again"


class ValidationIssue(FrozenModel):
    """Describes one structural problem.

    Attributes:
        level: Whether the issue blocks (`error`) or only informs (`warning`).
        code: Stable issue code.
        where: Location, e.g. `set 12` or `12→13 T4`.
        message: What is wrong.
        hint: What would make it valid.
        context: JSON values that help debug the issue, bounded like an error's context.
    """

    level: Literal["error", "warning"]
    code: str
    where: str
    message: str
    hint: str
    context: Annotated[Mapping[str, JsonValue], ReadOnly()] = Field(default_factory=dict)

    @field_validator("context", mode="before")
    @classmethod
    def _bound_context(cls, context: object) -> object:
        return bounded_context(context) if isinstance(context, Mapping) else context

    def __str__(self) -> str:
        prefix = LEVEL_PREFIX[self.level]
        return f"{prefix}: {self.code} {self.where}: {self.message} (hint: {self.hint})"


class ValidationReport(FrozenModel):
    """Collects validation issues.

    Attributes:
        issues: Every issue found, in discovery order.
    """

    issues: tuple[ValidationIssue, ...] = ()

    @property
    def ok(self) -> bool:
        """Returns True when no issue is an error."""
        return all(issue.level != "error" for issue in self.issues)

    @property
    def errors(self) -> tuple[ValidationIssue, ...]:
        """Returns the issues that block, in discovery order."""
        return tuple(issue for issue in self.issues if issue.level == "error")

    @property
    def warnings(self) -> tuple[ValidationIssue, ...]:
        """Returns the issues that only inform, in discovery order."""
        return tuple(issue for issue in self.issues if issue.level == "warning")

    @property
    def codes(self) -> tuple[str, ...]:
        """Returns each issue code once, in order of first discovery."""
        return tuple(dict.fromkeys(issue.code for issue in self.issues))

    def raise_for_errors(self) -> None:
        """Raises a ValidationError listing the error issues; returns when there are none.

        The message names at most five error codes and counts the rest; the context holds the
        error issues, capped like every error context.

        >>> from showpy.examples import two_set_show
        >>> from showpy.show.validate import validate_show
        >>> validate_show(two_set_show()).raise_for_errors()
        """
        errors = self.errors
        if not errors:
            return
        listed = join_listed([f"{issue.code} {issue.where}" for issue in errors], separator=", ")
        raise ValidationError(
            f"{len(errors)} validation error(s): {listed}",
            hint=ERRORS_HINT,
            context={"issues": [issue.model_dump(mode="json") for issue in errors]},
        )

    def to_json(self) -> str:
        """Renders the report as JSON text that `model_validate_json` reads back.

        >>> ValidationReport().to_json()
        '{"issues":[]}'
        """
        return self.model_dump_json()

    @classmethod
    def merge(cls, *reports: "ValidationReport") -> "ValidationReport":
        """Combines reports, keeping issue order."""
        return cls(issues=tuple(issue for report in reports for issue in report.issues))

    def __str__(self) -> str:
        return "\n".join(str(issue) for issue in self.issues)


def validate_or_schema_error[M: BaseModel](model: type[M], data: object) -> M:
    """Validates data against a model, raising SchemaError instead of a pydantic error.

    Args:
        model: Pydantic model class to validate against.
        data: Raw value to validate.

    Returns:
        instance: The validated model instance.

    >>> from showpy.core.types import Box2
    >>> validate_or_schema_error(Box2, {"min": (0, 0), "max": (1, 1)}).max
    (1.0, 1.0)
    """
    try:
        return model.model_validate(data)
    except PydanticValidationError as exc:
        raise schema_error_from(exc, model=model.__name__) from exc


def validate_adapter[T](adapter: TypeAdapter[T], data: object, *, name: str) -> T:
    """Validates a Python value against a TypeAdapter, raising SchemaError on failure.

    Args:
        adapter: Standalone adapter for a union or generic type no model class covers.
        data: Raw value to validate.
        name: What the adapter validates, echoed in the error context.

    Returns:
        value: The validated value.

    >>> from pydantic import TypeAdapter
    >>> validate_adapter(TypeAdapter(list[int]), ["1"], name="Ints")
    [1]
    """
    try:
        return adapter.validate_python(data)
    except PydanticValidationError as exc:
        raise schema_error_from(exc, model=name) from exc


def validate_adapter_json[T](adapter: TypeAdapter[T], raw: str | bytes, *, name: str) -> T:
    """Validates JSON against a TypeAdapter; bad JSON or UTF-8 is a SchemaError too.

    Args:
        adapter: Standalone adapter for a union or generic type no model class covers.
        raw: JSON text or UTF-8 bytes.
        name: What the adapter validates, echoed in the error context.

    Returns:
        value: The validated value.

    >>> from pydantic import TypeAdapter
    >>> validate_adapter_json(TypeAdapter(list[int]), b"[1]", name="Ints")
    [1]
    """
    try:
        return adapter.validate_json(raw)
    except PydanticValidationError as exc:
        raise schema_error_from(exc, model=name) from exc


def require_finite(
    value: float,
    *,
    name: str,
    low: float | None = None,
    low_open: bool = False,
    high: float | None = None,
) -> float:
    """Checks a numeric parameter is finite and inside optional bounds.

    Args:
        value: The number to check; any real number, numpy scalars included. Bools, non-numbers
            and ints too large for a float are rejected. The error context echoes it as text
            through `bounded_repr`.
        name: Parameter name, echoed in the error context.
        low: Lower bound, or None for no lower bound.
        low_open: Whether `low` itself is rejected.
        high: Inclusive upper bound, or None for no upper bound.

    Returns:
        number: The value as a float.

    >>> require_finite(2, name="tol", low=0.0, low_open=True)
    2.0
    """
    context = {"name": name, "value": bounded_repr(value), "low": low, "high": high}
    if isinstance(value, bool | np.bool_) or not isinstance(value, Real):
        raise SchemaError(f"{name} must be a number", hint="pass an int or float", context=context)
    try:
        number = float(value)
    except OverflowError as exc:
        raise SchemaError(
            f"{name} must be finite; it is too large for a float",
            hint="pass a finite number",
            context=context,
        ) from exc
    if not math.isfinite(number):
        raise SchemaError(f"{name} must be finite", hint="pass a finite number", context=context)
    if low is not None and (number < low or (low_open and number == low)):
        relation = ">" if low_open else ">="
        raise SchemaError(
            f"{name} must be {relation} {low}",
            hint=f"pass {name} {relation} {low}",
            context=context,
        )
    if high is not None and number > high:
        raise SchemaError(
            f"{name} must be <= {high}", hint=f"pass {name} <= {high}", context=context
        )
    return number
