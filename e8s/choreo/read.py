"""
read.py --- the floor readers: motion over time as arrays, all answered by PathEngine

Contains:
    frames(): returns every selected spot's position and facing at regular frame times
    coordinate_table(): returns selected spots' positions at sets, in meters and in steps
"""

import itertools
import math
from collections.abc import Sequence

import numpy as np

from e8s.cast.select import select
from e8s.choreo.engine import ALL, PathEngine
from e8s.choreo.engine_batch import group_counts, track_facings, track_positions
from e8s.choreo.read_models import CoordinateTable, Frames
from e8s.core.arrays import FloatArray
from e8s.core.caps import require_size
from e8s.core.errors import SchemaError, TimeRangeError
from e8s.core.ids import LandmarkId, SetId, SpotId
from e8s.core.selection import Selector
from e8s.core.types import Count
from e8s.core.units import convert, round_to
from e8s.core.validation import require_finite
from e8s.sheet.coords import axis_offsets, axis_origin
from e8s.show.models import Show
from e8s.time.clock import count_to_seconds, seconds_to_count
from e8s.time.models import Timeline

DEFAULT_FPS = 30.0
# Frame counts within this many frames of a whole number are whole: (s1 - s0) * fps carries
# float noise, and a range of exactly 4 s at 30 fps must keep its last frame.
FRAME_TOL = 1e-9
MAX_FRAME_VALUES = 20_000_000
# An 8-to-5 step: eight steps cover five yards.
STEPS_PER_FIVE_YARDS = 8
FIVE_YARDS_M = convert(5.0, "yd", "m")
STEP_M = FIVE_YARDS_M / STEPS_PER_FIVE_YARDS
RESOLUTIONS = (1.0, 0.5, 0.25, 0.125)
DEFAULT_RESOLUTION = 0.25


def _engine_for(show: Show, engine: PathEngine | None) -> PathEngine:
    if engine is None:
        return PathEngine(show)
    if engine.show is not show:
        raise SchemaError(
            "the engine was built for another show",
            hint="pass PathEngine(show) for this show, or no engine",
            context={"show": show.id, "engine_show": engine.show.id},
        )
    return engine


def _set_starts(show: Show) -> list[Count]:
    return list(itertools.accumulate((s.counts for s in show.sets[1:]), initial=0.0))


def _total_counts(show: Show) -> Count:
    return _set_starts(show)[-1]


def _require_range(show: Show, start: Count, end: Count) -> tuple[Count, Count]:
    first = require_finite(start, name="start")
    last = require_finite(end, name="end")
    total = _total_counts(show)
    if not 0.0 <= first <= last <= total:
        raise TimeRangeError(
            f"frame range {first:g}–{last:g} is not inside the show's 0–{total:g} counts",
            hint=f"use 0 <= start <= end <= {total:g}",
            context={"start": first, "end": last, "total": total},
        )
    return first, last


def _frame_times(
    timeline: Timeline, start: Count, end: Count, fps: float, spots: int
) -> tuple[FloatArray, FloatArray]:
    s0, s1 = count_to_seconds(timeline, start), count_to_seconds(timeline, end)
    n = math.floor((s1 - s0) * fps + FRAME_TOL) + 1
    require_size(n * max(spots, 1), MAX_FRAME_VALUES, what="frame values")
    seconds = np.minimum(s0 + np.arange(n) / fps, s1)
    counts = np.array([seconds_to_count(timeline, s) for s in seconds.tolist()], dtype=float)
    return seconds, np.clip(counts, start, end)


def _spot_ids(show: Show, who: Selector) -> tuple[SpotId, ...]:
    return select(show, who).ids


def frames(
    show: Show,
    start: Count,
    end: Count,
    fps: float = DEFAULT_FPS,
    who: Selector = ALL,
    *,
    engine: PathEngine | None = None,
) -> Frames:
    """Returns every selected spot's position and facing at regular frame times.

    Frames fall every 1/fps seconds of show time from `start` up to `end`, holds included:
    during a hold the count, and so every spot, stays still. Each frame equals
    `PathEngine.sample` at its count, exactly.

    Args:
        show: Show to read.
        start: Global count of the first frame.
        end: Global count the frames stop at; the last frame is on or before it.
        fps: Frames per second.
        who: Spots to read, as a selection expression, a list of IDs or a Selection.
        engine: A PathEngine already built for `show`, reused so repeated reads skip compiling.

    Returns:
        frames: Frame times, counts and (frames, spots) arrays of x, y and facing.

    Raises:
        TimeRangeError: The range is not inside the show's counts.
        SchemaError: `fps` is not above 0, the frames would pass MAX_FRAME_VALUES values, or
            `engine` belongs to another show.

    >>> from e8s.examples import two_set_show
    >>> moving = frames(two_set_show(), 0.0, 8.0, fps=1.0, who="T1")
    >>> moving.counts.tolist()
    [0.0, 2.0, 4.0, 6.0, 8.0]
    >>> moving.y[:, 0].tolist()
    [5.0, 6.0, 7.0, 8.0, 9.0]
    """
    first, last = _require_range(show, start, end)
    rate = require_finite(fps, name="fps", low=0.0, low_open=True)
    reader = _engine_for(show, engine)
    spots = _spot_ids(show, who)
    seconds, counts = _frame_times(show.timeline, first, last, rate, len(spots))
    groups = group_counts(reader, counts)
    x, y, facing = (np.empty((len(counts), len(spots))) for _ in range(3))
    for column, spot in enumerate(spots):
        points = track_positions(reader, spot, groups)
        x[:, column], y[:, column] = points[:, 0], points[:, 1]
        facing[:, column] = track_facings(reader, spot, groups)
    return Frames(spots=spots, fps=rate, seconds=seconds, counts=counts, x=x, y=y, facing=facing)


