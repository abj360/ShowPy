"""
types.py --- shared type aliases plus the JSON-safe Box2, Box3 and Region models

Contains:
    FrozenModel: provides the immutable, strict base for every data class
    Box2: bounds stage-plane points and reports size and center
    Box3: bounds world points
    Region: stores an area as exterior and hole rings, convertible to Shapely
"""

from typing import Self

import numpy as np
import shapely
from numpy.typing import NDArray
from pydantic import BaseModel, ConfigDict

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
    """Provides the immutable, extra-forbidding base every data class inherits."""

    model_config = ConfigDict(frozen=True, extra="forbid")


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
