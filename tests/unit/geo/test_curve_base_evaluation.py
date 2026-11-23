#!/usr/bin/env python3

"""
test_curve_base_evaluation.py --- records the evaluation finding on curve projection

Contains:
    nearest_distance(): finds the true nearest distance from a point to a curve by dense sampling
    test_project_finds_global_nearest_point_on_a_tight_spiral: projects onto the globally nearest
        arm of a 40-turn spiral
"""

import numpy as np
import pytest

from e8s.geo import shapes
from e8s.geo.curve_base import CurveBase

SPIRAL_TURNS = 40
COARSE_SAMPLES = 200_000
REFINE_SAMPLES = 20_001
REFINE_HALF_WIDTH_M = 0.05
DISTANCE_TOL_M = 1e-6
QUERY_POINT = (-11.5, 17.5)


def nearest_distance(curve: CurveBase, point: tuple[float, float]) -> float:
    """Finds the true nearest distance from a point to a curve by coarse then fine sampling."""
    target = np.asarray(point)
    length = curve.length()
    coarse = np.linspace(0.0, length, COARSE_SAMPLES)
    best = float(coarse[np.argmin(np.linalg.norm(curve.point_at(coarse) - target, axis=1))])
    fine = np.linspace(
        max(best - REFINE_HALF_WIDTH_M, 0.0),
        min(best + REFINE_HALF_WIDTH_M, length),
        REFINE_SAMPLES,
    )
    return float(np.linalg.norm(curve.point_at(fine) - target, axis=1).min())


@pytest.mark.defect("UP-E-01")
@pytest.mark.xfail(strict=True, reason="UP-E-01")
def test_project_finds_global_nearest_point_on_a_tight_spiral() -> None:
    """Projects onto the globally nearest arm, not a local minimum of another arm."""
    spiral = shapes.spiral((0, 0), 1, SPIRAL_TURNS, SPIRAL_TURNS)
    projected = float(np.linalg.norm(spiral.point_at(spiral.project(QUERY_POINT)) - QUERY_POINT))
    assert projected <= nearest_distance(spiral, QUERY_POINT) + DISTANCE_TOL_M
