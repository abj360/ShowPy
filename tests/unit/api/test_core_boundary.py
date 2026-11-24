#!/usr/bin/env python3

"""
test_core_boundary.py --- tests the boundary of the compiled core: errors, NaN and the part cache

Contains:
    test_core_error_raises_the_e8s_class_with_its_code: a core error reaches Python as its
        E8sError subclass with the same code, message, context and hint
    test_regression_core_rejects_nan_with_schema_error: NaN at the boundary raises SchemaError,
        never a panic or a silent pass
    cache_delta(): returns how far the probe cache's counters moved since a snapshot
    test_part_cache_hits_for_the_same_object: a second lookup of one part reuses its value
    test_part_cache_misses_for_an_equal_but_distinct_object: an equal copy gets its own value
    test_part_cache_evicts_a_collected_part: a collected part's entry leaves the cache
"""

import gc
import math

import pytest

from e8s._core import core
from e8s.core.error_codes import ErrorCode
from e8s.core.errors import E8sError, SchemaError
from e8s.core.types import Box2

FINITE_HINT = "pass a finite number"


def test_core_error_raises_the_e8s_class_with_its_code() -> None:
    with pytest.raises(SchemaError) as raised:
        core._finite_probe(-math.inf)
    error = raised.value
    assert type(error) is SchemaError
    assert error.code is ErrorCode.SCHEMA
    assert error.message == "value must be a finite number, got -inf"
    assert error.hint == FINITE_HINT
    assert error.context == {"field": "value", "value": "-inf"}


def test_regression_core_rejects_nan_with_schema_error() -> None:
    with pytest.raises(SchemaError) as raised:
        core._finite_probe(math.nan)
    assert isinstance(raised.value, E8sError)
    assert raised.value.code is ErrorCode.SCHEMA
    assert raised.value.context["value"] == "nan"
    assert core._finite_probe(0.25) == 0.25


def cache_delta(before: dict[str, int]) -> dict[str, int]:
    """Returns how far each counter of the probe cache moved since `before`."""
    after = core._cache_stats()
    return {name: after[name] - before[name] for name in before}


def _box() -> Box2:
    return Box2(min=(0.0, -1.5), max=(2.0, 3.25))


def test_part_cache_hits_for_the_same_object() -> None:
    part = _box()
    before = core._cache_stats()
    first = core._cache_probe(part)
    second = core._cache_probe(part)
    assert first == second
    assert cache_delta(before) == {"hits": 1, "misses": 1, "entries": 1}


def test_part_cache_misses_for_an_equal_but_distinct_object() -> None:
    part, twin = _box(), _box()
    assert part == twin
    assert part is not twin
    before = core._cache_stats()
    assert core._cache_probe(part) != core._cache_probe(twin)
    assert cache_delta(before) == {"hits": 0, "misses": 2, "entries": 2}


def test_part_cache_evicts_a_collected_part() -> None:
    part = _box()
    before = core._cache_stats()
    core._cache_probe(part)
    assert cache_delta(before)["entries"] == 1
    del part
    gc.collect()
    assert cache_delta(before)["entries"] == 0
