"""
timeline.py --- builds timelines, edits them purely, looks up markers and cues, persists to JSON

Contains:
    new_timeline(): starts a timeline, with a tempo at count 0 when one is given
    add_tempo(): sets a tempo section, replacing any at the same count
    remove_tempo(): drops the tempo section at a count
    add_hold(): sets a pause, replacing any at the same count
    remove_hold(): drops the pause at a count
    add_marker(): adds a uniquely labelled named moment
    remove_marker(): drops a named moment
    add_cue(): adds a uniquely labelled cue-mode moment
    remove_cue(): drops a cue
    set_measures(): turns measure numbering on
    clear_measures(): turns measure numbering off
    attach_audio(): adds a uniquely labelled playback track
    remove_audio(): drops a playback track
    set_sync(): moves where count 0 falls in one track
    marker_to_count(): resolves a marker label to its count
    counts_from_markers(): resolves marker labels to counts, keeping their order
    cue_to_seconds(): computes cue-mode start time from earlier cue durations
    to_json(): dumps a timeline to a JSON-safe dict
    from_json(): loads a timeline from a JSON-safe dict
"""

from collections.abc import Callable, Iterable, Sequence
from typing import Any

from showpy.core.errors import SchemaError, TimelineError, TimeRangeError, UnknownRefError
from showpy.core.types import Count, CountsPerMinute, Seconds
from showpy.time.models import (
    AudioTrack,
    Cue,
    Hold,
    Marker,
    MeasureSetting,
    TempoSection,
    Timeline,
    TimelineMode,
)


def _require_count(at_count: Count, what: str) -> None:
    if at_count < 0:
        raise TimeRangeError(
            f"{what} at negative count {at_count}",
            hint="counts start at 0, the show start",
            context={"what": what, "at_count": at_count},
        )


def _require_positive_tempo(cpm: CountsPerMinute) -> None:
    if not cpm > 0:
        raise TimelineError(
            f"tempo must be positive, got {cpm}",
            hint="give counts_per_minute above 0",
            context={"counts_per_minute": cpm},
        )


def _require_new_label(labels: Iterable[str], label: str, what: str) -> None:
    if label in set(labels):
        raise TimelineError(
            f"{what} {label!r} already exists",
            hint=f"pick another label or remove the existing {what} first",
            context={"what": what, "label": label},
        )


def _drop[T](
    items: tuple[T, ...], matches: Callable[[T], bool], what: str, key: object
) -> tuple[T, ...]:
    kept = tuple(item for item in items if not matches(item))
    if len(kept) == len(items):
        raise UnknownRefError(
            f"no {what} {key!r}",
            hint=f"remove one of the existing {what}s",
            context={"what": what, "key": key},
        )
    return kept


def new_timeline(
    mode: TimelineMode, counts_per_minute: CountsPerMinute | None = None, total: Count | None = None
) -> Timeline:
    """Starts a timeline; COUNTS mode needs a tempo, which becomes the section at count 0.

    Args:
        mode: How the show is timed.
        counts_per_minute: Starting tempo.
        total: Length of the show in counts, when known.

    Returns:
        timeline: Empty timeline with at most one tempo section.

    >>> new_timeline(TimelineMode.COUNTS, counts_per_minute=144.0).tempos[0].at_count
    0.0
    """
    if mode is TimelineMode.COUNTS and counts_per_minute is None:
        raise TimelineError(
            "a counts timeline needs a starting tempo",
            hint="pass counts_per_minute, e.g. new_timeline(TimelineMode.COUNTS, 144.0)",
            context={"mode": mode.value},
        )
    if total is not None and total < 0:
        raise TimeRangeError(
            f"total counts must not be negative, got {total}",
            hint="pass total >= 0 or None",
            context={"total": total},
        )
    tempos: tuple[TempoSection, ...] = ()
    if counts_per_minute is not None:
        _require_positive_tempo(counts_per_minute)
        tempos = (TempoSection(at_count=0.0, counts_per_minute=counts_per_minute),)
    return Timeline(mode=mode, tempos=tempos, total_counts=total)


def add_tempo(tl: Timeline, section: TempoSection) -> Timeline:
    """Sets a tempo section, replacing any section at the same count, keeping count order.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> tl = add_tempo(tl, TempoSection(at_count=16.0, counts_per_minute=90.0))
    >>> [s.at_count for s in tl.tempos]
    [0.0, 16.0]
    """
    _require_count(section.at_count, "tempo")
    _require_positive_tempo(section.counts_per_minute)
    others = [s for s in tl.tempos if s.at_count != section.at_count]
    tempos = tuple(sorted([*others, section], key=lambda s: s.at_count))
    return tl.model_copy(update={"tempos": tempos})


