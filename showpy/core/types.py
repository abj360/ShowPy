"""
types.py --- shared type aliases plus the JSON-safe Box2, Box3 and Region models

Contains:
    Finite, NonNegFinite, PositiveFinite: constrain a float to be finite, non-negative or positive
    Meters, Seconds, Count, Degrees: finite floats in the library's one unit system
    NonNegCount, NonNegSeconds, PositiveMeters: range-limited unit aliases
    CountsPerMinute: positive finite tempo alias
    Heading: wraps a stored facing into [0, 360)
    FinitePoint: a finite (x, y) pair with no coordinate bound, for derived values such as
        velocities
    JsonValue: the JSON data model, re-exported from json_types
    FrozenJsonValue: the read-only JSON value a JsonObject holds, re-exported from json_types
    JsonObject: a deeply read-only, hashable JSON object for open payloads; dumps plain JSON
    FrozenModel: provides the immutable, strict base that raises SchemaError on bad data
    FrozenModel.replace(): returns a revalidated copy with some fields changed
    FrozenModel.model_copy(): returns a copy whose cached_property values are dropped
    Box2: bounds stage-plane points and reports size and center
    Box3: bounds world points
    Region: stores a valid simple area as exterior and hole rings, convertible to Shapely
"""

from collections.abc import Mapping
from functools import cached_property
from typing import Annotated, Any, Self

import numpy as np
import shapely
from numpy.typing import NDArray
from pydantic import AfterValidator, BaseModel, ConfigDict, Field, ValidationError, model_validator
from pydantic.config import ExtraValues

from showpy.core.errors import schema_error_from
from showpy.core.json_types import FrozenJsonValue as FrozenJsonValue
from showpy.core.json_types import JsonValue as JsonValue
from showpy.core.numbers import normalize_angle
from showpy.core.readonly import FrozenJson

MIN_RING_POINTS = 3

type Finite = Annotated[float, Field(allow_inf_nan=False)]
type NonNegFinite = Annotated[float, Field(ge=0, allow_inf_nan=False)]
type PositiveFinite = Annotated[float, Field(gt=0, allow_inf_nan=False)]
type Meters = Annotated[float, Field(allow_inf_nan=False)]
type Seconds = Annotated[float, Field(allow_inf_nan=False)]
type Count = Annotated[float, Field(allow_inf_nan=False)]
type Degrees = Annotated[float, Field(allow_inf_nan=False)]
type NonNegCount = Annotated[float, Field(ge=0, allow_inf_nan=False)]
type NonNegSeconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]
type PositiveMeters = Annotated[float, Field(gt=0, allow_inf_nan=False)]
type CountsPerMinute = Annotated[float, Field(gt=0, allow_inf_nan=False)]
type Heading = Annotated[float, Field(allow_inf_nan=False), AfterValidator(normalize_angle)]
# Plain JSON in the static type lets callers pass dicts and lists; validation always stores the
# frozen form, so values read back are tuples and read-only mappings.
type JsonObject = Annotated[Mapping[str, JsonValue | FrozenJsonValue], FrozenJson()]
type Point = tuple[float, float]
type Point3 = tuple[float, float, float]
type FinitePoint = tuple[Finite, Finite]
type Ring = tuple[Point, ...]
type Points = NDArray[np.float64]
type Transform = NDArray[np.float64]
type Affine2D = NDArray[np.float64]
type Polygon = shapely.Polygon


