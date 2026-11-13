#!/usr/bin/env python3

"""
test_generated_shows.py --- checks that generated shows validate, match the schema and round-trip
"""

import json

from hypothesis import given
from hypothesis import strategies as st
from jsonschema import Draft202012Validator

from showpy.core.resources import schema_path
from showpy.show.changes import store_paths
from showpy.show.documents import decode_show, encode_show
from showpy.show.models import PathsOp, Show
from showpy.show.validate import validate_paths, validate_show
from tests.strategies.paths import path_rows
from tests.strategies.show import shows

SCHEMA_NAME = "showpy.show.v1.json"


def _schema_validator() -> Draft202012Validator:
    return Draft202012Validator(json.loads(schema_path(SCHEMA_NAME).read_text()))


@given(shows())
def test_generated_shows_validate(show: Show) -> None:
    report = validate_show(show)
    assert report.ok, [(i.code, i.where, i.message) for i in report.issues if i.level == "error"]


@given(shows())
def test_generated_shows_match_schema(show: Show) -> None:
    errors = sorted(_schema_validator().iter_errors(show.model_dump(mode="json")), key=str)
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
