#!/usr/bin/env python3

"""
test_import_time.py --- benchmarks importing the choreography data model in fresh interpreters

Importing e8s.choreo.models must not load pandas or any other extra. Its import time is the
median of 9 fresh interpreters, and at tier M it fails when that median is more than 20% above
the median stored for this benchmark in the baseline file, the same tolerance `nox -s bench`
applies to every benchmark's min time.

Contains:
    baseline_median(): reads one benchmark's stored median from a baseline file
    test_import_choreo_models: times the import and holds its median within 20% of the baseline
    test_baseline_median_names_a_missing_entry: proves the baseline lookup fails loudly
"""

import json
import statistics
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path

import pytest
from pytest_benchmark.fixture import BenchmarkFixture

from tests.conftest import BenchTier

pytestmark = pytest.mark.bench

BASELINE = Path(__file__).with_name("baseline-2026-11-13.json")
IMPORT_COMMAND = (sys.executable, "-c", "import e8s.choreo.models")
IMPORT_ROUNDS = 9
REGRESSION_SHARE = 0.20
GATED_TIER = "M"
BENCHMARK_NAME = "test_import_choreo_models"


def baseline_median(baseline: Path, name: str) -> float:
    """Reads one benchmark's stored median time from a pytest-benchmark baseline file.

    Args:
        baseline: The baseline JSON written by pytest-benchmark.
        name: The benchmark's test name.

    Returns:
        median_s: The stored median, in seconds.
    """
    benchmarks = json.loads(baseline.read_text())["benchmarks"]
    for entry in benchmarks:
        if entry["name"] == name:
            median: float = entry["stats"]["median"]
            return median
    raise AssertionError(f"E: {baseline.name} has no entry for {name}; re-record the baseline")


def test_import_choreo_models(benchmark: BenchmarkFixture, tier: BenchTier) -> None:
    budget_s = baseline_median(BASELINE, BENCHMARK_NAME) * (1 + REGRESSION_SHARE)
    times: list[float] = []

    def import_once() -> None:
        start = time.perf_counter()
        subprocess.run(IMPORT_COMMAND, check=True)
        times.append(time.perf_counter() - start)

    benchmark.extra_info.update({"tier": tier.name, "budget_s": budget_s})
    # pytest-benchmark leaves pedantic() untyped; binding it to a typed name keeps mypy strict.
    pedantic: Callable[..., object] = benchmark.pedantic
    pedantic(import_once, rounds=IMPORT_ROUNDS, iterations=1)
    assert len(times) == IMPORT_ROUNDS
    if tier.name == GATED_TIER:
        assert statistics.median(times) <= budget_s


def test_baseline_median_names_a_missing_entry(tmp_path: Path) -> None:
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"benchmarks": []}))
    with pytest.raises(AssertionError, match="no entry for absent"):
        baseline_median(baseline, "absent")
