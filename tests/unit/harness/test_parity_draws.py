#!/usr/bin/env python3

"""
test_parity_draws.py --- checks that the parity draws round the same on every platform

Contains:
    test_uniform_rounds_the_product_and_the_sum_separately: each value is CPython's unfused
        `low + (high - low) * u`, and differs from the fused result somewhere
    test_uniform_draws_what_generator_uniform_draws: same draws as numpy's `uniform`, within
        one ulp of the product, and the generator ends in the same state
    test_uniform_returns_a_float_without_a_size: one draw, as `Generator.uniform` gives
"""

import math

import numpy as np
import pytest

from tests.parity.draws import uniform

SEED = 20261128
DRAWS = 20_000
LOW, HIGH = -3.0, 3.0
# Fused or not, the two results differ by at most half an ulp of the product, under one of SPAN.
SPAN = HIGH - LOW
BOUNDS = ((LOW, HIGH), (-1e4, 1e4), (1.0, 18.0), (-324.0, -290.0))


@pytest.mark.parametrize(("low", "high"), BOUNDS)
def test_uniform_rounds_the_product_and_the_sum_separately(low: float, high: float) -> None:
    draws = np.random.default_rng(SEED).random(DRAWS).tolist()
    values = uniform(np.random.default_rng(SEED), low, high, size=DRAWS).tolist()
    assert values == [low + (high - low) * u for u in draws]
    assert values != [math.fma(high - low, u, low) for u in draws]


@pytest.mark.parametrize("size", [DRAWS, (DRAWS // 2, 2)])
def test_uniform_draws_what_generator_uniform_draws(size: int | tuple[int, ...]) -> None:
    ours, numpys = np.random.default_rng(SEED), np.random.default_rng(SEED)
    values = uniform(ours, LOW, HIGH, size=size)
    expected = numpys.uniform(LOW, HIGH, size=size)
    assert values.shape == expected.shape
    assert np.all(np.abs(values - expected) <= np.spacing(SPAN))
    assert np.all((values >= LOW) & (values < HIGH))
    assert ours.random(8).tolist() == numpys.random(8).tolist()


def test_uniform_returns_a_float_without_a_size() -> None:
    value = uniform(np.random.default_rng(SEED), 1.0, 18.0)
    assert type(value) is float
    assert value == 1.0 + 17.0 * np.random.default_rng(SEED).random()
