#!/usr/bin/env python3

"""
test_timeline.py --- tests timeline construction, pure edits, lookups and JSON persistence
"""

import pytest

from stage.core.errors import SchemaError, TimelineError, TimeRangeError, UnknownRefError
from stage.time.models import (
    AudioTrack,
    Cue,
    Hold,
    Marker,
    MarkerKind,
    MeasureSetting,
    TempoSection,
    Timeline,
    TimelineMode,
)
from stage.time.timeline import (
    add_cue,
    add_hold,
    add_marker,
    add_tempo,
    attach_audio,
    clear_measures,
    counts_from_markers,
    cue_to_seconds,
    from_json,
    marker_to_count,
    new_timeline,
    remove_audio,
    remove_cue,
    remove_hold,
    remove_marker,
    remove_tempo,
    set_measures,
    set_sync,
    to_json,
)


def _counts(cpm: float = 120.0) -> Timeline:
    return new_timeline(TimelineMode.COUNTS, counts_per_minute=cpm)


def test_new_counts_timeline_starts_with_tempo_at_zero() -> None:
    tl = new_timeline(TimelineMode.COUNTS, counts_per_minute=144.0, total=256.0)
    assert tl.tempos == (TempoSection(at_count=0.0, counts_per_minute=144.0),)
    assert tl.total_counts == 256.0


def test_new_counts_timeline_requires_tempo() -> None:
    with pytest.raises(TimelineError) as info:
        new_timeline(TimelineMode.COUNTS)
    assert "counts_per_minute" in info.value.hint


def test_new_timeline_rejects_non_positive_tempo() -> None:
    with pytest.raises(TimelineError):
        new_timeline(TimelineMode.COUNTS, counts_per_minute=0.0)


def test_new_timeline_rejects_negative_total() -> None:
    with pytest.raises(TimeRangeError):
        new_timeline(TimelineMode.SECONDS, total=-1.0)


def test_new_seconds_and_cue_timelines_need_no_tempo() -> None:
    assert new_timeline(TimelineMode.SECONDS).tempos == ()
    assert new_timeline(TimelineMode.CUES).tempos == ()


def test_add_tempo_keeps_sections_sorted_and_does_not_mutate() -> None:
    base = _counts()
    tl = add_tempo(base, TempoSection(at_count=64.0, counts_per_minute=96.0))
    tl = add_tempo(tl, TempoSection(at_count=32.0, counts_per_minute=160.0))
    assert [s.at_count for s in tl.tempos] == [0.0, 32.0, 64.0]
    assert len(base.tempos) == 1


def test_add_tempo_replaces_section_at_same_count() -> None:
    tl = add_tempo(_counts(), TempoSection(at_count=0.0, counts_per_minute=90.0))
    assert tl.tempos == (TempoSection(at_count=0.0, counts_per_minute=90.0),)


def test_add_tempo_rejects_negative_count() -> None:
    with pytest.raises(TimeRangeError):
        add_tempo(_counts(), TempoSection(at_count=-1.0, counts_per_minute=90.0))


def test_remove_tempo_drops_section() -> None:
    tl = add_tempo(_counts(), TempoSection(at_count=16.0, counts_per_minute=90.0))
    assert [s.at_count for s in remove_tempo(tl, 16.0).tempos] == [0.0]


def test_remove_tempo_unknown_count_raises() -> None:
    with pytest.raises(UnknownRefError):
        remove_tempo(_counts(), 8.0)


def test_add_hold_sorts_and_replaces_same_count() -> None:
    tl = add_hold(_counts(), Hold(at_count=16.0, seconds=2.0))
    tl = add_hold(tl, Hold(at_count=8.0, seconds=1.0))
    tl = add_hold(tl, Hold(at_count=16.0, seconds=3.0, note="fermata"))
    assert [(h.at_count, h.seconds) for h in tl.holds] == [(8.0, 1.0), (16.0, 3.0)]


def test_add_hold_rejects_negative_seconds_and_counts() -> None:
    with pytest.raises(TimeRangeError):
        add_hold(_counts(), Hold(at_count=4.0, seconds=-1.0))
    with pytest.raises(TimeRangeError):
        add_hold(_counts(), Hold(at_count=-4.0, seconds=1.0))


def test_remove_hold() -> None:
    tl = add_hold(_counts(), Hold(at_count=4.0, seconds=1.0))
    assert remove_hold(tl, 4.0).holds == ()
    with pytest.raises(UnknownRefError):
        remove_hold(tl, 5.0)


def test_add_marker_rejects_duplicate_label() -> None:
    tl = add_marker(_counts(), Marker(label="hit", at_count=32.0))
    with pytest.raises(TimelineError) as info:
        add_marker(tl, Marker(label="hit", at_count=48.0))
    assert info.value.context["label"] == "hit"


def test_add_marker_rejects_negative_count() -> None:
    with pytest.raises(TimeRangeError):
        add_marker(_counts(), Marker(label="early", at_count=-2.0))


