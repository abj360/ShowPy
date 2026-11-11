#!/usr/bin/env python3

"""
motion_shows.py --- builds shows with specific path rows for engine tests

Contains:
    with_rows(): returns the two-set show with path rows stored for transition 1→2
    with_targets(): returns the two-set show with set 2 placements replaced
"""

from collections.abc import Mapping
from typing import Any

from stage.choreo.models import PathRow, Route, TransitionSpec
from stage.show.models import Show
from tests.fixtures.shows import COUNTS, two_set_show

TID = ("1", "2")


def with_rows(
    rows: Mapping[str, PathRow], routes: Mapping[str, Route] | None = None, show: Show | None = None
) -> Show:
    """Returns a show whose transition 1→2 stores `rows` (and `routes`)."""
    base = show or two_set_show()
    spec = TransitionSpec(id=TID, counts=COUNTS, rows=dict(rows), routes=dict(routes or {}))
    return base.model_copy(update={"transitions": (spec,)})


# Any: target updates are partial placement dicts.
def with_targets(updates: Mapping[str, Mapping[str, Any]], show: Show | None = None) -> Show:
    """Returns a show whose set 2 placements are patched by `updates`."""
    base = show or two_set_show()
    second = base.sets[1].with_placements(updates)
    return base.model_copy(update={"sets": (base.sets[0], second)})
