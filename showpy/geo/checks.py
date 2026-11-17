"""
checks.py --- parameter checks that raise geometry-family errors instead of SchemaError

Contains:
    require_finite_as(): checks a finite, bounded number and raises the caller's error class
"""

from showpy.core.errors import SchemaError, ShowPyError
from showpy.core.validation import require_finite


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
