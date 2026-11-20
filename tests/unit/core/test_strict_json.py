#!/usr/bin/env python3

"""
test_strict_json.py --- tests the strict JSON parser for stored and imported documents

Contains:
    test_strict_parse_reads_standard_json(): parses objects, arrays, numbers and escaped pairs
    test_strict_parse_refuses_non_standard_tokens(): refuses NaN, Infinity and -Infinity tokens
    test_strict_parse_refuses_a_repeated_key(): names the first key that repeats, at any depth
    test_strict_parse_refuses_a_byte_order_mark(): refuses a leading UTF-8 BOM
    test_strict_parse_refuses_a_lone_surrogate(): refuses a \\ud800 escape outside a pair
    test_strict_parse_refuses_non_utf8_bytes(): refuses UTF-16 and invalid UTF-8
    test_strict_parse_turns_deep_nesting_into_schema_error(): never leaks RecursionError
    test_strict_copy_matches_the_value(): copies lists, tuples and dicts as JSON values
    test_strict_copy_refuses_what_json_cannot_hold(): refuses NaN, sets, surrogates and keys that
        collide once turned into text
"""

import math

import pytest

from showpy.core.error_codes import ErrorCode
from showpy.core.errors import SchemaError
from showpy.core.strict_json import parse_strict_json, strict_json_copy

DOC = "/show/show.json"
DEEP = 100_000


def _refusal(raw: bytes) -> SchemaError:
    with pytest.raises(SchemaError) as caught:
        parse_strict_json(raw, document=DOC)
    assert caught.value.context["path"] == DOC
    return caught.value


def test_strict_parse_reads_standard_json() -> None:
    raw = b'{"a": [1, 2.5, -0.0, null, true], "b": {"c": "\\ud83d\\ude00 \xc3\xa9"}}'
    assert parse_strict_json(raw, document=DOC) == {
        "a": [1, 2.5, -0.0, None, True],
        "b": {"c": "\U0001f600 é"},
    }


@pytest.mark.parametrize("token", ["NaN", "Infinity", "-Infinity"])
def test_strict_parse_refuses_non_standard_tokens(token: str) -> None:
    error = _refusal(f'{{"a": [1, {token}]}}'.encode())
    assert error.code == ErrorCode.STRICT_JSON
    assert error.context["token"] == token


@pytest.mark.parametrize(
    ("raw", "key"),
    [(b'{"a": 1, "a": 2}', "a"), (b'{"x": [{"b": 1, "c": 2, "b": 3}]}', "b")],
)
def test_strict_parse_refuses_a_repeated_key(raw: bytes, key: str) -> None:
    error = _refusal(raw)
    assert error.code == ErrorCode.STRICT_JSON
    assert error.context["key"] == key


def test_strict_parse_refuses_a_byte_order_mark() -> None:
    error = _refusal(b'\xef\xbb\xbf{"a": 1}')
    assert error.code == ErrorCode.STRICT_JSON
    assert error.context["position"] == 0


@pytest.mark.parametrize("raw", [b'{"a": "\\ud800"}', b'{"\\udfff": 1}', b'["x\\ud83d"]'])
def test_strict_parse_refuses_a_lone_surrogate(raw: bytes) -> None:
    assert _refusal(raw).code == ErrorCode.STRICT_JSON


@pytest.mark.parametrize("raw", ['{"a": 1}'.encode("utf-16"), b'{"a": "\xff"}'])
def test_strict_parse_refuses_non_utf8_bytes(raw: bytes) -> None:
    assert _refusal(raw).code == ErrorCode.SCHEMA


def test_strict_parse_turns_deep_nesting_into_schema_error() -> None:
    assert _refusal(b"[" * DEEP + b"]" * DEEP).code == ErrorCode.SCHEMA


def test_strict_copy_matches_the_value() -> None:
    assert strict_json_copy({"a": (1, [2.5]), "b": None}, document="doc") == {
        "a": [1, [2.5]],
        "b": None,
    }


@pytest.mark.parametrize(
    "value",
    [{"a": math.nan}, {"a": math.inf}, {"a": {1, 2}}, {"a": "\ud800"}, {1: "x", "1": "y"}],
)
def test_strict_copy_refuses_what_json_cannot_hold(value: object) -> None:
    with pytest.raises(SchemaError) as caught:
        strict_json_copy(value, document="doc")
    assert caught.value.code == ErrorCode.STRICT_JSON
