#!/usr/bin/env python3

"""
test_schema.py --- keeps the committed showpy.show.v1 JSON schema equal to the Show model

A stored-model change must regenerate the schema deliberately: run with
SHOWPY_UPDATE_SCHEMA=1, review the diff, and bump the version if old files no longer load.
"""

import json
import os
from pathlib import Path

from showpy.core.resources import schema_path
from showpy.show.models import Show

SCHEMA_NAME = "showpy.show.v1.json"
SCHEMA_PATH = Path(__file__).parents[3] / "showpy" / "schemas" / SCHEMA_NAME
SCHEMA_ID = "showpy.show.v1"
UPDATE_FLAG = "SHOWPY_UPDATE_SCHEMA"


def _schema() -> dict[str, object]:
    return {"$id": SCHEMA_ID, **Show.model_json_schema()}


def test_committed_schema_matches_show_model() -> None:
    if os.environ.get(UPDATE_FLAG) == "1":
        SCHEMA_PATH.write_text(json.dumps(_schema(), indent=2, sort_keys=True) + "\n")
    assert json.loads(schema_path(SCHEMA_NAME).read_text()) == _schema()
