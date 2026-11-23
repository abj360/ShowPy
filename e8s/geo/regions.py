"""
regions.py --- areas on the stage plane: building them from outlines and combining them

Contains:
    Area: names a Shapely Polygon or MultiPolygon
    region(): builds a valid polygon from a closed curve or points
    buffer(): grows or shrinks an area
    union(): merges areas
    difference(): cuts one area out of another
    intersection(): keeps the overlap of two areas
"""

import shapely
import shapely.validation

from e8s.core.errors import GeometryError
from e8s.core.util import as_points
from e8s.geo.curve_base import Curve

REGION_TOL = 0.001
MIN_RING_POINTS = 3

type Area = shapely.Polygon | shapely.MultiPolygon


def region(outline: Curve | object) -> shapely.Polygon:
    """Builds a valid polygon from a closed curve or a ring of points.

    >>> region([(0, 0), (2, 0), (2, 2), (0, 2)]).area
    4.0
    """
    pts = outline.to_polyline(REGION_TOL) if isinstance(outline, Curve) else as_points(outline)
    if len(pts) < MIN_RING_POINTS:
        raise GeometryError(
            "a region needs at least 3 points",
            hint="pass a closed curve or three or more points",
            context={"points": len(pts)},
        )
    polygon = shapely.Polygon(pts)
    if not polygon.is_valid:
        reason = shapely.validation.explain_validity(polygon)
        raise GeometryError(reason, hint="remove crossing edges from the outline")
    return polygon


def _as_area(geometry: shapely.Geometry) -> Area:
    if isinstance(geometry, shapely.Polygon | shapely.MultiPolygon):
        return geometry
    polygons = [g for g in getattr(geometry, "geoms", []) if isinstance(g, shapely.Polygon)]
    return shapely.MultiPolygon(polygons) if polygons else shapely.Polygon()


def buffer(area: Area, d: float) -> Area:
    """Grows an area by `d` meters, or shrinks it when `d` is negative.

    >>> round(buffer(region([(0, 0), (1, 0), (1, 1), (0, 1)]), -0.25).area, 6)
    0.25
    """
    return _as_area(area.buffer(d))


def union(*areas: Area) -> Area:
    """Merges areas into one.

    >>> union(region([(0, 0), (1, 0), (1, 1)]), region([(5, 5), (6, 5), (6, 6)])).geom_type
    'MultiPolygon'
    """
    return _as_area(shapely.union_all(list(areas)))


def difference(a: Area, b: Area) -> Area:
    """Returns the part of `a` outside `b`.

    >>> square = region([(0, 0), (2, 0), (2, 2), (0, 2)])
    >>> difference(square, region([(0, 0), (1, 0), (1, 2), (0, 2)])).area
    2.0
    """
    return _as_area(a.difference(b))


def intersection(a: Area, b: Area) -> Area:
    """Returns the overlap of `a` and `b`.

    >>> square = region([(0, 0), (2, 0), (2, 2), (0, 2)])
    >>> intersection(square, region([(1, 0), (3, 0), (3, 2), (1, 2)])).area
    2.0
    """
    return _as_area(a.intersection(b))
