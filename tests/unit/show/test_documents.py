#!/usr/bin/env python3

"""
test_documents.py --- encodes audited document encode and decode defects as strict xfails

Contains:
    test_regression_decode_wrong_shape_show_json_is_a_library_error: raises a ShowPyError, not
        TypeError, for a show.json of the wrong shape
    test_regression_decode_wrong_shape_forms_json_is_a_library_error: raises a ShowPyError, not
        TypeError, for a forms.json of the wrong shape
    test_regression_decode_rejects_string_set_order: rejects `"sets": "12"` instead of decoding it
        as sets 1 and 2
    test_regression_decode_wrong_shape_names_the_document: puts the path of the wrong-shaped
        document in the SchemaError context
    test_regression_show_json_carries_a_format_version: stamps show.json with a format and layout
        version
    test_regression_decode_names_the_minimum_version_for_a_newer_document: raises VersionError for a
        document written by a newer library
    test_regression_encode_never_writes_non_finite_numbers: refuses or avoids non-standard
        `Infinity` in stored JSON
    test_regression_negative_zero_encodes_like_zero: writes equal bytes for -0.0 and 0.0, which are
        equal models
    test_regression_fast_dump_matches_dump_json: writes the same bytes through dump_dumped_json
        and dump_json for plain, `-0.0`, reserved-key and over-long-int values
    test_regression_fast_dump_refuses_what_dump_json_refuses: raises SchemaError for NaN,
        infinity and a lone surrogate through both
    test_regression_encode_documents_matches_encode_show: encodes a subset of documents with the
        bytes encode_show gives and skips paths the show does not write
    test_regression_deep_or_huge_json_document_is_a_schema_error: raises SchemaError naming the
        document, not RecursionError or ValueError, for deep nesting or an overlong int
"""

import json
import math

import pytest

from showpy.core.canonical import INT_LIMIT
from showpy.core.errors import SchemaError, ShowPyError, VersionError
from showpy.show.documents import (
    SHOW_DOC,
    decode_show,
    dump_dumped_json,
    dump_json,
    encode_documents,
    encode_show,
    forms_doc,
)
from showpy.show.models import Show
from tests.fixtures.shows import two_set_show
from tests.helpers import HOSTILE_JSON

FUTURE_VERSION = 999
VERSION_KEYS = ("format", "version", "schema_version", "layout_version")


def _docs() -> dict[str, bytes]:
    return encode_show(two_set_show())


@pytest.mark.parametrize("raw", [b"5", b'"text"'])
def test_regression_decode_wrong_shape_show_json_is_a_library_error(raw: bytes) -> None:
    """Raises a ShowPyError, not TypeError, for a show.json of the wrong shape (audit 9.7 #9)."""
    with pytest.raises(ShowPyError):
        decode_show({**_docs(), SHOW_DOC: raw})


def test_regression_decode_wrong_shape_forms_json_is_a_library_error() -> None:
    """Raises a ShowPyError, not TypeError, for a forms.json of the wrong shape (audit 9.7 #9)."""
    with pytest.raises(ShowPyError):
        decode_show({**_docs(), forms_doc("2"): b"5"})


def test_regression_decode_rejects_string_set_order() -> None:
    """Rejects `"sets": "12"` instead of decoding it as sets 1 and 2 (audit 9.7 item 9)."""
    body = json.loads(_docs()[SHOW_DOC]) | {"sets": "12"}
    with pytest.raises(ShowPyError):
        decode_show({**_docs(), SHOW_DOC: json.dumps(body).encode()})


@pytest.mark.parametrize(
    ("path", "raw"),
    [
        (SHOW_DOC, b"[]"),
        ("/show/sheet.json", b"null"),
        ("/show/notes.json", b"{}"),
        ("/time/timeline.json", b"7"),
        ("/sets/2/set.json", b"[]"),
        (forms_doc("2"), b"{}"),
        ("/transitions/1-2/paths.json", b"[]"),
    ],
)
def test_regression_decode_wrong_shape_names_the_document(path: str, raw: bytes) -> None:
    """Puts the path of the wrong-shaped document in the SchemaError context."""
    with pytest.raises(SchemaError) as excinfo:
        decode_show({**_docs(), path: raw})
    assert excinfo.value.context["path"] == path


