#!/usr/bin/env python3

"""
test_avoid_evaluation.py --- records the evaluation finding on rectangle footprints in min_gaps

Contains:
    test_min_gaps_measures_rectangle_footprints_as_rectangles: reports the true gap between two
        side-by-side rectangles
"""

import pytest

from e8s.cast.models import Shape
from e8s.choreo.avoid import min_gaps
from e8s.examples import two_set_show

RECT_WIDTH_M = 0.6
RECT_DEPTH_M = 1.2
CENTRE_SPACING_M = 0.8
TRUE_GAP_M = 0.2
GAP_TOL_M = 1e-6
FAR_OFFSET_M = 10.0
PAIR = ("T1", "T2")


@pytest.mark.defect("UP-E-02")
@pytest.mark.xfail(strict=True, reason="UP-E-02")
def test_min_gaps_measures_rectangle_footprints_as_rectangles() -> None:
    """Reports 0.2 m between rectangles whose centres are 0.8 m apart along their 0.6 m side."""
    show = two_set_show()
    rectangle = Shape(kind="rectangle", width_m=RECT_WIDTH_M, depth_m=RECT_DEPTH_M)
    spots = tuple(
        spot.replace(shape=rectangle) if spot.id in PAIR else spot for spot in show.cast.spots
    )
    sets = tuple(
        stage.replace(
            placements=tuple(
                placement.replace(
                    x=0.0 if placement.entity == PAIR[0] else CENTRE_SPACING_M, y=placement.y
                )
                if placement.entity in PAIR
                else placement.replace(x=placement.x + FAR_OFFSET_M)
                for placement in stage.placements
            )
        )
        for stage in show.sets
    )
    placed = show.replace(cast=show.cast.replace(spots=spots), sets=sets)
    gap = min_gaps(placed, ("1", "2"), sel=list(PAIR), top=1)[0].gap
    assert abs(gap - TRUE_GAP_M) <= GAP_TOL_M
