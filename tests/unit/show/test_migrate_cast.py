#!/usr/bin/env python3

"""
test_migrate_cast.py --- tests the cast sub-step of the v1 to v2 migration

Contains:
    test_regression_*(): one repair or refusal of migrate_cast each
"""

import copy

import pytest

from e8s.core.errors import SchemaError
from e8s.core.json_types import JsonValue
from e8s.show.migrate import SHOW_DOC, ReportBuilder
from e8s.show.migrations.v1_to_v2_cast import NOTES_DOC, migrate_cast


def _docs(**cast: JsonValue) -> dict[str, JsonValue]:
    base: dict[str, JsonValue] = {
        "groups": {"g": {"key": "g", "label_prefix": "T", "color": "#808080"}},
        "spots": [{"id": "T1", "group": "g"}, {"id": "T2", "group": "g"}],
        "selections": {},
        "label_scheme": {"prefixes": {}, "start": 1, "pad": 0},
    }
    return {SHOW_DOC: {"id": "show", "cast": {**base, **cast}}}


def _cast(docs: dict[str, JsonValue]) -> dict[str, JsonValue]:
    show = docs[SHOW_DOC]
    assert isinstance(show, dict)
    cast = show["cast"]
    assert isinstance(cast, dict)
    return cast


def test_regression_a_clean_cast_migrates_unchanged_without_repairs() -> None:
    docs = _docs()
    report = ReportBuilder(1)
    assert migrate_cast(copy.deepcopy(docs), report) == docs
    assert report.build().repairs == ()


def test_regression_a_group_under_another_key_is_rekeyed_with_its_spots() -> None:
    docs = _docs(
        groups={"old": {"key": "g", "label_prefix": "T"}},
        spots=[{"id": "T1", "group": "old"}],
        label_scheme={"prefixes": {"old": "X"}, "start": 1, "pad": 0},
    )
    before = copy.deepcopy(docs)
    report = ReportBuilder(1)
    cast = _cast(migrate_cast(docs, report))
    assert cast["groups"] == {"g": {"key": "g", "label_prefix": "T"}}
    assert cast["spots"] == [{"id": "T1", "group": "g"}]
    assert cast["label_scheme"] == {"prefixes": {"g": "X"}, "start": 1, "pad": 0}
    assert docs == before
    assert len(report.build().repairs) == 1


def test_regression_a_rekey_that_collides_is_refused() -> None:
    docs = _docs(
        groups={
            "g": {"key": "g", "label_prefix": "T"},
            "other": {"key": "g", "label_prefix": "U"},
        }
    )
    with pytest.raises(SchemaError, match="other"):
        migrate_cast(docs, ReportBuilder(1))


def test_regression_selection_ids_naming_no_spot_are_dropped() -> None:
    docs = _docs(selections={"s": ["T1", "T9"], "t": ["T2"]})
    report = ReportBuilder(1)
    cast = _cast(migrate_cast(docs, report))
    assert cast["selections"] == {"s": ["T1"], "t": ["T2"]}
    assert len(report.build().repairs) == 1


def test_regression_label_prefixes_naming_no_group_are_dropped() -> None:
    docs = _docs(label_scheme={"prefixes": {"g": "X", "ghost": "Y"}, "start": 1, "pad": 0})
    report = ReportBuilder(1)
    cast = _cast(migrate_cast(docs, report))
    assert cast["label_scheme"] == {"prefixes": {"g": "X"}, "start": 1, "pad": 0}
    assert len(report.build().repairs) == 1


