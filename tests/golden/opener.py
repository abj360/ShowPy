#!/usr/bin/env python3

"""
opener.py --- builds the golden opener: a block opens to a company front, then curves into an arc

Contains:
    opener_show(): builds the eight-trumpet, three-set golden show deterministically
"""

from showpy.cast.models import Cast, Group, Spot
from showpy.choreo.models import Placement, Set, TransitionSpec
from showpy.choreo.paths import curve_path, set_easing, stagger
from showpy.core.ids import make_tid
from showpy.core.types import Points
from showpy.geo.shapes import arc, line
from showpy.geo.spacing import block, space_along
from showpy.show.models import Show
from showpy.time.models import TempoSection, Timeline, TimelineMode
from tests.fixtures.shows import field_sheet

IDS = tuple(f"T{i}" for i in range(1, 9))
TEMPO = 144.0


def _placements(points: Points) -> tuple[Placement, ...]:
    return tuple(
        Placement(entity=e, x=float(p[0]), y=float(p[1])) for e, p in zip(IDS, points, strict=True)
    )


def opener_show() -> Show:
    """Builds a 2x4 block (set 1), a 14 m company front (set 2, 16 counts) and an arc (set 3, 8)."""
    first = _placements(block((0.0, 8.0), rows=2, cols=4, interval=1.5))
    second = _placements(space_along(line((-7.0, 6.0), (7.0, 6.0)), 8))
    third = _placements(space_along(arc((0.0, 2.0), 9.0, 150.0, -120.0), 8))
    show = Show(
        id="golden-opener",
        title="Golden opener",
        sheet=field_sheet(),
        cast=Cast(
            groups={"tpt": Group(key="tpt", label_prefix="T")},
            spots=tuple(Spot(id=e, group="tpt") for e in IDS),
        ),
        timeline=Timeline(
            mode=TimelineMode.COUNTS, tempos=(TempoSection(at_count=0.0, counts_per_minute=TEMPO),)
        ),
        sets=(
            Set(id="1", counts=0.0, placements=first),
            Set(id="2", counts=16.0, placements=second),
            Set(id="3", counts=8.0, placements=third),
        ),
        dependencies={"sheet:test-field": "1"},
    )
    opening = stagger(TransitionSpec(id=make_tid("1", "2"), counts=16.0), list(IDS), step=1.0)
    opening = set_easing(opening, list(IDS), "ease_in_out")
    curving = TransitionSpec(id=make_tid("2", "3"), counts=8.0)
    for entity in IDS[:4]:
        curving = curve_path(curving, entity, (show.sets[1].get(entity).x, 9.0))
    return show.model_copy(update={"transitions": (opening, curving)})
