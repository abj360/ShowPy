"""
checks.py --- parameter checks that raise geometry-family errors instead of SchemaError

Contains:
    pick(): looks up a named option, raising UnknownRefError with suggestions
    require_positive(): checks a finite number above 0 and raises GeometryError
    require_finite_as(): checks a finite, bounded number and raises the caller's error class
"""

from collections.abc import Mapping

from showpy.core.errors import GeometryError, SchemaError, ShowPyError, UnknownRefError
from showpy.core.util import close_matches
from showpy.core.validation import require_finite


def pick[V](kind: str, name: str, options: Mapping[str, V]) -> V:
    """Looks up a named option; an unknown name raises UnknownRefError with close matches.

    >>> pick("axis", "x", {"x": 0, "y": 1})
    0
    """
    if name not in options:
        raise UnknownRefError(
            f"unknown {kind} {name!r}",
            hint=f"use one of {sorted(options)}; did you mean {close_matches(name, options)}?",
            context={kind: name},
        )
    return options[name]


def require_finite_as(
    error: type[ShowPyError],
    value: float,
    *,
    name: str,
    low: float | None = None,
    low_open: bool = False,
    high: float | None = None,
) -> float:
    """Checks `value` is finite and inside its bounds, raising `error` when it is not.

    Args:
        error: Error class to raise, such as `GeometryError`.
        value: Number to check.
        name: Parameter name used in the message.
        low: Smallest allowed value, or None for no lower bound.
        low_open: Whether `low` itself is rejected.
        high: Largest allowed value, or None for no upper bound.

    Returns:
        checked_value: The value as a float.

    Examples:
        >>> from showpy.core.errors import GeometryError
        >>> from showpy.geo.checks import require_finite_as
        >>> require_finite_as(GeometryError, 0.5, name="tol", low=0.0, low_open=True)
        0.5
        >>> require_finite_as(GeometryError, 0.0, name="tol", low=0.0, low_open=True)
        Traceback (most recent call last):
            ...
        showpy.core.errors.GeometryError: GEOMETRY: tol must be > 0.0 (hint: pass tol > 0.0)
    """
    try:
        return require_finite(value, name=name, low=low, low_open=low_open, high=high)
    except SchemaError as exc:
        raise error(exc.message, hint=exc.hint, context=exc.context) from exc


def require_positive(name: str, value: float) -> float:
    """Checks `value` is finite and above 0, raising GeometryError when it is not.

    >>> from showpy.geo.checks import require_positive
    >>> require_positive("radius", 2.0)
    2.0
    """
    return require_finite_as(GeometryError, value, name=name, low=0.0, low_open=True)
