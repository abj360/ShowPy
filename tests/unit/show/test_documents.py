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
    test_regression_show_json_carries_a_format_version: stamps show.json with a format and layout
        version
    test_regression_decode_names_the_minimum_version_for_a_newer_document: raises VersionError for a
        document written by a newer library
    test_regression_encode_never_writes_non_finite_numbers: refuses or avoids non-standard
        `Infinity` in stored JSON
    test_regression_negative_zero_encodes_like_zero: writes equal bytes for -0.0 and 0.0, which are
        equal models
"""

import json
import math

import pytest

from showpy.core.errors import ShowPyError, VersionError
from showpy.show.documents import SHOW_DOC, decode_show, encode_show, forms_doc
from showpy.show.models import Show
from tests.fixtures.shows import two_set_show

FUTURE_VERSION = 999
VERSION_KEYS = ("format", "version", "schema_version", "layout_version")


def _docs() -> dict[str, bytes]:
    return encode_show(two_set_show())


@pytest.mark.defect("UP-9.7-19")
@pytest.mark.xfail(strict=True, reason="UP-9.7-19")
@pytest.mark.parametrize("raw", [b"5", b'"text"'])
def test_regression_decode_wrong_shape_show_json_is_a_library_error(raw: bytes) -> None:
    """Raises a ShowPyError, not TypeError, for a show.json of the wrong shape (audit 9.7 #9)."""
    with pytest.raises(ShowPyError):
        decode_show({**_docs(), SHOW_DOC: raw})


@pytest.mark.defect("UP-9.7-20")
@pytest.mark.xfail(strict=True, reason="UP-9.7-20")
def test_regression_decode_wrong_shape_forms_json_is_a_library_error() -> None:
    """Raises a ShowPyError, not TypeError, for a forms.json of the wrong shape (audit 9.7 #9)."""
    with pytest.raises(ShowPyError):
        decode_show({**_docs(), forms_doc("2"): b"5"})


@pytest.mark.defect("UP-9.7-21")
@pytest.mark.xfail(strict=True, reason="UP-9.7-21")
def test_regression_decode_rejects_string_set_order() -> None:
    """Rejects `"sets": "12"` instead of decoding it as sets 1 and 2 (audit 9.7 item 9)."""
    body = json.loads(_docs()[SHOW_DOC]) | {"sets": "12"}
    with pytest.raises(ShowPyError):
        decode_show({**_docs(), SHOW_DOC: json.dumps(body).encode()})


@pytest.mark.defect("UP-9.7-29")
@pytest.mark.xfail(strict=True, reason="UP-9.7-29")
def test_regression_show_json_carries_a_format_version() -> None:
    """Stamps show.json with a format and layout version (audit 9.7 item 11)."""
    body = json.loads(_docs()[SHOW_DOC])
    assert any(key in body for key in VERSION_KEYS)


@pytest.mark.defect("UP-9.7-92")
@pytest.mark.xfail(strict=True, reason="UP-9.7-92")
def test_regression_decode_names_the_minimum_version_for_a_newer_document() -> None:
    """Raises VersionError for a document written by a newer library (audit 9.7 item 11)."""
    body = json.loads(_docs()[SHOW_DOC]) | {"version": FUTURE_VERSION}
    with pytest.raises(VersionError) as excinfo:
        decode_show({**_docs(), SHOW_DOC: json.dumps(body).encode()})
    message = str(excinfo.value).lower()
    assert str(FUTURE_VERSION) in message or "minimum" in message


def _reject_constant(name: str) -> float:
    raise AssertionError(f"non-standard JSON constant {name}")


@pytest.mark.defect("UP-9.7-30")
@pytest.mark.xfail(strict=True, reason="UP-9.7-30")
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


@pytest.mark.defect("UP-9.7-31")
@pytest.mark.xfail(strict=True, reason="UP-9.7-31")
def test_regression_negative_zero_encodes_like_zero() -> None:
    """Writes equal bytes for -0.0 and 0.0, which are equal models (audit 9.7 item 11)."""
    positive = _with_first_x(two_set_show(), 0.0)
    negative = _with_first_x(two_set_show(), -0.0)
    assert positive.sets == negative.sets
    assert encode_show(positive) == encode_show(negative)
