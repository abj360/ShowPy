"""
read.py --- the floor readers: motion over time as arrays, all answered by PathEngine

Contains:
    frames(): returns every selected spot's position and facing at regular frame times
    coordinate_table(): returns selected spots' positions at sets, in meters and in steps
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
from e8s.choreo.engine_models import MotionPiece, MotionPlan, MotionSegment
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
from e8s.core.ids import LandmarkId, SetId, SpotId, TransitionId
from e8s.core.selection import Selector
from e8s.core.types import Count, Degrees, Point
from e8s.core.units import angle_diff, convert, round_to, vector_facing
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
STILL_M = 1e-9
DIRECTION_TOL_DEG = 1e-6
GROUP_DIGITS = 6
FULL_TURN = 360.0
SEGMENT_KINDS: dict[PathStyle, SegmentKind] = {
    PathStyle.STRAIGHT: "straight",
    PathStyle.POLYLINE: "straight",
    PathStyle.CURVE: "curve",
    PathStyle.FOLLOW: "follow",
    PathStyle.ROTATE: "rotate",
    PathStyle.CUSTOM: "custom",
    PathStyle.HOLD: "hold",
}


class _Leg(NamedTuple):
    t0: float
    t1: float
    p0: Point
    p1: Point
    distance: float
    heading: Degrees | None


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


def _direction(p0: Point, p1: Point) -> Degrees | None:
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    if math.hypot(dx, dy) <= STILL_M:
        return None
    return vector_facing((dx, dy))


def _step_size(distance: float, counts: float, pace: float) -> float | None:
    if distance <= STILL_M:
        return None
    return FIVE_YARDS_M * counts * pace / distance


def _moving(kind: SegmentKind, leg: _Leg, pace: float) -> SegmentDescription:
    counts = leg.t1 - leg.t0
    return SegmentDescription(
        kind=kind,
        start=leg.t0,
        counts=counts,
        direction=_direction(leg.p0, leg.p1),
        distance=leg.distance,
        step_size=_step_size(leg.distance, counts, pace),
        pace=pace,
    )


def _holding(t0: float, t1: float, pace: float) -> SegmentDescription:
    return SegmentDescription(
        kind="hold",
        start=t0,
        counts=t1 - t0,
        direction=None,
        distance=0.0,
        step_size=None,
        pace=pace,
    )


def _bends(heading: Degrees | None, leg: _Leg) -> bool:
    if heading is None or leg.heading is None:
        return False
    return abs(angle_diff(leg.heading, heading)) > DIRECTION_TOL_DEG


def _straight_legs(segments: list[MotionSegment]) -> list[_Leg]:
    legs: list[_Leg] = []
    for seg in segments:
        heading = _direction(seg.p0, seg.p1)
        length = math.dist(seg.p0, seg.p1)
        if not legs or _bends(heading, legs[-1]):
            legs.append(_Leg(seg.t0, seg.t1, seg.p0, seg.p1, length, heading))
            continue
        last = legs[-1]
        kept = last.heading if last.heading is not None else heading
        legs[-1] = _Leg(last.t0, seg.t1, last.p0, seg.p1, last.distance + length, kept)
    return legs


def _by_piece(plan: MotionPlan, segments: list[MotionSegment]) -> list[list[MotionSegment]]:
    ends = [piece.t1 for piece in plan.pieces]
    grouped: list[list[MotionSegment]] = [[] for _ in plan.pieces]
    for seg in segments:
        index = bisect.bisect_left(ends, (seg.t0 + seg.t1) / 2)
        grouped[min(index, len(ends) - 1)].append(seg)
    return grouped


def _piece_segments(
    kind: SegmentKind, piece: MotionPiece, segments: list[MotionSegment], pace: float
) -> list[SegmentDescription]:
    if piece.kind == "hold" or not segments:
        return [_holding(piece.t0, piece.t1, pace)]
    if kind == "straight":
        return [_moving(kind, leg, pace) for leg in _straight_legs(segments)]
    whole = _Leg(piece.t0, piece.t1, segments[0].p0, segments[-1].p1, piece.length, None)
    return [_moving(kind, whole, pace)]


def _merged_holds(segments: list[SegmentDescription]) -> tuple[SegmentDescription, ...]:
    merged: list[SegmentDescription] = []
    for seg in segments:
        if merged and seg.kind == "hold" and merged[-1].kind == "hold":
            merged[-1] = merged[-1].replace(counts=merged[-1].counts + seg.counts)
            continue
        merged.append(seg)
    return tuple(merged)


def _describe_spot(
    engine: PathEngine, tid: TransitionId, spot: SpotId, pace: float
) -> tuple[SegmentDescription, ...]:
    plan = engine.compile(tid).plans[spot]
    kind = SEGMENT_KINDS[plan.style]
    grouped = _by_piece(plan, engine.entity_segments(spot, tid))
    described = [
        seg
        for piece, segments in zip(plan.pieces, grouped, strict=True)
        for seg in _piece_segments(kind, piece, segments, pace)
    ]
    return _merged_holds(described)


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
            _rounded_value(seg.step_size),
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

    Segments split where the move's window starts and ends, at timed waypoints and at each
    corner of a straight path. Each gives its kind, start, counts, direction of travel,
    distance, step size in steps per 5 yards and pace, all read from PathEngine.

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
    >>> move.kind, move.counts, move.direction, move.distance, round(move.step_size, 3)
    ('straight', 8.0, 180.0, 4.0, 9.144)
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
