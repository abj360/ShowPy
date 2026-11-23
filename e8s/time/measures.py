"""
measures.py --- converts counts to 1-based measure labels and back, honouring length changes

Contains:
    count_to_measure(): labels a count with its measure and count in measure
    measure_to_count(): finds the count where a measure label falls
"""

import math
from dataclasses import dataclass
from itertools import pairwise

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import TimelineError, TimeRangeError
from e8s.core.types import Count
from e8s.time.models import MeasureLabel, MeasureSetting, Timeline

MEASURE_TOL = 1e-9


@dataclass(frozen=True)
class _Segment:
    start: Count
    length: float
    first_measure: int


def _require_valid_setting(setting: MeasureSetting) -> None:
    lengths = [setting.counts_per_measure, *(length for _, length in setting.changes)]
    counts = [at_count for at_count, _ in setting.changes]
    is_valid = all(math.isfinite(length) and length > 0 for length in lengths)
    is_valid = is_valid and all(math.isfinite(at) and at >= 0 for at in counts)
    is_valid = is_valid and all(after > before for before, after in pairwise(counts))
    if not is_valid:
        raise TimelineError(
            "measure lengths must be above 0 and change counts must increase from 0",
            hint="give counts_per_measure and every change length above 0, one change per count",
            code=ErrorCode.MEASURE_LENGTH,
            context={"counts_per_measure": setting.counts_per_measure, "changes": setting.changes},
        )


def _segments(setting: MeasureSetting) -> list[_Segment]:
    _require_valid_setting(setting)
    segments = [_Segment(0.0, setting.counts_per_measure, setting.start_measure)]
    for at_count, length in setting.changes:
        previous = segments[-1]
        elapsed = math.ceil((at_count - previous.start) / previous.length - MEASURE_TOL)
        segments.append(_Segment(at_count, length, previous.first_measure + elapsed))
    return segments


def count_to_measure(tl: Timeline, c: Count) -> MeasureLabel | None:
    """Labels a count with its measure; returns None when measures are off.

    Count `c` in a segment starting at count `s`, with measure length `m` and first measure `k`,
    is measure `k + floor((c - s) / m)`, count `(c - s) mod m + 1`.

    >>> from e8s.time.models import TimelineMode
    >>> four = MeasureSetting(counts_per_measure=4.0)
    >>> tl = Timeline(mode=TimelineMode.SECONDS, measures=four)
    >>> count_to_measure(tl, 130.0)
    MeasureLabel(measure=33, count_in_measure=3.0)
    """
    if c < 0:
        raise TimeRangeError(
            f"count {c} is before the show start",
            hint="counts start at 0",
            context={"count": c},
        )
    if tl.measures is None:
        return None
    segment = [s for s in _segments(tl.measures) if s.start <= c][-1]
    offset = c - segment.start
    index = math.floor(offset / segment.length + MEASURE_TOL)
    in_measure = max(0.0, offset - index * segment.length)
    return MeasureLabel(measure=segment.first_measure + index, count_in_measure=in_measure + 1.0)


def measure_to_count(tl: Timeline, measure: int, count_in_measure: float = 1.0) -> Count:
    """Finds the count where `measure`, `count_in_measure` (both 1-based) falls.

    >>> from e8s.time.models import TimelineMode
    >>> four = MeasureSetting(counts_per_measure=4.0)
    >>> tl = Timeline(mode=TimelineMode.SECONDS, measures=four)
    >>> measure_to_count(tl, 33, 3.0)
    130.0
    """
    if tl.measures is None:
        raise TimelineError(
            "measures are off",
            hint="turn them on with set_measures(tl, MeasureSetting(...))",
            context={"measure": measure},
        )
    if isinstance(measure, bool) or not isinstance(measure, int):
        raise TimeRangeError(
            f"measure {measure!r} is not a whole number",
            hint="measures are numbered 1, 2, 3, ...; pass an int",
            context={"measure": measure},
        )
    _require_measure(tl.measures, measure)
    segments = _segments(tl.measures)
    position = [i for i, s in enumerate(segments) if s.first_measure <= measure][-1]
    segment = segments[position]
    _require_count_in_measure(segment, measure, count_in_measure)
    count = segment.start + (measure - segment.first_measure) * segment.length
    count += count_in_measure - 1.0
    next_start = segments[position + 1].start if position + 1 < len(segments) else math.inf
    if count >= next_start:
        raise TimeRangeError(
            f"measure {measure} ends at count {next_start}, before count {count_in_measure}",
            hint="the measure is shortened by a length change; pick an earlier count",
            context={"measure": measure, "count_in_measure": count_in_measure},
        )
    return count


def _require_measure(setting: MeasureSetting, measure: int) -> None:
    if measure < setting.start_measure:
        raise TimeRangeError(
            f"measure {measure} is before the first measure {setting.start_measure}",
            hint=f"use measure {setting.start_measure} or later",
            context={"measure": measure, "start_measure": setting.start_measure},
        )


def _require_count_in_measure(segment: _Segment, measure: int, count_in_measure: float) -> None:
    if not 1.0 <= count_in_measure < segment.length + 1.0:
        raise TimeRangeError(
            f"measure {measure} has no count {count_in_measure}",
            hint=f"use a count from 1 up to but not including {segment.length + 1.0:g}",
            context={"measure": measure, "count_in_measure": count_in_measure},
        )
