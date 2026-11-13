#!/usr/bin/env python3

"""
snapping.py --- snaps points to a grid or guide lines, lines them up and evens them out

Re-exported by `showpy.geo.transforms`; the same input kinds and set rules apply.

Contains:
    Axis: names a stage-plane axis
    snap(): snaps points to a grid and reports what moved
    snap_to_guides(): pulls points onto nearby sheet guide lines
    align(): lines points up on one coordinate
    distribute(): evens points out along an axis, keeping their order
"""

from collections.abc import Callable, Sequence
from typing import Literal

import numpy as np
import numpy.typing as npt
import shapely

from showpy.choreo.models import Set
from showpy.core.errors import GeometryError
from showpy.core.types import Meters, Points
from showpy.geo.curves import CurveModel
from showpy.geo.measure import ALL, SetSelector
from showpy.geo.models import SnapReport
from showpy.geo.transform_base import PointMap, apply_pointwise, pick
from showpy.sheet.models import GuideLine, Sheet

type Axis = Literal["x", "y"]

AXIS_INDEX = {"x": 0, "y": 1}
SNAP_AXES = {"x": (0,), "y": (1,), "xy": (0, 1)}
ALIGN_STATS: dict[str, Callable[[Points], float]] = {
    "mean": lambda v: float(v.mean()),
    "min": lambda v: float(v.min()),
    "max": lambda v: float(v.max()),
}
GUIDE_KINDS = ("yard", "hash", "sideline", "center", "edge", "spike", "custom")
SNAP_EPS = 1e-9
MOVE_TOL = 1e-12


def _grid_steps(values: Points, mode: str) -> Points:
    rounding: dict[str, Callable[[Points], Points]] = {
        "round": lambda v: np.sign(v) * np.floor(np.abs(v) + 0.5 + SNAP_EPS),
        "floor": lambda v: np.floor(v + SNAP_EPS),
        "ceil": lambda v: np.ceil(v - SNAP_EPS),
    }
    return pick("snap mode", mode, rounding)(values)


def snap[T: (Points, CurveModel, Set)](
    obj: T,
    precision: Meters,
    mode: Literal["round", "floor", "ceil"] = "round",
    axes: Literal["x", "y", "xy"] = "xy",
    sel: SetSelector = ALL,
    force: bool = False,
) -> tuple[T, SnapReport]:
    """Snaps points to multiples of `precision`; rounding is half away from zero.

    >>> pts, report = snap(np.array([[0.26, 1.0]]), 0.5)
    >>> pts.tolist(), report.moved
    ([[0.5, 1.0]], 1)
    """
    if precision <= 0:
        raise GeometryError(
            f"snap precision must be above 0, got {precision}", hint="pass a positive grid step"
        )
    columns = list(pick("snap axes", axes, SNAP_AXES))
    shifts: list[Points] = []

    def to_grid(pts: Points) -> Points:
        snapped = pts.copy()
        snapped[:, columns] = _grid_steps(pts[:, columns] / precision, mode) * precision
        shifts.append(np.linalg.norm(snapped - pts, axis=1))
        return snapped

    result = apply_pointwise(obj, to_grid, sel, force)
    moved = np.concatenate([np.zeros(0), *shifts])
    report = SnapReport(
        moved=int((moved > MOVE_TOL).sum()), max_shift=float(moved.max(initial=0.0))
    )
    return result, report


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
    force: bool = False,
) -> T:
    """Projects each point onto the nearest guide line within `tol`; others stay put.

    >>> from showpy.core.types import Region
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
    return apply_pointwise(obj, _onto_guides(_guides(sheet, kinds), tol), sel, force)


def _align_target(values: Points, value: float | str) -> float:
    if isinstance(value, str):
        return pick("align value", value, ALIGN_STATS)(values)
    return float(value)


def align[T: (Points, CurveModel, Set)](
    obj: T,
    axis: Axis,
    value: float | Literal["mean", "min", "max"],
    sel: SetSelector = ALL,
    force: bool = False,
) -> T:
    """Sets one coordinate of every point to a number or to the points' mean, min or max.

    >>> align(np.array([[0.0, 1.0], [2.0, 3.0]]), "y", "min").tolist()
    [[0.0, 1.0], [2.0, 1.0]]
    """
    column = pick("axis", axis, AXIS_INDEX)

    def line_up(pts: Points) -> Points:
        if not len(pts):
            return pts
        lined = pts.copy()
        lined[:, column] = _align_target(pts[:, column], value)
        return lined

    return apply_pointwise(obj, line_up, sel, force)


def distribute[S: (Points, Set)](
    obj: S,
    axis: Axis,
    start: float | None = None,
    end: float | None = None,
    sel: SetSelector = ALL,
    force: bool = False,
) -> S:
    """Evens points out along an axis from `start` to `end`, keeping their order on it.

    `start` and `end` default to the current extremes; ties keep their input order.

    >>> distribute(np.array([[4.0, 0.0], [0.0, 0.0], [1.0, 0.0]]), "x").tolist()
    [[4.0, 0.0], [0.0, 0.0], [2.0, 0.0]]
    """
    column = pick("axis", axis, AXIS_INDEX)

    def even_out(pts: Points) -> Points:
        if not len(pts):
            return pts
        values = pts[:, column]
        lo = float(values.min()) if start is None else start
        hi = float(values.max()) if end is None else end
        spread = pts.copy()
        spread[np.argsort(values, kind="stable"), column] = np.linspace(lo, hi, len(pts))
        return spread

    return apply_pointwise(obj, even_out, sel, force)
