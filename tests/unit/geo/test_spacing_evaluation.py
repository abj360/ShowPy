#!/usr/bin/env python3

"""
test_spacing_evaluation.py --- records the evaluation finding on scatter over split regions

Contains:
    test_scatter_samples_every_component_of_a_split_region: places 60 spots over two discs
"""

import numpy as np
import pytest

from e8s.geo import shapes
from e8s.geo.regions import region, union
from e8s.geo.spacing import scatter

DISC_RADIUS_M = 5.0
DISC_CENTRE_DISTANCE_M = 30.0
SPOT_COUNT = 60
MIN_GAP_M = 1.0
SEED = 2


@pytest.mark.defect("UP-E-04")
@pytest.mark.xfail(strict=True, reason="UP-E-04")
def test_scatter_samples_every_component_of_a_split_region() -> None:
    """Places all 60 spots, with some in each of two discs 30 m apart."""
    two_discs = union(
        region(shapes.circle((0, 0), DISC_RADIUS_M)),
        region(shapes.circle((DISC_CENTRE_DISTANCE_M, 0), DISC_RADIUS_M)),
    )
    spots = scatter(two_discs, SPOT_COUNT, MIN_GAP_M, seed=SEED)
    midline_m = DISC_CENTRE_DISTANCE_M / 2
    assert len(spots) == SPOT_COUNT
    assert int(np.sum(spots[:, 0] < midline_m)) >= 1
    assert int(np.sum(spots[:, 0] > midline_m)) >= 1
