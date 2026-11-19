"""
numbers.py --- plain-decimal number text and angle wrapping, free of the model layer

Contains:
    format_number(): writes a float in plain decimal notation, never in exponent form
    normalize_angle(): wraps an angle into [0, 360) and rejects NaN and infinity
"""

import math
from numbers import Real

from showpy.core.errors import SchemaError

TURN_DEG = 360.0
DEFAULT_DECIMALS = 6


def format_number(value: float, *, max_decimals: int = DEFAULT_DECIMALS) -> str:
    """Writes a float in plain decimal notation with trailing zeros removed.

    Values that round to zero print as `0`, never `-0`.

    Args:
        value: A finite number.
        max_decimals: Most digits kept after the decimal point.

    Returns:
        text: Digits with an optional sign and point; never an exponent.

    >>> format_number(1234567.5)
    '1234567.5'
    >>> format_number(3.0), format_number(1e-9)
    ('3', '0')
    """
    if max_decimals < 0:
        raise SchemaError(
            f"max_decimals must be 0 or more, got {max_decimals}",
            hint="pass max_decimals >= 0",
            context={"max_decimals": max_decimals},
        )
    if not math.isfinite(value):
        raise SchemaError(
            f"cannot format {value!r}", hint="pass a finite number", context={"value": repr(value)}
        )
    text = f"{value:.{max_decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return "0" if text == "-0" else text


def normalize_angle(deg: float) -> float:
    """Wraps an angle into [0, 360); the result is never -0.0.

    >>> normalize_angle(-90.0)
    270.0
    """
    if isinstance(deg, bool) or not isinstance(deg, Real) or not math.isfinite(deg):
        raise SchemaError(
            f"angle must be a finite number, got {deg!r}",
            hint="pass a finite number of degrees",
            context={"deg": repr(deg)},
        )
    wrapped = math.fmod(float(deg), TURN_DEG)
    wrapped = wrapped + TURN_DEG if wrapped < 0 else wrapped
    return 0.0 if wrapped >= TURN_DEG else wrapped + 0.0
