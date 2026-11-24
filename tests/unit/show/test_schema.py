#!/usr/bin/env python3

"""
test_schema.py --- keeps the stored-format JSON schemas in step with the stored documents

`showpy.show.v1.json` is frozen: it describes what v0.3.0 and earlier wrote, so its bytes never
change. `e8.show.v2.json` describes a stored tree of format version 2 as one JSON object
that maps each show-document path to its parsed document. It accepts both document sets: a
layout-1 set.json holding the placements, and a set.json with the placement order beside its
drill, facing and labels documents and the cast document. A stored-model change regenerates it
deliberately: run with E8S_UPDATE_SCHEMA=1 and review the diff.

Contains:
    stored_schema(): builds the format 2 schema of a stored tree from the models
    test_v1_schema_is_frozen(): pins the bytes of showpy.show.v1.json
    test_committed_v2_schema_matches_the_models(): compares e8.show.v2.json with the models
    test_v2_schema_validates_encoded_documents(): accepts the documents encode_show writes
    test_v2_schema_validates_the_layout1_documents_of_the_v040_fixture(): accepts every v0.4.0
        revision of format 2, whose sets hold placements and whose show.json holds the cast
    test_v2_schema_refuses_a_role_document_row_with_another_role_field(): refuses a facing row
        in drill.json
    test_v2_schema_refuses_a_tree_without_its_header(): refuses a show.json without format
    test_v2_schema_refuses_an_unknown_document(): refuses a path no show document has
    test_v2_schema_states_its_mit_licence(): checks the schema names its MIT notice
"""

import hashlib
import json
import os
import shutil
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from e8s.cast.models import Cast
from e8s.choreo.form_models import Form
from e8s.choreo.models import Placement, Set
from e8s.choreo.path_models import TransitionSpec
from e8s.core.caps import MAX_SETS
from e8s.core.resources import schema_path
from e8s.examples import crossing_show, two_set_show
from e8s.sheet.models import Sheet
from e8s.show.document_paths import (
    CAST_DOC,
    ROLE_NAMES,
    SEGMENT,
    SET_ORDER_FIELD,
    SHEET_DOC,
    SPLIT_FIELDS,
    TIMELINE_DOC,
)
from e8s.show.documents import encode_show, is_show_doc
from e8s.show.file_repository import FileRepository
from e8s.show.migrate import CURRENT_VERSION, FORMAT, FORMAT_KEY, SHOW_DOC, VERSION_KEY
from e8s.show.models import Show
from e8s.show.role_documents import PLACEMENT_ORDER_FIELD, PLACEMENTS_FIELD, ROLE_FIELDS
from e8s.time.models import Timeline
from tests.fixtures.cast_shows import rich_show

V1_NAME = "showpy.show.v1.json"
V1_SHA256 = "dd0467b169c74cbfdea81ac83092f56bb3182c152bc80d6a30bb91ad8ac78f37"
V2_NAME = "e8.show.v2.json"
V2_PATH = Path(__file__).parents[3] / "e8s" / "schemas" / V2_NAME
V2_ID = "e8.show.v2"
V040_FORMAT2_REVISIONS = 4
V040 = Path(__file__).resolve().parents[2] / "fixtures" / "repos" / "v0.4.0"
DRAFT = "https://json-schema.org/draft/2020-12/schema"
UPDATE_FLAG = "E8S_UPDATE_SCHEMA"
REF = "#/$defs/{model}"
SHOW_DOCUMENT = "ShowDocument"
SET_DOCUMENT = "SetDocument"
JSON_SUFFIX = ".json"
LICENCE_COMMENT = "Licensed under the MIT License: see LICENSE-MIT.txt beside this file."


def _ref(name: str) -> dict[str, str]:
    return {"$ref": REF.format(model=name)}


def _show_document(show_schema: dict[str, Any]) -> dict[str, Any]:
    """Describes show.json: the header, the show fields kept in it and the set order."""
    kept = {k: v for k, v in show_schema["properties"].items() if k not in SPLIT_FIELDS}
    required = [k for k in show_schema.get("required", []) if k not in SPLIT_FIELDS]
    return {
        "title": SHOW_DOCUMENT,
        "type": "object",
        "properties": {
            FORMAT_KEY: {"const": FORMAT},
            VERSION_KEY: {"const": CURRENT_VERSION},
            **kept,
            SET_ORDER_FIELD: {"type": "array", "items": {"type": "string"}, "maxItems": MAX_SETS},
        },
        "required": [FORMAT_KEY, VERSION_KEY, *required, SET_ORDER_FIELD],
        "additionalProperties": False,
    }


def _set_document(set_schema: dict[str, Any]) -> dict[str, Any]:
    """Describes a role-split set.json: every set field but the placements, plus their order."""
    kept = {k: v for k, v in set_schema["properties"].items() if k != PLACEMENTS_FIELD}
    order = {"type": "array", "items": {"type": "string"}, "uniqueItems": True}
    return {
        "title": SET_DOCUMENT,
        "type": "object",
        "properties": {**kept, PLACEMENT_ORDER_FIELD: order},
        "required": [*set_schema.get("required", []), PLACEMENT_ORDER_FIELD],
        "additionalProperties": False,
    }


def _role_title(name: str) -> str:
    return f"{name.removesuffix(JSON_SUFFIX).capitalize()}Document"


