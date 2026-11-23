#!/usr/bin/env python3

"""
test_migrate_sheet.py --- tests the sheet sub-step of the v1 to v2 migration

Contains:
    test_sheet_step_paths_match_the_stored_paths(): names the stored sheet and show documents
    test_sheet_step_returns_valid_documents_unchanged(): keeps a sheet that v2 accepts
    test_regression_v1_landmarks_zones_and_coordinates_are_dropped_and_listed(): removes them from
        the sheet and show.json and lists each that held more than its v1 default, as stored
    test_regression_v1_fields_at_their_defaults_are_dropped_unlisted(): removes empty landmarks,
        zones and default coordinate systems without listing them
    test_regression_sheet_breaking_a_v2_rule_is_refused(): refuses a stored sheet that v2 refuses
        instead of repairing it
    test_regression_v1_tree_with_a_bad_sheet_fails_to_load(): refuses the same through
        migrate_documents
    test_sheet_step_never_mutates_its_input(): leaves the parsed documents as they were
"""

import copy
import json
from typing import Any

import pytest

from e8s.core.errors import SchemaError
from e8s.examples import two_set_show
from e8s.show.document_paths import SHEET_DOC
from e8s.show.documents import encode_show, migrate_documents
from e8s.show.migrate import SHOW_DOC, ReportBuilder, without_header
from e8s.show.migrations import v1_to_v2_sheet
from e8s.show.migrations.v1_to_v2_sheet import migrate_sheet

GUIDE = {"id": "tape", "kind": "custom", "points": [[0.0, 1.0], [1.0, 1.0]], "label": ""}


def _docs() -> dict[str, Any]:
    raw = encode_show(two_set_show())
    return {
        SHEET_DOC: json.loads(raw[SHEET_DOC]),
        SHOW_DOC: without_header(json.loads(raw[SHOW_DOC])),
    }


def _migrate(docs: dict[str, Any]) -> dict[str, Any]:
    return migrate_sheet(docs, ReportBuilder(1))


def test_sheet_step_paths_match_the_stored_paths() -> None:
    assert v1_to_v2_sheet.SHEET_DOC == SHEET_DOC
    assert v1_to_v2_sheet.SHOW_DOC == SHOW_DOC


def test_sheet_step_returns_valid_documents_unchanged() -> None:
    docs = _docs()
    assert _migrate(docs) == docs


def _break(docs: dict[str, Any], change: str) -> None:
    sheet = docs[SHEET_DOC]
    if change == "size":
        sheet["size_m"] = [0.0, 6.0]
    elif change == "outline":
        sheet["outline"] = sheet["outline"][:2]
    elif change == "ref":
        sheet["ref"] = "nonsense"
    elif change == "guides":
        sheet["guides"] = [GUIDE] * 2
    else:
        sheet["guides"] = [{**GUIDE, "points": []}]


BREAKS = ["size", "outline", "ref", "guides", "guide-points"]


@pytest.mark.parametrize("change", BREAKS)
def test_regression_sheet_breaking_a_v2_rule_is_refused(change: str) -> None:
    """Refuses a stored value v2 refuses, naming the document, instead of repairing it."""
    docs = _docs()
    _break(docs, change)
    with pytest.raises(SchemaError) as caught:
        _migrate(docs)
    assert caught.value.context["document"] in {SHEET_DOC, SHOW_DOC}


def test_regression_v1_tree_with_a_bad_sheet_fails_to_load() -> None:
    """Refuses a v1 tree whose sheet has a width of 0 when it is migrated."""
    raw = encode_show(two_set_show())
    sheet = json.loads(raw[SHEET_DOC])
    sheet["size_m"] = [0.0, 6.0]
    show_doc = without_header(json.loads(raw[SHOW_DOC]))
    docs = {
        **raw,
        SHEET_DOC: json.dumps(sheet).encode(),
        SHOW_DOC: json.dumps({**show_doc}).encode(),
    }
    with pytest.raises(SchemaError):
        migrate_documents(docs)


def test_sheet_step_never_mutates_its_input() -> None:
    docs = _docs()
    before = copy.deepcopy(docs)
    _migrate(docs)
    assert docs == before


LANDMARK = {"id": "c", "name": "center", "kind": "point", "geometry": [[0.0, 10.0]], "aliases": []}
ZONE = {"id": "pit", "kind": "pit", "polygon": {"exterior": [[0, 0], [1, 0], [1, 1]], "holes": []}}
FEET = {
    "kind": "marching",
    "origin": "c",
    "audience_side": "front",
    "units": "ft",
    "precision": 0.5,
    "labels": {"left": "SR"},
}
METERS = {
    "kind": "grid",
    "origin": [0.0, 0.0],
    "audience_side": "front",
    "units": "m",
    "precision": 0.01,
    "labels": {},
}


def test_regression_v1_landmarks_zones_and_coordinates_are_dropped_and_listed() -> None:
    """Drops what v2 lacks and lists each non-default value as stored, so a host can keep it."""
    docs = _docs()
    expected = copy.deepcopy(docs)
    docs[SHEET_DOC] |= {"landmarks": [LANDMARK], "zones": [ZONE], "default_coordinates": FEET}
    docs[SHOW_DOC] |= {"coordinates": FEET, "zones": [ZONE]}
    report = ReportBuilder(1)
    assert migrate_sheet(docs, report) == expected
    dropped = [(d.document, d.location, d.stored_id) for d in report.build().dropped]
    assert dropped == [(SHEET_DOC, "", None), (SHOW_DOC, "", None)]
    sheet_fields, show_fields = (d.fields for d in report.build().dropped)
    assert json.loads(json.dumps(sheet_fields, default=dict)) == {
        "landmarks": [LANDMARK],
        "zones": [ZONE],
        "default_coordinates": FEET,
    }
    assert json.loads(json.dumps(show_fields, default=dict)) == {
        "coordinates": FEET,
        "zones": [ZONE],
    }


def test_regression_v1_fields_at_their_defaults_are_dropped_unlisted() -> None:
    """Drops empty landmarks and zones and default coordinate systems without listing them."""
    docs = _docs()
    expected = copy.deepcopy(docs)
    docs[SHEET_DOC] |= {"landmarks": [], "zones": [], "default_coordinates": METERS}
    docs[SHOW_DOC] |= {"coordinates": METERS, "zones": []}
    report = ReportBuilder(1)
    assert migrate_sheet(docs, report) == expected
    assert report.build().dropped == ()