def remove_tempo(tl: Timeline, at_count: Count) -> Timeline:
    """Drops the tempo section that starts at `at_count`.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> tl = add_tempo(tl, TempoSection(at_count=8.0, counts_per_minute=90.0))
    >>> len(remove_tempo(tl, 8.0).tempos)
    1
    """
    tempos = _drop(tl.tempos, lambda s: s.at_count == at_count, "tempo section", at_count)
    return tl.model_copy(update={"tempos": tempos})


def add_hold(tl: Timeline, hold: Hold) -> Timeline:
    """Sets a pause, replacing any pause at the same count, keeping count order.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> add_hold(tl, Hold(at_count=8.0, seconds=2.0)).holds[0].seconds
    2.0
    """
    _require_count(hold.at_count, "hold")
    if hold.seconds < 0:
        raise TimeRangeError(
            f"hold at count {hold.at_count} has negative length {hold.seconds}",
            hint="give a hold of 0 seconds or more",
            context={"at_count": hold.at_count, "seconds": hold.seconds},
        )
    others = [h for h in tl.holds if h.at_count != hold.at_count]
    holds = tuple(sorted([*others, hold], key=lambda h: h.at_count))
    return tl.model_copy(update={"holds": holds})


def remove_hold(tl: Timeline, at_count: Count) -> Timeline:
    """Drops the pause at `at_count`.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> tl = add_hold(tl, Hold(at_count=8.0, seconds=2.0))
    >>> remove_hold(tl, 8.0).holds
    ()
    """
    holds = _drop(tl.holds, lambda h: h.at_count == at_count, "hold", at_count)
    return tl.model_copy(update={"holds": holds})


def add_marker(tl: Timeline, marker: Marker) -> Timeline:
    """Adds a named moment; labels are unique and markers stay in count order.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> add_marker(tl, Marker(label="hit", at_count=32.0)).markers[0].label
    'hit'
    """
    _require_count(marker.at_count, "marker")
    _require_new_label((m.label for m in tl.markers), marker.label, "marker")
    markers = tuple(sorted([*tl.markers, marker], key=lambda m: m.at_count))
    return tl.model_copy(update={"markers": markers})


def remove_marker(tl: Timeline, label: str) -> Timeline:
    """Drops the marker named `label`.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> tl = add_marker(tl, Marker(label="hit", at_count=32.0))
    >>> remove_marker(tl, "hit").markers
    ()
    """
    markers = _drop(tl.markers, lambda m: m.label == label, "marker", label)
    return tl.model_copy(update={"markers": markers})


def add_cue(tl: Timeline, cue: Cue) -> Timeline:
    """Adds a cue; labels are unique and cues stay in running order.

    >>> tl = new_timeline(TimelineMode.CUES)
    >>> add_cue(tl, Cue(label="go", order=1, estimated_seconds=5.0)).cues[0].label
    'go'
    """
    if cue.estimated_seconds < 0:
        raise TimeRangeError(
            f"cue {cue.label!r} has negative duration {cue.estimated_seconds}",
            hint="estimate 0 seconds or more",
            context={"label": cue.label, "estimated_seconds": cue.estimated_seconds},
        )
    _require_new_label((c.label for c in tl.cues), cue.label, "cue")
    cues = tuple(sorted([*tl.cues, cue], key=lambda c: c.order))
    return tl.model_copy(update={"cues": cues})


def remove_cue(tl: Timeline, label: str) -> Timeline:
    """Drops the cue named `label`.

    >>> tl = new_timeline(TimelineMode.CUES)
    >>> tl = add_cue(tl, Cue(label="go", order=1, estimated_seconds=5.0))
    >>> remove_cue(tl, "go").cues
    ()
    """
    cues = _drop(tl.cues, lambda c: c.label == label, "cue", label)
    return tl.model_copy(update={"cues": cues})


def set_measures(tl: Timeline, setting: MeasureSetting) -> Timeline:
    """Turns measure numbering on; every measure length must be positive.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> set_measures(tl, MeasureSetting(counts_per_measure=4.0)).measures
    MeasureSetting(counts_per_measure=4.0, start_measure=1, changes=())
    """
    lengths = [setting.counts_per_measure, *(length for _, length in setting.changes)]
    if not all(length > 0 for length in lengths):
        raise TimelineError(
            "measure lengths must be positive",
            hint="give counts_per_measure and every change length above 0",
            context={"lengths": lengths},
        )
    for at_count, _ in setting.changes:
        _require_count(at_count, "measure change")
    return tl.model_copy(update={"measures": setting})


