"""
clock.py --- converts counts to seconds and back through tempo sections, linear ramps and holds

Contains:
    CompiledClock: answers count and second lookups by binary search over a timeline's pieces
    compile_clock(): compiles a timeline into its clock, refusing data that cannot convert
    tempo_at(): returns the tempo at a count, including ramps
    count_to_seconds(): converts a count to show seconds
    seconds_to_count(): converts show seconds to a count; during a hold returns its count
    seconds_per_count(): returns the length of one count at a count, for speed checks
    describe_count(): renders a count as readable time for the canvas and agent
"""

import math
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from itertools import pairwise

from e8s.core.error_codes import ErrorCode
from e8s.core.errors import TimelineError, TimeRangeError
from e8s.core.numbers import format_number
from e8s.core.types import Count, CountsPerMinute, Seconds
from e8s.core.units import round_to
from e8s.time.measures import count_to_measure
from e8s.time.models import TempoSection, Timeline, TimelineMode

SECONDS_PER_MINUTE = 60.0
SECONDS_MODE_CPM = 60.0
TENTHS_PER_SECOND = 10
TENTHS_PER_MINUTE = 600
TEMPO_DISPLAY_DIGITS = 1
LOG1P_RATIO = 0.5


def _lerp(a: float, b: float, u: float) -> float:
    """Mixes two positive tempos; the weighted sum stays positive, unlike a + (b - a) · u."""
    return a * (1.0 - u) + b * u


def _log_ratio(a: CountsPerMinute, b: CountsPerMinute) -> float:
    """Returns ln(b / a) for positive tempos, through log1p when they are close."""
    if abs(b - a) < LOG1P_RATIO * a:
        return math.log1p((b - a) / a)
    ratio = b / a
    if 0.0 < ratio < math.inf:
        return math.log(ratio)
    return math.log(b) - math.log(a)


def _log_mean(a: CountsPerMinute, b: CountsPerMinute) -> CountsPerMinute:
    """Returns (b - a) / ln(b / a), the mean tempo of a linear ramp from `a` to `b`."""
    return a if a == b else (b - a) / _log_ratio(a, b)


@dataclass(frozen=True)
class _Piece:
    """Covers counts [start, end); a ramp goes linearly from `tempo` to `end_tempo` in `length`.

    A ramp's `length` is its stored count length, not `end - start`, which rounds in float. Every
    ramp formula clamps to it, so the ramp ends exactly at `end_tempo` and its own seconds.
    """

    start: Count
    end: Count
    tempo: CountsPerMinute
    end_tempo: CountsPerMinute
    length: Count

    @property
    def is_ramp(self) -> bool:
        return self.tempo != self.end_tempo

    def _fraction(self, c: Count) -> float:
        return min(1.0, (c - self.start) / self.length)

    def tempo_in(self, c: Count) -> CountsPerMinute:
        if not self.is_ramp:
            return self.tempo
        return _lerp(self.tempo, self.end_tempo, self._fraction(c))

    def seconds_to(self, c: Count) -> Seconds:
        """Integrates 60 / tempo(c) from the piece start: counts over the ramp's mean tempo."""
        span = c - self.start
        if not self.is_ramp:
            return SECONDS_PER_MINUTE * span / self.tempo
        return SECONDS_PER_MINUTE * min(span, self.length) / _log_mean(self.tempo, self.tempo_in(c))

    def count_after(self, s: Seconds) -> Count:
        """Inverts seconds_to(): `s` seconds into a ramp, its tempo has grown by exp(growth)."""
        if not self.is_ramp:
            return min(self.end, self.start + s * self.tempo / SECONDS_PER_MINUTE)
        a, b = self.tempo, self.end_tempo
        if s >= SECONDS_PER_MINUTE * self.length / _log_mean(a, b):
            return self.end
        growth = (b - a) * (s / (SECONDS_PER_MINUTE * self.length))
        return min(self.end, self.start + self.length * self._ramp_fraction(growth))

    def _ramp_fraction(self, growth: float) -> float:
        """Returns the share of the ramp's counts done at `growth`: expm1(growth) / expm1(λ).

        λ = ln(end_tempo / tempo). A speeding ramp rescales by exp(growth - λ), the same value,
        so a tempo ratio past e**709 never overflows.
        """
        a, b = self.tempo, self.end_tempo
        if b < a:
            return math.expm1(growth) / ((b - a) / a)
        rescale = math.exp(growth - _log_ratio(a, b))
        return rescale * math.expm1(-growth) / ((a - b) / b)


