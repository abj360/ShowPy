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
    test_strict_parse_keeps_large_finite_and_tiny_numbers(): reads 1.7e308 and 1e-400 as floats
    test_regression_strict_parse_refuses_nesting_past_the_cap(): refuses MAX_NESTING + 1 levels,
        far under any recursion limit, and reads MAX_NESTING levels
    test_json_nesting_counts_only_brackets_outside_strings(): ignores brackets and escaped quotes
        inside strings
"""

import math

import pytest

from showpy.core.canonical import MAX_NESTING
from showpy.core.error_codes import ErrorCode
from showpy.core.errors import SchemaError
from showpy.core.strict_json import json_nesting, parse_strict_json, strict_json_copy

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


def test_strict_parse_keeps_large_finite_and_tiny_numbers() -> None:
    assert parse_strict_json(b"[1.7e308, 1e-400, 2.5e10]", document="d.json") == [
        1.7e308,
        0.0,
        2.5e10,
    ]


def _nested(depth: int) -> bytes:
    return b"[" * depth + b"]" * depth


def test_regression_strict_parse_refuses_nesting_past_the_cap() -> None:
    error = _refusal(_nested(MAX_NESTING + 1))
    assert error.code == ErrorCode.SCHEMA
    assert error.context["max_nesting"] == MAX_NESTING
    assert parse_strict_json(_nested(MAX_NESTING), document=DOC) is not None


def test_json_nesting_counts_only_brackets_outside_strings() -> None:
    assert json_nesting(b"1") == 0
    assert json_nesting(b'{"a": [1, {"b": []}]}') == 4
    assert json_nesting(b'["[[[[{{{", "]]]]"]') == 1
    assert json_nesting(b'["\\"]') == 1
    assert json_nesting(b'["a\\"[[[", [[]]]') == 3
