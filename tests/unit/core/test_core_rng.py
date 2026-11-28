#!/usr/bin/env python3

"""
test_core_rng.py --- tests the compiled PCG64 generator draw for draw against numpy's

The core's generator is reached through the private hook `_pcg64_draws`, which seeds one
generator and draws, in order, `integers(high)`, `random()` and
`choice(n, size, replace=False)`, as numpy's `Generator(PCG64(seed))` does with arrays.

Contains:
    test_core_pcg64_matches_numpy_on_10000_seeds: draws the same integers, floats and samples
        without replacement, bit for bit, for 10,000 seeds over every bounded-draw path
    test_core_pcg64_shares_the_32_bit_buffer_across_draws: keeps the half of a 64-bit draw that
        a 32-bit draw left, as numpy's bit generator does
    test_core_pcg64_refuses_an_empty_range_and_an_oversized_sample: raises SchemaError where
        numpy raises ValueError
"""

import numpy as np
import pytest

from e8s._core import core
from e8s.core.errors import SchemaError

SEEDS = 10_000
DRAWS = 1_000
GOLDEN_RATIO_64 = 0x9E3779B97F4A7C15
WORD = 2**64
# One exclusive bound per bounded-draw path: no draw, 32-bit Lemire, the full 32-bit word,
# 64-bit Lemire and the widest int64 bound. 2**62 + 2 rejects about a quarter of its 64-bit
# draws, so Lemire's rejection threshold decides most of its stream.
HIGHS = (1, 2, 7, 1_000, 2**31, 2**32 - 1, 2**32, 2**32 + 1, 2**40 + 3, 2**62 + 2, 2**63 - 1)
# Population sizes for both sampling methods: Floyd's set (small or sparse samples) and the
# tail shuffle (populations over 10,000 sampled more than one in fifty). Populations past
# 2**32 draw Floyd's values with 64-bit Lemire.
POPULATIONS = (1_000, 1_001, 5_000, 10_000, 10_001, 40_000, 60_000, 2**33 + 7, 5 * 10**10, 2**40)


def _seed(index: int) -> int:
    return index if index % 4 else index * GOLDEN_RATIO_64 % WORD


def _numpy_draws(seed: int, high: int, population: int) -> tuple[bytes, bytes, bytes]:
    generator = np.random.Generator(np.random.PCG64(seed))
    integers = generator.integers(high, size=DRAWS)
    floats = generator.random(DRAWS)
    sample = generator.choice(population, size=DRAWS, replace=False)
    return integers.astype("<i8").tobytes(), floats.tobytes(), sample.astype("<i8").tobytes()


def _core_draws(seed: int, high: int, population: int) -> tuple[bytes, bytes, bytes]:
    integers, floats, sample = core._pcg64_draws(seed, high, population, DRAWS)
    return integers.astype("<i8").tobytes(), floats.tobytes(), sample.astype("<i8").tobytes()


def test_core_pcg64_matches_numpy_on_10000_seeds() -> None:
    mismatches = []
    for index in range(SEEDS):
        seed = _seed(index)
        high = HIGHS[index % len(HIGHS)] + index // len(HIGHS) % 2
        population = POPULATIONS[index % len(POPULATIONS)]
        if _core_draws(seed, high, population) != _numpy_draws(seed, high, population):
            mismatches.append((seed, high, population))
    assert mismatches == []


def test_core_pcg64_shares_the_32_bit_buffer_across_draws() -> None:
    generator = np.random.Generator(np.random.PCG64(5))
    expected_integers = [int(generator.integers(7)) for _ in range(3)]
    expected_floats = generator.random(3)
    expected_sample = generator.choice(3, size=3, replace=False)
    integers, floats, sample = core._pcg64_draws(5, 7, 3, 3)
    assert integers.tolist() == expected_integers
    assert floats.tobytes() == expected_floats.tobytes()
    assert sample.tolist() == expected_sample.tolist()


@pytest.mark.parametrize(("high", "population", "size"), [(0, 5, 1), (1, 3, 4)])
def test_core_pcg64_refuses_an_empty_range_and_an_oversized_sample(
    high: int, population: int, size: int
) -> None:
    with pytest.raises(SchemaError):
        core._pcg64_draws(1, high, population, size)