def _require_resolution(resolution: float) -> float:
    if resolution in RESOLUTIONS:
        return float(resolution)
    raise SchemaError(
        f"resolution {resolution!r} is not a step size coordinates round to",
        hint=f"use one of {', '.join(f'{r:g}' for r in RESOLUTIONS)} steps",
        context={"resolution": repr(resolution)},
    )


def _set_ids(show: Show, sets: Sequence[str] | None) -> tuple[SetId, ...]:
    if sets is None:
        return tuple(s.id for s in show.sets)
    if not sets:
        raise SchemaError(
            "no sets were named for the coordinate table",
            hint="name at least one set, or pass sets=None for every set",
            context={"sets": []},
        )
    return tuple(show.sets[show.set_index(set_id)].id for set_id in sets)


def _set_positions(
    engine: PathEngine, spots: tuple[SpotId, ...], counts: FloatArray
) -> tuple[FloatArray, FloatArray]:
    groups = group_counts(engine, counts)
    x, y = np.empty((len(counts), len(spots))), np.empty((len(counts), len(spots)))
    for column, spot in enumerate(spots):
        points = track_positions(engine, spot, groups)
        x[:, column], y[:, column] = points[:, 0], points[:, 1]
    return x, y


def _rounded(steps: FloatArray, resolution: float) -> FloatArray:
    flat = [round_to(value, resolution) for value in steps.ravel().tolist()]
    return np.array(flat, dtype=float).reshape(steps.shape)


def coordinate_table(
    show: Show,
    who: Selector,
    sets: Sequence[str] | None = None,
    resolution: float = DEFAULT_RESOLUTION,
    *,
    engine: PathEngine | None = None,
) -> CoordinateTable:
    """Returns selected spots' positions at sets, in meters and in steps from the origin.

    Positions are PathEngine positions at each set's count. Steps are 8-to-5 steps (eight to
    five yards) from the show's coordinate origin, across toward the audience's right and away
    from the audience, rounded half away from zero to `resolution`.

    Args:
        show: Show to read.
        who: Spots to read, as a selection expression, a list of IDs or a Selection.
        sets: Set IDs in the order wanted; None reads every set in show order.
        resolution: Rounding in steps: 1, 0.5, 0.25 or 0.125.
        engine: A PathEngine already built for `show`, reused so repeated reads skip compiling.

    Returns:
        table: (sets, spots) arrays of meters, rounded and exact steps, and field states.

    Raises:
        UnknownRefError: A set or spot is not in the show.
        SchemaError: `resolution` is not one of the allowed steps, `sets` is empty, or
            `engine` belongs to another show.

    >>> from e8s.examples import two_set_show
    >>> table = coordinate_table(two_set_show(), "T2", resolution=0.5)
    >>> table.x[:, 0].tolist(), table.y[:, 0].tolist()
    ([2.0, 2.0], [5.0, 9.0])
    >>> table.across[:, 0].tolist(), table.depth[:, 0].tolist()
    ([3.5, 3.5], [8.5, 15.5])
    """
    rounding = _require_resolution(resolution)
    reader = _engine_for(show, engine)
    spots = _spot_ids(show, who)
    set_ids = _set_ids(show, sets)
    starts = _set_starts(show)
    counts = np.array([starts[show.set_index(set_id)] for set_id in set_ids], dtype=float)
    x, y = _set_positions(reader, spots, counts)
    system = show.coordinates
    across_m, depth_m = axis_offsets(show.sheet, system, np.column_stack([x.ravel(), y.ravel()]))
    across, depth = across_m.reshape(x.shape) / STEP_M, depth_m.reshape(x.shape) / STEP_M
    on_field = [[show.set(set_id).get(spot).on_field for spot in spots] for set_id in set_ids]
    return CoordinateTable(
        spots=spots,
        sets=set_ids,
        counts=counts,
        x=x,
        y=y,
        across=_rounded(across, rounding),
        depth=_rounded(depth, rounding),
        across_exact=across,
        depth_exact=depth,
        on_field=np.array(on_field, dtype=bool).reshape(x.shape),
        origin=axis_origin(show.sheet, system),
        origin_landmark=LandmarkId(system.origin) if isinstance(system.origin, str) else None,
        audience_side=system.audience_side,
        step=STEP_M,
        resolution=rounding,
    )
