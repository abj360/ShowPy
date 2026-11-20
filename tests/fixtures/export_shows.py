#!/usr/bin/env python3

"""
export_shows.py --- builds shows with guide lines, varied paths and claims for export tests

Contains:
    AT: fixed time stamp for publications and claims
    guided_show(): returns the two-set show with a guide line and four different moves
    performer(): builds a performer with a predictable ID
    live_publication(): returns a live publication of a show with no claims
"""

from showpy.choreo.path_models import PathRow, PathStyle
from showpy.core.ids import SpotId
from showpy.examples import EXAMPLE_TIME as AT
from showpy.examples import live_publication, performer
from showpy.sheet.models import GuideLine
from showpy.show.models import Show
from tests.fixtures.motion_shows import with_rows, with_targets
from tests.fixtures.shows import two_set_show

__all__ = ["AT", "guided_show", "live_publication", "performer"]

GUIDE_Y = 7.0
HALF_WIDTH = 20.0


def guided_show() -> Show:
    """Returns the two-set show with a guide at y=7 labelled `A` and four different moves.

    T1 moves straight across the guide, T2 is delayed 2 counts, T3 holds, and T4 rotates
    180° about (6, 7), crossing the guide at (8, 7).
    """
    base = two_set_show()
    guide = GuideLine(
        id="hash-a",
        kind="hash",
        points=((-HALF_WIDTH, GUIDE_Y), (HALF_WIDTH, GUIDE_Y)),
        label="A",
    )
    sheet = base.sheet.model_copy(update={"guides": (guide,)})
    show = with_targets({"T3": {"y": 5.0}}, show=base.model_copy(update={"sheet": sheet}))
    rows = {
        "T2": PathRow(entity=SpotId("T2"), start=2.0),
        "T3": PathRow(entity=SpotId("T3"), style=PathStyle.HOLD),
        "T4": PathRow(
            entity=SpotId("T4"), style=PathStyle.ROTATE, pivot=(6.0, GUIDE_Y), sweep_deg=180.0
        ),
    }
    return with_rows(rows, show=show)
