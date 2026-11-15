"""
clock.py --- converts counts to seconds and back through tempo sections, linear ramps and holds

Contains:
    tempo_at(): returns the tempo at a count, including ramps
    count_to_seconds(): converts a count to show seconds
    seconds_to_count(): converts show seconds to a count; during a hold returns its count
    seconds_per_count(): returns the length of one count at a count, for speed checks
    describe_count(): renders a count as readable time for the canvas and agent
"""

import math
from collections import defaultdict
from dataclasses import dataclass

from showpy.core.errors import TimelineError, TimeRangeError
from showpy.core.types import Count, CountsPerMinute, Seconds
from showpy.core.units import round_to
from showpy.time.measures import count_to_measure
from showpy.time.models import TempoSection, Timeline, TimelineMode

SECONDS_PER_MINUTE = 60.0
SECONDS_MODE_CPM = 60.0
TENTHS_PER_SECOND = 10
TENTHS_PER_MINUTE = 600
TEMPO_DISPLAY_DIGITS = 1


@dataclass(frozen=True)
class _Piece:
    """Covers counts [start, end) with tempo changing linearly at `slope` cpm per count."""

    start: Count
    end: Count
    tempo: CountsPerMinute
    slope: float

    def tempo_in(self, c: Count) -> CountsPerMinute:
        return self.tempo + self.slope * (c - self.start)

    def seconds_to(self, c: Count) -> Seconds:
        """Integrates 60 / tempo(c) from the piece start; a ramp gives 60/k · ln(r(c)/r0)."""
        span = c - self.start
        if self.slope == 0.0:
            return SECONDS_PER_MINUTE * span / self.tempo
        return SECONDS_PER_MINUTE / self.slope * math.log1p(self.slope * span / self.tempo)

    def count_after(self, s: Seconds) -> Count:
        """Inverts seconds_to(): r(c) = r0 · exp(k·s/60), so c = start + r0 · expm1(k·s/60) / k."""
        if self.slope == 0.0:
            span = s * self.tempo / SECONDS_PER_MINUTE
        else:
            span = self.tempo * math.expm1(self.slope * s / SECONDS_PER_MINUTE) / self.slope
        return min(self.end, self.start + max(0.0, span))


def _require_non_negative(value: float, what: str) -> None:
    if value < 0:
        raise TimeRangeError(
            f"{what} {value} is before the show start",
            hint=f"{what} start at 0, the show start",
            context={what: value},
        )


def _sections(tl: Timeline) -> tuple[TempoSection, ...]:
    if not tl.tempos and tl.mode is TimelineMode.SECONDS:
        return (TempoSection(at_count=0.0, counts_per_minute=SECONDS_MODE_CPM),)
    if not tl.tempos:
        raise TimelineError(
            f"a {tl.mode.value} timeline without tempo cannot convert counts",
            hint="add a tempo with add_tempo(); in cue mode time cues with cue_to_seconds()",
            context={"mode": tl.mode.value},
        )
    if tl.tempos[0].at_count != 0.0:
        raise TimelineError(
            f"first tempo starts at count {tl.tempos[0].at_count}, not 0",
            hint="add a tempo section at count 0",
            context={"first_at_count": tl.tempos[0].at_count},
        )
    return tl.tempos


def _require_valid_section(section: TempoSection) -> None:
    has_ramp_to, has_ramp_counts = section.ramp_to is not None, section.ramp_counts is not None
    ramp_tempos = [] if section.ramp_to is None else [section.ramp_to]
    tempos = [section.counts_per_minute, *ramp_tempos]
    problems = [
        has_ramp_to != has_ramp_counts,
        not all(t > 0 for t in tempos),
        section.ramp_counts is not None and not section.ramp_counts > 0,
    ]
    if any(problems):
        raise TimelineError(
            f"tempo section at count {section.at_count} is malformed",
            hint="tempos must be positive; give both ramp_to and ramp_counts (> 0) or neither",
            context={"section": section.model_dump()},
        )


def _section_pieces(section: TempoSection, next_start: Count) -> list[_Piece]:
    _require_valid_section(section)
    start, tempo = section.at_count, section.counts_per_minute
    if section.ramp_to is None or section.ramp_counts is None:
        return [_Piece(start, next_start, tempo, 0.0)]
    slope = (section.ramp_to - tempo) / section.ramp_counts
    ramp_end = min(start + section.ramp_counts, next_start)
    pieces = [_Piece(start, ramp_end, tempo, slope)]
    if ramp_end < next_start:
        pieces.append(_Piece(ramp_end, next_start, section.ramp_to, 0.0))
    return pieces


def _split(pieces: list[_Piece], at: Count) -> list[_Piece]:
    result: list[_Piece] = []
    for piece in pieces:
        if piece.start < at < piece.end:
            result.append(_Piece(piece.start, at, piece.tempo, piece.slope))
            result.append(_Piece(at, piece.end, piece.tempo_in(at), piece.slope))
        else:
            result.append(piece)
    return result


