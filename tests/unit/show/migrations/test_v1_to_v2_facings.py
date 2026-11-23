#!/usr/bin/env python3

"""
test_v1_to_v2_facings.py --- tests the facings sub-step of the v1 to v2 format migration

Contains:
    test_regression_facings_step_wraps_placement_and_fixed_form_facings: wraps stored placement
        facings and fixed form facings into [0, 360), recording one repair per value
    test_facings_step_leaves_in_range_values_and_other_fields_alone: changes nothing when every
        facing is already a heading, keeps offsets of the other facing modes and other documents,
        and never mutates its input
    test_regression_facings_step_refuses_what_v2_cannot_hold: refuses a facing that is not a
        finite number, naming the document, the field and the value
"""

import copy

import pytest

from e8s.core.errors import SchemaError
from e8s.core.json_types import JsonValue
from e8s.show.migrate import FIRST_VERSION, ReportBuilder
from e8s.show.migrations.v1_to_v2_facings import migrate_facings

SET_DOC = "/sets/2/set.json"
FORMS_DOC = "/sets/2/forms.json"


def _placement(entity: str, *, facing: JsonValue) -> dict[str, JsonValue]:
    return {"entity": entity, "x": 0.0, "y": 0.0, "facing": facing}


def _form(form_id: str, mode: str, value: JsonValue) -> dict[str, JsonValue]:
    return {"id": form_id, "facing": {"mode": mode, "value": value}}


def test_regression_facings_step_wraps_placement_and_fixed_form_facings() -> None:
    docs: dict[str, JsonValue] = {
        SET_DOC: {
            "placements": [
                _placement("A", facing=720),
                _placement("B", facing=-90.0),
                _placement("C", facing=45),
            ]
        },
        FORMS_DOC: [_form("f", "fixed", 450.0), _form("g", "fixed", 10.0)],
    }
    report = ReportBuilder(FIRST_VERSION)
    migrated = migrate_facings(docs, report)
    assert migrated == {
        SET_DOC: {
            "placements": [
                _placement("A", facing=0.0),
                _placement("B", facing=270.0),
                _placement("C", facing=45),
            ]
        },
        FORMS_DOC: [_form("f", "fixed", 90.0), _form("g", "fixed", 10.0)],
    }
    assert report.build().repairs == (
        f"{SET_DOC}: placement A faced 720; stored 0.0",
        f"{SET_DOC}: placement B faced -90.0; stored 270.0",
        f"{FORMS_DOC}: form f faced 450.0; stored 90.0",
    )


def test_facings_step_leaves_in_range_values_and_other_fields_alone() -> None:
    docs: dict[str, JsonValue] = {
        SET_DOC: {"placements": [_placement("A", facing=359.5)], "counts": 4.0},
        FORMS_DOC: [
            _form("f", "along", -90.0),
            _form("g", "toward_point", [1.0, 2.0]),
            {"id": "h", "facing": {"mode": "audience"}},
        ],
        "/sets/2/paths.json": [{"facing": 720.0}],
    }
    before = copy.deepcopy(docs)
    report = ReportBuilder(FIRST_VERSION)
    migrated = migrate_facings(docs, report)
    assert docs == before
    assert migrated == before
    assert report.build().repairs == ()


@pytest.mark.parametrize(
    ("docs", "field", "value"),
    [
        (
            {SET_DOC: {"placements": [_placement("A", facing="north")]}},
            "placements[0].facing",
            "north",
        ),
        ({SET_DOC: {"placements": [_placement("A", facing=True)]}}, "placements[0].facing", "True"),
        ({SET_DOC: {"placements": [_placement("A", facing=None)]}}, "placements[0].facing", "None"),
        (
            {
                SET_DOC: {
                    "placements": [
                        _placement("A", facing=0.0),
                        _placement("B", facing=float("nan")),
                    ]
                }
            },
            "placements[1].facing",
            "nan",
        ),
        ({FORMS_DOC: [_form("f", "fixed", float("inf"))]}, "[0].facing.value", "inf"),
        ({FORMS_DOC: [_form("f", "fixed", None)]}, "[0].facing.value", "None"),
    ],
)
def test_regression_facings_step_refuses_what_v2_cannot_hold(
    docs: dict[str, JsonValue], field: str, value: str
) -> None:
    with pytest.raises(SchemaError) as caught:
        migrate_facings(docs, ReportBuilder(FIRST_VERSION))
    path = SET_DOC if SET_DOC in docs else FORMS_DOC
    assert caught.value.context == {"path": path, "field": field, "value": value}
