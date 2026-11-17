#!/usr/bin/env python3

"""
test_document_dump.py --- ties the dump_dumped_json fast path to dump_json on random JSON values

Contains:
    test_fast_dump_equals_dump_json: gives identical bytes, or the same SchemaError, from both
        writers for nested JSON values holding the cases canonical_value treats specially
    test_fast_dump_equals_dump_json_on_values_without_other_errors: does the same where only
        `-0.0` and reserved-prefix keys can differ, so one error cannot hide another
"""

from collections.abc import Callable

from hypothesis import given, settings
from hypothesis import strategies as st

from showpy.core.canonical import INT_LIMIT, MAX_NESTING
from showpy.core.errors import SchemaError
from showpy.show.documents import dump_dumped_json, dump_json

HUGE_INT = 10**700
CLEAN_EXAMPLES = 300
LOOKALIKES = ["-0.0", "-0.07", "x-0.0", "$showpy:map", "a$showpy:", "-0.0e1", "0.0"]

type Leaf = st.SearchStrategy[object]

texts = st.one_of(
    st.text(),
    st.sampled_from(LOOKALIKES),
    st.just("\ud800"),
    st.text(min_size=700, max_size=700, alphabet="0123456789"),
)
floats = st.one_of(
    st.floats(allow_nan=True, allow_infinity=True),
    st.sampled_from([-0.0, 0.0, 1.0, -1.0, 1e21, 1e-7, -0.07]),
)
ints = st.one_of(
    st.integers(),
    st.integers(min_value=INT_LIMIT - 5, max_value=HUGE_INT),
    st.integers(min_value=-HUGE_INT, max_value=-INT_LIMIT + 5),
)
scalars = st.one_of(st.none(), st.booleans(), ints, floats, texts)
keys = st.one_of(texts, st.text(max_size=3).map(lambda tail: f"$showpy:{tail}"))
clean_scalars = st.one_of(
    st.none(),
    st.booleans(),
    st.integers(min_value=-1000, max_value=1000),
    st.floats(allow_nan=False, allow_infinity=False),
    st.sampled_from([-0.0, 0.0, -0.07, 1.0]),
    st.sampled_from(LOOKALIKES),
)
clean_keys = st.one_of(st.sampled_from(LOOKALIKES), st.text(alphabet="abc", max_size=3))


def _nested(leaf: Leaf, key: st.SearchStrategy[str]) -> Leaf:
    return st.recursive(
        leaf,
        lambda inner: st.one_of(
            st.lists(inner, max_size=4), st.dictionaries(key, inner, max_size=4)
        ),
        max_leaves=12,
    )


def _nest(depth: int) -> object:
    value: object = [0]
    for _ in range(depth):
        value = {"k": value}
    return value


values = _nested(scalars, keys)
clean_values = _nested(clean_scalars, clean_keys)
deep_values = st.integers(min_value=MAX_NESTING - 2, max_value=MAX_NESTING + 3).map(_nest)


def _outcome(write: Callable[[object], bytes], value: object) -> bytes | str:
    try:
        return write(value)
    except SchemaError as exc:
        return f"SchemaError:{exc.code}"


@given(st.one_of(values, deep_values))
def test_fast_dump_equals_dump_json(value: object) -> None:
    assert _outcome(dump_dumped_json, value) == _outcome(dump_json, value)


@settings(max_examples=CLEAN_EXAMPLES)
@given(clean_values)
def test_fast_dump_equals_dump_json_on_values_without_other_errors(value: object) -> None:
    assert _outcome(dump_dumped_json, value) == _outcome(dump_json, value)
