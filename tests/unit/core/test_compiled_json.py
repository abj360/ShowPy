#!/usr/bin/env python3

"""
test_compiled_json.py --- tests the compiled canonical writer, content hash and strict JSON reader

Contains:
    test_core_serves_the_canonical_hash_and_strict_json_names: the Python modules re-export the
        compiled functions and constants
    test_strict_parse_takes_bytes_and_bytearrays_only: reads both, refuses other types
    test_strict_parse_words_invalid_json_as_the_interpreter_does: repeats the message of
        Python's own decoder and JSON reader, with its exception as the cause
    test_strict_parse_follows_the_interpreter_int_digit_limit: reads the limit at each call
    test_strict_parse_gives_a_lone_surrogate_a_unicode_encode_error_cause: keeps v0.4.0's cause
    test_strict_parse_names_a_repeated_key_that_holds_a_lone_surrogate: keeps the key's text
    test_canonical_value_keeps_one_member_for_keys_that_repeat_in_a_mapping: as a dict would
    test_content_hash_refuses_a_domain_with_a_lone_surrogate: keeps the domain in the context
    test_content_hash_refuses_bad_keywords_and_non_text_domains: raises TypeError as v0.4.0 did
    test_asset_uri_hashes_any_buffer_as_hashlib_does: names bytearray and memoryview bytes
    test_content_hash_keeps_its_v040_signature: takes obj and a keyword-only domain
    test_hashing_large_bytes_lets_other_threads_run: releases the GIL as hashlib does, with the
        same digests on both sides of the size threshold
    test_regression_jcs_breaks_a_digit_tie_to_even_as_python_repr_does: writes the nearer of two
        shortest digit strings, and the even one when both are equally near
    test_regression_canonical_value_refuses_endless_unwrapping: refuses a model whose dump is
        itself and an array whose tolist is itself as nesting
"""

import hashlib
import inspect
import json
import sys
import threading
import time
from collections.abc import Iterator, Mapping
from typing import Any, cast

import numpy as np
import pytest
from pydantic import BaseModel

from e8s._core.core import (
    asset_uri,
    canonical_value,
    content_hash,
    jcs,
    json_nesting,
    parse_strict_json,
    require_nesting,
    strict_json_copy,
)
from e8s.core import canonical, strict_json, util
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import SchemaError

DOC = "d.json"
GIL_FREE_BYTES = 2048
LARGE_PAYLOAD = 4 * 2**20
SWITCH_INTERVAL_S = 30.0
HASHING_BUDGET_S = 5.0
COUNTER_NAP_S = 0.0001
WAIT_STEP_S = 0.001
SMALL_DIGIT_LIMIT = 640
DIGITS_PAST_SMALL_LIMIT = 641


class RepeatingMapping(Mapping[str, int]):
    """Yields the key "a" twice, as a mapping over a list of pairs could."""

    def __getitem__(self, key: str) -> int:
        return len(key)

    def __iter__(self) -> Iterator[str]:
        return iter(["a", "b", "a"])

    def __len__(self) -> int:
        return 3


def test_core_serves_the_canonical_hash_and_strict_json_names() -> None:
    assert (canonical.canonical_value, canonical.jcs) == (canonical_value, jcs)
    assert (util.content_hash, util.asset_uri) == (content_hash, asset_uri)
    assert strict_json.parse_strict_json is parse_strict_json
    assert strict_json.strict_json_copy is strict_json_copy
    assert strict_json.json_nesting is json_nesting
    assert strict_json.require_nesting is require_nesting
    assert canonical.INT_LIMIT == 10**canonical.MAX_INT_DIGITS
    assert (canonical.MAX_NESTING, canonical.MAP_TAG) == (100, "$showpy:map")
    assert (util.HASH_PREFIX, util.ASSET_PREFIX) == ("showpy-hash/1", "asset:sha256:")