def _require_in_range(value: float, what: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise TimeRangeError(
            f"{what} {value} is not a finite time at or after the show start",
            hint=f"{what} are finite and start at 0, the show start",
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
    counts = [s.at_count for s in tl.tempos]
    if counts[0] != 0.0:
        raise TimelineError(
            f"first tempo starts at count {counts[0]}, not 0",
            hint="add a tempo section at count 0",
            context={"first_at_count": counts[0]},
        )
    if any(not after > before for before, after in pairwise(counts)):
        raise TimelineError(
            f"tempo counts {counts} do not increase",
            hint="keep tempo sections in increasing count order, one per count",
            context={"at_counts": counts},
        )
    return tl.tempos


def _require_valid_section(section: TempoSection) -> None:
    has_ramp_to, has_ramp_counts = section.ramp_to is not None, section.ramp_counts is not None
    ramp_tempos = [] if section.ramp_to is None else [section.ramp_to]
    ramp_counts = [] if section.ramp_counts is None else [section.ramp_counts]
    tempos = [section.counts_per_minute, *ramp_tempos]
    problems = [
        has_ramp_to != has_ramp_counts,
        not all(math.isfinite(t) and t > 0 for t in tempos),
        not all(math.isfinite(n) and n > 0 for n in ramp_counts),
        not math.isfinite(section.at_count),
    ]
    if any(problems):
        raise TimelineError(
            f"tempo section at count {section.at_count} is malformed",
            hint="tempos must be positive and finite; give both ramp_to and ramp_counts (> 0, "
            "finite) or neither",
            context={"section": section._dumped()},
        )


def _section_pieces(section: TempoSection, next_start: Count) -> list[_Piece]:
    _require_valid_section(section)
    start, tempo = section.at_count, section.counts_per_minute
    if section.ramp_to is None or section.ramp_counts is None:
        return [_Piece(start, next_start, tempo, tempo, next_start - start)]
    ramp_to, ramp_counts = section.ramp_to, section.ramp_counts
    ramp_end = start + ramp_counts
    if ramp_end >= next_start:
        length = min(next_start - start, ramp_counts)
        reached = _lerp(tempo, ramp_to, length / ramp_counts)
        return [_Piece(start, next_start, tempo, reached, length)]
    return [
        _Piece(start, ramp_end, tempo, ramp_to, ramp_counts),
        _Piece(ramp_end, next_start, ramp_to, ramp_to, next_start - ramp_end),
    ]


def _split(pieces: list[_Piece], at: Count) -> list[_Piece]:
    result: list[_Piece] = []
    for piece in pieces:
        if piece.start < at < piece.end:
            before = min(at - piece.start, piece.length)
            reached = piece.tempo_in(at)
            result.append(_Piece(piece.start, at, piece.tempo, reached, before))
            result.append(_Piece(at, piece.end, reached, piece.end_tempo, piece.length - before))
        else:
            result.append(piece)
    return result


def _hold_seconds(tl: Timeline) -> dict[Count, Seconds]:
    totals: dict[Count, Seconds] = {}
    for hold in tl.holds:
        if not (math.isfinite(hold.at_count) and math.isfinite(hold.seconds)) or (
            hold.at_count < 0 or hold.seconds < 0
        ):
            raise TimeRangeError(
                f"hold at count {hold.at_count} lasting {hold.seconds}s is out of range",
                hint="holds need a finite count >= 0 and finite seconds >= 0",
                context={"at_count": hold.at_count, "seconds": hold.seconds},
            )
        if hold.at_count in totals:
            raise TimelineError(
                f"two holds share count {hold.at_count}",
                hint="merge them into one hold, or use add_hold(), which replaces the hold",
                context={"at_count": hold.at_count},
                code=ErrorCode.HOLD_OVERLAP,
            )
        totals[hold.at_count] = hold.seconds
    return totals


def _pieces(tl: Timeline) -> list[_Piece]:
    """Splits the timeline into ramp and steady pieces, cut at every hold count."""
    sections = _sections(tl)
    starts = [s.at_count for s in sections[1:]] + [math.inf]
    pieces = [p for s, end in zip(sections, starts, strict=True) for p in _section_pieces(s, end)]
    for at in sorted({h.at_count for h in tl.holds}):
        pieces = _split(pieces, at)
    return pieces


@dataclass(frozen=True)
class CompiledClock:
    """Holds a timeline's pieces and running totals, so each lookup is a binary search.

    Attributes:
        pieces: Ramp and steady pieces in count order, cut at every hold count.
        starts: Start count of each piece.
        holds: Hold seconds at each piece start; 0 where there is none.
        elapsed: Seconds before each piece's hold, so piece `i` ends at `elapsed[i + 1]`.
    """

    pieces: tuple[_Piece, ...]
    starts: tuple[Count, ...]
    holds: tuple[Seconds, ...]
    elapsed: tuple[Seconds, ...]

    def tempo_at(self, c: Count) -> CountsPerMinute:
        """Returns the tempo of the piece holding count `c`."""
        return self.pieces[bisect_right(self.starts, c) - 1].tempo_in(c)

    def seconds_at(self, c: Count) -> Seconds:
        """Returns the show seconds at count `c`, with the holds before it."""
        index = bisect_left(self.starts, c) - 1
        if index < 0:
            return 0.0
        piece = self.pieces[index]
        return self.elapsed[index] + self.holds[index] + piece.seconds_to(min(c, piece.end))

    def count_at(self, s: Seconds) -> Count:
        """Returns the count at show second `s`; during a hold, the hold's count."""
        last = len(self.pieces) - 1
        index = bisect_left(self.elapsed, s, 1, last + 1) - 1
        piece = self.pieces[index]
        into = s - self.elapsed[index]
        if into <= self.holds[index]:
            return piece.start
        return piece.count_after(into - self.holds[index])


def compile_clock(tl: Timeline) -> CompiledClock:
    """Compiles a timeline into its clock, refusing tempo or hold data that cannot convert.

    Args:
        tl: Timeline to compile.

    Returns:
        clock: Pieces and running totals that answer every conversion by binary search.
    """
    holds = _hold_seconds(tl)
    pieces = _pieces(tl)
    hold_seconds = tuple(holds.get(p.start, 0.0) for p in pieces)
    elapsed = [0.0]
    for piece, hold in zip(pieces[:-1], hold_seconds, strict=False):
        elapsed.append(elapsed[-1] + hold + piece.seconds_to(piece.end))
    return CompiledClock(
        pieces=tuple(pieces),
        starts=tuple(p.start for p in pieces),
        holds=hold_seconds,
        elapsed=tuple(elapsed),
    )


def tempo_at(tl: Timeline, c: Count) -> CountsPerMinute:
    """Returns the tempo at a count; ramps change tempo linearly in counts.

    >>> from e8s.time.timeline import new_timeline
    >>> ramp = TempoSection(at_count=0.0, counts_per_minute=60.0, ramp_to=120.0, ramp_counts=60.0)
    >>> tempo_at(Timeline(mode=TimelineMode.COUNTS, tempos=(ramp,)), 15.0)
    75.0
    """
    _require_in_range(c, "counts")
    return tl.clock.tempo_at(c)


def count_to_seconds(tl: Timeline, c: Count) -> Seconds:
    """Converts a count to seconds since the show start, adding holds before `c`.

    A hold at count `h` counts toward every count after `h`, not toward `h` itself.

    >>> from e8s.time.timeline import new_timeline
    >>> count_to_seconds(new_timeline(TimelineMode.COUNTS, 120.0), 8.0)
    4.0
    """
    _require_in_range(c, "counts")
    return tl.clock.seconds_at(c)


def seconds_to_count(tl: Timeline, s: Seconds) -> Count:
    """Converts seconds since the show start to a count; during a hold returns the hold's count.

    >>> from e8s.time.timeline import new_timeline
    >>> seconds_to_count(new_timeline(TimelineMode.COUNTS, 120.0), 4.0)
    8.0
    """
    _require_in_range(s, "seconds")
    return tl.clock.count_at(s)


def seconds_per_count(tl: Timeline, c: Count) -> Seconds:
    """Returns how long one count lasts at `c`, i.e. 60 / tempo_at(c).

    >>> from e8s.time.timeline import new_timeline
    >>> seconds_per_count(new_timeline(TimelineMode.COUNTS, 120.0), 3.0)
    0.5
    """
    return SECONDS_PER_MINUTE / tempo_at(tl, c)


def _clock_text(s: Seconds) -> str:
    """Writes seconds as m:ss.s, rounding tenths half away from zero and keeping the sign."""
    tenths = int(round_to(s * TENTHS_PER_SECOND, 1.0))
    sign = "-" if tenths < 0 else ""
    minutes, rest = divmod(abs(tenths), TENTHS_PER_MINUTE)
    whole, tenth = divmod(rest, TENTHS_PER_SECOND)
    return f"{sign}{minutes}:{whole:02d}.{tenth}"


def _plain_number(value: float) -> str:
    """Writes six significant digits, falling back to plain decimals where that is an exponent."""
    text = f"{value:g}"
    return format_number(value) if "e" in text else text


def describe_count(tl: Timeline, c: Count) -> str:
    """Renders a count as count, measure (when measures are on), m:ss.s and tempo.

    >>> from e8s.time.timeline import new_timeline
    >>> describe_count(new_timeline(TimelineMode.COUNTS, 120.0), 130.0)
    'count 130, 1:05.0, 120 cpm'
    """
    label = count_to_measure(tl, c)
    measure = "" if label is None else f" (m. {label.measure})"
    tempo = round(tempo_at(tl, c), TEMPO_DISPLAY_DIGITS)
    clock = _clock_text(count_to_seconds(tl, c))
    return f"count {_plain_number(c)}{measure}, {clock}, {_plain_number(tempo)} cpm"
