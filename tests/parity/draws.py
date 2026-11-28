#!/usr/bin/env python3

"""
draws.py --- seeded float draws that give the same bits on every CI cell

numpy's `Generator.uniform` computes `low + (high - low) * u` in one C expression, which Apple
clang fuses into one rounding on arm64 macOS and the x86_64 Linux and Windows builds do not
(2026-11-28: the core cases' inputs, and so their digests, differed between macOS and Linux in
CI). A parity area whose digests are gated across cells draws through here: the
product and the sum are separate numpy operations, each rounded once, as the x86_64 builds
round them, so the inputs match those builds bit for bit and consume the same draws.

Powers of ten come from the float reader's correctly rounded parse of `1e<k>`, never the C
library's `pow`: 1e23 lies exactly halfway between two doubles, and the glibc and Windows
`pow(10, 23)` return the upper one where Apple's returns the even one (2026-11-28: the core
cases' generated JSON differed between Linux and Windows on one side and macOS on the other,
by 1 or 2 ulps on values near 1e22).

Contains:
    uniform(): draws floats in [low, high) from a generator, rounded the same on every platform
    power_of_ten(): returns the double nearest 10**exponent, rounded half to even
    powers_of_ten(): maps an array of exponents to their power_of_ten values
"""

from typing import overload

import numpy as np
import numpy.typing as npt


@overload
def uniform(rng: np.random.Generator, low: float, high: float, size: None = None) -> float: ...


@overload
def uniform(
    rng: np.random.Generator, low: float, high: float, size: int | tuple[int, ...]
) -> npt.NDArray[np.float64]: ...


def uniform(
    rng: np.random.Generator, low: float, high: float, size: int | tuple[int, ...] | None = None
) -> float | npt.NDArray[np.float64]:
    """Draws floats in [low, high) as `Generator.uniform` does, never fused into one rounding.

    Args:
        rng: The generator to draw from; it advances by one double per value, as for `uniform`.
        low: The lower bound.
        high: The upper bound.
        size: The output shape, or None for one float.

    Returns:
        values: `low + (high - low) * u` for each draw `u` of `rng.random`.
    """
    scale = high - low
    if size is None:
        return low + scale * float(rng.random())
    return np.add(low, np.multiply(scale, rng.random(size)))


def power_of_ten(exponent: int) -> float:
    """Returns the double nearest 10**exponent, as the float reader rounds `1e<exponent>`.

    Args:
        exponent: The power of ten.

    Returns:
        power: The correctly rounded power: 0.0 below about 1e-324, inf above 1e308.
    """
    return float(f"1e{exponent}")


def powers_of_ten(exponents: npt.NDArray[np.int64]) -> npt.NDArray[np.float64]:
    """Maps each exponent of an array to its power_of_ten value, keeping the array's shape."""
    distinct, inverse = np.unique(exponents, return_inverse=True)
    table = np.array([power_of_ten(exponent) for exponent in distinct.tolist()], dtype=np.float64)
    return table[inverse].reshape(exponents.shape)
