"""
numbers.py --- plain-decimal number text and angle wrapping, free of the model layer

Contains:
    TURN_DEG: the degrees of one full turn
    DEFAULT_DECIMALS: the decimals format_number keeps when none are given
    MAX_DECIMALS: the most decimals format_number keeps
    format_number(): writes a float in plain decimal notation, never in exponent form
    normalize_angle(): wraps an angle into [0, 360) and rejects NaN and infinity
"""

from e8s._core.core import DEFAULT_DECIMALS as DEFAULT_DECIMALS
from e8s._core.core import MAX_DECIMALS as MAX_DECIMALS
from e8s._core.core import TURN_DEG as TURN_DEG
from e8s._core.core import format_number as format_number
from e8s._core.core import normalize_angle as normalize_angle
