#!/usr/bin/env python3

"""
shows.py --- builds small, fully specified shows shared by unit tests

Contains:
    field_sheet(): builds a 40 x 20 m test sheet with a center landmark and a pit zone
    two_set_show(): builds a show with four trumpets moving between two sets
"""

from stage.cast.models import Cast, Group, Spot
from stage.choreo.models import Placement, Set
from stage.core.types import Region
from stage.sheet.models import Landmark, Sheet, SurfaceType, Zone, ZoneKind
from stage.show.models import Show
from stage.time.models import TempoSection, Timeline, TimelineMode

WIDTH = 40.0
DEPTH = 20.0
TEMPO = 120.0
COUNTS = 8.0


def field_sheet() -> Sheet:
    """Builds a 40 x 20 m sheet: origin front-center, a center landmark, a front pit zone."""
    half = WIDTH / 2
    ring = ((-half, 0.0), (half, 0.0), (half, DEPTH), (-half, DEPTH))
    pit = Region(exterior=((-4.0, -3.0), (4.0, -3.0), (4.0, -0.5), (-4.0, -0.5)))
    return Sheet(
        ref="sheet:test-field@1",
        name="Test field",
        surface_type=SurfaceType.BLANK,
        size_m=(WIDTH, DEPTH),
        outline=ring,
        walkable=Region(exterior=ring),
        landmarks=(Landmark(id="c", name="center", kind="point", geometry=((0.0, 10.0),)),),
        zones=(Zone(id="pit", kind=ZoneKind.PIT, polygon=pit),),
    )


def two_set_show() -> Show:
    """Builds four trumpets in a line at y=5 (set 1) moving 4 m upstage over 8 counts (set 2)."""
    spots = tuple(Spot(id=f"T{i}", group="trumpets") for i in range(1, 5))
    first = tuple(Placement(entity=s.id, x=2.0 * i, y=5.0) for i, s in enumerate(spots))
    second = tuple(p.model_copy(update={"y": 9.0}) for p in first)
    return Show(
        id="show-1",
        title="Test show",
        sheet=field_sheet(),
        cast=Cast(groups={"trumpets": Group(key="trumpets", label_prefix="T")}, spots=spots),
        timeline=Timeline(
            mode=TimelineMode.COUNTS, tempos=(TempoSection(at_count=0.0, counts_per_minute=TEMPO),)
        ),
        sets=(
            Set(id="1", counts=0.0, placements=first),
            Set(id="2", counts=COUNTS, placements=second),
        ),
        dependencies={"sheet:test-field": "1"},
    )