def test_regression_show_json_carries_a_format_version() -> None:
    """Stamps show.json with a format and layout version (audit 9.7 item 11)."""
    body = json.loads(_docs()[SHOW_DOC])
    assert any(key in body for key in VERSION_KEYS)


def test_regression_decode_names_the_minimum_version_for_a_newer_document() -> None:
    """Raises VersionError for a document written by a newer library (audit 9.7 item 11)."""
    body = json.loads(_docs()[SHOW_DOC]) | {"version": FUTURE_VERSION}
    with pytest.raises(VersionError) as excinfo:
        decode_show({**_docs(), SHOW_DOC: json.dumps(body).encode()})
    message = str(excinfo.value).lower()
    assert str(FUTURE_VERSION) in message or "minimum" in message


def _reject_constant(name: str) -> float:
    raise AssertionError(f"non-standard JSON constant {name}")


def test_regression_encode_never_writes_non_finite_numbers() -> None:
    """Refuses or avoids non-standard `Infinity` in stored JSON (audit 9.7 item 11)."""
    show = two_set_show()
    broken = show.model_copy(
        update={
            "sets": (show.sets[0], show.sets[1].model_copy(update={"counts": math.inf})),
        }
    )
    try:
        docs = encode_show(broken)
    except ShowPyError:
        return
    for raw in docs.values():
        json.loads(raw, parse_constant=_reject_constant)


def _with_first_x(show: Show, x: float) -> Show:
    first = show.sets[0]
    moved = first.placements[0].model_copy(update={"x": x})
    updated = first.model_copy(update={"placements": (moved, *first.placements[1:])})
    return show.model_copy(update={"sets": (updated, *show.sets[1:])})


def test_regression_negative_zero_encodes_like_zero() -> None:
    """Writes equal bytes for -0.0 and 0.0, which are equal models (audit 9.7 item 11)."""
    positive = _with_first_x(two_set_show(), 0.0)
    negative = _with_first_x(two_set_show(), -0.0)
    assert positive.sets == negative.sets
    assert encode_show(positive) == encode_show(negative)


@pytest.mark.parametrize(
    "value",
    [
        {"b": [1.5, None, "-0.07"], "a": {"z": 1, "y": True}},
        {"x": -0.0, "y": [0.0, -0.0]},
        {"$showpy:map": 1},
        {"huge": INT_LIMIT},
        {"long": "x" * 1000},
    ],
)
def test_regression_fast_dump_matches_dump_json(value: object) -> None:
    try:
        expected = dump_json(value)
    except SchemaError:
        with pytest.raises(SchemaError):
            dump_dumped_json(value)
        return
    assert dump_dumped_json(value) == expected


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf, "\ud800"])
def test_regression_fast_dump_refuses_what_dump_json_refuses(value: object) -> None:
    with pytest.raises(SchemaError):
        dump_json({"v": value})
    with pytest.raises(SchemaError):
        dump_dumped_json({"v": value})


def test_regression_fast_dump_writes_no_negative_zero() -> None:
    assert b"-0.0" not in dump_dumped_json({"x": -0.0, "y": [-0.0]})
    assert dump_dumped_json({"x": -0.0}) == dump_dumped_json({"x": 0.0})


def test_regression_encode_documents_matches_encode_show() -> None:
    show = two_set_show()
    docs = encode_show(show)
    wanted = [SHOW_DOC, "/sets/2/set.json", "/sets/9/set.json"]
    assert encode_documents(show, wanted) == {p: docs[p] for p in wanted if p in docs}
    assert "/sets/9/set.json" not in encode_documents(show, wanted)


@pytest.mark.parametrize("raw", HOSTILE_JSON.values(), ids=HOSTILE_JSON.keys())
def test_regression_deep_or_huge_json_document_is_a_schema_error(raw: bytes) -> None:
    """Raises SchemaError naming show.json for nesting past the stack or an overlong int."""
    with pytest.raises(SchemaError) as caught:
        decode_show({**_docs(), SHOW_DOC: raw})
    assert caught.value.context["path"] == SHOW_DOC
