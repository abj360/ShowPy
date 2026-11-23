#!/usr/bin/env python3

"""
test_migrate_sheet.py --- tests the sheet sub-step of the v1 to v2 migration

Contains:
    test_sheet_step_paths_match_the_stored_paths(): names the stored sheet and show documents
    test_sheet_step_returns_valid_documents_unchanged(): keeps a sheet that v2 accepts
    test_regression_stored_precision_of_zero_is_refused(): refuses a precision of 0 or less in
        the sheet and in show.json, naming the document, the field and the value
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


@pytest.mark.parametrize("precision", [0, 0.0, -0.5])
def test_regression_stored_precision_of_zero_is_refused(precision: float) -> None:
    """Refuses a stored precision of 0 or less, naming the document, the field and the value."""
    sheet_docs = _docs()
    sheet_docs[SHEET_DOC]["default_coordinates"]["precision"] = precision
    with pytest.raises(SchemaError) as caught:
        _migrate(sheet_docs)
    assert caught.value.context["document"] == SHEET_DOC
    assert caught.value.context["field"] == "default_coordinates.precision"
    assert caught.value.context["value"] == repr(precision)
    show_docs = _docs()
    show_docs[SHOW_DOC]["coordinates"]["precision"] = precision
    with pytest.raises(SchemaError) as caught:
        _migrate(show_docs)
    assert caught.value.context["document"] == SHOW_DOC
    assert caught.value.context["field"] == "coordinates.precision"


def _break(docs: dict[str, Any], change: str) -> None:
    sheet = docs[SHEET_DOC]
    if change == "size":
        sheet["size_m"] = [0.0, 6.0]
    elif change == "outline":
        sheet["outline"] = sheet["outline"][:2]
    elif change == "ref":
        sheet["ref"] = "nonsense"
    elif change == "landmarks":
        sheet["landmarks"] = [sheet["landmarks"][0]] * 2
    elif change == "zones":
        sheet["zones"] = [sheet["zones"][0]] * 2
    elif change == "guides":
        sheet["guides"] = [GUIDE] * 2
    elif change == "guide-points":
        sheet["guides"] = [{**GUIDE, "points": []}]
    elif change == "labels":
        sheet["default_coordinates"]["labels"] = {"left": 3}
    else:
        docs[SHOW_DOC]["coordinates"]["labels"] = {"left": 3}


BREAKS = [
    "size",
    "outline",
    "ref",
    "landmarks",
    "zones",
    "guides",
    "guide-points",
    "labels",
    "show-labels",
]


@pytest.mark.parametrize("change", BREAKS)
def test_regression_sheet_breaking_a_v2_rule_is_refused(change: str) -> None:
    """Refuses a stored value v2 refuses, naming the document, instead of repairing it."""
    docs = _docs()
    _break(docs, change)
    with pytest.raises(SchemaError) as caught:
        _migrate(docs)
    assert caught.value.context["document"] in {SHEET_DOC, SHOW_DOC}


def test_regression_v1_tree_with_a_bad_sheet_fails_to_load() -> None:
    """Refuses a v1 tree whose sheet has a precision of 0 when it is migrated."""
    raw = encode_show(two_set_show())
    sheet = json.loads(raw[SHEET_DOC])
    sheet["default_coordinates"]["precision"] = 0
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