class FrozenModel(BaseModel):
    """Provides the immutable, extra-forbidding base every data class inherits.

    Construction and validation raise SchemaError, chained from the pydantic error.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    # Any: pydantic's own keyword signature, forwarded unchanged.
    def __init__(self, /, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise schema_error_from(exc, model=type(self).__name__) from exc

    # Makes pydantic validate nested models through its core validator, so only the outermost
    # call converts and the error keeps the outer model name and dotted locs.
    __init__.__pydantic_base_init__ = True  # type: ignore[attr-defined]

    @classmethod
    def model_validate(
        cls,
        obj: object,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        from_attributes: bool | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Validates an object into this model, raising SchemaError on bad data."""
        try:
            return super().model_validate(
                obj,
                strict=strict,
                extra=extra,
                from_attributes=from_attributes,
                context=context,
                by_alias=by_alias,
                by_name=by_name,
            )
        except ValidationError as exc:
            raise schema_error_from(exc, model=cls.__name__) from exc

    @classmethod
    def model_validate_json(
        cls,
        json_data: str | bytes | bytearray,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Validates JSON text into this model, raising SchemaError on bad data."""
        try:
            return super().model_validate_json(
                json_data,
                strict=strict,
                extra=extra,
                context=context,
                by_alias=by_alias,
                by_name=by_name,
            )
        except ValidationError as exc:
            raise schema_error_from(exc, model=cls.__name__) from exc

    @classmethod
    def model_validate_strings(
        cls,
        obj: object,
        *,
        strict: bool | None = None,
        extra: ExtraValues | None = None,
        context: object | None = None,
        by_alias: bool | None = None,
        by_name: bool | None = None,
    ) -> Self:
        """Validates string-leaved data into this model, raising SchemaError on bad data."""
        try:
            return super().model_validate_strings(
                obj,
                strict=strict,
                extra=extra,
                context=context,
                by_alias=by_alias,
                by_name=by_name,
            )
        except ValidationError as exc:
            raise schema_error_from(exc, model=cls.__name__) from exc

    def model_copy(self, *, update: Mapping[str, object] | None = None, deep: bool = False) -> Self:
        """Returns a copy whose `cached_property` values are dropped, so none goes stale.

        Like pydantic's, `update` is not validated; edit with `replace` to revalidate.

        >>> from showpy.core.types import Box2
        >>> Box2(min=(0.0, 0.0), max=(1.0, 1.0)).model_copy().max
        (1.0, 1.0)
        """
        duplicate = super().model_copy(update=update, deep=deep)
        for owner in type(self).__mro__:
            for name, attribute in vars(owner).items():
                if isinstance(attribute, cached_property):
                    vars(duplicate).pop(name, None)
        return duplicate

    def replace(self, **changes: object) -> Self:
        """Returns a copy with `changes` applied, run through every validator.

        Unlike `model_copy(update=)`, a result that breaks a field or model rule raises
        SchemaError, as does an unknown field name. Nested models are not revalidated.

        >>> from showpy.core.types import Box2
        >>> Box2(min=(0.0, 0.0), max=(1.0, 1.0)).replace(max=(2.0, 2.0)).max
        (2.0, 2.0)

        Args:
            changes: New values keyed by field name.

        Returns:
            edited_model: A new instance of the same class holding the merged fields.
        """
        current = {name: getattr(self, name) for name in type(self).model_fields}
        return type(self).model_validate(current | changes)


class Box2(FrozenModel):
    """Bounds a set of stage-plane points.

    Attributes:
        min: Lower-left corner.
        max: Upper-right corner.
    """

    min: Point
    max: Point

    @property
    def size(self) -> tuple[float, float]:
        """Returns the width and depth of the box."""
        return self.max[0] - self.min[0], self.max[1] - self.min[1]

    @property
    def center(self) -> Point:
        """Returns the middle of the box."""
        return (self.min[0] + self.max[0]) / 2, (self.min[1] + self.max[1]) / 2


class Box3(FrozenModel):
    """Bounds a set of world points.

    Attributes:
        min: Lowest corner.
        max: Highest corner.
    """

    min: Point3
    max: Point3


class Region(FrozenModel):
    """Stores an area on the stage plane as rings, so it survives JSON round-trips.

    Attributes:
        exterior: Outer ring, not repeating the first point.
        holes: Inner rings cut out of the area.
    """

    exterior: Ring
    holes: tuple[Ring, ...] = ()

    @model_validator(mode="after")
    def _require_simple_ring(self) -> Self:
        if len(set(self.exterior)) < MIN_RING_POINTS:
            raise ValueError("a region needs at least 3 distinct exterior points")
        if not shapely.is_valid(self.to_shapely()):
            raise ValueError("a region must be a simple ring: no self-crossing, holes inside")
        return self

    @classmethod
    def from_shapely(cls, polygon: shapely.Polygon) -> Self:
        """Creates a Region from a Shapely polygon."""
        return cls(
            exterior=_ring(polygon.exterior), holes=tuple(_ring(h) for h in polygon.interiors)
        )

    def to_shapely(self) -> shapely.Polygon:
        """Returns the region as a Shapely polygon."""
        return shapely.Polygon(self.exterior, [list(hole) for hole in self.holes])


def _ring(ring: shapely.LinearRing) -> Ring:
    coords = [(float(x), float(y)) for x, y in ring.coords]
    return tuple(coords[:-1]) if len(coords) > 1 and coords[0] == coords[-1] else tuple(coords)
