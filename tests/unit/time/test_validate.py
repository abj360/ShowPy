#!/usr/bin/env python3

"""
test_validate.py --- tests that validate_timeline reports each structural problem by code
"""

from stage.time.models import (
    AudioTrack,
    Cue,
    Hold,
    Marker,
    MeasureSetting,
    TempoSection,
    Timeline,
    TimelineMode,
)
from stage.time.timeline import new_timeline
from stage.time.validate import validate_timeline


def _codes(tl: Timeline) -> list[str]:
    return [issue.code for issue in validate_timeline(tl).issues]


def _tempo(at: float, cpm: float = 120.0, **ramp: float) -> TempoSection:
    return TempoSection(at_count=at, counts_per_minute=cpm, **ramp)


def _counts(**fields: object) -> Timeline:
    base = new_timeline(TimelineMode.COUNTS, counts_per_minute=120.0, total=64.0)
    return base.model_copy(update=fields)


def test_clean_timeline_is_ok_with_no_issues() -> None:
    tl = _counts(
        tempos=(_tempo(0.0, ramp_to=140.0, ramp_counts=8.0), _tempo(16.0, 90.0)),
        holds=(Hold(at_count=8.0, seconds=1.0),),
        markers=(Marker(label="hit", at_count=64.0),),
        audio=(AudioTrack(file="a.wav", label="a", section=(0.0, 64.0)),),
    )
    report = validate_timeline(tl)
    assert report.ok
    assert report.issues == ()


def test_counts_mode_without_tempo_is_an_error() -> None:
    report = validate_timeline(Timeline(mode=TimelineMode.COUNTS))
    assert not report.ok
    assert _codes(Timeline(mode=TimelineMode.COUNTS)) == ["TEMPO_MISSING"]


def test_cue_mode_without_tempo_is_fine() -> None:
    assert _codes(new_timeline(TimelineMode.CUES)) == []


def test_first_tempo_must_start_at_zero() -> None:
    assert _codes(_counts(tempos=(_tempo(4.0),))) == ["TEMPO_START"]


def test_tempos_must_be_strictly_ordered() -> None:
    assert _codes(_counts(tempos=(_tempo(0.0), _tempo(16.0), _tempo(8.0)))) == ["TEMPO_ORDER"]
    assert _codes(_counts(tempos=(_tempo(0.0), _tempo(8.0), _tempo(8.0)))) == ["TEMPO_ORDER"]


def test_tempos_must_be_positive() -> None:
    assert _codes(_counts(tempos=(_tempo(0.0, 0.0),))) == ["TEMPO_NONPOSITIVE"]
    assert _codes(_counts(tempos=(_tempo(0.0, ramp_to=-5.0, ramp_counts=4.0),))) == [
        "TEMPO_NONPOSITIVE"
    ]


def test_ramp_must_be_fully_specified_with_positive_length() -> None:
    assert _codes(_counts(tempos=(_tempo(0.0, ramp_to=140.0),))) == ["RAMP_INCOMPLETE"]
    assert _codes(_counts(tempos=(_tempo(0.0, ramp_counts=4.0),))) == ["RAMP_INCOMPLETE"]
    assert _codes(_counts(tempos=(_tempo(0.0, ramp_to=140.0, ramp_counts=0.0),))) == ["RAMP_LENGTH"]


def test_ramp_running_into_next_section_is_a_warning() -> None:
    tl = _counts(tempos=(_tempo(0.0, ramp_to=140.0, ramp_counts=16.0), _tempo(8.0)))
    report = validate_timeline(tl)
    assert report.ok
    assert _codes(tl) == ["RAMP_CUT"]


def test_holds_must_not_overlap_or_go_negative() -> None:
    same = (Hold(at_count=8.0, seconds=1.0), Hold(at_count=8.0, seconds=2.0))
    assert _codes(_counts(holds=same)) == ["HOLD_OVERLAP"]
    assert _codes(_counts(holds=(Hold(at_count=8.0, seconds=-1.0),))) == ["HOLD_NEGATIVE"]
    assert _codes(_counts(holds=(Hold(at_count=-1.0, seconds=1.0),))) == ["HOLD_NEGATIVE"]


def test_markers_must_be_within_show_and_unique() -> None:
    late = (Marker(label="end", at_count=65.0),)
    assert _codes(_counts(markers=late)) == ["MARKER_RANGE"]
    early = (Marker(label="pre", at_count=-1.0),)
    assert _codes(_counts(markers=early)) == ["MARKER_RANGE"]
    twice = (Marker(label="a", at_count=1.0), Marker(label="a", at_count=2.0))
    assert _codes(_counts(markers=twice)) == ["MARKER_DUPLICATE"]


def test_markers_without_total_only_need_non_negative_counts() -> None:
    tl = _counts(total_counts=None, markers=(Marker(label="far", at_count=1e6),))
    assert _codes(tl) == []


def test_audio_sections_must_be_ordered_and_in_range() -> None:
    backwards = AudioTrack(file="a.wav", label="a", section=(32.0, 16.0))
    assert _codes(_counts(audio=(backwards,))) == ["AUDIO_RANGE"]
    beyond = AudioTrack(file="a.wav", label="a", section=(0.0, 80.0))
    assert _codes(_counts(audio=(beyond,))) == ["AUDIO_RANGE"]
    twice = (AudioTrack(file="a.wav", label="a"), AudioTrack(file="b.wav", label="a"))
    assert _codes(_counts(audio=twice)) == ["AUDIO_DUPLICATE"]


def test_cue_labels_must_be_unique_and_durations_non_negative() -> None:
    twice = (
        Cue(label="a", order=1, estimated_seconds=1.0),
        Cue(label="a", order=2, estimated_seconds=1.0),
    )
    assert _codes(_counts(cues=twice)) == ["CUE_DUPLICATE"]
    negative = (Cue(label="a", order=1, estimated_seconds=-1.0),)
    assert _codes(_counts(cues=negative)) == ["CUE_NEGATIVE"]


def test_measures_must_have_positive_lengths() -> None:
    bad = MeasureSetting(counts_per_measure=4.0, changes=((8.0, 0.0),))
    assert _codes(_counts(measures=bad)) == ["MEASURE_LENGTH"]


def test_issue_location_names_the_item() -> None:
    issue = validate_timeline(_counts(markers=(Marker(label="end", at_count=99.0),))).issues[0]
    assert issue.where == "marker 'end'"
    assert issue.level == "error"
