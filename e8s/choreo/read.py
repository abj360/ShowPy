"""
read.py --- the floor readers: motion over time as arrays, all answered by PathEngine

Contains:
    frames(): returns every selected spot's position and facing at regular frame times
"""

import itertools
import math

import numpy as np

from e8s.cast.select import select
from e8s.choreo.engine import ALL, PathEngine
from e8s.choreo.engine_batch import group_counts, track_facings, track_positions
from e8s.choreo.read_models import Frames
from e8s.core.arrays import FloatArray
from e8s.core.caps import require_size
from e8s.core.errors import SchemaError, TimeRangeError
from e8s.core.ids import SpotId
from e8s.core.selection import Selector
from e8s.core.types import Count
from e8s.core.validation import require_finite
from e8s.show.models import Show
from e8s.time.clock import count_to_seconds, seconds_to_count
from e8s.time.models import Timeline

DEFAULT_FPS = 30.0
# Frame counts within this many frames of a whole number are whole: (s1 - s0) * fps carries
# float noise, and a range of exactly 4 s at 30 fps must keep its last frame.
FRAME_TOL = 1e-9
MAX_FRAME_VALUES = 20_000_000


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


def _total_counts(show: Show) -> Count:
    starts = list(itertools.accumulate((s.counts for s in show.sets[1:]), initial=0.0))
    return starts[-1]


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
