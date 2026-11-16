#!/usr/bin/env python3

"""
test_models.py --- tests timeline data class defaults, immutability and JSON safety
"""

import pydantic
import pytest

from showpy.core.errors import SchemaError
from showpy.time.models import (
    AudioTrack,
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
