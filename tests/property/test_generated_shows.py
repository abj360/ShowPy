#!/usr/bin/env python3

"""
test_generated_shows.py --- checks that generated shows validate, match the schema and round-trip
"""

import json

import pytest
from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from e8s.core.json_types import JsonValue
from e8s.core.resources import schema_path
from e8s.show.changes import store_paths
from e8s.show.documents import decode_show, encode_show
from e8s.show.migrate import CURRENT_VERSION
from e8s.show.models import PathsOp, Show
from e8s.show.validate import validate_paths, validate_show
from tests.strategies.paths import path_rows
from tests.strategies.show import shows

pytestmark = pytest.mark.slow

SCHEMA_NAME = f"e8.show.v{CURRENT_VERSION}.json"


def encoded_tree(show: Show) -> dict[str, JsonValue]:
    return {path: json.loads(raw) for path, raw in encode_show(show).items()}


def _schema_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(schema_path(SCHEMA_NAME).read_text()))


@given(shows())
def test_generated_shows_validate(show: Show) -> None:
    report = validate_show(show)
    assert report.ok, [(i.code, i.where, i.message) for i in report.issues if i.level == "error"]


@given(shows())
def test_generated_shows_match_schema(show: Show) -> None:
    errors = sorted(_schema_validator().iter_errors(encoded_tree(show)), key=str)
    assert errors == [], [e.message for e in errors[:3]]


@given(shows())
def test_generated_show_round_trips(show: Show) -> None:
    assert decode_show(encode_show(show)) == show


@given(st.data())
def test_path_rows_compile_against_their_show(data: st.DataObject) -> None:
    show = data.draw(shows(min_sets=2))
    tid = data.draw(st.sampled_from(show.transition_ids()))
    row = data.draw(path_rows(show, tid))
    spec = show.transition(tid)
    edited = spec.model_copy(update={"rows": {**spec.rows, row.entity: row}})
    stored = store_paths(show, PathsOp(spec=edited))
    report = validate_paths(stored, stored.transition(tid))
    assert report.ok, [(i.code, i.message) for i in report.issues if i.level == "error"]
