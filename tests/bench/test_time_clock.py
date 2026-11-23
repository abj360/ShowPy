#!/usr/bin/env python3

"""
test_time_clock.py --- benchmarks count conversion on a timeline with many holds

The clock compiles once per timeline, so every later call is a binary search. The budget is 1 ms
per call after the first, as the fastest of five timed rounds; it was 26 ms with 1,500 holds
when each call rebuilt the pieces. Like test_id_check.py, a miss fails the benchmark run.

Contains:
    test_count_to_seconds_with_many_holds: converts a count on a 1,500-hold timeline within budget
"""

import timeit

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from e8s.time.clock import count_to_seconds
from e8s.time.models import Hold, Timeline, TimelineMode
from e8s.time.timeline import new_timeline

pytestmark = pytest.mark.bench

HOLDS = 1_500
HOLD_SPACING = 4.0
HOLD_SECONDS = 0.5
PROBE_COUNT = HOLDS * HOLD_SPACING / 2
BUDGET_S = 0.001
GATE_ROUNDS = 5


def _timeline_with_holds() -> Timeline:
    holds = tuple(
        Hold(at_count=n * HOLD_SPACING, seconds=HOLD_SECONDS) for n in range(1, HOLDS + 1)
    )
    return new_timeline(TimelineMode.COUNTS, 120.0).replace(holds=holds)


def test_count_to_seconds_with_many_holds(benchmark: BenchmarkFixture) -> None:
    timeline = _timeline_with_holds()
    count_to_seconds(timeline, PROBE_COUNT)

    seconds = benchmark(count_to_seconds, timeline, PROBE_COUNT)

    assert seconds > 0
    fastest = min(
        timeit.repeat(lambda: count_to_seconds(timeline, PROBE_COUNT), number=1, repeat=GATE_ROUNDS)
    )
    assert fastest < BUDGET_S
