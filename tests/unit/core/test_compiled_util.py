#!/usr/bin/env python3

"""
test_compiled_util.py --- tests the compiled float helpers, ID ranges, close names and CSV guard

Contains:
    test_core_serves_the_utilities_and_csv_cells: the Python modules re-export the compiled
        functions and constants
    test_utilities_keep_their_v040_signatures: approx_equal keeps keyword-only tolerances
    test_approx_equal_refuses_a_tolerance_of_none: compares None with 0 and raises TypeError
    test_numbers_beyond_64_bits_stay_exact: split_id, parse_range and chunk_counts keep every
        digit in values and error contexts
    test_float_part_counts_fail_where_v040_failed: checks parts, then the total, then the cap,
        then refuses the float
    test_text_with_lone_surrogates_sorts_and_splits_by_code_point: orders and splits such text as
        Python's own string methods do
    test_non_text_arguments_raise_the_error_v040_raised: lets the object's own method fail first
    test_regression_range_errors_show_a_lone_surrogate_as_replacement: writes U+FFFD in the
        message and context, as the ID checks do
    test_regression_chunk_counts_splits_other_number_types_as_floats: returns floats for a
        Fraction or numpy total
    test_regression_chunk_counts_refuses_other_part_types_with_type_error: refuses a Fraction
        part count before reading the total
    test_regression_close_matches_takes_text_only: refuses tuples, which difflib matched
"""

import inspect
import math
import re
from collections.abc import Callable
from fractions import Fraction
from typing import cast

import numpy as np
import pytest

from e8s._core import core
from e8s.core import csv_cells, util
from e8s.core.errors import SchemaError

SURROGATE = "\ud800"
REPLACEMENT = "\ufffd"
BIG = 10**30
CAP = 1_000_000
V040_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")
V040_DECIMAL = r"[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?"


def _v040_natural_key(text: str) -> tuple[tuple[tuple[int, int, str], ...], str]:
    chunks = tuple(
        (0, len(stripped), stripped) if index % 2 else (1, 0, chunk)
        for index, chunk in enumerate(re.split("([0-9]+)", text))
        if chunk
        for stripped in (chunk.lstrip("0"),)
    )
    return chunks, text


def test_core_serves_the_utilities_and_csv_cells() -> None:
    for name in (
        "approx_equal",
        "chunk_counts",
        "close_matches",
        "format_ranges",
        "natural_key",
        "natural_sort",
        "noise_rank",
        "parse_range",
        "split_id",
    ):
        assert getattr(util, name) is getattr(core, name), name
    assert csv_cells.neutralize_cell is core.neutralize_cell
    assert csv_cells.FORMULA_TRIGGERS == V040_TRIGGERS
    assert csv_cells.FORMULA_GUARD == "'"
    assert csv_cells.PLAIN_DECIMAL.pattern == V040_DECIMAL
    assert csv_cells.PLAIN_DECIMAL.flags & re.ASCII


def test_utilities_keep_their_v040_signatures() -> None:
    assert str(inspect.signature(util.approx_equal)) == "(a, b, *, abs_tol=1e-06, rel_tol=0.0)"
    assert util.approx_equal(1.0, 1.0 + 1e-7, abs_tol=1e-6)
    loose = cast("Callable[..., bool]", util.approx_equal)
    with pytest.raises(TypeError):
        loose(1.0, 1.0, 1e-6)


def test_approx_equal_refuses_a_tolerance_of_none() -> None:
    with pytest.raises(TypeError):
        util.approx_equal(1.0, 1.0, abs_tol=cast("float", None))


