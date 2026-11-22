#!/usr/bin/env python3

"""
test_match_solve_evaluation.py --- records the evaluation finding on bottleneck matching

Contains:
    test_bottleneck_assignment_does_not_depend_on_slot_order: keeps the spot-to-position
        assignment the same when the slots are shuffled
"""

import numpy as np
import pytest

from showpy.choreo.match import match
from showpy.choreo.match_models import MatchOptions
from showpy.core.util import seeded_rng

GRID_SIDE = 10
GRID_PITCH_M = 1.5
SEED = 1
SHUFFLE_SEEDS = (11, 12)
ROTATION = np.array([[0.8, 0.6], [-0.6, 0.8]])
SHIFT = np.array([3.0, 2.0])


def assigned_positions(starts: np.ndarray, slots: np.ndarray) -> dict[str, tuple[float, float]]:
    """Matches under the bottleneck objective and maps each spot to its target position."""
    result = match(starts, slots, options=MatchOptions(objective="bottleneck"))
    return {
        spot: (float(target[0]), float(target[1]))
        for spot, target in zip(result.entities, result.targets, strict=True)
    }


@pytest.mark.defect("UP-E-05")
@pytest.mark.xfail(strict=True, reason="UP-E-05")
def test_bottleneck_assignment_does_not_depend_on_slot_order() -> None:
    """Gives every spot the same position whichever order the slots are listed in."""
    starts = np.array(
        [(GRID_PITCH_M * i, GRID_PITCH_M * j) for i in range(GRID_SIDE) for j in range(GRID_SIDE)]
    )
    slots = starts[seeded_rng(SEED).permutation(len(starts))] @ ROTATION + SHIFT
    expected = assigned_positions(starts, slots)
    for seed in SHUFFLE_SEEDS:
        shuffled = slots[seeded_rng(seed).permutation(len(slots))]
        assert assigned_positions(starts, shuffled) == expected
