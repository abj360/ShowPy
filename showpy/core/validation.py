"""
validation.py --- ValidationIssue and ValidationReport shared by every structural validator

Contains:
    ValidationIssue: describes one error or warning with location and hint
    ValidationReport: collects issues, reports ok, renders one line per issue
    ValidationReport.merge(): combines several reports into one
    validate_or_schema_error(): validates data against any pydantic model, raising SchemaError
    require_finite(): checks a numeric parameter is finite and inside optional bounds
"""

import math
from numbers import Real
from typing import Literal

import numpy as np
from pydantic import BaseModel, ValidationError

from showpy.core.errors import SchemaError, schema_error_from
from showpy.core.types import FrozenModel

LEVEL_PREFIX = {"error": "E", "warning": "W"}


class ValidationIssue(FrozenModel):
    """Describes one structural problem.

    Attributes:
        level: Whether the issue blocks (`error`) or only informs (`warning`).
        code: Stable issue code.
        where: Location, e.g. `set 12` or `12→13 T4`.
        message: What is wrong.
        hint: What would make it valid.
    """

    level: Literal["error", "warning"]
    code: str
    where: str
    message: str
    hint: str

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
    except ValidationError as exc:
        raise schema_error_from(exc, model=model.__name__) from exc


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
            and ints too large for a float are rejected.
        name: Parameter name, echoed in the error context.
        low: Lower bound, or None for no lower bound.
        low_open: Whether `low` itself is rejected.
        high: Inclusive upper bound, or None for no upper bound.

    Returns:
        number: The value as a float.

    >>> require_finite(2, name="tol", low=0.0, low_open=True)
    2.0
    """
    context = {"name": name, "value": value, "low": low, "high": high}
    if isinstance(value, bool | np.bool_) or not isinstance(value, Real):
        raise SchemaError(f"{name} must be a number", hint="pass an int or float", context=context)
    try:
        number = float(value)
    except OverflowError:
        number = math.inf
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
