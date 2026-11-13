#!/usr/bin/env python3

"""
cast_shows.py --- builds shows whose spot IDs appear in every kind of show reference

Contains:
    rich_show(): builds the two-set show with forms, paths, routes, notes, overrides and zones
"""

from datetime import UTC, datetime

from showpy.choreo.models import (
    FacingMode,
    FacingOverride,
    Form,
    PathRow,
    PathStyle,
    Route,
    TransitionSpec,
)
from showpy.core.selection import Selection
from showpy.core.types import Region
from showpy.notes.models import Anchor, Note
from showpy.sheet.models import Zone, ZoneKind
from showpy.show.models import Show
from tests.fixtures.shows import two_set_show

AT = datetime(2026, 11, 11, tzinfo=UTC)
SQUARE = Region(exterior=((0.0, 0.0), (4.0, 0.0), (4.0, 4.0), (0.0, 4.0)))


def rich_show() -> Show:
    """Builds two_set_show() plus a form, paths, a route, a note, an override and zones."""
    show = two_set_show()
    form = Form(id="line1", set="1", kind="line", geometry={}, members=("T1", "T2", "T3", "T4"))
    route = Route(id="r1", curve={}, members=("T3", "T4"), offsets=(0.0, 1.0), travel=2.0)
    rows = {
        "T1": PathRow(entity="T1", facing_mode=FacingMode.TOWARD_ENTITY, facing_value="T2"),
        "T3": PathRow(entity="T3", style=PathStyle.FOLLOW, route="r1"),
        "T4": PathRow(entity="T4", style=PathStyle.FOLLOW, route="r1"),
    }
    spec = TransitionSpec(id=("1", "2"), counts=8.0, rows=rows, routes={"r1": route})
    note = Note(id="n1", text="solo", anchor=Anchor.on_spots(["T2"]), author="pat", created_at=AT)
    override = FacingOverride(
        entities=("T1", "T2"), mode=FacingMode.TOWARD_ENTITY, value="T3", start=0.0, end=4.0
    )
    zones = (
        Zone(id="z-ids", kind=ZoneKind.CUSTOM, polygon=SQUARE, applies_to="T1-T2"),
        Zone(id="z-group", kind=ZoneKind.CUSTOM, polygon=SQUARE, applies_to="group:trumpets"),
        Zone(id="z-sel", kind=ZoneKind.CUSTOM, polygon=SQUARE, applies_to=Selection(ids=("T4",))),
    )
    cast = show.cast.model_copy(update={"selections": {"pair": frozenset({"T1", "T4"})}})
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
