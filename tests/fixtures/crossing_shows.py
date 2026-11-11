#!/usr/bin/env python3

"""
crossing_shows.py --- builds a show where two spots cross mid-transition

Contains:
    crossing_show(): returns T1 and T2 swapping places through the same point at count 4
"""

from stage.show.models import Show
from tests.fixtures.motion_shows import with_targets
from tests.fixtures.shows import two_set_show


def crossing_show() -> Show:
    """Returns a show where T1 (0,5)→(4,9) and T2 (4,5)→(0,9) meet at (2,7) at count 4.

    T3 already sits at (4,5) in set 1, so T2 starts from T3's old column; T3 and T4 hold.
    """
    base = two_set_show()
    first = base.sets[0].with_placements({"T2": {"x": 4.0}, "T3": {"x": 8.0}, "T4": {"x": 12.0}})
    show = base.model_copy(update={"sets": (first, base.sets[1])})
    return with_targets(
        {
            "T1": {"x": 4.0, "y": 9.0},
            "T2": {"x": 0.0, "y": 9.0},
            "T3": {"x": 8.0, "y": 5.0},
            "T4": {"x": 12.0, "y": 5.0},
        },
        show=show,
    )
