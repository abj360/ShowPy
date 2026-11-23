#!/usr/bin/env python3

"""
test_id_check.py --- benchmarks the ID check over every placement of a large show

Model fields run check_id on every stored ID, so loading 300 spots over 60 sets checks 18,000
spot IDs. The budget is 20 ms for all of them, as the fastest of five timed rounds; unlike the
targets in test_budgets.py, this one fails the benchmark run when missed.

Contains:
    test_check_id_over_a_large_show: checks 300 x 60 placement IDs within the budget
"""

import timeit

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from e8s.core.ids import check_id

pytestmark = pytest.mark.bench

SPOTS = 300
SETS = 60
SECTIONS = ("T", "B", "Flute_", "Sn.", "C12A")
BUDGET_S = 0.020
GATE_ROUNDS = 5


def _placement_ids() -> list[str]:
    spot_ids = [f"{SECTIONS[n % len(SECTIONS)]}{n}" for n in range(SPOTS)]
    return spot_ids * SETS


def test_check_id_over_a_large_show(benchmark: BenchmarkFixture) -> None:
    ids = _placement_ids()

    def check_all() -> list[str]:
        return [check_id(spot_id, kind="spot") for spot_id in ids]

    benchmark.extra_info.update({"ids": len(ids), "budget_s": BUDGET_S})
    assert benchmark(check_all) == ids
    assert min(timeit.repeat(check_all, number=1, repeat=GATE_ROUNDS)) < BUDGET_S
