#!/usr/bin/env python3

"""
examples.py --- small, fully specified example shows for docs, doctests and tests

These are deterministic so examples print stable output. Tests re-export them from
tests/fixtures, so there is one definition of each example.

Contains:
    EXAMPLE_TIME: fixed time stamp for examples that need one
    EXAMPLE_TID: the transition between the two example sets
    field_sheet(): builds a 40 x 20 m sheet with a center landmark and a front pit zone
    two_set_show(): builds four trumpets moving 4 m upstage over 8 counts
    with_rows(): returns the example show with path rows stored for transition 1→2
    with_targets(): returns the example show with set 2 placements patched
    crossing_show(): returns two spots whose straight moves cross at count 4
    performer(): builds a performer whose ID is the lower-case name
    live_publication(): returns a live, unclaimed publication of a show
"""

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from stage.cast.models import Cast, Group, Spot
from stage.choreo.models import PathRow, Placement, Route, Set, TransitionSpec
from stage.core.types import Region
from stage.export.models import Performer, PerformerRole, Publication
from stage.sheet.models import Landmark, Sheet, SurfaceType, Zone, ZoneKind
from stage.show.models import Show
from stage.time.models import TempoSection, Timeline, TimelineMode

EXAMPLE_TIME = datetime(2026, 11, 11, 18, 0, tzinfo=UTC)
EXAMPLE_TID = ("1", "2")
WIDTH = 40.0
DEPTH = 20.0
TEMPO = 120.0
COUNTS = 8.0
EXAMPLE_LINK = "https://eight.example/p/pub-1"


def field_sheet() -> Sheet:
    """Builds a 40 x 20 m sheet: origin front-center, a center landmark, a front pit zone.

    >>> field_sheet().size_m
    (40.0, 20.0)
    """
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
    """Builds four trumpets in a line at y=5 (set 1) moving 4 m upstage over 8 counts (set 2).

    >>> [p.point for p in two_set_show().set("2").placements][:2]
    [(0.0, 9.0), (2.0, 9.0)]
    """
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


def with_rows(
    rows: Mapping[str, PathRow], routes: Mapping[str, Route] | None = None, show: Show | None = None
) -> Show:
    """Returns a show whose transition 1→2 stores `rows` (and `routes`).

    >>> with_rows({"T1": PathRow(entity="T1", start=2.0)}).transition(("1", "2")).row("T1").start
    2.0
    """
    base = show or two_set_show()
    spec = TransitionSpec(id=EXAMPLE_TID, counts=COUNTS, rows=dict(rows), routes=dict(routes or {}))
    return base.model_copy(update={"transitions": (spec,)})


# Any: target updates are partial placement dicts.
def with_targets(updates: Mapping[str, Mapping[str, Any]], show: Show | None = None) -> Show:
    """Returns a show whose set 2 placements are patched by `updates`.

    >>> with_targets({"T1": {"x": 3.0}}).set("2").get("T1").x
    3.0
    """
    base = show or two_set_show()
    second = base.sets[1].with_placements(updates)
    return base.model_copy(update={"sets": (base.sets[0], second)})


def crossing_show() -> Show:
    """Returns T1 (0,5)→(4,9) and T2 (4,5)→(0,9), meeting at (2,7) at count 4; T3, T4 hold.

    >>> crossing_show().set("1").get("T2").point
    (4.0, 5.0)
    """
    base = two_set_show()
    first = base.sets[0].with_placements({"T2": {"x": 4.0}, "T3": {"x": 8.0}, "T4": {"x": 12.0}})
    show = base.model_copy(update={"sets": (first, base.sets[1])})
    targets = {
        "T1": {"x": 4.0, "y": 9.0},
        "T2": {"x": 0.0, "y": 9.0},
        "T3": {"x": 8.0, "y": 5.0},
        "T4": {"x": 12.0, "y": 5.0},
    }
    return with_targets(targets, show=show)


def performer(name: str, role: PerformerRole = "performer") -> Performer:
    """Builds a performer whose ID is the lower-case name.

    >>> performer("Ada").id
    'ada'
    """
    return Performer(id=name.lower(), name=name, role=role)


def live_publication(show: Show) -> Publication:
    """Returns a live publication of `show` with no claims.

    >>> live_publication(two_set_show()).status
    'live'
    """
    return Publication(
        id="pub-1", show_id=show.id, revision="r1", link=EXAMPLE_LINK, published_at=EXAMPLE_TIME
    )
