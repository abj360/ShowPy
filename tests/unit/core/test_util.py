#!/usr/bin/env python3

"""
test_util.py --- tests ID sorting, ranges, float comparison, hashing and point normalization

Contains:
    test_*(): one behavior of showpy.core.util each
    test_regression_natural_sort_breaks_numeric_ties_by_original_text: orders T02 and T2 by text
    test_regression_seeded_rng_rejects_non_integer_or_negative_seeds: rejects bad seeds with hint
    test_regression_seeded_rng_keeps_the_default_rng_stream: matches numpy's default_rng stream
    test_regression_format_ranges_ties_ignore_hash_seed: orders natural-sort ties the same under
        every hash seed
    test_regression_seeded_rng_rejects_none: rejects a missing seed instead of returning an unseeded
        generator
    test_regression_seeded_rng_rejects_negative_seed: rejects a negative seed instead of leaking
        numpy's ValueError
    test_content_hash_is_domain_tagged_sha256: matches a hand-built tagged SHA-256 for each kind
    test_content_hash_separates_domains_and_kinds: gives equal content different hashes per
        domain, and bytes, text and JSON different hashes
    test_content_hash_ignores_hash_seed: hashes the example show alike under two hash seeds
    test_regression_content_hash_rejects_a_domain_outside_its_alphabet: rejects a newline or any
        character outside `[a-z0-9_.]`, which could forge the kind line of the hash header
    test_regression_content_hash_equal_for_signed_zero: hashes 0.0 and -0.0 alike
    test_regression_content_hash_equal_for_int_and_float_one: hashes 1 and 1.0 alike
    test_regression_content_hash_separates_string_from_object: keeps the string '{"a":1}' and the
        object {"a": 1} apart
    test_regression_content_hash_separates_int_keys_from_string_keys: keeps {1: "a"} and {"1": "a"}
        apart
    test_regression_content_hash_mixed_key_types_never_leak_type_error: hashes (order-independently)
        or rejects mixed key types, never a TypeError
    test_regression_content_hash_distinguishes_large_arrays: tells two different 2,000-element
        arrays apart
    test_regression_content_hash_hashes_nested_models_by_content: hashes a nested model by its
        fields, not by a truncated repr
    test_regression_content_hash_rejects_nan: rejects NaN, which has no standard JSON form
    test_regression_content_hash_rejects_lone_surrogate: rejects a lone surrogate with a
        ShowPyError, not UnicodeEncodeError
    test_regression_as_points_drops_z_of_3d_linestring: keeps x and y of each 3D vertex instead of
        scrambling the flat coordinates
    test_regression_as_points_accepts_3d_point: reads a 3D Shapely point as one (x, y) row
    test_regression_as_points_multipart_geometry_is_handled: flattens or rejects multi-part geometry
        with a ShowPyError
    test_regression_as_points_returns_a_copy: returns data the caller can mutate without corrupting
        its own array
    test_regression_natural_sort_survives_unicode_digits: sorts IDs holding non-ASCII digits such as
        superscript two
    test_regression_natural_sort_survives_huge_numbers: sorts IDs whose number has over 4,300 digits
    test_regression_approx_equal_treats_equal_infinities_as_equal: treats inf and inf as equal
        without a RuntimeWarning
    test_regression_chunk_counts_sum_exactly_to_total: splits so the parts sum exactly to the total
    test_regression_chunk_counts_rejects_nan_total: rejects a NaN total
    test_as_points_*(): copies, flattens 3D input and rejects multi-part geometry
    test_approx_equal_*(): infinities, NaN and relative tolerance
    test_chunk_counts_*(): exact sums for any total and rejection of infinite totals
    test_approx_equal_old_tol_keyword_*(), test_approx_equal_rejects_both_*(): the renamed keyword
    test_regression_id_split_is_linear_on_long_ids: splits a 10,000-character ID in linear time
    test_regression_range_check_happens_before_expansion: checks range endpoints before building
        300,000 strings
    test_split_id_*(): splits IDs into a prefix and a trailing ASCII number
    test_regression_split_id_rejects_digit_runs_no_id_can_hold: rejects a 5,000-digit tail with a
        ShowPyError
    test_regression_parse_range_rejects_oversized_ranges: rejects a range over MAX_RANGE before
        expanding it
    test_regression_parse_range_ignores_unicode_digits: treats an Arabic-Indic digit range as one ID
    test_regression_parse_range_rejects_huge_endpoints: rejects a 5,000-digit endpoint with a
        ShowPyError
    test_regression_parse_range_reads_ranges_with_digits_in_the_prefix: expands `P1x1-P1x3` whose
        prefix holds a digit
    test_regression_parse_range_reads_ranges_with_dash_prefixes: expands `flag-1-flag-3` the way
        `flag-*` already matches
    test_regression_parse_range_does_not_read_unicode_digits_as_ascii: does not read the
        Arabic-Indic digit three as 3
    test_regression_format_ranges_output_parses_back: writes ranges that parse_range reads back to
        the same IDs
    test_regression_parse_range_honors_padding_of_the_last_endpoint: keeps the spelling of the last
        endpoint or rejects mixed padding
"""