def _hold_seconds(tl: Timeline) -> dict[Count, Seconds]:
    totals: dict[Count, Seconds] = defaultdict(float)
    for hold in tl.holds:
        if hold.at_count < 0 or hold.seconds < 0:
            raise TimeRangeError(
                f"hold at count {hold.at_count} lasting {hold.seconds}s is out of range",
                hint="holds need a count >= 0 and seconds >= 0",
                context={"at_count": hold.at_count, "seconds": hold.seconds},
            )
        totals[hold.at_count] += hold.seconds
    return dict(totals)


def _pieces(tl: Timeline) -> list[_Piece]:
    """Splits the timeline into ramp and steady pieces, cut at every hold count."""
    sections = _sections(tl)
    starts = [s.at_count for s in sections[1:]] + [math.inf]
    pieces = [p for s, end in zip(sections, starts, strict=True) for p in _section_pieces(s, end)]
    for at in sorted({h.at_count for h in tl.holds}):
        pieces = _split(pieces, at)
    return pieces


def tempo_at(tl: Timeline, c: Count) -> CountsPerMinute:
    """Returns the tempo at a count; ramps change tempo linearly in counts.

    >>> from showpy.time.timeline import new_timeline
    >>> ramp = TempoSection(at_count=0.0, counts_per_minute=60.0, ramp_to=120.0, ramp_counts=60.0)
    >>> tempo_at(Timeline(mode=TimelineMode.COUNTS, tempos=(ramp,)), 15.0)
    75.0
    """
    _require_non_negative(c, "counts")
    piece = [p for p in _pieces(tl) if p.start <= c][-1]
    return piece.tempo_in(c)


def count_to_seconds(tl: Timeline, c: Count) -> Seconds:
    """Converts a count to seconds since the show start, adding holds before `c`.

    A hold at count `h` counts toward every count after `h`, not toward `h` itself.

    >>> from showpy.time.timeline import new_timeline
    >>> count_to_seconds(new_timeline(TimelineMode.COUNTS, 120.0), 8.0)
    4.0
    """
    _require_non_negative(c, "counts")
    holds = _hold_seconds(tl)
    elapsed = 0.0
    for piece in _pieces(tl):
        if c <= piece.start:
            return elapsed
        elapsed += holds.get(piece.start, 0.0) + piece.seconds_to(min(c, piece.end))
    return elapsed


def seconds_to_count(tl: Timeline, s: Seconds) -> Count:
    """Converts seconds since the show start to a count; during a hold returns the hold's count.

    >>> from showpy.time.timeline import new_timeline
    >>> seconds_to_count(new_timeline(TimelineMode.COUNTS, 120.0), 4.0)
    8.0
    """
    _require_non_negative(s, "seconds")
    holds = _hold_seconds(tl)
    *bounded, last = _pieces(tl)
    remaining = s
    for piece in bounded:
        hold = holds.get(piece.start, 0.0)
        if remaining <= hold:
            return piece.start
        duration = piece.seconds_to(piece.end)
        if remaining - hold <= duration:
            return piece.count_after(remaining - hold)
        remaining -= hold + duration
    hold = holds.get(last.start, 0.0)
    return last.start if remaining <= hold else last.count_after(remaining - hold)


def seconds_per_count(tl: Timeline, c: Count) -> Seconds:
    """Returns how long one count lasts at `c`, i.e. 60 / tempo_at(c).

    >>> from showpy.time.timeline import new_timeline
    >>> seconds_per_count(new_timeline(TimelineMode.COUNTS, 120.0), 3.0)
    0.5
    """
    return SECONDS_PER_MINUTE / tempo_at(tl, c)


def _clock_text(s: Seconds) -> str:
    tenths = int(round_to(s * TENTHS_PER_SECOND, 1.0))
    minutes, rest = divmod(tenths, TENTHS_PER_MINUTE)
    whole, tenth = divmod(rest, TENTHS_PER_SECOND)
    return f"{minutes}:{whole:02d}.{tenth}"


def describe_count(tl: Timeline, c: Count) -> str:
    """Renders a count as count, measure (when measures are on), m:ss.s and tempo.

    >>> from showpy.time.timeline import new_timeline
    >>> describe_count(new_timeline(TimelineMode.COUNTS, 120.0), 130.0)
    'count 130, 1:05.0, 120 cpm'
    """
    label = count_to_measure(tl, c)
    measure = "" if label is None else f" (m. {label.measure})"
    tempo = round(tempo_at(tl, c), TEMPO_DISPLAY_DIGITS)
    return f"count {c:g}{measure}, {_clock_text(count_to_seconds(tl, c))}, {tempo:g} cpm"
