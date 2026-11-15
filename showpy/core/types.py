"""
types.py --- shared type aliases plus the JSON-safe Box2, Box3 and Region models

Contains:
    FrozenModel: provides the immutable, strict base that raises SchemaError on bad data
    Box2: bounds stage-plane points and reports size and center
    Box3: bounds world points
    Region: stores an area as exterior and hole rings, convertible to Shapely
"""

from typing import Any, Self

import numpy as np
import shapely
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict, ValidationError

from showpy.core.errors import schema_error_from

type Meters = float
type Seconds = float
type Count = float
type Degrees = float
type CountsPerMinute = float
type Point = tuple[float, float]
type Point3 = tuple[float, float, float]
type Ring = tuple[Point, ...]
type Points = NDArray[np.float64]
type Transform = NDArray[np.float64]
type Affine2D = NDArray[np.float64]
type Polygon = shapely.Polygon


class FrozenModel(BaseModel):
    """Provides the immutable, extra-forbidding base every data class inherits.

    Construction and validation raise SchemaError, chained from the pydantic error.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Any: pydantic's own keyword signature, forwarded unchanged.
    def __init__(self, /, **data: Any) -> None:
        try:
            super().__init__(**data)
        except ValidationError as exc:
            raise schema_error_from(exc, model=type(self).__name__) from exc

    # Makes pydantic validate nested models through its core validator, so only the outermost
    # call converts and the error keeps the outer model name and dotted locs.
    __init__.__pydantic_base_init__ = True  # type: ignore[attr-defined]  # pyright: ignore[reportFunctionMemberAccess]

    @classmethod
    def model_validate(cls, obj: Any, **options: Any) -> Self:  # Any: pydantic passthrough
        """Validates an object into this model, raising SchemaError on bad data."""
        try:
            return super().model_validate(obj, **options)
        except ValidationError as exc:
            raise schema_error_from(exc, model=cls.__name__) from exc

    @classmethod
    def model_validate_json(cls, json_data: str | bytes | bytearray, **options: Any) -> Self:
        """Validates JSON text into this model, raising SchemaError on bad data."""
        try:
            return super().model_validate_json(json_data, **options)
        except ValidationError as exc:
            raise schema_error_from(exc, model=cls.__name__) from exc


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
