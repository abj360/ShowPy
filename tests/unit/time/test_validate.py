#!/usr/bin/env python3

"""
test_validate.py --- tests that validate_timeline reports each structural problem by code

Contains:
    test_*(): one behavior of showpy.time.validate each
    test_regression_validate_flags_negative_total_counts: rejects or reports a negative show length
    test_regression_validate_flags_nan_hold: rejects or reports a hold of NaN seconds
    test_regression_validate_flags_unsorted_holds: rejects or reports holds that are not sorted by
        count
    test_regression_validate_flags_a_hold_past_the_end: rejects or reports a hold after the last
        count
    test_regression_validate_flags_duplicate_cue_orders: rejects or reports two cues sharing one
        order
    test_regression_validate_flags_nan_gain: rejects or reports an audio track with NaN gain
    test_regression_validate_flags_infinite_sync_offset: rejects or reports an audio track with an
        infinite sync offset
    test_regression_overlapping_holds_have_one_policy: makes the clock reject overlapping holds
        exactly when validation does
"""

import math
from collections.abc import Callable
from typing import Any

import pytest
from pydantic import ValidationError as PydanticValidationError

from showpy.core.errors import ShowPyError
from showpy.time.clock import count_to_seconds
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
from showpy.time.timeline import new_timeline
from showpy.time.validate import validate_timeline


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


REJECTED = (ShowPyError, PydanticValidationError)
BASE_TEMPO = TempoSection(at_count=0.0, counts_per_minute=120.0)


def _rejected_or_flagged(build: Callable[[], Timeline]) -> None:
    try:
        timeline = build()
    except REJECTED:
        return
    assert not validate_timeline(timeline).ok


# Any: fields are arbitrary Timeline fields, validated by the model under test.
def _counts_with(**fields: Any) -> Timeline:
    return Timeline(mode=TimelineMode.COUNTS, tempos=(BASE_TEMPO,), **fields)


@pytest.mark.defect("UP-9.3-114")
@pytest.mark.xfail(strict=True, reason="UP-9.3-114")
def test_regression_validate_flags_negative_total_counts() -> None:
    """Rejects or reports a negative show length (audit 9.3)."""
    _rejected_or_flagged(lambda: _counts_with(total_counts=-5.0))


@pytest.mark.defect("UP-9.3-115")
@pytest.mark.xfail(strict=True, reason="UP-9.3-115")
def test_regression_validate_flags_nan_hold() -> None:
    """Rejects or reports a hold of NaN seconds (audit 9.3)."""
    _rejected_or_flagged(lambda: _counts_with(holds=(Hold(at_count=4.0, seconds=math.nan),)))


@pytest.mark.defect("UP-9.3-116")
@pytest.mark.xfail(strict=True, reason="UP-9.3-116")
def test_regression_validate_flags_unsorted_holds() -> None:
    """Rejects or reports holds that are not sorted by count (audit 9.3)."""
    holds = (Hold(at_count=8.0, seconds=1.0), Hold(at_count=4.0, seconds=1.0))
    _rejected_or_flagged(lambda: _counts_with(holds=holds))


@pytest.mark.defect("UP-9.3-117")
@pytest.mark.xfail(strict=True, reason="UP-9.3-117")
def test_regression_validate_flags_a_hold_past_the_end() -> None:
    """Rejects or reports a hold after the last count (audit 9.3)."""
    _rejected_or_flagged(
        lambda: _counts_with(total_counts=10.0, holds=(Hold(at_count=20.0, seconds=1.0),))
    )


@pytest.mark.defect("UP-9.3-118")
@pytest.mark.xfail(strict=True, reason="UP-9.3-118")
def test_regression_validate_flags_duplicate_cue_orders() -> None:
    """Rejects or reports two cues sharing one order (audit 9.3)."""
    cues = (
        Cue(label="a", order=1, estimated_seconds=2.0),
        Cue(label="b", order=1, estimated_seconds=3.0),
    )
    _rejected_or_flagged(lambda: Timeline(mode=TimelineMode.CUES, cues=cues))


@pytest.mark.defect("UP-9.3-119")
@pytest.mark.xfail(strict=True, reason="UP-9.3-119")
def test_regression_validate_flags_nan_gain() -> None:
    """Rejects or reports an audio track with NaN gain (audit 9.3)."""
    track = AudioTrack(file="a.wav", label="a", gain=math.nan)
    _rejected_or_flagged(lambda: _counts_with(audio=(track,)))


@pytest.mark.defect("UP-9.3-120")
@pytest.mark.xfail(strict=True, reason="UP-9.3-120")
def test_regression_validate_flags_infinite_sync_offset() -> None:
    """Rejects or reports an audio track with an infinite sync offset (audit 9.3)."""
    track = AudioTrack(file="a.wav", label="a", sync_offset_s=math.inf)
    _rejected_or_flagged(lambda: _counts_with(audio=(track,)))


@pytest.mark.defect("UP-9.3-121")
@pytest.mark.xfail(strict=True, reason="UP-9.3-121")
def test_regression_overlapping_holds_have_one_policy() -> None:
    """Makes the clock reject overlapping holds exactly when validation does (audit 9.3)."""
    holds = (Hold(at_count=4.0, seconds=1.0), Hold(at_count=4.0, seconds=2.0))
    try:
        timeline = _counts_with(holds=holds)
    except REJECTED:
        return
    clock_accepts = True
    try:
        count_to_seconds(timeline, 8.0)
    except ShowPyError:
        clock_accepts = False
    assert clock_accepts == validate_timeline(timeline).ok
