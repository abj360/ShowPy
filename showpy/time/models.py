#!/usr/bin/env python3

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

from enum import StrEnum

from showpy.core.types import Count, CountsPerMinute, FrozenModel, Seconds


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
        counts_per_measure: Measure length from count 0.
        start_measure: Number of the measure that starts at count 0.
        changes: (count, new length) pairs; each starts a new measure at its count.
    """

    counts_per_measure: float
    start_measure: int = 1
    changes: tuple[tuple[Count, float], ...] = ()


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

    Attributes:
        mode: How the show is timed.
        tempos: Tempo sections sorted by count; the first starts at count 0.
        holds: Pauses sorted by count.
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
