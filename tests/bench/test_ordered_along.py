#!/usr/bin/env python3

"""
test_ordered_along.py --- benchmarks how ordered_along scales with the number of spots

`ordered_along` looks every chosen spot up with `Set.get`, so it is linear only while that
lookup is O(1). Quadrupling the spots from 75 to 300 must cost under six times as long, as the
fastest of several timed rounds; a lookup that rebuilt the index on every call cost 15 times as
long. Unlike the targets in test_budgets.py, this one fails the benchmark run when missed.

Contains:
    test_ordered_along_scales_linearly: checks 4x the spots costs under 6x the time
"""

import timeit

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from showpy.cast.select import OrderBy, ordered_along
from showpy.choreo.models import Placement, Set
from showpy.core.ids import SetId, SpotId

pytestmark = pytest.mark.bench

SMALL_SPOTS = 75
LARGE_SPOTS = 300
MAX_TIME_RATIO = 6.0
GATE_ROUNDS = 7
CALLS_PER_ROUND = 5
SPOTS_PER_ROW = 20


def _grid_set(spots: int) -> Set:
    rows = tuple(
        Placement(
            entity=SpotId(f"T{n}"),
            x=float(n % SPOTS_PER_ROW),
            y=float(n // SPOTS_PER_ROW),
            form=None,
            order=None,
        )
        for n in range(spots)
    )
    return Set(id=SetId("1"), counts=0.0, placements=rows)


def _fastest_s(set_: Set, by: OrderBy) -> float:
    def order() -> None:
        ordered_along(set_, "all", by=by)

    return min(timeit.repeat(order, number=CALLS_PER_ROUND, repeat=GATE_ROUNDS))


@pytest.mark.parametrize("by", ["form", "x"])
def test_ordered_along_scales_linearly(benchmark: BenchmarkFixture, by: OrderBy) -> None:
    small, large = _grid_set(SMALL_SPOTS), _grid_set(LARGE_SPOTS)
    benchmark.extra_info.update({"spots": LARGE_SPOTS, "by": by, "max_ratio": MAX_TIME_RATIO})
    chosen = benchmark(ordered_along, large, "all", by=by)
    assert len(chosen.ids) == LARGE_SPOTS
    assert _fastest_s(large, by) < MAX_TIME_RATIO * _fastest_s(small, by)
