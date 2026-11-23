"""
models.py --- frozen timeline data classes: tempo, holds, markers, measures, cues, audio

Contains:
    TimelineMode: names how a show is timed: counts, seconds or cues
    MarkerKind: classifies a marker as rehearsal, cue or custom
    TempoSection: sets the tempo from a count onward, with an optional linear ramp
    Hold: pauses the counts for a number of seconds at one count
    Marker: names one moment on the timeline
    MeasureSetting: configures optional measure numbering and its length changes
    MeasureLabel: holds the 1-based display label of a count
    Cue: describes one cue-mode moment with an estimated duration
    AudioTrack: describes playback audio and its sync point; never changes timing
    Timeline: holds the whole show timeline
"""

from collections import Counter
from enum import StrEnum
from functools import cached_property
from itertools import pairwise
from typing import TYPE_CHECKING, Self

from pydantic import model_validator

from e8s.core.types import (
    Count,
    CountsPerMinute,
    FrozenModel,
    NonNegCount,
    PositiveFinite,
    Seconds,
)

if TYPE_CHECKING:
    from e8s.time.clock import CompiledClock


class TimelineMode(StrEnum):
    """Names how a show is timed."""

    COUNTS = "counts"
    SECONDS = "seconds"
    CUES = "cues"


class MarkerKind(StrEnum):
    """Classifies a marker."""

    REHEARSAL = "rehearsal"
    CUE = "cue"
    CUSTOM = "custom"


class TempoSection(FrozenModel):
    """Sets how fast counts go from a count onward.

    Attributes:
        at_count: Count where the section starts.
        counts_per_minute: Tempo at the start of the section.
        ramp_to: Tempo reached at the end of the ramp, or None for a steady tempo.
        ramp_counts: Counts the ramp takes, changing tempo linearly in counts.
    """

    at_count: Count
    counts_per_minute: CountsPerMinute
    ramp_to: CountsPerMinute | None = None
    ramp_counts: Count | None = None

    @model_validator(mode="after")
    def _require_complete_ramp(self) -> Self:
        if (self.ramp_to is None) != (self.ramp_counts is None):
            raise ValueError("a ramp needs both ramp_to and ramp_counts, or neither")
        if self.ramp_counts is not None and not self.ramp_counts > 0:
            raise ValueError(f"ramp_counts must be above 0, got {self.ramp_counts}")
        return self


class Hold(FrozenModel):
    """Pauses the counts: time reaches `at_count`, waits `seconds`, then continues.

    Attributes:
        at_count: Count where the pause happens.
        seconds: Length of the pause.
        note: Designer note such as "fermata".
    """

    at_count: Count
    seconds: Seconds
    note: str | None = None


class Marker(FrozenModel):
    """Names one moment, such as "the hit" or "letter B".

    Attributes:
        label: Unique name of the moment.
        at_count: Count where it happens.
        kind: What the marker is for.
    """

    label: str
    at_count: Count
    kind: MarkerKind = MarkerKind.CUSTOM


class MeasureSetting(FrozenModel):
    """Configures measure numbering for designers who talk in measures.

    Attributes:
        counts_per_measure: Measure length from count 0; finite and above 0.
        start_measure: Number of the measure that starts at count 0.
        changes: (count, new length) pairs; each starts a new measure at its count. Counts are
            at least 0 and strictly increasing, lengths finite and above 0.
    """

    counts_per_measure: PositiveFinite
    start_measure: int = 1
    changes: tuple[tuple[NonNegCount, PositiveFinite], ...] = ()

    @model_validator(mode="after")
    def _require_increasing_changes(self) -> Self:
        counts = [at_count for at_count, _ in self.changes]
        if any(after <= before for before, after in pairwise(counts)):
            raise ValueError(f"measure change counts {counts} must increase, one change per count")
        return self


class MeasureLabel(FrozenModel):
    """Holds the display label of a count, e.g. m. 33, count 1.

    Attributes:
        measure: Measure number.
        count_in_measure: 1-based position inside the measure.
    """

    measure: int
    count_in_measure: float


class Cue(FrozenModel):
    """Describes one moment in cue mode.

    Attributes:
        label: Unique name of the cue.
        order: Position in the running order.
        estimated_seconds: Estimated duration, so previews can play.
    """

    label: str
    order: int
    estimated_seconds: Seconds


class AudioTrack(FrozenModel):
    """Describes music or sound that plays alongside the show.

    Attributes:
        file: Path or URI of the audio asset.
        label: Unique name of the track.
        sync_offset_s: Audio time that plays at count 0.
        gain: Linear playback gain.
        section: (start, end) counts the track covers, or None for the whole show.
    """

    file: str
    label: str
    sync_offset_s: Seconds = 0.0
    gain: float = 1.0
    section: tuple[Count, Count] | None = None


class Timeline(FrozenModel):
    """Holds the whole show timeline.

    Construction refuses a timeline the clock could not convert: tempo sections out of order,
    two at one count or a first that is not at count 0; two cues with one order; holds out of
    order, two at one count or outside the show; a negative `total_counts`.

    Attributes:
        mode: How the show is timed.
        tempos: Tempo sections sorted by count; the first starts at count 0.
        holds: Pauses sorted by count, at most one per count, inside the show.
        markers: Named moments sorted by count.
        measures: Measure numbering, or None when measures are off.
        cues: Cue-mode moments sorted by order.
        audio: Playback tracks.
        total_counts: Length of the show in counts, when known.
    """

    mode: TimelineMode
    tempos: tuple[TempoSection, ...] = ()
    holds: tuple[Hold, ...] = ()
    markers: tuple[Marker, ...] = ()
    measures: MeasureSetting | None = None
    cues: tuple[Cue, ...] = ()
    audio: tuple[AudioTrack, ...] = ()
    total_counts: Count | None = None

    @model_validator(mode="after")
    def _require_valid_timeline(self) -> Self:
        self._require_tempo_map()
        self._require_holds()
        repeated = sorted(
            order for order, seen in Counter(c.order for c in self.cues).items() if seen > 1
        )
        if repeated:
            raise ValueError(f"cues share the order {repeated}; give each cue its own order")
        if self.total_counts is not None and self.total_counts < 0:
            raise ValueError(f"total_counts must not be negative, got {self.total_counts}")
        return self

    def _require_tempo_map(self) -> None:
        counts = [s.at_count for s in self.tempos]
        if counts and counts[0] != 0.0:
            raise ValueError(f"the first tempo starts at count {counts[0]}, not 0")
        if any(after <= before for before, after in pairwise(counts)):
            raise ValueError(f"tempo counts {counts} must increase, one section per count")

    def _require_holds(self) -> None:
        counts = [h.at_count for h in self.holds]
        if any(after <= before for before, after in pairwise(counts)):
            raise ValueError(f"hold counts {counts} must increase, one hold per count")
        end = self.total_counts
        outside = [c for c in counts if c < 0 or (end is not None and c > end)]
        if outside:
            raise ValueError(f"holds at counts {outside} are outside the show")

    @cached_property
    def clock(self) -> "CompiledClock":
        """Returns the compiled clock of this timeline, built once and reused by every lookup."""
        from e8s.time.clock import compile_clock

        return compile_clock(self)
