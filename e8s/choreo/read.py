"""
read.py --- the floor readers: motion over time as arrays, all answered by PathEngine

Contains:
    frames(): returns every selected spot's position and facing at regular frame times
    coordinate_table(): returns selected spots' positions at each set, in meters or a unit
    describe_transition(): describes each selected spot's move as segments, alike spots grouped
"""

import bisect
import itertools
import math
from collections.abc import Sequence
from typing import NamedTuple

import numpy as np

from e8s.cast.select import select
from e8s.choreo.engine import ALL, PathEngine
from e8s.choreo.engine_batch import group_counts, track_facings, track_positions
from e8s.choreo.engine_compile import plan_breaks, plan_progress
from e8s.choreo.engine_models import MotionPlan, MotionSegment
from e8s.choreo.path_models import PathStyle
from e8s.choreo.read_models import (
    CoordinateTable,
    Frames,
    SegmentDescription,
    SegmentKind,
    TransitionDescription,
)
from e8s.core.arrays import FloatArray
from e8s.core.caps import require_size
from e8s.core.errors import SchemaError, TimeRangeError
from e8s.core.ids import SetId, SpotId, TransitionId
from e8s.core.selection import Selector
from e8s.core.types import Count, Degrees, Point
from e8s.core.units import DEFAULT_UNITS, UnitRegistry, convert, vector_facing
from e8s.core.validation import require_finite
from e8s.show.models import Show
from e8s.time.clock import count_to_seconds, seconds_to_count
from e8s.time.models import Timeline

DEFAULT_FPS = 30.0
# Frame counts within this many frames of a whole number are whole: (s1 - s0) * fps carries
# float noise, and a range of exactly 4 s at 30 fps must keep its last frame.
FRAME_TOL = 1e-9
MAX_FRAME_VALUES = 20_000_000
DEFAULT_UNIT = "m"
STILL_M = 1e-9
GROUP_DIGITS = 6
FULL_TURN = 360.0
# Path styles name what the designer asked for; the compiled curve names what the spot does.
CURVE_SEGMENT_KINDS: dict[str, SegmentKind] = {
    "segment": "straight",
    "polyline": "straight",
    "arc": "rotate",
    "circle": "rotate",
    "trimmed": "follow",
}


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
    intervals = (s1 - s0) * fps
    if not intervals < MAX_FRAME_VALUES:
        raise SchemaError(
            f"{s1 - s0:g} s at {fps:g} fps is too many frames: about {intervals:.3g}, over the"
            f" cap of {MAX_FRAME_VALUES:,} (MAX_FRAME_VALUES)",
            hint="use a lower fps or a shorter range",
            context={"seconds": s1 - s0, "fps": fps, "cap": MAX_FRAME_VALUES},
        )
    n = math.floor(intervals + FRAME_TOL) + 1
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


def coordinate_table(
    show: Show,
    who: Selector = ALL,
    sets: Sequence[str] | None = None,
    *,
    unit: str = DEFAULT_UNIT,
    units: UnitRegistry = DEFAULT_UNITS,
    engine: PathEngine | None = None,
) -> CoordinateTable:
    """Returns selected spots' positions at each set, unrounded, in meters or another unit.

    Positions are PathEngine positions at each set's count, in the library's stage frame: x
    runs from audience-left to audience-right, y away from the audience, and the origin is the
    sheet's front-center. `unit` converts them through the unit registry; rounding for display
    is left to the caller.

    Args:
        show: Show to read.
        who: Spots to read, as a selection expression, a list of IDs or a Selection.
        sets: Set IDs in the order wanted; None reads every set in show order.
        unit: Length unit the positions are given in, meters by default.
        units: Registry `unit` is looked up in.
        engine: A PathEngine already built for `show`, reused so repeated reads skip compiling.

    Returns:
        table: (sets, spots) arrays of x, y and field states.

    Raises:
        UnknownRefError: A set or spot is not in the show, or `unit` is not in `units`.
        SchemaError: `sets` is empty, or `engine` belongs to another show.

    >>> from e8s.examples import two_set_show
    >>> table = coordinate_table(two_set_show(), "T2")
    >>> table.x[:, 0].tolist(), table.y[:, 0].tolist(), table.unit
    ([2.0, 2.0], [5.0, 9.0], 'm')
    >>> coordinate_table(two_set_show(), "T2", unit="cm").y[:, 0].tolist()
    [500.0, 900.0]
    """
    per_meter = convert(1.0, "m", unit, units)
    reader = _engine_for(show, engine)
    spots = _spot_ids(show, who)
    set_ids = _set_ids(show, sets)
    starts = _set_starts(show)
    counts = np.array([starts[show.set_index(set_id)] for set_id in set_ids], dtype=float)
    x, y = _set_positions(reader, spots, counts)
    on_field = [[show.set(set_id).get(spot).on_field for spot in spots] for set_id in set_ids]
    return CoordinateTable(
        spots=spots,
        sets=set_ids,
        unit=unit,
        counts=counts,
        x=x * per_meter,
        y=y * per_meter,
        on_field=np.array(on_field, dtype=bool).reshape(x.shape),
    )


def _direction(p0: Point, p1: Point) -> Degrees | None:
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    if math.hypot(dx, dy) <= STILL_M:
        return None
    return vector_facing((dx, dy))


def _travel_kind(plan: MotionPlan) -> SegmentKind:
    """Returns what the plan's curve is; a custom motion's sampled polyline stays custom."""
    if plan.curve is None:
        return "hold"
    if plan.style == PathStyle.CUSTOM:
        return "custom"
    return CURVE_SEGMENT_KINDS.get(plan.curve.kind, "curve")