def clear_measures(tl: Timeline) -> Timeline:
    """Turns measure numbering off.

    >>> clear_measures(new_timeline(TimelineMode.COUNTS, 120.0)).measures is None
    True
    """
    return tl.model_copy(update={"measures": None})


def attach_audio(tl: Timeline, track: AudioTrack) -> Timeline:
    """Adds a playback track; labels are unique. Audio never changes timing.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> attach_audio(tl, AudioTrack(file="a.wav", label="a")).audio[0].file
    'a.wav'
    """
    _require_new_label((t.label for t in tl.audio), track.label, "audio track")
    return tl.model_copy(update={"audio": (*tl.audio, track)})


def remove_audio(tl: Timeline, label: str) -> Timeline:
    """Drops the playback track named `label`.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> tl = attach_audio(tl, AudioTrack(file="a.wav", label="a"))
    >>> remove_audio(tl, "a").audio
    ()
    """
    audio = _drop(tl.audio, lambda t: t.label == label, "audio track", label)
    return tl.model_copy(update={"audio": audio})


def set_sync(tl: Timeline, label: str, offset_s: Seconds) -> Timeline:
    """Sets the audio time of track `label` that plays at count 0.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> tl = attach_audio(tl, AudioTrack(file="a.wav", label="a"))
    >>> set_sync(tl, "a", 2.5).audio[0].sync_offset_s
    2.5
    """
    if label not in {t.label for t in tl.audio}:
        raise _unknown("audio track", label, [t.label for t in tl.audio])
    audio = tuple(
        t.model_copy(update={"sync_offset_s": offset_s}) if t.label == label else t
        for t in tl.audio
    )
    return tl.model_copy(update={"audio": audio})


def _unknown(what: str, label: str, known: Sequence[str]) -> UnknownRefError:
    return UnknownRefError(
        f"no {what} {label!r}",
        hint=f"use one of: {', '.join(known)}" if known else f"add a {what} first",
        context={"what": what, "label": label},
    )


def marker_to_count(tl: Timeline, label: str) -> Count:
    """Resolves a marker label to its count.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> marker_to_count(add_marker(tl, Marker(label="B", at_count=64.0)), "B")
    64.0
    """
    for marker in tl.markers:
        if marker.label == label:
            return marker.at_count
    raise _unknown("marker", label, [m.label for m in tl.markers])


def counts_from_markers(tl: Timeline, labels: Sequence[str]) -> list[Count]:
    """Resolves marker labels to counts in the order given, e.g. to place set boundaries.

    >>> tl = new_timeline(TimelineMode.COUNTS, 120.0)
    >>> tl = add_marker(tl, Marker(label="A", at_count=32.0))
    >>> counts_from_markers(tl, ["A"])
    [32.0]
    """
    return [marker_to_count(tl, label) for label in labels]


def cue_to_seconds(tl: Timeline, label: str) -> Seconds:
    """Computes when a cue starts: the summed estimates of every cue with a lower order.

    >>> tl = new_timeline(TimelineMode.CUES)
    >>> tl = add_cue(tl, Cue(label="a", order=1, estimated_seconds=10.0))
    >>> cue_to_seconds(add_cue(tl, Cue(label="b", order=2, estimated_seconds=3.0)), "b")
    10.0
    """
    target = next((c for c in tl.cues if c.label == label), None)
    if target is None:
        raise _unknown("cue", label, [c.label for c in tl.cues])
    return sum((c.estimated_seconds for c in tl.cues if c.order < target.order), 0.0)


# Any: a JSON document holds arbitrarily nested JSON values.
def to_json(tl: Timeline) -> dict[str, Any]:
    """Dumps a timeline to a JSON-safe dict.

    >>> to_json(new_timeline(TimelineMode.CUES))["mode"]
    'cues'
    """
    return tl.model_dump(mode="json")


# Any: a JSON document holds arbitrarily nested JSON values.
def from_json(d: dict[str, Any]) -> Timeline:
    """Loads a timeline from a JSON-safe dict, raising SchemaError when it does not fit.

    >>> from_json({"mode": "cues"}).mode
    <TimelineMode.CUES: 'cues'>
    """
    try:
        return Timeline.model_validate(d)
    except SchemaError as exc:
        raise SchemaError(
            "timeline JSON does not match the Timeline schema",
            hint="pass a dict produced by to_json()",
            context={"errors": exc.context["errors"]},
        ) from exc
