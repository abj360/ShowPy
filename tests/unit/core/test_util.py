#!/usr/bin/env python3

"""
test_util.py --- tests ID sorting, ranges, float comparison, hashing and point normalization
"""

import numpy as np
import pytest
import shapely
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel

from showpy.core.errors import SchemaError
from showpy.core.util import (
    approx_equal,
    as_points,
    chunk_counts,
    close_matches,
    content_hash,
    format_ranges,
    log,
    natural_sort,
    parse_range,
    seeded_rng,
    timer,
)


def test_natural_sort_orders_numbers_numerically() -> None:
    assert natural_sort(["T10", "T2", "B1", "T1"]) == ["B1", "T1", "T2", "T10"]


def test_parse_range_expands() -> None:
    assert parse_range("T1-T4") == ["T1", "T2", "T3", "T4"]


def test_parse_range_keeps_zero_padding() -> None:
    assert parse_range("T08-T10") == ["T08", "T09", "T10"]


def test_parse_range_single_id() -> None:
    assert parse_range("flag-12") == ["flag-12"]


@pytest.mark.parametrize("text", ["T4-T1", "T1-B4", "T1-", ""])
def test_parse_range_rejects_bad_ranges(text: str) -> None:
    with pytest.raises(SchemaError):
        parse_range(text)


def test_format_ranges_compacts_runs() -> None:
    assert format_ranges(["T3", "T1", "T2", "T4", "T7", "B1"]) == "B1, T1–T4, T7"


def test_format_ranges_empty() -> None:
    assert format_ranges([]) == ""


def test_approx_equal_scalars_and_arrays() -> None:
    assert approx_equal(1.0, 1.0 + 1e-9)
    assert not approx_equal(1.0, 1.1)
    assert approx_equal(np.array([1.0, 2.0]), [1.0, 2.0 + 1e-9])
    assert not approx_equal(np.array([1.0, 2.0]), [1.0])


def test_seeded_rng_is_deterministic() -> None:
    assert seeded_rng(7).random() == seeded_rng(7).random()


def test_as_points_from_mixed_inputs() -> None:
    expected = np.array([[0.0, 0.0], [1.0, 2.0]])
    assert np.array_equal(as_points([(0, 0), (1, 2)]), expected)
    assert np.array_equal(as_points((1.0, 2.0)), np.array([[1.0, 2.0]]))
    assert np.array_equal(as_points(shapely.LineString([(0, 0), (1, 2)])), expected)


def test_as_points_from_objects_with_xy() -> None:
    class Dot(BaseModel):
        x: float
        y: float

    assert np.array_equal(as_points([Dot(x=1, y=2)]), np.array([[1.0, 2.0]]))


def test_as_points_rejects_wrong_shape() -> None:
    with pytest.raises(SchemaError):
        as_points([(1.0, 2.0, 3.0)])


def test_as_points_empty_has_shape_zero_by_two() -> None:
    assert as_points([]).shape == (0, 2)


def test_content_hash_is_stable_and_key_order_free() -> None:
    assert content_hash({"a": 1, "b": 2}) == content_hash({"b": 2, "a": 1})
    assert content_hash(b"x") != content_hash(b"y")
    assert len(content_hash("x")) == 64


def test_content_hash_of_model_matches_its_dump() -> None:
    class Thing(BaseModel):
        value: int

    assert content_hash(Thing(value=1)) == content_hash({"value": 1})


@given(st.floats(min_value=0.0, max_value=1000.0), st.integers(min_value=1, max_value=50))
def test_chunk_counts_sum_to_total(total: float, parts: int) -> None:
    chunks = chunk_counts(total, parts)
    assert len(chunks) == parts
    assert approx_equal(sum(chunks), total, tol=1e-9)


def test_chunk_counts_rejects_zero_parts() -> None:
    with pytest.raises(SchemaError):
        chunk_counts(8.0, 0)


def test_log_emits_structured_record(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level("INFO", logger="showpy")
    log("built set", set_id="12")
    assert caplog.records[0].getMessage() == "built set set_id=12"


def test_timer_measures_elapsed_ms() -> None:
    with timer() as clock:
        sum(range(1000))
    assert clock.elapsed_ms >= 0.0


def test_close_matches_finds_short_ids() -> None:
    assert close_matches("T3", ["T1", "B7", "T12"]) == ["T1", "T12"]
    assert close_matches("zzz", ["T1"]) == []
