"""
paths.py --- pure editing functions for a transition's path rows

Every function returns a new TransitionSpec and raises LockedError on a locked row unless
force=True. `sel` is a list of spot IDs or a Selection; resolve selection expressions first
with `showpy.cast.select.select`.

Contains:
    default_paths(): returns straight moves over the full counts for everyone
    set_style(): changes path style with style-specific parameters
    delay(): starts moves later
    hold(): holds for part or all of the transition
    arrive_early(): finishes moves early
    window(): sets both ends of the move
    add_waypoint(): bends a path through a point
    remove_waypoint(): removes one bend
    clear_waypoints(): removes every bend
    curve_path(): makes a smooth path through control points
    stagger(): offsets starts step by step along an order
    ripple(): spreads starts over a total number of counts
    set_facing(): sets facing while moving
    set_pace(): sets the step pace multiplier
    custom_path(): uses a registered custom motion
    set_easing(): changes pacing within the move
    lock(): locks rows against edits
    unlock(): unlocks rows
    paths_table(): returns non-default rows as a DataFrame
"""

from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any, Literal

import numpy as np
import pandas as pd

from showpy.choreo.models import (
    Easing,
    FacingMode,
    PathRow,
    PathStyle,
    TransitionSpec,
    TurnDirection,
    Waypoint,
)
from showpy.choreo.motion import DEFAULT_MOTIONS, MotionRegistry, ease
from showpy.core.errors import LockedError, PathError
from showpy.core.selection import Selection
from showpy.core.types import Degrees, Point

if TYPE_CHECKING:
    from showpy.show.models import Show

type Sel = Sequence[str] | Selection

REQUIRED_PARAMS: dict[PathStyle, tuple[str, ...]] = {
    PathStyle.ROTATE: ("pivot", "sweep_deg"),
    PathStyle.FOLLOW: ("route",),
    PathStyle.CUSTOM: ("motion_fn",),
}
STYLE_PARAMS = {"waypoints", "route", "pivot", "sweep_deg", "motion_fn", "motion_params"}
WAYPOINT_STYLES = (PathStyle.POLYLINE, PathStyle.CURVE)
TABLE_FIELDS = ("entity", "style", "start", "end", "facing_mode", "turn_counts", "pace", "locked")


def _edit(
    spec: TransitionSpec,
    sel: Sel,
    change: Callable[[PathRow], dict[str, Any]],
    *,
    force: bool,
) -> TransitionSpec:
    rows = dict(spec.rows)
    for entity in sel:
        row = spec.row(entity)
        if row.locked and not force:
            raise LockedError(
                f"path of {entity} in {spec.id[0]}→{spec.id[1]} is locked",
                hint="unlock it first, or pass force=True as the designer",
                context={"entity": entity},
            )
        updated = PathRow.model_validate({**row.model_dump(), **change(row)})
        _require_fits(updated, spec.counts)
        rows[updated.entity] = updated
    return spec.replace(rows=rows)


def _require_fits(row: PathRow, counts: float) -> None:
    start, end = row.window(counts)
    if end > counts or start >= end:
        raise PathError(
            f"window {start}–{end} of {row.entity} does not fit {counts} counts",
            hint="keep 0 <= start < end <= counts",
            context={"entity": row.entity, "start": start, "end": end},
        )


def default_paths(show: "Show", tid: tuple[str, str]) -> TransitionSpec:
    """Returns straight moves over the full counts for everyone (no stored rows).

    >>> from showpy.examples import two_set_show
    >>> default_paths(two_set_show(), ("1", "2")).rows
    {}
    """
    return TransitionSpec.model_validate({"id": tid, "counts": show.set(tid[1]).counts})