def test_strict_parse_takes_bytes_and_bytearrays_only() -> None:
    assert parse_strict_json(cast("bytes", bytearray(b"[1]")), document=DOC) == [1]
    assert json_nesting(cast("bytes", bytearray(b"[[]]"))) == 2
    for wrong in ("[1]", memoryview(b"[1]"), None):
        with pytest.raises(TypeError):
            parse_strict_json(wrong, document=DOC)  # type: ignore[arg-type]  # refused type


def _python_reason(raw: bytes) -> ValueError:
    """Returns the exception Python's own decoder and JSON reader raise for the bytes."""
    try:
        json.loads(raw.decode("utf-8"))
    except ValueError as reason:
        return reason
    raise AssertionError(raw)


@pytest.mark.parametrize("raw", [b"[1,]", b'{"a" 1}', b"[1] 2", b'["\xff"]', b'"\\x"'])
def test_strict_parse_words_invalid_json_as_the_interpreter_does(raw: bytes) -> None:
    expected = _python_reason(raw)

    with pytest.raises(SchemaError) as caught:
        parse_strict_json(raw, document=DOC)

    assert caught.value.message == f"{DOC} is not valid JSON: {expected}"
    assert type(caught.value.__cause__) is type(expected)
    assert caught.value.code == ErrorCode.SCHEMA


def test_strict_parse_follows_the_interpreter_int_digit_limit() -> None:
    allowed = b"9" * SMALL_DIGIT_LIMIT
    refused = b"-" + b"9" * DIGITS_PAST_SMALL_LIMIT
    limit = sys.get_int_max_str_digits()
    assert parse_strict_json(refused, document=DOC) == -int(refused[1:])
    try:
        sys.set_int_max_str_digits(SMALL_DIGIT_LIMIT)
        assert parse_strict_json(allowed, document=DOC) == int(allowed)
        with pytest.raises(SchemaError) as caught:
            parse_strict_json(refused, document=DOC)
    finally:
        sys.set_int_max_str_digits(limit)
    assert "Exceeds the limit (640 digits)" in caught.value.message
    assert isinstance(caught.value.__cause__, ValueError)


def test_strict_parse_gives_a_lone_surrogate_a_unicode_encode_error_cause() -> None:
    with pytest.raises(SchemaError) as caught:
        parse_strict_json(b'["\\ud800"]', document=DOC)

    assert caught.value.code == ErrorCode.STRICT_JSON
    assert isinstance(caught.value.__cause__, UnicodeEncodeError)


def test_strict_parse_names_a_repeated_key_that_holds_a_lone_surrogate() -> None:
    with pytest.raises(SchemaError) as caught:
        parse_strict_json(b'{"\\ud800": 1, "\\ud800": 2}', document=DOC)

    assert caught.value.code == ErrorCode.STRICT_JSON
    assert caught.value.context["key"] == "\ud800"
    assert caught.value.message == f"{DOC} is not strict JSON: repeated key '\\ud800'"


def test_canonical_value_keeps_one_member_for_keys_that_repeat_in_a_mapping() -> None:
    assert canonical_value(RepeatingMapping()) == {"a": 1, "b": 1}
    assert jcs(RepeatingMapping()) == b'{"a":1,"b":1}'


def test_content_hash_refuses_a_domain_with_a_lone_surrogate() -> None:
    with pytest.raises(SchemaError) as caught:
        content_hash("x", domain="a\ud800")

    assert caught.value.context["domain"] == "a\ud800"
    assert caught.value.message == "hash domain 'a\\ud800' is not allowed"


def test_content_hash_refuses_bad_keywords_and_non_text_domains() -> None:
    with pytest.raises(TypeError, match="unexpected keyword argument 'domains'"):
        content_hash("x", domains="showpy.content")  # type: ignore[call-arg]  # refused keyword
    for domain in (None, b"showpy.content", 1):
        with pytest.raises(TypeError):
            content_hash("x", domain=domain)  # type: ignore[arg-type]  # refused domain type


