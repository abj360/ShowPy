"""
types.py --- shared type aliases plus the JSON-safe Box2, Box3 and Region models

Contains:
    Finite, NonNegFinite, PositiveFinite: constrain a float to be finite, non-negative or positive
    Meters, Seconds, Count, Degrees: finite floats in the library's one unit system; meters stay
        within MAX_COORD_M and counts within MAX_COUNTS
    NonNegCount, NonNegSeconds, PositiveMeters: range-limited unit aliases
    CountsPerMinute: finite tempo alias, at least MIN_TEMPO
    Heading: wraps a stored facing into [0, 360)
    Point, Point3, Ring: coordinate tuples whose items are Meters
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
from dataclasses import dataclass
from functools import cached_property
from typing import Annotated, Any, Self

import numpy as np
import shapely
from numpy.typing import NDArray
from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    GetCoreSchemaHandler,
    GetJsonSchemaHandler,
    ValidationError,
    model_validator,
)
from pydantic.config import ExtraValues
from pydantic.json_schema import JsonSchemaValue
from pydantic_core import CoreSchema, core_schema

from e8s.core.caps import MAX_COORD_M, MAX_COUNTS, MIN_TEMPO
from e8s.core.errors import ALLOWED_KEY, BOUND_KEY, schema_error_from
from e8s.core.json_types import FrozenJsonValue as FrozenJsonValue
from e8s.core.json_types import JsonValue as JsonValue
from e8s.core.numbers import normalize_angle
from e8s.core.readonly import FrozenJson

MIN_RING_POINTS = 3
_BOUND_ERROR = "out_of_bounds"


@dataclass(frozen=True)
class _Within:
    """Holds a float alias within a named bound, checked by pydantic after the finite check.

    Attributes:
        bound: Name of the constant that sets the bound, reported in the error.
        allowed: What the alias allows, in words, reported in the error.
        ge: Smallest value allowed, or None for no lower bound.
        le: Largest value allowed, or None for no upper bound.
    """

    bound: str
    allowed: str
    ge: float | None = None
    le: float | None = None

    def __get_pydantic_core_schema__(
        self, source: object, handler: GetCoreSchemaHandler
    ) -> CoreSchema:
        within = core_schema.custom_error_schema(
            core_schema.float_schema(ge=self.ge, le=self.le),
            custom_error_type=_BOUND_ERROR,
            custom_error_message=f"Input should be {self.allowed} ({self.bound})",
            custom_error_context={BOUND_KEY: self.bound, ALLOWED_KEY: self.allowed},
        )
        return core_schema.chain_schema([handler(source), within])

    def __get_pydantic_json_schema__(
        self, schema: CoreSchema, handler: GetJsonSchemaHandler
    ) -> JsonSchemaValue:
        json_schema = handler(schema)
        if self.ge is not None:
            json_schema["minimum"] = self.ge
        if self.le is not None:
            json_schema["maximum"] = self.le
        return json_schema


_COORD_ALLOWED = f"between -{MAX_COORD_M:,.0f} and {MAX_COORD_M:,.0f} m"
_POSITIVE_COORD_ALLOWED = f"at most {MAX_COORD_M:,.0f} m"
_COUNT_ALLOWED = f"between -{MAX_COUNTS:,} and {MAX_COUNTS:,} counts"
_NON_NEG_COUNT_ALLOWED = f"at most {MAX_COUNTS:,} counts"
_TEMPO_ALLOWED = f"at least {MIN_TEMPO:g} counts per minute"
_COORD_BOUND = _Within("MAX_COORD_M", _COORD_ALLOWED, ge=-MAX_COORD_M, le=MAX_COORD_M)
_POSITIVE_COORD_BOUND = _Within("MAX_COORD_M", _POSITIVE_COORD_ALLOWED, le=MAX_COORD_M)
_COUNT_BOUND = _Within("MAX_COUNTS", _COUNT_ALLOWED, ge=-MAX_COUNTS, le=MAX_COUNTS)
_NON_NEG_COUNT_BOUND = _Within("MAX_COUNTS", _NON_NEG_COUNT_ALLOWED, le=MAX_COUNTS)
_TEMPO_BOUND = _Within("MIN_TEMPO", _TEMPO_ALLOWED, ge=MIN_TEMPO)

type Finite = Annotated[float, Field(allow_inf_nan=False)]
type NonNegFinite = Annotated[float, Field(ge=0, allow_inf_nan=False)]
type PositiveFinite = Annotated[float, Field(gt=0, allow_inf_nan=False)]
type Meters = Annotated[float, Field(allow_inf_nan=False), _COORD_BOUND]
type Seconds = Annotated[float, Field(allow_inf_nan=False)]
type Count = Annotated[float, Field(allow_inf_nan=False), _COUNT_BOUND]
type Degrees = Annotated[float, Field(allow_inf_nan=False)]
type NonNegCount = Annotated[float, Field(ge=0, allow_inf_nan=False), _NON_NEG_COUNT_BOUND]
type NonNegSeconds = Annotated[float, Field(ge=0, allow_inf_nan=False)]
type PositiveMeters = Annotated[float, Field(gt=0, allow_inf_nan=False), _POSITIVE_COORD_BOUND]
type CountsPerMinute = Annotated[float, Field(allow_inf_nan=False), _TEMPO_BOUND]
type Heading = Annotated[float, Field(allow_inf_nan=False), AfterValidator(normalize_angle)]
# Plain JSON in the static type lets callers pass dicts and lists; validation always stores the
# frozen form, so values read back are tuples and read-only mappings.
type JsonObject = Annotated[Mapping[str, JsonValue | FrozenJsonValue], FrozenJson()]
type Point = tuple[Meters, Meters]
type Point3 = tuple[Meters, Meters, Meters]
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
    vars(__init__)["__pydantic_base_init__"] = True

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

        >>> from e8s.core.types import Box2
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

        >>> from e8s.core.types import Box2
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
