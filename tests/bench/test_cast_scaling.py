#!/usr/bin/env python3

"""
test_cast_scaling.py --- benchmarks how cast lookups, regroup and mirror_pairs scale with spots

A designer's show holds hundreds of spots, so a quadratic step shows within one edit. Each gate
times the same work at 300 and at 1,200 spots, as the fastest of many rounds of CPU time after a
warm-up call, and demands under 6x the time for 4x the spots (linear is 4x).

Contains:
    test_cast_spot_lookup_scales_linearly: gates looking up every spot by ID
    test_regroup_scales_linearly: gates moving every spot of one group to another
    test_mirror_pairs_scaling_is_recorded: records the mirror_pairs ratio without gating it
"""

import time
import timeit
from collections.abc import Callable

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from e8s.cast.models import Cast, Group, Spot
from e8s.cast.select import mirror_pairs
from e8s.cast.spots import regroup
from e8s.choreo.models import Placement, Set
from e8s.core.ids import SetId, SpotId
from e8s.show.models import Show
from tests.fixtures.generators import seeded_show

pytestmark = pytest.mark.bench

SMALL = 300
LARGE = 1_200
MAX_RATIO = 6.0
ROUNDS = 15
CALLS_PER_ROUND = 5
SEED = 7


def _best(work: Callable[[], object]) -> float:
    work()
    rounds = timeit.repeat(work, number=CALLS_PER_ROUND, repeat=ROUNDS, timer=time.process_time)
    return min(rounds)


def _cast(count: int) -> Cast:
    spots = tuple(Spot(id=SpotId(f"T{n}"), group="g") for n in range(count))
    return Cast(groups={"g": Group(key="g", label_prefix="T")}, spots=spots)


def _show(count: int) -> Show:
    return seeded_show(seed=SEED, spots=count, sets=2)


def _lookup_all(cast: Cast) -> Callable[[], object]:
    return lambda: [cast.spot(spot.id) for spot in cast.spots]


def _regroup_all(show: Show) -> Callable[[], object]:
    target = next(iter(show.cast.groups))
    return lambda: regroup(show, "all", target)


def _mirror_set(count: int) -> Set:
    half = count // 2
    left = [Placement(entity=SpotId(f"L{n}"), x=-1.0 - n, y=0.0) for n in range(half)]
    right = [Placement(entity=SpotId(f"R{n}"), x=1.0 + n, y=0.0) for n in range(half)]
    return Set(id=SetId("1"), counts=0, placements=(*left, *right))


def _ratio(small: Callable[[], object], large: Callable[[], object]) -> float:
    return _best(large) / _best(small)


def test_cast_spot_lookup_scales_linearly(benchmark: BenchmarkFixture) -> None:
    small, large = _cast(SMALL), _cast(LARGE)
    benchmark(_lookup_all(large))
    assert _ratio(_lookup_all(small), _lookup_all(large)) < MAX_RATIO


def test_regroup_scales_linearly(benchmark: BenchmarkFixture) -> None:
    small, large = _show(SMALL), _show(LARGE)
    benchmark(_regroup_all(large))
    assert _ratio(_regroup_all(small), _regroup_all(large)) < MAX_RATIO


def test_mirror_pairs_scaling_is_recorded(benchmark: BenchmarkFixture) -> None:
    small, large = _mirror_set(SMALL), _mirror_set(LARGE)
    benchmark.extra_info["ratio_1200_over_300"] = _ratio(
        lambda: mirror_pairs(small, "all"), lambda: mirror_pairs(large, "all")
    )
    benchmark(lambda: mirror_pairs(large, "all"))
