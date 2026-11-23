"""
set_snapping.py --- snaps points, curves and set selections to a grid or guides, aligns them

Takes `Points`, a curve or a `Set`, like `e8s.choreo.set_transforms`; on a set only the
selected on-field placements move and pinned placements raise LockedError unless force=True.

Contains:
    snap(): snaps to a grid and reports what moved
    snap_to_guides(): pulls onto nearby sheet guide lines
    align(): lines up on one coordinate
    distribute(): evens out along an axis, keeping order
"""

from collections.abc import Sequence
from typing import Literal

import numpy as np
import numpy.typing as npt
import shapely

from e8s.choreo.models import Set
from e8s.choreo.set_transforms import apply_pointwise
from e8s.core.errors import GeometryError
from e8s.core.types import Meters, Points
from e8s.geo import snapping
from e8s.geo.checks import pick
from e8s.geo.curves import CurveModel
from e8s.geo.measure import ALL, SetSelector
from e8s.geo.models import SnapReport
from e8s.geo.snapping import Axis
from e8s.geo.transform_base import PointMap
from e8s.sheet.models import GuideLine, Sheet

GUIDE_KINDS = ("yard", "hash", "sideline", "center", "edge", "spike", "custom")


def snap[T: (Points, CurveModel, Set)](
    obj: T,
    precision: Meters,
    mode: Literal["round", "floor", "ceil"] = "round",
    axes: Literal["x", "y", "xy"] = "xy",
    sel: SetSelector = ALL,
    *,
    force: bool = False,
) -> tuple[T, SnapReport]:
    """Snaps points to multiples of `precision`; rounding is half away from zero.

    >>> pts, report = snap(np.array([[0.26, 1.0]]), 0.5)
    >>> pts.tolist(), report.moved
    ([[0.5, 1.0]], 1)
    """
    to_grid, read_report = snapping.grid_snapper(precision, mode, axes)
    result = apply_pointwise(obj, to_grid, sel, force=force)
    return result, read_report()


def _guides(sheet: Sheet, kinds: Sequence[str] | None) -> list[GuideLine]:
    for kind in kinds or ():
        pick("guide kind", kind, dict.fromkeys(GUIDE_KINDS))
    return [g for g in sheet.guides if kinds is None or g.kind in kinds]


def _guide_geometry(guide: GuideLine) -> shapely.Geometry:
    return (
        shapely.LineString(guide.points) if len(guide.points) > 1 else shapely.Point(guide.points)
    )


def _onto_guides(guides: Sequence[GuideLine], tol: Meters) -> PointMap:
    shapes = np.array([_guide_geometry(g) for g in guides], dtype=object)

    def pull(pts: Points) -> Points:
        if not len(shapes) or not len(pts):
            return pts
        targets: npt.NDArray[np.object_] = np.asarray(shapely.points(pts), dtype=object)
        gaps = shapely.distance(shapes[:, None], targets[None, :])
        best = np.argmin(gaps, axis=0)
        close = gaps[best, np.arange(len(pts))] <= tol
        moved = pts.copy()
        links = shapely.shortest_line(shapes[best[close]], targets[close])
        moved[close] = shapely.get_coordinates(links)[::2]
        return moved

    return pull


def snap_to_guides[T: (Points, CurveModel, Set)](
    obj: T,
    sheet: Sheet,
    tol: Meters,
    kinds: Sequence[str] | None = None,
    sel: SetSelector = ALL,
    *,
    force: bool = False,
) -> T:
    """Projects each point onto the nearest guide line within `tol`; others stay put.

    >>> from e8s.core.types import Region
    >>> ring = ((-5.0, 0.0), (5.0, 0.0), (5.0, 9.0), (-5.0, 9.0))
    >>> sheet = Sheet(
    ...     ref="sheet:demo@1", name="Demo", surface_type="blank", size_m=(10.0, 9.0),
    ...     outline=ring, walkable=Region(exterior=ring),
    ...     guides=(GuideLine(id="g", kind="yard", points=((0, 0), (0, 9))),),
    ... )
    >>> snap_to_guides(np.array([[0.2, 3.0]]), sheet, tol=0.5).tolist()
    [[0.0, 3.0]]
    """
    if tol < 0:
        raise GeometryError(f"tolerance must be >= 0, got {tol}", hint="pass tol in meters")
    return apply_pointwise(obj, _onto_guides(_guides(sheet, kinds), tol), sel, force=force)


def align[T: (Points, CurveModel, Set)](
    obj: T,
    axis: Axis,
    value: float | Literal["mean", "min", "max"],
    sel: SetSelector = ALL,
    *,
    force: bool = False,
) -> T:
    """Sets one coordinate of every point to a number or to the points' mean, min or max.

    >>> align(np.array([[0.0, 1.0], [2.0, 3.0]]), "y", "min").tolist()
    [[0.0, 1.0], [2.0, 1.0]]
    """
    return apply_pointwise(obj, snapping.align_map(axis, value), sel, force=force)


def distribute[S: (Points, Set)](
    obj: S,
    axis: Axis,
    start: float | None = None,
    end: float | None = None,
    sel: SetSelector = ALL,
    *,
    force: bool = False,
) -> S:
    """Evens points out along an axis from `start` to `end`, keeping their order on it.

    `start` and `end` default to the current extremes; ties keep their input order.

    >>> distribute(np.array([[4.0, 0.0], [0.0, 0.0], [1.0, 0.0]]), "x").tolist()
    [[4.0, 0.0], [0.0, 0.0], [2.0, 0.0]]
    """
    return apply_pointwise(obj, snapping.distribute_map(axis, start, end), sel, force=force)
