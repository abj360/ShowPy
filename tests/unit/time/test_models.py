#!/usr/bin/env python3

"""
test_models.py --- tests timeline data class defaults, immutability and JSON safety
"""

import pydantic
import pytest

from showpy.core.errors import SchemaError
from showpy.time.models import (
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


def test_enum_values_are_lowercase_strings() -> None:
    assert TimelineMode.COUNTS.value == "counts"
    assert MarkerKind.REHEARSAL.value == "rehearsal"


def test_defaults_are_empty_and_optional() -> None:
    tl = Timeline(mode=TimelineMode.COUNTS)
    assert tl.tempos == ()
    assert tl.holds == ()
    assert tl.measures is None
    assert tl.total_counts is None


def test_marker_defaults_to_custom_kind() -> None:
    assert Marker(label="hit", at_count=8.0).kind is MarkerKind.CUSTOM


def test_tempo_section_has_no_ramp_by_default() -> None:
    section = TempoSection(at_count=0.0, counts_per_minute=120.0)
    assert section.ramp_to is None
    assert section.ramp_counts is None


def test_audio_track_defaults() -> None:
    track = AudioTrack(file="opener.wav", label="opener")
    assert track.sync_offset_s == 0.0
    assert track.gain == 1.0
    assert track.section is None


def test_models_are_frozen() -> None:
    hold = Hold(at_count=4.0, seconds=2.0)
    with pytest.raises(pydantic.ValidationError):
        hold.seconds = 3.0  # type: ignore[misc]


def test_measure_changes_round_trip_through_json() -> None:
    setting = MeasureSetting(counts_per_measure=4.0, changes=((16.0, 3.0),))
    restored = MeasureSetting.model_validate_json(setting.model_dump_json())
    assert restored == setting


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(SchemaError):
        Hold.model_validate({"at_count": 1.0, "seconds": 1.0, "colour": "red"})


def _counts(holds: tuple[Hold, ...] = (), total_counts: float | None = None) -> Timeline:
    tempos = (TempoSection(at_count=0.0, counts_per_minute=120.0),)
    return Timeline(mode=TimelineMode.COUNTS, tempos=tempos, holds=holds, total_counts=total_counts)


@pytest.mark.parametrize(
    "tempos",
    [
        (
            TempoSection(at_count=0.0, counts_per_minute=120.0),
            TempoSection(at_count=16.0, counts_per_minute=90.0),
            TempoSection(at_count=8.0, counts_per_minute=60.0),
        ),
        (
            TempoSection(at_count=0.0, counts_per_minute=120.0),
            TempoSection(at_count=0.0, counts_per_minute=90.0),
        ),
        (TempoSection(at_count=4.0, counts_per_minute=120.0),),
    ],
    ids=["unsorted", "duplicate-count", "first-not-at-zero"],
)
def test_regression_timeline_refuses_a_bad_tempo_map(tempos: tuple[TempoSection, ...]) -> None:
    with pytest.raises(SchemaError):
        Timeline(mode=TimelineMode.COUNTS, tempos=tempos)


@pytest.mark.parametrize(
    "fields",
    [{"ramp_to": 90.0}, {"ramp_counts": 4.0}, {"ramp_to": 90.0, "ramp_counts": 0.0}],
    ids=["no-length", "no-target", "zero-length"],
)
def test_regression_tempo_section_refuses_a_bad_ramp(fields: dict[str, float]) -> None:
    with pytest.raises(SchemaError):
        TempoSection(at_count=0.0, counts_per_minute=120.0, **fields)


def test_regression_timeline_refuses_bad_holds() -> None:
    one, two = Hold(at_count=4.0, seconds=1.0), Hold(at_count=8.0, seconds=1.0)
    with pytest.raises(SchemaError):
        _counts(holds=(two, one))
    with pytest.raises(SchemaError):
        _counts(holds=(one, Hold(at_count=4.0, seconds=2.0)))
    with pytest.raises(SchemaError):
        _counts(holds=(Hold(at_count=-1.0, seconds=1.0),))
    with pytest.raises(SchemaError):
        _counts(total_counts=6.0, holds=(one, two))
    assert _counts(total_counts=8.0, holds=(one, two)).holds == (one, two)


def test_regression_timeline_refuses_a_repeated_cue_order_and_a_negative_length() -> None:
    cues = (
        Cue(label="a", order=1, estimated_seconds=1.0),
        Cue(label="b", order=1, estimated_seconds=1.0),
    )
    with pytest.raises(SchemaError):
        Timeline(mode=TimelineMode.CUES, cues=cues)
    with pytest.raises(SchemaError):
        _counts(total_counts=-1.0)
