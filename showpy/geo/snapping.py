"""
snapping.py --- snaps points to a grid, lines them up and evens them out

Re-exported by `showpy.geo.transforms`; the same input kinds apply. A `Set` goes to
`showpy.choreo.set_snapping`, which warns as deprecated. Snapping to sheet guide lines needs a
`Sheet` and lives in `showpy.choreo.set_snapping`.

Contains:
    Axis: names a stage-plane axis
    snap(): snaps points to a grid and reports what moved
    snap_to_guides(): forwards to showpy.choreo.set_snapping.snap_to_guides, deprecated
    align(): lines points up on one coordinate
    distribute(): evens points out along an axis, keeping their order
    grid_snapper(): builds the point map that snaps to a grid, and a reader of what moved
    align_map(): builds the point map that sets one coordinate
    distribute_map(): builds the point map that evens points out along an axis
"""

from collections.abc import Callable
from typing import Literal

import numpy as np

from showpy.core.deprecation import deprecated_call
from showpy.core.errors import GeometryError
from showpy.core.types import Meters, Points
from showpy.geo.checks import pick
from showpy.geo.curves import CurveModel
from showpy.geo.measure import ALL, SetLike, SetSelector
from showpy.geo.models import SnapReport
from showpy.geo.transform_base import PointMap, apply_pointwise, is_set_input, warn_set_moved

type Axis = Literal["x", "y"]

GEO_MODULE = "showpy.geo.snapping"
CHOREO_MODULE = "showpy.choreo.set_snapping"
AXIS_INDEX = {"x": 0, "y": 1}
SNAP_AXES = {"x": (0,), "y": (1,), "xy": (0, 1)}
ALIGN_STATS: dict[str, Callable[[Points], float]] = {
    "mean": lambda v: float(v.mean()),
    "min": lambda v: float(v.min()),
    "max": lambda v: float(v.max()),
}
SNAP_EPS = 1e-9
MOVE_TOL = 1e-12


def _grid_steps(values: Points, mode: str) -> Points:
    rounding: dict[str, Callable[[Points], Points]] = {
        "round": lambda v: np.sign(v) * np.floor(np.abs(v) + 0.5 + SNAP_EPS),
        "floor": lambda v: np.floor(v + SNAP_EPS),
        "ceil": lambda v: np.ceil(v - SNAP_EPS),
    }
    return pick("snap mode", mode, rounding)(values)


def grid_snapper(
    precision: Meters, mode: str, axes: str
) -> tuple[PointMap, Callable[[], SnapReport]]:
    """Builds the point map that snaps to multiples of `precision`, and a reader of its report.

    The reader summarizes every call of the map so far; call it after the map has run.

    >>> snapper, report = grid_snapper(0.5, "round", "xy")
    >>> snapper(np.array([[0.26, 1.0]])).tolist(), report().moved
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

    def read_report() -> SnapReport:
        moved = np.concatenate([np.zeros(0), *shifts])
        return SnapReport(
            moved=int((moved > MOVE_TOL).sum()), max_shift=float(moved.max(initial=0.0))
        )

    return to_grid, read_report


def snap[T: (Points, CurveModel)](
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
    if is_set_input(obj):
        from showpy.choreo.set_snapping import snap as twin

        warn_set_moved(GEO_MODULE, "snap", CHOREO_MODULE)
        return twin(obj, precision, mode, axes, sel, force=force)
    to_grid, read_report = grid_snapper(precision, mode, axes)
    return apply_pointwise(obj, to_grid), read_report()


@deprecated_call("showpy.choreo.set_snapping.snap_to_guides", since="0.4.0", remove="0.6.0")
def snap_to_guides(
    obj: SetLike,
    sheet: SetLike,
    tol: Meters,
    kinds: list[str] | None = None,
    sel: SetSelector = ALL,
    *,
    force: bool = False,
) -> SetLike:
    """Projects points onto sheet guide lines; moved to `showpy.choreo.set_snapping`."""
    from showpy.choreo.set_snapping import snap_to_guides as twin

    return twin(obj, sheet, tol, kinds, sel, force=force)


def _align_target(values: Points, value: float | str) -> float:
    if isinstance(value, str):
        return pick("align value", value, ALIGN_STATS)(values)
    return float(value)


def align_map(axis: Axis, value: float | Literal["mean", "min", "max"]) -> PointMap:
    """Builds the point map that sets one coordinate to a number or to the mean, min or max.

    >>> align_map("y", "min")(np.array([[0.0, 1.0], [2.0, 3.0]])).tolist()
    [[0.0, 1.0], [2.0, 1.0]]
    """
    column = pick("axis", axis, AXIS_INDEX)

    def line_up(pts: Points) -> Points:
        if not len(pts):
            return pts
        lined = pts.copy()
        lined[:, column] = _align_target(pts[:, column], value)
        return lined

    return line_up


def align[T: (Points, CurveModel)](
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
    if is_set_input(obj):
        from showpy.choreo.set_snapping import align as twin

        warn_set_moved(GEO_MODULE, "align", CHOREO_MODULE)
        return twin(obj, axis, value, sel, force=force)
    return apply_pointwise(obj, align_map(axis, value))


def distribute_map(axis: Axis, start: float | None, end: float | None) -> PointMap:
    """Builds the point map that evens points out along an axis, keeping their order on it.

    >>> distribute_map("x", None, None)(np.array([[4.0, 0.0], [0.0, 0.0], [1.0, 0.0]])).tolist()
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

    return even_out


def distribute(
    obj: Points,
    axis: Axis,
    start: float | None = None,
    end: float | None = None,
    sel: SetSelector = ALL,
    *,
    force: bool = False,
) -> Points:
    """Evens points out along an axis from `start` to `end`, keeping their order on it.

    `start` and `end` default to the current extremes; ties keep their input order.

    >>> distribute(np.array([[4.0, 0.0], [0.0, 0.0], [1.0, 0.0]]), "x").tolist()
    [[4.0, 0.0], [0.0, 0.0], [2.0, 0.0]]
    """
    if is_set_input(obj):
        from showpy.choreo.set_snapping import distribute as twin

        warn_set_moved(GEO_MODULE, "distribute", CHOREO_MODULE)
        return twin(obj, axis, start, end, sel, force=force)
    return apply_pointwise(obj, distribute_map(axis, start, end))
