#!/usr/bin/env python3

"""
test_migrate_timeline.py --- tests the timeline sub-step of the v1 to v2 format migration

Contains:
    test_*(): one repair or refusal of migrate_timeline each
"""

import copy
from typing import Any

import pytest

from showpy.core.caps import MIN_TEMPO
from showpy.core.errors import SchemaError
from showpy.core.json_types import JsonValue
from showpy.show.document_paths import TIMELINE_DOC
from showpy.show.migrate import ReportBuilder
from showpy.show.migrations.v1_to_v2_timeline import TIMELINE_DOC as STEP_PATH
from showpy.show.migrations.v1_to_v2_timeline import migrate_timeline
from showpy.time.models import Timeline


def _tempo(at: float, cpm: float = 120.0, **ramp: float) -> dict[str, JsonValue]:
    section: dict[str, JsonValue] = {
        "at_count": at,
        "counts_per_minute": cpm,
        "ramp_to": None,
        "ramp_counts": None,
    }
    section.update(ramp)
    return section


def _hold(at: float) -> dict[str, JsonValue]:
    return {"at_count": at, "seconds": 1.0, "note": None}


def _cue(label: str, order: int) -> dict[str, JsonValue]:
    return {"label": label, "order": order, "estimated_seconds": 2.0}


# Any: a stored document holds arbitrary JSON, which the step under test must check itself.
def _doc(**fields: Any) -> dict[str, JsonValue]:
    base: dict[str, JsonValue] = {
        "mode": "counts",
        "tempos": [_tempo(0.0)],
        "holds": [],
        "markers": [],
        "measures": None,
        "cues": [],
        "audio": [],
        "total_counts": None,
    }
    base.update(fields)
    return {TIMELINE_DOC: base}


def _entries(timeline: dict[str, JsonValue], field: str) -> list[dict[str, JsonValue]]:
    entries = timeline[field]
    assert isinstance(entries, list)
    return [entry for entry in entries if isinstance(entry, dict)]


def _run(docs: dict[str, JsonValue]) -> tuple[dict[str, JsonValue], tuple[str, ...]]:
    report = ReportBuilder(1)
    migrated = migrate_timeline(docs, report)
    timeline = migrated[TIMELINE_DOC]
    assert isinstance(timeline, dict)
    Timeline.model_validate(timeline)
    return timeline, report.build().repairs


def test_the_stored_path_is_the_timeline_document() -> None:
    assert STEP_PATH == TIMELINE_DOC


def test_a_valid_timeline_passes_through_unchanged() -> None:
    docs = _doc(holds=[_hold(4.0)], total_counts=16.0)
    timeline, repairs = _run(docs)
    assert timeline == docs[TIMELINE_DOC]
    assert repairs == ()


def test_unsorted_tempo_sections_are_sorted_by_count_and_reported() -> None:
    docs = _doc(tempos=[_tempo(0.0, 100.0), _tempo(16.0, 60.0), _tempo(8.0, 90.0)])
    before = copy.deepcopy(docs)
    timeline, repairs = _run(docs)
    assert [t["at_count"] for t in _entries(timeline, "tempos")] == [0.0, 8.0, 16.0]
    assert len(repairs) == 1
    assert docs == before


def test_two_tempo_sections_at_one_count_are_refused_with_their_location() -> None:
    docs = _doc(tempos=[_tempo(0.0), _tempo(8.0, 90.0), _tempo(8.0, 60.0)])
    with pytest.raises(SchemaError) as info:
        migrate_timeline(docs, ReportBuilder(1))
    assert info.value.context["path"] == TIMELINE_DOC
    assert info.value.context["field"] == "tempos"
    assert info.value.context["value"] == 8.0


def test_a_first_tempo_after_count_zero_starts_at_zero() -> None:
    timeline, repairs = _run(_doc(tempos=[_tempo(8.0, 90.0), _tempo(16.0)]))
    assert [t["at_count"] for t in _entries(timeline, "tempos")] == [0.0, 16.0]
    assert len(repairs) == 1


@pytest.mark.parametrize(
    ("ramp", "expected"),
    [
        ({"ramp_counts": 4.0}, {"ramp_to": None, "ramp_counts": None}),
        ({"ramp_to": 90.0}, {"ramp_to": None, "ramp_counts": None}),
        ({"ramp_to": 90.0, "ramp_counts": 0.0}, {"ramp_to": None, "ramp_counts": None}),
        ({"ramp_to": 90.0, "ramp_counts": -2.0}, {"ramp_to": None, "ramp_counts": None}),
        ({"ramp_to": 90.0, "ramp_counts": 4.0}, {"ramp_to": 90.0, "ramp_counts": 4.0}),
    ],
)
def test_a_ramp_without_a_target_or_length_becomes_a_steady_tempo(
    ramp: dict[str, float], expected: dict[str, float | None]
) -> None:
    timeline, repairs = _run(_doc(tempos=[_tempo(0.0, **ramp)]))
    section = _entries(timeline, "tempos")[0]
    assert {key: section[key] for key in expected} == expected
    assert len(repairs) == (0 if expected["ramp_to"] is not None else 1)