# Any: style parameters are PathRow field values whose types depend on the style.
def set_style(
    spec: TransitionSpec, sel: Sel, style: PathStyle, *, force: bool = False, **params: Any
) -> TransitionSpec:
    """Changes path style; raises PathError when a style's required parameters are missing.

    >>> spec = TransitionSpec(id=("1", "2"), counts=8.0)
    >>> set_style(spec, ["A"], PathStyle.HOLD).row("A").style.value
    'hold'
    """
    unknown = set(params) - STYLE_PARAMS
    if unknown:
        raise PathError(
            f"unknown path params {sorted(unknown)}", hint=f"use {sorted(STYLE_PARAMS)}"
        )
    missing = [p for p in REQUIRED_PARAMS.get(style, ()) if params.get(p) is None]
    if missing:
        raise PathError(
            f"{style.value} paths need {', '.join(missing)}",
            hint=f"pass {', '.join(missing)}",
            context={"style": style.value},
        )
    return _edit(spec, sel, lambda _row: {"style": style, **params}, force=force)


def delay(
    spec: TransitionSpec, sel: Sel, counts: float, *, keep_end: bool = True, force: bool = False
) -> TransitionSpec:
    """Starts moves `counts` later; with keep_end the move gets faster, otherwise it shifts.

    >>> delay(TransitionSpec(id=("1", "2"), counts=8.0), ["A"], 2.0).row("A").start
    2.0
    """

    def change(row: PathRow) -> dict[str, Any]:
        start, end = row.window(spec.counts)
        return {"start": start + counts, "end": row.end if keep_end else end + counts}

    return _edit(spec, sel, change, force=force)


def hold(
    spec: TransitionSpec,
    sel: Sel,
    until: float | None = None,
    from_: float | None = None,
    *,
    force: bool = False,
) -> TransitionSpec:
    """Holds until a local count, from a local count, or (neither given) the whole transition.

    >>> hold(TransitionSpec(id=("1", "2"), counts=8.0), ["A"], until=3.0).row("A").start
    3.0
    """
    if until is None and from_ is None:
        return _edit(spec, sel, lambda _row: {"style": PathStyle.HOLD}, force=force)
    change: dict[str, Any] = {}
    if until is not None:
        change["start"] = until
    if from_ is not None:
        change["end"] = from_
    return _edit(spec, sel, lambda _row: change, force=force)


def arrive_early(
    spec: TransitionSpec, sel: Sel, counts: float, *, force: bool = False
) -> TransitionSpec:
    """Finishes moves `counts` before the end of their window.

    >>> arrive_early(TransitionSpec(id=("1", "2"), counts=8.0), ["A"], 2.0).row("A").end
    6.0
    """
    return _edit(spec, sel, lambda row: {"end": row.window(spec.counts)[1] - counts}, force=force)


def window(
    spec: TransitionSpec, sel: Sel, start: float, end: float, *, force: bool = False
) -> TransitionSpec:
    """Sets both ends of the move in local counts.

    >>> window(TransitionSpec(id=("1", "2"), counts=8.0), ["A"], 1.0, 5.0).row("A").window(8.0)
    (1.0, 5.0)
    """
    return _edit(spec, sel, lambda _row: {"start": start, "end": end}, force=force)


def add_waypoint(
    spec: TransitionSpec,
    entity: str,
    point: Point,
    at: float | None = None,
    index: int | None = None,
    *,
    force: bool = False,
) -> TransitionSpec:
    """Bends a path through `point`, optionally reached at local count `at`.

    A STRAIGHT row becomes POLYLINE; CURVE rows stay smooth.

    >>> spec = add_waypoint(TransitionSpec(id=("1", "2"), counts=8.0), "A", (1.0, 1.0))
    >>> spec.row("A").style.value
    'polyline'
    """

    def change(row: PathRow) -> dict[str, Any]:
        waypoints = list(row.waypoints)
        waypoints.insert(
            len(waypoints) if index is None else index, Waypoint(x=point[0], y=point[1], at=at)
        )
        style = row.style if row.style in WAYPOINT_STYLES else PathStyle.POLYLINE
        return {"waypoints": tuple(waypoints), "style": style}

    return _edit(spec, [entity], change, force=force)