import hashlib
import math
import subprocess
import sys
import time
import tracemalloc
from collections.abc import Callable

import numpy as np
import pytest
import shapely
from hypothesis import given
from hypothesis import strategies as st
from pydantic import BaseModel, ConfigDict

from showpy.cast.models import Cast, Group, Spot
from showpy.cast.select import label_prefix, select
from showpy.core.canonical import jcs
from showpy.core.errors import DeprecatedWarning, GeometryError, SchemaError, ShowPyError
from showpy.core.ids import SpotId
from showpy.core.util import (
    approx_equal,
    as_points,
    asset_uri,
    chunk_counts,
    close_matches,
    content_hash,
    format_ranges,
    log,
    natural_sort,
    parse_range,
    seeded_rng,
    split_id,
    timer,
)
from showpy.show.repositories import InMemoryAssetStore
from tests.helpers import digests_under_hash_seeds, fails_after


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
    assert approx_equal(sum(chunks), total, abs_tol=1e-9)


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


HASH_SEEDS = ("1", "2", "3")
SUBPROCESS_TIMEOUT_S = 20
BIG_ARRAY_SIZE = 2000
BIG_ID_LENGTH = 10000
QUADRATIC_BUDGET_S = 0.1
RANGE_PEAK_BUDGET_BYTES = 5_000_000
HUGE_RANGE_END = 300_000
NAN = float("nan")
SHA256_HEX_LENGTH = 64
HASH_TIES_SCRIPT = (
    "from showpy.core.util import format_ranges\n"
    "print(format_ranges({'T02', 'T2', 'T1', 'T01', 'T3'}))\n"
)


def run_under_seeds(script: str) -> list[str]:
    """Runs a script once per PYTHONHASHSEED and returns each combined output."""
    outputs = []
    for seed in HASH_SEEDS:
        done = subprocess.run(
            [sys.executable, "-c", script],
            env={"PYTHONHASHSEED": seed},
            capture_output=True,
            text=True,
            timeout=SUBPROCESS_TIMEOUT_S,
            check=False,
        )
        outputs.append(done.stdout + done.stderr[-200:])
    return outputs


class _ArrayModel(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True, frozen=True)

    values: np.ndarray


