#!/usr/bin/env python3

"""
export_shows.py --- builds shows with guide lines, varied paths and claims for export tests

Contains:
    AT: fixed time stamp for publications and claims
    guided_show(): returns the two-set show with a guide line and four different moves
    performer(): builds a performer with a predictable ID
    live_publication(): returns a live publication of a show with no claims
"""

from datetime import UTC, datetime

from stage.choreo.models import PathRow, PathStyle
from stage.export.models import Performer, PerformerRole, Publication
from stage.sheet.models import GuideLine
from stage.show.models import Show
from tests.fixtures.motion_shows import with_rows, with_targets
from tests.fixtures.shows import two_set_show

AT = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)
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
        "T2": PathRow(entity="T2", start=2.0),
        "T3": PathRow(entity="T3", style=PathStyle.HOLD),
        "T4": PathRow(entity="T4", style=PathStyle.ROTATE, pivot=(6.0, GUIDE_Y), sweep_deg=180.0),
    }
    return with_rows(rows, show=show)


def performer(name: str, role: PerformerRole = "performer") -> Performer:
    """Builds a performer whose ID is the lower-case name."""
    return Performer(id=name.lower(), name=name, role=role)


def live_publication(show: Show) -> Publication:
    """Returns a live publication of `show` with no claims."""
    return Publication(
        id="pub-1",
        show_id=show.id,
        revision="r1",
        link="https://eight.example/p/pub-1",
        published_at=AT,
    )
