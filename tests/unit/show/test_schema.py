#!/usr/bin/env python3

"""
test_schema.py --- keeps the stored-format JSON schemas in step with the stored documents

`showpy.show.v1.json` is frozen: it describes what v0.3.0 and earlier wrote, so its bytes never
change. `showpy.show.v2.json` describes a stored tree of format version 2 as one JSON object
that maps each show-document path to its parsed document. A stored-model change regenerates it
deliberately: run with SHOWPY_UPDATE_SCHEMA=1 and review the diff.

Contains:
    stored_schema(): builds the format 2 schema of a stored tree from the models
    test_v1_schema_is_frozen(): pins the bytes of showpy.show.v1.json
    test_committed_v2_schema_matches_the_models(): compares showpy.show.v2.json with the models
    test_v2_schema_validates_encoded_documents(): accepts the documents encode_show writes
    test_v2_schema_refuses_a_tree_without_its_header(): refuses a show.json without format
    test_v2_schema_refuses_an_unknown_document(): refuses a path no show document has
    test_v2_schema_states_its_mit_licence(): checks the schema names its MIT notice
"""

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from showpy.choreo.form_models import Form
from showpy.choreo.models import Set
from showpy.choreo.path_models import TransitionSpec
from showpy.core.resources import schema_path
from showpy.examples import crossing_show, two_set_show
from showpy.sheet.models import Sheet
from showpy.show.document_paths import (
    SEGMENT,
    SET_ORDER_FIELD,
    SHEET_DOC,
    SPLIT_FIELDS,
    TIMELINE_DOC,
)
from showpy.show.documents import encode_show
from showpy.show.migrate import CURRENT_VERSION, FORMAT, FORMAT_KEY, SHOW_DOC, VERSION_KEY
from showpy.show.models import Show
from showpy.time.models import Timeline
from tests.fixtures.cast_shows import rich_show

V1_NAME = "showpy.show.v1.json"
V1_SHA256 = "dd0467b169c74cbfdea81ac83092f56bb3182c152bc80d6a30bb91ad8ac78f37"
V2_NAME = "showpy.show.v2.json"
V2_PATH = Path(__file__).parents[3] / "showpy" / "schemas" / V2_NAME
V2_ID = "showpy.show.v2"
DRAFT = "https://json-schema.org/draft/2020-12/schema"
UPDATE_FLAG = "SHOWPY_UPDATE_SCHEMA"
REF = "#/$defs/{model}"
SHOW_DOCUMENT = "ShowDocument"
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
            SET_ORDER_FIELD: {"type": "array", "items": {"type": "string"}},
        },
        "required": [FORMAT_KEY, VERSION_KEY, *required, SET_ORDER_FIELD],
        "additionalProperties": False,
    }


def stored_schema() -> dict[str, Any]:
    """Builds the format 2 schema of a stored tree given as {show-document path: document}."""
    models: list[type[BaseModel]] = [Show, Sheet, Timeline, Set, Form, TransitionSpec]
    _, defs = models_json_schema([(m, "validation") for m in models], ref_template=REF)
    definitions = dict(defs["$defs"])
    definitions[SHOW_DOCUMENT] = _show_document(definitions.pop(Show.__name__))
    return {
        "$schema": DRAFT,
        "$id": V2_ID,
        "$comment": LICENCE_COMMENT,
        "title": "ShowPy stored show, format version 2",
        "description": "A stored tree as one object mapping each show-document path to its JSON.",
        "type": "object",
        "properties": {
            SHOW_DOC: _ref(SHOW_DOCUMENT),
            SHEET_DOC: _ref(Sheet.__name__),
            TIMELINE_DOC: _ref(Timeline.__name__),
        },
        "patternProperties": {
            f"^/sets/{SEGMENT}/set\\.json$": _ref(Set.__name__),
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