def test_unsorted_holds_are_sorted_and_a_hold_after_the_end_is_dropped() -> None:
    timeline, repairs = _run(_doc(holds=[_hold(8.0), _hold(4.0), _hold(40.0)], total_counts=16.0))
    assert [h["at_count"] for h in _entries(timeline, "holds")] == [4.0, 8.0]
    assert len(repairs) == 2


def test_duplicate_cue_orders_are_renumbered_in_stored_order() -> None:
    cues = [_cue("a", 1), _cue("b", 1), _cue("c", 2), _cue("d", 9)]
    timeline, repairs = _run(_doc(mode="cues", tempos=[], cues=cues))
    assert [(c["label"], c["order"]) for c in _entries(timeline, "cues")] == [
        ("a", 1),
        ("b", 2),
        ("c", 3),
        ("d", 9),
    ]
    assert len(repairs) == 1


@pytest.mark.parametrize(
    ("fields", "field", "value"),
    [
        ({"holds": [_hold(4.0), _hold(4.0)]}, "holds", 4.0),
        ({"holds": [_hold(-1.0)]}, "holds", -1.0),
        ({"total_counts": -5.0}, "total_counts", -5.0),
    ],
)
def test_values_no_v0_3_0_edit_could_write_are_refused(
    fields: dict[str, Any], field: str, value: float
) -> None:
    with pytest.raises(SchemaError) as info:
        migrate_timeline(_doc(**fields), ReportBuilder(1))
    assert info.value.context["path"] == TIMELINE_DOC
    assert info.value.context["field"] == field
    assert info.value.context["value"] == value


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [
        (_tempo(0.0, 0.0), "counts_per_minute", 0.0),
        (_tempo(0.0, -60.0), "counts_per_minute", -60.0),
        (_tempo(0.0, 120.0, ramp_to=0.0, ramp_counts=8.0), "ramp_to", 0.0),
        (_tempo(0.0, 120.0, ramp_to=-30.0, ramp_counts=8.0), "ramp_to", -30.0),
        (_tempo(0.0, 120.0, ramp_to=0.0), "ramp_to", 0.0),
    ],
)
def test_regression_tempo_below_the_floor_is_refused_with_its_location(
    section: dict[str, JsonValue], field: str, value: float
) -> None:
    with pytest.raises(SchemaError) as info:
        migrate_timeline(_doc(tempos=[section]), ReportBuilder(1))
    assert info.value.context["path"] == TIMELINE_DOC
    assert info.value.context["field"] == field
    assert info.value.context["value"] == value


def test_regression_tempo_at_the_floor_is_kept() -> None:
    section = _tempo(0.0, MIN_TEMPO, ramp_to=MIN_TEMPO, ramp_counts=8.0)
    timeline, repairs = _run(_doc(tempos=[section]))
    assert _entries(timeline, "tempos") == [section]
    assert repairs == ()


def test_a_document_that_is_not_a_timeline_object_is_left_for_validation() -> None:
    docs: dict[str, JsonValue] = {TIMELINE_DOC: ["not", "a", "timeline"]}
    assert migrate_timeline(docs, ReportBuilder(1)) == docs


def _measures(*changes: tuple[float, float], length: float = 4.0) -> dict[str, JsonValue]:
    return {
        "counts_per_measure": length,
        "start_measure": 1,
        "changes": [list(c) for c in changes],
    }


def test_unsorted_measure_changes_are_sorted_and_reported() -> None:
    docs = _doc(measures=_measures((14.0, 2.0), (8.0, 3.0)))
    before = copy.deepcopy(docs)
    timeline, repairs = _run(docs)
    measures = timeline["measures"]
    assert isinstance(measures, dict)
    assert measures["changes"] == [[8.0, 3.0], [14.0, 2.0]]
    assert len(repairs) == 1
    assert docs == before


def test_sorted_measure_changes_pass_through_unchanged() -> None:
    docs = _doc(measures=_measures((8.0, 3.0), (14.0, 2.0)))
    timeline, repairs = _run(docs)
    assert timeline == docs[TIMELINE_DOC]
    assert repairs == ()


@pytest.mark.parametrize(
    ("measures", "field", "value"),
    [
        (_measures(length=0.0), "measures.counts_per_measure", 0.0),
        (_measures(length=-4.0), "measures.counts_per_measure", -4.0),
        (_measures((8.0, 0.0)), "measures.changes", 0.0),
        (_measures((8.0, -2.0)), "measures.changes", -2.0),
        (_measures((-1.0, 2.0)), "measures.changes", -1.0),
        (_measures((8.0, 3.0), (8.0, 5.0)), "measures.changes", 8.0),
    ],
)
def test_measure_values_v2_cannot_hold_are_refused_with_their_location(
    measures: dict[str, JsonValue], field: str, value: float
) -> None:
    with pytest.raises(SchemaError) as info:
        migrate_timeline(_doc(measures=measures), ReportBuilder(1))
    assert info.value.context["path"] == TIMELINE_DOC
    assert info.value.context["field"] == field
    assert info.value.context["value"] == value
