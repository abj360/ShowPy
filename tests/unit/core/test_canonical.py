#!/usr/bin/env python3

"""
test_canonical.py --- tests canonical values and the RFC 8785 encoder of ADR 0014

Contains:
    test_jcs_number_*(): one RFC 8785 Appendix B vector or ECMAScript number rule each
    test_jcs_*(): one serialization rule of RFC 8785 each
    test_canonical_value_*(): one normalization rule of ADR 0014 decision 1 each
    test_*_rejects_*(): one input that must raise SchemaError each
    test_jcs_matches_the_rfc8785_package(): compares jcs with the rfc8785 reference encoder
"""

import struct
from collections.abc import Iterator, Mapping

import numpy as np
import pytest
import rfc8785
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

from showpy.core.canonical import JsonValue, canonical_value, jcs
from showpy.core.errors import SchemaError

RFC_SAFE_INT = 2**53 - 1
LONG_ARRAY_LENGTH = 2_000


class Inner(BaseModel):
    """Holds one float, to test nested model normalization."""

    value: float


class Outer(BaseModel):
    """Holds a nested model and a tuple, to test model normalization."""

    inner: Inner
    labels: tuple[str, ...]


class FixedMapping(Mapping[object, object]):
    """Exposes a fixed list of pairs as a read-only mapping, keeping duplicate-looking keys."""

    def __init__(self, pairs: list[tuple[object, object]]) -> None:
        self._pairs = dict(pairs)

    def __getitem__(self, key: object) -> object:
        return self._pairs[key]

    def __iter__(self) -> Iterator[object]:
        return iter(self._pairs)

    def __len__(self) -> int:
        return len(self._pairs)


def _from_bits(hex_bits: str) -> float:
    return float(struct.unpack(">d", bytes.fromhex(hex_bits))[0])


@pytest.mark.parametrize(
    ("hex_bits", "expected"),
    [
        ("0000000000000001", "5e-324"),
        ("8000000000000000", "0"),
        ("4340000000000000", "9007199254740992"),
        ("4430000000000000", "295147905179352830000"),
        ("444b1ae4d6e2ef50", "1e+21"),
        ("44b52d02c7e14af6", "1e+23"),
        ("3eb0c6f7a0b5ed8d", "0.000001"),
        ("41b3de4355555555", "333333333.3333333"),
    ],
)
def test_jcs_number_matches_rfc8785_appendix_b(hex_bits: str, expected: str) -> None:
    assert jcs(_from_bits(hex_bits)) == expected.encode()


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (1e-7, "1e-7"),
        (1.5e-7, "1.5e-7"),
        (1e20, "100000000000000000000"),
        (1.2345e20, "123450000000000000000"),
        (-2.5, "-2.5"),
        (1e300, "1e+300"),
        (-1.75e-300, "-1.75e-300"),
        (123.456, "123.456"),
        (0.1, "0.1"),
    ],
)
def test_jcs_number_follows_ecmascript_number_to_string(value: float, expected: str) -> None:
    assert jcs(value) == expected.encode()


def test_jcs_number_prints_an_integral_float_as_an_integer() -> None:
    assert jcs({"b": 1.0, "a": [True, None]}) == b'{"a":[true,null],"b":1}'


def test_jcs_number_prints_negative_zero_as_zero() -> None:
    assert jcs(-0.0) == b"0"


def test_jcs_number_keeps_python_ints_exact_beyond_the_float_range() -> None:
    assert jcs(2**64 + 1) == b"18446744073709551617"


def test_jcs_sorts_keys_by_utf16_code_units() -> None:
    keys = ["\u20ac", "\r", "\ufb33", "1", "\U0001f600", "\u0080", "\u00f6"]

    expected = '{"\\r":0,"1":0,"\u0080":0,"\u00f6":0,"\u20ac":0,"\U0001f600":0,"\ufb33":0}'
    assert jcs(dict.fromkeys(keys, 0)) == expected.encode()


def test_jcs_escapes_strings_minimally() -> None:
    text = 'q" b\\ \b\f\n\r\t \x00\x1f \x7f é \u2028 \U0001f600 /'

    expected = '"q\\" b\\\\ \\b\\f\\n\\r\\t \\u0000\\u001f \x7f é \u2028 \U0001f600 /"'
    assert jcs(text) == expected.encode()


def test_jcs_writes_no_whitespace() -> None:
    assert jcs({"a": [1, {"b": "c d"}], "e": {}}) == b'{"a":[1,{"b":"c d"}],"e":{}}'


def test_jcs_tags_a_mapping_with_non_string_keys() -> None:
    assert jcs({1: "a"}) == b'{"$showpy:map":[[1,"a"]]}'


def test_jcs_distinguishes_int_keys_from_string_keys() -> None:
    assert jcs({1: "a"}) != jcs({"1": "a"})


def test_jcs_ignores_insertion_order_of_mixed_keys() -> None:
    assert jcs({1: "a", "b": 2}) == jcs({"b": 2, 1: "a"})


def test_canonical_value_sorts_tagged_pairs_by_key_bytes() -> None:
    assert canonical_value({(0, 1): None, 1: "a", "b": 2}) == {
        "$showpy:map": [["b", 2], [1, "a"], [[0, 1], None]]
    }


def test_canonical_value_breaks_ties_between_equal_keys_by_value() -> None:
    forward = FixedMapping([((1,), "x"), (frozenset({1}), "a")])
    backward = FixedMapping([(frozenset({1}), "a"), ((1,), "x")])

    assert canonical_value(forward) == canonical_value(backward)
    assert canonical_value(forward) == {"$showpy:map": [[[1], "a"], [[1], "x"]]}


