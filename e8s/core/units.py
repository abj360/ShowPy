"""
units.py --- the only unit conversion point, plus angle helpers for facings

Contains:
    UnitRegistry: maps unit names to exact meters-per-unit fractions, immutably
    UnitRegistry.exact(): returns one unit's size as a fraction
    UnitRegistry.factor(): returns one unit's size as a float
    convert(): converts a length between two registered units
    register_unit(): returns a registry extended with one unit
    units(): lists the default units
    normalize_angle(): wraps an angle into [0, 360), computed in the core
    angle_diff(): returns the signed shortest turn from a to b, computed in the core
    facing_vector(): converts a facing to a unit direction, computed in the core
    vector_facing(): converts a direction to a facing, computed in the core
    round_to(): rounds half away from zero to a step, computed in the core
    DEFAULT_STEP: the step round_to rounds onto when none is given
    ZERO_VECTOR_TOL: the length under which a direction has no facing
"""

import math
from collections.abc import Mapping
from fractions import Fraction
from typing import Annotated

from pydantic import BeforeValidator, model_validator

from e8s._core.core import DEFAULT_STEP as DEFAULT_STEP
from e8s._core.core import ZERO_VECTOR_TOL as ZERO_VECTOR_TOL
from e8s._core.core import angle_diff as angle_diff
from e8s._core.core import facing_vector as facing_vector
from e8s._core.core import normalize_angle as normalize_angle
from e8s._core.core import round_to as round_to
from e8s._core.core import vector_facing as vector_facing
from e8s.core.errors import SchemaError, UnknownRefError
from e8s.core.readonly import ReadOnly
from e8s.core.types import FrozenModel

INCH_M = Fraction(127, 5000)
BASE_UNIT = "m"


def _as_exact_size(raw: object) -> object:
    if isinstance(raw, float):
        return Fraction(repr(raw)) if math.isfinite(raw) else raw
    return raw


type UnitSize = Annotated[Fraction, BeforeValidator(_as_exact_size)]


class UnitRegistry(FrozenModel):
    """Maps unit names to their size in meters as exact fractions.

    Attributes:
        meters_per_unit: Unit name to meters per one unit; read-only, and `m` is always 1.
    """

    meters_per_unit: Annotated[Mapping[str, UnitSize], ReadOnly()]

    @model_validator(mode="after")
    def _check_sizes(self) -> "UnitRegistry":
        if self.meters_per_unit.get(BASE_UNIT) != 1:
            raise SchemaError(
                f"unit {BASE_UNIT!r} must be present and equal 1",
                hint=f"include {BASE_UNIT!r} with a size of 1 meter",
                context={"unit": BASE_UNIT},
            )
        for name, size in self.meters_per_unit.items():
            if not name.strip() or size <= 0:
                raise SchemaError(
                    f"unit {name!r} needs a non-empty name and a positive size",
                    hint="give each unit a name and meters_per_unit above 0",
                    context={"unit": name, "meters_per_unit": str(size)},
                )
        return self

    def exact(self, name: str) -> Fraction:
        """Returns meters per unit as an exact fraction, raising UnknownRefError if unknown."""
        if name not in self.meters_per_unit:
            raise UnknownRefError(
                f"unknown unit {name!r}",
                hint=f"use one of {', '.join(sorted(self.meters_per_unit))}",
                context={"unit": name},
            )
        return self.meters_per_unit[name]

    def factor(self, name: str) -> float:
        """Returns meters per unit, raising UnknownRefError for unknown names."""
        return float(self.exact(name))


DEFAULT_UNITS = UnitRegistry(
    meters_per_unit={
        "m": Fraction(1),
        "cm": Fraction(1, 100),
        "mm": Fraction(1, 1000),
        "in": INCH_M,
        "ft": 12 * INCH_M,
        "yd": 36 * INCH_M,
    }
)


def convert(
    value: float, from_unit: str, to_unit: str, registry: UnitRegistry = DEFAULT_UNITS
) -> float:
    """Converts a length between units; the unit ratio is exact before it meets `value`.

    Args:
        value: Length in `from_unit`.
        from_unit: Source unit name.
        to_unit: Target unit name.
        registry: Units to resolve names against.

    Returns:
        converted: Length in `to_unit`.

    >>> convert(3.0, "ft", "yd")
    1.0
    """
    return value * float(registry.exact(from_unit) / registry.exact(to_unit))


def register_unit(registry: UnitRegistry, name: str, meters_per_unit: float) -> UnitRegistry:
    """Returns a new registry that also knows `name`; a float is read as its decimal text.

    Raises SchemaError for an empty name, for `m`, and for a size that is not finite and positive.

    >>> register_unit(DEFAULT_UNITS, "step85", 0.5715).factor("step85")
    0.5715
    """
    context = {"unit": name, "meters_per_unit": repr(meters_per_unit)}
    if not name.strip() or name == BASE_UNIT:
        raise SchemaError(
            f"unit name {name!r} is not allowed",
            hint=f"use a non-empty name other than {BASE_UNIT!r}",
            context=context,
        )
    if not math.isfinite(meters_per_unit) or meters_per_unit <= 0:
        raise SchemaError(
            f"unit {name!r} needs a finite positive size",
            hint="give meters_per_unit above 0",
            context=context,
        )
    return UnitRegistry(
        meters_per_unit={**registry.meters_per_unit, name: Fraction(repr(meters_per_unit))}
    )


def units() -> dict[str, float]:
    """Lists the default units and their size in meters.

    >>> units()["m"]
    1.0
    """
    return {name: float(size) for name, size in DEFAULT_UNITS.meters_per_unit.items()}