def test_markers_are_kept_in_count_order() -> None:
    tl = add_marker(_counts(), Marker(label="B", at_count=64.0))
    tl = add_marker(tl, Marker(label="A", at_count=32.0, kind=MarkerKind.REHEARSAL))
    assert [m.label for m in tl.markers] == ["A", "B"]


def test_remove_marker() -> None:
    tl = add_marker(_counts(), Marker(label="hit", at_count=32.0))
    assert remove_marker(tl, "hit").markers == ()
    with pytest.raises(UnknownRefError):
        remove_marker(tl, "miss")


def test_marker_lookups() -> None:
    tl = add_marker(_counts(), Marker(label="A", at_count=32.0))
    tl = add_marker(tl, Marker(label="B", at_count=64.0))
    assert marker_to_count(tl, "B") == 64.0
    assert counts_from_markers(tl, ["B", "A"]) == [64.0, 32.0]
    with pytest.raises(UnknownRefError) as info:
        marker_to_count(tl, "C")
    assert "A" in info.value.hint


def test_cues_sort_by_order_and_reject_duplicates() -> None:
    tl = new_timeline(TimelineMode.CUES)
    tl = add_cue(tl, Cue(label="blackout", order=3, estimated_seconds=5.0))
    tl = add_cue(tl, Cue(label="lights up", order=1, estimated_seconds=10.0))
    assert [c.label for c in tl.cues] == ["lights up", "blackout"]
    with pytest.raises(TimelineError):
        add_cue(tl, Cue(label="blackout", order=4, estimated_seconds=1.0))


def test_add_cue_rejects_negative_duration() -> None:
    with pytest.raises(TimeRangeError):
        add_cue(new_timeline(TimelineMode.CUES), Cue(label="x", order=1, estimated_seconds=-1.0))


def test_cue_to_seconds_sums_earlier_cues() -> None:
    tl = new_timeline(TimelineMode.CUES)
    tl = add_cue(tl, Cue(label="a", order=1, estimated_seconds=10.0))
    tl = add_cue(tl, Cue(label="b", order=2, estimated_seconds=4.5))
    tl = add_cue(tl, Cue(label="c", order=5, estimated_seconds=2.0))
    assert cue_to_seconds(tl, "a") == 0.0
    assert cue_to_seconds(tl, "c") == 14.5


def test_remove_cue_and_unknown_cue() -> None:
    tl = add_cue(new_timeline(TimelineMode.CUES), Cue(label="a", order=1, estimated_seconds=1.0))
    assert remove_cue(tl, "a").cues == ()
    with pytest.raises(UnknownRefError):
        remove_cue(tl, "b")
    with pytest.raises(UnknownRefError):
        cue_to_seconds(tl, "b")


def test_set_and_clear_measures() -> None:
    tl = set_measures(_counts(), MeasureSetting(counts_per_measure=4.0))
    assert tl.measures == MeasureSetting(counts_per_measure=4.0)
    assert clear_measures(tl).measures is None


def test_set_measures_rejects_non_positive_length() -> None:
    with pytest.raises(TimelineError):
        set_measures(_counts(), MeasureSetting(counts_per_measure=0.0))
    with pytest.raises(TimelineError):
        set_measures(_counts(), MeasureSetting(counts_per_measure=4.0, changes=((8.0, -2.0),)))


def test_attach_audio_rejects_duplicate_label_and_remove_works() -> None:
    track = AudioTrack(file="opener.wav", label="opener")
    tl = attach_audio(_counts(), track)
    with pytest.raises(TimelineError):
        attach_audio(tl, track)
    assert remove_audio(tl, "opener").audio == ()
    with pytest.raises(UnknownRefError):
        remove_audio(tl, "closer")


def test_set_sync_changes_only_named_track() -> None:
    tl = attach_audio(_counts(), AudioTrack(file="a.wav", label="a"))
    tl = attach_audio(tl, AudioTrack(file="b.wav", label="b", sync_offset_s=1.0))
    synced = set_sync(tl, "a", 2.5)
    assert [t.sync_offset_s for t in synced.audio] == [2.5, 1.0]
    assert tl.audio[0].sync_offset_s == 0.0
    with pytest.raises(UnknownRefError):
        set_sync(tl, "c", 1.0)


def test_json_round_trip_preserves_everything() -> None:
    tl = add_tempo(
        _counts(),
        TempoSection(at_count=16.0, counts_per_minute=96.0, ramp_to=120.0, ramp_counts=8.0),
    )
    tl = add_hold(tl, Hold(at_count=24.0, seconds=2.0, note="freeze"))
    tl = add_marker(tl, Marker(label="hit", at_count=32.0, kind=MarkerKind.CUE))
    tl = set_measures(tl, MeasureSetting(counts_per_measure=4.0, changes=((16.0, 3.0),)))
    tl = attach_audio(tl, AudioTrack(file="a.wav", label="a", section=(0.0, 64.0)))
    payload = to_json(tl)
    assert payload["mode"] == "counts"
    assert from_json(payload) == tl


def test_from_json_rejects_bad_payload_with_schema_error() -> None:
    with pytest.raises(SchemaError) as info:
        from_json({"mode": "dance"})
    assert "mode" in str(info.value.context)
