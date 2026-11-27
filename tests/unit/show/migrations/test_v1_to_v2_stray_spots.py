#!/usr/bin/env python3

"""
test_v1_to_v2_stray_spots.py --- tests how the v1 to v2 migration drops spots outside the cast

Contains:
    test_regression_every_reference_to_a_stray_spot_is_dropped_and_listed: drops a stray spot's
        path row, row facing, route membership, form slot, facing override and pin, lists each
        in MigrationReport.dropped, and the migrated show validates with no error
"""

import copy
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from e8s.core.json_types import JsonValue
from e8s.show.documents import decode_show, migrate_documents
from e8s.show.file_repository import FileRepository
from e8s.show.validate import validate_show

V030_FIXTURE = Path(__file__).resolve().parents[3] / "fixtures" / "repos" / "v0.3.0"
STRAY = "ZZ9"
SHOW_DOC = "/show/show.json"
PATHS_DOC = "/transitions/1-2/paths.json"
FORMS_DOC = "/sets/2/forms.json"
FORM_ID = "brass-2"
ROUTE_ID = "route"
FACING_SPOT = "B1"
TOWARD_ENTITY = "toward_entity"
SET_DOC_SUFFIX = "/set.json"

type Docs = dict[str, Any]
# The listed fields must hold these, as stored after the earlier sub-steps.
type Expected = tuple[str, str, str | None, dict[str, JsonValue]]


def _head(tmp_path: Path) -> Docs:
    shutil.copytree(V030_FIXTURE / "repo", tmp_path / "repo")
    expected = json.loads((V030_FIXTURE / "expected.json").read_text())
    stored = FileRepository(tmp_path / "repo").documents(expected["show_id"])
    return {path: json.loads(raw) for path, raw in stored.items() if path.endswith(".json")}


def _stored(docs: Docs) -> dict[str, bytes]:
    return {path: json.dumps(doc).encode() for path, doc in docs.items()}


def _place_stray_everywhere(docs: Docs) -> None:
    for path, doc in docs.items():
        if path.endswith("/set.json"):
            stray = {**doc["placements"][0], "entity": STRAY, "form": None, "order": None}
            doc["placements"].append(stray)


def _path_row(docs: Docs) -> Expected:
    rows = docs[PATHS_DOC]["rows"]
    row = {**copy.deepcopy(rows[FACING_SPOT]), "entity": STRAY}
    rows[STRAY] = row
    return PATHS_DOC, f"rows.{STRAY}", STRAY, {"entity": STRAY}


def _row_facing(docs: Docs) -> Expected:
    row = docs[PATHS_DOC]["rows"][FACING_SPOT]
    row["facing_mode"], row["facing_value"] = TOWARD_ENTITY, STRAY
    fields: dict[str, JsonValue] = {
        "facing_mode": TOWARD_ENTITY,
        "facing_value": {"kind": "entity", "entity": STRAY},
    }
    return PATHS_DOC, f"rows.{FACING_SPOT}", FACING_SPOT, fields


def _route_member(docs: Docs) -> Expected:
    route = docs[PATHS_DOC]["routes"][ROUTE_ID]
    route["members"].insert(1, STRAY)
    route["offsets"].insert(1, 2.5)
    fields: dict[str, JsonValue] = {"members": [STRAY], "offsets": [2.5]}
    return PATHS_DOC, f"routes.{ROUTE_ID}", ROUTE_ID, fields


def _form_member(docs: Docs) -> Expected:
    form = docs[FORMS_DOC][0]
    form["members"].append(STRAY)
    for placement in docs["/sets/2/set.json"]["placements"]:
        if placement["entity"] == STRAY:
            placement["form"], placement["order"] = FORM_ID, len(form["members"]) - 1
    return FORMS_DOC, "[0]", FORM_ID, {"members": [STRAY]}


def _override_spot(docs: Docs) -> Expected:
    override = {"entities": ["B2", STRAY], "mode": "audience", "start": 0.0, "end": 1.0}
    docs[SHOW_DOC]["facing_overrides"].append(override)
    return SHOW_DOC, "facing_overrides[0]", None, {"entities": [STRAY]}


def _override_only_stray(docs: Docs) -> Expected:
    override = {"entities": [STRAY], "mode": "audience", "start": 0.0, "end": 1.0}
    docs[SHOW_DOC]["facing_overrides"].append(override)
    return SHOW_DOC, "facing_overrides[0]", None, {"entities": [STRAY]}


def _override_toward_stray(docs: Docs) -> Expected:
    override = {
        "entities": ["B2"],
        "mode": TOWARD_ENTITY,
        "value": STRAY,
        "start": 0.0,
        "end": 1.0,
    }
    docs[SHOW_DOC]["facing_overrides"].append(override)
    fields: dict[str, JsonValue] = {"value": {"kind": "entity", "entity": STRAY}}
    return SHOW_DOC, "facing_overrides[0]", None, fields


def _entity_pin(docs: Docs) -> Expected:
    pin: dict[str, JsonValue] = {"target": "entity", "id": STRAY, "by": "designer", "reason": ""}
    docs[SHOW_DOC]["pins"].append(pin)
    return SHOW_DOC, "pins[0]", STRAY, pin


CASES: dict[str, Callable[[Docs], Expected]] = {
    "path row": _path_row,
    "row facing": _row_facing,
    "route member": _route_member,
    "form member": _form_member,
    "override spot": _override_spot,
    "override of only the stray": _override_only_stray,
    "override toward the stray": _override_toward_stray,
    "entity pin": _entity_pin,
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_regression_every_reference_to_a_stray_spot_is_dropped_and_listed(
    tmp_path: Path, case: str
) -> None:
    """Drops each reference to a spot outside the cast, lists it, and the show validates."""
    docs = _head(tmp_path)
    _place_stray_everywhere(docs)
    baseline = migrate_documents(_stored(docs))[1].dropped
    expected = CASES[case](docs)
    migrated, report = migrate_documents(_stored(docs))
    errors = [i for i in validate_show(decode_show(migrated)).issues if i.level == "error"]
    assert errors == []
    placements = [d for d in report.dropped if SET_DOC_SUFFIX in d.document]
    assert len(placements) == len([d for d in baseline if SET_DOC_SUFFIX in d.document])
    added = [d.to_json() for d in report.dropped if d not in baseline and d not in placements]
    assert len(added) == 1
    (listed,) = added
    document, location, stored_id, fields = expected
    assert (listed["document"], listed["location"], listed["stored_id"]) == (
        document,
        location,
        stored_id,
    )
    listed_fields = listed["fields"]
    assert isinstance(listed_fields, dict)
    assert {key: listed_fields.get(key) for key in fields} == fields