def test_canonical_value_turns_negative_zero_into_zero() -> None:
    value = canonical_value(-0.0)

    assert isinstance(value, float)
    assert str(value) == "0.0"


def test_canonical_value_keeps_integral_floats_as_floats() -> None:
    value = canonical_value(1.0)

    assert isinstance(value, float)


def test_canonical_value_sorts_sets_by_canonical_bytes() -> None:
    assert canonical_value({"T3", "T1", "T2"}) == ["T1", "T2", "T3"]
    assert canonical_value(frozenset({10, 9, "a"})) == ["a", 10, 9]


def test_canonical_value_turns_tuples_into_lists() -> None:
    assert canonical_value((1, (2, "x"))) == [1, [2, "x"]]


def test_canonical_value_dumps_nested_models() -> None:
    model = Outer(inner=Inner(value=-0.0), labels=("a", "b"))

    assert canonical_value(model) == {"inner": {"value": 0.0}, "labels": ["a", "b"]}


def test_canonical_value_keeps_every_element_of_a_long_numpy_array() -> None:
    first = np.zeros(LONG_ARRAY_LENGTH)
    second = first.copy()
    second[LONG_ARRAY_LENGTH // 2] = 1.0

    assert jcs(first) != jcs(second)
    assert canonical_value(first) == [0.0] * LONG_ARRAY_LENGTH


def test_canonical_value_unwraps_numpy_scalars() -> None:
    values = [np.int64(3), np.float32(0.5), np.bool_(True), np.str_("x")]

    items = canonical_value(values)

    assert items == [3, 0.5, True, "x"]
    assert isinstance(items, list)
    assert [type(item) for item in items] == [int, float, bool, str]


def test_canonical_value_keeps_booleans_distinct_from_numbers() -> None:
    assert jcs(True) != jcs(1)
    assert jcs({True: "a"}) != jcs({1: "a"})


def test_canonical_value_returns_plain_builtin_types_for_subclasses() -> None:
    class Label(str):
        pass

    value = canonical_value({Label("k"): Label("v")})

    assert value == {"k": "v"}
    assert isinstance(value, dict)
    assert [type(item) for pair in value.items() for item in pair] == [str, str]


@pytest.mark.parametrize(
    "bad",
    [
        float("nan"),
        float("inf"),
        -float("inf"),
        np.float64("nan"),
        [1.0, float("nan")],
        Inner(value=0.0).model_copy(update={"value": float("nan")}),
    ],
)
def test_canonical_value_rejects_non_finite_floats(bad: object) -> None:
    with pytest.raises(SchemaError):
        canonical_value(bad)


@pytest.mark.parametrize("bad", ["\ud800", "a\udfffb", {"\udc00": 1}, ["ok", "\ud83d"]])
def test_canonical_value_rejects_lone_surrogates(bad: object) -> None:
    with pytest.raises(SchemaError):
        canonical_value(bad)


@pytest.mark.parametrize(
    "bad",
    [{"$showpy:map": 1}, {"$showpy:other": 1}, {"$showpy:map": [], 1: 2}, [{"$showpy:x": 0}]],
)
def test_canonical_value_rejects_reserved_keys(bad: object) -> None:
    with pytest.raises(SchemaError):
        canonical_value(bad)


@pytest.mark.parametrize(
    "bad",
    [object(), 1j, b"bytes", {1: object()}, {object(): 1}, np.datetime64("2026-01-01")],
)
def test_canonical_value_rejects_unsupported_types(bad: object) -> None:
    with pytest.raises(SchemaError):
        canonical_value(bad)


def test_canonical_value_rejects_a_cycle() -> None:
    cyclic: list[object] = []
    cyclic.append(cyclic)

    with pytest.raises(SchemaError):
        canonical_value(cyclic)


def test_canonical_value_rejects_deep_nesting() -> None:
    nested: object = 0
    for _ in range(10_000):
        nested = [nested]

    with pytest.raises(SchemaError):
        canonical_value(nested)


def test_canonical_value_rejects_an_int_too_long_to_print_everywhere() -> None:
    with pytest.raises(SchemaError):
        canonical_value(10**700)


def test_jcs_rejects_what_canonical_value_rejects() -> None:
    with pytest.raises(SchemaError):
        jcs(float("nan"))


def _json_keys() -> st.SearchStrategy[str]:
    return _json_text().filter(lambda key: not key.startswith("$showpy:"))


def _json_text() -> st.SearchStrategy[str]:
    return st.text(st.characters(exclude_categories=["Cs"]))


JSON_SCALARS: st.SearchStrategy[JsonValue] = (
    st.none()
    | st.booleans()
    | st.integers(min_value=-RFC_SAFE_INT, max_value=RFC_SAFE_INT)
    | st.floats(allow_nan=False, allow_infinity=False)
    | _json_text()
)
JSON_VALUES: st.SearchStrategy[JsonValue] = st.recursive(
    JSON_SCALARS,
    lambda children: st.lists(children) | st.dictionaries(_json_keys(), children),
    max_leaves=30,
)


@given(JSON_VALUES)
def test_jcs_matches_the_rfc8785_package(value: JsonValue) -> None:
    """Compares jcs with rfc8785.dumps on JSON-model values both encoders accept.

    Ints are drawn within +-(2**53 - 1) only: rfc8785 rejects larger ints with
    IntegerDomainError, while jcs keeps Python ints exact at any size.
    """
    assert jcs(value) == rfc8785.dumps(value)