@pytest.mark.parametrize(
    ("cast", "named"),
    [
        ({"groups": {"g": {"key": "g", "label_prefix": "T", "color": "red"}}}, "color"),
        (
            {"groups": {"g": {"key": "g", "label_prefix": "T", "default_radius": -1}}},
            "default_radius",
        ),
        ({"groups": {"g": {"key": "g", "label_prefix": "T", "limits": {"speed": 0}}}}, "limits"),
        ({"spots": [{"id": "T1", "group": "g", "color": "#12"}]}, "color"),
        ({"spots": [{"id": "T1", "group": "g", "shape": {"width_m": -1}}]}, "width_m"),
        ({"label_scheme": {"prefixes": {}, "start": 1, "pad": 9}}, "pad"),
        ({"label_scheme": {"prefixes": {}, "start": 1, "pad": -1}}, "pad"),
    ],
)
def test_regression_values_with_no_safe_repair_are_refused_naming_the_field(
    cast: dict[str, JsonValue], named: str
) -> None:
    with pytest.raises(SchemaError, match=named):
        migrate_cast(_docs(**cast), ReportBuilder(1))


def test_regression_a_document_without_a_cast_passes_through() -> None:
    docs: dict[str, JsonValue] = {SHOW_DOC: {"id": "show"}, "/other": {"x": 1}}
    assert migrate_cast(docs, ReportBuilder(1)) == docs


def _note(note_id: str, created_at: JsonValue) -> JsonValue:
    return {
        "id": note_id,
        "text": "go",
        "kind": "idea",
        "anchor": {"kind": "set", "set_id": "1"},
        "author": "pat",
        "created_at": created_at,
        "visible": True,
    }


def _notes(docs: dict[str, JsonValue]) -> list[JsonValue]:
    notes = docs[NOTES_DOC]
    assert isinstance(notes, list)
    return notes


def test_regression_a_naive_note_time_is_read_as_utc_and_reported() -> None:
    docs = _docs()
    docs[NOTES_DOC] = [_note("n1", "2026-11-11T09:30:00"), _note("n2", "2026-11-12T10:00:00.5")]
    before = copy.deepcopy(docs)
    report = ReportBuilder(1)
    notes = _notes(migrate_cast(docs, report))
    assert [n["created_at"] for n in notes if isinstance(n, dict)] == [
        "2026-11-11T09:30:00Z",
        "2026-11-12T10:00:00.500000Z",
    ]
    assert docs == before
    assert len(report.build().repairs) == 2
    assert "n1" in report.build().repairs[0]


def test_regression_an_aware_note_time_is_kept_without_a_repair() -> None:
    docs = _docs()
    docs[NOTES_DOC] = [
        _note("n1", "2026-11-11T09:30:00Z"),
        _note("n2", "2026-11-11T09:30:00+02:00"),
    ]
    report = ReportBuilder(1)
    assert migrate_cast(copy.deepcopy(docs), report) == docs
    assert report.build().repairs == ()


def test_regression_a_note_time_that_is_not_a_time_is_left_for_validation() -> None:
    docs = _docs()
    docs[NOTES_DOC] = [_note("n1", "yesterday"), _note("n2", None)]
    report = ReportBuilder(1)
    assert migrate_cast(copy.deepcopy(docs), report) == docs
    assert report.build().repairs == ()


def test_regression_a_show_without_notes_migrates_without_a_notes_document() -> None:
    assert NOTES_DOC not in migrate_cast(_docs(), ReportBuilder(1))


def test_regression_claimable_is_dropped_and_each_non_default_value_is_listed() -> None:
    docs = _docs(
        spots=[
            {"id": "T1", "group": "g", "claimable": "performer"},
            {"id": "T2", "group": "g", "claimable": "crew"},
            {"id": "T3", "group": "g"},
        ]
    )
    before = copy.deepcopy(docs)
    report = ReportBuilder(1)
    cast = _cast(migrate_cast(docs, report))
    assert cast["spots"] == [
        {"id": "T1", "group": "g"},
        {"id": "T2", "group": "g"},
        {"id": "T3", "group": "g"},
    ]
    assert docs == before
    dropped = report.build().dropped
    assert [(d.document, d.location, d.stored_id) for d in dropped] == [
        (SHOW_DOC, "cast.spots[1]", "T2")
    ]
    assert dict(dropped[0].fields) == {"claimable": "crew"}


def test_regression_a_cast_without_claimable_lists_nothing_dropped() -> None:
    report = ReportBuilder(1)
    migrate_cast(_docs(), report)
    assert report.build().dropped == ()