def remove_waypoint(
    spec: TransitionSpec, entity: str, index: int, *, force: bool = False
) -> TransitionSpec:
    """Removes the waypoint at `index`.

    >>> spec = add_waypoint(TransitionSpec(id=("1", "2"), counts=8.0), "A", (1.0, 1.0))
    >>> remove_waypoint(spec, "A", 0).row("A").waypoints
    ()
    """
    waypoints = list(spec.row(entity).waypoints)
    if not 0 <= index < len(waypoints):
        raise PathError(
            f"{entity} has no waypoint {index}",
            hint=f"use an index below {len(waypoints)}",
            context={"entity": entity, "index": index},
        )
    del waypoints[index]
    return _edit(spec, [entity], lambda _row: {"waypoints": tuple(waypoints)}, force=force)


def clear_waypoints(spec: TransitionSpec, sel: Sel, *, force: bool = False) -> TransitionSpec:
    """Removes every waypoint; POLYLINE and CURVE rows go back to STRAIGHT.

    >>> spec = add_waypoint(TransitionSpec(id=("1", "2"), counts=8.0), "A", (1.0, 1.0))
    >>> clear_waypoints(spec, ["A"]).row("A").style.value
    'straight'
    """

    def change(row: PathRow) -> dict[str, Any]:
        style = PathStyle.STRAIGHT if row.style in WAYPOINT_STYLES else row.style
        return {"waypoints": (), "style": style}

    return _edit(spec, sel, change, force=force)


def curve_path(
    spec: TransitionSpec, entity: str, *control: Point, force: bool = False
) -> TransitionSpec:
    """Makes a smooth path through control points between the two placements.

    >>> curve_path(TransitionSpec(id=("1", "2"), counts=8.0), "A", (1, 1)).row("A").style.value
    'curve'
    """
    waypoints = tuple(Waypoint(x=p[0], y=p[1]) for p in control)
    return _edit(
        spec, [entity], lambda _row: {"style": PathStyle.CURVE, "waypoints": waypoints}, force=force
    )


def stagger(
    spec: TransitionSpec,
    ordered_sel: Sel,
    step: float = 1.0,
    direction: Literal["start", "end"] = "start",
    *,
    keep_end: bool = True,
    force: bool = False,
) -> TransitionSpec:
    """Delays the i-th spot by i·step counts; `end` staggers from the last spot instead.

    >>> spec = stagger(TransitionSpec(id=("1", "2"), counts=8.0), ["A", "B"], 2.0)
    >>> spec.row("B").start
    2.0
    """
    order = list(ordered_sel) if direction == "start" else list(ordered_sel)[::-1]
    for i, entity in enumerate(order):
        spec = delay(spec, [entity], i * step, keep_end=keep_end, force=force)
    return spec


RIPPLE_CURVES: dict[str, Easing] = {
    "linear": Easing(),
    "ease_in": Easing(kind="ease_in"),
    "ease_out": Easing(kind="ease_out"),
    "ease_in_out": Easing(kind="ease_in_out"),
}


def ripple(
    spec: TransitionSpec,
    ordered_sel: Sel,
    total: float,
    curve: Literal["linear", "ease_in", "ease_out", "ease_in_out"] = "linear",
    *,
    force: bool = False,
) -> TransitionSpec:
    """Spreads starts from 0 to `total` counts along an order, shaped by an easing curve.

    >>> spec = ripple(TransitionSpec(id=("1", "2"), counts=8.0), ["A", "B", "C"], 4.0)
    >>> [spec.row(e).start for e in "ABC"]
    [0.0, 2.0, 4.0]
    """
    order = list(ordered_sel)
    shares = np.linspace(0.0, 1.0, len(order)) if len(order) > 1 else np.zeros(1)
    starts = total * ease(RIPPLE_CURVES[curve], shares)
    for entity, start in zip(order, starts, strict=True):
        spec = _edit(spec, [entity], _setter("start", float(start)), force=force)
    return spec