def _big_arrays() -> tuple[np.ndarray, np.ndarray]:
    first = np.zeros(BIG_ARRAY_SIZE)
    second = np.zeros(BIG_ARRAY_SIZE)
    second[BIG_ARRAY_SIZE // 2] = 1.0
    return first, second


def _points_or_rejection(obj: object) -> np.ndarray:
    try:
        return as_points(obj)
    except ShowPyError:
        return np.zeros((0, 2))


def _best_of(call: Callable[[], object], trials: int = 3) -> float:
    best = math.inf
    for _ in range(trials):
        start = time.perf_counter()
        call()
        best = min(best, time.perf_counter() - start)
    return best


def test_regression_format_ranges_ties_ignore_hash_seed() -> None:
    """Orders natural-sort ties the same under every hash seed (audit 9.3; P0 #9)."""
    outputs = run_under_seeds(HASH_TIES_SCRIPT)
    assert len(set(outputs)) == 1, outputs


def test_regression_seeded_rng_rejects_none() -> None:
    """Rejects a missing seed instead of returning an unseeded generator (audit 9.3)."""
    with pytest.raises(ShowPyError):
        seeded_rng(None)  # type: ignore[arg-type]


def test_regression_seeded_rng_rejects_negative_seed() -> None:
    """Rejects a negative seed instead of leaking numpy's ValueError (audit 9.3)."""
    with pytest.raises(ShowPyError):
        seeded_rng(-1)


def test_regression_natural_sort_breaks_numeric_ties_by_original_text() -> None:
    """Orders T02 and T2 by text, whatever order they arrive in."""
    assert natural_sort(["T2", "T02"]) == natural_sort(["T02", "T2"]) == ["T02", "T2"]


@pytest.mark.parametrize("seed", [None, True, -1, 1.5, "1"])
def test_regression_seeded_rng_rejects_non_integer_or_negative_seeds(seed: object) -> None:
    """Rejects None, booleans, negatives and non-integers with a hint."""
    with pytest.raises(SchemaError) as caught:
        seeded_rng(seed)  # type: ignore[arg-type]
    assert caught.value.hint


def test_regression_seeded_rng_keeps_the_default_rng_stream() -> None:
    """Draws the same stream as numpy's default_rng for int and numpy integer seeds."""
    expected = np.random.default_rng(7).random(5)
    assert np.array_equal(seeded_rng(7).random(5), expected)
    assert np.array_equal(seeded_rng(np.int64(7)).random(5), expected)  # type: ignore[arg-type]


def test_regression_content_hash_equal_for_signed_zero() -> None:
    """Hashes 0.0 and -0.0 alike (audit 9.3)."""
    assert content_hash({"x": 0.0}) == content_hash({"x": -0.0})


def test_regression_content_hash_equal_for_int_and_float_one() -> None:
    """Hashes 1 and 1.0 alike (audit 9.3)."""
    assert content_hash({"x": 1}) == content_hash({"x": 1.0})


def test_regression_content_hash_separates_string_from_object() -> None:
    """Keeps the string '{"a":1}' and the object {"a": 1} apart (audit 9.3)."""
    assert content_hash('{"a":1}') != content_hash({"a": 1})


def test_regression_content_hash_separates_int_keys_from_string_keys() -> None:
    """Keeps {1: "a"} and {"1": "a"} apart (audit 9.3)."""
    assert content_hash({1: "a"}) != content_hash({"1": "a"})


def test_regression_content_hash_mixed_key_types_never_leak_type_error() -> None:
    """Hashes (order-independently) or rejects mixed key types, never a TypeError (audit 9.3).

    The audit allows reject or accept; an accepted hash must not depend on insertion order.
    """
    mixed = {1: "a", "b": 2}
    try:
        digest = content_hash(mixed)
    except ShowPyError:
        return
    assert len(digest) == SHA256_HEX_LENGTH
    assert digest == content_hash({"b": 2, 1: "a"})


def test_regression_content_hash_distinguishes_large_arrays() -> None:
    """Tells two different 2,000-element arrays apart (audit 9.3)."""
    first, second = _big_arrays()
    assert content_hash({"a": first}) != content_hash({"a": second})


def test_regression_content_hash_hashes_nested_models_by_content() -> None:
    """Hashes a nested model by its fields, not by a truncated repr (audit 9.3)."""
    first, second = _big_arrays()
    left = content_hash({"m": _ArrayModel(values=first)})
    right = content_hash({"m": _ArrayModel(values=second)})
    assert left != right


def test_regression_content_hash_rejects_nan() -> None:
    """Rejects NaN, which has no standard JSON form (audit 9.3)."""
    with pytest.raises(ShowPyError):
        content_hash({"x": NAN})


def test_regression_content_hash_rejects_lone_surrogate() -> None:
    """Rejects a lone surrogate with a ShowPyError, not UnicodeEncodeError (audit 9.3)."""
    with pytest.raises(ShowPyError):
        content_hash("\ud800")


def test_regression_as_points_drops_z_of_3d_linestring() -> None:
    """Keeps x and y of each 3D vertex instead of scrambling the flat coordinates (audit 9.3)."""
    points = as_points(shapely.LineString([(0, 0, 5), (1, 1, 7)]))
    assert approx_equal(points, np.array([[0.0, 0.0], [1.0, 1.0]]))


def test_regression_as_points_accepts_3d_point() -> None:
    """Reads a 3D Shapely point as one (x, y) row (audit 9.3)."""
    assert approx_equal(as_points(shapely.Point(1, 2, 3)), np.array([[1.0, 2.0]]))


def test_regression_as_points_multipart_geometry_is_handled() -> None:
    """Flattens or rejects multi-part geometry with a ShowPyError (audit 9.3)."""
    points = _points_or_rejection(shapely.MultiPoint([(0, 0), (1, 1)]))
    assert points.shape in {(0, 2), (2, 2)}


def test_regression_as_points_returns_a_copy() -> None:
    """Returns data the caller can mutate without corrupting its own array (audit 9.3)."""
    original = np.zeros((2, 2))
    as_points(original)[:] += 1.0
    assert approx_equal(original, np.zeros((2, 2)))


def test_regression_natural_sort_survives_unicode_digits() -> None:
    """Sorts IDs holding non-ASCII digits such as superscript two (audit 9.3)."""
    assert len(natural_sort(["T1²", "T1"])) == 2


def test_regression_natural_sort_survives_huge_numbers() -> None:
    """Sorts IDs whose number has over 4,300 digits (audit 9.3)."""
    huge = "T" + "9" * 5000
    assert natural_sort([huge, "T1"]) == ["T1", huge]


def test_regression_approx_equal_treats_equal_infinities_as_equal() -> None:
    """Treats inf and inf as equal without a RuntimeWarning (audit 9.3)."""
    assert approx_equal(math.inf, math.inf)


def test_regression_chunk_counts_sum_exactly_to_total() -> None:
    """Splits so the parts sum exactly to the total (audit 9.3)."""
    total = 0.7000000000000001
    assert math.fsum(chunk_counts(total, 7)) == total


def test_regression_chunk_counts_rejects_nan_total() -> None:
    """Rejects a NaN total (audit 9.3)."""
    with pytest.raises(ShowPyError):
        chunk_counts(NAN, 4)


@fails_after(5)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_id_split_is_linear_on_long_ids() -> None:
    """Splits a 10,000-character ID in linear time (audit 9.3; P0 #8)."""
    long_id = "1" * BIG_ID_LENGTH + "x"
    assert _best_of(lambda: label_prefix(long_id), trials=1) < QUADRATIC_BUDGET_S


@fails_after(5)
@pytest.mark.skipif(sys.platform == "win32", reason="signal timeout")
def test_regression_range_check_happens_before_expansion() -> None:
    """Checks range endpoints before building 300,000 strings (audit 9.3; P0 #8)."""
    cast = Cast(
        groups={"t": Group(key="t", label_prefix="T")},
        spots=(Spot(id=SpotId("T1"), group="t"), Spot(id=SpotId("T2"), group="t")),
    )
    tracemalloc.start()
    try:
        with pytest.raises(ShowPyError):
            select(cast, f"T1-T{HUGE_RANGE_END}")
        _, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    assert peak < RANGE_PEAK_BUDGET_BYTES


@pytest.mark.defect("UP-9.3-24")
@pytest.mark.xfail(strict=True, reason="UP-9.3-24")
def test_regression_parse_range_reads_ranges_with_digits_in_the_prefix() -> None:
    """Expands `P1x1-P1x3` whose prefix holds a digit (audit 9.3)."""
    assert parse_range("P1x1-P1x3") == ["P1x1", "P1x2", "P1x3"]


@pytest.mark.defect("UP-9.3-25")
@pytest.mark.xfail(strict=True, reason="UP-9.3-25")
def test_regression_parse_range_reads_ranges_with_dash_prefixes() -> None:
    """Expands `flag-1-flag-3` the way `flag-*` already matches (audit 9.3)."""
    assert parse_range("flag-1-flag-3") == ["flag-1", "flag-2", "flag-3"]


def test_regression_parse_range_does_not_read_unicode_digits_as_ascii() -> None:
    """Does not read the Arabic-Indic digit three as 3 (audit 9.3)."""
    try:
        expanded = parse_range("T٣-T5")
    except ShowPyError:
        return
    assert "T3" not in expanded


@pytest.mark.defect("UP-9.3-27")
@pytest.mark.xfail(strict=True, reason="UP-9.3-27")
def test_regression_format_ranges_output_parses_back() -> None:
    """Writes ranges that parse_range reads back to the same IDs (audit 9.3)."""
    assert parse_range(format_ranges(["T1", "T2", "T3"])) == ["T1", "T2", "T3"]


@pytest.mark.defect("UP-9.3-28")
@pytest.mark.xfail(strict=True, reason="UP-9.3-28")
def test_regression_parse_range_honors_padding_of_the_last_endpoint() -> None:
    """Keeps the spelling of the last endpoint or rejects mixed padding (audit 9.3)."""
    try:
        expanded = parse_range("T1-T010")
    except ShowPyError:
        return
    assert expanded[-1] == "T010"


def _tagged(domain: str, kind: str, payload: bytes) -> str:
    head = f"showpy-hash/1\n{domain}\n{kind}\n".encode()
    return hashlib.sha256(head + payload).hexdigest()


def test_content_hash_is_domain_tagged_sha256() -> None:
    assert content_hash(b"abc") == _tagged("showpy.content", "bytes", b"abc")
    assert content_hash("abc") == _tagged("showpy.content", "str", b"abc")
    value = {"b": 1.0, "a": [True]}
    assert content_hash(value, domain="d") == _tagged("d", "json", jcs(value))
    assert content_hash(value, domain="d") == _tagged("d", "json", b'{"a":[true],"b":1}')


def test_content_hash_separates_domains_and_kinds() -> None:
    assert content_hash({"a": 1}) != content_hash({"a": 1}, domain="showpy.revision")
    assert len({content_hash(b'"a"'), content_hash("a"), content_hash(["a"])}) == 3
    truth = True
    assert content_hash(truth) != content_hash(1)


def test_content_hash_ignores_hash_seed() -> None:
    """Hashes `two_set_show()`, which holds no multi-element frozenset (ruling R11).

    The helper's tagged `show` carries frozenset tags, so the example show is built directly.
    Hashing a show with multi-element frozensets is seed independent since UP-9.3-55 was fixed.
    The helper covers hash seeds 1 and 2; seed 0 disables hash randomization, so it adds nothing.
    """
    digests = digests_under_hash_seeds(
        "from showpy.core.util import content_hash",
        "content_hash(two_set_show()).encode()",
    )
    assert all(first == second for first, second in digests), digests
    assert len({first for first, _ in digests}) == 1, digests


@pytest.mark.parametrize("domain", ["", "a\njson", "Showpy.content", "showpy-content", "a b"])
def test_regression_content_hash_rejects_a_domain_outside_its_alphabet(domain: str) -> None:
    with pytest.raises(SchemaError) as caught:
        content_hash({"a": 1}, domain=domain)
    assert caught.value.context["domain"] == domain


def test_split_id_splits_prefix_and_number() -> None:
    assert split_id("flag-12") == ("flag-", 12)
    assert split_id("T007") == ("T", 7)
    assert split_id("lead") == ("lead", None)
    assert split_id("") == ("", None)


def test_split_id_ignores_non_ascii_digits() -> None:
    assert split_id("T\u0663") == ("T\u0663", None)


def test_regression_split_id_rejects_digit_runs_no_id_can_hold() -> None:
    """Rejects a 5,000-digit tail with a ShowPyError, not ValueError from int()."""
    with pytest.raises(SchemaError):
        split_id("T" + "9" * 5000)


def test_regression_parse_range_rejects_oversized_ranges() -> None:
    """Rejects a range over MAX_RANGE before expanding it."""
    with pytest.raises(SchemaError):
        parse_range("T1-T10001")
    assert len(parse_range("T1-T10000")) == 10_000


def test_regression_parse_range_ignores_unicode_digits() -> None:
    """Treats a range written in Arabic-Indic digits as one ID, not a range."""
    assert parse_range("T\u0663-T\u0665") == ["T\u0663-T\u0665"]


def test_regression_parse_range_rejects_huge_endpoints() -> None:
    """Rejects a 5,000-digit endpoint with a ShowPyError, not ValueError from int()."""
    with pytest.raises(SchemaError):
        parse_range("T1-T" + "9" * 5000)


def test_asset_uri_matches_the_asset_store() -> None:
    assert asset_uri(b"x") == InMemoryAssetStore().put(b"x")
    assert asset_uri(b"x").startswith("asset:sha256:")


def test_as_points_returns_a_contiguous_float64_copy_of_any_input() -> None:
    strided = np.arange(12, dtype=np.float32).reshape(3, 4)[:, :2]
    result = as_points(strided)
    assert result.dtype == np.float64
    assert result.flags["C_CONTIGUOUS"]
    assert result.shape == (3, 2)
    assert not np.shares_memory(result, strided)


@pytest.mark.parametrize(
    "geometry",
    [
        shapely.MultiPoint([(0, 0), (1, 1)]),
        shapely.MultiLineString([[(0, 0), (1, 1)], [(2, 2), (3, 3)]]),
        shapely.GeometryCollection([shapely.Point(0, 0)]),
    ],
)
def test_as_points_rejects_multi_part_geometry(geometry: shapely.Geometry) -> None:
    with pytest.raises(GeometryError):
        as_points(geometry)


def test_as_points_reads_a_3d_polygon_exterior() -> None:
    polygon = shapely.Polygon([(0, 0, 9), (2, 0, 9), (2, 2, 9)])
    assert as_points(polygon).shape == (4, 2)


def test_approx_equal_handles_infinities_and_nan_without_warning() -> None:
    assert approx_equal(np.array([math.inf, 1.0]), np.array([math.inf, 1.0]))
    assert not approx_equal(math.inf, -math.inf)
    assert not approx_equal(math.inf, 1.0)
    assert not approx_equal(math.nan, math.nan)


def test_approx_equal_honors_relative_tolerance() -> None:
    assert approx_equal(1e9, 1e9 + 1.0, abs_tol=0.0, rel_tol=1e-6)
    assert not approx_equal(1e9, 1e9 + 1.0)


@given(st.floats(min_value=-1e6, max_value=1e6), st.integers(min_value=1, max_value=200))
def test_chunk_counts_sum_exactly_for_any_total(total: float, parts: int) -> None:
    chunks = chunk_counts(total, parts)
    assert len(chunks) == parts
    assert math.fsum(chunks) == total


@pytest.mark.parametrize("bad", [math.inf, -math.inf])
def test_chunk_counts_rejects_infinite_totals(bad: float) -> None:
    with pytest.raises(SchemaError):
        chunk_counts(bad, 3)


def test_approx_equal_old_tol_keyword_warns_and_matches() -> None:
    with pytest.warns(DeprecatedWarning, match="abs_tol"):
        old = approx_equal(1.0, 1.05, tol=0.1)
    assert old is approx_equal(1.0, 1.05, abs_tol=0.1) is True


def test_approx_equal_rejects_both_tolerance_keywords() -> None:
    with pytest.raises(SchemaError):
        approx_equal(1.0, 1.0, tol=0.1, abs_tol=0.1)
