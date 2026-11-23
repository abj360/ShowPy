#!/usr/bin/env python3

"""
test_document_reading.py --- tests how v1 interchange dumps split into stored documents

Contains:
    test_v1_dump_round_trips_through_the_split(): decodes a v1 dump equal to its show
    test_regression_v1_dump_split_refuses_what_no_tree_can_hold(): refuses set lists, duplicate
        sets, transitions (a pair of lists, found by fuzzing, and a pair of sets that are not
        consecutive, which every v1 release refused to store) and forms that no stored tree can
        hold, with SchemaError
    test_regression_v1_dump_without_sheet_counts_the_missing_fields(): names the first missing
        field and counts both
    test_decode_show_dump_never_mutates_the_dump(): leaves a fixture dump with forms, paths,
        zones and legacy set IDs exactly as it was
"""

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from e8s.core.errors import SchemaError
from e8s.examples import crossing_show, two_set_show
from e8s.show.document_reading import decode_show_dump
from e8s.show.documents import decode_show
from e8s.show.file_repository import FileRepository

type Damage = Callable[[dict[str, Any]], None]

V030_REPO = Path(__file__).resolve().parents[2] / "fixtures" / "repos" / "v0.3.0" / "repo"


def _dump() -> dict[str, Any]:
    show = crossing_show()
    return copy.deepcopy(show.to_json())


def _set_forms(dump: dict[str, Any], forms: object) -> None:
    dump["forms"] = forms


def _orphan_form(dump: dict[str, Any]) -> None:
    dump["forms"] = {"f": {"id": "f", "set": "9"}}


def _unknown_pair(dump: dict[str, Any]) -> None:
    dump["transitions"] = [{"id": ["1", "9"]}]


def _pair_of_lists(dump: dict[str, Any]) -> None:
    dump["transitions"] = [{"id": [["1"], "2"]}]


def _pair_of_non_consecutive_sets(dump: dict[str, Any]) -> None:
    spec = crossing_show().transition(("1", "2")).to_json()
    dump["transitions"] = [{**spec, "id": ["2", "1"]}]


def _repeated_pair(dump: dict[str, Any]) -> None:
    dump["transitions"] = [{"id": ["1", "2"]}, {"id": ["1", "2"]}]


def _repeated_set(dump: dict[str, Any]) -> None:
    dump["sets"] = [dump["sets"][0], dump["sets"][0]]


def _sets_as_text(dump: dict[str, Any]) -> None:
    dump["sets"] = "12"


def test_v1_dump_round_trips_through_the_split() -> None:
    for show in (two_set_show(), crossing_show()):
        assert decode_show_dump(show.to_json(), 1) == show


@pytest.mark.parametrize(
    "damage",
    [
        _sets_as_text,
        _repeated_set,
        _unknown_pair,
        _pair_of_lists,
        _pair_of_non_consecutive_sets,
        _repeated_pair,
        lambda dump: _set_forms(dump, []),
        _orphan_form,
    ],
)
def test_regression_v1_dump_split_refuses_what_no_tree_can_hold(damage: Damage) -> None:
    dump = _dump()
    damage(dump)
    with pytest.raises(SchemaError):
        decode_show_dump(dump, 1)


def test_regression_v1_dump_without_sheet_counts_the_missing_fields() -> None:
    dump = {"id": "x", "title": "t"}
    with pytest.raises(SchemaError) as caught:
        decode_show_dump(dump, 1)
    assert caught.value.context == {"field": "sheet", "error_count": 2}


def test_decode_show_dump_never_mutates_the_dump() -> None:
    tree = dict(FileRepository(V030_REPO).documents("legacy-show"))
    stored = json.loads((V030_REPO.parent / "expected.json").read_text())["show"]
    dump = {**stored, "revision": ""}
    before = copy.deepcopy(dump)
    assert decode_show_dump(dump, 1) == decode_show(tree)
    assert dump == before