def _setter(field: str, value: float) -> Callable[[PathRow], dict[str, Any]]:
    return lambda _row: {field: value}


def set_facing(
    spec: TransitionSpec,
    sel: Sel,
    mode: FacingMode,
    value: Degrees | Point | str | None = None,
    turn_counts: float = 0.0,
    direction: TurnDirection = TurnDirection.SHORTEST,
    *,
    force: bool = False,
) -> TransitionSpec:
    """Sets how spots face while they move.

    >>> spec = set_facing(TransitionSpec(id=("1", "2"), counts=8.0), ["A"], FacingMode.TRAVEL)
    >>> spec.row("A").facing_mode.value
    'travel'
    """
    change = {
        "facing_mode": mode,
        "facing_value": value,
        "turn_counts": turn_counts,
        "turn_direction": direction,
    }
    return _edit(spec, sel, lambda _row: change, force=force)


def set_pace(spec: TransitionSpec, sel: Sel, pace: float, *, force: bool = False) -> TransitionSpec:
    """Sets the step pace multiplier (2 = double time); positions do not change.

    >>> set_pace(TransitionSpec(id=("1", "2"), counts=8.0), ["A"], 0.5).row("A").pace
    0.5
    """
    return _edit(spec, sel, lambda _row: {"pace": pace}, force=force)


# Any: motion parameters are JSON values defined by each registered motion.
def custom_path(
    spec: TransitionSpec,
    sel: Sel,
    motion: str,
    registry: MotionRegistry = DEFAULT_MOTIONS,
    *,
    force: bool = False,
    **params: Any,
) -> TransitionSpec:
    """Uses a registered custom motion; raises UnknownRefError for unknown names.

    >>> custom_path(TransitionSpec(id=("1", "2"), counts=8.0), ["A"], "weave").row("A").motion_fn
    'weave'
    """
    registry.get(motion)
    change = {"style": PathStyle.CUSTOM, "motion_fn": motion, "motion_params": params}
    return _edit(spec, sel, lambda _row: change, force=force)


def set_easing(
    spec: TransitionSpec, sel: Sel, easing: Easing | str, *, force: bool = False
) -> TransitionSpec:
    """Changes pacing within the move; a kind name uses default strength.

    >>> spec = TransitionSpec(id=("1", "2"), counts=8.0)
    >>> set_easing(spec, ["A"], "ease_out").row("A").easing.kind
    'ease_out'
    """
    chosen = easing if isinstance(easing, Easing) else Easing.model_validate({"kind": easing})
    return _edit(spec, sel, lambda _row: {"easing": chosen}, force=force)


def lock(spec: TransitionSpec, sel: Sel) -> TransitionSpec:
    """Locks rows against edits.

    >>> lock(TransitionSpec(id=("1", "2"), counts=8.0), ["A"]).row("A").locked
    True
    """
    return _edit(spec, sel, lambda _row: {"locked": True}, force=True)


def unlock(spec: TransitionSpec, sel: Sel) -> TransitionSpec:
    """Unlocks rows.

    >>> unlock(lock(TransitionSpec(id=("1", "2"), counts=8.0), ["A"]), ["A"]).row("A").locked
    False
    """
    return _edit(spec, sel, lambda _row: {"locked": False}, force=True)


def paths_table(spec: TransitionSpec) -> pd.DataFrame:
    """Returns the rows that differ from a default straight move, one per spot.

    >>> paths_table(TransitionSpec(id=("1", "2"), counts=8.0)).empty
    True
    """
    rows = [
        {field: getattr(row, field) for field in TABLE_FIELDS}
        for entity, row in sorted(spec.rows.items())
        if row != PathRow(entity=entity)
    ]
    return pd.DataFrame(rows, columns=list(TABLE_FIELDS))
