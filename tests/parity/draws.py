#!/usr/bin/env python3

"""
draws.py --- seeded float draws that give the same bits on every CI cell

numpy's `Generator.uniform` computes `low + (high - low) * u` in one C expression, which Apple
clang fuses into one rounding on arm64 macOS and the x86_64 Linux and Windows builds do not
(2026-11-28: the core cases' inputs, and so their digests, differed between macOS and Linux in
CI). A parity area whose digests are gated across cells draws through here: the
product and the sum are separate numpy operations, each rounded once, as the x86_64 builds
round them, so the inputs match those builds bit for bit and consume the same draws.

Contains:
    uniform(): draws floats in [low, high) from a generator, rounded the same on every platform
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