def test_numbers_beyond_64_bits_stay_exact() -> None:
    assert util.split_id(f"T{BIG}") == ("T", BIG)
    with pytest.raises(SchemaError) as caught:
        util.parse_range(f"T1-T{BIG}")
    assert caught.value.context["requested"] == BIG
    assert util.parse_range("T99999999999999999999-T100000000000000000001") == [
        "T99999999999999999999",
        "T100000000000000000000",
        "T100000000000000000001",
    ]
    with pytest.raises(SchemaError) as too_many:
        util.chunk_counts(8.0, BIG)
    assert too_many.value.context["requested"] == BIG
    with pytest.raises(SchemaError) as too_few:
        util.chunk_counts(8.0, -BIG)
    assert too_few.value.context["parts"] == -BIG
    assert util.chunk_counts(2**60 + 1, 3)[0] == (2**60 + 1) / 3


def test_float_part_counts_fail_where_v040_failed() -> None:
    with pytest.raises(SchemaError, match="parts must be at least 1"):
        util.chunk_counts(8.0, cast("int", 0.5))
    with pytest.raises(SchemaError, match="total must be finite"):
        util.chunk_counts(math.nan, cast("int", 2.0))
    with pytest.raises(SchemaError, match=r"chunks would need 2000000\.0, over the cap"):
        util.chunk_counts(8.0, cast("int", 2.0 * CAP))
    with pytest.raises(TypeError, match="cannot be interpreted as an integer"):
        util.chunk_counts(8.0, cast("int", 2.0))


def test_text_with_lone_surrogates_sorts_and_splits_by_code_point() -> None:
    ids = [f"T{SURROGATE}10", f"T{SURROGATE}2", "T\udc001", "T\U0001f6001", "T\uffff1"]
    expected = sorted(ids, key=_v040_natural_key)
    assert util.natural_sort(ids) == expected
    assert util.split_id(f"T{SURROGATE}12") == (f"T{SURROGATE}", 12)
    assert util.parse_range(f"T{SURROGATE}1-T{SURROGATE}2") == [f"T{SURROGATE}1", f"T{SURROGATE}2"]
    assert util.natural_key(f"{SURROGATE}7")[0] == ((1, 0, SURROGATE), (0, 1, "7"))
    assert csv_cells.neutralize_cell(f"={SURROGATE}") == f"'={SURROGATE}"


def test_non_text_arguments_raise_the_error_v040_raised() -> None:
    with pytest.raises(AttributeError):
        util.split_id(cast("str", 5))
    with pytest.raises(AttributeError):
        util.parse_range(cast("str", None))
    with pytest.raises(AttributeError):
        csv_cells.neutralize_cell(cast("str", 5))
    with pytest.raises(TypeError):
        csv_cells.neutralize_cell(cast("str", b"=1"))
    with pytest.raises(TypeError):
        util.natural_sort(cast("list[str]", [1, "T1"]))


def test_regression_range_errors_show_a_lone_surrogate_as_replacement() -> None:
    with pytest.raises(SchemaError) as caught:
        util.parse_range(f"{SURROGATE}-")
    assert caught.value.message == f"bad range '{REPLACEMENT}-'"
    assert caught.value.context == {"text": f"{REPLACEMENT}-"}
    with pytest.raises(SchemaError) as long_number:
        util.split_id(f"T{SURROGATE}{'9' * 65}")
    assert long_number.value.message.startswith(f"number in 'T{REPLACEMENT}9")


def test_regression_chunk_counts_splits_other_number_types_as_floats() -> None:
    thirds = util.chunk_counts(cast("float", Fraction(1)), 3)
    assert [type(part) for part in thirds] == [float, float, float]
    assert math.fsum(thirds) == 1.0
    assert [type(part) for part in util.chunk_counts(cast("float", np.float32(1.5)), 3)] == [
        float
    ] * 3
    assert [type(part) for part in util.chunk_counts(np.float64(9.0), 4)] == [float] * 4


def test_regression_chunk_counts_refuses_other_part_types_with_type_error() -> None:
    with pytest.raises(TypeError):
        util.chunk_counts(math.nan, cast("int", Fraction(1, 2)))


def test_regression_close_matches_takes_text_only() -> None:
    with pytest.raises(TypeError):
        util.close_matches("T1", cast("list[str]", [("T", "1")]))
    with pytest.raises(TypeError):
        util.close_matches(cast("str", ("T", "1")), ["T1"])