def test_content_hash_keeps_its_v040_signature() -> None:
    assert str(inspect.signature(content_hash)) == "(obj, *, domain='showpy.content')"
    assert content_hash(obj=b"x") == content_hash(b"x", domain="showpy.content")
    with pytest.raises(TypeError):
        content_hash(b"x", "showpy.content")  # type: ignore[call-arg]  # domain is keyword-only


def test_asset_uri_hashes_any_buffer_as_hashlib_does() -> None:
    digest = hashlib.sha256(b"font").hexdigest()

    for data in (b"font", bytearray(b"font"), memoryview(b"font")):
        assert asset_uri(data) == f"asset:sha256:{digest}"  # type: ignore[arg-type]  # buffers
    with pytest.raises(TypeError):
        asset_uri("font")  # type: ignore[arg-type]  # text is refused as hashlib refuses it


@pytest.mark.parametrize(
    ("value", "expected"),
    [(165.035919189453125, b"165.03591918945312"), (-649435869553220.25, b"-649435869553220.2")],
)
def test_regression_jcs_breaks_a_digit_tie_to_even_as_python_repr_does(
    value: float, expected: bytes
) -> None:
    assert repr(value).encode() == expected
    assert jcs(value) == expected


class EndlessModel(BaseModel):
    """Dumps to itself, so unwrapping it never reaches plain values."""

    def model_dump(self, **_options: object) -> dict[str, object]:
        return cast("dict[str, object]", self)


class EndlessArray(np.ndarray):
    """Lists to itself, so unwrapping it never reaches plain values."""

    # Any: numpy declares tolist as returning Any, and an override must match it.
    def tolist(self) -> Any:
        return self


@pytest.mark.parametrize("value", [EndlessModel(), np.zeros(2).view(EndlessArray)])
def test_regression_canonical_value_refuses_endless_unwrapping(value: object) -> None:
    with pytest.raises(SchemaError) as caught:
        canonical_value(value)

    assert caught.value.context["max_nesting"] == canonical.MAX_NESTING


def _hashes_while_counting(data: bytes) -> int:
    """Hashes data again and again while a second thread counts, returning how far it counted.

    The counter naps after each count, so it hands the GIL back by itself, and the switch
    interval outlasts the measurement, so the hashing thread is never made to give the GIL up.
    The count can grow during the hashing only while a hash lets go of the GIL; a hash that holds
    it leaves the count at exactly 0. Hashing stops at the first count or after HASHING_BUDGET_S,
    so a loaded machine gets hundreds of chances to run the counter.
    """
    count = [0]
    stop = threading.Event()

    def counting() -> None:
        while not stop.is_set():
            count[0] += 1
            time.sleep(COUNTER_NAP_S)

    interval = sys.getswitchinterval()
    thread = threading.Thread(target=counting)
    try:
        sys.setswitchinterval(SWITCH_INTERVAL_S)
        thread.start()
        while count[0] == 0:
            time.sleep(WAIT_STEP_S)
        before = count[0]
        deadline = time.monotonic() + HASHING_BUDGET_S
        while count[0] == before and time.monotonic() < deadline:
            content_hash(data)
        during = count[0] - before
    finally:
        sys.setswitchinterval(interval)
        stop.set()
        if thread.is_alive():
            thread.join()
    return during


def test_hashing_large_bytes_lets_other_threads_run() -> None:
    for size in (GIL_FREE_BYTES - 1, GIL_FREE_BYTES, LARGE_PAYLOAD):
        data = bytes(range(256)) * (size // 256) + b"x" * (size % 256)
        head = b"showpy-hash/1\nshowpy.content\nbytes\n"
        assert content_hash(data) == hashlib.sha256(head + data).hexdigest()
        assert asset_uri(data) == f"asset:sha256:{hashlib.sha256(data).hexdigest()}"
    assert _hashes_while_counting(b"x" * LARGE_PAYLOAD) > 0