def _corners(plan: MotionPlan, kind: SegmentKind) -> frozenset[float]:
    """Returns the local counts where a straight path turns a corner of its curve."""
    if kind != "straight" or plan.curve is None:
        return frozenset()
    inner = plan.curve._breaks()[1:-1]
    if not len(inner):
        return frozenset()
    breaks = plan_breaks(plan)
    progress = plan_progress(plan, breaks)
    turning = np.min(np.abs(progress[:, None] - inner[None, :]), axis=1) <= STILL_M
    return frozenset(breaks[turning].tolist())


class _Run(NamedTuple):
    piece: int
    still: bool
    t0: float
    t1: float
    p0: Point
    p1: Point


def _runs(plan: MotionPlan, segments: list[MotionSegment], kind: SegmentKind) -> list[_Run]:
    """Joins engine segments into runs that share a piece, stillness and corner-free travel."""
    ends = [piece.t1 for piece in plan.pieces]
    corners = _corners(plan, kind)
    runs: list[_Run] = []
    for seg in segments:
        piece = min(bisect.bisect_left(ends, (seg.t0 + seg.t1) / 2), len(ends) - 1)
        still = plan.pieces[piece].kind == "hold" or math.dist(seg.p0, seg.p1) <= STILL_M
        last = runs[-1] if runs else None
        if last and (last.piece, last.still) == (piece, still) and seg.t0 not in corners:
            runs[-1] = last._replace(t1=seg.t1, p1=seg.p1)
            continue
        runs.append(_Run(piece, still, seg.t0, seg.t1, seg.p0, seg.p1))
    return runs


def _described(plan: MotionPlan, run: _Run, kind: SegmentKind, pace: float) -> SegmentDescription:
    counts = run.t1 - run.t0
    if run.still:
        return SegmentDescription(
            kind="hold",
            start=run.t0,
            counts=counts,
            direction=None,
            distance=0.0,
            stride_m=None,
            pace=pace,
        )
    s0, s1 = plan_progress(plan, np.array([run.t0, run.t1])).tolist()
    distance = abs(s1 - s0)
    return SegmentDescription(
        kind=kind,
        start=run.t0,
        counts=counts,
        direction=_direction(run.p0, run.p1),
        distance=distance,
        stride_m=distance / (counts * pace),
        pace=pace,
    )


def _merged_holds(segments: list[SegmentDescription]) -> tuple[SegmentDescription, ...]:
    merged: list[SegmentDescription] = []
    for seg in segments:
        if merged and seg.kind == "hold" and merged[-1].kind == "hold":
            merged[-1] = merged[-1].replace(counts=seg.start + seg.counts - merged[-1].start)
            continue
        merged.append(seg)
    return tuple(merged)


def _describe_spot(
    engine: PathEngine, tid: TransitionId, spot: SpotId, pace: float
) -> tuple[SegmentDescription, ...]:
    plan = engine.compile(tid).plans[spot]
    kind = _travel_kind(plan)
    runs = _runs(plan, engine.entity_segments(spot, tid), kind)
    return _merged_holds([_described(plan, run, kind, pace) for run in runs])


def _rounded_value(value: float | None) -> float | None:
    return None if value is None else round(value, GROUP_DIGITS) + 0.0


def _rounded_direction(value: Degrees | None) -> Degrees | None:
    return None if value is None else round(value, GROUP_DIGITS) % FULL_TURN + 0.0


def _alike_key(segments: tuple[SegmentDescription, ...]) -> tuple[tuple[object, ...], ...]:
    return tuple(
        (
            seg.kind,
            _rounded_value(seg.start),
            _rounded_value(seg.counts),
            _rounded_direction(seg.direction),
            _rounded_value(seg.distance),
            _rounded_value(seg.stride_m),
            _rounded_value(seg.pace),
        )
        for seg in segments
    )


def describe_transition(
    show: Show,
    transition: tuple[str, str],
    who: Selector = ALL,
    *,
    engine: PathEngine | None = None,
) -> TransitionDescription:
    """Describes each selected spot's move in a transition as segments, alike spots grouped.

    Segments split where the move's window starts and ends, at timed waypoints, at each corner
    of a straight path and wherever the spot stands still inside its move. Each gives its kind
    (from the path's curve), start, counts, direction of travel, distance, mean stride and
    pace, all read from PathEngine.

    Args:
        show: Show to read.
        transition: Pair of consecutive set IDs, such as `("1", "2")`.
        who: Spots to describe, as a selection expression, a list of IDs or a Selection.
        engine: A PathEngine already built for `show`, reused so repeated reads skip compiling.

    Returns:
        description: Every selected spot's segments, and the groups of spots that move alike.

    Raises:
        UnknownRefError: The pair is not a transition of the show, or a spot is unknown.
        SchemaError: `engine` belongs to another show.

    >>> from e8s.examples import two_set_show
    >>> told = describe_transition(two_set_show(), ("1", "2"))
    >>> told.groups
    (('T1', 'T2', 'T3', 'T4'),)
    >>> move = told.moves["T1"][0]
    >>> move.kind, move.counts, move.direction, move.distance, move.stride_m
    ('straight', 8.0, 180.0, 4.0, 0.5)
    """
    reader = _engine_for(show, engine)
    tid = show.transition_id(transition)
    spec = show.transition(tid)
    moves = {
        spot: _describe_spot(reader, tid, spot, spec.row(spot).pace)
        for spot in _spot_ids(show, who)
    }
    alike: dict[tuple[tuple[object, ...], ...], list[SpotId]] = {}
    for spot, segments in moves.items():
        alike.setdefault(_alike_key(segments), []).append(spot)
    return TransitionDescription(
        transition=tid,
        counts=spec.counts,
        moves=moves,
        groups=tuple(tuple(spots) for spots in alike.values()),
    )
