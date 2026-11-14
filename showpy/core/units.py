"""
units.py --- the only unit conversion point, plus angle helpers for facings

Contains:
    UnitRegistry: maps unit names to meters per unit, immutably
    convert(): converts a length between two registered units
    register_unit(): returns a registry extended with one unit
    units(): lists the default units
    normalize_angle(): wraps an angle into [0, 360)
    angle_diff(): returns the signed shortest turn from a to b
    facing_vector(): converts a facing to a unit direction
    vector_facing(): converts a direction to a facing
    round_to(): rounds half away from zero to a precision
"""

import math
from decimal import ROUND_HALF_UP, Decimal

from showpy.core.errors import UnknownRefError, ZeroLengthError
from showpy.core.types import Degrees, FrozenModel, Point

INCH_M = 0.0254
ZERO_VECTOR_TOL = 1e-12


class UnitRegistry(FrozenModel):
    """Maps unit names to their size in meters.

    Attributes:
        meters_per_unit: Unit name to meters per one unit.
    """

    meters_per_unit: dict[str, float]

    def factor(self, name: str) -> float:
        """Returns meters per unit, raising UnknownRefError for unknown names."""
        if name not in self.meters_per_unit:
            raise UnknownRefError(
                f"unknown unit {name!r}",
                hint=f"use one of {', '.join(sorted(self.meters_per_unit))}",
                context={"unit": name},
            )
        return self.meters_per_unit[name]


DEFAULT_UNITS = UnitRegistry(
    meters_per_unit={
        "m": 1.0,
        "cm": 0.01,
        "mm": 0.001,
        "in": INCH_M,
        "ft": 12 * INCH_M,
        "yd": 36 * INCH_M,
    }
)


def convert(
    value: float, from_unit: str, to_unit: str, registry: UnitRegistry = DEFAULT_UNITS
) -> float:
    """Converts a length between units.

    Args:
        value: Length in `from_unit`.
        from_unit: Source unit name.
        to_unit: Target unit name.
        registry: Units to resolve names against.

    Returns:
        converted: Length in `to_unit`.

    >>> round(convert(3.0, "ft", "yd"), 6)
    1.0
    """
    return value * registry.factor(from_unit) / registry.factor(to_unit)


def register_unit(registry: UnitRegistry, name: str, meters_per_unit: float) -> UnitRegistry:
    """Returns a new registry that also knows `name`.

    >>> register_unit(DEFAULT_UNITS, "step85", 0.5715).factor("step85")
    0.5715
    """
    if not meters_per_unit > 0:
        raise UnknownRefError(
            f"unit {name!r} needs a positive size",
            hint="give meters_per_unit above 0",
            context={"unit": name, "meters_per_unit": meters_per_unit},
        )
    return UnitRegistry(meters_per_unit={**registry.meters_per_unit, name: meters_per_unit})


def units() -> dict[str, float]:
    """Lists the default units and their size in meters.

    >>> units()["m"]
    1.0
    """
    return dict(DEFAULT_UNITS.meters_per_unit)


def normalize_angle(a: Degrees) -> Degrees:
    """Wraps an angle into [0, 360).

    >>> normalize_angle(-90.0)
    270.0
    """
    wrapped = math.fmod(a, 360.0)
    wrapped = wrapped + 360.0 if wrapped < 0 else wrapped
    return 0.0 if wrapped >= 360.0 else wrapped


def angle_diff(a: Degrees, b: Degrees) -> Degrees:
    """Returns the signed shortest turn from `a` to `b`, in (-180, 180].

    >>> angle_diff(350.0, 10.0)
    20.0
    """
    d = normalize_angle(b - a)
    return d - 360.0 if d > 180.0 else d


def facing_vector(theta: Degrees) -> Point:
    """Converts a facing to a unit direction; 0° faces the audience (0, -1).

    >>> facing_vector(0.0)
    (0.0, -1.0)
    """
    rad = math.radians(theta)
    return math.sin(rad) + 0.0, -math.cos(rad) + 0.0


def vector_facing(v: Point) -> Degrees:
    """Converts a direction to a facing in [0, 360).

    >>> vector_facing((1.0, 0.0))
    90.0
    """
    if math.hypot(v[0], v[1]) < ZERO_VECTOR_TOL:
        raise ZeroLengthError(
            "facing of a zero vector is undefined",
            hint="pass a non-zero direction or keep the previous facing",
            context={"vector": v},
        )
    return normalize_angle(math.degrees(math.atan2(v[0], -v[1])))


def round_to(value: float, precision: float = 0.01) -> float:
    """Rounds to a multiple of `precision`, half away from zero, for display.

    >>> round_to(2.345)
    2.35
    """
    steps = (Decimal(repr(value)) / Decimal(repr(precision))).quantize(
        Decimal(1), rounding=ROUND_HALF_UP
    )
    return float(steps * Decimal(repr(precision)))