def _role_document(name: str, placement: dict[str, Any]) -> dict[str, Any]:
    """Describes a role document: spot ID to the placement fields of its roles."""
    fields = sorted(ROLE_FIELDS[name])
    row = {
        "type": "object",
        "properties": {f: placement["properties"][f] for f in fields},
        "required": [f for f in placement.get("required", []) if f in fields],
        "additionalProperties": False,
    }
    return {"title": _role_title(name), "type": "object", "additionalProperties": row}


def _role_patterns() -> dict[str, Any]:
    return {
        f"^/sets/{SEGMENT}/{name.removesuffix(JSON_SUFFIX)}\\.json$": _ref(_role_title(name))
        for name in ROLE_NAMES
    }


def stored_schema() -> dict[str, Any]:
    """Builds the format 2 schema of a stored tree given as {show-document path: document}."""
    models: list[type[BaseModel]] = [Show, Sheet, Timeline, Set, Form, TransitionSpec]
    _, defs = models_json_schema([(m, "validation") for m in models], ref_template=REF)
    definitions = dict(defs["$defs"])
    definitions[SHOW_DOCUMENT] = _show_document(definitions.pop(Show.__name__))
    definitions[SET_DOCUMENT] = _set_document(definitions[Set.__name__])
    for name in ROLE_NAMES:
        definitions[_role_title(name)] = _role_document(name, definitions[Placement.__name__])
    return {
        "$schema": DRAFT,
        "$id": V2_ID,
        "$comment": LICENCE_COMMENT,
        "title": "e8Show stored show, format version 2",
        "description": "A stored tree as one object mapping each show-document path to its JSON.",
        "type": "object",
        "properties": {
            SHOW_DOC: _ref(SHOW_DOCUMENT),
            SHEET_DOC: _ref(Sheet.__name__),
            TIMELINE_DOC: _ref(Timeline.__name__),
            CAST_DOC: _ref(Cast.__name__),
        },
        "patternProperties": {
            f"^/sets/{SEGMENT}/set\\.json$": {"anyOf": [_ref(Set.__name__), _ref(SET_DOCUMENT)]},
            **_role_patterns(),
            f"^/sets/{SEGMENT}/forms\\.json$": {"type": "array", "items": _ref(Form.__name__)},
            f"^/transitions/{SEGMENT}/paths\\.json$": _ref(TransitionSpec.__name__),
        },
        "required": [SHOW_DOC, SHEET_DOC, TIMELINE_DOC],
        "additionalProperties": False,
        "$defs": definitions,
    }


def _tree(show: Show) -> dict[str, object]:
    return {path: json.loads(raw) for path, raw in encode_show(show).items()}


def _v2_schema() -> dict[str, Any]:
    loaded: dict[str, Any] = json.loads(schema_path(V2_NAME).read_text())
    return loaded


def test_v1_schema_is_frozen() -> None:
    assert hashlib.sha256(schema_path(V1_NAME).read_bytes()).hexdigest() == V1_SHA256


def test_committed_v2_schema_matches_the_models() -> None:
    if os.environ.get(UPDATE_FLAG) == "1":
        V2_PATH.write_text(json.dumps(stored_schema(), indent=2, sort_keys=True) + "\n")
    assert _v2_schema() == stored_schema()


@pytest.mark.parametrize("show", [two_set_show(), crossing_show(), rich_show()])
def test_v2_schema_validates_encoded_documents(show: Show) -> None:
    jsonschema.validate(_tree(show), _v2_schema())


def test_v2_schema_validates_the_layout1_documents_of_the_v040_fixture(tmp_path: Path) -> None:
    shutil.copytree(V040 / "repo", tmp_path / "repo")
    expected: dict[str, Any] = json.loads((V040 / "expected.json").read_text())
    repo, show_id = FileRepository(tmp_path / "repo"), expected["show_id"]
    format2 = [
        r for r in expected["revisions"] if b'"format"' in (repo.read(show_id, SHOW_DOC, r) or b"")
    ]
    assert len(format2) == V040_FORMAT2_REVISIONS
    for revision in format2:
        docs = repo.documents(show_id, revision)
        tree = {p: json.loads(raw) for p, raw in docs.items() if is_show_doc(p)}
        assert "placements" in tree["/sets/1/set.json"]
        assert "cast" in tree[SHOW_DOC]
        jsonschema.validate(tree, _v2_schema())


def test_v2_schema_refuses_a_role_document_row_with_another_role_field() -> None:
    tree = _tree(two_set_show())
    drill = tree["/sets/1/drill.json"]
    assert isinstance(drill, dict)
    tree["/sets/1/drill.json"] = {**drill, "T1": {**drill["T1"], "facing": 0.0}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(tree, _v2_schema())


def test_v2_schema_refuses_a_tree_without_its_header() -> None:
    tree = _tree(two_set_show())
    meta = tree[SHOW_DOC]
    assert isinstance(meta, dict)
    tree[SHOW_DOC] = {k: v for k, v in meta.items() if k != FORMAT_KEY}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(tree, _v2_schema())


def test_v2_schema_refuses_an_unknown_document() -> None:
    tree = {**_tree(two_set_show()), "/sets/1/extra.json": {}}
    with pytest.raises(jsonschema.ValidationError):
        jsonschema.validate(tree, _v2_schema())


def test_v2_schema_states_its_mit_licence() -> None:
    assert _v2_schema()["$comment"] == LICENCE_COMMENT
    assert schema_path("LICENSE-MIT.txt").is_file()
