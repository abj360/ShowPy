#!/usr/bin/env python3

"""
cast_shows.py --- builds shows whose spot IDs appear in every kind of show reference

Contains:
    rich_show(): builds the two-set show with forms, paths, routes, notes, overrides and zones
"""

from datetime import UTC, datetime

from showpy.choreo.form_models import Form
from showpy.choreo.path_models import (
    EntityFacing,
    FacingMode,
    FacingOverride,
    PathRow,
    PathStyle,
    Route,
    TransitionSpec,
)
from showpy.core.ids import FormId, RouteId, SetId, SpotId, ZoneId, make_tid
from showpy.core.selection import Selection
from showpy.core.types import Region
from showpy.geo.curves import Segment
from showpy.notes.models import Anchor, Note
from showpy.sheet.models import Zone, ZoneKind
from showpy.show.models import Show
from tests.fixtures.shows import two_set_show

AT = datetime(2026, 11, 11, tzinfo=UTC)
SQUARE = Region(exterior=((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)))


def rich_show() -> Show:
    """Builds two_set_show() plus a form, paths, a route, a note, an override and zones."""
    show = two_set_show()
    form = Form(
        id=FormId("line1"),
        set=SetId("1"),
        kind="line",
        geometry={},
        members=(SpotId("T1"), SpotId("T2"), SpotId("T3"), SpotId("T4")),
    )
    route = Route(
        id=RouteId("r1"),
        curve=Segment(a=(0.0, 0.0), b=(10.0, 0.0)),
        members=(SpotId("T3"), SpotId("T4")),
        offsets=(0.0, 1.0),
        travel=2.0,
    )
    rows = {
        SpotId("T1"): PathRow(
            entity=SpotId("T1"),
            facing_mode=FacingMode.TOWARD_ENTITY,
            facing_value=EntityFacing(entity=SpotId("T2")),
        ),
        SpotId("T3"): PathRow(entity=SpotId("T3"), style=PathStyle.FOLLOW, route=RouteId("r1")),
        SpotId("T4"): PathRow(entity=SpotId("T4"), style=PathStyle.FOLLOW, route=RouteId("r1")),
    }
    spec = TransitionSpec(
        id=make_tid("1", "2"), counts=8.0, rows=rows, routes={RouteId("r1"): route}
    )
    note = Note(id="n1", text="solo", anchor=Anchor.on_spots(["T2"]), author="pat", created_at=AT)
    override = FacingOverride(
        entities=(SpotId("T1"), SpotId("T2")),
        mode=FacingMode.TOWARD_ENTITY,
        value=EntityFacing(entity=SpotId("T3")),
        start=0.0,
        end=4.0,
    )
    zones = (
        Zone(id=ZoneId("z-ids"), kind=ZoneKind.CUSTOM, polygon=SQUARE, applies_to="T1-T2"),
        Zone(
            id=ZoneId("z-group"), kind=ZoneKind.CUSTOM, polygon=SQUARE, applies_to="group:trumpets"
        ),
        Zone(
            id=ZoneId("z-sel"),
            kind=ZoneKind.CUSTOM,
            polygon=SQUARE,
            applies_to=Selection(ids=(SpotId("T4"),)),
        ),
    )
    cast = show.cast.replace(selections={"pair": frozenset({"T1", "T4"})})
    return show.model_copy(
        update={
            "cast": cast,
            "forms": {"line1": form},
            "transitions": (spec,),
            "notes": (note,),
            "facing_overrides": (override,),
            "zones": zones,
        }
    )
