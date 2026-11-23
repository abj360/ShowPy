#!/usr/bin/env python3

"""
placements.py --- strategies that append sets whose spots move, turn, enter and exit

Contains:
    SpotChange: what one spot does in a new set
    placement_for(): draws one spot's placement in the next set from its previous placement
    with_next_set(): appends a set where each spot stays, moves or enters or exits the field
"""

from enum import StrEnum

from hypothesis import strategies as st

from e8s.choreo.models import Placement
from e8s.sheet.models import Sheet
from e8s.show.manage import add_set
from e8s.show.models import Show
from tests.strategies.sheet import headings, points_on


class SpotChange(StrEnum):
    """Names what one spot does between two sets."""

    STAY = "stay"
    MOVE = "move"
    TOGGLE = "toggle"


@st.composite
def placement_for(draw: st.DrawFn, previous: Placement, sheet: Sheet) -> Placement:
    """Draws a spot's next placement: the same, moved and turned, or entering or exiting.

    Args:
        draw: Hypothesis draw function.
        previous: The spot's placement in the previous set.
        sheet: Sheet the spot stands on.
    """
    change = draw(st.sampled_from(SpotChange))
    if change is SpotChange.STAY:
        return previous.model_copy(update={"form": None, "order": None})
    x, y = draw(points_on(sheet))
    on_field = not previous.on_field if change is SpotChange.TOGGLE else previous.on_field
    return Placement(entity=previous.entity, x=x, y=y, facing=draw(headings()), on_field=on_field)


@st.composite
def with_next_set(draw: st.DrawFn, show: Show, set_id: str, counts: float) -> Show:
    """Appends set `set_id`, `counts` after the last set, with every spot's placement drawn.

    Args:
        draw: Hypothesis draw function.
        show: Show to extend.
        set_id: ID of the new set.
        counts: Counts of the move into the new set.
    """
    previous = show.sets[-1]
    rows = [draw(placement_for(p, show.sheet)) for p in previous.placements]
    return add_set(show, set_id, counts, placements=rows)
